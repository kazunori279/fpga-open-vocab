# M2 link RTL

Two loopback designs for the MCU↔FPGA link, sharing one core. Both simulate
anywhere and both now synthesize to a real T8F49 bitstream.

## What is here

| File | Role |
|---|---|
| `link_core.v` | The whole design. XOR-reduce `WIDTH` data lines to one bit, delay 8 link clocks, invert, drive the return line. Plus a heartbeat and three LED status bits off the 32 MHz oscillator. |
| `link_narrow.v` | Configuration **A** top, `WIDTH=1`. No board modification. |
| `link_wide.v` | Configuration **C** top, `WIDTH=3`. Needs the PIN2↔PIN17 jumper. |
| `link_narrow_io.isf`, `link_wide_io.isf` | Efinity pin assignments. |
| `link.sdc` | Timing constraints, shared by both. |
| `tb_link.v` | iverilog testbench, both widths plus a shorted-line negative control. |
| `mk_peri.py` | Turns an `.isf` into the `.peri.xml` the flow needs. Runs inside the container. |
| `docker/Dockerfile` | Efinity 2026.1 under Linux/amd64. |
| `build.sh` | Drives the whole synthesis from macOS. |

## Simulate

```sh
make sim
```

No vendor tools required. The testbench sweeps 12.5/25/50/75 MHz for each width,
reports the correlator's recovered offset and error count, then runs the negative
control. It ends `PASS` or `FAIL`.

**What the testbench does not tell you.** It cannot predict the maximum link
frequency. A correlator makes overclocking look like a shifted offset rather than
corruption, and the point where that stops being true depends on the real
clock-to-out delay of a placed-and-routed T8 plus the board's signal integrity.
That is M2's measurement, not simulation's.

## Synthesize

```sh
export EFINITY_TARBALL=~/Downloads/efinity-2026.1.132-linux-x64.tar.bz2
export EFINITY_VERSION=2026.1
./build.sh narrow      # or: ./build.sh wide
```

The first run builds a ~6 GB image; after that a full compile is under a minute.
Output lands in `rtl/build/` — the bitstream plus the timing, pinout and resource
reports — and is gitignored. Re-run cmake in `firmware/` afterwards so
`tools/hex2c.py` embeds the new `.hex`.

Efinity is Linux/Windows only, so this runs in a Linux/amd64 container under
Rosetta. The build happens in `/tmp`, not here: Docker Desktop cannot bind-mount
`~/Documents` (iCloud-synced) and hangs the container in "Created" if you try.

### Four things the flow does not tell you

These each cost an hour. Written down so they cost nobody else one.

1. **`efx_run.py` does not need a project `.xml`.** Passing `--family Trion -d
   T8F49 --timing_model C2 -v <sources>` covers everything the XML would carry.
   The XML is a GUI artifact.
2. **It does need a `.peri.xml`, and only the GUI can make one from scratch.**
   The shipped `efx_run_pt_import_isf.py` merges an ISF into an existing design,
   so a project that has never been opened in the Interface Designer has no way
   in. `mk_peri.py` calls `DesignAPI.create()` first, which is the missing half.
   Without it the flow prints one line — `Warning: Skipping Interface Designer
   step` — and then fails in place-and-route with no pin assignments.
3. **The `.sdc` is found by filename, not by flag.** `link.sdc` is ignored;
   `link_narrow.sdc` is picked up. `build.sh` copies it under the design's name.
   An ignored SDC does not fail — it defaults every clock to a 1 ns period, so
   the report is full of dramatic negative slack that means nothing.
4. **Turn the bitstream header off.** By default Efinity prepends an ASCII
   banner (`Version: …`, `Generated: …`) *inside* the byte stream. The Efinity
   programmer strips it; our firmware would shift it into the FPGA.

The container also needs libX11 (for `efx_pnr` and `efx_sta`) and libGL (the
headless periphery API imports PyQt6 on the way in), plus `PYTHONPATH` including
`$EFXPT_HOME/bin` and a writable `EFINITY_USER_DIR_INI`. All handled in the
Dockerfile.

## Results

Both configurations place, route and generate a bitstream on **T8F49C2**.

| | `link_narrow` | `link_wide` |
|---|---|---|
| Flip-flops | 34 | 38 |
| Adders | 22 | 22 |
| LUT4s | 5 | 11 |
| Global buffers | 2 | 2 |
| Fabric Fmax, `link_clk` | 365 MHz | 228 MHz |
| Fabric Fmax, `clk_32m` | 150 MHz | 146 MHz |
| Bitstream | 173,124 bytes | 173,124 bytes |

Out of 7,384 LEs, so the link costs roughly half a percent of the device. The
bitstream is a fixed size regardless of design — it is a full frame image.

**Do not read those Fmax numbers as link rates.** The SDC constrains internal
paths only; there is no `set_input_delay` or `set_output_delay`, because those
would need the RP2354A's PIO clock-to-out, which is not something the FPGA
toolchain knows. What 365 MHz says is that the *fabric* is nowhere near the
limit — the link ceiling will be set by the PIO's instruction count and by
pad-to-pad timing on the real board, and only M2's sweep can measure it.

Two details worth carrying forward:

- **`link_wide` is slower in the fabric than `link_narrow`** (228 vs 365 MHz)
  because XOR-reducing three lines adds a LUT level ahead of the shift register.
  Both are far above anything the PIO can generate, so it does not matter here,
  but the pattern will matter in M6.
- **The jumper buys less clock quality than hoped.** On B3, a real GCLK ball,
  pad-to-global-buffer routing is 2.64 ns; on F3, which is not clock-capable, it
  is 3.99 ns. Both then pay the same 3.32 ns through the buffer itself. So B3 is
  1.35 ns better, not a different class of routing. The jumper's value is the
  third data bit, not the clock ball.

## Design notes

**Why XOR-reduce instead of echoing each line?** The return path is one wire —
GPIO6 has no contiguous neighbour, so it cannot be widened — and a 3-bit forward
bus therefore cannot be echoed bit-for-bit in real time. The trade-off is that
two lines failing in the same cycle can alias to a correct parity. The realistic
failure mode is one line missing its setup window, which is always caught.

**Why invert the return?** So that a solder bridge from a data line straight to
the return line reads as ~50% errors instead of a perfect pass. Without the
inversion the most likely wiring mistake is indistinguishable from success.

**Why is everything on `posedge link_clk`?** Sample-phase tuning then lives in the
PIO program, which rebuilds in seconds, rather than in the bitstream, which needs
Efinity. Only the heartbeat and the LED divider run on `clk_32m`.

**Why are `link_wide`'s data ports scalar rather than a vector?** The Efinity
`.isf` bus-assignment syntax is unverified here. Three named scalars assign
unambiguously in a syntax copied from a working vendor example.

**Passive vs active configuration mode.** The board is strapped passive, and
`build.sh` passes `mode=passive`. On Trion T8 this turns out to produce a
byte-identical bitstream to `mode=active` — verified by diffing the two — so it
is a statement of intent rather than a functional switch. The pinout report
still prints `Configuration mode: active (x1)`; that line is cosmetic here.

**Check the LED polarity on the board.** The vendor `.isf` has red and blue
swapped; the schematic says E1 = red, F1 = green, G1 = blue, and both `.isf`
files here follow the schematic. The pinout report confirms the assignment went
where it was asked to go, but only the board can confirm the schematic is right.
