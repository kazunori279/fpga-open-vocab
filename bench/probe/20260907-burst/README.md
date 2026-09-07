# `0x01` does exactly what the note says, and it is not worth using for speed

*2026-09-07, five boots of `forgix_cam_burst`. Not a bench: no cue schedule, no
enrolment, no held-out set, no accuracy. One register, asked five ways.*

`docs/camera.md` has carried this line unprobed for weeks: "`0`–`254` means
frames = value + 1; `255` means the memory is full (8 MB)". That is the
application note, and the application note is the source that has already been
wrong twice on this module — `0x05` against `0x06` for the frame source, and
`0x07` bit 6's name. ArduCAM's own driver never writes `0x01`, so there was no
second opinion to read.

There is now, and the note is right about the count and wrong about the size.

1. **`0x01` is readable**, which almost nothing else on this surface is. 20 of 20
   writes echoed. That matters more than it sounds: `CAM_REG_AUTO_CONTROL` being
   write-only is why #33 took three weeks.
2. **The FIFO comes back `(N+1)` frames long**, exactly, on 20 of 20 counts.
3. **They are captures and not padding.** 40 slices across the five boots, 40
   distinct crc32s, no repeat.
4. **The speed argument is nearly empty.** Eight frames in one burst take **93%**
   of eight frames one at a time, on all five boots. The frame boundary is the
   cost, and it is paid either way.
5. **`255` is not 8 MB.** It returns **8,390,500 bytes** — 256 whole frames and
   1,892 bytes over — identical to the byte on all five boots. The last frame in
   that FIFO is torn, and the capture after it comes back empty.

## The ruler, and why there is no frame size written in this file

Everything here is a multiple of a number the boot measures rather than a number
the source knows. Stage L takes four ordinary captures and requires the non-empty
ones to agree; that length is the ruler, and every later stage is reported as a
multiple of it. It came out 32,768 bytes on all five boots and on all four visits
of each — which is 128 × 128 × 2, but the file never says so and would have kept
working if the mode had changed.

The stage runs **four times a boot**, between the other stages rather than once
at the end, and that is a change made after boot 00. That boot's closing ruler
read `0 / 32768 / 32768 / 32768` and the stage called the whole thing void,
because "all four agree" was the only rule it had. One empty capture then three
good ones is a different fault from a ruler that wanders, and running the stage
between the stages is what says which stage the empty one belongs to. It belongs
to stage M; see §4.

## 1. The count register

Stage R writes each count and reads `0x01` back:

```
  at entry: 00
  wrote 00, read 00   echoes
  wrote 01, read 01   echoes
  wrote 03, read 03   echoes
  wrote 07, read 07   echoes
```

Five boots, 20 writes, 20 echoes. Stage N then triggers one capture per count and
reads the FIFO length out of `0x45`/`0x46`/`0x47` — which is what `cam_collect()`
already does before it moves a byte of pixels, so the count question is answerable
without a buffer and without looking at an image.

| `0x01` | frames wanted | FIFO bytes | / ruler | trigger → CAP_DONE |
|---|---|---|---|---|
| `00` | 1 | 32,768 | 1 exactly | 38.6 ms |
| `01` | 2 | 65,536 | 2 exactly | 71.5 ms |
| `03` | 4 | 131,072 | 4 exactly | 143.1 ms |
| `07` | 8 | 262,144 | 8 exactly | 286.5 ms |

Boot 01's figures; the other four agree to within 0.2 ms on every row. **20 of 20
counts gave (N+1) rulers.**

The times say the same thing from the other side. The first frame costs 38.5 ms
and each one after it costs **35.8 ms** — 33.3, 35.7, 35.8 ms marginal across the
three steps — so the burst is running the sensor at its own rate and adding
nothing per frame. `255` runs at 35.85 ms a frame too (§4), over 256 of them.

## 2. Eight frames, not one frame eight times

A FIFO eight times as long could be eight captures or one capture written eight
times, and the difference is the whole of the only use anybody has proposed for
this register. Stage P slices the burst at the ruler and crc32s each slice:

```
  slice   crc32     R   G   B
  0       11f756f2   15  16   5
  1       8c996ae4   15  16   5
  ...
  7       bf59d455   14  16   5

  8 distinct slices out of 8
```

40 slices over five boots, no crc repeated. Two of the boots (`burst00`,
`burst01`) were taken in a dark room and three in a lit one — R means around 16
against around 136 — and the answer is the same in both, which is worth having
by accident: in the dark boots the channel means *walk* across the slices
(16 17 4 → 17 18 5, monotone in R and B), and in the lit ones they sit still and
only the crcs separate. The first is the AE loop moving between frames inside one
burst. The second is read noise. Either way the bytes are eight readouts.

## 3. The burst is 7% faster and that is all

Stage T takes eight frames both ways, with the SPI read included on both arms
because a caller has to move the pixels either way.

| | boot 00 | 01 | 02 | 03 | 04 |
|---|---|---|---|---|---|
| 8 singles | 590.5 ms | 591.5 | 591.4 | 591.4 | 591.5 |
| one burst of 8 | 552.2 ms | 552.0 | 552.2 | 552.2 | 552.2 |
| burst / singles | 93% | 93% | 93% | 93% | 93% |

Eight frames cost 286 ms of sensor and about 262 ms of SPI, and the burst saves
only the seven redundant trigger sequences in between — about 5 ms each. Whatever
this register is for, it is not for going faster. What it *is* for is holding
frames the RP has nowhere to put: 8 MB of ArduChip cache against 520 KB of SRAM,
which is the exposure-ramp-as-images idea in `docs/camera.md` and is a capacity
argument, not a speed one.

## 4. `255`, and the capture it costs

The note calls `255` "memory full (8 MB)". `cam_collect()`'s CAP_DONE poll gives
up after 3 s — correctly, for the single frames it was written for — so this
stage polls CAP_DONE itself with a 40 s bound and prints the bound next to the
answer.

```
  8390500 bytes after 9178054 us
  against 8 MB (8388608): +1892 bytes
  against the ruler: 256 whole frames and 1892 bytes over
  NEITHER 8 MB NOR A WHOLE NUMBER OF FRAMES - the last frame in this FIFO is torn
```

**8,390,500 on all five boots, to the byte.** 8 MB is 8,388,608 and 256 frames is
also 8,388,608, so the note's two descriptions of `255` agree with each other and
neither agrees with the board — by 1,892 bytes, which is 5.8% of a frame. This
directory records the number and does not explain it. What it does say is the
consequence: a caller that slices a `255` burst at the ruler gets 256 frames and
a 1,892-byte remainder, and the 256th frame is not the whole 256th frame.

**And the capture after a `255` comes back empty.** On 5 boots of 5, stage L's
first capture after stage M returned a FIFO length of 0 and the three after it
returned the ruler:

```
  !! FIFO length 0, buffer is 262144
  capture 0: 0 bytes   <- EMPTY
  capture 1: 32768 bytes
  capture 2: 32768 bytes
  capture 3: 32768 bytes
```

The empty count by stage is **0 / 0 / 0 / 1** on every boot — nothing after the
count sweep, nothing after the slicing and timing stages, one after `255`. That
is the attribution the four ruler visits exist to make, and it is why they are
there.

## 5. The put-back works, which was not a given

Everything above writes a register the driver does not know exists, and two weeks
of #33 have made the un-set the thing to check rather than the set:
[`../20260907-hold/`](../20260907-hold/) found
`cam_image_auto_mask(CAM_AUTO_ALL)` failing to switch a loop back **on** 31 times
in 56, on the same board, through the same bus. If writing `0` to `0x01` did not
take, every capture for the rest of that boot would be a multi-frame blob
arriving in a single-frame buffer, and the caller would have no reason to
attribute it to a probe that had already finished.

It takes. The ruler reads 32,768 at all four visits on all five boots, and the
one empty capture in §4 recovers on the next one. `ruler=unchanged` on 5 of 5.

## The runs

| boot | scene | `0x01` echoes | (N+1) rulers | distinct slices | burst/singles | `255` bytes | empties |
|---|---|---|---|---|---|---|---|
| [`burst00`](burst00.log) | dark | 4 / 4 | 4 / 4 | 8 / 8 | 93% | 8,390,500 | see below |
| [`burst01`](burst01.log) | dark | 4 / 4 | 4 / 4 | 8 / 8 | 93% | 8,390,500 | 0/0/0/1 |
| [`burst02`](burst02.log) | lit | 4 / 4 | 4 / 4 | 8 / 8 | 93% | 8,390,500 | 0/0/0/1 |
| [`burst03`](burst03.log) | lit | 4 / 4 | 4 / 4 | 8 / 8 | 93% | 8,390,500 | 0/0/0/1 |
| [`burst04`](burst04.log) | lit | 4 / 4 | 4 / 4 | 8 / 8 | 93% | 8,390,500 | 0/0/0/1 |

`burst00` is a different build — it ran stage L twice rather than four times and
called the trailing empty capture a void ruler. Its numbers are otherwise the
same and its empty capture is visible in the log at the same place. It is kept
because it is the run that showed the stage needed splitting.

All five read sensor id `0x82` and firmware `2023-03-03, fpga rev 32`, at 320 MHz
sys.

## What this does not say

- **Not a cold-boot result.** These five are firmware resets through
  `host/bootsel.py --flash`, not hub power cycles. `../20260907-lockrate/` needed
  power cycles because it was measuring something that varies by boot; a register
  width does not, and five identical readings to the byte are the evidence for
  that rather than an assumption about it.
- **Nothing about whether the driver should use it.** `cam_collect()` reads the
  whole FIFO in one burst into one buffer, and nothing in `frame.c` has anywhere
  to put eight frames. Wiring this up is a separate change with a separate
  argument, and §3 says the speed argument is not it.
- **Nothing about the 1,892 bytes.** Measured, reproducible, unexplained. It is
  not a multiple of the ruler, of 4, or of the 256-byte chunk `cam_collect()`
  reads in.
- **Not a check that the frames are evenly spaced in time.** The slices differ
  and the means walk, which says they are separate readouts; the interval between
  them is the sensor's and this probe never asked what it is.
- **Nothing about other resolutions.** One mode, `cam_mode_128()`'s `0x0b`. The
  ruler is measured rather than assumed precisely so the same file would answer
  at another size, but no boot has run one.
