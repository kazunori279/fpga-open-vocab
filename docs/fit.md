<!-- Written 2026-08-22, from the benches in bench/cue/ and the generated sets in
     bench/stills/. Every number here is cited to the file it came out of. -->

# Will it work for what you want?

**Probably not the way you are about to phrase it, and the only way to find out
is to run it.** This page is the shortest honest path from "I have an idea" to
"I know". It is three screens, cheapest first, and the first two are free.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [camera](camera.md) · [bring-up log](bring-up-log.md)

---

## What the board actually is

**A one-scene, enrolled discriminator with a reject option.** You show it the
scenes you care about, in the room and the lighting you care about, and it
answers two questions per frame: *which of these is it*, by nearest reference,
and *is it any of them at all*, by how far the nearest one is.

What it is **not**:

- **Not a detector that generalises to a room it was never shown.** The
  distilled student is barely scene-invariant. Asked to rank the same object
  state across thirty different rooms, the shipped checkpoint reads **0.645**
  pooled cross-scene AUC where its own teacher reads **0.811**
  ([`bench/stills/README.md`](../bench/stills/README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise)).
  That gap is not for want of trying: six distillation losses moved it by
  nothing at all, training twice as long moved it by nothing at all, quadrupling
  the training data bought 0.05 of the 0.17, and the largest knob found so far —
  the InfoNCE weight, worth 0.08 — was already set to its best value before
  anybody measured it. The enrolment step is not a convenience. It is the thing
  carrying the accuracy.
- **Not a fixed-accuracy component.** Four pairs benched back to back on one
  afternoon scored **95.8 / 90.8 / 50.0 / 34.2%**. What the appliance does
  depends on what you ask it to tell apart, and the spread is the headline
  rather than the mean.
- **Not zero-shot in the useful sense**, even though the text side is. You can
  type any phrase — the text tower runs on the host, in full, unquantised — but
  the *board* still has to have been shown your scenes before it can rank them.

It is, in exchange, a $50 board doing a 1.40 M-parameter int4 CNN in 265 ms with
no network, and keeping **91%** of the queries its teacher gets right.

---

## Screen 0 — the wording. Free, and it eliminates most ideas.

**Both sides of your contrast must be nameable as a thing that is present.**
Not "X" versus "not X". Not "X" versus "X missing".

This is the single most reliable failure in the project, and it has now happened
three times — on a book, on a glass, and on a hand. The absence side ranks
correctly *between* the two scenes and never wins its own. In the hand bench,
**`"a closed hand"` fired on 0 of 90 frames** and sat negative in both scenes.
SigLIP 2 fixed which way negation points without making the negated thing
detectable, and the room calibration gave the positive side an 8× tighter spread
while giving the complement nothing.

The fix is usually a dictionary lookup, not a model change. English has a
positive noun for the closed hand:

| do not ask | ask |
| --- | --- |
| an open hand / a closed hand | an open hand / **a fist** |
| a cup with tea / an empty cup | a cup of tea / **an empty glass mug** — or make the *contents* the thing |
| a person / no person | a person / **an empty chair** |
| a door open / a door not open | an open doorway / **a closed door** |

`tools/phrases_hand.txt` is the candidate list this was tested from, and
`tools/probe_prompts.py --a ... --b ... --phrases ...` is how you test your own
before spending a morning on a bench.

Two more wording rules from the same evidence:

- **Ask for a thing, not for a property of the scene.** `dining table set` vs
  `cleared` was deliberately never shot: it is a state of the room, not of an
  object, and the board's rule is built on objects.
- **Do not ask where something is.** `a book on the left` / `a book on the
  right` is not a weak contrast, it is not a contrast: the teacher reads
  **0.499** on it over eleven objects and 278 pairs, where each pair is one
  photograph and its own mirror image. Fitting a direction to the labels and
  holding out scenes finds nothing either, so this is the representation and not
  the phrasing. `tools/mirror_pairs.py` and
  [`bench/stills/README.md`](../bench/stills/README.md#left-and-right-and-the-axis-that-is-not-there)
  are the measurement. If your alert is really about position, put a second
  object in the frame and ask about *that* — `an empty shelf` / `a parcel on the
  shelf` is a thing being present, which Screen 0 opened with.
- **Check that your contrast is not answerable by brightness.** If it is, the
  board will look brilliant and will be reading the lamp.
  `tools/probe_bisect.py` prints the mean-luma cue for exactly this reason. The
  tea/empty-glass pair separates at **AUC 1.000 on luma alone** on real stills,
  which is why that result had to be re-shot on generated scenes before it meant
  anything.

---

## Screen 1 — does the teacher carry it at all? Minutes, no hardware.

```sh
uv run --script tools/fit_check.py --pos "an open hand" --neg "a fist"
```

This generates a set of paired scenes, runs the **teacher** over them, and
prints a GO or a NO-GO.

**Use it to deprioritise ideas, not to certify or rule out a real-world use
case.** It measures the full SigLIP 2 SO400M teacher on generated scenes. A low
score is evidence against spending the next bench on that wording and image
set; it is not proof that a real object cannot work. Generation artefacts can
help or obscure the distinction, and the student's response need not track the
teacher's. A high score also does not predict the appliance. **Neither outcome
replaces Screen 2.** The camera-referenced experiment below illustrates that
gap even on the hand control.

The ten contrasts already measured, as a calibration for reading your own
(pooled cross-scene AUC, teacher at 1152-d):

| contrast | teacher | read as |
| --- | --- | --- |
| a glass with tea / an empty glass | .933 | strong |
| an opened book / a closed book | .907 | strong |
| an open suitcase / a closed suitcase | .894 | strong |
| an open oven / a closed oven | .850 | strong |
| an unmade bed / a neatly made bed | .847 | workable |
| a bowl full of food / an empty bowl | .819 | workable |
| an open refrigerator / a closed refrigerator | .786 | workable |
| an opened laptop / a closed laptop | .781 | workable |
| an open umbrella / a folded umbrella | .715 | thin |
| a toilet with the lid up / lid down | **.579** | **stop** |
| *a X on the left / a X on the right* | **.499** | **the floor — this is what a coin looks like** |

Under about **0.75, deprioritise it in this screen.** This is a screening
heuristic from these sets, not a universal detection limit. At 0.579 the teacher is barely above a
coin, and every student ever distilled from it came back at or below chance on
that contrast — 0.448 for the shipped one, 0.382 to 0.435 across the others —
which is an axis pointing backwards rather than a weak one.

The last row is not a contrast anybody should try; it is there so the top of the
table has a bottom. It is the mean over eleven objects and it is what a
distinction the encoder does not carry reads like.

---

### Household candidates from the 2026-09-26 synthetic screen

To search for uses without another board session, this experiment generated
household objects against a real camera frame's background and visual style.
Panels were reduced to 128×128 RGB565 and passed through the **shipped int4
weights and firmware C encoder**. Its 2,048 golden output floats matched
bit for bit before evaluation. The final decision was a simplified nearest
reference on the two text-query cosine difference, not the full firmware rule.

| Possible use | States | Native correct /6 | Worst perturbation /6 | With empty reference /6 |
| --- | --- | ---: | ---: | ---: |
| Laundry tidiness | Folded / crumpled towel | 6 | 5 | 3 |
| Mug orientation | Upright / tipped mug | 6 | 5 | 6 |
| Food-preparation progress | Unpeeled / peeled banana | 5 | 3 | 4 |
| Glasses storage | Unfolded / folded eyeglasses | 4 | 4 | 3 |
| Entryway belongings | Keys / wallet on a tray | 3 | 3 | 3 |
| Visible leaf posture | Upright / drooping basil leaves | 2 | 2 | 2 |
| Known-use control | Open hand / fist | 5 | 3 | 4 |

Each case is **one generated scene**, with three lighting conditions and
repeated state images. Each lighting condition contributes one registration
image and one withheld image per state. The six withheld images are correlated
samples from the same generation, not six independent trials. Perturbations
change exposure, colour balance, blur or noise after registration; they do not
add independent samples. The empty-reference column counts correct **object**
images after adding the empty desk as a third reference, not empty-scene recall.

**Towels and mugs are candidates to try, not established applications.**
The towel's 6/6 becomes 3/6 with the empty reference. The mug reaches 6/6 with
that reference but drops to 4/6 under some perturbations. A brightness-only
baseline already gets 4/6 on both. Raw text ranking on the hand control is only
1/6 for the student, against 6/6 for the teacher, despite hands reportedly
working on the board. Registration recovers 5/6. That discrepancy prevents
treating the synthetic ranking as a calibrated prediction of board performance.

Camera matching was also incomplete: the generated empty frames were smoother
than the real references. RGB565 conversion reproduces the pixel format, not
the sensor's exposure history or drift. The decision surrogate omits room
standardisation, temporal smoothing, registration windows and presence
hysteresis. It does not establish a spill detector, a missing-keys alert or a
plant-watering decision.

Fourteen pairs were planned; eight sheets were generated, and scissors were
excluded before scoring because they stood unnaturally upright. Six further
sheets failed at the generation service: box open/shut, whole/halved apple,
intact/crushed bottle, fork/spoon, rice-filled/empty jar and the cube control.
Those remain **untested**.

The [experiment report](../bench/stills/20260926-home-screen/README.md),
[image gallery](../bench/stills/20260926-home-screen/index.html) and
[raw results](../bench/stills/20260926-home-screen/results.json) retain the
prompts, exclusions, camera diagnostics and all scores. To rerun without a board:

```sh
uv run --script tools/screen_home.py --set bench/stills/20260926-home-screen
```

## Screen 2 — a bench. A morning of daylight, and you need more than one.

There is no substitute and no shortcut, and the reason is not caution. It is
measured:

**No enrolment-time number has ever predicted a run.** Four have been tried —
`sep`, the spread ratio measured inside one enrolment window, the same ratio
measured across two visits, and that one read one-sided — and all four failed
the same way. The last of them compares the gap between the two references
against the spread of the frames each was averaged from, and prints its verdict
ninety seconds in. Prospectively, over eight benches:

| board ratio | held out | the bar's call |
| --- | --- | --- |
| 5.5× | 95.8% | certify — right |
| 3.7× | 57.5% | certify — **wrong** |
| 2.4× | 90.8% | reject — **wrong** |
| 2.3× | 74.2% | reject — right |
| 1.8× | 92.5% | reject — **wrong** |
| 1.2× | 68.3% | reject — right |
| 0.5× | 50.0% | reject — right |
| 0.4× | 34.2% | reject — right |

Four of those eight were calls that mattered — the two best runs and the two
worst — and it got **one of the four** right.

**And one bench is not a measurement.** Replaying every archived run of *the
same book pair on the same desk*, the ceiling — the best any decision rule could
have scored on that morning's frames — spans **1.000 to 0.579 across nineteen
runs**. Forty-two points are decided before the rule is reached, by staging you
cannot see and did not change on purpose. A single 90% is not a capability and a
single 55% is not a limit.

So: **bench your contrast at least three times, on different days, re-staging
from scratch each time.** Read the spread, not the best one. If the low end is
usable, you have a product; if only the high end is, you have a demo.

`tools/score_cue.py` scores a bench log and `tools/probe_ceiling.py` prints that
morning's ceiling, which tells you whether a bad number was the rule or the
staging. With two queries the centred space is one-dimensional, so the margin's
own separability is a hard bound on anything the board can do — a run that
collected 60% of a 67.5% ceiling has a scene problem, not a rule problem.

---

## What nobody can tell you in advance

- **Which of your two states will be the fragile one.** In the book pair the
  opened book's four enrolment visits walked +1.96, +3.25, +4.64, +4.39 toward
  the closed book sitting flat at +5.8 — without ever leaving the frame — and 9
  of 60 held-out opened frames were called right against 66 of 66 closed ones.
  The encoder was fine that morning; the margin still separated those scenes at
  93.8%.
- **Whether the empty scene will land where you expect.** Across twenty-three
  archived benches, **eight are inverted**: the empty desk reads *nearer* the
  references than the objects do. No threshold repairs that, which is why the
  presence stage rejects on distance in the centred space rather than on a level
  ([#18](https://github.com/kazunori279/fpga-open-vocab/issues/18)).
- **How much of your accuracy is the operator.** Re-staging is a real variable
  and it has not been separated from the object
  ([#22](https://github.com/kazunori279/fpga-open-vocab/issues/22)).

---

## The short version

1. Phrase both sides as things that are present. If one side is an absence,
   rewrite it or drop the idea.
2. Use `tools/fit_check.py` to screen ideas; under 0.75, deprioritise that
   wording and set. The household synthetic screen can suggest candidates,
   but neither screen establishes real-world success or impossibility.
3. Bench it three times on three days. Believe the low end.

Everything above rests on `bench/cue/`, which is the only place in this
repository that numbers about *recognition* come from, and on
[`bench/stills/`](../bench/stills/), which is not a bench and cannot rank the
appliance — only the encoder stages inside it.
