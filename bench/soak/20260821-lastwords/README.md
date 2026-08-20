# 2026-08-21, early morning — the flash record survives a power cycle

Four runs between 05:57 and 06:05 that finish the second owed item on
[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9): a last-words
record in flash, written *during* an outage and read back after the supply had
been taken away. They also answer a question about
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27) that was not the
one being asked.

The board is on hub port `2-1:2` throughout, at 280 MHz system / 140 MHz link.

## Why this had to wait for the camera

`lastwords.c`'s write path has exactly one caller, `usb_watch()` in `m9.c`, and
`usb_watch()` only runs inside a live frame loop. The camera state recorded in
`../20260820-usb-p2/README.md` — nothing but `08 01` unless something waits
300 ms after `cam_begin()` — meant no frame, no run, and therefore no way to
exercise the write at all. Everything verified before this session was verified
by seeding the sector with `picotool load -o 0x101ff000`, which proves the whole
read side and nothing about `lw_write()`.

`cam_probe-20260821-0557.log` is what unblocked it, and it is the #27 finding
below.

## Test A — `lw_write()` runs from the frame loop

`lw_testA-20260821-0600.log`. A 60-frame run with `--usb-drop-hard 20`, left to
reach its own give-up and reboot itself. The next banner:

```
reset     : chip_reset 00010000
usb       : the last run rebooted itself at frame 112 because this port stopped answering.
lastwords : THE LAST RUN LEFT A RECORD IN FLASH AND THEN DID NOT REBOOT ITSELF.
            Written just before the deliberate reboot, at frame 112, 76.448 s into that run.
            The bus had been gone 30241 ms by then, since frame 22; 0 outages and
            2 re-attaches before it. Stage was poll_host - waiting on stdin for a key or a new query set.
            That run was at 280 MHz; chip_reset read 00010000 as it stood.
            The scratch survived too, so this boot follows a reset that left the
            always-on domain alone - read the line above this one, not this one.
```

Written live, from the frame loop, with core 1 running behind
`multicore_lockout_start_timeout_us()`, and the run reported no declines. This
is the *uninteresting* case for #9 — the scratch survived and the `usb :` line
above already said the same thing — and the banner says so rather than claiming
credit for it.

## Test B — the case #9 exists for

`lw_testB-20260821-0605.log`, harness in `lw_testB.sh`. Same run shape, but
instead of letting the board give up and reboot, VBUS is cut eight seconds after
it leaves the bus — while it is still looping, and after the first re-attach
attempt has landed a `LW_WHY_KICK` record. Cutting VBUS is the only known
recovery from #9 and is exactly what takes the always-on domain away, which is
what made this class of outage unattributable by construction.

```
reset     : chip_reset 00010000  (scratch was cold: ...)
            new this boot: power-on reset - the supply arrived
lastwords : THE LAST RUN LEFT A RECORD IN FLASH AND THEN DID NOT REBOOT ITSELF.
            Written on the first re-attach attempt, at frame 28, 176.530 s into that run.
            The bus had been gone 2001 ms by then, since frame 22; 0 outages and
            0 re-attaches before it. Stage was poll_host - waiting on stdin for a key or a new query set.
            That run was at 280 MHz; chip_reset read 00010000 as it stood.
            The scratch did NOT survive, so the always-on domain went away between
            that record and this banner: the outage ended in a power cycle. This is
            the case issue #9 could not attribute before.
```

No `usb :` line, no `hang :` line, no watchdog tag — the scratch is gone, which
is the whole problem — and the run is still named: which frame, how long the bus
had been gone, and what the firmware was doing at the time.

### The void first attempt

`lw_testB-20260821-0601-void.log` has no `lastwords :` line and **is not a
firmware failure.** It used `bootsel.py --power-cycle --run`, which boots the
board twice: the first boot read the record, printed the banner and erased the
sector, all before `demo.py` raised DTR. `stdio_usb` drops everything written
before a host asserts DTR — enumeration is not a reader — so the banner that
mattered went into the void and the second boot correctly found the sector
blank. Kept because the failure mode is easy to repeat.

The fix is in `lw_testB.sh`: cut the power with `uhubctl -l 2-1 -p 2 -a cycle
-d 3` and nothing else, so there is exactly one boot and `demo.py` is already
waiting on it. Note that the port number is looked up, not assumed — see
`../usb_soak.sh`.

## `cam_probe-20260821-0557.log` — the camera has two faults, and they alternate

The repeat-capture matrix is back to what 2026-08-03 recorded, exactly:

```
  #  recipe      2026-08-03    2026-08-20    2026-08-21
  0  as-was      a picture     CONSTANT      a picture
  1  as-was      CONSTANT      CONSTANT      CONSTANT
  2  no-rewrite  a picture     CONSTANT      a picture
  3  flush       CONSTANT      CONSTANT      CONSTANT
  4  norw+flush  a picture     CONSTANT      a picture
  5  settle300   a picture     a picture     a picture
  6  everything  a picture     a picture     a picture
```

So the two faults are separate and were not both present on any one day:

- **the redundant-write fault (08-03, 08-21).** Rows 1 and 3 only. A second
  identical FORMAT/RESOLUTION write blanks the next frame; rows 2 and 4 write no
  registers and are fine.
- **the settle fault (08-20).** Rows 0/2/4 as well. Nothing produces a frame
  without a ≥300 ms untriggered stretch after `cam_begin()`, regardless of what
  is written to the bus.

`#27` asks for the threshold in the second one. **It cannot be measured on a day
the second one is absent**, and 08-21 was such a day: the sweep returned 3/3 at
every value from 0 to 400 ms in both directions.

### The sweep's control was wrong, and the log shows it being wrong

The version that produced this log used a three-row preflight — long settle after
a reset, then settle 0 with no reset, then settle 0 after a reset — expecting
picture / picture / CONSTANT:

```
  control   : reset then 400 ms -> a picture;  then settle 0, no reset -> CONSTANT;
              then reset and settle 0 -> a picture
              *** cam_begin() does NOT un-start it the way this sweep needs.
```

Both of its verdicts are wrong, in opposite directions.

Row 2 ran with `rewrite = true`, so it was a second identical register write —
i.e. **it reproduced the redundant-write fault**, which is the other bug, and
read CONSTANT for a reason that has nothing to do with settling. Row 3 was
expected to read CONSTANT because the reset had un-started the sensor, but a
sensor that has no settle fault produces a picture at settle 0 whether or not it
was reset, so the row cannot distinguish "the reset did nothing" from "there is
nothing to un-start". The control announced a broken `cam_begin()` on a boot
where the only thing actually established is that the camera was in the other
state.

`firmware/cam_probe.c` now checks the premise with `rewrite = false` (no register
writes at all, so the 08-03 fault cannot fire) and uses the **descending pass**
as the control: it opens at 400 ms, which works, so a `cam_begin()` that does not
un-start the sensor makes every row below it work too and `down` reads full marks
where `up` does not. It also names the "fault absent this boot" case explicitly
instead of printing a column of 3/3 and a threshold verdict.

Re-run it when the matrix looks like 2026-08-20 again.
