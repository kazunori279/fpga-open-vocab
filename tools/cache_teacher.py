# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "sentencepiece", "sentence-transformers>=6.1.0", "transformers>=5.18.0"]
# ///
"""Cache interchangeable SigLIP/Gemma 512d targets in canonical COCO image order.

    uv run --script tools/cache_teacher.py --backend gemma --split val2017
    uv run --script tools/cache_teacher.py --backend siglip --split val2017

Train using the printed --targets path. A cache cannot be overwritten; its
identity travels through the checkpoint and export to the host query encoder.
--subset and --holdout must match distill.py; zero rows are explicitly unused.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", required=True, choices=["siglip", "gemma"])
    ap.add_argument("--split", default="val2017")
    ap.add_argument("--subset", type=int, default=0)
    ap.add_argument("--holdout", type=int, default=1000)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--basis", type=Path, help="SigLIP frozen PCA; defaults to shipped basis")
    args = ap.parse_args()
    if args.batch < 1 or args.subset < 0 or args.holdout < 1:
        ap.error("batch and holdout must be positive; subset must be nonnegative")
    if args.backend == "gemma" and args.basis:
        ap.error("Gemma uses MRL, not PCA")

    import data
    import distill
    import numpy as np
    from PIL import Image
    from teacher_backend import GEMMA_SPEC, SIGLIP_SPEC, TeacherBackend, identity

    spec = GEMMA_SPEC if args.backend == "gemma" else SIGLIP_SPEC
    basis = None
    if args.backend == "siglip":
        export = ROOT / "model/runs/so400m-full-a05/export"
        basis = args.basis or export / json.loads((export / "export.json").read_text())["basis"]
    config = identity(spec, basis)
    names = data.image_list(args.split)
    if args.holdout >= len(names):
        ap.error("holdout must be smaller than the split")
    train, hold = distill.split_indices(len(names), args.holdout)
    if args.subset:
        train = train[:args.subset]
    indices = np.sort(np.concatenate([train, hold]))
    tag = f"teacher_{config['space_id'][:16]}_{args.split}"
    data.CACHE.mkdir(exist_ok=True, parents=True)
    dest = data.CACHE / f"{tag}.npy"
    if dest.exists() or dest.with_suffix(".json").exists():
        raise SystemExit(f"Refusing to overwrite {dest}; use the existing cache or move it explicitly")
    backend = TeacherBackend(spec, args.device, basis)
    targets = np.zeros((len(names), 512), dtype=np.float16)
    for start in range(0, len(indices), args.batch):
        rows = indices[start:start + args.batch]
        images = []
        for i in rows:
            with Image.open(data.image_dir(args.split) / names[i]) as image:
                images.append(image.convert("RGB"))
        targets[rows] = backend.encode_images(images, args.batch)
        print(f"{min(start + args.batch, len(indices))}/{len(indices)}", flush=True)
    meta = {**config, "backend_cache": 1, "teacher": spec, "split": args.split,
            "subset": args.subset, "holdout": args.holdout, "n_encoded": len(indices),
            "names_sha256": hashlib.sha256(json.dumps(names).encode()).hexdigest()}
    if basis:
        basis_dest = data.CACHE / f"{tag}.basis.npz"
        basis_dest.write_bytes(basis.read_bytes())
        meta["basis"] = basis_dest.name
    np.save(dest, targets)
    meta["targets_sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"--targets {dest}")


if __name__ == "__main__":
    main()
