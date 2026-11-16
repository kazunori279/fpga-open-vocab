# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "sentencepiece", "sentence-transformers>=6.1.0", "transformers>=5.18.0"]
# ///
"""Evaluate a backend-aware student on the fixed selection and household suites.

No confirmation draws or prompt selection. Each checkpoint supplies its own
teacher, text policy and projection; never compare a Gemma student to SigLIP text.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--int4", action="store_true", help="use the exported C integer encoder")
    args = ap.parse_args()

    import distill
    import numpy as np
    import student
    import torch
    from eval_embeddinggemma import dataset, home_dataset, metrics, sha, unit
    from PIL import Image
    from spaces import resolve
    from teacher_backend import TeacherBackend, checkpoint_targets

    run = ROOT / "model/runs" / args.run
    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    checkpoint_targets(ckpt)
    spec, basis = resolve(ckpt["teacher"])
    backend = TeacherBackend(spec, args.device, basis)
    model = student.Student().to(args.device).eval()
    model.load_state_dict(ckpt["state_dict"])
    transform = distill.student_transform(False, False)
    report = {"run": args.run, "space_id": ckpt["teacher_space"], "spec": spec,
              "seed": ckpt["seed"], "checkpoint_sha256": sha(run / "student.pt"),
              "precision": "int4-rgb565" if args.int4 else "fp32-rgb888", "suites": {}}
    binary = None
    if args.int4:
        from artifacts import load_export
        from screen_home import build_and_verify
        export = run / "export"
        meta = load_export(export)
        if meta.get("space_id") != ckpt["teacher_space"] or meta["wbits"] != 4:
            raise ValueError("Student and int4 export differ")
        build = run / "c-eval"
        build.mkdir(exist_ok=True)
        binary = build_and_verify(build, export)
        report["weights_sha256"] = sha(export / "weights.bin")
    for suite, records in (("cross-scene", dataset(None)), ("home", home_dataset())):
        report["suites"][suite] = {}
        for name, paths, npos, prompts in records:
            images = []
            for path in paths:
                with Image.open(path) as im:
                    images.append(im.convert("RGB"))
            tv = backend.encode_text(prompts)
            if binary:
                from screen_home import infer
                # Mirrors screen_home's native128 RGB565 device path.
                if any(im.size != (128, 128) for im in images):
                    raise ValueError("Integer evaluation expects native 128px panels")
                iv = unit(infer(binary, export / "weights.bin", [np.asarray(im) for im in images]))
            else:
                with torch.inference_mode():
                    iv = unit(model(torch.stack([transform(im) for im in images]).to(args.device)).cpu().numpy())
            report["suites"][suite][name] = {
                "prompts": prompts, "images": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in paths],
                "metrics": metrics(iv, tv, npos)}
            print(suite, name, report["suites"][suite][name]["metrics"]["standardized"]["signed_auc"], flush=True)
    (run / ("selection-int4.json" if args.int4 else "selection-fp32.json")).write_text(json.dumps(report, indent=2)+"\n")


if __name__ == "__main__":
    main()
