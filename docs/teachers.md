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
