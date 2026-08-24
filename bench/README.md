# The cue benches

Three directories. **`cue/`** is accuracy — this file is its manifest.
**`soak/`** is reliability: eight 200-frame runs from 2026-08-15 at two clocks,
three of which died, and the earliest recorded instance of the USB outage behind
#9. It has [its own README](soak/README.md).

**`stills/`** is neither, and is not a bench. It holds PNGs off the appliance's
camera, shot so that the stages of the encoder chain can be asked about the same
pixels off the board. [`stills/20260821-bisect/`](stills/20260821-bisect/) is
where the glass pair's axis was found to be lost at the student and nowhere
earlier. Nothing in there is comparable to a row in the manifest below: no cue
schedule, no enrolment, no held-out set, no sidecar.

Every number in this repository that says how well the appliance recognises
anything came out of one of the logs in `cue/`. The enrolment guard's constant
(`FGX_ENROL_SNR`, deleted from `firmware/m9.c` on 2026-08-17 after the run that
certified it scored 57.5% — and a 90.8% run read below it three hours later),
the seventeen-run table in
`tools/probe_sepscale.py`, the argument in `docs/bring-up-log.md` that four
enrolment-time quantities have each failed the same way — all of it rests on
these files and nothing else. A bench costs a morning of daylight and cannot be
re-run from anything on disk.

Until 2026-08-17 they lived in `/tmp`, which macOS empties. That is the only
reason this directory exists.

## Two different held-out numbers, and the tables use the first one

`tools/score_cue.py` prints both, and they are not the same measurement:

- **`HELD OUT n/120`** — the board's own rule, run live on the board, against
  the references the operator actually enrolled. This is what the appliance did.
- **`one visit per state, then held out n/180`** — an offline replay that
  re-enrols from one visit per state and re-scores the whole log. This is what
  the appliance *would* have done under a different enrolment.

They can disagree by a lot. `m9_cue-20260817-085504.log` scored 0.0% live and
83.3% on the replay. The denominators differ too, because the replay holds out
whatever the re-enrolment did not consume.

**The tables in `tools/probe_sepscale.py` and `firmware/m9.c` use the live
figure.** That is worth stating in a file that cannot drift, because it was
already got wrong once: the two rows added on 08-17 were filled in from the
replay column, and the correction that went in with `2e48d86` moved them the
wrong way. The manifest below carries both columns so the next person does not
have to guess which one a row came from.

### The one disagreement that is neither column being wrong

The two can also part company because **the wrong digit was pressed**, and that
case is worth naming because both numbers look reportable and neither is.

The board binds `'1'`..`'6'` to the **positional query list** and checks nothing
about what is in front of the lens. Every offline tool instead labels a
reference by the **cued segment** its enrolment window fell in. Those are two
different lists: `host/cue.py` takes the rotation from `--scene` and the classes
from its positional arguments, and until 2026-08-23 it pressed `k + 1` for the
k-th cued scene — correct only when the two happened to be typed in the same
order, which nothing checked. Type them the other way round and both references
go in swapped, silently, with each half internally consistent.

`m9_cue-20260823-0710` is the worked example, and it is why that sidecar is
VOID. Key 1 was pressed during the `an opened book` segment against a board that
had bound key 1 to `a closed book`, so both references went in swapped.
`score_cue.py` read the board and reported **15.0%** held out with
`an opened book 0/60`; `probe_ceiling.py` relabelled off the cues, quietly
undid the mistake, and reported **85.0%** and `the pair works`. The exact
complement, seventy points apart, for a run that never happened — and the
presence gate had nothing to do with it, the worst class sitting at 1.86 sep
against a 2.0 trip.

Both ends are closed now. `host/cue.py` looks the digit up in the query list
instead of assuming the cue order, so the two can be typed in any order; and
`tools/probe_reject.py`'s `load()` refuses a log where they still disagree, so
anything that gets past the first stops at the tool rather than in a table. The
guard fires only on a genuine permutation — a board whose query names are a
*different set* from the cued labels is a different fault with its own message,
and it is deliberate on 08-20 13:12 and 14:22.

## Manifest

`live` is `HELD OUT`, `replay` is `one visit per state, then held out`; `—`
means the run never reached a scoreable state. `docs` is the label the run goes
by in `docs/bring-up-log.md` and the tool tables, which is not always the
timestamp in the filename — see 11:44 below.

| file | live | replay | docs | what it is |
| --- | --- | --- | --- | --- |
| `m9_cue-20260811-072207.log` | 100.0% | 100.0% | 08-11 07:22 | the run that set the expectation everything since has been measured against |
| `m9_cue-20260815-111130.log` | — | — | | the port vanished mid-run: a watchdog reboot seen from the host |
| `m9_cue-20260815-111750.log` | — | — | | same, and the log ends in the `uhubctl` recovery advice |
| `m9_cue-20260816-172256.log` | 58.3% | 48.3% | 08-16 17:22 | first of the two runs that opened issue #19 |
| `m9_cue-20260816-173537.log` | 57.5% | 45.0% | 08-16 17:35 | second of them, same rule, same room |
| `m9_cue-20260817-073335.log` | 96.7% | 99.2% | 08-17 07:33 | |
| `m9_cue-20260817-085204.log` | — | — | | 179 bytes: the board went before the run did |
| `m9_cue-20260817-085504.log` | 0.0% | 83.3% | | the widest live-against-replay gap there is, and the reason this file explains the two columns |
| `m9_cue-20260817-085747.log` | 76.7% | 77.5% | 08-17 08:57 | the run whose `sep` of 5.83 is the largest ever measured and did not help |
| `m9_cue-20260817-091826.log` | 91.7% | 90.8% | 08-17 09:18 | |
| `m9_cue-20260817-093309.log` | 59.2% | 59.2% | 08-17 09:33 | |
| `m9_cue-20260817-095529.log` | 47.5% | 47.5% | 08-17 09:55 | the worst bench there is |
| `m9_cue-20260817-095808.log` | 74.2% | 74.2% | 08-17 09:57 | |
| `m9_cue-20260817-103054.log` | — | — | | aborted at the query handshake |
| `m9_cue-20260817-103550.log` | — | — | | a 103-frame `--frame-check`, not a bench |
| `m9_cue-20260817-103720.log` | — | — | | the same check again |
| `m9_cue-20260817-104419.log` | — | — | | smoke test of the VOID fix; ran to `stopped` without a held-out set |
| `m9_cue-20260817-104849.log` | 45.8% | 50.0% | | smoke test, 48 held-out frames — too short to mean anything |
| `m9_cue-20260817-105019.log` | — | — | | truncated mid-run |
| `m9_cue-20260817-105251.log` | 6.2% | 36.5% | | smoke test |
| `m9_cue-20260817-112155.log` | — | — | | what `pkill` on a running bench looks like: the board kept going to its frame budget, parked, and rebooted into BOOTSEL |
| `m9_cue-20260817-112606.log` | 92.5% | 91.7% | 08-17 11:26 | the first enrolment the board built from two visits, and the first prospective test of the two-visit guard. It failed the guard and scored the best of the day |
| `m9_cue-20260817-113304.log` | 68.3% | 76.1% | 08-17 11:44 | the control for 11:26. The docs call it 11:44 and the file is stamped 11:33; the scores are what tie the two together |
| `m9_cue-20260817-133552.log` | 57.5% | 65.6% | 08-17 13:35 | issue #19's control: the empty revisit removed, to reproduce 08-11's schedule. It scored what the 08-16 runs did, which is what killed the hypothesis. Also the only bench ever to clear the enrolment bar prospectively — at 3.7×, the highest ratio there is — which is what deleted the bar |
| `m9_cue-20260817-133952.log` | 74.2% | 70.6% | 08-17 13:39 | four minutes later with the empty rotation back on, below the bar at 2.3×, and 17 points better. #18's tenth bench: AUC 0.911, and the one that pushed the leave-one-out cost past the gain |
| `m9_cue-20260817-152044.log` | 95.8% | 98.3% | 08-17 15:20 | **an opened hand / a closed hand** — the first bench since 08-11 to reproduce 90%+, and the first on a pair that is not the book. Visit centres flat, so no pose drift. Its presence half is the opposite: `an opened hand` enrolled 0.08 sep from the origin and all 90 empty frames were called that class, exactly as the board warned at enrolment |
| `m9_cue-20260817-152757.log` | 34.2% | 42.8% | 08-17 15:27 | **a glass with tea / an empty glass** — the worst held-out figure there is, and the first bench that fails for a reason that is not staging. Margin AUC 0.301: inverted, not absent, and folding to a 0.699 ceiling. The state stage collected 60.0% against a 75.0% oracle *on the same held-out frames*, so the rule lost 15.0 points and the gate took the rest. That figure was 7.5 until 08-20, when `probe_ceiling.py`'s `lost` stopped subtracting a 120-frame rule from a 240-frame oracle; it now sits exactly on the #19 line and is the weakest member of that set |
| `m9_cue-20260817-153747.log` | 50.0% | 43.3% | 08-17 15:37 | **a person standing / a person, hands up** — the model does separate them (margin AUC 0.771, inverted, ceiling 80.4%) and the subject will not hold still: spread 1.37 against sep 0.46, and the pooled reference lands where neither visit was. But the scatter cost only 10.8 points — the state stage held 78.3% against an 89.2% oracle on those frames — and the drop to 50.0% is #18's gate calling 34 held-out class frames absent. `enrolled from 0/6` is that same gated figure, not a state-stage one |
| `m9_cue-20260817-154206.log` | 90.8% | 80.6% | 08-17 15:42 | **a small bag / a big bag** — third best in the project, and the bench that finished off the enrolment bar: it read 2.4x, below the 2.6 that was deleted three hours earlier, so the bar would have thrown it away |
| `m9_cue-20260820-062406.log` | 96.7% | 94.4% | 08-20 06:24 | **book control A**, the first of four interleaved book / glass / glass / book runs at 280 MHz in thirteen minutes. Ceiling 1.000. Its job is to be the alibi for the two glass runs that follow, and it is |
| `m9_cue-20260820-062932.log` | 50.8% | 50.0% | 08-20 06:29 | **a glass with tea / an empty glass**, second run of the pair. Margin AUC 0.409 — inverted, ceiling 0.591, and the oracle on the held-out frames reaches 56.7% against a chance of 50.0%. Fenced by book controls either side, so "the scene did not show it" is not available here |
| `m9_cue-20260820-063324.log` | 48.3% | 60.6% | 08-20 06:33 | **glass, third run**, four minutes after the second — and the margin has changed sign: AUC 0.680, upright, where 06:29 read 0.409 and 15:27 read 0.301. Same phrases, same glass, same tea. A pair whose axis does not keep its direction cannot be repaired by renaming it. The archived frames were rendered and the staging did not move |
| `m9_cue-20260820-063704.log` | 98.3% | 98.3% | 08-20 06:37 | **book control B**, thirteen minutes after A and on the same desk. Ceiling 0.950, `lost` 1.7. Together with A this is the strongest control the project has run: the glass failure has nowhere left to hide but the pair |
| `m9_cue-20260820-131217.log` | 97.5% | 97.2% | 08-20 13:12 | **a red cube / a blue cube**, and **the only bench in here run with contrast queries** — the frame lines say `a red cube~`, and the `~` is the difference. `probe_ceiling.py` refuses it (the cued labels are not the query names) and **the live figure is not comparable to any row above**: each query was phrased as the negative of the other, so part of the separation was built in the text tower rather than measured in the encoder. Kept as one half of a pair — see the note below |
| `m9_cue-20260820-132448.log` | 96.7% | 96.1% | 08-20 13:24 | **a red cube / a blue cube, bare** — the comparable half, twelve minutes after the contrast one and with the cubes unmoved. Margin AUC 0.992, `within` 0.993, `lost` 0.8. **Colour is carried**, which puts it in the hands-and-bags tier and takes colour off the list of explanations for the glass pair. Also reproduces the presence-stage failure of 13:12 with a wider overlap (−0.16 sep against −0.08), so that one is not a query-form artefact — see [#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) |
| `m9_cue-20260820-142249.log` | 54.2% | 64.4% | 08-20 14:22 | **the glass pair, contrast** — `"a glass with tea / an empty glass"` and its mirror, run to test whether rephrasing repairs a pair that fails bare. **Margin AUC 0.674, against bare's 0.699 / 0.680 / 0.591 — it bought nothing.** The enrolment is also **degenerate**: two contrast queries built from the same two phrases are exact negatives, so `lvl` reads `+0.00` on all 546 frames and both of M21's axes collapse to the raw pair. The margin survives that (it is `2·z`, so the AUC is unchanged) and is the number this run was for; **the live figure and every presence number in it are not usable.** See the note below |
| `m9_cue-20260823-0710.log` | **VOID** | **VOID** | | **the enrolment keys were crossed** — the board had bound key 1 to `a closed book` and the key was pressed during the `an opened book` segment, so both references went in swapped. Its two columns are 15.0% and 85.0%, exact complements, and neither is about the book pair. See the subsection above; `tools/probe_reject.py` refuses this shape now |
| `m9_cue-20260824-0651.log` | 75.8% | 80.0% | 08-24 06:51 | **issue #22's treatment run**: the book taken out of shot and re-staged from scratch before every visit. Ceiling 86.7%, `lost` 4.2, presence 0/90. Paired with 08:39 below, which is the same run staged the old way — read the two together or not at all |
| `m9_cue-20260824-0839.log` | 73.6% | 78.4% | 08-24 08:39 | **issue #22's control**, 108 minutes after the treatment on one firmware and one schedule: staged the old way, the book never leaving the frame. Ceiling 86.3%, `lost` 15.7, presence 0/90. **2.2 points from the treatment**, so re-staging bought nothing, and it did not reproduce 13:35's walk either. This is the run that took the mechanism out of #19 |
| `m9_cue-20260825-0558.log` | 77.5% | 76.1% | 08-25 05:58 | **first of five identical runs in twenty-five minutes**, and the one that collapsed: ceiling 98.3%, `lost` **20.8**, over the #19 line on current firmware. Its four siblings below are the reason this is a number about the rule and not about the morning |
| `m9_cue-20260825-0602.log` | 89.2% | 89.4% | 08-25 06:02 | second of five, four minutes after the collapse and 11.7 points better. `lost` 2.5 |
| `m9_cue-20260825-0611.log` | 75.2% | 79.0% | 08-25 06:11 | third of five, and **the lowest of the session at 75.2%** — but its ceiling is 84.3%, the weakest of the five, so only 9.1 of it is the rule. A low score and a collapse are not the same event |
| `m9_cue-20260825-0618.log` | 93.4% | 92.8% | 08-25 06:18 | fourth of five, the best of the session, `lost` **0.0** — the rule collected every point its scene offered |
| `m9_cue-20260825-0622.log` | 91.1% | 92.0% | 08-25 06:22 | fifth of five, and **the one with an operator error in it**: the book was removed instead of opened on the last cue, so frames 461–500 are an empty desk. The span is relabelled `empty` in the sidecar; the error had inflated the run to 92.5% over 120 frames and correcting it gives 91.1% over 90 |
| `m9_cue-smoke-2e48d86.log` | 37.5% | 37.5% | | `/tmp/m9_cue.log` as it stood after flashing the one-sided guard — a smoke test, kept because it is the only log of that firmware running |
| `m9_cue_fake_d.log` | 58.3% | 48.3% | | **synthetic.** A doctored copy of `m9_cue-20260816-172256.log`, made to exercise a probe against a class that was never in the room. Not a bench, and it will happily score like one if you forget that |

## Bare queries, and the one run that is not

Every row in the manifest above except 08-20 13:12 was run with **bare**
queries — two phrases, no negatives:

```
uv run --script host/cue.py --enrol \
  --scene "an empty glass" --scene "a glass with tea" \
  "an empty glass" "a glass with tea"
```

`host/demo.py` also accepts a **contrast** query, `"a red cube / a blue cube /
a cube"`, where everything after the first `/` is subtracted from the positive.
The board marks those on every frame line with a trailing `~`, and two things
follow that are easy to miss:

- **`tools/probe_ceiling.py` refuses the log.** It requires the cued labels and
  the query names to be the same strings, because that is what makes the
  margin's orientation knowable, and `a red cube` ≠ `a red cube~`. It skips
  rather than guessing which way round the margin goes.
- **The number means something else.** Phrasing each query as the negative of
  the other makes the pair contrastive *in the text tower*. A high margin then
  does not establish that the image encoder carries the distinction, which is
  the whole question [#23](https://github.com/kazunori279/fpga-open-vocab/issues/23)
  exists to ask — and rephrasing is the first candidate on that issue's own list
  of things that might repair a pair.

So a contrast run is not a substitute for a bare one. It is worth having as the
**second half of a pair**: the same scene, unmoved, run both ways, with the
difference between them measuring what the rephrasing bought.

**That pair is now complete, and it measures nothing — for a reason worth
keeping.** 08-20 13:12 (contrast) and 13:24 (bare) are the same two cubes twelve
minutes apart:

| | margin | live |
| --- | --- | --- |
| bare, 13:24 | 0.992 | 96.7% |
| contrast, 13:12 | 0.999 | 97.5% |

The bare run is already at the ceiling, so there was nothing for the rephrasing
to buy and the 0.007 between them is not a result. **A contrast/bare comparison
can only be informative on a pair that fails bare** — which is the glass pair,
not this one.

**Run there, on 08-20 14:22, it bought nothing.** Four runs of
`a glass with tea` / `an empty glass` now, two phrasings:

| run | phrasing | margin |
| --- | --- | --- |
| 08-17 15:27 | bare | 0.699 |
| 08-20 06:29 | bare | 0.591 |
| 08-20 06:33 | bare | 0.680 |
| 08-20 14:22 | contrast | 0.674 |

The contrast run lands inside the spread of the bare ones. Rephrasing is the
cheapest candidate on [#23](https://github.com/kazunori279/fpga-open-vocab/issues/23)'s
list for repairing a pair, and on the only pair with room to move it did nothing.

### A contrast pair built from two phrases is a degenerate enrolment

Worth its own heading, because it has now cost two benches and the board does
not warn about it.

Two contrast queries built from **the same two phrases** — `"A / B"` and
`"B / A"` — are exact negatives of each other on every frame. So their mean is
identically zero, `level` cannot move, M21's centring subtracts nothing, and
both of its axes collapse to the raw pair. The signature is unmistakable once
you know it: every segment reads `−x` against `+x`, and `lvl+0.00` is the only
level in the log.

`firmware/m9.c` describes this exactly, above the enrolment guards, from the
2026-08-11 bench that spent six minutes measuring an identically-zero presence
axis. **There is still no check for it.** The three guards that do fire —
classes on top of each other (`sep > 0.05`), too few visits, and a reference on
the origin — all pass, and the board prints `level +0.00` without judging it.

What survives a degenerate run: the **margin**, because it is `2·z` and the AUC
of that is the AUC of `z`. What does not: the live held-out figure, the presence
stage, and anything else the centred space is used for. 08-20 14:22 is quoted
for its margin only.

The cube contrast run, 08-20 13:12, is **not** degenerate — it used three
phrases (`"a red cube / a blue cube / a cube"`), so the shared negative breaks
the anti-symmetry. If a contrast pair is wanted, give it a third phrase.

## What #19 actually is, once `lost` is taken apart

`tools/probe_ceiling.py` narrowed
[#19](https://github.com/kazunori279/fpga-open-vocab/issues/19) from "a
regression since 08-11" to a handful of benches that threw away a ceiling their
own scenes had shown. It could not say why, because `lost` is a difference of
two accuracies and an accuracy has no direction.

`tools/probe_midpoint.py` gives it one. With two queries the centred space is
one-dimensional, so **the shipped rule is a single threshold and its value is
fixed at enrolment** — the midpoint of the two references on the margin axis.
The oracle is the best threshold on that same axis. So `lost` is the distance
between two cuts, and it splits exactly:

    lost = SWAP + cut

| bench | lost | SWAP | cut | ENROL | ASYM | pair |
| --- | --- | --- | --- | --- | --- | --- |
| 08-17 08:55 | 71.7 | **47.5** | 24.2 | −1.20 | 3.50 | book, **backwards** |
| 08-17 10:52 | 47.9 | **31.2** | 16.7 | 1.22 | 1.86 | book, **backwards** |
| 08-17 13:35 | 36.7 | 0.0 | 36.7 | −2.01 | 0.46 | book |
| 08-16 17:22 | 29.2 | 0.0 | 29.2 | 0.94 | 0.38 | book |
| 08-17 10:48 | 25.0 | 0.0 | 25.0 | −4.76 | 0.22 | book |
| 08-17 15:27 | 15.0 | 0.0 | 15.0 | 0.24 | 0.58 | glass |

**`SWAP` is the enrolment choosing the direction the held-out frames
contradict**, and it is a different bug from a misplaced cut: no threshold
anywhere on the axis recovers a backwards enrolment. Four of the twenty-five
benches did it — 08-17 08:55, 08-17 10:52, 08-17 09:55 and 08-20 06:33 — and on
#19's two largest it is most of the loss.

**`SWAP` is not the same thing as an inverted margin**, and 08-16 17:22 is the
bench that makes the difference concrete. Its margin AUC is 0.176, as inverted as
anything in the archive, and its `SWAP` is 0.0: the enrolment learned the same
inverted direction the frames use, so the naming error cost the rule nothing and
all 29.2 points are a misplaced cut. `SWAP` is only charged when the references
and the held-out frames disagree — which is why the column exists separately
from `probe_ceiling.py`'s `!` marker and does not track it.

**`cut` is then one scalar**, how far the enrolment's midpoint sits from the
best cut, split into the references not being where the frames are (`ENROL`) and
the price of cutting at the midpoint of two unequal spreads (`ASYM`).

**And the displacement is a step between visits, not a slide through the run.**
That distinction decides whether re-enrolling periodically would fix anything,
so it was tested rather than assumed. The margin axis's slope correlates with
`ENROL` at −0.937, but both are computed from the same frames, so that number is
the decomposition agreeing with itself. Measured separately on the taught spans
and on the held-out ones — disjoint frames, nothing shared — the slope agrees in
sign on **12 of 25 against a chance of 12.5, r = +0.201**. There is no run-long
drift. Re-enrolling later in the same run, which is the obvious fix and would
cost firmware, would not have helped.

What is **not** settled is whether that step is the whole scene or one object.
Both references are displaced the same way on 7 of the 9 worst benches against a
60% base rate, which is a lean and not a finding. That is
[#22](https://github.com/kazunori279/fpga-open-vocab/issues/22), and it needs a
run designed for it rather than more replay of these.

**That run has now happened, twice, and the next section is what it found.**
Read the two together: this section says the loss is a step between visits, and
the next one says the size of that step does not predict the loss. Read the
third one too, before comparing any two rows of the table above: **the same
bench run five times spans 18 points**, so a gap under about twenty between two
single runs is not a finding. The six rows above are chosen for a large `lost`,
which is exactly the selection that noise satisfies most easily.

`cue/analysis/20260823-midpoint.txt` is the run. Every row carries an `=state`
column: scoring the held-out frames against that single threshold has to
reproduce the rule's own accuracy, and if it does not on any bench the whole
table is void. It caught a real error while this was being written — the first
version hardcoded the upright direction and six benches came back with a
negative `lost`, which is the rule beating its own ceiling.

## Re-stage the book every visit, and nothing happens

[#22](https://github.com/kazunori279/fpga-open-vocab/issues/22) is the run the
section above says it needs, and it was run twice on 2026-08-24 — once each way,
108 minutes apart, same firmware, same schedule, same desk, same morning.

- `m9_cue-20260824-0651.log`, **the treatment**: the book taken completely out
  of shot and re-staged from scratch before every visit, no nudging and no
  adjusting the previous placement.
- `m9_cue-20260824-0839.log`, **the control**: staged the old way. The book stays
  in shot, is opened and closed where it lies, and its position is corrected by
  eye against the last visit.

| | 08-17 13:35 | 08-24 06:51 **re-staged** | 08-24 08:39 **control** |
| --- | --- | --- | --- |
| `an opened book` centres | +1.96, +3.25, +4.64, +4.39 | +0.43, −0.04, +0.08, +1.35 | −0.67, −0.98, −0.44, +1.06 |
| `a closed book` centres | flat near +5.8 | +1.30, +1.19, +1.99, +1.53 | −0.08, +1.94, +1.66, +2.09 |
| margin AUC | 0.970 | 0.813 | 0.871 |
| ceiling `best` | 93.8% | 86.7% | 86.3% |
| oracle on the scored frames `held` | 94.2% | 80.0% | 89.3% |
| **live `HELD OUT n/120`** | 57.5% | **75.8%** | **73.6%** |
| `lost` = `SWAP` + `cut` | 36.7 = 0 + 36.7 | 4.2 = 0 + 4.2 | 15.7 = 0 + 15.7 |
| empty desk held | — | 0/90 | 0/90 |

**Re-staging bought 2.2 points, which is nothing.** That is the result. Read
against 13:35 alone the treatment looks like an 18-point win; the control says
the win is an artefact of comparing across a week. Both of 08-24's runs land
within 1.6 points of 08-17 13:39 (74.2%), which was nudged.

`lost` did move, 15.7 to 4.2, and it is worth being precise about what that
means, because it is easy to read as an improvement. The ceilings are the same
to within half a point (86.3% and 86.7%). What changed is where the loss sits:
re-staging lowered the oracle on the scored frames by 9.3 points and lowered the
rule's shortfall by 11.5. **The loss moved from the rule to the ceiling and the
board's own column did not move.**

### The walk did not reproduce, and it does not predict anything

The control was supposed to reproduce 13:35's monotone walk, since it staged the
way 13:35 did. It did not — neither of 08-24's runs walks.

That is worth more than one run's worth of doubt, so the archive was asked
directly. For every bench with two classes and three or more visits each,
`cue/analysis/20260824-walk.txt` measures the net movement of the worse-behaved
class's centres **toward the other class's mean**, and puts it against `lost`:

| scaling | r | benches |
| --- | --- | --- |
| raw units | **−0.150** | 27 |
| divided by the gap between the class means | **+0.163** | 27 |

Nothing, on either scaling, and the raw one points the wrong way. Two rows make
the point better than the correlation does:

- **08-17 13:35 and 13:39 are four minutes apart, same operator, same book, same
  desk.** They walk by +2.43 and +2.51 — indistinguishable — and lose 36.7 and
  12.5.
- **`m9_cue-20260820-063704.log` is book control B, 98.3% and `lost` 1.7**, and
  its `an opened book` centres are the most nearly monotone of any book bench:
  +2.20, +1.03, +0.49, −0.41.

So **the pose drift named as #19's visible mechanism is not one.** The centres
do move between visits, on almost every bench; what they do not do is tell you
which runs the rule is about to fail on. 13:35 remains a real 36.7-point
collapse and remains unexplained — what has been removed is the explanation, not
the observation.

**The 13:35-against-13:39 comparison itself was retired the next week**, by the
five-run session two sections below: 16.7 points is inside what the same bench
does against itself in half an hour. The walk correlation over 27 benches is not
affected — that is a correlation, not a pair of runs — but this bullet's second
sentence is now a weaker argument than the table above it.

An honest caveat about the control: the operator knew which condition was being
run. "Correct it by eye against the last visit" is exactly the behaviour a
person performs differently when they are watching themselves do it. That cannot
be ruled out from these two runs, and it is an argument for the next attempt at
#19 being something other than a staging protocol.

### The presence stage failed identically in both

All 90 held-out empty-desk frames were called present in each run — 0/90 held,
twice. `tools/probe_reject.py` finds no radius that helps on 06:51 (best
balanced 81.2% at r = 1.0; the worst empty frame and the worst class frame
overlap by 2.92 sep). Being reproducible across a staging change is the useful
part: it is not a staging artefact. That belongs to
[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) and
[#21](https://github.com/kazunori279/fpga-open-vocab/issues/21).

`within` against `|sep|` is 0.858/0.813 on the treatment and 0.886/0.871 on the
control — both **below `probe_ceiling.py`'s own `NOTABLE` of 0.10, so the tool
does not report either and neither should a table.** Noted only because the
direction is now the same on a pair staged both ways.

## The same bench, five times in half an hour, spans eighteen points

Every comparison above is between single runs, and the two sections above spend
their length asking why one bench beat another. On 2026-08-25 the obvious
control was finally run: **the same command five times in twenty-five minutes**,
same book, same desk, same staging, one firmware, nothing changed between them.

| run | start | live `HELD OUT` | AUC | `best` | `held` | `state` | `lost` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 05:58 | 93/120 **77.5%** | 0.962 | 93.3% | 98.3% | 77.5% | **20.8** |
| 2 | 06:02 | 107/120 **89.2%** | 0.960 | 89.6% | 91.7% | 89.2% | 2.5 |
| 3 | 06:11 | 91/121 **75.2%** | 0.924 | 86.3% | 84.3% | 75.2% | 9.1 |
| 4 | 06:18 | 113/121 **93.4%** | 0.961 | 93.8% | 93.4% | 93.4% | 0.0 |
| 5 | 06:22 | 82/90 **91.1%** | 0.970 | 93.3% | 93.3% | 91.1% | 2.2 |

**75.2% to 93.4%, an 18.2-point spread with nothing changed.** The standard
deviation is 8.3 points, so two independent runs differ with an SD of
8.3 × √2 = 11.8, and **about 23 points is what 95% costs**. Five runs is four
degrees of freedom and that SD is itself loose, so read 23 as *roughly twenty*
and not as a threshold.

Held against the comparisons this repository has actually made:

| comparison | gap | verdict |
| --- | --- | --- |
| 08-11 07:22 100.0% vs 08-16 17:22 58.3% | 41.7 | **real** |
| 08-17 13:35 57.5% vs 13:39 74.2% | 16.7 | not distinguishable |
| 08-24 06:51 75.8% vs 08:39 73.6% | 2.2 | not distinguishable |

The last row was already reported as nothing. The middle one was not: two
sections above lean on 13:35 and 13:39 being four minutes apart and 17 points
apart, and **that pair of runs cannot carry it.** What survives is the
observation that opened #19 in the first place, which is also the only gap in
the archive wider than this session's noise.

**Run 1 lost 20.8 points, over `probe_ceiling.py`'s `LOST_19` of 15.0, and the
tool names it: `lost 21 points the ceiling allowed, on a scene that showed it -
#19`.** So the collapse still happens on the current firmware. Neither the 08-23
settle fix nor anything else since 08-17 removed it, and it is now reproducible
on demand at roughly one run in five rather than only in the archive.

Two hypotheses die here. **Not the first run of a session** — over the whole
archive the first bench of a day loses a mean of 12.50 and every later run
12.37, on 5 benches and 26. And **not warm-up of any other shape**: the worst
run is the first, the second worst is the third, and the best is the fourth.

`cue/analysis/20260825-repeat.txt` has the arithmetic. The caveat that matters
is scope: one pair, one morning, five runs. It says what a book bench's spread
is, not what every pair's is, and the glass pair's failures are three times this
wide.

### The mis-staged visit in run 5, and why the score went down

On run 5's last cue the book was taken away instead of opened, so frames 461–500
are an empty desk cued as `an opened book`. The sidecar's span is relabelled
`empty`, which is what the camera saw — a normal 10-frame settle and 30-frame
hold, identical in form to a cued empty — and the comment on line 5 of the
`.cues` says so.

**The error inflated the run, so correcting it cost points.** The board called
all 30 of those frames `an opened book`; scored as cued they counted as correct,
and the run read 92.5% over 120 frames. It is 91.1% over 90 above. The
references are untouched either way: enrolment is visits 1 and 2 only, and this
was visit 12.

## A threshold that follows the scene, and why it is still not a fix

The five-run session left one bench, `m9_cue-20260825-0558.log`, that collapsed
on the current firmware with its ceiling intact — margin AUC 0.962 and `best`
93.3%, the highest of the five, and 20.8 points thrown away. `probe_midpoint.py`
puts all of it in `cut` and almost all of `cut` in ENROL, at −2.13 against
−0.62..+0.48 on its four siblings. Reading that run's spans on the margin axis:

| span | visit 1 | visit 2 | visit 3 | visit 4 |
| --- | --- | --- | --- | --- |
| `a closed book` | +5.99 | +8.13 | +11.57 | +9.48 |
| `an opened book` | −1.01 | −1.17 | +3.21 | +2.05 |
| `empty` | −3.02 | −0.59 | +1.02 | +1.01 |

**Both classes and the empty desk all translated about +4 up the axis after the
references were taken, and the distance between the classes barely moved** —
4.08 early, 3.94 late. The empty span contains no book, so whatever moved is not
the book, the pose or the staging. The separation was never lost; the cut was
left behind.

`tools/probe_adapt.py` asks the obvious next question in replay: give the
threshold a causal running estimate of where the scene sits, `t_n = t_rule +
(c_n − c_enrol)`, and see what comes back. `c_n` is an EMA over frames seen so
far, updated after each frame is classified so nothing informs the threshold
that judges it. Tau is 120 frames — one rotation of the standard schedule, taken
from `host/cue.py` and not from the data.

Over 32 two-query benches (the synthetic `fake_d` and the smoke tests are
refused in code, because this tool pools into a mean and a duplicate of the
biggest winner would corrupt it):

| arm | pooled |
| --- | --- |
| `rule`, the shipped fixed cut | 74.7% |
| `adapt`, the correction | **76.4%** |
| `flip`, the correction negated | 70.5% |
| `empty`, the correction from empty spans only | 73.8% |
| `oracle`, the best fixed cut, known only afterwards | 83.4% |

**+1.7 points with a per-bench standard deviation of 9.3 is t = 1.00 on 31
degrees of freedom. It does not clear, and it is not a fix.** Three things
around that null are worth the file existing.

**The sign matters.** `flip` loses 4.2 points and wins on 6 benches of 32, so
this is not a moving cut beating a still one — that control was built in
precisely because a wandering threshold flattering itself would look identical
in the pooled row.

**It is aimed correctly**, which a null usually is not:

| group | `adapt` | `empty` |
| --- | --- | --- |
| 11 benches that lost 10 points or more | **+6.5** | +3.7 |
| 21 benches that kept most of their ceiling | −0.9 | −3.2 |

It helps the benches #19 is about and is nearly free on the rest — except that
"nearly free" is an average hiding `m9_cue-20260817-085747.log`, which goes
76.7% to 53.3%. **A correction that can cost a healthy bench 23 points is not
shippable on +6.5 average against the sick ones.**

**And part of the shift is in the bare desk.** The `empty` arm never sees a
frame with an object in it and still recovers +3.7 on the collapsed group. That
is the run-1 span table above, measured across the archive instead of read off
one bench.

**Both benches #19 was opened on recover.** 08-16 17:22 goes 58.3% to 91.7%,
past its own 87.5% fixed-cut ceiling, and 08-17 13:35 goes 57.5% to 74.2%. So
those collapses are a threshold sitting still under a scene that moved, and they
are recoverable in replay. **That is a diagnosis, not a fix**, and the reason is
worth stating because it is what a future attempt has to get past: the estimator
that recovers them averages *all* frames, and an average over all frames is a
common-mode estimate only while the schedule keeps the two classes balanced. An
appliance pointed at one scene for an hour would drive `c_n` to that scene's own
level and turn the rule into "is this frame above the recent average", which is
not a classifier. The arm without that flaw is `empty`, and it needs a presence
stage that works — 0 of 90 on every recent bench,
[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) and
[#21](https://github.com/kazunori279/fpga-open-vocab/issues/21).

`cue/analysis/20260825-adapt.txt` is the full table.

## A reference on the origin, and the unit that does not exist

The third enrolment guard above — *a reference on the origin* — is the one
[#21](https://github.com/kazunori279/fpga-open-vocab/issues/21) wants to turn
into a refusal rather than a warning, and the issue blocks itself on a
condition it wrote for itself: **quote the threshold in something that is not
`sep`.** `2.0 sep` is 0.52 absolute on one bench and 5.80 on another, because a
collapsed pair shrinks the denominator.

`tools/probe_origin.py` is that condition tested, over every log in `cue/` that
scores — 29 of 37, the rest having no sidecar, a VOID one, or no frames after
the rule engages. Ten are inverted. Three units were scored against that:

| unit | rank-AUC | best threshold fitted to these 29 |
| --- | --- | --- |
| `d/sep`, what the board prints | 0.642 | below 0.33 — catches 6/10, refuses 3/19 healthy |
| `d` raw, no denominator | 0.668 | below 1.96 — catches 10/10, refuses 11/19 healthy |
| `d/scat`, over the class's own frame spread | 0.805 | below 1.38 — catches 10/10, refuses 7/19 healthy |

**All three overlap and none of them splits the archive.** Removing the
denominator does not help; `d/scat` ranks best and then catches the same disease
from the other side, since to reach all ten it refuses seven working benches,
and its second-largest value belongs to 08-11 07:22 only because that bench's
scatter of 0.13 is four times smaller than any other's.

The counterexample is the part that settles it. **The reference sitting nearest
the origin in the whole archive is, in every one of the three units, a bench
that worked**: `d/sep` 0.01 is 08-20 06:24 at presence AUC 0.536, and `d` 0.02
and `d/scat` 0.03 are both 08-17 15:27's `a glass with tea` at 0.754. A guard
quoted in any of these refuses a good bench before it refuses a bad one.

So the answer to #21's own precondition is no, and the reason is not the unit —
the correlation between the origin distance and inversion is weaker than the
three benches in the issue made it look. Every threshold in that table is fitted
in-sample to the benches in hand, which is the sequence that has already deleted
`sep`, two ratios and `FGX_ENROL_SNR`. `cue/analysis/20260822-origin-units.txt`
is the run, and the per-bench table is in it.

## The `.cues` sidecars, and the three logs that have none

Every log is `<name>.log` and its sidecar is `<name>.log.cues` — the suffix is
appended, not replaced, so a stray `<name>.cues` is not one of ours. The sidecar
holds the cue schedule: the flags `cue.py` ran under, the enrolment frames, the
snapshot frames, and one line per scene span. **The log alone is not scoreable.**
`tools/score_cue.py` exits rather than guessing, and everything else in `tools/`
goes through its `load()`, so a missing sidecar can never quietly become a
number. A sidecar can also be marked `VOID`, which is refused unless forced.

**A span may be corrected after the run, and exactly one has been.**
`m9_cue-20260825-0622.log.cues` has its last span relabelled from `an opened
book` to `empty`, because the book was removed instead of opened on that cue and
`empty` is what the camera saw. The rule for doing this: relabel only to what
the frames show, never to what improves a score — that correction cost 1.4
points — leave the enrolment spans alone, and say so in a comment in the sidecar
itself, where anyone rescoring the log will read it. A relabel that touches an
enrolment span is not a relabel; it is a different run.

`cue.py` writes it when the run ends, so a run that never ended has none. Three:

| file | why there is no sidecar |
| --- | --- |
| `m9_cue-20260815-111130.log` | 373 lines, then the port vanished — a watchdog reboot seen from the host. The frames are real and the schedule that produced them is gone |
| `m9_cue-20260817-085204.log` | two lines. The board went before the run did |
| `m9_cue-20260817-103054.log` | 25 lines, aborted at the query handshake |

They are kept because they are the record of three ways a bench dies, not
because anything can be recovered from them. Nothing is missing and nothing is
worth reconstructing: a schedule guessed after the fact would be a sidecar that
looks exactly like a real one.

## Re-deriving anything here

    uv run --script tools/score_cue.py bench/cue/m9_cue-20260817-112606.log
    uv run --script tools/probe_sepscale.py bench/cue/*.log
    uv run --script tools/probe_reject.py bench/cue/m9_cue-20260817-112606.log
    uv run --script tools/probe_origin.py bench/cue/m9_cue-*.log
    uv run --script tools/probe_ceiling.py bench/cue/m9_cue-2026*.log
    uv run --script tools/probe_midpoint.py bench/cue/m9_cue-2026*.log

The tools all default to `/tmp/m9_cue.log`, which is where a fresh run still
lands. Pass a path from here to look at a past one.

## What is not in here

Only the cue benches. The soak, thermal and USB-drop logs that issues #9 and #12
rest on live in [`../soak/`](../soak/) — `20260820-usb-p2/` for the drop,
`20260821-lastwords/` for what the board said last, and `20260822-settle/` for
the camera. They were archived on 2026-08-21 and 08-22, and this paragraph went
on saying they were in `/tmp` until 08-23.
