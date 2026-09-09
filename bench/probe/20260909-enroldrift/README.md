# Re-enrolling the same two objects moves the reference by most of the gap

*2026-09-09, one boot of `forgix_m9`, six rounds. Not a bench: no cue schedule,
no held-out set, no ground truth, no accuracy. The output is a set of reference
vectors and the distances between them. Issue #34 step 2, and where issue #35
came from.*

The boot enrolled *an opened book*, *a closed book* and the empty scene, dumped
all three with `'T'`, forgot them with `'Y'`, and did it again — six times. `'Y'`
drops the references and keeps the background, which is what makes the six
rounds comparable: **the largest `qbg` difference across all six dumps is exactly
0**. One coordinate system, six independent enrolments in it.

Driven by [`tools/enrol_repeat.py`](../../../tools/enrol_repeat.py), read by
[`tools/probe_enroldrift.py`](../../../tools/probe_enroldrift.py).

## How far the references moved

Everything is in units of `sep`, the distance between the two class references
in that round — the quantity the board prints as `nearest pair N apart`. Nothing
is fitted; the question "is this large" is answered inside the run.

| class | pairs | median | max | worst pair |
|---|---|---|---|---|
| an opened book | 15 | 0.724 | 2.229 | 3–5 |
| a closed book | 15 | 0.336 | 0.793 | 0–4 |
| the empty scene | 15 | 0.441 | 1.752 | 0–2 |

`sep` itself ran from 0.229 in round 0 to 4.255 in round 5 — 18× for the same
two objects, the same room, the same frozen background.

A median of 0.724 `sep` means the reference for *an opened book* moves by most
of the gap it exists to defend, with no power cycle involved. That is the floor
#34's across-boot number would have to be judged against, and it is high enough
that the across-boot measurement cannot say anything useful.

**Round 0 is visibly worse than the rest** — scatter 1.015 against 0.234–0.868,
`sep` 0.229 against 1.6–4.3, and it is the only round where the empty reference
lands on the same side as the classes. A hand still in shot is the obvious
reading. It is kept in the table rather than dropped, and `--drop` exists for
anyone who wants the numbers without it; the conclusion does not depend on it.

## The finding that displaced the drift finding

Drift turned out not to be the interesting part. `c[0]`, the component along
*an opened book*, for each of the two class references:

| round | *an opened book* | *a closed book* |
|---|---|---|
| 0 | −3.40 | −3.56 |
| 1 | −4.93 | −2.72 |
| 2 | −4.17 | −3.01 |
| 3 | −1.25 | −3.16 |
| 4 | −4.74 | −2.10 |
| 5 | −5.37 | −2.36 |

With `nq = 2` the class space is one line and `c = (x, -x)`, so showing class k
should put `c[k] > 0` and the two references should **straddle zero**. They never
do — same side, 6 rounds of 6. What separates them is magnitude alone, mean
−3.98 against −2.82, which is nearest-centroid on a projection and is what
clustering is.

And the order along that line is not stable. Rounds 1, 2, 4 and 5 put *opened*
further from zero than *closed*; rounds 0 and 3 put it nearer. **Round 3's
references classify round 4's frames backwards.** The repo already knew an axis
could point the wrong way — `an empty glass` winning 6.7% of the frames it was
the truth for — but that was across runs. Re-enrolling was enough.

Whether this is the pair, the `nq = 2` degeneracy, or the rule is issue #35,
which registers the test and its prediction before running it.

## The second pair, and the answer

#35's test (a) is the same six rounds with `a red cube` / `a green cube` —
colour being where this teacher is strongest. The prediction was written into
the issue before the run: the two class references straddle zero in every round.

They do, 6 of 6.

| round | *a red cube* | *a green cube* |
|---|---|---|
| 0 | +0.11 | −13.57 |
| 1 | +10.04 | −7.79 |
| 2 | +8.58 | −10.19 |
| 3 | +12.67 | −6.41 |
| 4 | +11.64 | −9.20 |
| 5 | +11.19 | −7.99 |

Everything else follows the sign. Side by side, same procedure, same boot
length, same operator:

| | book pair | cube pair |
|---|---|---|
| rounds where the classes straddle zero | 0 / 6 | **6 / 6** |
| order along the axis flips | yes, rounds 0 and 3 | never |
| median displacement, worse class | 0.724 sep | **0.144 sep** |
| max displacement | 2.229 sep | 0.689 sep |
| `sep` range across rounds | 0.229–4.255 (18×) | 19.3–29.5 (1.5×) |
| median displacement, empty scene | 0.441 sep | **0.019 sep** |

**So the implementation is not discarding the text embedding.** The axis works,
and #35's first branch is the right one: the rule is fine and what differs here
is the pair.

**The sentence that used to stand here was wrong, and the archive says so.** It
read that SigLIP cannot tell this room's book open from the same book closed.
Two pairs is not a sample, this README said as much, and the sample that
existed all along disagrees: across 30 archived cue benches of the same book
pair, `m9_cue-20260811-072207` scored **120/120 held out** with an AUC of 0.978
on the `an opened book` query, and 2026-08-20 averaged 97.6% over two runs. A
representation that cannot carry the distinction does not return 126 of 126.

What the six rounds above actually sampled is the *unstable* regime of a pair
that does work. Sorted by date, the same pair reads 100.0% on 08-11, 61.8% on
08-17 with four of thirteen runs enrolling at `sep` below 1, 97.6% on 08-20 and
82.2% over six runs on 08-25. The cube pair is not better represented so much as
better behaved, and the difference this probe measured is enrolment variance
rather than a ceiling in the teacher. See
[`../20260909-straddle/`](../20260909-straddle/), which is where the archive got
read.

That also puts a floor back under issue #34. 0.144 sep, not 0.724, is what an
across-boot number would be judged against on a pair worth shipping.

**Two things this does not establish.** Two pairs is not a sample: the repo has
eight enrolment-time predictors that looked this clean and then reversed, and
"do the references straddle zero" is a ninth candidate until it is tested
against benches with known accuracy. And the mechanism suggested above — that
the axis direction is set by the ratio of background spreads, which differ 29%
on the book pair and 11% on the cube pair — is consistent with both runs and
demonstrated by neither.

## Files

- [`staged.log`](staged.log) — the book pair. Six `enroldump` blocks, six
  `enrolment : forgotten` lines, the receipts in between.
- [`cubes.log`](cubes.log) — the cube pair, #35 test (a), same procedure.
