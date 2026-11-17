# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "sentencepiece", "sentence-transformers>=6.1.0", "transformers>=5.18.0"]
# ///
"""Separate camera precision and PTQ effects without changing published bundles.

Calibration candidates are fixed in advance (99.9%, 99.99%, max); no suite
labels are used in calibration. These diagnostic blobs are not deployment exports.
"""
import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))


def expand565(rgb):
    """Bit replication, exactly as firmware/cam_pixel.h (not scaled rounding)."""
    import numpy as np
    shifts = np.array([3, 2, 3], dtype=np.uint8)
    reduced = rgb >> shifts
    return (reduced << shifts) | (reduced >> (8 - 2 * shifts))


def codes(rgb, scale):
    import numpy as np
    x = (rgb.astype(np.float32) / np.float32(127.5) - np.float32(1)) / np.float32(scale)
    return np.clip(np.rint(x), -127, 127).astype(np.int8).transpose(0, 3, 1, 2).copy()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    import data
    import distill
    import export as exporter
    import numpy as np
    import quantize as quant
    import student
    import torch
    from eval_embeddinggemma import dataset, home_dataset, metrics, sha, unit
    from PIL import Image
    from screen_home import build_and_verify, infer
    from spaces import resolve
    from teacher_backend import TeacherBackend, checkpoint_targets
    from torch.utils.data import DataLoader

    torch.set_num_threads(4)
    torch.manual_seed(0)
    args.out.mkdir(parents=True, exist_ok=True)
    run = ROOT / "model/runs" / args.run
    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    targets = checkpoint_targets(ckpt)
    spec, basis = resolve(ckpt["teacher"])
    teacher = TeacherBackend(spec, "mps", basis)
    records, all_rgb = [], []
    for suite, cases in (("cross-scene", dataset(None)), ("home", home_dataset())):
        for name, paths, npos, prompts in cases:
            start = len(all_rgb)
            for path in paths:
                with Image.open(path) as image:
                    if image.size != (128, 128):
                        raise ValueError("Expected native128 panels")
                    all_rgb.append(np.asarray(image.convert("RGB")))
            records.append((suite, name, start, len(all_rgb), npos, teacher.encode_text(prompts)))
    del teacher
    rgb = np.stack(all_rgb)
    rgb565 = expand565(rgb)
    model = student.Student().eval()
    model.load_state_dict(ckpt["state_dict"])
    folded = quant.fold_bn(model)
    binary = build_and_verify(args.out, run / "export")
    report = {"run": args.run, "checkpoint_sha256": sha(run / "student.pt"),
              "published_weights_sha256": sha(run / "export/weights.bin"),
              "calibration": {"indices": "first 512 training indices", "seed": 0, "batch": 64},
              "limitations": ["one seed", "correlated synthetic home panels", "diagnostic candidates only"],
              "variants": {}}

    def float_infer(images):
        tensor = torch.from_numpy(images.astype(np.float32).transpose(0, 3, 1, 2) / 127.5 - 1)
        with torch.inference_mode():
            return unit(torch.cat([model(batch) for batch in tensor.split(32)]).numpy())

    refs = {"rgb888": float_infer(rgb), "rgb565": float_infer(rgb565)}

    def record(name, iv, reference):
        cos = (iv * reference).sum(1)
        row = {"cosine_to_fp32_same_input": {"mean": float(cos.mean()), "min": float(cos.min())},
               "suites": {}, "means": {}}
        for suite, case, start, end, npos, tv in records:
            row["suites"].setdefault(suite, {})[case] = metrics(iv[start:end], tv, npos)
        for suite, cases in row["suites"].items():
            row["means"][suite] = float(np.mean([v["standardized"]["signed_auc"] for v in cases.values()]))
        report["variants"][name] = row
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(name, row["means"], row["cosine_to_fp32_same_input"], flush=True)

    def c_infer(weights, images):
        scale = struct.unpack_from("<f", weights.read_bytes(), 28)[0]
        result = subprocess.run([str(binary), str(weights), "--codes"],
                                input=codes(images, scale).tobytes(), capture_output=True, check=True)
        return unit(np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 512))

    for mode, images in (("rgb888", rgb), ("rgb565", rgb565)):
        record("fp32-" + mode, refs[mode], refs[mode])
        iv = c_infer(run / "export/weights.bin", images)
        if mode == "rgb565":
            native = unit(infer(binary, run / "export/weights.bin", list(rgb)))
            if not np.array_equal(iv, native):
                raise ValueError("Python camera preprocessing differs from the actual C RGB565 path")
            report["camera_preprocessing_exact"] = True
        record("published-int4-" + mode, iv, refs[mode])

    names = data.image_list(ckpt["split"])
    indices = np.array(ckpt["train_indices"][:512])
    loader = DataLoader(distill.DistillSet(ckpt["split"], names, indices, targets,
                                         distill.student_transform(False, False)),
                        batch_size=64, shuffle=False, num_workers=0)
    # Materialize once to keep candidate calibration images/order identical.
    calibration = [(x, y) for x, y in loader]
    for bits, percentile in ((4, .999), (8, .999), (4, .9999), (4, 1.0)):
        quant.CALIB_PERCENTILE = percentile
        torch.manual_seed(0)
        qmodel = quant.quantize(folded, w_bits=bits, search=bits < 8)
        if bits == 4:
            for layer in (qmodel.features[0], qmodel.head):
                layer.w_qmax = 127.0
                layer.w_scale = quant.pick_w_scale(layer.layer.weight.detach(), 127.0, False)
        quant.calibrate(qmodel, calibration, "cpu", 8)
        layers, head_scale = exporter.build_layers(qmodel)
        blob = exporter.pack(layers, head_scale, float(qmodel.features[0].in_scale), 128)
        name = f"int{bits}-p{percentile}"
        weights = args.out / (name + ".bin")
        weights.write_bytes(blob)
        if bits == 4 and percentile == .999:
            if blob != (run / "export/weights.bin").read_bytes():
                raise ValueError("Reconstructed baseline does not match published int4 weights")
            report["baseline_reconstruction_exact"] = True
        record(name + "-rgb565", c_infer(weights, rgb565), refs["rgb565"])
        # Diagnostic only: compare layer accumulation on calibration images.
        report["variants"][name + "-rgb565"]["layer_cosines_calibration"] = quant.layer_cosines(
            folded, qmodel, calibration[0][0][:16])
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    report["status"] = "complete"
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
