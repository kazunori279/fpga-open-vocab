# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Exploratory camera-referenced synthetic screen, not measured board accuracy.

uv run --script tools/screen_home.py --set bench/stills/20260926-home-screen

The contact sheets are split as dataset preprocessing. All numerical image
perturbations below are sensitivity tests, not independently generated samples.
The encoder is the actual firmware C kernel plus the shipped weights; the
nearest-reference score is an offline surrogate, not a replay of m9's temporal
smoothing, enrolment windows, presence hysteresis or unknown-object rejection.
"""
import argparse
import hashlib
import importlib.metadata
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from artifacts import load_export

LAYOUT = [[0, 1, 1, 0], [1, 0, 0, 1], [0, 1, 1, 0]]
PROFILES = ["native", "dim", "bright", "warm", "cool", "blur", "noise"]
QUERIES = {
    "towel": ["a neatly folded towel", "a crumpled towel"],
    "mug": ["an upright mug", "a mug lying on its side"],
    "scissors": ["a pair of open scissors", "a pair of closed scissors"],
    "box": ["an open cardboard box", "a closed cardboard box"],
    "banana": ["a peeled banana", "an unpeeled banana"],
    "apple": ["a whole apple", "an apple cut in half"],
    "bottle": ["an intact plastic bottle", "a crushed plastic bottle"],
    "cutlery": ["a fork", "a spoon"],
    "jar": ["a jar full of rice", "an empty glass jar"],
    "glasses": ["a pair of unfolded eyeglasses", "a pair of folded eyeglasses"],
    "tray": ["a tray with keys", "a tray with a wallet"],
    "plant": ["a healthy basil plant", "a wilted basil plant"],
    "hand_control": ["an open hand", "a fist"],
    "cube_control": ["a red cube", "a green cube"],
}
# The generation plan's A is unpeeled. Fix the mapping before looking at scores.
QUERIES["banana"] = ["an unpeeled banana", "a peeled banana"]


def normalize(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def perturb(image, profile, seed):
    if profile == "blur":
        return np.asarray(image.filter(ImageFilter.GaussianBlur(0.6)), dtype=np.uint8)
    x = np.asarray(image, dtype=np.float32)
    if profile == "dim":
        x *= 0.75
    elif profile == "bright":
        x *= 1.25
    elif profile == "warm":
        x *= np.array([1.10, 1.0, 0.85])
    elif profile == "cool":
        x *= np.array([0.85, 1.0, 1.10])
    elif profile == "noise":
        x += np.random.default_rng(seed).normal(0, 4, x.shape)
    return np.clip(np.rint(x), 0, 255).astype(np.uint8)


def rgb565(rgb):
    x = rgb.astype(np.uint16)
    packed = ((x[..., 0] >> 3) << 11) | ((x[..., 1] >> 2) << 5) | (x[..., 2] >> 3)
    return packed.astype(">u2").tobytes()


def infer(binary, weights, images):
    result = subprocess.run([str(binary), str(weights)], input=b"".join(map(rgb565, images)),
                            capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 512).copy()


def build_and_verify(build, export):
    binary = build / "encode_stills"
    subprocess.run(["cc", "-O2", "-DFGX_DSP_SHIM", "-Ifirmware",
                    "tools/encode_stills.c", "firmware/encoder.c", "firmware/encoder_fast.c",
                    "-lm", "-o", str(binary)], cwd=ROOT, check=True)
    data = (export / "testvec.bin").read_bytes()
    magic, count, size = struct.unpack_from("<4sII", data)
    assert magic == b"FGXT" and size == 128 * 128 * 3
    stride = size + 512 * 8
    codes = b"".join(data[12 + i * stride:12 + i * stride + size] for i in range(count))
    golden = b"".join(data[12 + i * stride + size:12 + i * stride + size + 2048]
                       for i in range(count))
    actual = subprocess.run([str(binary), str(export / "weights.bin"), "--codes"],
                            input=codes, capture_output=True, check=True).stdout
    if actual != golden:
        raise SystemExit("C encoder does not match shipped golden vectors bit for bit")
    print(f"C encoder verified: {count * 512} / {count * 512} golden floats exact", flush=True)
    return binary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--export", type=Path, default=ROOT / "model/runs/so400m-full-a05/export")
    args = ap.parse_args()
    manifest = json.loads((args.set / "manifest.json").read_text())
    approved = [c for c in manifest if c["quality"] == "keep"]
    meta = load_export(args.export)
    actual_crc = zlib.crc32((args.export / "weights.bin").read_bytes())
    if actual_crc != int(meta["crc32"], 16):
        raise SystemExit("weights.bin does not match export.json CRC32")
    (args.set / "inputs").mkdir(exist_ok=True)
    records, pixels, native_images = [], [], []
    for case in approved:
        cid = case["id"]
        sheet = Image.open(args.set / case["sheet"]).convert("RGB")
        w, h = sheet.size
        for row in range(4):
            for col in range(4):
                tile = sheet.crop((round(col*w/4), round(row*h/4),
                                   round((col+1)*w/4), round((row+1)*h/4)))
                tile = tile.resize((128, 128), Image.Resampling.BOX)
                tile.save(args.set / "inputs" / f"{cid}-r{row}-c{col}.png")
                native_images.append(tile)
                for profile in PROFILES:
                    records.append({"case": cid, "row": row, "col": col, "profile": profile})
                    pixels.append(perturb(tile, profile, seed=100 * row + col))
    with tempfile.TemporaryDirectory(prefix="fgx-offline-") as tmp:
        binary = build_and_verify(Path(tmp), args.export)
        embeddings = normalize(infer(binary, args.export / "weights.bin", pixels))
    np.save(args.set / "student_embeddings.npy", embeddings)
    (args.set / "records.json").write_text(json.dumps(records, indent=2)+"\n")
    query_names = [phrase for c in approved for phrase in QUERIES[c["id"]]]
    # Teacher embeddings are cached with their exact prompts and model identity.
    cachemeta = {"queries": query_names, "spec": meta["spec"],
                 "basis_sha256": meta["basis_sha256"],
                 "pixels_sha256": hashlib.sha256(b"".join(im.tobytes() for im in native_images)).hexdigest()}
    cachepath = args.set / "text_vectors.npz"
    cachejson = args.set / "text_vectors.json"
    if cachepath.exists() and cachejson.exists() and json.loads(cachejson.read_text()) == cachemeta:
        cache = np.load(cachepath)
        queries = cache["projected"]
        fullqueries = cache["full"]
        teacher_images = cache["images"]
    else:
        import open_clip
        import teacher
        import torch
        device = teacher.pick_device()
        name, pre = meta["spec"].split(":")
        print(f"Loading teacher {meta['spec']} on {device}", flush=True)
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
        model = model.to(device).eval()
        tok = open_clip.get_tokenizer(name)
        fullqueries = teacher.encode_queries_spec(model, tok, query_names, device)
        basis = np.load(meta["basis_path"])
        queries = normalize((fullqueries - basis["mu"]) @ basis["w"])
        outputs = []
        with torch.no_grad():
            for at in range(0, len(native_images), 8):
                batch = torch.stack([preprocess(im) for im in native_images[at:at+8]]).to(device)
                outputs.append(normalize(model.encode_image(batch).float().cpu().numpy()))
                print(f"Teacher images {min(at+8,len(native_images))}/{len(native_images)}", flush=True)
        teacher_images = np.concatenate(outputs)
        np.savez(cachepath, projected=queries, full=fullqueries, images=teacher_images)
        cachejson.write_text(json.dumps(cachemeta, indent=2)+"\n")
    results = []
    for ci, case in enumerate(approved):
        cid = case["id"]
        mask = [i for i, r in enumerate(records) if r["case"] == cid]
        es = embeddings[mask].reshape(4,4,len(PROFILES),512)
        scores = es @ queries[ci*2:ci*2+2].T
        margins = scores[...,0] - scores[...,1]
        full = teacher_images[ci*16:(ci+1)*16].reshape(4,4,-1) @ fullqueries[ci*2:ci*2+2].T
        native_correct, teacher_correct, pair_right = 0, 0, 0
        enrolled = dict.fromkeys(PROFILES, 0)
        presence = dict.fromkeys(PROFILES, 0)
        luma_right = 0
        gaps, drift = [], []
        # Each row: first example of A/B enrols; second is held out.
        for row in range(3):
            a = [c for c in range(4) if LAYOUT[row][c] == 0]
            b = [c for c in range(4) if LAYOUT[row][c] == 1]
            refs = margins[row,[a[0],b[0]],0]
            gaps.append(float(refs[0]-refs[1]))
            drift.append(float(max(abs(margins[row,a[1],0]-refs[0]), abs(margins[row,b[1],0]-refs[1]))))
            emptymargin = margins[3,row,0]
            for label, cols in enumerate((a,b)):
                col = cols[1]
                native_correct += int(np.argmax(scores[row,col,0]) == label)
                teacher_correct += int(np.argmax(full[row,col]) == label)
                for pi, profile in enumerate(PROFILES):
                    value = margins[row,col,pi]
                    enrolled[profile] += int(np.argmin(abs(refs-value)) == label)
                    presence[profile] += int(np.argmin(abs(np.r_[refs,emptymargin]-value)) == label)
            pair_right += int(margins[row,a[1],0] > margins[row,b[1],0])
            # A deliberately simple brightness-only baseline, fitted on enrolment only.
            lum = [float((np.asarray(native_images[ci*16+row*4+c]) @ np.array([0.299,0.587,0.114])).mean()) for c in range(4)]
            lr = np.array([lum[a[0]],lum[b[0]]])
            luma_right += int(np.argmin(abs(lr-lum[a[1]])) == 0)
            luma_right += int(np.argmin(abs(lr-lum[b[1]])) == 1)
        # No recalibration: row-1 references must survive the other lighting rows.
        fixedrefs = margins[0,[0,1],0]
        lighting_correct = 0
        for row in (1,2):
            for col in range(4):
                lighting_correct += int(np.argmin(abs(fixedrefs-margins[row,col,0])) == LAYOUT[row][col])
        item = {"case": cid, "queries": QUERIES[cid], "held_out_n":6,
                "teacher_raw_correct":teacher_correct,"student_raw_correct":native_correct,
                "heldout_pair_direction_correct_of_3":pair_right,
                "enrolled_correct":enrolled,"enrolled_with_empty_correct":presence,
                "brightness_baseline_correct":luma_right,
                "enrolment_margin_gaps":gaps,"restaging_margin_drift":drift,
                "fixed_enrolment_lighting_correct_of_8":lighting_correct,
                "quality_note":case.get("note","")}
        results.append(item)
        print(json.dumps(item),flush=True)
    versions = {p: importlib.metadata.version(p) for p in
                ("numpy", "pillow", "torch", "torchvision", "open_clip_torch", "transformers")}
    (args.set / "results.json").write_text(json.dumps({"model":meta["run"],
        "versions": versions, "basis_sha256": meta["basis_sha256"],
        "encoder": "firmware/encoder_fast.c with FGX_DSP_SHIM; 2048/2048 golden floats exact",
        "scope": "Exploratory synthetic screen; nearest-reference surrogate, not full firmware replay",
        "input": "128x128 RGB565, upright, cam_frame_to_chw; teacher receives 128px RGB888 diagnostic images",
        "synthetic_scene_count_per_case": 1, "capture_conditions_per_case": 3,
        "weights_crc32":meta["crc32"],"profiles":PROFILES,"results":results},indent=2)+"\n")


if __name__ == "__main__":
    main()
