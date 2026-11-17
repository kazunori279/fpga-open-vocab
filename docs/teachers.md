# Selecting a distillation teacher

`tools/cache_teacher.py --backend siglip|gemma` selects the teacher when building
training targets. Both backends emit normalized 512-dimensional image and text
vectors. The student's architecture and wire format stay unchanged.

| Backend | Model | Image/text projection | Text policy |
|---|---|---|---|
| `siglip` | `ViT-SO400M-14-SigLIP2:webli` | Shipped frozen PCA to 512d | Existing seven-template ensemble |
| `gemma` | `google/embeddinggemma-2`, pinned revision | First 512 MRL dimensions, re-normalized | Bare text, no task prefix |

Gemma inference uses fp32 and disables the audio encoder. Its pinned revision is
in `model/teacher_backend.py`; changing it creates a different space identity.
Neither a SigLIP PCA nor SigLIP text vectors can be used with a Gemma student.


## Run the Gemma model on the device

The local ready-to-flash package is `artifacts/demo-gemma/`. It contains
`forgix_m9.uf2`, the matching `export/`, the M16 NARROW `gemm_top.hex`,
`run.sh`, and a checksum manifest. The UF2 includes the Gemma student weights
(CRC `0xAFF86EF7`); the full teacher runs on the host only to encode text.
The equivalent SigLIP package is `artifacts/demo-siglip/` (CRC `0x384A95D4`).
These generated local packages are ignored by Git; the model exports are tracked.

From the repository root, with the board connected:

```sh
picotool reboot -f -u
picotool load artifacts/demo-gemma/forgix_m9.uf2
picotool verify artifacts/demo-gemma/forgix_m9.uf2
diskutil unmount /Volumes/RP2350
picotool reboot
artifacts/demo-gemma/run.sh 'an open hand' 'a fist'
```

If already in BOOTSEL, start at `picotool load`. The launcher uses the package's
matching model and NARROW bitstream and guides enrolment of the two scenes.
To switch back, flash the SigLIP package and use its `run.sh`.
The direct host command, without the launcher, is:

```sh
uv run --script host/spot.py \
  --export model/runs/compare43-gemma-s30k-seed0/export \
  --bitstream rtl/bitstreams/m16/gemm_top.hex \
  'an open hand' 'a fist'
```

To rebuild the Gemma UF2 from the tracked export:

```sh
cmake -S firmware -B firmware/build-demo-gemma -G Ninja \
  -DFGX_EXPORT="$PWD/model/runs/compare43-gemma-s30k-seed0/export" \
  -DLINK_CFG=NARROW -DGP_KPACK=1 -DFGX_SYS_KHZ=320000 \
  -DPICO_TOOLCHAIN_PATH="$HOME/toolchains/arm-gnu-toolchain-14.2.rel1-darwin-arm64-arm-none-eabi"
cmake --build firmware/build-demo-gemma --target forgix_m9
```

The generated image is `firmware/build-demo-gemma/forgix_m9.uf2`.
See [building and flashing](building.md) for toolchain setup. No RP2350 was
connected when this package was prepared, so flashing and the first live run
remain to be performed; the binary and matching host assets are ready.

## Cache, train, evaluate, export

```sh
# Choose either backend. Use the same backend for both splits.
uv run --script tools/cache_teacher.py --backend gemma --split train2017
uv run --script tools/cache_teacher.py --backend gemma --split val2017

# Copy the --targets path printed by the train2017 cache command.
uv run --script model/distill.py --split train2017 \
  --targets model/cache/teacher_8075c704d48b6e4e_train2017.npy \
  --epochs 20 --batch 512 --lr 0.006 --infonce 0.3 --name gemma-experiment

uv run --script model/evaluate.py --split val2017 --run gemma-experiment
uv run --script model/quantize.py --run gemma-experiment
uv run --script model/export.py --run gemma-experiment --wbits 4 --wsearch --ends8
```

Replace `gemma` with `siglip` and choose a separate run name to train a SigLIP
candidate. SigLIP defaults to the committed export's PCA; `--basis PATH` permits
another frozen basis and produces a distinct identity.

`--subset N --holdout H` on the cache command supports small experiments; match
these arguments when training. Non-encoded rows are zero and cannot be used as
training targets. Evaluation on a different split requires a complete cache.
Cache files cannot be overwritten. A partial cache must be explicitly moved
aside before making the full cache of that space/split; preserve its sidecars
with any checkpoint that still depends on it.

Cache sidecars bind the spec, prompt policy, projection hash, image ordering,
and target-file hash. Checkpoints record the space ID; evaluation and export
reject a mismatch. The exported bundle carries the spec, space ID, and its own
PCA when applicable, so host inference does not need the training cache.
Legacy CLIP/SigLIP checkpoints and exports remain supported.

## Select the matching bundle on the host

After separately building/flashing a student that has passed validation, use
its matching export on the host:

```sh
uv run --script host/spot.py --export model/runs/gemma-experiment/export \
  'an open hand' 'a fist'
# host/demo.py and host/watch.py also accept --export.
```

Changing `--export` does not flash new weights. The board's weights must match
the export CRC. The host automatically loads the teacher named in that bundle;
there is deliberately no independent host teacher override. Run-specific
thresholds and calibration embeddings must also be regenerated, not reused from
SigLIP. No shipped firmware, weights or default export is changed by this feature.

This is backend plumbing, not evidence that Gemma improves the model. The
comparison in issue #43 did not establish a general advantage; training or
export smoke artifacts are not deployment candidates.

## Checks

```sh
uv run --script tools/test_teacher_backend.py
uv run make test-host
```

## Controlled comparison campaign

`uv run --script tools/run_teacher_comparison.py` runs both 30k-image,
20-epoch, seed-0 experiments serially. It records stage logs and resumable state
under `model/runs/teacher-comparison-43/`. Both students must have identical
initial-state hashes and training settings. A changed source commit stops a
resumed campaign rather than silently changing the experiment.

Target generation now maintains a `.partial.npy` and `.progress.json`; rerun
the identical cache command after interruption to resume. A per-cache lock
prevents concurrent writers. `--max-batches 1` exercises restart without
finishing the cache. Completed caches remain immutable.

Student comparison uses fixed signed margin AUC on the ten cross-scene
selection contrasts and, separately, seven correlated synthetic home cases.
The default `--int4-policy both` quantizes, exports and evaluates both models
regardless of which wins. `--int4-policy on-gain` can reproduce the historical
replacement screen (cross-scene gain > twice its contrast-level SE, nonnegative
home gain). Neither policy triggers firmware deployment or establishes
statistical confirmation. Integer evaluation
includes RGB565 input conversion; fp32 uses RGB888, so it measures the deployable
path's total change rather than isolating weight quantization alone.

### Support both demos, without a replacement gate

The goal of the demonstration is to support **both** SigLIP- and Gemma-derived
students, not require Gemma to outperform SigLIP. After the fp32 campaign,
`uv run --script tools/run_int4_comparison.py` evaluates both int4 exports
regardless of the earlier replacement-gain gate. It verifies the original
checkpoint hashes and records independent progress in
`model/runs/teacher-comparison-43/int4/`, preserving the historical decision.
`comparison-int4.json` compares the backends; `precision-changes.json` compares
each to its own fp32 result, including the RGB565 input difference. Both export
bundles remain independently selectable with `--export`; flashing matching
weights and on-board validation are separate steps.

### Matching the firmware and host for the two demo models

The comparison runs use these separate bundles:

| Demo | Export directory |
|---|---|
| SigLIP-derived student | `model/runs/compare43-siglip-s30k-seed0/export` |
| Gemma-derived student | `model/runs/compare43-gemma-s30k-seed0/export` |

Configure a **separate build directory** for each with
`-DFGX_EXPORT=/absolute/path/to/that/export`, retaining the toolchain, board,
clock and link options used for your hardware (see [building](building.md)).
Build and flash the corresponding firmware before choosing the same directory
with `host/spot.py --export` or `host/watch.py --export`. The host validates the
board's weight CRC against its bundle. This is a paired firmware/host switch,
not hot-swapping only the text encoder on an unchanged board.

The original `so400m-full-a05/export` remains the default. The two 30k-image
comparison students are additional demos, not claims that either improves that
full-data default. Their model comparison is separate from RTL-generation
experiments; no RTL files or device state are changed by the int4 evaluator.

A failed cosine diagnostic from `model/quantize.py` is retained as a quality
warning when a valid fresh calibration artifact exists. The continuation then
measures actual int4 task scores. Other failures, including integer/golden
mismatches, stop the run; they are never treated as successful validation.

### Completed int4 comparison (2026-11-17)

Mean standardized signed margin AUC (higher is better):

| Student | Cross-scene fp32 | Cross-scene int4 | Home fp32 | Home int4 |
|---|---:|---:|---:|---:|
| SigLIP | 0.6203 | 0.6053 | 0.6508 | 0.6587 |
| Gemma | 0.6171 | 0.6123 | 0.7381 | 0.6548 |

Both weight bundles are 780,720 bytes. Each C encoder matched all 2,048
golden floats exactly, and both `forgix_m9` firmware variants built with
`LINK_CFG=NARROW` and `FGX_SYS_KHZ=320000`. No device was flashed or measured.
Both int8 calibration cosine diagnostics failed the minimum 0.98 threshold
(SigLIP 0.95752; Gemma 0.94932); these remain quality warnings, despite the
successful integer implementation checks. Gemma's home score drop warrants
further calibration work before a hardware demo.

These are one-seed comparisons; the home panels are correlated synthetic cases.
The int4 path includes RGB565 input conversion, while fp32 uses RGB888.
Neither result establishes general superiority. Reports and artifact hashes
are in [the comparison summary](../bench/probe/20261117-switchable-int4/summary.json),
with checkpoint and training provenance in each export's `provenance.json`.

### Input precision and calibration diagnosis

`tools/diagnose_teacher_int4.py` separates RGB888/RGB565 input precision from
weight/activation quantization using the same C encoder and frozen prompts:

```sh
uv run --script tools/diagnose_teacher_int4.py \
  --run compare43-gemma-s30k-seed0 \
  --out model/runs/teacher-comparison-43/diagnosis/gemma
# Repeat with compare43-siglip-s30k-seed0 and a separate output directory.
```

It verifies the Python RGB565 bit expansion/input quantization against the
actual C camera path, reconstructs the published int4 weights byte for byte,
and compares fixed calibration percentiles (99.9%, 99.99%, maximum) plus int8.
All candidates use the same first 512 training images, batch order and seed.
Generated candidate blobs are diagnostics, not deployment bundles; the
published exports remain unchanged.

For Gemma, home AUC is 0.7381 (fp32/RGB888), 0.7024 (fp32/RGB565),
0.6667 (int4/RGB888), and 0.6548 (int4/RGB565). Both input precision and
quantization contribute; the effects interact, so they are not independent
additive penalties. The int8/RGB565 diagnostic reaches 0.7341. Int4 calibration
at 99.99% and maximum reaches only 0.6429 and 0.6627 respectively: improved
embedding cosine does not imply improved task ranking. No calibration candidate
is promoted from this exploratory suite.

The next model-quality experiment should test selective higher precision or
quantization-aware fine-tuning with camera-equivalent input augmentation, using
separate validation scenes before choosing a demo bundle. Calibration-only
changes have not resolved the regression. Full results, per-case margins and
cumulative per-layer cosines are in
[the diagnosis summary](../bench/probe/20261117-int4-diagnosis/summary.json).
