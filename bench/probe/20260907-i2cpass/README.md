# `0x07` bit 0 does fire an I²C read, and it costs the control surface

*2026-09-07, four boots of `forgix_cam_i2c`, each after a hub power cycle. Not
a bench: no cue schedule, no enrolment, no held-out set, no accuracy. **The
headline question is answered and the validation is not**, and this file says
which is which in every section.*

| log | what it was for |
|---|---|
| [`discover.log`](discover.log) | the first run. Named `0x0b`, `0x0c` **and** `0x48`, which is a defect in the rule and not three registers |
| [`stageB.log`](stageB.log) | rule A1 corrected to exclude the probe's own inputs. Named `0x48`, `0x8b`, `0x8c`, `0xc8` — the top half is a mirror |
| [`decisive.log`](decisive.log) | alias fold and a 512-address stage B. **`0x48` alone.** Stage B returned nothing and blamed stage A for it |
| [`stageB-handled.log`](stageB-handled.log) | stage B made to check its own handle first. It has none, and now says so instead of returning a verdict |

## Why this binary and not the AWB soak it is blocking

`bench/soak/20260907-simsrc-vs-live/` cleared everything downstream of the
sensor — `common` walk 0.00 on 541 frames. `bench/soak/20260907-camlock-cold/`
cleared the exposure and gain loops. What is left for
[#30](https://github.com/kazunori279/fpga-open-vocab/issues/30) is the auto
white balance, and it is the one loop that cannot be held:
`bench/soak/20260825-camlock/` measured `attribute.log` going from mean RGB
130 127 129 with exposure and gain frozen to **100 158 122** with white balance
frozen too. Clearing bit 7 drops the colour gains toward unity rather than
latching what the loop chose. That is a green cast, not an arm. `0x31`–`0x35`
are gain and exposure only. **The AWB arm is blocked on reaching the die.**

## The answer: `0x48` is the data register, and the die is an OV3640

Stage A writes `0x0B`/`0x0C`, fires `0x07 = 0x01`, and dumps the whole ArduChip
space, at twelve sensor addresses, three visits, ascending then descending then
ascending. A register is named only if its value depends on **which** address
was asked for by more than it wobbles when nothing changed (A1) **and** it
retraces exactly (A2). Both are relations between this run's own measurements;
neither contains a constant.

```
reg    per-address means, all 12              spread   scatter  retrace
0b^    00 00 00 00 00 00 00 30 30 30 30 31    49       0        yes   (the probe writes this one)
0c^    00 01 02 03 0a 1c 1d 00 02 0a 0b 00    29       0        yes   (the probe writes this one)
48     00 00 00 00 00 00 00 00 00 36 4c 02    76       0        yes   <- A1 and A2
```

**The three addresses that produced anything are `0x300A`, `0x300B` and
`0x3100`, and `0x48` read `36 4c 02` at them.** `0x300A`/`0x300B` is where an
OmniVision-style map keeps its product ID, and `0x364C` is the OV3640's. That
is a value from outside this experiment agreeing with it, which is a stronger
witness than anything the probe could have computed about itself — and it is
the first time anything in this repo has named the die. `0x40` reads `0x82`,
which `cam.h` only resolves as "below 5MP".

**It is recorded as an observation and not as a pre-registered test.** The rules
above were written before the run; "0x364C means OV3640" was recognised after
seeing it, and reading it as confirmation of a hypothesis nobody had stated
would be exactly the move this directory exists to avoid.

Two more measurements fell out, both stated once because they are cheap and
permanent:

- **The ArduChip register space is 7 bits.** `0x80`–`0xFF` mirrored `0x00`–`0x7F`
  in all 4608 reads. The first correction to this probe was folding that alias:
  `0x8b`, `0x8c` and `0xc8` are not registers.
- **The space is completely static at rest** — three dumps, not one byte moved.
  Including `0x01`, which this probe's header expected to be a running frame
  counter and which reads `00`. A2 was written to defeat a counter that turns
  out not to exist.

Firing the bit thirty-six times did **not** break the capture path: luma 87 to
107 afterwards across the four runs, a normal picture every time.

## What is NOT answered, and the probe refuses to pretend otherwise

Stage B is the causal check: write two exposures four decades apart through
`0x33`–`0x35`, which `20260907-manexp/` proved move the picture, then sweep 512
sensor addresses and see what tracks. It never ran, because **after the I²C
fires the exposure writes stop working**:

```
handle check: luma 133 133 133 at 0x00010, 133 133 133 at 0x00200
              - parting 0 against a wobble of 0, SO STAGE B HAS NONE
```

`20260907-manexp/` measured the same two exposures as 11 and 233 on the same
board. Here they are the same byte. So `0x07` bit 0 does something that costs
the manual exposure surface — it does not break capture, and it does not break
the register reads, but the WO control block at `0x30`–`0x35` stops taking.
Whether the writes are being ignored or the auto loop is being switched back on
underneath them, this probe cannot say.

**`decisive.log` printed a verdict against `0x48` and that verdict is void.**
It read "stage A named 0x48 and stage B found NOTHING that tracks a value this
board controls. That is an artefact." The premise was false: the board did not
control the value. Stage B's guard was `hi_luma > lo_luma`, which passed on 97
against 100 — a three-count separation where the handle should be over two
hundred. The guard is now the same shape as A1, a parting against a wobble with
no constant in it, and a stage that cannot move its own handle exits without a
verdict. The void log is kept rather than deleted, for the same reason
`20260907-manexp/` kept the runs its rule (b) rejected.

**So `0x48` is not adopted.** Nothing in `firmware/cam.h` refers to it. The
evidence for it is strong and one-sided — three runs, A1 and A2, and a chip ID
that matches a part number — and it is still one stage short of the standard
this repo has been holding.

## What to do next, in order

1. **Recover the control surface after firing, and find out how.** Candidates
   in cost order: `0x07` bit 1, the documented I²C reset; re-running
   `cam_begin()`; a hub power cycle. Whichever works tells you what bit 0
   actually disturbed, and it is the difference between a readback the shipping
   firmware can use mid-run and one that costs a re-init every time.
2. **Then run stage B**, either after that recovery or with the order reversed
   so the handle is established and read back before anything is fired.
3. **Then the AWB gains.** With the die named, the OV3640's white-balance gain
   registers are documentable rather than guessable, and #30's AWB arm can be
   written as a lock instead of a hope.

## Reproduce

```sh
# power-cycle first: 0x06 and the rest of the ArduChip survive a reflash
uhubctl -l <the hub with the 2e8a device> -p <its port> -a cycle --delay 3
cmake --build firmware/build --target forgix_cam_i2c
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_i2c.uf2
uv run --script host/mon.py --out /tmp/i2c.log
# power-cycle again before flashing m9 back
```
