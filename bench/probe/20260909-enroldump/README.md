# The `'T'` dump prints what it claims to print

*2026-09-09, one boot of `forgix_m9`. Not a bench and not a measurement: the
three references in this log were all enrolled against the same unchanged scene,
so every accuracy number derivable from them is meaningless. This is an
instrument check for issue #34, and the instrument is the new key.*

Issue #34 asks whether a reference saved in one boot means anything in the next
one. Nothing in the firmware could answer that, because until this boot there
was no way to read a reference out at all — `qref[]` is written in one place and
never printed, and `flash_range_program` appears zero times in the image. Step 1
of the issue is the read half on its own, with no loader, so that nothing can
ship on the assumption before it is measured.

The key is `'T'`. It prints `qbg[]`, `bg_spread()`, COCO's `qmu`/`qsd`, then one
line per enrolled class carrying `qref_vis`, `qref_scat` and the reference
vector, then `eref[]`, then `enroldump : end`.

## What this boot establishes

Two dumps, 371 frames apart: one at board frame 44 with nothing enrolled, one at
frame 415 with two classes and the empty scene in.

1. **Both dumps are complete.** Every line the code can emit appeared, and the
   `end` line landed both times — the truncation check works, which matters
   because the dump is text on the same CDC line the frame log uses.
2. **The background is frozen and the dump proves it.** Both `bg` lines are
   character-identical across the two dumps, 371 frames apart:
   `qbg -1.171500161e-01 sd 3.764245892e-03` for query 0. That is the coordinate
   system the references are expressed in, and it stopped moving at frame 30.
3. **The scatters agree with the receipts.** The board printed
   `scatter 0.20 / 0.33 / 0.41` when each enrolment landed; the dump prints
   `1.999426782e-01 / 3.326686323e-01 / 4.144965708e-01`. Same numbers, more
   digits.
4. **The vectors are in the centred space, as documented.** With `nq = 2` the
   class space `c[i] = z[i] - mean(z)` is a single line, so `c[0]` must be
   exactly `-c[1]`. Both class references are exactly negated. `eref` is off by
   one ulp — `-5.400226712e-01` against `5.400227308e-01` — which is what
   accumulating two sums separately in binary32 does, and is the right kind of
   disagreement to see.
5. **The geometry closes.** Class 0 and class 1 differ by 0.9746302068 in each
   coordinate, so they are `0.9746302068 * sqrt(2) = 1.3783` apart. The board's
   own receipt said `nearest pair 1.38 apart`, computed by different code.

## Why the dump is text and not base64

`'P'` and `'V'` both stage a buffer and emit base64 through an emitter M8
verified, and the obvious move was to reuse it. The constraint that decides
against it is RAM: the note above `qref_sqsum` records about twenty bytes of
headroom, and a staging buffer for sixty floats is not twenty bytes. `printf`
walks the arrays in place. `%.9e` round-trips binary32 exactly, so nothing is
lost by the choice, and the output is greppable, which base64 is not.

There is no crc32 either. The `end` line catches the failure that can actually
happen here — a truncated CDC stream — and a checksum over text a human is
going to read is protection against a corruption mode this link does not have.

## What it does not establish

Nothing about #34's question. Three references enrolled against one unmoving
scene are three coordinates for the same thing; the log's `MATCH` lines flip
between the two classes on frame-to-frame noise, exactly as they should. The
across-boot comparison is step 3, and it needs a staged scene, a power cycle and
a held-out set.

## Files

- [`enroldump.log`](enroldump.log) — the whole boot, 539 frames, both dumps.
  Ended with SIGTERM rather than Ctrl-C: `demo.py` was started as a background
  job from a non-interactive shell, which leaves SIGINT at `SIG_IGN`, and Python
  does not override an inherited `SIG_IGN`. Nothing is lost — `emit()` flushes
  the log on every line — but the run has no closing tally.
