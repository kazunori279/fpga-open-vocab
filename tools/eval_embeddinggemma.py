# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "sentencepiece", "sentence-transformers>=6.1.0", "transformers>=5.18.0"]
# ///
"""Phase 1 of #43: compare teachers on the existing ten-contrast selection set.

    uv run --script tools/eval_embeddinggemma.py --backend gemma
    uv run --script tools/eval_embeddinggemma.py --backend siglip

No confirmation draws, training, or deployment. Bare prompts are fixed before
scoring; SigLIP's shipped template ensemble is also scored as a separate row.
Signed AUC keeps inverted state semantics visible. Standardized margins match
probe_bisect's pooled-background convention; raw cosine margins are also saved.
Results include image hashes, raw embeddings, timing and package versions.
"""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
REVISION = "f9972ac22e696a105c482202646979e33e4b5a1e"
MODEL = "google/embeddinggemma-2"
SPEC = "ViT-SO400M-14-SigLIP2:webli"
EXPORT = ROOT / "model/runs/so400m-full-a05/export"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unit(v):
    import numpy as np
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    if not np.isfinite(v).all() or (n <= 0).any():
        raise ValueError("Non-finite or zero embedding")
    return v / n


def metrics(iv, tv, npos):
    scores = iv @ tv.T
    std = scores.std(axis=0, ddof=1)
    std[std == 0] = 1
    normalized = (scores - scores.mean(axis=0)) / std
    result = {}
    for name, values in (("raw", scores), ("standardized", normalized)):
        margin = values[:, 0] - values[:, 1]
        diff = margin[:npos, None] - margin[None, npos:]
        auc = float(((diff > 0) + 0.5 * (diff == 0)).mean())
        result[name] = {"signed_auc": auc, "folded_auc": max(auc, 1 - auc),
                        "margins": margin.tolist()}
    return result


def dataset(names):
    from sieve_text import SETS, STILLS, setdir
    records = []
    for name in names or SETS:
        if name not in SETS:
            raise ValueError(f"Not a selection contrast: {name}")
        directory = STILLS / setdir(name)
        keep = {s.split("#")[0].strip() for s in
                (directory / "keep.txt").read_text().splitlines()} - {""}
        groups = [sorted(p for p in (directory / side).glob("*.png")
                         if p.stem in keep) for side in ("pos", "neg")]
        if not keep or any({p.stem for p in group} != keep for group in groups):
            raise ValueError(f"Incomplete selection set: {directory}")
        prompts = (directory / "queries.txt").read_text().splitlines()[:2]
        if len(prompts) != 2 or not all(prompts):
            raise ValueError(f"Missing query pair: {directory}")
        paths = groups[0] + groups[1]
        records.append((name, paths, len(groups[0]), prompts))
    return records


def home_dataset():
    """Existing synthetic home screen: correlated panels, not new held-out scenes.

    Use all seven pre-approved cases, exact archived prompts and native pixels.
    Empty/distractor panels (row 3) are excluded from binary state AUC.
    """
    directory = ROOT / "bench/stills/20260926-home-screen"
    approved = [c for c in json.loads((directory / "manifest.json").read_text())
                if c["quality"] == "keep"]
    prompts = json.loads((directory / "text_vectors.json").read_text())["queries"]
    if len(prompts) != 2 * len(approved):
        raise ValueError("Archived home-screen prompt count mismatch")
    layout = [[0, 1, 1, 0], [1, 0, 0, 1], [0, 1, 1, 0]]
    records = []
    for index, case in enumerate(approved):
        groups = [[], []]
        for row, labels in enumerate(layout):
            for col, label in enumerate(labels):
                path = directory / "inputs" / f"{case['id']}-r{row}-c{col}.png"
                if not path.is_file():
                    raise ValueError(f"Missing native panel: {path}")
                groups[label].append(path)
        records.append((case["id"], groups[0] + groups[1], len(groups[0]),
                        prompts[2 * index:2 * index + 2]))
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", required=True, choices=["gemma", "siglip"])
    ap.add_argument("--suite", choices=["cross-scene", "home"], default="cross-scene")
    ap.add_argument("--sets", nargs="+", help="Selection-set subset for smoke runs")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--manifest-only", action="store_true")
    args = ap.parse_args()
    if args.suite == "home" and args.sets:
        ap.error("--sets is only supported for cross-scene; home scores all approved cases")
    records = home_dataset() if args.suite == "home" else dataset(args.sets)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or ROOT / "model/runs" / f"eg2-{args.backend}-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    report = {"backend": args.backend, "created_utc": stamp,
              "device": args.device, "dtype": "float32", "batch": args.batch,
              "model": MODEL if args.backend == "gemma" else SPEC,
              "revision": REVISION if args.backend == "gemma" else None,
              "selection_only": True, "suite": args.suite,
              "limitations": ("Correlated synthetic panels from one generation per case; "
                              "not independent scenes or a deployment accuracy estimate"
                              if args.suite == "home" else "Previously used selection data"),
              "status": "prepared", "contrasts": {}}
    for name, paths, npos, prompts in records:
        report["contrasts"][name] = {
            "prompts": prompts, "npos": npos,
            "images": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in paths]}
    target = out / "results.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Manifest: {target}", flush=True)
    if args.manifest_only:
        return

    import numpy as np
    import torch
    from PIL import Image

    report["packages"] = {p: importlib.metadata.version(p) for p in
                          ["torch", "transformers", "sentence-transformers", "open_clip_torch"]}
    start = time.perf_counter()
    if args.backend == "gemma":
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL, revision=REVISION, device=args.device,
                                    config_kwargs={"audio_config": None},
                                    model_kwargs={"dtype": torch.float32})
        report["preprocessing"] = str(model)
    else:
        import open_clip
        import teacher
        model_name, pretrained = SPEC.split(":", 1)
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=args.device)
        tok = open_clip.get_tokenizer(model_name)
        model.eval()
        meta = json.loads((EXPORT / "export.json").read_text())
        basis_path = EXPORT / meta["basis"]
        if sha(basis_path) != meta["basis_sha256"]:
            raise ValueError("Shipped PCA hash mismatch")
        basis = np.load(basis_path)
        report["basis_sha256"] = sha(basis_path)
        report["preprocessing"] = str(preprocess)
        report["templates"] = teacher.TEMPLATES
    report["load_seconds"] = time.perf_counter() - start
    for name, paths, npos, prompts in records:
        start = time.perf_counter()
        images = []
        for path in paths:
            with Image.open(path) as im:
                images.append(im.convert("RGB"))
        with torch.inference_mode():
            if args.backend == "gemma":
                iv = unit(model.encode([{"image": im} for im in images],
                                      batch_size=args.batch, normalize_embeddings=True))
                tv = unit(model.encode(prompts, prompt="", normalize_embeddings=True))
                if iv.shape[1] != 768 or tv.shape != (2, 768):
                    raise ValueError("Unexpected Gemma embedding shape")
                rows = {"gemma-768-bare": (iv, tv),
                        "gemma-512-bare": (unit(iv[:, :512]), unit(tv[:, :512]))}
            else:
                chunks = []
                for i in range(0, len(images), args.batch):
                    x = torch.stack([preprocess(im) for im in images[i:i + args.batch]])
                    chunks.append(model.encode_image(x.to(args.device)).float().cpu().numpy())
                iv = unit(np.concatenate(chunks))
                tv = unit(model.encode_text(tok(prompts).to(args.device)).float().cpu().numpy())
                ensemble = teacher.encode_queries_spec(model, tok, prompts, args.device)
                def project(v):
                    return unit((v - basis["mu"]) @ basis["w"])
                rows = {"siglip-1152-bare": (iv, tv),
                        "siglip-512-bare": (project(iv), project(tv)),
                        "siglip-1152-ensemble": (iv, ensemble),
                        "siglip-512-ensemble": (project(iv), project(ensemble))}
        record = report["contrasts"][name]
        record["encode_seconds"] = time.perf_counter() - start
        record["rows"] = {}
        for label, (image_vectors, text_vectors) in rows.items():
            record["rows"][label] = metrics(image_vectors, text_vectors, npos)
            np.savez_compressed(out / f"{name}-{label}.npz", images=image_vectors, text=text_vectors)
        report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if platform.system() == "Darwin" else 1024)
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(name, {k: round(v["standardized"]["signed_auc"], 4)
                     for k, v in record["rows"].items()}, flush=True)
    report["status"] = "complete"
    target.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
