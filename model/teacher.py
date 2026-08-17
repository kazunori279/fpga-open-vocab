# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "pillow", "open_clip_torch"]
# ///
"""The CLIP teacher: cache image embeddings, encode text queries.

    uv run model/teacher.py embed  --split val2017
    uv run model/teacher.py verify --split val2017

**ViT-B/16, and the choice is load-bearing.** It emits 512-d, which is what the
firmware, the README and (at M9) host/demo.py all assume - the 512 floats pushed
over USB are literally this vector. ViT-L/14 is a stronger teacher but 768-d,
which would change the on-device contract. B/16 beats B/32 at the same output
dim, and since embeddings are cached once, the extra runtime is a few minutes.

Embeddings are cached in the canonical image order from data.image_list(), which
is what makes row i of the cache correspond to image i everywhere downstream.
`verify` re-embeds a random sample and checks exactly that, because a silent
off-by-one here would train the student against other images' targets and look
identical to "the student is too small".
"""

import argparse
import json
import sys

import data
import numpy as np
import open_clip
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

MODEL = "ViT-B-16"
PRETRAINED = "openai"
EMBED_DIM = 512

# Prompt ensembling is how CLIP zero-shot is normally measured, so using it here
# makes the teacher row a fair ceiling rather than an artificially low one.
# These are photo-centric rather than CLIP's ImageNet-tuned set ("a origami
# {}.", "a {} in a video game.") because this appliance looks at real scenes
# through a camera.
#
# M9's demo.py MUST use this same list. The device stores the averaged vector,
# so a different ensemble at query time is a different query.
TEMPLATES = [
    "a photo of a {}.",
    "a photo of the {}.",
    "a close-up photo of a {}.",
    "a cropped photo of a {}.",
    "a bright photo of a {}.",
    "a photo of a small {}.",
    "a photo of a large {}.",
]


def tag() -> str:
    return f"{MODEL}-{PRETRAINED}"


def cache_path(split: str) -> tuple:
    stem = data.CACHE / f"emb_{split}_{tag()}"
    return stem.with_suffix(".npy"), stem.with_suffix(".json")


def pick_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class ImageList(Dataset):
    """COCO images by filename, in data.image_list() order."""

    def __init__(self, split: str, names: list[str], transform):
        self.dir = data.image_dir(split)
        self.names = names
        self.transform = transform

    def __len__(self) -> int:
        return len(self.names)

    def __getitem__(self, i: int):
        # A handful of COCO images are greyscale; convert unconditionally so the
        # tensor is always 3-channel.
        img = Image.open(self.dir / self.names[i]).convert("RGB")
        return self.transform(img), i


def load_clip(device: torch.device):
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)
    model = model.to(device).eval()
    return model, preprocess


@torch.no_grad()
def embed_images(model, preprocess, split: str, names: list[str], device, batch: int = 64) -> np.ndarray:
    loader = DataLoader(
        ImageList(split, names, preprocess),
        batch_size=batch,
        num_workers=6,
        shuffle=False,
    )
    out = np.zeros((len(names), EMBED_DIM), dtype=np.float16)
    done = 0
    for pixels, idx in loader:
        feats = model.encode_image(pixels.to(device))
        feats = feats / feats.norm(dim=-1, keepdim=True)
        out[idx.numpy()] = feats.cpu().numpy().astype(np.float16)
        done += len(idx)
        print(f"\rembed  : {done}/{len(names)}", end="", flush=True)
    print()
    return out


def load_spec(spec: str, device):
    """(model, tokenizer) for an open_clip "NAME:PRETRAINED" spec.

    The spec comes out of model/spaces.py, which reads it off the cached
    teacher's sidecar - so a student distilled from a swapped teacher gets that
    teacher here without anything in this file naming it. SigLIP 2 needs
    `transformers` and `sentencepiece` for its tokenizer; a caller that might
    resolve to one has to list them.
    """
    name, pretrained = spec.split(":")
    model, _, _ = open_clip.create_model_and_transforms(name, pretrained=pretrained)
    return model.to(device).eval(), open_clip.get_tokenizer(name)


@torch.no_grad()
def encode_queries_spec(model, tokenizer, names: list[str], device,
                        basis=None) -> np.ndarray:
    """One L2-normalized vector per query name, TEMPLATES-ensembled.

    **The ensembling happens in the model's own space and the projection is
    applied afterwards**, which is the order tools/probe_retention.py measured
    and host/demo.py ships. Averaging seven prompts is a statement about the
    teacher's geometry; doing it after a PCA that was fitted on single vectors
    would be a different operation, and the difference would not raise anything.

    `basis` is a loaded .npz with `mu` and `w` - teacher_swap.py folds its alpha
    into `mu`, so there is one formula here and no centring dial to get wrong.
    None means the model already emits the board's 512 and the map is identity.
    """
    out = []
    for name in names:
        tokens = tokenizer([t.format(name) for t in TEMPLATES]).to(device)
        feats = model.encode_text(tokens).float()
        feats = feats / feats.norm(dim=-1, keepdim=True)
        mean = feats.mean(dim=0)
        out.append((mean / mean.norm()).cpu().numpy())
    v = np.stack(out).astype(np.float32)
    if basis is None:
        return v
    p = (v - basis["mu"]) @ basis["w"]
    return (p / np.linalg.norm(p, axis=-1, keepdims=True)).astype(np.float32)


def encode_queries(model, names: list[str], device) -> np.ndarray:
    """One L2-normalized 512-d vector per query name, averaged over TEMPLATES.

    The incumbent ViT-B/16 path, kept as its own name because every caller that
    predates the teacher swap means *this* space specifically.
    """
    return encode_queries_spec(model, open_clip.get_tokenizer(MODEL), names,
                               device)


def load_cache(split: str) -> np.ndarray:
    npy, meta = cache_path(split)
    if not npy.exists():
        raise SystemExit(f"{npy} not found - run: uv run model/teacher.py embed --split {split}")
    n = json.loads(meta.read_text())["n_images"]
    emb = np.load(npy)
    if emb.shape[0] != n or emb.shape[0] != len(data.image_list(split)):
        raise SystemExit(f"{npy}: {emb.shape[0]} rows but the split has {len(data.image_list(split))} images")
    return emb


def cmd_embed(split: str, batch: int) -> int:
    names = data.image_list(split)
    device = pick_device()
    print(f"model  : {MODEL} / {PRETRAINED}")
    print(f"device : {device.type}")
    print(f"images : {len(names)} ({split})")

    model, preprocess = load_clip(device)
    emb = embed_images(model, preprocess, split, names, device, batch)

    npy, meta = cache_path(split)
    npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy, emb)
    meta.write_text(json.dumps({
        "model": MODEL,
        "pretrained": PRETRAINED,
        "split": split,
        "n_images": len(names),
        "dim": EMBED_DIM,
        "templates": TEMPLATES,
    }))
    print(f"wrote  : {npy} ({emb.nbytes / 2**20:.1f} MiB)")

    # A cache of all-zero or non-unit rows means encode_image silently failed.
    norms = np.linalg.norm(emb.astype(np.float32), axis=1)
    ok = bool(np.all(np.abs(norms - 1.0) < 1e-2))
    print(f"norms  : min {norms.min():.4f} max {norms.max():.4f}")
    print("\nRESULT : " + ("PASS" if ok else "FAIL - embeddings are not unit-norm"))
    return 0 if ok else 1


def cmd_verify(split: str, sample: int) -> int:
    """Re-embed a random sample and check it against the cache, row for row.

    This is the check that catches an index/filename misalignment. Such a bug
    trains the student against other images' targets, which does not crash and
    does not look like a bug - it looks like a student that is too small.
    """
    names = data.image_list(split)
    cached = load_cache(split).astype(np.float32)

    rng = np.random.default_rng(0)
    idx = rng.choice(len(names), size=min(sample, len(names)), replace=False)
    subset = [names[i] for i in idx]

    device = pick_device()
    model, preprocess = load_clip(device)
    fresh = embed_images(model, preprocess, split, subset, device, batch=32).astype(np.float32)

    cos = (fresh * cached[idx]).sum(axis=1)
    print(f"sample : {len(idx)} images")
    print(f"cosine : min {cos.min():.5f} mean {cos.mean():.5f}")

    # float16 storage costs a few ulps; anything below 0.999 is a real mismatch.
    ok = bool(cos.min() > 0.999)
    print("\nRESULT : " + ("PASS - cache is aligned" if ok else "FAIL - cache does not match the image order"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("embed", help="cache CLIP image embeddings")
    p.add_argument("--split", default="val2017")
    p.add_argument("--batch", type=int, default=64)

    p = sub.add_parser("verify", help="check the cache against a fresh sample")
    p.add_argument("--split", default="val2017")
    p.add_argument("--sample", type=int, default=32)

    args = ap.parse_args()
    if args.cmd == "embed":
        return cmd_embed(args.split, args.batch)
    return cmd_verify(args.split, args.sample)


if __name__ == "__main__":
    sys.exit(main())
