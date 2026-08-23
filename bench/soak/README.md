# The 2026-08-15 clock-and-outage soak

Eight `m9` runs of 200 frames each, taken back to back over twenty-three
minutes on 2026-08-15, four at 280 MHz system / 140 MHz link and five at
150/75. One bitstream (`crc32 0a2e9953`), one room, one afternoon. They are
here for two reasons: they are where the **454 ms / 802 ms** pair in issue #1
came from, and **three of the eight logs are broken runs** — two events, in two
different ways that look identical from the host and are not the same bug. Note
the arithmetic: two of those three logs are the *same* run, seen twice.

Like `../cue/`, they lived in `/tmp` until they were archived — 2026-08-17 in
this case, two days and one macOS reboot's worth of luck later. `bench_loop.sh`
is the harness that produced them, checked in unmodified.

**There is a second soak here now: [`20260820-usb-p2/`](20260820-usb-p2/).** It
is the follow-up to the #9 event below — twenty runs on a new port and a new
cable with `host/usb_watch.py` recording the bus throughout, which is the thing
this 08-15 set did not have and could not be re-read for. Its harness is
`usb_soak.sh`, and the two traps at the bottom of this page are what that
harness exists to avoid.

**And a third set: [`20260821-lastwords/`](20260821-lastwords/).** Four short
runs rather than a soak. They are where #9's flash record was finally driven end
to end — written during an outage, read back after VBUS had been cut, which is
the case the watchdog scratch cannot survive by construction — plus the
`cam_probe` that showed the camera's two faults are separate and alternate.

**And [`20260821-q25/`](20260821-q25/)**, three short runs verifying the
degenerate-enrolment guard: a query set whose level axis is identically zero,
one that is healthy, and one mixed set that a naive rule would have failed.
**[`20260821-q26/`](20260821-q26/)** is its twin for the scene: the acquire's
doubt now reaches the `stopped :` summary, which is what runs 8–20 below needed
and did not have.

**And a six-day bus trace:
[`usb_watch-20260816-20260822.log.gz`](usb_watch-20260816-20260822.log.gz).**
`host/usb_watch.py` polling every port `uhubctl` can see, once a second, from
05:55 on 2026-08-16 to 14:43 on 2026-08-22 — 9 603 lines, gzipped because 9 086
of them are the once-a-minute heartbeat that is the whole point of the file. It
is a snapshot; the run was still going when it was copied, and the copy has been
refreshed once already, which is the cheap half of not losing it.

The board's port took 241 transitions across those six days — 71 / 12 / 0 / 0 /
56 / 66 / 36 by day — and the two days nobody touched the board, 08-18 and
08-19, have **none at all**, which is the negative result #9 wants. **Six days
of continuous watching and not one drop on a day the board was left alone.**

That is not a fix for #9: the 08-15 event was real and this trace does not
contain one. What it does is put a floor under how often it happens, and the two
empty days are the part that matters, because they are the only stretch in the
file where a transition would have had nobody to blame it on.

**The trace continues in
[`usb_watch-20260822-20260824.log.gz`](usb_watch-20260822-20260824.log.gz)** —
the same process, still running, snapshotted again on 2026-08-24 at 06:19. The
two files do not overlap: the continuation starts on the line after the last one
in the six-day file. Board-port transitions by day across the whole eight days
are now **71 / 12 / 0 / 0 / 56 / 66 / 65 / 72 / 0**, 342 in total.

Two of those numbers are the interesting ones. 08-22's 36 became **65** because
the six-day snapshot was taken at 14:43 and the settle work ran on into the
afternoon; anyone quoting 36 is quoting a copy, not a day. And **08-24 is zero**:
the last transition on the board's port is `2026-08-23 08:27:52`, the tail of the
exposure work, and the board has been enumerated as `2e8a:0009` continuously for
the twenty-two hours since, with the once-a-second poll never missing a
heartbeat by more than the poll's own drift. That is a third untouched stretch
to set beside 08-18 and 08-19, and it is the first one that is not a whole
calendar day, so it is the first that can be read as *uptime* rather than as
*absence*.

What this does not say is that the board is healthy — `usb_watch.py` sees the
bus and nothing above it, so a core wedged behind a live CDC looks exactly like
a quiet day. Pair it with something that asks the board a question.

The 26 transitions added by the first refresh are all 2026-08-22 flashes and one
`--power-cycle` from the #27 work, checked line by line. The rest of the file
has not been audited event by event and should not be read as if it had. What it turned out to be *worth*, though, was
something nobody set it up for. On 2026-08-22 at 12:04:02 the board dropped and
came back one second later as `2e8a:000f Raspberry Pi RP2350 Boot`: not an
outage, but `host/demo.py` doing precisely what it says it does on a clean exit.
The board found in BOOTSEL twenty minutes later had been written up as having
"come up" that way. A bus trace with a timestamp is what turned a mystery into a
line of code — the argument for this file being on all the time and the reason
the copy in `/tmp` was a bad place for it.

## Manifest

`frames` counts `frame NNN :` lines actually printed, so it is where the run
stopped and not what it was asked for.

| file | clock | frames | ms/frame | outcome |
|---|---|---|---|---|
| `m9_soak-20260815-1204-280mhz.log` | 280/140 | 130 | — | **watchdog caught a hang** at frame 130, inside `ft_capture` |
| `m9_soak-20260815-1206-280mhz.log` | 280/140 | 202 | 454 | clean |
| `m9_soak-20260815-1209-280mhz.log` | 280/140 | 202 | 454 | clean |
| `m9_soak-20260815-1212-150mhz.log` | 150/75 | 201 | 802 | clean |
| `m9_soak-20260815-1216-150mhz.log` | 150/75 | 72 | — | **off USB at frame 71, still computing** — #9, first half |
| `m9_soak-20260815-1220-150mhz.log` | 150/75 | 12 | — | **the same run, seen again at frame 244** — #9, second half |
| `m9_soak-20260815-1224-150mhz.log` | 150/75 | 201 | 802 | clean |
| `m9_soak-20260815-1227-150mhz.log` | 150/75 | 201 | 802 | clean |

The 12:20 file is **not a fragment of a truncated file — it is a whole run's
`--out`, and the run it caught was already at frame 244 when the host opened
the port.** `bench_loop.sh` passes a fresh `--out` per iteration, so nothing was
lost; there is no header because the board was mid-stream and had printed its
banner four minutes earlier, into the 12:16 file. That is the single most
important fact in this directory and an earlier version of this page had it
backwards. See below.

## The two failures are not the same failure

Both look the same from the Mac — `/dev/cu.usbmodem21101` vanishes mid-run and
`demo.py` prints the same first line. What separates them is whether the board
comes back on its own.

**12:04, at 280 MHz — the watchdog caught it.** The board rebooted itself,
re-enumerated, and printed the reason:

```
hang      : the last run stopped for 8000 ms at frame 130, inside ft_capture - the camera
```

That is a byte lost on the camera bus and the deadline added in #8 doing its
job. It is the signature behind [#12](https://github.com/kazunori279/fpga-open-vocab/issues/12).

**12:16 and 12:20, at 150 MHz — the watchdog did not get it.**

```
[host] nothing with VID 2E8A came back within 45s, so the board is not
       enumerating at all and the watchdog did not get it either.
[host] uhubctl -l 2-1 -p 1 -a cycle   # then wait ~9 s and retry
```

**This page used to say "only removing 5 V brought it back", and file the pair
under [#16](https://github.com/kazunori279/fpga-open-vocab/issues/16)'s PSRAM
chip select. That was wrong, and it was wrong in the way that costs the most:
it filed the one confirmed instance of an open issue under a closed one.**

The error was reading the third line above as a record of an action. It is not.
`recover()` in `host/demo.py:969` **returns a string**, `follow_reboot()` prints
it and raises `BoardGone`; `demo.py` has never run `uhubctl`. And nothing else
cycled the port either, which the next file proves:

- **12:20 opens at frame 244, with no banner, no `clock :` line and no `hang :`
  report.** A VBUS cycle is a power-on reset, so it would have produced all
  three and a frame 0. There are none.
- **The frame counter never reset.** 12:16 stopped printing at 71; 12:20 starts
  at 244, scoring the same two queries against the same enrolment. At 802
  ms/frame those 173 frames are 139 s, which fits the four minutes between the
  two files with the 12:16 host's 2 × 45 s of waiting inside it.
- `bench_loop.sh` only cycles when `uhubctl` cannot see `2e8a:0009`. It saw it,
  which is to say **the board put itself back on the bus.**

So the board never stopped computing and was never power-cycled. It dropped off
USB, kept running the whole loop with the camera in it, and re-enumerated on its
own. A wedged QSPI bus stops the core dead, so this cannot be #16 — the two
shapes are mutually exclusive. This is
[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9), whose scope note
says exactly this, and **12:16 + 12:20 is its defining event and its only
confirmed instance at 150/75.**

They are at 150 MHz, the slow control, which is part of why the clock and the
rail were excluded early. The other part is the 08-16 5 × 3000-frame
comparison, whose logs did not survive `/tmp`.

## What the timing numbers here are, and are not

**454 ms at 280/140 against 802 ms at 150/75 — a ratio of 1.766 — is issue #1's
headline and it is stale by design.** This is `m9` *before* #10 overlapped
capture with compute and before #14 found `ft_set_rq()` uncalled outside `m7`.
The appliance is **282 ms per camera frame at 320/160** as of 2026-08-15
evening. Do not quote 454 as a current figure; quote it as the thing those two
issues moved.

The clean runs are also the only place the 1/f scaling was visible before #13
swept it properly: with the sensor grid still in the frame these two rates
happen to sit far enough apart that the grid does not hide the ratio.

## Two traps in the harness

`bench_loop.sh` **hard-codes `uhubctl -l 2-1 -p 1`**. That port number was
correct on 2026-08-15 and became wrong on 2026-08-16 when a neighbouring device
was unplugged. A stale port cycles an empty socket and then reports that even
the hammer failed. Before re-running this, take the port that shows `power`
with no `connect` — never the one written down here.

It also greps for `2e8a:0009` (running) and `2e8a:000f` (BOOTSEL) to decide
whether to nudge or to cycle. That is the right order — `picotool reboot` is
cheap and a VBUS cycle costs ten seconds — but it means a board that is neither
gets cycled, which is what happened at 12:16 and 12:20 and is the correct
response there.
