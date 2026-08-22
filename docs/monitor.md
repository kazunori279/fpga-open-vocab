<!-- Written 2026-08-22. Every number here comes from replaying the runs in
     bench/cue/ through host/watch.py; the replay is reproducible with the
     script quoted at the bottom. -->

# Using the board as a monitor

Point the camera at something, say what the two states are, and get a line when
it changes. That is one command:

```sh
uv run --script host/watch.py --enrol "a glass with tea" "an empty glass"
```

```
watching: a glass with tea, an empty glass
mode    : enrolled - the board can say nothing is there
confirm : 5 frames

enrolment, in order - each window is 6s of held scene:

  t+   20s   put the scene in 'a glass with tea' and hold it
  t+   30s   put the scene in 'an empty glass' and hold it

ready   : background frozen, 2 classes enrolled
12:04:31  a glass with tea
12:08:43  an empty glass            (held 4m12s)
12:08:54  a glass with tea          (held 11s)
```

One line per confirmed change and nothing in between, so a quiet day is a short
file.

[← back to the README](../README.md) · [will it work for you](fit.md) ·
[architecture](architecture.md) · [building](building.md)

---

## Before the first run

**Read [`fit.md`](fit.md) first.** It is three screens and the first two are
free, and they will tell you whether your contrast is one this board can do at
all. Nothing on this page rescues a contrast that fails them — the monitor
reports what the board decides, and if the board decides wrong it will report
that steadily and with a timestamp.

The short form of the rule that matters most: **both states must be nameable as
a thing that is present.** `a glass with tea` / `an empty glass` is the wrong
shape and the numbers below show what it costs. `a red cube` / `a blue cube` is
the right shape.

Then aim the camera, which has its own command and takes a few seconds:

```sh
uv run --script host/cue.py --frame-check
```

It writes a PNG of what the sensor actually sees and keeps it current. Lighting
and framing are decided here, before ten minutes are spent on a run.

## The three modes, cheapest first

```sh
# 1. Is my idea possible at all?  Minutes, no hardware.
uv run --script tools/fit_check.py --pos "a red cube" --neg "a blue cube"

# 2. Watch, using words only.  No setup, and no reject option.
uv run --script host/watch.py "a red cube / a blue cube / a cube"

# 3. Watch, enrolled.  Six seconds per state, and the board can say "nothing".
uv run --script host/watch.py --enrol "a red cube" "a blue cube"
```

**Mode 2 has no reject.** The board ranks your phrases and the top one wins;
point the camera at a bare wall and it will still name a state, because you
never gave it the option of saying nothing is there. It is the mode for finding
out in thirty seconds whether the camera is pointed at the right thing.

**Mode 3 is the appliance.** `--enrol` walks you through holding each state in
front of the camera while the board takes a reference off twenty frames. From
then on the board answers with its own verdict, which includes `unknown` when
nothing it was shown is in frame. That is the decision rule
[`architecture.md`](architecture.md) describes, and the accuracy numbers in this
repository are all measured on it.

## What it catches, measured

Replaying the three benched runs of 2026-08-20 — the same rotation of scenes,
recorded live with the operator's own cue timings as ground truth — through
`host/watch.py` at its default `--confirm 5`:

| run | contrast | object → object | object → gone | spurious |
| --- | --- | --- | --- | --- |
| `131217` | red cube / blue cube | **7 / 7** | 0 / 4 | 0 |
| `132448` | red cube / blue cube | **7 / 7** | 0 / 4 | 0 |
| `142249` | glass with tea / empty glass | 3 / 7 | 1 / 4 | 3 |
| | | **17 / 21** | **1 / 12** | 3 |

Three things to take from that table.

**On a contrast that passes `fit.md`, it does not miss and it does not invent.**
Fourteen state changes across two runs, fourteen events, nothing spurious.

**On a contrast that fails it, the monitor fails the same way the board does.**
The glass row is not a different bug: `an empty glass` is an absence, `fit.md`
Screen 0 says not to ask for one, and the board's own per-frame winner is right
on 6.7% of the frames where that state is the truth. Debouncing a backwards axis
produces steady, confident, wrong events.

**"The object went away" is not reliable — 1 of 12.** The reject option exists
and does fire, but on these runs emptying the scene usually left the board still
matching the last thing it saw. If your alert is *"tell me when the parcel is
taken"* rather than *"tell me which of these two states it is in"*, bench that
specific transition before trusting it. This is a known open edge of the
decision rule, not something the monitor can paper over.

## Reading the output

`--confirm N` is why the output is readable. The board's per-frame verdict
flickers — between 0 and 9 changes per hundred frames while a scene is being
held perfectly still — and a state has to survive N frames in a row before it is
announced. At 322 ms a frame the default of 5 is about 1.6 seconds of latency.
Raise it for something slow and noisy, lower it for something you need promptly.

**It is a latency knob, not an accuracy knob.** It removes flicker. It cannot
turn a wrong answer into a right one.

Nothing is reported at all until the board says it is ready: the background
freeze has to have completed, and in `--enrol` mode every class has to have a
reference. On the runs above, *every* spurious event before that gate was added
came from the frames before it.

## Wiring it into something

```sh
uv run --script host/watch.py --enrol "a red cube" "a blue cube" \
    --jsonl ~/fgx-events.jsonl \
    --on-change 'echo "$FGX_STATE at $(date)" >> ~/alerts.txt'
```

`--jsonl` appends one object per transition, line-buffered so it is readable
while the run is still going:

```json
{"time": "2026-08-22T12:08:43+09:00", "state": "an empty glass",
 "prev": "a glass with tea", "frame": 782, "mode": "enrolled",
 "margin": 5.72, "held": 252.0}
```

`mode` is in every record on purpose. A file whose `mode` is `text` contains no
`unknown` events — not because nothing was ever unrecognisable, but because
nothing could be.

`--on-change` gets `FGX_STATE`, `FGX_PREV`, `FGX_FRAME` and `FGX_MARGIN` in its
environment. It is spawned and not waited on: a hook that blocks would stall the
reader, and a stalled reader fills the pipe the board is writing into.

## Leaving it running

`host/watch.py` relaunches the board driver when it exits, which is the default
and what `--no-restart` turns off. A USB hiccup or a watchdog reboot costs the
15 seconds of `--restart-wait` and an announcement of the current state, since
after a restart nobody watching the output knows the board went away.

**An `--enrol` run does not restart.** The references do not survive a board
reboot and nobody is standing there to hold the scenes again, so the run ends
and says so rather than quietly continuing in a mode that means something
different.

The raw per-frame log keeps going to `--log` (default `/tmp/fgx-watch-demo.log`)
if you need to see what the board actually said. **Move it somewhere real for
anything you care about** — this repository has twice lost logs left in `/tmp`.

## When it is wrong

| what you see | what it usually is |
| --- | --- |
| no events at all, ever | the states are too close; check `ready :` printed, then `tools/fit_check.py` |
| events every few seconds | raise `--confirm`; if that does not fix it, the contrast is the problem |
| one state never wins | an absence phrase — `fit.md` Screen 0 |
| "nothing there" never fires | the known 1/12 above; bench that transition |
| `no /dev/cu.usbmodem*` | `uv run --script host/board.py` says which port and why not |

And the thing to do when none of that helps is to bench it properly rather than
tune flags: `fit.md` Screen 2, three runs on three different days, and believe
the low one. The same desk and the same pair have spanned a margin of 1.000 to
0.579 across fourteen runs.

---

The table above was produced by replaying `bench/cue/*.log` through
`parse_frame()` and `Debounce` directly, scoring each event against the
`.cues` sidecar the operator's own cue timings wrote. It is a test of this
file's event layer against recorded board output, not a fresh measurement of the
board.
