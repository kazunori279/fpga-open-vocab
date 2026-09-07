# `0x48` is a readback. The flat boots are a cache, and one bit clears it

*2026-09-07, thirteen boots of `forgix_cam_i2crec` and `forgix_cam_i2c`. Not a
bench: no cue schedule, no enrolment, no held-out set, no accuracy. This
directory was opened to find a recovery that [`../20260907-i2cpass/`](../20260907-i2cpass/)
said was needed, discovered it was needed for a different reason than that
directory gave, found it, and then ran the stage that had been blocked behind
it.*

Three things came out of it, in the order they were found:

1. Firing `0x07` bit 0 **does not** cost the manual exposure surface.
   `20260907-i2cpass`'s central claim is wrong.
2. What does cost it is a **boot-to-boot flat condition**, present with zero
   fires, and `0x07` bit 7 — reset cache — **buys it back**.
3. With a handle held either side of the sweep, **`0x48` returns what the die
   holds**: the sixteen bits written into `0x33`–`0x35` come back at `0x3002`
   and `0x3003`, byte for byte, on two consecutive boots. `0x48` is adopted in
   `firmware/cam.h`.

| log | what it was for |
|---|---|
| [`nothing-broke.log`](nothing-broke.log) | one fire, 36 fires, 36 null writes. **Nothing broke.** The recovery ladder never ran |
| [`bisect.log`](bisect.log) | stage A taken apart: the register dump, writing `0x07`, the high addresses, the low ones. All four harmless |
| [`dead-from-cold.log`](dead-from-cold.log) | the handle **dead before a single fire**, and the run halting because of it. The short log is the finding |
| [`stagea-whole.log`](stagea-whole.log) | 20260907-i2cpass's stage A reproduced entire — 36 fires, 256 register reads after each. Handle alive throughout |
| [`stageB-dead-again.log`](stageB-dead-again.log) | first attempt to run stage B in `forgix_cam_i2c`. Flat at 134. No handle, no test |
| [`stageB-mask-twice.log`](stageB-mask-twice.log) | the prediction that a second application of the mask recovers it. **Refuted**: 133 both times |
| [`stageB-void-verdict.log`](stageB-void-verdict.log) | the two-point handle passed on parting 4 against wobble 2, and the sweep behind it printed a verdict. **That verdict is void** |
| [`stageB-ladder-rejects.log`](stageB-ladder-rejects.log) | the six-rung ladder replacing the two-point check, rejecting the same board the two-point check had passed |
| [`stageB-order-reversed.log`](stageB-order-reversed.log) | handle acquired before any fire, so a failure cannot be blamed on the passthrough. Flat before the first fire |
| [`stageB-warmup-first.log`](stageB-warmup-first.log) | the full `CAM_AUTO_ALL` + 20-capture warm-up restored ahead of it. **Refuted**: still flat |
| [`stageB-sweep-0x3400.log`](stageB-sweep-0x3400.log) | the ladder alive at last, and the sweep reading 512 addresses from `0x3400`: 0 tracked, and no way to tell a null result from a broken sweep |
| [`stageB-sweep-0x3000.log`](stageB-sweep-0x3000.log) | base moved to `0x3000`, product ID added as a positive control. 4 addresses track |
| [`stageB-b3-confirmed.log`](stageB-b3-confirmed.log) | **the run this directory is for.** Second consecutive boot, same table, and the written exposure read back byte for byte |

## 1. The passthrough costs nothing

`20260907-i2cpass/` fired `0x07` bit 0 for the first time, found `0x48` holding
what comes back, read `36 4c` at the OV3640's product-ID addresses — and then
its stage B could not run, because the manual exposure writes had stopped
moving the picture: `luma 133 133 133 at 0x00010, 133 133 133 at 0x00200`. It
concluded that firing the bit costs the control surface.

The instrument here is the same handle check, run before each phase's cost is
incurred as well as after, which is the one thing `20260907-i2cpass` did not do:

```
                                       lo luma       hi luma      part  wobble
  baseline, nothing fired               6   6   6 | 182 182 182    176      0
  after ONE fire at 0x300A              6   6   6 | 183 183 183    103      0
  36 fires of 0x07 = 0x01               6   6   6 | 182 182 181    176      1
  36 writes of 0x07 = 0x00              5   5   5 | 179 179 180    174      1
  read all 128 registers, 3 times       6   6   6 | 181 181 180    175      1
  fire at the 5 HIGH addresses          6   6   6 | 184 184 184    178      0
  fire at the 7 LOW addresses           6   6   6 | 184 184 184    178      0
  STAGE A, WHOLE AND UNSHORTENED        4   4   4 | 144 143 143    140      1
```

The last row settles it. It is not a summary of stage A or a sampled version of
it: twelve addresses in the order that file lists them, three visits
ascending-descending-ascending, a full 256-register read after every fire. The
handle is alive on the other side.

Firing `0x07` bit 0 does not cost the manual exposure surface — not once, not
thirty-six times, not at low sensor addresses, not at high ones, and not
stacked with 3456 register reads the way the original ran it.

## 2. What actually happens, and what buys it back

The handle goes flat **before anything is fired at all**:

```
handle at rest    133 133 133 | 133 133 133   part 0  wobble 0  DEAD
```

Zero fires. `133` is the same byte `20260907-i2cpass`'s stage B reported, and
the picture sits exactly where the auto loop settled it — `cam_image_auto_mask`
is called and the exposure writes that follow do nothing. `0x30` is write-only,
so there is nothing to read back and the handle is the only check available.

This is not new. `20260907-manexp/` recorded one flat exposure run it could not
explain and left it open. This is the same thing, seen repeatedly.

On one flat boot the ladder ran end to end and named the rung:

```
   rung 0: apply the auto mask a second time, no reset       DEAD
   rung 1: 0x07 bit 1, the documented I2C reset              DEAD
   rung 2: 0x07 bit 7, reset cache                           ALIVE   part 149, wobble 1
```

**`0x07` bit 7 recovers it, from cold, with nothing fired.** That is one
register write. It is cheap enough for the shipping path to do unconditionally
before it touches exposure, and it explains every flat run in this series:
`cam_i2c.c` never does a cache reset, and `cam.c:455` does one only when it
flushes.

Two written-down predictions were refuted along the way and are kept here
rather than dropped: a second application of the mask does not recover it
([`stageB-mask-twice.log`](stageB-mask-twice.log)), and neither does restoring
the full warm-up ([`stageB-warmup-first.log`](stageB-warmup-first.log)).

## 3. The handle had to be rebuilt before stage B could be believed

[`stageB-void-verdict.log`](stageB-void-verdict.log) is the reason the rule
changed. The two-point handle — *two exposures four decades apart must part the
picture by more than repeated captures at one exposure wobble* — passed on
`parting 4 against a wobble of 2`, and the sweep behind it went on to print a
verdict calling `0x48` an artefact. Four grey levels apart is not a control
surface. **That verdict is void** and the log is kept as the reason.

The replacement is a **six-rung ladder**, six exposures doubling from `0x00010`
to `0x00200`, and two rules, still threshold-free and still no fitted constant:

- **H1** — the picture rises at every rung by more than the worst
  repeat-capture wobble across the six.
- **H2** — walking the ladder back down retraces every rung to within that same
  wobble.

The two-point form cannot distinguish four grey levels of drift from a working
handle; six monotone rungs and a retrace can. The ladder rejected the same
board the two-point check had passed ([`stageB-ladder-rejects.log`](stageB-ladder-rejects.log)).
The sweep's own brightening remark was converted from something printed
afterwards into a hard gate that halts the run.

## 4. Stage B: what `0x48` is

Base address matters. The first sweep read 512 addresses from `0x3400` and
found nothing, which is unreadable — a sweep that reads nothing and a sweep
that is broken print the same table. Moving to `0x3000` fixed both: it is where
the OV3640 keeps its exposure, and it contains the product ID, which makes a
free positive control. The sweep must find `36` at `0x300A` and `4c` at
`0x300B` or its silence means nothing.

The confirming boot, the second in a row with the same table:

```
  ladder before the sweep
    up      5  12  29  63 111 165
    down    5  12  29  63 111 165
    wobble 1, retrace 0 | H1 yes  H2 yes  -> HANDLE

  positive control: 0x300a reads 36/36 and 0x300b reads 4c/4c (low/high exp)

  sensor   at low exp     at high exp    verdict
  0x3002   00 00 00      02 02 02      B1 and B2 - TRACKS
  0x3003   10 10 10      00 00 00      B1 and B2 - TRACKS
  0x301b   02 02 02      54 54 54      B1 and B2 - TRACKS
  0x30de   ff ff ff      39 39 39      B1 and B2 - TRACKS
  0x3118   40 f8 f8      f8 40 40      unstable
  4 tracked, 1 unstable, 507 unchanged and not listed.

  B3: exposure written 0x0010, read back from 0x3002/0x3003 as 0x0010
      exposure written 0x0200, read back as 0x0200   ->  BYTE FOR BYTE
```

B3 is the decisive rule and it is stricter than "something moved": the value at
`0x3002`/`0x3003` is not merely correlated with the exposure, it **is** the
exposure, both halves, both times. It cannot be an echo of the question — the
probe never writes `0x3002`, it only asks for it, and what comes back is a
number the sensor was told through a different register entirely.

The other two tracked addresses are the sensor's own business; an exposure
change moves more than the register it is stored in. `0x301B` and `0x30DE` are
**not identified here and are not claimed** — they read differently on the two
boots (`3d`/`54`, `55`/`39`) while `0x3002`/`0x3003` were identical.

## What this licenses

**`0x48` is adopted.** `firmware/cam.h` now carries `CAM_REG_I2C_ADDR_H`,
`CAM_REG_I2C_ADDR_L`, `CAM_REG_I2C_DATA`, `CAM_I2C_INITIATE_READ`, and the four
OV3640 addresses these runs measured, with the provenance in a comment above
them. The adoption condition was written into the probe before the run that met
it: *cam.h gets `0x48` when a second boot reproduces this table, and not
before.*

**The die is an OV3640.** `0x300A`/`0x300B` read `0x36`/`0x4C` through this path
on six boots.

**The flat boot has a recovery, not an explanation.** One bit clears it. Why the
cache goes stale between boots is unknown, and knowing the cure is not knowing
the cause.

**Why `forgix_cam_i2c` never got a handle is still open.** Six boots, zero
successes, against a binary with byte-identical `luma()`, `write_exposure()`,
exposures, and stage A. The cache reset is the likely explanation and has not
been tested there.

## What to do next, in order

1. **Put the cache reset where it belongs** — a `0x07` bit 7 before the exposure
   surface is used, and a `cam_sensor_read()` helper in `cam.c` so the
   passthrough is reachable from something other than a probe. Nothing in
   `cam.c` uses the new `cam.h` block yet.
2. **The AWB gains**, which is what all of this is for. With the die named and a
   working readback, the OV3640's white-balance gain registers are documentable
   rather than guessable, and #30's AWB arm can be written as a lock instead of
   a hope.
3. **Sensor-only power cycle (`0x02`) against the cold-boot count**, from
   `docs/camera.md`'s list — now worth running because there is a cheaper known
   cure to compare it against.

## Reproduce

```sh
# power-cycle first: 0x06 and the rest of the ArduChip survive a reflash
uhubctl -l <the hub with the 2e8a device> -p <its port> -a cycle --delay 3
cmake --build firmware/build --target forgix_cam_i2crec
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_i2crec.uf2
uv run --script host/mon.py --out /tmp/i2crec.log
# power-cycle again before flashing m9 back
```

A boot that reports the handle DEAD at rest is the interesting one and is not a
failed run. Keep the log.
