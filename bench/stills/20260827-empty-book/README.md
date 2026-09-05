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

That last sentence is about *this* projection. `model/cache/` holds three SO400M
basis files under two distinct contents — `-a0.5` and `-a0.5_s30000` are byte for
byte the same file. The `pca 512` rows above are that shipped `-a0.5` content.
Run the same four phrases through `-pca512_s30000` instead and they read
0.92 / 0.59 / **0.47** / 1.05, with `sep` at the `k=2` self-test dropping from
1.75 to 1.17. `nothing` loses well over half its off-plane distance before any
student is involved. The frozen projection is a place the direction can go, and
on one of the two contents some of it does.

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

*This section is about `so400m-full-a05`, the shipped checkpoint. It is not about
the student in general — the section below finds another distillation of the same
teacher, through the same projection, that keeps `an empty desk` at 0.64.*

## Which distillations keep it — a screen over twelve checkpoints

*Added 2026-09-06. Same ninety stills, same query vectors, no new capture.*

If the plane is in the teacher and dies in the student, the next question is
cheap to ask: **is it the 1.4 M parameters, or is it this particular
distillation?** `--runs` puts several checkpoints' student blocks on the same
pixels in one table.

**`sep` is the column to read first.** `off` and `along` are both divided by the
class separation, so a checkpoint whose two class references have collapsed
prints a *large* `off` that means the opposite of what it looks like. The tell is
the `k=2` self-test: it must read `0.00`, and when it does not, the whole stage
is division noise.

### The shipped checkpoint against its own subsample

These two share a teacher *and* a projection — `emb_train2017_SO400M-pca512-a0.5`
and `..._s30000` are two names for one file, byte for byte identical. Same
architecture, same input size, same holdout indices. What differs is the number
of distillation pairs and the epochs it took.

| run | third query | sep | off | gain | g1 | A | B | empty |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `full-a05` *(shipped)* | *(k = 2)* | 1.09 | **0.00** | 63.3% | 65.0% | 23% | 100% | 67% |
| `full-a05` | `an empty desk` | 1.39 | **0.07** | 55.6% | 56.7% | 0% | 100% | 67% |
| `full-a05` | `a bare table` | 1.30 | **0.12** | 55.6% | 43.3% | 0% | 100% | 67% |
| `full-a05` | `nothing` | 1.09 | 1.22 | 61.1% | 51.7% | 27% | 90% | 67% |
| `full-a05` | `a blank wall` | 1.40 | 1.30 | 77.8% | 66.7% | 60% | 97% | 77% |
| `s30k-a05` | *(k = 2)* | 1.33 | **0.00** | 76.7% | 31.7% | 67% | 100% | 63% |
| `s30k-a05` | `an empty desk` | 1.56 | 0.64 | **94.4%** | 78.3% | 87% | 100% | 97% |
| `s30k-a05` | `a bare table` | 1.59 | 0.39 | 65.6% | 43.3% | 33% | 100% | 63% |
| `s30k-a05` | `nothing` | 1.62 | 0.21 | 65.6% | 46.7% | 33% | 100% | 63% |
| `s30k-a05` | `a blank wall` | 2.03 | 0.64 | **98.9%** | 91.7% | 100% | 100% | 97% |

`an empty desk` — the phrase this feature is for, and the one the shipped
checkpoint flattens to 0.07 — comes out of the 30 k-pair student at 0.64, with
every `sep` healthy and the self-test clean. That is not the whole 1.14 the
teacher has, but it is most of the way back from nothing.

**And the 30 k student is the worse checkpoint by every number that was
available when it was trained:**

| | `full-a05` | `s30k-a05` |
| --- | ---: | ---: |
| `holdout_top1` | 0.635 | **0.365** |
| `holdout_cosine` | 0.672 | 0.619 |
| `holdout_centered` | 0.535 | 0.421 |
| epochs | 37 | 20 |

Nearly half the top-1, and it is the one that keeps the direction. This
repository already holds the general form of that — no enrolment-time number has
ever predicted a run — but every earlier instance was a number failing to
predict. This is one pointing the wrong way, on the axis the selection was
actually made on.

Two things it does **not** say. `s30k-a05`'s `k=2` `g1` is 31.7%, which for three
scenes is chance: enrol on round 1 and this checkpoint is no better than the
shipped one, because the between-round drift below swallows it. And `a bare
table` and `nothing` sit at 0.39 and 0.21 — the direction is partly back, not
back.

### The wider net, on one phrase

Ten more checkpoints share the other SO400M projection
(`emb_train2017_SO400M-pca512_s30000`). They cannot be put in the table above:
that basis file is a different file with different bytes, so the query vectors
the students are scored against are not the same vectors. `off` on `an empty
desk`, against a teacher reading of 1.14:

| run | sep | off | gain, k = 2 → k = 3 |
| --- | ---: | ---: | --- |
| `s30k` | 1.10 | 0.10 | 42.2% → 50.0% |
| `s30k-e40` | 0.49 | 1.25 | 65.6% → 61.1% |
| `rkd10` | 1.44 | 0.08 | 44.4% → 74.4% |
| `rkd100` | 1.04 | 0.36 | 55.6% → 77.8% |
| `s30k-nce00` | 0.34 | 0.15 | 78.9% → 43.3% |
| `s30k-nce10` | **0.14** | *3.86* | **discard** |
| `text_text-0.1` | 1.43 | 0.50 | 63.3% → 93.3% |
| `text_text-0.3` | 1.71 | 0.25 | 74.4% → 72.2% |
| `text_text-1.0` | 1.08 | 0.01 | 64.4% → 65.6% |
| `text_text-0.3+rkd-10` | 0.58 | 0.36 | 41.1% → 54.4% |

`s30k-nce10` is the reason the `sep` column exists. Its `k=2` references are
0.001 apart, its self-test prints `0.01` instead of `0.00`, and its `along` reads
−354. The `off` of 3.86 is not a large distance, it is a small number over a
smaller one, and every row of that stage is unreadable.

`text_text-0.1` is the one candidate here: `off` 0.50 / 0.51 / 0.63 / 0.41 across
the four phrases with gain 93.3 / 100 / 100 / 85.6 — the only checkpoint in the
group whose four phrases look like a scaled-down teacher rather than four
unrelated numbers.

### What a screen is not

**One contrast. This ranks nothing.** Two students that both keep the direction
on an opened-versus-closed book have not been told apart; they have both passed.
The tie is broken by shooting a second pair — a different object, its own empty
scene, the same desk in the same session — not by reading further down these
tables. `probe_bisect.py` had a run ordering retracted for exactly this, and the
retraction is in that tool's docstring.

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
| `offplane.json` | the shipped student, per stage and per phrase |
| `offplane-a05.json` | `full-a05` against `s30k-a05`, one projection |
| `offplane-sweep.json` | the ten `_s30000`-basis checkpoints |

```sh
S=bench/stills/20260827-empty-book

uv run --script tools/probe_offplane.py \
    --a $S/open --b $S/closed --empty $S/empty \
    --pos "an opened book" --neg "a closed book" \
    --third "an empty desk" --third "a bare table" \
    --third "nothing" --third "a blank wall" \
    --json $S/offplane.json
```

Add `--runs a,b,c` for the comparison tables. The tool refuses runs that do not
share a teacher and a projection, and it compares the projection **by content**:
`-a0.5` and `-a0.5_s30000` name one file and are allowed together, while the two
non-`a0.5` bases really are different files and are not.

```sh
uv run --script tools/probe_offplane.py \
    --a $S/open --b $S/closed --empty $S/empty \
    --pos "an opened book" --neg "a closed book" \
    --third "an empty desk" --third "a bare table" \
    --third "nothing" --third "a blank wall" \
    --runs so400m-full-a05,so400m-s30k-a05 \
    --json $S/offplane-a05.json
```
