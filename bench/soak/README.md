# The soak archive

Runs that are long, unattended, or pre-registered — everything that is measured
by leaving the board alone rather than by holding a scene in front of it. Scene
benches live in [`../cue/`](../cue/) and encoder measurements in
[`../stills/`](../stills/); nothing here ranks the decision rule.

The page opens with the directory, because ten sets have accumulated under a
heading that used to name only the first one. The 2026-08-15 soak that this
directory started as is the last section.

## What is here

| set | issue | what it measures |
|---|---|---|
| the eight `m9_soak-20260815-*.log` files, below | [#1](https://github.com/kazunori279/fpga-open-vocab/issues/1), [#9](https://github.com/kazunori279/fpga-open-vocab/issues/9), [#12](https://github.com/kazunori279/fpga-open-vocab/issues/12) | 280/140 against 150/75, and the two outage shapes that look identical from the host |
| [`20260820-usb-p2/`](20260820-usb-p2/) | #9 | twenty runs on a new port and a new cable, with `host/usb_watch.py` recording the bus throughout — the instrument the 08-15 set did not have |
| [`20260821-lastwords/`](20260821-lastwords/) | #9 | #9's flash record driven end to end: written during an outage, read back after VBUS was cut, which is the case watchdog scratch cannot survive |
| [`20260821-q25/`](20260821-q25/) | #25 | the degenerate-enrolment guard — a zero level axis, a healthy set, and a mixed set a naive rule would have failed |
| [`20260821-q26/`](20260821-q26/) | #26 | the acquire's doubt reaching the `stopped :` summary, which runs 8–20 below needed and did not have |
| [`20260822-settle/`](20260822-settle/) | #27 | five `cam_probe` runs chasing a matrix that stopped matching its 08-03 record; a hand over the lens reproduced it first try |
| [`20260823-exposure/`](20260823-exposure/) | #28 | why every frame in this repository is under-exposed: the settle loop called the exposure converged while the AEC was still climbing, and whether it did was a coin flip per boot |
| [`20260825-fmtcache/`](20260825-fmtcache/) | #29 | the separate bug #27 turned up — with the fix, with it commented out, and with it again. The middle run is the interesting one |
| [`20260825-camlock/`](20260825-camlock/) | #30 | nineteen runs on an empty desk. Switching the white-balance loop off does not hold the white balance, and this is the first set here whose headline **fails** significance and says so |
| [`20260906-camlock-cold/`](20260906-camlock-cold/) | #30 | pre-registered cold run, **aborted after one pair** when the instrument turned out to be broken. The design was never spent and carries forward |
| [`20260907-camlock-cold/`](20260907-camlock-cold/) | #30 | the second attempt, incorporating the design above by hash rather than by copy |
| [`20260907-simsrc-vs-live/`](20260907-simsrc-vs-live/) | #30 | **two runs, and not a bench.** The sensor taken out of the loop scores 541 frames at a `common` walk of exactly 0.00. Everything downstream of the sensor is ruled out; the prediction was zero and was written down first |
| the four `usb_watch-*.log.gz` slices | #9 | one `host/usb_watch.py` process polling every port `uhubctl` can see, once a second, since 2026-08-16 |

Like [`../cue/`](../cue/), the 08-15 logs lived in `/tmp` until they were
archived — 2026-08-17, two days and one macOS reboot's worth of luck later.
`bench_loop.sh` and `usb_soak.sh` are the harnesses, checked in unmodified; the
[two traps](#two-traps-in-the-harness) at the bottom are what `usb_soak.sh`
exists to avoid.

## The continuous bus trace

One process, started 2026-08-16 at 05:55 and still running, snapshotted four
times. The slices do not overlap — each is cut on the line after the last one in
the slice before it — and each is gzipped because the great majority of every
file is the once-a-minute heartbeat that is the whole point of it.

| slice | to | lines | board-port transitions by day |
|---|---|---|---|
| [`…20260816-20260822`](usb_watch-20260816-20260822.log.gz) | 08-22 14:43 | 9 603 | 71 / 12 / 0 / 0 / 56 / 66 / 36 |
| [`…20260822-20260824`](usb_watch-20260822-20260824.log.gz) | 08-24 06:19 | — | 71 / 12 / 0 / 0 / 56 / 66 / **65** / 72 / 0 — 342 total |
| [`…20260824-20260825`](usb_watch-20260824-20260825.log.gz) | 08-25 13:19 | 1 920 | 71 / 12 / 0 / 0 / 56 / 66 / 65 / 72 / **9** / 52 — 403 total |
| [`…20260825-20260907`](usb_watch-20260825-20260907.log.gz) | 09-07, at the `uhubctl -p 1 -a off` that opens [`20260907-camlock-cold/`](20260907-camlock-cold/) | 18 652 | 56 / 9 / 72 / 0 × 9 / 243 / 4 |

**The negative result #9 wants is in the first slice.** Six days of continuous
watching and not one drop on a day the board was left alone: 08-18 and 08-19
have no transitions at all. That is not a fix — the 08-15 event was real and
this trace does not contain one — but it puts a floor under how often it
happens, and those two days are the only stretch in the file where a transition
would have had nobody to blame it on.

09-06's 243 are that morning's aborted session and the four `cam_probe` sweeps
that replaced it; 09-07's 4 are two power-downs and the boot between them.
08-25's 52 are the morning's five-run repeat session, the `cam_probe` work and
two `forgix_m9` flashes. Beyond that, and beyond the 26 transitions of the first
refresh — all 08-22 flashes plus one `--power-cycle` from #27, checked line by
line — **the file has not been audited event by event and should not be read as
if it had.**

### A quiet day in this file has been wrong three times

Each row above corrects the one before it, and the corrections are left standing
because the same mistake keeps arriving in a new costume.

- **08-22 read 36 and is 65.** The six-day snapshot was taken at 14:43 and the
  settle work ran on into the afternoon. Anyone quoting 36 is quoting a copy,
  not a day. The boundary day of every slice is counted twice for the same
  reason, so the rows are not addable across files.
- **08-24 read zero and is nine** — and the paragraph that got it wrong was the
  one warning about 08-22. The snapshot was at 06:19; the board was flashed at
  06:51 and 06:54, power-cycled at 08:39 and dropped to BOOTSEL at 08:42. **A
  count that ends where the copy ends is not a count of anything.**
- **The nine zeroes in the last row are not an untouched stretch. They are nine
  days of BOOTSEL.** Every heartbeat from 2026-08-27 19:57 to 2026-09-06 06:04
  reads `2-1:1 0103 power enable connect [2e8a:000f Raspberry Pi RP2350 Boot …]`
  — `0103` being `power enable connect`, so the board was powered, enumerated
  and drawing current the entire time, parked in the bootloader. It had been
  written up as idle since 08-25 and treated as a genuine cold start, on the
  evidence that there was no `/dev/cu.usbmodem` to open. There was no device
  node because the bootloader does not present one. `uhubctl` would have said so
  in one line on either morning and was consulted on neither. **A port with no
  transitions is not a port with nothing on it.** The correction is Amendment 4
  in [`20260906-camlock-cold/README.md`](20260906-camlock-cold/README.md).

Two further things the trace is not. It **sees the bus and nothing above it**,
so a core wedged behind a live CDC looks exactly like a quiet day — pair it with
something that asks the board a question. And **neither port number is a fact
about the rig**: the board sits on `2-1:1` now and sat on `2-1:2` for the earlier
files, because the C-Media sound device that held `2-1:1` through 08-24 is gone.

What the file turned out to be worth is something nobody set it up for. On
2026-08-22 at 12:04:02 the board dropped and came back one second later as
`2e8a:000f Raspberry Pi RP2350 Boot`: not an outage, but `host/demo.py` doing
precisely what it says it does on a clean exit. The board found in BOOTSEL
twenty minutes later had been written up as having "come up" that way. A bus
trace with a timestamp is what turned a mystery into a line of code — the
argument for this file being on all the time, and the reason the copy in `/tmp`
was a bad place for it.

## The 2026-08-15 clock-and-outage soak

Eight `m9` runs of 200 frames each, back to back over twenty-three minutes, four
at 280 MHz system / 140 MHz link and five at 150/75. One bitstream
(`crc32 0a2e9953`), one room, one afternoon. They are here for two reasons: they
are where the **454 ms / 802 ms** pair in #1 came from, and **three of the eight
logs are broken runs** — two events, in two ways that look identical from the
host and are not the same bug. Note the arithmetic: two of those three logs are
the *same* run, seen twice.

### Manifest

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
backwards.

### The two failures are not the same failure

Both look the same from the Mac — `/dev/cu.usbmodem21101` vanishes mid-run and
`demo.py` prints the same first line. What separates them is whether the board
comes back on its own.

**12:04, at 280 MHz — the watchdog caught it.** The board rebooted itself,
re-enumerated, and printed the reason:

```
hang      : the last run stopped for 8000 ms at frame 130, inside ft_capture - the camera
```

That is a byte lost on the camera bus and the deadline added in #8 doing its
job. It is the signature behind
[#12](https://github.com/kazunori279/fpga-open-vocab/issues/12).

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

### What the timing numbers here are, and are not

**454 ms at 280/140 against 802 ms at 150/75 — a ratio of 1.766 — is issue #1's
headline and it is stale by design.** This is `m9` *before* #10 overlapped
capture with compute and before #14 found `ft_set_rq()` uncalled outside `m7`.
The appliance is **282 ms per camera frame at 320/160** as of 2026-08-15
evening. Do not quote 454 as a current figure; quote it as the thing those two
issues moved.

The clean runs are also the only place the 1/f scaling was visible before #13
swept it properly: with the sensor grid still in the frame these two rates
happen to sit far enough apart that the grid does not hide the ratio.

### Two traps in the harness

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
