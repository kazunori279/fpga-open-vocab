# Seven pairs, no registration: colour and pose work, the box and the count do not

*2026-09-09, thirteen boots of `forgix_m9`, ~16000 frames. **Not a bench**: no
cue schedule, no sidecar, no held-out set, no accuracy. Every reading below is a
spot reading taken with a known object in shot, because the operator was cued
by voice a few seconds before. Issues #31 and #35.*

The question was whether the appliance can name a state with **nothing
enrolled** — no operator, no reference, no threshold — and show the answer on
the LED. It can, and the LED wiring for it already exists. Whether it is
*right* turns out to depend entirely on the pair.

## Getting the bipolar LED without enrolling anything

With nothing enrolled and bare phrases, `m9.c:2811` falls to `led_map()`: hue is
the winning query's `z` over its own threshold, brightness is pinned at 1.0. The
colour is a confidence meter on whoever is ahead, and it says nothing about
*which* state. That is the trap this probe started in — the first box run looked
like it was working and the LED was not encoding the answer at all.

`led_two()` (`m9.c:2808`) is the one that maps state to colour, and it needs the
query set to carry two roles. `host/demo.py:505` assigns them: a query becomes
`Q_CLASS` only if it is a **contrast query** *and* a gate exists. So the recipe
is three phrases and no enrolment:

    uv run --script host/demo.py \
        "an open hand / a closed hand" \
        "a closed hand / an open hand" \
        --gate "a hand" \
        --frames 0 --leave-running --out hand.log

Red is the first state phrase, green the second, brightness is the gate. Nothing
was enrolled in any run in this directory.

`--state` (added the same day, `host/demo.py`) is the other way in. The role is a
tag — one byte per query in `pack_queries()` — and `m9.c` reads the tag, never
how the vector was built, so a bare phrase can be a state query too. That took
no firmware change, and it is what let the plain pairs below be screened rather
than admired. The counting section is what happened when they were.

## What the pairs did

| pair | contrast axis, object A | object B | LED |
|---|---|---|---|
| *an open hand* / *a closed hand* | **+9.04** | **+8.26** the other way | red, then green |
| *a red cube* / *a green cube* | **+4.41** | **+11.20** the other way | red, then green |
| *a cup* / *two cups* | **+0.91** | **+8.29** the other way | red, then green |
| *a glass* / *stacked glasses* | ±1 to ±3, both ways | — | flickers through yellow |
| *an opened box* / *a closed box* | −2.11 (**wrong sign**) | +7.91 | green, then green |
| *an open cardboard box* / *a cardboard box* | −12.60 (**wrong sign**) | — | green |

The hand, at `hand.log` frames 300–302 and 432–434:

    an open hand~ +9.04   a closed hand~ -9.04   led 255/  0 h1.00 b1.00   (open palm)
    a closed hand~ +8.26  an open hand~  -8.26   led   0/255 h0.00 b1.00   (fist)

The box, at `box2.log` frames 348–350 and 523–525:

    a closed box~ +2.11   an opened box~ -2.11   led   0/255 h0.00 b1.00   (box OPEN)
    a closed box~ +7.91   an opened box~ -7.91   led   0/255 h0.00 b1.00   (box closed)

**The box axis has one answer.** It says *closed* whether the box is open or
shut, and it never turns red. It is not weakly right, it is pinned.

## The box's own words disagree with its contrast axis

Three plain phrases rode along in the spare query slots of `box2.log`, scored on
the same frames and not driving anything:

| in shot | *an open cardboard box* | *a closed cardboard box* | points to |
|---|---|---|---|
| open | **+1.21** | −0.22 | open ✓ |
| closed | +10.61 | **+14.09** | closed ✓ |

The plain pair gets both states right and flips sign correctly. The contrast
pair, on the same frames of the same run, does not. So the distinction is not
absent from the representation — it is worth about 1.4 to 3.5 `z`, against the
hand's 17 and the cube's 22, and `normalize(e_pos - mean(e_neg))` destroys what
little there is.

Contrasting against the generic noun instead of against each other (`box3.log`,
`an open cardboard box / a cardboard box`) is worse: −12.60 on an open box.

Four spot readings of a plain pair beating its own contrast pair is what
`--state` was written for. Section below: on the pair it was tried on, the plain
pair stopped winning as soon as it was screened over a whole run rather than
over the readings somebody picked.

## Counting: three ways at one pair, and the plain one is not the answer either

*a cup* / *two cups* works, asymmetrically — +0.91 on one cup against +8.29 on
two. The singular end is nine times the weaker, and the same asymmetry shows in
the plain phrases riding along: with one cup in shot *one cup* leads *two cups*
by 2.66, with two cups in shot the lead is 0.95 the other way. The board is much
surer that two cups are two than that one cup is one.

Two rewrites were tried against that.

**Asserting the singular helps, and moves the weakness rather than removing
it.** *a single cup* / *double cups* (`cup2.log`) reads +3.8 on one cup where
*a cup* read +0.91 — four times the singular end — and +2.4 on two cups where
*two cups* read +8.29. The gain on one side is the loss on the other, which is a
sign of tuning noise rather than of finding the axis.

**Anchoring both classes to the shared noun does not work at all.** The idea is
sound in outline: send `normalize(e_pos - e("cup"))` for each class, so the two
axes share a reference and the decision boundary lands at zero by construction
with nothing fitted. `cupdiff.log` is that run, and it is right on 1 of 4
readings:

| in shot | *one cup / cup* | *two cups / cup* | LED | |
|---|---|---|---|---|
| one | −0.82 | **+1.37** | green | ✗ |
| two | **+6.33** | +1.01 | red | ✗ |
| one | **+4.11** | +0.28 | red | ✓ |
| two | **+10.36** | +5.59 | red | ✗ |

Not inverted — unstable. The mechanism is the `normalize()`: *one cup* and *cup*
are near-synonyms, a single cup being a cup, so `e("one cup") - e("cup")` is a
small residual that is mostly noise, and normalising it to unit length promotes
that noise to full scale. The numbers say so directly. Over the run, the
near-synonym axis spans −0.82 to +10.36 while *two cups / cup*, which subtracts
along a real difference, stays inside +0.28 to +5.59. `box3.log`'s −12.60 is the
same failure, so **the generic term is the wrong anchor precisely because it is
true of both states**: subtracting it removes nothing that separates them.

**The plain pair on the LED is pinned.** `--state "one cup" --state "two cups"`
puts the pair that had won all four earlier readings onto `led_two()` with no
contrast anywhere. Over 622 gate-open frames of `cupstate.log`, *two cups* leads
on 576 of them, the axis spans −9.2 to +0.5, and the hue is red on 7%. That is
the box's signature, not a working axis, and the four readings that looked good
were four readings somebody picked out of two boots.

The flag is still worth having: it is what made the plain pair screenable
against the same `probe_axis.py` table as everything else, and the pair failed
that screen. Being able to measure it is what let it be dropped.

## Two failures that were not the pair's fault

**A poisoned background costs a factor of 16.** `gatehunt.log` read
`a red cube~ ±0.0616` where every other run reads `±0.0029` to `±0.0047`. Since
`z = (cos - qbg) / bg_spread`, that divides every score by 16, and a green cube
that had scored +12.45 scored +0.24. Something moved during the first 30 frames
and the Welford sd absorbed it as room spread. The desk has to be empty *and
still* until the background freezes; `gatehunt2.log` is the same run done again
and reads ±0.0039.

The box runs were **not** affected — `live` ±0.0034, `gated` ±0.0047,
`box2` ±0.0029 — so the box result stands on a clean background.

**A gate can sit on its own threshold.** In `cube.log` the presence query
`a cube` scored +1.57 on the red cube against a threshold of 1.23, and −2.39 on
the green one, so the LED went black on a frame the classifier had right at
`h0.00`. Four candidate gate phrases were scored side by side in `gatehunt2.log`
on a green cube:

| phrase | z | vs threshold 1.23 |
|---|---|---|
| *a cube* | +2.5 .. +3.2 | passes |
| *a block* | +0.8 .. +1.5 | marginal |
| *a toy block* | −0.5 .. −1.4 | fails |
| *an object* | −4.0 .. −4.5 | fails |

`a cube` was already the best of the four; no swap was needed and the earlier
failure was placement, not wording. The lesson is that the gate has about one
`z` of headroom and the brightness axis will flicker for reasons that have
nothing to do with the state.

## Where this leaves the pairs

| pair | verdict |
|---|---|
| *a red cube* / *a green cube* | works, both directions |
| *an open hand* / *a closed hand* | works, both directions |
| *a cup* / *two cups* | works, asymmetrically — +0.91 one way against +8.29 the other |
| *a glass* / *stacked glasses* | swings both ways at ±1 to ±3 and flickers through yellow |
| *an opened book* / *a closed book* | unstable — the archive has runs at 100.0% and at 0.0% |
| *an opened box* / *a closed box* | does not work |

Colour and pose the teacher carries; the box's open/closed it does not, or not
through this chain. That is also a reading of why the book pair is the one that
swings: it sits between the two.

Count sits between them as well, and lower than the first reading suggested.
*a cup* / *two cups* is the only pair here whose two ends differ by nine times,
and three rewrites moved that asymmetry around without removing it — the
singular end is where every version was weakest. Counting is a known soft spot
for a CLIP-shaped teacher, so the split that matters is whether the chain lost
it or never had it, which is the same measurement the box needs.

## What this does not settle, and the next measurement

Nothing here is accuracy. These are spot readings under a spoken cue, four to
six frames each, with no sidecar and no held-out set — enough to separate a
pinned axis from a working one in an hour, not enough to put a percentage on
either. [`tools/probe_axis.py`](../../../tools/probe_axis.py) reports the
ground-truth-free part (does the axis ever swing, how much room the gate had)
and its red/green split does **not** cleanly separate the pairs, because a hand
reaching in to move the box swings the axis too.

Five rewrites of one pair also cost most of a morning and left the count no
better placed than the first phrasing did, which is the argument against another
one. The question phrase-hunting cannot answer is **where in the chain the axis
is lost**. [`tools/probe_bisect.py`](../../../tools/probe_bisect.py) answers it
from stills — teacher 1152, then the frozen PCA to 512, then the student. If it
survives the teacher and dies at the PCA, refitting the basis is the cheapest
fix in this project and needs no retraining. If the teacher is already weak,
nothing board-side helps and it belongs on #23. That needs
[`bench/stills/`](../../stills/) shot in alternating rounds, which is what the
rounds in `shoot.sh` are for and why they are not optional there.

The gate is the other thing to fix before measuring anything else. It went dark
three times on 2026-09-09 for reasons unrelated to any axis: `tableware` failed a
whole boot at −1.8 to +0.8, and `a cup` drifted from +7.7 to −7.2 over a
fourteen-minute run while cups stayed in shot. The background freezes at frame 30
of a boot and is never re-estimated, so a long run walks away from it, and every
LED reading taken late in one is worth less than it looks.

## Files

Thirteen logs, `.log` plus the `.console` that carries the query banner:

| log | queries | note |
|---|---|---|
| `live` | 2 plain box phrases | the `led_map()` trap — colour is confidence, not state |
| `gated` | box contrast + gate | first `led_two()` run |
| `cube` | cube contrast + gate | axis works; gate marginal |
| `gatehunt` | cube + 3 candidate gates | **invalid**, poisoned background, kept as the evidence |
| `gatehunt2` | same, re-shot | clean; the gate table above |
| `box2` | box contrast + 3 plain | the plain-vs-contrast disagreement |
| `box3` | box contrasted against the generic noun | worse |
| `hand` | hand contrast + gate + *a fist* | works |
| `glass` | glass contrast + gate *glassware* + 3 plain | swings, flickers |
| `cup` | cup contrast + gate *tableware* + 3 plain | works, asymmetric |
| `cup2` | *a single cup* / *double cups* | singular end 4x stronger, plural end weaker; gate failed all boot |
| `cupdiff` | both classes contrasted against *cup* | 1 of 4; the near-synonym residual |
| `cupstate` | the plain pair via `--state`, no contrast | pinned: *two cups* leads on 576 of 622 frames |

[`axis.txt`](axis.txt) is the ground-truth-free table, as generated:

    uv run --script tools/probe_axis.py bench/probe/20260909-pairs/*.log
