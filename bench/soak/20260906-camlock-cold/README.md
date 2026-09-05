# The cold run #30 asked for — written before the first frame

*2026-09-06, Sunday, from 06:41 JST. Board idle since 2026-08-25 and found in
BOOTSEL with no `/dev/cu.usbmodem`, so this is a genuine cold start. Desk empty,
nothing shown to the camera at any point.*

**Everything above the results heading was committed before any run was
launched.** That is the entire point of this directory.
[`../20260825-camlock/`](../20260825-camlock/) reached a coherent reading — the
lock helps while the room is still settling and its benefit closes in the second
half — and could not quote it, because the half-and-half split was chosen after
seeing the numbers. This session exists to give that reading a chance to be wrong
in advance.

## The hypothesis, stated as a prediction

A camera lock can only help a sensor that is still re-deciding. So the benefit of
freezing exposure and gain should be **largest in an unsettled room and should
decay as the session warms**, which is why this run starts at dawn on a cold
board rather than at any convenient hour.

If that is right, this session should show a locked-versus-free gap that is
present early and gone late. If the gap is flat across the session, or absent,
the 08-25 reading was the shape of eight numbers and not a mechanism.

## The protocol, fixed in advance

Identical to 08-25's so the two sessions can be pooled, and nothing about it is
chosen once numbers exist.

- **Eight pairs, sixteen runs**, 600 frames each. Not "until it looks clear".
- Arms **alternate**, and which arm leads **swaps every pair**, so the arm is not
  confounded with position within a pair.
- Every run is `host/demo.py "a closed book" "an opened book" --no-smooth
  --frames 600 --leave-running`, with `--enrol=40:L` added on locked runs. The
  queries only define the axis. The desk is empty throughout.
- `demo.py` re-enters `ft_acquire()` each run, so the lock state resets between
  runs and the arms cannot leak into each other. 08-25 verified this with
  `statecheck-{1,2}.log`; it is not re-verified here.
- Scored by `tools/probe_camlock.py`, `common` and `margin`, both the range of a
  centred 31-frame mean after dropping 60 frames of ramp.

**Discards.** A run is dropped only for a mechanical failure named at the time it
happens — a USB outage, a camera bus stall, an `EXPOSURE NEVER SETTLED`. A run is
never dropped for its value. A dropped run is re-run at the end of the session,
not in place, and is listed below with its reason.

## The tests, fixed in advance

| | test | why this one |
| --- | --- | --- |
| **Primary** | Mann–Whitney U on `common`, locked vs free, one-sided (locked < free), all eight pairs | Exact replication of 08-25's test, which gave U = 46/64, p ≈ 0.07 |
| **Secondary** | The same test restricted to **pairs 1–4** | The cold window. 08-25's post-hoc split was its first half of eight, so this is that split, pre-registered |
| **Tertiary** | Sign of the per-arm slope of `common` against position in session | The decay the hypothesis actually predicts, and the thing a two-group test cannot see |

The primary is the one that counts. The secondary is the hypothesis under test and
is underpowered at four a side by construction — the smallest one-sided p it can
reach is 1/70 ≈ 0.014 — so a null there is not evidence of absence and will not be
reported as one.

**What would make the 08-25 reading survive:** the secondary significant with the
tertiary showing a negative free-arm slope and a flat locked one. **What would
retire it:** a flat gap across the session, or no gap.

Pooling with 08-25 is legitimate for the primary and only the primary, since the
protocol is identical. It is not legitimate for the secondary, because 08-25's
window was defined after the fact.

## Timing, and the limit it puts on this

600 frames is about four to five minutes, so eight pairs is roughly eighty
minutes and the session ends near 08:10. **The cold window is therefore only
pairs 1–4.** Runs cannot be shortened to fit more into it: the walk statistic is
a range over 31-frame windows, and a 300-frame run would shrink it for reasons
that have nothing to do with the lock.

This is a real ceiling on the design and is stated here rather than discovered
later. If the effect needs a colder room than pairs 5–8 see, this session can
only ever measure it in half of itself.

## Also recorded, for issue #32

`last mean RGB` per run, because the AEC's boot-time branch is now its own issue
and sixteen cold boots on an unmoved desk is exactly the sample it wants. No test
is pre-registered for it — it is an observation this session gets for free, not a
question it was designed to answer.

## Results

*Nothing here yet. This section is written after the session and the sections
above are not edited when it is.*
