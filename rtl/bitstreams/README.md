# Shipped bitstreams

Efinity ASCII hex for the T8F49, one directory per milestone. These are the
images that were actually verified on hardware — not the latest build, and not
necessarily reproducible from the RTL beside them.

**They are checked in because they cannot be regenerated.** `rtl/build/` is
gitignored and `build.sh` overwrites its output in place, so every synthesis run
destroys the previous image. That would be a routine tradeoff if the RTL
determined the result, but it does not: M11 found that **a place-and-route seed
is not portable across netlists.** The seed that produced the shipped M10 image
became the *worst* of four options on the M11 netlist — 59.9 MHz against 63.9 —
so re-running `build.sh` at the recorded settings is not guaranteed to hand back
the same quality of result, or the same file. About 2 MB of ASCII hex is a cheap
price for not having to find out under pressure.

Use one directly; nothing needs copying into `rtl/build/`:

```
uv run host/m7.py   --bitstream rtl/bitstreams/m16/gemm_top.hex \
                    --wide      rtl/bitstreams/m16/gemm_top_wide.hex
uv run host/demo.py --bitstream rtl/bitstreams/m16/gemm_top_wide.hex
```

| | link config | measured on hardware |
|---|---|---|
| [`m16/`](m16/README.txt) | `gemm_top` = A (narrow), `gemm_top_wide` = C | **current.** int4 weights, in-tile requantize, paired taps; config C 569 ms at 75.0 MHz, 304 ms at 140.0 MHz |
| [`m11/`](m11/README.txt) | same | previous known-good, kept as the fallback; m7 ladder PASS both, config C 845 ms at 75.0 MHz |
| [`m10/`](m10/README.txt) | same | kept as the seed evidence above |

The m16 images need firmware built at `GP_KPACK=1`; the m10 and m11 images need
`GP_KPACK=0`. A mismatch does not fail politely — see
[`m16/README.txt`](m16/README.txt).

Each directory's `README.txt` carries its seeds, its reported fmax and slack, and
what was run against it. Read those before trusting a number here.

**The reported fmax is below the clock these run at, and they are bit-exact
anyway** — 75 MHz `link_clk` against a 52–65 MHz report, and 140 MHz against
M16's 52.9, which has been true of every image since M6c and is discussed in the clock rows of
[`docs/history.md`](../../docs/history.md). Treat the
slack figures as a way to spot drift between respins, not as a prediction of
whether the board will work.
