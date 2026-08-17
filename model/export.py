# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch"]
# ///
"""Export the int8 student for M5: a flat weight blob and golden test vectors.

    uv run model/export.py --run train2017

`quantize.py` is *fake* quantization - it rounds to the integer grid but does the
arithmetic in float. That is the right level for a GO/NO-GO gate, but it is not
what the MCU will run, and the difference is not cosmetic: a real pipeline
accumulates int32 and requantizes once per output, so rounding lands in
different places. **The C in firmware/encoder.c, not quantize.py, is the integer
contract M6 has to match bit-exactly.**

So this script does three things, in an order chosen so a mistake in the third is
not blamed on the first:

1. Fold BN and quantize exactly as `quantize.py` does, reusing its code rather
   than reimplementing it - the scales in `quant.json` must be the scales here.
2. Run the **true integer pipeline in numpy** and check it against PyTorch
   fake-quant. This is the step that proves the integer semantics are faithful,
   in a language where the answer is easy to inspect. If it fails, no amount of
   debugging C will help.
3. Write `weights.bin` and `testvec.bin`.

## The integer semantics, stated once

For each layer, with `x` the input codes and `w` the int8 weights:

    acc[c]  = sum(x * w[c])                        int32
    codes   = clamp((acc[c] + bias_q[c]) * M[c] rounded >> s[c], 0, 255)
                                                   if the layer emits codes
    out[c]  = max((acc[c] + bias_q[c]) * mult[c], 0)   otherwise, float

where `bias_q[c] = round(bias[c] / (in_scale * w_scale[c]))` folds the float bias
into accumulator units, and `mult[c] = in_scale * w_scale[c] / out_scale` folds
dequantization and requantization into one multiply.

**Requantization is fixed point for the layers that emit codes.** It was float
until M15, and the reason it was float has an expiry date on it: the RP2350 has
a single-precision FPU, so on the MCU a float multiply and an IEEE round cost
nothing. But M15 moves that epilogue into the FPGA, where the whole point is to
drain one byte per accumulator instead of four - and a float datapath in LEs is
three times the logic with round-half-even to get right in three places. So the
per-channel `mult[c]` becomes `M[c] * 2^-s[c]` with `M` in [2^17, 2^18), and
`(acc + bias) * M` is an exact integer product where the float path rounded
twice. `M` and `s` are *not* stored: `rq_pick()` derives them from `mult[c]`
with one frexp, and firmware/encoder.h does the same, so weights.bin is
unchanged and there is nothing to keep in step.

conv7 and the head keep the float multiplier. They emit float, so there is no
byte for the fabric to send and no reason to move them.

tools/probe_rq.py is the measurement behind this: over 5,000 val2017 images the
two epilogues disagree on 0.0035% of codes, always by exactly 1, and mean AUC
and retention are the same to four decimal places (0.8856, 94%).

Keeping the *accumulator* integer is what matters most: that is the part the
FPGA computes, and `acc[c]` is where bit-exactness is checked.

Only conv0 sees signed input (the normalized image spans [-1, 1]). Every later
layer is fed post-ReLU codes, which are unsigned 0..255 - a full extra bit of
resolution, and what a GEMM tile wants anyway.

conv7 and the head emit float rather than codes: conv7's output is 4x4x256, small
enough that keeping it in float costs 16 KB and avoids quantizing immediately
before a global average pool that would wash the codes out anyway.
"""

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import distill
import numpy as np
import quantize as q_mod
import spaces
import student as student_mod
import torch

MAGIC = b"FGX5"
# 2 spends the trailing `reserved` float on a per-layer weight width. See
# FGX_VERSION in firmware/encoder.h for why this is a version bump and not a
# quiet reuse of spare bytes.
VERSION = 2
# One descriptor per layer, packed little-endian. Keep in sync with
# firmware/encoder.h - the C reads this struct back byte for byte.
DESC_FMT = "<BBBB HH HHHH II I B3x"
DESC_SIZE = struct.calcsize(DESC_FMT)
# Asserted, not assumed: encoder.h declares a struct that must match this byte
# for byte, and a silent size change here would be read as garbage offsets there.
assert DESC_SIZE == 32, DESC_SIZE

KIND_CONV, KIND_LINEAR = 0, 1

# Only these two are storable. 5 and 6 bits measured well in tools/probe_int4.py
# but there is no packing for them that keeps a channel byte-aligned, and the
# gate found int4 already free, so nothing is given up by refusing them.
WIDTHS = {127: 8, 7: 4}


def _grid(qlayer):
    """(clip bound, storage width) for one quantized layer, from its own w_qmax.

    Read off the module rather than passed down, because tools/probe_int4.py's
    pin_to_8() works by writing w_qmax and w_scale back onto named layers. Any
    policy expressed that way arrives here without export.py having to know
    what the policy was.
    """
    qmax = round(float(qlayer.w_qmax))
    if qmax not in WIDTHS:
        raise SystemExit(
            f"export: w_qmax {qmax} has no packing; supported: "
            + ", ".join(f"{q} (int{b})" for q, b in WIDTHS.items()))
    return qmax, WIDTHS[qmax]


def pack_weights(w_q, w_bits):
    """Flatten to the byte stream the descriptor's w_off points at.

    int4 goes two per byte, **low nibble first**, in the same flat order int8
    uses. That ordering is not arbitrary: gemm_tile reads lane j's weight as
    wreg[4*j +: 4], so element 0 has to be the low nibble of the first byte for
    the tile's lanes to line up with the host's channels without a shuffle.
    """
    flat = w_q.reshape(-1)
    if w_bits == 8:
        return flat.tobytes()
    lo = flat[0::2].astype(np.uint8) & 0x0F
    hi = flat[1::2].astype(np.uint8) & 0x0F
    return (lo | (hi << 4)).astype(np.uint8).tobytes()


class Layer:
    """One exported layer: int8 weights, int32 bias, float per-channel multiplier."""

    def __init__(self, kind, w_q, bias_q, mult, in_shape, out_shape,
                 stride, relu, unsigned_in, w_bits=8):
        self.kind = kind
        self.w_bits = w_bits          # 8, or 4 and packed two per byte at export
        self.w_q = w_q                # int8, (out, in, k, k) or (out, in)
        self.bias_q = bias_q          # int32, (out,)
        self.mult = mult              # float32, (out,)
        self.in_shape = in_shape      # (c, h, w) or (c,)
        self.out_shape = out_shape
        self.stride = stride
        self.relu = relu
        self.unsigned_in = unsigned_in

    @property
    def ksize(self):
        return self.w_q.shape[-1] if self.kind == KIND_CONV else 1


def build_layers(qmodel) -> tuple[list[Layer], float]:
    """Turn the calibrated QuantConv graph into flat integer layers.

    Output scales chain: layer i's output codes are quantized with layer i+1's
    *input* scale, because that is where quantize.py puts the rounding. Getting
    this off by one would be a subtle, plausible-looking accuracy loss rather
    than an obvious failure, so it is derived from the graph rather than a list.
    """
    convs = list(qmodel.features)
    head = qmodel.head
    layers = []

    # conv_i's output scale is conv_{i+1}'s input scale; conv7 (the last) emits
    # float, so its "out scale" is 1.0 and no clamp to 0..255 happens.
    for i, layer in enumerate(convs):
        in_scale = float(layer.in_scale)
        w_scale = layer.w_scale.cpu().numpy().astype(np.float64)
        emits_codes = i + 1 < len(convs)
        out_scale = float(convs[i + 1].in_scale) if emits_codes else 1.0

        # The clip bound is the layer's own, not a literal 127: quantize.py
        # picked w_scale against w_qmax, and clipping wider than the scale was
        # chosen for would put codes outside the grid the accuracy gate
        # measured. pin_to_8() sets w_qmax back to 127 per layer, so this is
        # where the mixed-width policy actually lands.
        qmax, w_bits = _grid(layer)
        w = layer.layer.weight.detach().cpu().numpy().astype(np.float64)
        w_q = np.clip(np.round(w / w_scale[:, None, None, None]),
                      -qmax, qmax).astype(np.int8)

        bias = layer.layer.bias.detach().cpu().numpy().astype(np.float64)
        step = in_scale * w_scale
        bias_q = np.round(bias / step).astype(np.int32)
        mult = (step / out_scale).astype(np.float32)

        cin, _k = w.shape[1], w.shape[2]
        layers.append(Layer(KIND_CONV, w_q, bias_q, mult, (cin, 0, 0), (w.shape[0], 0, 0),
                            layer.layer.stride[0], True, i > 0, w_bits))

    # The head is fed the global average pool of conv7's float output, quantized
    # with head.in_scale. Unsigned, because conv7 ends in a ReLU.
    in_scale = float(head.in_scale)
    w_scale = head.w_scale.cpu().numpy().astype(np.float64)
    qmax, w_bits = _grid(head)
    w = head.layer.weight.detach().cpu().numpy().astype(np.float64)
    w_q = np.clip(np.round(w / w_scale[:, None]), -qmax, qmax).astype(np.int8)
    bias = head.layer.bias.detach().cpu().numpy().astype(np.float64)
    step = in_scale * w_scale
    layers.append(Layer(KIND_LINEAR, w_q, np.round(bias / step).astype(np.int32),
                        step.astype(np.float32), (w.shape[1],), (w.shape[0],),
                        1, False, True, w_bits))
    return layers, in_scale


def conv_int(x, layer: Layer):
    """int32 convolution, 3x3 stride s pad 1, via im2col.

    Accumulates in int32 exactly as the C will. numpy would happily promote to
    int64 on some paths, so the accumulate is done in int32 explicitly - a
    reference that silently uses wider accumulators than the target is not a
    reference.
    """
    cin, h, w = x.shape
    cout = layer.w_q.shape[0]
    k, s = layer.ksize, layer.stride
    oh, ow = (h + 2 * 1 - k) // s + 1, (w + 2 * 1 - k) // s + 1

    xp = np.zeros((cin, h + 2, w + 2), dtype=np.int32)
    xp[:, 1:h + 1, 1:w + 1] = x

    cols = np.empty((cin * k * k, oh * ow), dtype=np.int32)
    idx = 0
    for c in range(cin):
        for ky in range(k):
            for kx in range(k):
                cols[idx] = xp[c, ky:ky + oh * s:s, kx:kx + ow * s:s].reshape(-1)
                idx += 1

    wm = layer.w_q.reshape(cout, -1).astype(np.int32)
    acc = (wm @ cols).astype(np.int32)          # (cout, oh*ow)
    return acc.reshape(cout, oh, ow), oh, ow


RQ_MBITS = 18       # keep in step with FGX_RQ_MBITS in firmware/encoder.h
RQ_SMAX = 63


def rq_pick(mult) -> tuple[np.ndarray, np.ndarray]:
    """Per channel, the (M, s) that replaces a float `mult`. M15's contract.

    Deliberately the same closed form as fgx_rq_pick() in firmware/encoder.h,
    written out element by element rather than vectorised: this is a reference
    for a fabric datapath, and a numpy expression that disagreed with the C on
    one channel out of 1,568 would be found on hardware instead of here.
    cout <= 512, so the loop is free.

    frexp splits mult into f * 2^e with f in [0.5, 1), so f * 2^(e+s) lands in
    [2^17, 2^18) exactly when e + s is RQ_MBITS. The obvious alternative -
    growing s until mult * 2^s clears 2^17 - was checked against this on all
    1,568 exported channels and agrees on every one.
    """
    hi = float(1 << RQ_MBITS)
    Ms, ss = [], []
    for mu in np.asarray(mult, dtype=np.float64).ravel():
        if not mu > 0.0:
            Ms.append(0); ss.append(1); continue
        s = min(RQ_MBITS - math.frexp(mu)[1], RQ_SMAX)
        M = int(math.ldexp(mu, s) + 0.5)
        if float(M) >= hi and s > 0:
            s -= 1
            M = int(math.ldexp(mu, s) + 0.5)
        Ms.append(M); ss.append(s)
    return np.array(Ms, dtype=np.int64), np.array(ss, dtype=np.int64)


def run_int(x_codes: np.ndarray, layers: list[Layer], head_in_scale: float,
            fixed: bool = True):
    """The golden integer pipeline. Input is int8 codes, output is the float 512-d.

    `fixed` selects M15's fixed-point epilogue for the code-emitting layers and
    is the contract; `fixed=False` is the pre-M15 float epilogue, kept only so
    tools/probe_rq.py can score the two against each other on one model. conv7
    and the head keep the float multiplier either way.

    **Every float here is float32, in the order encoder.c evaluates it.** This
    used to accumulate the epilogue in float64 and pool with `x.mean()`, which
    is a more accurate pipeline than the one that ships and therefore the wrong
    reference. The two agreed bit for bit at int8 and that was luck, not a
    property: `(acc + bias)` needs 26 bits and float32 carries 24, so the float64
    path rounds once and the target rounds twice, and the results part company
    whenever the second rounding lands on a tie. int4 makes `mult` coarser, which
    makes ties more common, and it took exactly one of four test images to find
    one - a single output code off by 1, 1-cos = 1.3e-06 in the embedding.

    So the fix is not a tolerance. firmware/encoder.c is the contract; this
    function's job is to model it, including the parts that lose precision:
    float32 throughout, the pool summed sequentially rather than pairwise, and
    `s / hw * (1/head_in_scale)` rather than `s / hw / head_in_scale`.
    """
    f32 = np.float32
    x = x_codes.astype(np.int32)
    for i, layer in enumerate(layers[:-1]):
        acc, _oh, _ow = conv_int(x, layer)
        emits_codes = i + 1 < len(layers) - 1

        if fixed and emits_codes:
            # fgx_code_fixed(). No relu: the clip to [0, 255] below already
            # sends every negative to zero, which is what relu would have done
            # and is why the fabric carries no relu logic.
            M, s = rq_pick(layer.mult)
            t = (acc.astype(np.int64) + layer.bias_q[:, None, None]) \
                * M[:, None, None]
            r = (t + (np.int64(1) << (s[:, None, None] - 1))) >> s[:, None, None]
            x = np.clip(r, 0, 255).astype(np.int32)
            continue

        # (float)(acc + bias) * mult, and the cast is where the 26-bit
        # accumulator loses its low bits - exactly as fgx_requant() does.
        out = (acc + layer.bias_q[:, None, None]).astype(f32) \
            * layer.mult[:, None, None].astype(f32)
        out = np.maximum(out, f32(0.0))
        # conv7 does not emit codes and stays float32.
        x = np.clip(np.rint(out), 0, 255).astype(np.int32) if emits_codes else out

    # fgx_pool_head() runs a scalar float32 accumulator over the hw positions in
    # order. np.sum would use pairwise summation and np.mean would divide in
    # float64; both are better and neither is what the MCU does.
    flat = x.reshape(x.shape[0], -1).astype(f32)
    hw = flat.shape[1]
    s = np.zeros(flat.shape[0], dtype=f32)
    for j in range(hw):
        s = (s + flat[:, j]).astype(f32)
    inv = f32(1.0) / f32(head_in_scale)
    codes = np.clip(np.rint(s / f32(hw) * inv), 0, 255).astype(np.int32)

    head = layers[-1]
    acc = (head.w_q.astype(np.int32) @ codes).astype(np.int32)
    return (acc + head.bias_q).astype(f32) * head.mult.astype(f32)


def cosine(a, b):
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def pack(layers: list[Layer], head_in_scale: float, in_scale: float,
         in_size: int) -> bytes:
    """Header, descriptors, then weights / biases / multipliers, each contiguous.

    Sections are separate rather than interleaved per layer so the MCU can DMA
    the weight section straight into PSRAM and leave the small int32/float
    sections in SRAM, where they are read once per output channel.
    """
    w_blob, b_blob, m_blob = bytearray(), bytearray(), bytearray()
    descs = bytearray()

    # Shapes are recomputed here rather than trusted, so the descriptor table
    # cannot disagree with what the reference actually ran.
    h = w = in_size
    cin = layers[0].w_q.shape[1]
    for i, layer in enumerate(layers):
        if layer.kind == KIND_CONV:
            cout, k, s = layer.w_q.shape[0], layer.ksize, layer.stride
            oh, ow = (h + 2 - k) // s + 1, (w + 2 - k) // s + 1
        else:
            cout, k, s, oh, ow = layer.w_q.shape[0], 1, 1, 1, 1

        # A 4-bit layer packs flat, so output channel `oc` starts on a byte
        # boundary only if cin*k*k is even. encoder.c refuses a blob that
        # breaks this; failing here is better, because here we know which layer
        # and can say so. conv0 is the only odd one (3*3*3 = 27) and the
        # accuracy work pinned it to 8 anyway.
        n = layer.w_q[0].size
        if layer.w_bits == 4 and n % 2:
            raise SystemExit(
                f"export: layer {i} has an odd channel stride ({n}) and cannot "
                f"be packed at int4; pin it to 8 bits")

        descs += struct.pack(
            DESC_FMT,
            layer.kind, 1 if layer.relu else 0, 1 if layer.unsigned_in else 0, k,
            cin, cout, h, w, oh, ow,
            len(w_blob), len(b_blob), len(m_blob),
            layer.w_bits)
        w_blob += pack_weights(layer.w_q, layer.w_bits)
        b_blob += layer.bias_q.astype("<i4").tobytes()
        m_blob += layer.mult.astype("<f4").tobytes()
        cin, h, w = cout, oh, ow

    header = struct.pack(
        "<4sIIIIII ff",
        MAGIC, VERSION, len(layers), in_size, layers[0].w_q.shape[1],
        layers[-1].w_q.shape[0], DESC_SIZE,
        in_scale, head_in_scale)
    offsets = struct.pack("<III", len(w_blob), len(b_blob), len(m_blob))
    return bytes(header + offsets + descs + w_blob + b_blob + m_blob)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="train2017")
    ap.add_argument("--images", type=int, default=4, help="golden test vectors")
    ap.add_argument("--batches", type=int, default=8)
    # M14. The defaults reproduce every blob exported before it, byte for byte
    # apart from the version field; --wbits 4 --wsearch --ends8 is the
    # configuration tools/probe_int4.py measured as free (94% retention, the
    # same as int8, at 4.38 bits per weight).
    ap.add_argument("--wbits", type=int, default=8, choices=(4, 8))
    ap.add_argument("--wsearch", action="store_true",
                    help="MSE clip-ratio search per channel (probe_int4's 'mse')")
    ap.add_argument("--ends8", action="store_true",
                    help="pin conv0 and the head to 8 bits (probe_int4's 'ends8')")
    # The output directory used to be run/"export" with no way to say
    # otherwise, which makes exporting a second width a destructive act: M14's
    # int4 blob is what the shipped firmware links, and overwriting it to
    # measure an int8 control would cost the thing being controlled against.
    ap.add_argument("--out", default="export",
                    help="directory under the run to write into")
    args = ap.parse_args()

    import data
    import teacher as teacher_mod
    from torch.utils.data import DataLoader

    run = Path(distill.RUNS) / args.run
    out_dir = run / args.out
    out_dir.mkdir(exist_ok=True)

    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    device = torch.device("cpu")    # exactness matters more than speed here

    model = student_mod.Student()
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    folded = q_mod.fold_bn(model).to(device)
    qmodel = q_mod.quantize(folded, w_bits=args.wbits,
                            search=args.wsearch).to(device)
    if args.ends8:
        # Deliberately the same two lines probe_int4.py's pin_to_8() runs,
        # including search=False for the pinned layers: the gate measured
        # maxabs there, and an MSE-searched conv0 would be a different model
        # from the one that scored 94%.
        pinned = {"conv0": qmodel.features[0], "head": qmodel.head}
        for m_ in pinned.values():
            m_.w_qmax = 127.0
            m_.w_scale = q_mod.pick_w_scale(m_.layer.weight.detach(), 127.0, False)

    split = ckpt["split"]
    names = data.image_list(split)
    targets = teacher_mod.load_cache(split)
    hold = set(ckpt["holdout"])
    calib_idx = np.array([i for i in range(len(names)) if i not in hold])
    loader = DataLoader(
        distill.DistillSet(split, names, calib_idx, targets,
                           distill.student_transform(False, False)),
        batch_size=64, shuffle=False, num_workers=4)

    # Recalibrate rather than reading quant.json: the scales must come from the
    # same code path quantize.py used, and re-deriving them here means a change
    # to CALIB_PERCENTILE cannot silently desynchronize the export from the gate.
    #
    # Seeded, because QuantConv.observe() subsamples with torch.randint to get
    # under torch.quantile's element limit. Unseeded, every export would produce
    # slightly different activation scales - harmless for accuracy (the observed
    # drift is ~3e-4) but fatal for M6, which has to check a *fixed* blob
    # bit-exactly. The blob is a contract; contracts do not get to be random.
    torch.manual_seed(0)
    seen = q_mod.calibrate(qmodel, loader, device, args.batches)
    print(f"calib     : {seen} images")

    saved = json.loads((run / "quant.json").read_text())
    drift = max(abs(float(l.in_scale) - saved["activation_scales"][n])
                for n, l in zip(list(saved["activation_scales"]),
                                list(qmodel.features) + [qmodel.head], strict=False))
    print(f"scales    : max drift vs quant.json {drift:.3e}")

    layers, head_in_scale = build_layers(qmodel)
    total_w = sum(l.w_q.size for l in layers)
    stored = sum(len(pack_weights(l.w_q, l.w_bits)) for l in layers)
    # Mean bits per weight is the figure WGT scales with, and it is what
    # probe_int4.py reported, so print it in the same units for comparison.
    print(f"weights   : {total_w:,} values, {stored:,} B stored "
          f"= {stored / 2**20:.2f} MiB, {8 * stored / total_w:.2f} bits/weight")
    print("widths    : "
          + " ".join(f"{'head' if l.kind == KIND_LINEAR else f'conv{i}'}"
                     f":{l.w_bits}" for i, l in enumerate(layers)))

    # Golden vectors, and the check that the integer semantics are right.
    pixels = next(iter(loader))[0][:args.images].to(device)
    with torch.no_grad():
        ref = qmodel(pixels).cpu().numpy().astype(np.float64)

    in_scale = float(qmodel.features[0].in_scale)
    codes = np.clip(np.rint(pixels.cpu().numpy() / in_scale), -127, 127).astype(np.int8)

    print()
    print(f"{'image':<8} {'int-vs-fakequant':>18} {'|int|':>10} {'|fq|':>10}")
    cos = []
    ints = []
    for i in range(len(codes)):
        y = run_int(codes[i], layers, head_in_scale)
        ints.append(y)
        c = cosine(y, ref[i])
        cos.append(c)
        print(f"{i:<8} {c:>18.6f} {np.linalg.norm(y):>10.3f} "
              f"{np.linalg.norm(ref[i]):>10.3f}")

    blob = pack(layers, head_in_scale, in_scale, student_mod.INPUT_SIZE)
    (out_dir / "weights.bin").write_bytes(blob)

    # Test vectors carry the *quantized* input codes, not pixels: the C then
    # starts at exactly the point PyTorch's fake-quant starts, so a mismatch
    # cannot be blamed on image decoding or resize filters.
    tv = bytearray(struct.pack("<4sII", b"FGXT", len(codes), codes[0].size))
    for i in range(len(codes)):
        tv += codes[i].tobytes()
        tv += np.asarray(ints[i], dtype="<f4").tobytes()
        tv += np.asarray(ref[i], dtype="<f4").tobytes()
    (out_dir / "testvec.bin").write_bytes(bytes(tv))

    # Which space these weights emit into, written beside them.
    #
    # Both spaces this project has shipped are 512-d - ViT-B/16's, and SigLIP 2
    # SO400M's squeezed 1152 -> 512 by a frozen PCA. So a host that encodes its
    # text queries with the wrong one produces a dot product that *succeeds* and
    # means nothing: no shape error, no NaN, just scores that are noise. Nothing
    # in weights.bin says which space it is, and there is no free field to put it
    # in without moving FGX_VERSION.
    #
    # So it goes here instead, and host/demo.py *derives* its query encoder from
    # this file rather than choosing one. It cannot pick the wrong space because
    # it no longer picks. crc32 is over the same bytes written to weights.bin,
    # and firmware/m9.c prints its own over the blob it actually booted with -
    # which is what catches the remaining hole, a host reading this file while
    # the board still holds an older flash.
    spec, basis = spaces.resolve(ckpt.get("teacher", ""))
    side = {
        "run": args.run,
        "teacher": ckpt.get("teacher", ""),
        "spec": spec,
        "basis": basis.name if basis else None,
        "embed_dim": int(layers[-1].w_q.shape[0]),
        "wbits": args.wbits,
        "wsearch": bool(args.wsearch),
        "ends8": bool(args.ends8),
        "bytes": len(blob),
        "crc32": f"0x{zlib.crc32(blob):08X}",
    }
    (out_dir / "export.json").write_text(json.dumps(side, indent=2) + "\n")

    print()
    print(f"wrote     : {out_dir / 'weights.bin'} ({len(blob):,} bytes)")
    print(f"wrote     : {out_dir / 'testvec.bin'} ({len(tv):,} bytes)")
    print(f"wrote     : {out_dir / 'export.json'} ({side['crc32']}, "
          f"{spec}{'' if basis is None else ' + ' + basis.name})")

    worst = min(cos)
    print(f"\nworst cosine, true int8 vs PyTorch fake-quant : {worst:.6f}")
    ok = worst > 0.999
    print("\nRESULT : " + ("PASS - the integer pipeline reproduces fake-quant"
                           if ok else "FAIL - integer semantics disagree with quantize.py"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
