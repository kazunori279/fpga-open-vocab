# Straddle does not predict a run, and the ninth predictor fails like the others

*2026-09-09. No board time: this is 47 archived cue logs re-read, and 31 of them
joined against accuracy they were already scored for. Issue #35.*

The [enrolment-drift probe](../20260909-enroldrift/) found that on *an opened
book* / *a closed book* the two enrolled references land on the **same** side of
the text axis in 6 rounds of 6, while *a red cube* / *a green cube* straddle it
in 6 of 6 — and the book pair is the one that classifies badly. That suggested a
check an operator could run the moment enrolment finishes, with no held-out set
and no ground truth: **does the reference for class k score highest on phrase
k?**

It does not survive. Inside one pair, with the phrases held fixed, straddle
separates a 78.1% mean from a 67.7% one at p = 0.43, and reverses under a swap
of the query order. It is the ninth enrolment-time predictor this repo has put
up and the ninth to fail, and like four of the earlier ones it spent a while
pointing the wrong way.

## Getting the answer out of logs that already exist

The frame lines carry every query's `z`. The enrolment window is delimited by
its cue and its receipt. The reference is the mean of `c[] = z[] - mean(z)` over
that window. So the enrolment side of the question can be reconstructed from any
log that has both, and `tools/score_cue.py` scores the same file — which means
straddle and accuracy come out of **one log, one boot, one set of conditions**,
with no month-apart confound and no bench to run.

47 of the 54 logs in [`bench/cue/`](../../cue/) reconstruct. The other seven
predate the enrolment receipt.

**Two firmware generations.** The older one prints `name, level L (N frames)`
with no scatter and no visit number, and its `N` is per-visit where the newer
one's is cumulative. That is 24 of the 47 logs, and it is most of the accuracy
the repo has, so both are read.

### The window is not the one it looks like

The receipt does not print after the window's last frame line. It prints
*before* it — the board folds the frame in, prints the receipt, then prints that
frame's own score line. Reading the 20 lines above the receipt is off by one and
drops the most informative frame of the window.

Measured, not assumed. The two 2026-09-09 probe logs carry `'T'` dumps of the
references the board actually held:

| window | worst component error, staged | cubes |
|---|---|---|
| **19 above + 1 below** | **0.0007** | **0.0006** |
| 20 above | 0.0290 | 0.0921 |
| 18 above + 2 below | 0.0298 | 0.0839 |
| 20 above + 1 below | 0.0221 | 0.0719 |

0.0007 is the rounding floor of a two-decimal score averaged over 20 frames.
There is nothing left in it to explain.

That check needs a `'T'` dump, and only two logs have one. The other 45 get a
weaker but universal one: the receipt's own `level` is `mean(z)` over the window,
so **every log states the answer to half of the reconstruction on its own
line**. Across all 47, the worst residual is 0.0050 — again exactly the print
quantum. The window is right on both firmware generations.

## What the join says

Ordered by straddle across everything scored, it looks like a result:

| straddle | n | mean | min | max |
|---|---|---|---|---|
| 2/2 | 11 | 82.8% | 63.3% | 97.6% |
| 1/2 | 29 | 65.7% | 0.0% | 100.0% |
| 0/2 | 1 | 47.6% | — | — |

Monotone, 35 points end to end. It is the pair confound. The 2/2 group is where
the cube and bag runs are; the 1/2 group is where the glass runs are; and this
repo has known since 2026-08-17 that four pairs benched the same afternoon score
95.8 / 90.8 / 50.0 / 34.2%. Sorting good pairs from bad ones tells nobody
anything they could not get by reading the phrase list.

The book pair is the only one with a sample — 31 of the 40 scored runs, both
query orderings, phrases held fixed:

| straddle | n | mean | min | max |
|---|---|---|---|---|
| 2/2 | 8 | 78.1% | 63.3% | 96.8% |
| 1/2 | 23 | 67.7% | **0.0%** | **100.0%** |

Rank-sum p = 0.429. The 1/2 group contains both the best run in the archive and
the worst: `m9_cue-20260811-072207` scored 100.0% and
`m9_cue-20260817-085504` scored 0.0%, and straddle calls them the same thing.

**And the sign is not stable.** Split by query order — the same two phrases, the
same objects, only which one was typed first:

| ordering | 2/2 | 1/2 |
|---|---|---|
| *an opened book* first | 80.7% (n=3) | 64.5% (n=19) |
| *a closed book* first | 76.6% (n=5) | **78.0%** (n=5) |

Straddle itself does not depend on query order — it asks whether each
reference's largest component is its own phrase. The accuracy it is supposed to
predict flips which way it points anyway.

### The continuous version is worse than it looks

A sign test throws away how far from zero the thing was, so the obvious repair
is the graded form, scale-free and with no constant in it: the weaker
reference's own component in units of `sep`, the gap between the two references
that the board already prints as `nearest pair N apart`.

    min(own)/sep vs accuracy    rho = +0.539   n = 31

On 31 runs inside one pair that is p ≈ 0.002, and for about ten minutes it was
the first enrolment-time predictor here to clear a bar. It is an artifact:

| | rho | n |
|---|---|---|
| `min(own)/sep` | +0.539 | 31 |
| control — `sep` alone | +0.345 | 31 |
| control — `min(own)` alone | +0.324 | 31 |
| control — dropping runs with `sep` ≤ 1 | +0.231 | 26 |
| control — `sep` alone, same subset | −0.109 | 26 |
| control — within 1/2 only | **+0.749** | 23 |
| control — within 2/2 only | **−0.381** | 8 |

The last two rows are the finding. **The correlation reverses sign across the
predictor's own boundary.** Accuracy rises as `min(own)/sep` climbs toward zero
from below and falls once it crosses — it peaks *at* the straddle boundary, not
beyond it, which is the opposite of what the quantity was proposed to mean.

Where the headline comes from is the fourth row. Five runs have `sep` ≤ 1: the
two references nearly coincide, `sep` in the denominator blows the ratio up to
−18.9, and they score badly because the references coincide. Take them out and
`sep` alone measures nothing (−0.109) and the ratio drops to +0.231, p ≈ 0.26.

## What is left standing

- **The reconstruction.** Two independent checks, 0.0007 against the board's own
  dumps and 0.0050 across all 47 logs against the receipts. Any future question
  about what an enrolment produced can be asked of the archive instead of the
  bench.
- **The enroldrift observation does not generalise.** That probe saw the book
  pair straddle 0 times in 6. Across the archive it straddles 8 times in 31.
  Straddle is not a property of the pair either; it is noise that lands about a
  quarter of the time, and which way it lands does not matter.
- **The two-pair contrast in #35 stands as far as it went** — the cube pair does
  behave differently from the book pair — but the mechanism proposed for it does
  not. Whatever separates those two pairs, it is not that one straddles.
- **And the book pair is not undiscriminable, which the enroldrift README said
  it was.** That sentence has been corrected. `m9_cue-20260811-072207` scores
  **120/120 held out** with AUC 0.978 on the `an opened book` query, and 08-20
  averages 97.6% over two runs. By date the same pair reads 100.0% (08-11),
  56.7% (08-16), 61.8% (08-17), 97.6% (08-20), 76.0% (08-24), 82.2% (08-25),
  71.9% (09-08). The pair is unstable, not impossible, and 08-17 is where the
  instability lives — four of its thirteen runs enrolled with `sep` below 1.

**One thing to be careful with in the row above.** Splitting those 30 runs at
`sep = 1` separates them perfectly: 4 runs below at 25.5% mean and 50.0% max, 26
above at 78.6% mean and 54.8% min. That is a cut chosen by looking at the
answers, on the one pair, and this repo has burned a bar picked that way before.
It is also not new. `sep` is already one of the four enrolment-time quantities
[`docs/architecture.md`](../../../docs/architecture.md) records as having failed
— its largest value across the nine benches that mattered belongs to a 76.7% run
— and the board already declines to gate at `sep < 0.05`. What the split shows
is the shape the repo already named: a **floor**, not a predictor. Below it the
references have collapsed and the run is dead; above it `sep` says nothing
(rho = −0.109 over the 26).

Straddle joins the register in
[`docs/bring-up-log.md`](../../../docs/bring-up-log.md#2026-08-17--the-two-visit-guards-first-prospective-test-rejects-the-best-run-of-the-day):
nine enrolment-time predictors, nine failures, and still no number available at
enrolment time that says whether the run about to happen is worth keeping.

## Files

- [`join.txt`](join.txt) — the table and the summary, as generated.
- [`validate.txt`](validate.txt) — the reconstruction against the `'T'` dumps.

Reproduce:

    uv run --script tools/probe_straddle.py --validate \
        bench/probe/20260909-enroldrift/*.log
    uv run --script tools/probe_straddle.py --score \
        bench/cue/m9_cue-2026*.log bench/cue/m9_cue-smoke*.log

`m9_cue_fake_d.log` is excluded from the join on purpose: it is a synthetic copy
of `m9_cue-20260816-172256` and would count that run twice.
