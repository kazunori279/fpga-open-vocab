# The module can be told an exposure, and `cam.h:246` was wrong

**2026-09-07, four runs of `forgix_cam_manexp`, all at 320 MHz.** Not a bench:
no cue schedule, no enrolment, no held-out set, no accuracy. It settles one
contradiction inside the repo and the four logs here are the whole of it.

`firmware/cam.h:246` stated as fact that

> There is no manual value to write on this module - the register takes a
> switch, not a number - so a lock can only ever mean "stop deciding", never
> "use this number".

while `docs/camera.md` had said since 2026-09-06 that writing `0x31`–`0x35`
"turns that hope into a lock". Two files, opposite claims, no experiment. This
is the shape of the 1200-baud CDC touch, where four files disagreed and the fix
was to make them agree rather than to pick a winner.

**They now agree, and the code was the one that was wrong.**

| register group | verdict |
|---|---|
| `0x33`/`0x34`/`0x35` manual exposure | **responds** |
| `0x31`/`0x32` manual gain | **responds** |

## The run that settles it

[`three-pass-repeat.log`](three-pass-repeat.log), the exposure section. Three
passes over the same eight values in three different orders — ascending,
descending, and evens-then-odds:

```
null           -:133      -:133      -:133      -:133      -:133      -:133      -:133      -:133   range 0
up         00010:11   00040:59   00100:159  00400:233  01000:233  04000:233  10000:5    40000:5     range 228
down       40000:5    10000:5    04000:233  01000:233  00400:233  00100:159  00040:59   00010:11    range 228
mix        00010:11   00100:159  01000:233  10000:5    00040:59   00400:233  04000:233  40000:5     range 228
```

Every value read **the same number in all three orders**. Worst retrace gap 0,
worst within-value spread 0, against a range of 228 across values. A scene that
happened to be drifting cannot produce that, and neither can a register doing
nothing. This is a deterministic function of the written value.

Two shapes in it are worth carrying forward:

- **The response saturates at `0x400`.** `0x400`, `0x1000` and `0x4000` all read
  233. The useful part of the ladder is below `0x400` at this light level.
- **Setting the high nibble goes dark, not bright.** `0x10000` and `0x40000` —
  the two values with a non-zero `0x33` — both read 5. Either that byte is not
  exposure `[19:16]`, or the field is narrower than the app note says. Untested
  which; **the working range measured here is `0x00000`–`0x0FFFF`.**

## The verdict rule was changed once, and the reason was not the answer

Three rules are in the logs and the middle one is the interesting entry.

Rule **(a)(b)** was fixed before the first run: respond means the UP range beats
a measured NULL range, *and* DOWN retraces UP to within that NULL range. It
returned "does not respond" twice.

**The first rejection was mechanical and the fix was mechanical.**
[`no-settle.log`](no-settle.log) measured immediately after each write, and the
sensor applies an exposure at a frame boundary, so every capture reported the
previous value. A one-step shift misaligns UP and DOWN maximally, which breaks
(b) while leaving (a) intact — exactly the pattern in that log, UP range 228
against NULL range 1. Three settle captures were inserted between the write and
the measurement. **The rule was not touched**, on the stated ground that a rule
relaxed until the data passes is not a rule.

**The second rejection was the rule's own construction.**
[`settle.log`](settle.log) still failed (b) — and it failed it *because the
settle worked*. Criterion (b) is

```
worst disagreement between UP and DOWN   <=   range of the NULL
```

which puts a between-pass quantity on one side and a within-pass quantity on the
other. With the null now perfectly flat — range 0, eight identical captures of a
still scene — the right-hand side is zero, and **no register with any hysteresis
at all can pass.** Rule (b) became strictly unsatisfiable exactly because the
measurement improved. That is a defect readable off the rule without reference
to any data, which is not the same as a rule that merely returned an unwelcome
answer.

So rule **(c)** was written, and the runs already taken were **not** rescored —
they keep their verdicts and their logs say so.

```
RESPONDS-B iff  spread ACROSS values  >  worst spread WITHIN a value
```

Each value is visited once per pass, so it has three readings. Revisiting the
same number should land you closer together than visiting different numbers.
**Rule (c) has no threshold, no tolerance and no null in it** — there is no free
parameter that could have been chosen to make the data pass, which is the only
reason it was allowed to be written after the data existed.

## All four runs

| log | exposure UP | rule (a)(b) | rule (c) |
|---|---|---|---|
| [`no-settle.log`](no-settle.log) | range 228, lagged one frame | no | not yet written |
| [`settle.log`](settle.log) | range 194 | no | not yet written |
| [`three-pass.log`](three-pass.log) | **range 0 — flat** | no | no |
| [`three-pass-repeat.log`](three-pass-repeat.log) | range 228, gap 0 | **yes** | **yes** |

Three of four show a large response. The fourth shows nothing at all — 32
captures of a live desk all reading exactly 130, on a code path that had not
changed since the run before it.

**That run is not thrown away and it is not explained.** The one thing known
about it is that it followed a manual-exposure run with only a reflash in
between, and `20260907-simsrc/` established that a reflash leaves the ArduChip
powered. So it inherited whatever state the previous run left. That is a
hypothesis with a cheap test — power-cycle, run, power-cycle, run — and nobody
has run it. Until someone does, **the honest form is that these registers
respond and there is one observed way for them to appear not to.**

## The auto loops do not take the value back straight away

Read down the same repeat log past the exposure section:

```
-- back to auto between sections --
  settled at luma 11
```

Twenty captures with `CAM_AUTO_ALL` back on, starting from a manual exposure of
`0x010`, and the frame is still at 11. It was 133 before the sweep. By the end
of the run it had reached 88, still not back.

This is `cam.h:245`'s note running the other way. That note says switching a loop
off is not the same as holding what it decided. This says **switching it back on
is not the same as forgetting what it was told** — not within twenty frames, at
any rate. Anything that writes these registers has to plan its own way back.

The consequence for this probe is that the gain section of the repeat run
executed against a floored dark scene, so five of its six points sit at 5 and
only `0x3ff` escapes. It passes both rules, but it is the weak gain measurement.

## Gain, from the run whose scene was not floored

[`three-pass.log`](three-pass.log), which had a clean null at 131:

```
up         00001:122  00004:135  00010:173  00040:162  00100:117  003ff:252   range 135
down       003ff:252  00100:116  00040:156  00010:152  00004:129  00001:121   range 136
mix        00001:122  00010:174  00100:118  00004:136  00040:158  003ff:252   range 134
(c) spread across values 135 > worst spread within a value 22 : yes
```

**Gain is not a monotone ladder.** It rises to `0x010`, falls back through
`0x100`, and jumps to saturation at `0x3ff`. The dip is real rather than noise:
[`settle.log`](settle.log) has the same shape a run earlier at a different scene
level — 95, 107, 143, 132, 89, 251 — with the same peak at `0x010`, the same
trough at `0x100` and the same spike at the top. Two runs, two light levels, one
curve.

So gain responds and **the mapping from written value to brightness is not the
obvious one.** Anything that wants a particular gain has to measure the curve,
not assume it.

## What this changes

`cam.h:246`'s claim is deleted; the file now says what was measured here.
[#30](https://github.com/kazunori279/fpga-open-vocab/issues/30)'s lock arm was
built on that claim — `'L'` switches the auto loops off through `0x30` and hopes
the last converged value stays put — and
[`20260907-camlock-cold/`](../../soak/20260907-camlock-cold/) found that hoping
did not reduce the common-mode walk over sixteen runs. **That arm can now be
rebuilt as a written value rather than a released loop**, which is a different
experiment and not a repair of that one. The sixteen runs stand as they are.

Two things stand between here and that rebuild, both from this page: the flat
run has no explanation, and the auto loops do not hand control back promptly.

## Reproducing it

```
cmake --build firmware/build --target forgix_cam_manexp
uv run --script host/bootsel.py --power-cycle
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_manexp.uf2
```

Then read the CDC port; it takes about three and a half minutes. **Power-cycle
first** — that is the one variable the flat run implicates, and a reflash on its
own does not do it.

The probe puts both register groups back and switches the auto loops on before
it stops, but this page's own finding is that the loops are slow to reclaim
exposure. **Power-cycle before flashing m9 back** —
`host/bootsel.py --power-cycle`, then
`host/bootsel.py --flash firmware/build/forgix_m9.uf2`.
