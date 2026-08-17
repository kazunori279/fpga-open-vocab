# The cue benches

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
| `m9_cue-20260817-152757.log` | 34.2% | 42.8% | 08-17 15:27 | **a glass with tea / an empty glass** — the worst held-out figure there is, and the first bench that fails for a reason that is not staging: margin AUC 0.301, the model does not carry the distinction at all. Best presence half of the four |
| `m9_cue-20260817-153747.log` | 50.0% | 43.3% | 08-17 15:37 | **a person standing / a person, hands up** — the model does separate them (margin AUC 0.771) but the subject will not hold still: spread 1.37 against sep 0.46, and the pooled reference lands where neither visit was. `enrolled from 0/6` |
| `m9_cue-20260817-154206.log` | 90.8% | 80.6% | 08-17 15:42 | **a small bag / a big bag** — third best in the project, and the bench that finished off the enrolment bar: it read 2.4x, below the 2.6 that was deleted three hours earlier, so the bar would have thrown it away |
| `m9_cue-smoke-2e48d86.log` | 37.5% | 37.5% | | `/tmp/m9_cue.log` as it stood after flashing the one-sided guard — a smoke test, kept because it is the only log of that firmware running |
| `m9_cue_fake_d.log` | 58.3% | 48.3% | | **synthetic.** A doctored copy of `m9_cue-20260816-172256.log`, made to exercise a probe against a class that was never in the room. Not a bench, and it will happily score like one if you forget that |

## Re-deriving anything here

    uv run --script tools/score_cue.py bench/cue/m9_cue-20260817-112606.log
    uv run --script tools/probe_sepscale.py bench/cue/*.log
    uv run --script tools/probe_reject.py bench/cue/m9_cue-20260817-112606.log

The tools all default to `/tmp/m9_cue.log`, which is where a fresh run still
lands. Pass a path from here to look at a past one.

## What is not in here

Only the cue benches. The soak, thermal and USB-drop logs that issues #9 and #12
rest on are still in `/tmp` and still at risk.
