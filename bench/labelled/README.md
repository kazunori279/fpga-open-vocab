# Labelled frames, for the probes that need ground truth and no board

A probe that asks "can the model tell these apart" needs frames somebody has
labelled by eye. Five scripts need the same ones -
[`../../tools/probe_open.py`](../../tools/probe_open.py),
[`probe_noise.py`](../../tools/probe_noise.py),
[`probe_teacher.py`](../../tools/probe_teacher.py),
[`probe_gemini.py`](../../tools/probe_gemini.py) and
[`probe_inherit.py`](../../tools/probe_inherit.py) - and until 2026-09-08 all
five hardcoded four filenames that lived in `/tmp/snaps`.

**They are gone.** `/tmp` was cleared. That is the third thing this repo has
lost to `/tmp` after two bench logs, and this one was worse than a log: it was
the labelled set five results rested on, so those results could not be
re-derived, re-checked against a new checkpoint, or disputed. The numbers in
`probe_open.py`'s docstring from 2026-08-07 are now history that cannot be
reproduced, and they are marked as such in the file.

This directory exists so there is a fourth time that does not happen.

## The rule

A labelled set lives here, in the repo, as a manifest of `label`, `camera arm`
and a path from the repo root - **not** as a copy of the images. The frames
stay in the bench run that produced them. One copy, so the label cannot drift
away from the pixels it describes, and the provenance is the filename: the run,
and the frame number inside it.

## `book-20260908.tsv`

Fifteen frames from the three cue runs of 2026-09-08: six CLOSED, six OPEN,
three EMPTY. The scene at each snapshot is fixed by the cue schedule, which
records its boundaries at the cue rather than inferring them afterwards, and
all fifteen were checked by eye before being written down.

| run | arm | note |
|---|---|---|
| [`m9_cue-20260908-0602`](../cue/m9_cue-20260908-0602.log) | gain locked, and the lock took | die witness `gain 06 06` |
| [`m9_cue-20260908-0610`](../cue/m9_cue-20260908-0610.log) | free | the control arm |
| [`m9_cue-20260908-0615`](../cue/m9_cue-20260908-0615.log) | gain lock **did not take** | die witness `gain 08 0d`; run stopped at frame 405 |

The arm column is a covariate, not a label. It is there because these frames
came off three different camera configurations and a reader is entitled to ask
whether a separation is the scene or the camera; `probe_open.py` prints every
frame's value in manifest order so that question can be answered by looking,
and on 2026-09-08 the answer was that the classes interleave across the arms
rather than blocking by them.

The third run's lock failure does not disqualify its frames. Its scenes were
staged and checked like the others, and for a question about the *model* the
camera arm is input diversity. It disqualifies that run from
[issue #30](https://github.com/kazunori279/fpga-open-vocab/issues/30)'s paired
comparison, which is a different question and recorded in that run's own log.

## What a set here is not

- **Not a benchmark.** Fifteen frames of one book on one desk in one morning.
  It can show an axis is inverted; it cannot rank two checkpoints' accuracy.
- **Not a substitute for a bench.** It has no camera drift, no int4, no link
  and no enrolment. `probe_open.py`'s 2026-09-08 entry is precisely a case
  where the offline answer and the bench answer disagreed, and the gap between
  them is where the remaining work is.
- **Not fixed forever.** When a set stops being the right question, add a new
  manifest next to this one. Do not edit a manifest a recorded result was
  measured on.
