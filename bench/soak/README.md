# The 2026-08-15 clock-and-outage soak

Eight `m9` runs of 200 frames each, taken back to back over twenty-three
minutes on 2026-08-15, four at 280 MHz system / 140 MHz link and five at
150/75. One bitstream (`crc32 0a2e9953`), one room, one afternoon. They are
here for two reasons: they are where the **454 ms / 802 ms** pair in issue #1
came from, and **three of the eight died**, in two different ways that look
identical from the host and are not the same bug.

Like `../cue/`, they lived in `/tmp` until they were archived — 2026-08-17 in
this case, two days and one macOS reboot's worth of luck later. `bench_loop.sh`
is the harness that produced them, checked in unmodified.

## Manifest

`frames` counts `frame NNN :` lines actually printed, so it is where the run
stopped and not what it was asked for.

| file | clock | frames | ms/frame | outcome |
|---|---|---|---|---|
| `m9_soak-20260815-1204-280mhz.log` | 280/140 | 130 | — | **watchdog caught a hang** at frame 130, inside `ft_capture` |
| `m9_soak-20260815-1206-280mhz.log` | 280/140 | 202 | 454 | clean |
| `m9_soak-20260815-1209-280mhz.log` | 280/140 | 202 | 454 | clean |
| `m9_soak-20260815-1212-150mhz.log` | 150/75 | 201 | 802 | clean |
| `m9_soak-20260815-1216-150mhz.log` | 150/75 | 72 | — | **board gone from USB** at frame 71, watchdog did not get it |
| `m9_soak-20260815-1220-150mhz.log` | 150/75 | 12 | — | **board gone from USB** after frame 255; fragment, see below |
| `m9_soak-20260815-1224-150mhz.log` | 150/75 | 201 | 802 | clean |
| `m9_soak-20260815-1227-150mhz.log` | 150/75 | 201 | 802 | clean |

The 12:20 file is a **tail fragment**: it holds frames 244–255 and the host's
notes, with no header. The harness passes `--out` fresh per run and the host
reopened it after the recovery attempt, so everything before frame 244 was lost.
The run itself reached at least 255 frames. Kept because the tail is the part
that matters and because a truncated log is worth having on record as something
this harness can produce.

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

Only removing 5 V brought it back. That is the PSRAM chip-select outage —
GPIO0's reset pull-down holds U1 selected for the whole run until it drives
SD0..3 against the flash — found on 2026-08-16 and closed as
[#16](https://github.com/kazunori279/fpga-open-vocab/issues/16), with the
remaining targets tracked in
[#17](https://github.com/kazunori279/fpga-open-vocab/issues/17).
[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9) is the same
symptom. **These two runs are the earliest recorded instance**, and they are at
150 MHz — the slow control — which is a small part of why the clock and the
rail were excluded before the CS was found. The larger part is the 08-16
5 × 3000-frame comparison, whose logs did not survive `/tmp`.

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
