# A third query for "nothing there", issue #31

*2026-08-27, one morning, ninety stills. Three scenes — an opened book, a closed
book, and the same desk with the book taken away — three rounds each, alternated.
Read with [`tools/probe_offplane.py`](../../../tools/probe_offplane.py).*

**Not a bench.** No cue schedule, no enrolment guard, no hysteresis, no LED, no
int4. What it can do is say whether a direction exists, which no bench can see.

## The question

With two queries the board's centred space is exactly one-dimensional — 3.55e-15
over 8 514 pairs — so the empty reference is a point on the very line the class
decision is made on, and presence and class trade against each other by
arithmetic. At k = 3 there is a plane, and "nothing there" has a direction
available that is not the class decision. Whether the model *puts* it there is
empirical, and if the answer were no, #31 would close without a bench.

The answer is yes, and #31 stays open for a different reason than the one it was
filed with.

`off` below is the empty reference's perpendicular distance from the class line,
in units of the distance between the two classes. `along` is where its foot lands
on that line. Every `k=2` row must read `off = 0.00`: in one dimension there is
no perpendicular. That is the self-test, and it passes on all three stages.

## The direction is there, and the teacher hands it over intact

| stage | third query | along | off | gain | A | B | empty |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| teacher 1152 | *(k = 2 self-test)* | 0.26 | **0.00** | 91.1% | 80% | 100% | 93% |
| teacher 1152 | `an empty desk` | 0.40 | 1.14 | 100.0% | 100% | 100% | 100% |
| teacher 1152 | `a bare table` | 0.65 | 1.11 | 100.0% | 100% | 100% | 100% |
| teacher 1152 | `nothing` | 0.21 | 1.13 | 100.0% | 100% | 100% | 100% |
| teacher 1152 | `a blank wall` | 0.70 | 1.08 | 100.0% | 100% | 100% | 100% |
| pca 512 | *(k = 2 self-test)* | 0.39 | **0.00** | 100.0% | 100% | 100% | 100% |
| pca 512 | `an empty desk` | 0.74 | 1.50 | 100.0% | 100% | 100% | 100% |
| pca 512 | `a bare table` | 1.12 | 1.26 | 100.0% | 100% | 100% | 100% |
| pca 512 | `nothing` | 0.62 | 1.68 | 100.0% | 100% | 100% | 100% |
| pca 512 | `a blank wall` | 1.11 | 1.33 | 100.0% | 100% | 100% | 100% |

An `off` of 1.1 means the empty scene sits about as far off the class axis as the
two classes sit from each other. It is not a sliver. It does not depend on the
phrase: four unrelated wordings land within 0.06 of one another at the teacher,
which is what a real direction looks like and what a lucky phrase does not.

And it is worth something rather than merely present. At k = 2 the teacher
misfiles one opened book in five and one empty desk in fourteen, because the only
way to say "empty" is a position on the book axis. The third query takes every
scene to 100%. **The frozen PCA to 512 does not lose it**, which matters because
that projection is the one place a 1152-d direction could quietly disappear
before the student ever sees it.

## The student is where it goes, and only for the phrases you would write

| stage | third query | along | off | gain | A | B | empty |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `so400m-full-a05` | *(k = 2 self-test)* | −0.63 | **0.00** | 63.3% | 23% | 100% | 67% |
| `so400m-full-a05` | `an empty desk` | −0.58 | **0.07** | 55.6% | 0% | 100% | 67% |
| `so400m-full-a05` | `a bare table` | −0.71 | **0.12** | 55.6% | 0% | 100% | 67% |
| `so400m-full-a05` | `nothing` | −0.61 | 1.22 | 61.1% | 27% | 90% | 67% |
| `so400m-full-a05` | `a blank wall` | 0.41 | 1.30 | **77.8%** | 60% | 97% | 77% |

`an empty desk` and `a bare table` — the two phrasings anybody would reach for —
come out of the 1.4 M student at `off` 0.07 and 0.12. That is the k = 2 line with
noise on it. The teacher gives those same two phrases 1.14 and 1.11.

The ordering **replicates on an independently shot set**: the contaminated
morning set next door, [`../20260825-empty-book/`](../20260825-empty-book/),
reads 0.14, 0.21, 0.57, 1.41 for the same four phrases in the same order. Two
sets, different pixels, different exposures, same ranking — `an empty desk` <
`a bare table` < `nothing` < `a blank wall`.

**So the question #31 asked has a yes, and a new question underneath it.** The
plane exists in the teacher and survives the projection; what does not survive is
the distillation, and it fails worst on exactly the phrases the feature is for.
`a blank wall` is the one that comes through, and `a blank wall` is a description
of *this bench's backdrop*, not of nothing being there. It is not a phrase that
generalises off this desk and it should not be shipped as one.

## What this set cannot say, and why one number in it is unreadable

`gain` here leaves one round out at a time and averages the three folds. It is
printed beside `g1`, the appliance's own split — enrol on round 1, run
afterwards — and on the previous set those two came apart badly enough to be
worth the extra column.

**The student's `gain` column should not be read as an accuracy.**
[`probe_bisect.py`](../../../tools/probe_bisect.py) on the same pixels puts the
student's between-round swing at 9.1 sd against a class gap of 8.2 sd. The
open/closed axis is intact — `|sep|` 0.994, held-out oracle AUC 1.000 — but the
shift from one round to the next is *larger than the difference the axis is
supposed to carry*, so any nearest-reference rule enrolled in one round is
already wrong by the next. The student's 23% recall on the opened book at k = 2
is that, not a lost axis. It is also the reason the student's rows move around
while the teacher's do not.

That drift is [#30](../../soak/20260825-camlock/)'s subject, and the two issues
meet here: a k = 3 presence rule cannot be benched on this student until the
between-round shift is smaller than the thing being measured.

Also true, and none of it improved by more rounds of the same set:

- one book, one striped backdrop, one fifteen-minute session
- thirty stills a scene, ten a round
- fp32 student, not the int4 the board runs
- the AEC still settled at two operating points across the nine rounds — mean RGB
  102 to 135 — which is [`../../soak/20260823-exposure/`](../../soak/20260823-exposure/)'s
  open question and not something staging fixes
- `probe_bisect`'s `drift` null reads 1.000 at every stage on this set. On a
  hand-staged pair that is not the sensor: the operator physically re-places the
  book each round, so two rounds of the same class genuinely differ. The null is
  built for a scene that does not move and does not apply to `--a`/`--b` here.
  The empty scene, which really is unmoved, is the only one it would apply to.

## Files

| file | what it is |
| --- | --- |
| `queries.txt` | the pair, beside the pixels |
| `open/`, `closed/`, `empty/` | 30 stills each, `rN-` prefixed by round |
| `logs/rN-CLASS.log` | the board's own reading while it captured them, kept as provenance |
| `offplane.json` | the table above, per stage and per phrase |

```sh
uv run --script tools/probe_offplane.py \
    --a bench/stills/20260827-empty-book/open \
    --b bench/stills/20260827-empty-book/closed \
    --empty bench/stills/20260827-empty-book/empty \
    --pos "an opened book" --neg "a closed book" \
    --third "an empty desk" --third "a bare table" \
    --third "nothing" --third "a blank wall"
```
