# Detecting the states without registering them: 64.1% against 69.2%

*2026-09-09. No board time: the same 40 archived cue logs, scored twice. Issue
#35.*

The registration stage can be removed. Take the query with the highest `z` and
call the frame that — no operator, no reference, no threshold, no constant. It
is already computed: the `*` on every frame line marks it, and with `nq = 2` it
is the sign of the margin, because `c[] = z[] - mean(z)` subtracts the same
number from every query and cannot change which one is largest.

So the question has an arithmetic answer. Score both rules on the same frames of
the same logs and subtract.

| rule | mean | min | median | max |
|---|---|---|---|---|
| enrolled | 69.2% | **0.8%** | 75.8% | 99.2% |
| argmax, no registration | 64.1% | **26.0%** | 59.2% | 100.0% |
| oracle — best cut per run, chosen with the answers | 85.8% | 52.0% | 93.0% | 100.0% |

31 book-pair runs plus two smoke runs, 3925 frames. Across all four pairs
(40 runs, 4821 frames) it reads 68.9 / 62.7 / 83.3.

**Registration buys 5.1 points.** It wins on 22 of 33 runs and loses on 10, sign
test p = 0.050 — which is to say that on this sample the enrolled rule is
better, and only just. Over all 40 runs it is 28–11, p = 0.009.

## The 5.1 points are not the interesting number

The oracle row is. A cut chosen for each run individually scores 85.8%, and
argmax — which is that same rule with the cut nailed to zero — scores 64.1%.
**21.7 points sit in the archive waiting for somebody to name the threshold, and
the enrolment collects 5.1 of them.**

That is the registration stage's actual job, stated as a measurement rather than
as a rationale, and it is the same shape as
[`docs/architecture.md`](../../../docs/architecture.md)'s: the ordering was
already right and the boundary was not at zero. Reading the best cut off each of
the 31 book-pair runs says how far from zero:

| query | best cut, min | max | range | mean | AUC mean |
|---|---|---|---|---|---|
| *an opened book* | −17.59 | +4.65 | 22.24 | −2.04 | 0.825 |
| *a closed book* | −15.48 | +10.48 | 25.96 | +1.84 | 0.573 |

Drop the two worst outliers on the first row and it still spans −6.5 to +4.7.
Nothing constant fits in that, which is why no firmware release has ever shipped
one and why `tools/score_cue.py` refuses to report accuracy at a fixed cut.

## Four runs that carry the argument

| log | enrolled | argmax | what happened |
|---|---|---|---|
| `m9_cue-20260811-072207` | **99.2%** | 46.9% | The case for registration. `sep` 2.35, argmax at chance, the enrolment finds the boundary and gets it almost perfectly right. |
| `m9_cue-20260817-085504` | **0.8%** | 73.4% | `sep` 0.20 — the references collapsed onto each other, so the presence rule called 127 of 128 frames absent. The registration-free rule was unaffected. |
| `m9_cue-20260823-0710` | **14.1%** | 89.1% | `sep` 2.32, only 2 absent frames: a healthy-looking enrolment that assigned the axis **backwards**. 14.1% is 89.1% inverted. |
| `m9_cue-20260825-0602` | 89.8% | 90.6% | The ordinary case. The two rules agree to within a frame. |

Row 3 is [the enrolment-drift probe's](../20260909-enroldrift/) "round 3's
references classify round 4's frames backwards", caught in a scored bench with
ground truth attached. Row 2 is the collapsed-`sep` failure the
[straddle probe](../20260909-straddle/) named as a floor.

Both are failures the argmax rule cannot have. It has no references to collapse
and no axis to invert; its worst book-pair run is 26.0% and its worst run of the
forty is 18.0%, against the enrolled rule's 0.8%. **Registration raises the mean
and lowers the floor**, and the floor is where an appliance lives.

## What this does not say

- **It is not an argument for shipping argmax.** 64.1% is not a product either,
  and the two rules fail on different runs rather than one dominating.
- **The oracle row cannot be shipped and is not a target.** It is fitted to the
  answers of each run, one run at a time. It is here to size the prize.
- **Nothing here tests the presence rule.** Frames where the object was absent
  are excluded from both columns, because argmax has no way to say "nothing".
  The 9.5% of present frames the enrolled rule declined are counted as misses,
  which is the comparison the two rules can both stand in; scoring absence
  properly is issue #18's question and not this one.
- **`nq = 2` is the whole sample.** With two queries the class space is a line
  and argmax is one fixed cut. Issue #31 asks what happens at `nq >= 4`, where
  argmax stops being a threshold at all and this comparison has to be redone.

## Files

- [`book.txt`](book.txt) — the 33 runs on *an opened book* / *a closed book*.
- [`all.txt`](all.txt) — all 40 scored runs, four pairs.

Reproduce:

    uv run --script tools/probe_noenrol.py --verbose \
        --queries "an opened book,a closed book" \
        bench/cue/m9_cue-2026*.log bench/cue/m9_cue-smoke*.log

Both rules read the same rows. `enrolled` is the frame line's own `MATCH` field
— the board's verdict as it happened, not a re-derivation — so the only thing
this file computes is the comparison.
