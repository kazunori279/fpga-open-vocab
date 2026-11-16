"""512d teacher interface shared by target caching and online query encoding.

Legacy OpenCLIP specs keep their seven-template text ensemble and frozen PCA.
Gemma specs pin a revision and use bare text plus normalized 512d MRL on both
modalities. The spec is part of the student's identity, never a runtime toggle
for already-trained weights.
"""
import hashlib
import json
import re

TEMPLATES = [
    "a photo of a {}.",
    "a photo of the {}.",
    "a close-up photo of a {}.",
    "a cropped photo of a {}.",
    "a bright photo of a {}.",
    "a photo of a small {}.",
    "a photo of a large {}.",
]

GEMMA_REVISION = "f9972ac22e696a105c482202646979e33e4b5a1e"
GEMMA_SPEC = f"gemma:google/embeddinggemma-2@{GEMMA_REVISION}"
SIGLIP_SPEC = "ViT-SO400M-14-SigLIP2:webli"


def gemma_revision(spec):
    match = re.fullmatch(r"gemma:google/embeddinggemma-2@([0-9a-f]{40})", spec)
    if not match:
        raise ValueError("Gemma requires gemma:google/embeddinggemma-2@<40-character revision>")
    return match[1]


def normalize(v):
    import numpy as np
    v = np.asarray(v, dtype=np.float32)
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    if not np.isfinite(v).all() or (norm <= 0).any():
        raise ValueError("Teacher returned zero or non-finite vectors")
    return v / norm


def identity(spec, basis=None):
    """Stable space identity includes prompt policy and exact projection bytes."""
    if spec.startswith("gemma:"):
        gemma_revision(spec)
        if basis is not None:
            raise ValueError("Gemma MRL must not use a SigLIP PCA basis")
    config = {"spec": spec, "dim": 512,
              "text_templates": ["{}"] if spec.startswith("gemma:") else TEMPLATES,
              "projection": "mrl512" if spec.startswith("gemma:") else "pca-or-identity",
              "basis_sha256": hashlib.sha256(basis.read_bytes()).hexdigest() if basis else None}
    config["space_id"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return config


class GemmaTeacher:
    def __init__(self, spec, device):
        import torch
        from sentence_transformers import SentenceTransformer
        revision = gemma_revision(spec)
        self.model = SentenceTransformer(
            "google/embeddinggemma-2", revision=revision, device=str(device),
            config_kwargs={"audio_config": None}, model_kwargs={"dtype": torch.float32})

    def _encode(self, values, batch=4):
        import numpy as np
        vectors = np.asarray(self.model.encode(
            values, prompt="", batch_size=batch, normalize_embeddings=True), dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[1] != 768:
            raise ValueError(f"Expected Gemma 768d output, got {vectors.shape}")
        return normalize(vectors[:, :512])

    def encode_text(self, texts):
        return self._encode(list(texts))

    def encode_images(self, images, batch=4):
        return self._encode([{"image": image} for image in images], batch)


class TeacherBackend:
    def __init__(self, spec, device, basis=None):
        import numpy as np
        self.config = identity(spec, basis)
        self.device = device
        self.basis = dict(np.load(basis)) if basis else None
        if spec.startswith("gemma:"):
            self.model = GemmaTeacher(spec, device)
            self.tokenizer = None
            self.preprocess = None
        else:
            import open_clip
            name, pretrained = spec.split(":", 1)
            self.model, _, self.preprocess = open_clip.create_model_and_transforms(
                name, pretrained=pretrained, device=device)
            self.model.eval()
            self.tokenizer = open_clip.get_tokenizer(name)

    def encode_text(self, texts):
        import teacher
        return teacher.encode_queries_spec(self.model, self.tokenizer, list(texts),
                                           self.device, self.basis)

    def encode_images(self, images, batch=4):
        import numpy as np
        import torch
        if isinstance(self.model, GemmaTeacher):
            return self.model.encode_images(images, batch)
        with torch.inference_mode():
            vectors = []
            for i in range(0, len(images), batch):
                pixels = torch.stack([self.preprocess(im) for im in images[i:i + batch]])
                vectors.append(self.model.encode_image(pixels.to(self.device)).float().cpu().numpy())
            values = normalize(np.concatenate(vectors))
        if self.basis is not None:
            values = normalize((values - self.basis["mu"]) @ self.basis["w"])
        if values.shape[1] != 512:
            raise ValueError("Teacher needs a frozen PCA to produce the board's 512d space")
        return values


def load_targets(tag, split, require_complete=False):
    """Validate cache identity, pixel ordering and content before using targets."""
    import data
    import numpy as np
    path = data.CACHE / f"{tag}.npy"
    side = json.loads(path.with_suffix(".json").read_text())
    names = data.image_list(split)
    digest = hashlib.sha256(json.dumps(names).encode()).hexdigest()
    if side["split"] != split or side["names_sha256"] != digest:
        raise ValueError("Teacher target split/image order mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != side["targets_sha256"]:
        raise ValueError("Teacher target content hash mismatch")
    basis = data.CACHE / side["basis"] if side.get("basis") else None
    if identity(side["teacher"], basis)["space_id"] != side["space_id"]:
        raise ValueError("Teacher target space identity mismatch")
    vectors = np.load(path)
    if vectors.shape != (len(names), 512) or not np.isfinite(vectors).all():
        raise ValueError("Invalid teacher target array")
    norms = np.linalg.norm(vectors.astype(np.float32), axis=1)
    valid = np.isclose(norms, 1, atol=0.002)
    if not np.all(valid | (norms == 0)) or (require_complete and not valid.all()):
        raise ValueError("Incomplete or non-unit teacher targets")
    return vectors, side


def checkpoint_targets(checkpoint):
    """Keep calibration/export tied to the training cache, including legacy runs."""
    import data
    import numpy as np
    import teacher
    tag = checkpoint.get("teacher", "")
    side = data.CACHE / f"{tag}.json"
    meta = json.loads(side.read_text()) if side.exists() else {}
    if meta.get("backend_cache"):
        targets, meta = load_targets(tag, checkpoint["split"])
        if checkpoint.get("teacher_space") != meta["space_id"]:
            raise ValueError("Checkpoint teacher space does not match target cache")
        return targets
    if checkpoint.get("teacher_space") or tag.startswith("teacher_"):
        raise ValueError("Missing backend teacher cache metadata")
    path = data.CACHE / f"{tag}.npy"
    if path.exists():
        return np.load(path)
    if tag not in ("", teacher.tag()):
        raise ValueError(f"Missing targets for teacher {tag}")
    return teacher.load_cache(checkpoint["split"])
