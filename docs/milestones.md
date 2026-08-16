<!-- moved out of README.md on 2026-08-01; see ../README.md#documentation -->
<!-- 2026-08-11: the project was renamed MicroCLIP -> fpga-open-vocab, and that
     one identifier was replaced mechanically throughout this file. No number,
     date, claim or verdict was touched. It is the only after-the-fact edit
     here; the reasons are in history.md#the-name -->

# Dev plan — M0 through M21

The milestone-by-milestone record: what each one was scoped to do, what it
actually measured, and where the two differed. **Append-only.** Numbers here are
what was true when the milestone closed; the README carries the current ones,
and [`history.md`](history.md) is the curated read of the same material.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [history](history.md) · [bring-up log](bring-up-log.md)

---

Each milestone has a **headless check** — something measurable over USB serial,
no eyes on the board. Same discipline as `../2026-0702-xls-uart-blinky`.

### M0 — Answer the open questions ✅ *done*
Confirmed map is in [`docs/pinmap.md`](../docs/pinmap.md), extracted from the vendor
KiCad source by [`tools/kicad_netlist.py`](../tools/kicad_netlist.py). Questions 1–6
are resolved; 7 is mitigated and 8 is a cheap physical check that does not block
anything.

The headline result is negative: **#5 killed the 8-bit MCU↔FPGA bus.** M2's job
changed from "bring up a parallel bus" to "measure the 3-bit serial link and find
out whether the FPGA is still worth using."

### M1 — Bitstream loader ✅ *done, using the vendor loader*
The boards ship with Adiuvo's **forge-loader** firmware already flashed, which
exposes a USB CDC framed protocol and shifts a bitstream over the config SPI. No
custom firmware was needed to clear this milestone.

*Check (passed):* `uv run host/load.py vendor/plasm_led/plasm_led.hex` streams
173,380 bytes at ~133 KB/s and the `END` reply returns **CDONE = 1, nSTATUS = 1**.

**What CDONE actually proves** — established by deliberately feeding the loader
bad images (`--corrupt N`, `--garbage {zeros,ones,invert}`, both of which send a
*matching* CRC so the firmware's own integrity check passes and the bytes really
reach the FPGA):

| Image | Result | CDONE |
|---|---|---|
| valid `plasm_led.hex` | ACK `fpga programmed` | **1** |
| 4 KB inverted mid-stream | ACK `fpga programmed` | **1** |
| all `0x00` | NACK `fpga DONE timeout` | 0 |
| whole file inverted | NACK `fpga DONE timeout` | 0 |

So CDONE is a real signal — it cleanly separates "the config engine synced and
consumed a full frame count" from "it never started". But it is **not** an
integrity check: a 4 KB payload corruption still configures and still reports
success, and nSTATUS stays high, so the T8 raises no error for it. Configuration
framing survives; the fabric just gets wrong bits.

**Consequence for M6/M7:** never treat CDONE as evidence that the right design is
loaded. Bit-exactness has to be proven by the design itself — a readable ID
register over the M2 bus, then bit-exact GEMM comparison against the C reference.

*Physical confirmation:* with the valid image loaded, **the RGB LED animates.**
That is worth more than the CDONE read — it proves Y2 (32 MHz), the `OSC_EN`
gate, the PLL lock and the fabric are all actually running user logic, not just
that the config engine finished.

Two more things worth knowing before writing our own loader:

- The vendor `plasm_led.hex` is generated for **active (x1)** mode, yet it
  configures fine over **passive** SPI. The Efinity mode setting affects the
  flash-boot wrapper, not the passive data stream.
- `detail` in an ACK payload is **overloaded**: for `HELLO`/`STATUS` it is the
  loader state enum (0 IDLE, 1 PROGRAMMING, 2 DONE, 3 ERROR); only for `END` is
  it the FPGA pin bitmap. Decoding one as the other yields a plausible wrong
  answer — `host/forge.py` keys off the command sent.

### M1b — Own the config path ✅ *folded into M2*
Replaced the vendor loader with `firmware/fpga_config.c`: passive x1 SPI, mode 3,
bitstream embedded in the image by `tools/hex2c.py`. This could not be deferred —
M2 has to repurpose GPIO1/2/3 the instant `CDONE` rises, and a loader that owns
those pins as an external service cannot hand them over.

**Recovery is confirmed, and it is the `PRG`–`GND` strap on the short bottom
edge** (question #8). Flashing custom firmware is safe. The 1200-baud touch the
firmware still carries does *not* work here, so treat every reflash as a physical
bench interaction and batch your experiments into one image.

**The lead-in clocks are the thing to know about this file.** After releasing
`CRESET_N` the T8 must be *clocked* before it starts matching the synchronization
pattern — AN 006 Figure 15 draws the CDI0 waveform as `Header, D, D, D, …`, and
the 256-byte ASCII banner Efinity prepends to a `.hex` is exactly that lead-in,
not skippable filler. The first version of this file waited `sleep_us(100)`
instead, and an idle SPI master emits no clock, so the part got time and zero
edges. It cost two bench sessions: the vendor's `plasm_led.hex` configured every
time (its header supplied the clocks by accident) while nothing from
`rtl/build.sh` ever would, because that script passes `generate_header=off`.

`fpga_config.c` now clocks `LEADIN_BYTES` = 256 zero bytes before the image, so
configuration no longer depends on how the bitstream was generated. **Measured
minimum on this board is 32 bytes / 256 clocks**, swept ascending from zero at
bring-up, so the default carries 8× margin for 32 µs.

Two smaller corrections went with it. The `FPGA_ERR_NSTATUS` timeout loop is
gone: pin-probing showed nSTATUS is driven high externally and never dips, so it
exited on its first iteration every time and gated nothing. And `CDONE` is polled
while the trailing clocks go out rather than read once immediately — a single
read cannot distinguish "not configured" from "needed one more microsecond".

### M2 — MCU↔FPGA link ✅ **second GO / NO-GO gate — PASSED**

Two configurations, measured with the same code:

- **A — no board modification.** Clock GPIO2 → F3, 1 data bit GPIO3 → F2,
  return GPIO1 ← G3, heartbeat A4. Ceiling 8.9 MB/s each way.
- **C — one jumper, pad PIN2 ↔ pad PIN17.** Clock GPIO22 → B3 (a real GCLK
  ball), 3 data bits GPIO1/2/3 → G3/F3/F2, return GPIO6 ← A4. Ceiling
  26.8 MB/s out, 8.9 MB/s back.

The FPGA side is one shared `link_core`: XOR-reduce the data lines to one bit,
delay by 8 link clocks, invert, drive the return line. The reduction is forced —
the return path is one wire and cannot echo a 3-bit bus in real time — and the
inversion is what makes a data-to-return solder bridge show up as ~50% errors
instead of a perfect pass. The MCU correlates the returned stream against the
LFSR it sent, searching sample offsets 0–63, so pipeline latency is discovered
rather than assumed.

**A correlator changes what "too fast" looks like.** Overclocking a
source-synchronous link does not corrupt data; it slides the alignment offset,
and the correlator simply finds the new one. Errors appear only when the sample
instant lands inside the return line's transition window. The testbench therefore
cannot honestly predict a maximum frequency — it verifies the protocol, and a
shorted-line negative control proves the check can fail. The rate is hardware's
answer to give.

**Measured, configuration A, 2026-07-30.** Every operating point the firmware can
reach came back clean:

| variant | clkdiv | `link_clk` | offset | errors | MB/s |
|---|---|---|---|---|---|
| x2 | 1 | **75.00 MHz** | 10 | 0 | **8.94** |
| x2 | 2 | 37.50 MHz | 9 | 0 | 4.47 |
| x2 | 4 | 18.75 MHz | 8 | 0 | 2.23 |
| x4 | 1 | 37.50 MHz | 9 | 0 | 4.47 |
| x4 | 2 | 18.75 MHz | 8 | 0 | 2.24 |
| x4 | 4 | 9.38 MHz | 8 | 0 | 1.12 |

**Highest clean rate: 8.94 MB/s, and it is 8.94 MB/s *each way simultaneously*.**
Configuration A is one bit out and one bit back, so a single figure describes
both directions — there is nothing to report separately. That is 95% of the
9.375 MB/s the 75 MHz bit clock allows; the missing 5% is per-burst DMA setup,
not the wire.

**This is a floor, not a ceiling.** The sweep bottoms out at `clkdiv=1` with the
2-cycle PIO program, so 75 MHz is simply the fastest this firmware can clock the
link — no error was ever observed. The alignment offset moving 8 → 9 → 10 as the
rate climbs is the correlator doing its job, exactly as predicted above, not
degradation.

The estimate this validates: **8.94 × 3 lines = 26.8 MB/s**, which is precisely
configuration C's predicted forward rate. So the jumper's value is confirmed
arithmetically, though C itself is still unmeasured.

The netlist costs 34 FFs / 22 adders / 5 LUT4s in configuration A and 38/22/11
in C — about half a percent of the T8's 7,384 LEs. Fabric Fmax is 365 MHz (A)
and 228 MHz (C) on `link_clk`, which says only that the fabric is not the
constraint: the SDC has no `set_input_delay`/`set_output_delay`, because those
need the RP2354A's PIO clock-to-out, which Efinity has no way to know. The link
ceiling is the PIO instruction count and pad-to-pad timing on the real board.

*Check:* 1 MB LFSR round-trip, zero bit errors; print measured MB/s each way.
**Record the highest clean rate — every later estimate depends on it.**
✅ **0 errors at every operating point, 8.94 MB/s** — with one honest shortfall
against the spec above: the sweep sends 16 KiB × 8 bursts = **128 KiB per point**
(768 KiB across all six), not 1 MB per point. Zero errors in 1.05 M bits bounds
the BER below ~3 × 10⁻⁶ at 95% confidence, which is enough to call the link clean
but is 8× weaker than the check asked for. Raising `BURSTS` to 64 closes it;
that is queued to ride along with the configuration-C flash rather than spend a
`PRG` strap of its own.

### M3 — Memory bandwidth ✅ **answered as a side-effect of M5**
Originally: measure sustained read from the APS1604M PSRAM into SRAM, honoring
the ~8 µs max-CS-low refresh constraint. **That measurement is still unmade** —
U1 does not answer its ID probe ([#10](history.md#verify-before-building)) — but the
question it was asked to settle has been answered anyway, because the weights
are in the RP2354A's 2 MB stacked flash and the real issue is whether *any*
QMI-side store can feed the encoder.

**It can, with margin, and M5's per-layer table shows it directly.** Across
conv1–conv7 the weight bytes touched *per MAC* vary **64×** (conv1 reads
0.00097 B/MAC, conv7 reads 0.0623 B/MAC). If the flash fetch were binding, cost
per MAC would climb steeply along that axis. Instead it is flat and slightly
*falling* — 201 ms/MMAC at conv1 down to 187 at conv7, a 7% spread against a
64× change in fetch pressure. The run is compute-bound. (conv0 is the one
outlier at 438 ms/MMAC, and it points the same way: it has the *smallest*
weight set on the board at 864 bytes, so its extra cost is loop overhead
amortized over only 27 taps per output, not memory.)

The absolute numbers agree. 1.42 MB of weights per frame at 31.8 s/frame is
**45 KB/s**. Even at the M7 target of ~230 ms/frame it is 6 MB/s, against a
QSPI XIP ceiling in the tens of MB/s. A dedicated bandwidth sweep would be
measuring something already known not to bind; if M6/M7 make it bind, it
comes back.

**M5b tested this the hard way and it held.** Its tiled im2col deliberately
re-sweeps the weight matrix once per 32-pixel tile — ~5.2 MB per frame, 3.7× the
distinct bytes — and still ran 7.4× faster. At 3.36 s that is ~1.5 MB/s. A
restructuring that multiplies flash traffic and wins anyway is about as direct a
demonstration as this board can give that the fetch is not the constraint.

This is also why #10 is a curiosity rather than a blocker. PSRAM sits behind the
same QMI as the flash and would not be *faster*; what it buys is 2 MB of
writeable space, which matters for growing the model past the flash budget and
for holding activations, not for feeding this one.

### M4 — Distill the student ✅ **GO / NO-GO GATE — PASSED**
Train a ~1.5M-param CNN (128×128 input, ~250 MMACs) against a frozen CLIP
teacher, cosine loss on the 512-d output. Evaluate on the **actual query set** —
"a person smiling" vs "a person with a neutral expression" and whatever else
matters — with a calibrated threshold.

*Check:* accuracy on a held-out set, in PyTorch, on a laptop.

**This is where the project lives or dies.** A 1.5M-param student is a weak
encoder; if it can't separate the queries in float32 on a workstation, no amount
of FPGA work will save it. Do not write a line of Verilog before this passes. If
it fails: shrink the ambition to a fixed binary classifier, or move to
[Tier 2](history.md#tier-3-vs-tier-2--revisited-after-the-netlist) — 8 MB of FPGA-side
PSRAM buys ~6 M params, at the price of putting the whole encoder in RTL.

**Result, 2026-07-30 — GO.** 1.40 M params, distilled on train2017 for 40 epochs
against CLIP ViT-B/16, evaluated on 1,000 held-out images. Queries are the COCO
classes under the prominent-object criterion (≥ 5% of frame positive, absent
negative, in-between excluded); 46 of the 80 are dropped for having fewer than 10
prominent positives or negatives in the eval set, leaving 34.

|  | queries @ AUC ≥ 0.80 | mean AUC | cos-to-teacher |
|---|---|---|---|
| teacher CLIP ViT-B/16 | 32 / 34 | 0.947 | 1.000 |
| student fp32 | 30 / 34 | 0.915 | 0.843 |
| student int8 (simulated) | 30 / 34 | 0.915 | 0.843 |

**Retention: 30/32 = 94% of the queries the teacher clears** — against a GO
threshold of 60%. The teacher's 0.947 mean is far above the 0.75 concept floor,
so the eval construction is sound and the ceiling is real.

⚠️ **Do not quote the 0.843 cos-to-teacher as "84% of the teacher."** CLIP
embeddings share a strong mean direction, and `model/runs/train2017/config.json`
records what that is worth:

```
constant_cosine : 0.733    # what a fixed vector scores against every target
best_cosine     : 0.843    # what the student scores
best_centered   : 0.339    # the student with the mean direction removed
```

A constant output — a student that has learned nothing — already scores 0.733.
The honest measure of what distillation bought is the **centered** figure,
**0.339**. The raw cosine is still the right *training* signal, and it is what
`distill.py` reports per epoch, but as a headline it flatters badly.

**This does not touch the gate.** AUC is the headline here precisely because it
is immune to this: a constant student assigns every image the same similarity to
every query and lands at AUC 0.5, so 0.915 is discrimination the student actually
has. The cos-to-teacher column is diagnostic, not the verdict.

**int8 is free.** Not "cheap" — free: the fp32 and int8 rows are identical to
three decimals, and per-query they differ by at most ±0.013 in either direction.
Post-training quantization with BN folding, per-output-channel symmetric weight
scales, and per-tensor activation scales taken at the **99.9th percentile over
512 calibration images** costs this student nothing measurable. M5 inherits a
solved problem — but it has to reproduce those exact scales, or "bit-exact
against the PyTorch int8 reference" is not a meaningful check. They are written
to `model/runs/train2017/quant.json` for precisely that reason.

**What the student loses is the hard queries, not the easy ones.** It tracks the
teacher within 0.01–0.03 AUC on prominent, well-framed objects (zebra 0.999,
airplane 0.985, toilet 0.994) and falls off where the teacher is already
struggling — `suitcase` 0.991 → 0.840 and `bottle` 0.888 → 0.755 are the two
genuine losses. Both are small objects that only just clear the 5% criterion.

**Two queries where the student *beats* the teacher, and they are the
interesting ones:** `person` 0.599 → 0.674 and `chair` 0.709 → 0.743. This is not
the student being better than CLIP. Those are the two queries where the teacher
is nearest chance, and "a photo of a person" is a poor prompt for *"is a person
prominent in frame"* — 350 of 1,000 images are positive, so the class is close to
a coin flip on this eval and the student has fit the dataset's own bias rather
than the semantics. **Do not read these as headroom.** If M9's real queries look
like `person`, the prompt ensemble needs work before the student does.

Reproduce with `uv run model/evaluate.py --split train2017`.

⚠️ **The artifacts M5 consumes are not in git.** `model/runs/train2017/` holds
`student.pt` (5.7 MB) and `quant.json` (the per-channel weight scales and
per-tensor activation scales), and `model/runs/` is gitignored along with the
41 GB of COCO and cached embeddings beside it. Regenerating them is a 40-epoch
train, not a script run — back them up before touching `model/`, or move the two
small files somewhere tracked when M5 starts.

### M5 — int8 reference on the MCU ✅ **PASSED, bit-exact on device**
Post-training quantize to int8, export a flat weight blob, run the whole encoder
on the RP2354A alone in plain C.
*Check:* embedding matches PyTorch int8 within tolerance; print cosine similarity
vs the reference. Also print latency (expect ~1.7 s) — this is the baseline the
FPGA has to beat. *(The latency half of that check missed by 19×, and fixing it
became [M5b](#m5b--tuned-mcu-baseline--3358-msframe-bit-exact-74-the-reference).
The correctness half passed exactly.)*

**The host half is done and passing.** `firmware/encoder.c` deliberately
includes no Pico SDK header, so the same source compiles as a macOS binary and
was validated before any strap was pulled:

```sh
uv run model/export.py --run train2017          # weights.bin + testvec.bin
cc -O2 -o /tmp/te firmware/test_encoder.c firmware/encoder.c -lm
/tmp/te model/runs/train2017/export
```

| check | result |
|---|---|
| encoder.c vs the numpy int8 golden | cosine 1.000000, `1-cos = 1.1e-16` |
| **bit-exact float32 outputs** | **2048 / 2048 (100%)** |
| encoder.c vs PyTorch fake-quant | 0.999993 worst of 4 |
| host latency (M-series, scalar C) | ~100 ms/frame |

The bit-exact row is the one that matters. Cosine 1.0 is also what a small
systematic scale error looks like; 2048/2048 identical floats is not, and it
reaches all the way back through 159 M MACs to say the int32 accumulators agree.
**That accumulator, in `fgx_conv()`, is the contract M6 must reproduce** — not
`quantize.py`, which is fake-quant and computes in float.

⚠️ The `vs fake-quant` column reproduces `export.py`'s four numbers digit for
digit. That is a stronger check than the golden column alone, because the two
comparisons come from different code in different languages.

#### On device, 2026-07-30

```sh
cmake --build firmware/build --target forgix_m5    # weights .incbin'd from model/runs/
# PRG-GND strap while plugging in, then:
cp -X firmware/build/forgix_m5.uf2 /Volumes/RP2350/
uv run host/mon.py --out /tmp/m5.log --idle 90     # DTR matters; see the script
```

| | result |
|---|---|
| worst cosine vs golden, 4 images | **1.000000** |
| **bit-exact float32 outputs** | **2048 / 2048 (100%)**, same as the host |
| latency, weights in PSRAM | *not run — U1 is fitted and does return its ID, but 18 bits out of frame, so XIP never comes up, [#10](history.md#verify-before-building)* |
| latency, weights from flash XIP | **31,798 ms/frame** (σ < 1 ms over 4 images) |
| effective rate | 5.0 MMAC/s = **30 cycles/MAC** at 150 MHz |

**The correctness result is the one worth having.** The Cortex-M33 reproduced
all 2048 floats *bit-identically* to numpy on macOS — same `lrintf`
round-half-to-even, same FPU rounding, same int32 accumulators. The integer
contract for M6 is now pinned on the actual target silicon, not just on a
laptop. `cos = 1.000000` on its own would not have shown that.

**The latency figure is real but is not the MCU baseline, and must not be
quoted as one.** 30 cycles/MAC is what naive scalar C costs: `fgx_conv()`'s
inner loop re-tests `unsigned_in`, bounds-checks both axes, and recomputes a
full index for every single tap. It was written to be *obviously* correct, and
the bit-exact row is what that bought.

So the M5 latency row does not settle "does the FPGA earn its keep"; it
brackets it. Against an untuned 31.8 s the FPGA looks like a 140× win, which
would be dishonest arithmetic. **[M5b](#m5b--tuned-mcu-baseline--3358-msframe-bit-exact-74-the-reference)
has since settled it: 3,358 ms/frame**, bit-exact with this row, which is the
number M6 has to be justified against.

Two footnotes that M5b later supplied. This row's 31,798 ms is a *pre-refactor*
figure — the identical arithmetic runs in 24,970 ms once `fgx_conv` stops being
inlined into `fgx_run`, which is a code-footprint effect in flash XIP and not an
arithmetic change. And the PSRAM row stayed "not run": U1's reply is 18 bits out
of frame ([#10](history.md#verify-before-building)), so `m5b.c` drops the PSRAM path
entirely and measures flash XIP only.

#### Per-layer breakdown, mean of 4 frames

This is the output M6 actually needs: it says *what to offload*, not merely
that offloading is worthwhile.

| layer | shape | ms/frame | share | MMAC |
|---|---|---|---|---|
| 0 | 128×128×3 → 32 | 1,533.9 | 4.8% | 3.5 |
| 1 | 64×64×32 → 64 | 3,796.2 | 11.9% | 18.9 |
| 2 | 32×32×64 → 64 | 7,464.8 | **23.5%** | 37.7 |
| 3 | 32×32×64 → 128 | 3,734.5 | 11.7% | 18.9 |
| 4 | 16×16×128 → 128 | 7,274.3 | **22.9%** | 37.7 |
| 5 | 16×16×128 → 192 | 2,739.1 | 8.6% | 14.2 |
| 6 | 8×8×192 → 192 | 3,912.5 | 12.3% | 21.2 |
| 7 | 8×8×192 → 256 | 1,330.0 | 4.2% | 7.1 |
| 8 | GAP + 256→512 | 12.7 | 0.0% | 0.1 |

Cost tracks MAC count and nothing else — which is the useful finding, because
it means **there is no cheap layer to skip and no expensive one to special-case**.
conv2 and conv4 alone are 46% of the frame, so a GEMM tile that handles just
those two shapes captures nearly half the work; but the flat profile also says
a tile that only handles *some* shapes leaves the rest at MCU speed, and
Amdahl's law then caps the whole thing. M6 should target the general 3×3 case.

Two footnotes on the numbers. The `pool + head` row at 12.7 ms is float, not
int8, and is why it is nearly free — it is also the one layer that will *not*
speed up on the FPGA. And conv0 costs 438 ms/MMAC against ~195 for everything
else, because `cin=3` gives only 27 taps per output to amortize the requantize
and store over; that overhead is an artefact of the naive kernel and should
mostly vanish in a tuned one.

### M5b — tuned MCU baseline ✅ *3,358 ms/frame, bit-exact, 7.4× the reference*
`fgx_conv()` rewritten as tiled im2col + a blocked int8 GEMM with a `SMLAD`
inner loop, in [`firmware/encoder_fast.c`](../firmware/encoder_fast.c), with
`encoder.c` left beside it untouched as the reference.

**This is the number M6/M7 must beat — not 31.8 s.**

#### On device, 2026-07-30

```sh
cmake --build firmware/build --target forgix_m5b   # both kernels in one binary
# PRG-GND strap while plugging in, then:
cp -X firmware/build/forgix_m5b.uf2 /Volumes/RP2350/
uv run host/mon.py --out /tmp/m5b.log --idle 120   # the reference frame alone is ~25 s
```

| kernel | ms/frame | cycles/MAC | vs reference | bit-exact vs golden |
|---|---|---|---|---|
| reference, `encoder.c` | 24,969.6 | 23.55 | 1.0× | 512 / 512 |
| im2col, portable C | 5,876.0 | 5.54 | 4.2× | **2048 / 2048** |
| **im2col + `SMLAD`** | **3,357.6** | **3.17** | **7.4×** | **2048 / 2048** |

σ < 1 ms over 4 images on both tuned paths; throughput 47.4 MMAC/s.

**All three kernels produce byte-identical float32 to numpy.** The tuned kernel
was not checked against the reference and hoped to be right — it is checked
against the same golden vectors `encoder.c` matched in M5, and separately
`memcmp`'d layer by layer against `fgx_conv_ref()` on the host, so a mismatch
names the layer instead of just failing at the embedding.

That bit-exactness is not luck, it is the design: the accumulator stays `int32`,
and int32 addition is associative and commutative, so **reordering the taps
cannot change the sum**. Nor can it overflow — the widest layer is conv6 at
K = 1728 taps, largest term 255·127, so |acc| ≤ 55,961,280, which is 38× inside
int32. The one subtlety is padding: `encoder.c` *skips* out-of-range taps while
im2col writes a literal `0`. Those agree even for the `unsigned_in` layers,
where code 0 does *not* dequantize to 0.0 — because the reference contributes no
term at all, and `0 · w = 0`. The zero point never enters the accumulator on
either side.

#### Per-layer, reference vs `SMLAD`

| layer | shape | MMAC | ref ms | dsp ms | speedup | dsp cycles/MAC |
|---|---|---|---|---|---|---|
| 0 | 128×128×3 → 32 | 3.5 | 1,425.4 | 179.4 | 7.9× | **7.69** |
| 1 | 64×64×32 → 64 | 18.9 | 2,946.4 | 404.5 | 7.3× | 3.21 |
| 2 | 32×32×64 → 64 | 37.7 | 5,790.8 | 796.3 | 7.3× | 3.17 |
| 3 | 32×32×64 → 128 | 18.9 | 2,897.6 | 383.7 | 7.6× | 3.05 |
| 4 | 16×16×128 → 128 | 37.7 | 5,655.3 | 747.5 | 7.6× | 2.97 |
| 5 | 16×16×128 → 192 | 14.2 | 2,132.0 | 276.6 | 7.7× | 2.92 |
| 6 | 8×8×192 → 192 | 21.2 | 3,062.7 | 411.7 | 7.4× | 2.91 |
| 7 | 8×8×192 → 256 | 7.1 | 1,046.7 | 145.2 | 7.2× | 3.07 |
| 8 | GAP + 256→512 | 0.1 | 12.7 | 12.7 | 1.0× | — |

**The speedup is flat at 7.2–7.9× across every conv shape**, which says the win
is structural rather than a lucky fit to one geometry — and it is the same
finding M5's profile gave, from the other side: cost tracks MAC count and
nothing else, so M6 still has to target the general 3×3 case.

conv0 is the one outlier, at 7.69 cycles/MAC against 2.9–3.2 everywhere else.
Its K is 27, so it is the only layer that (a) takes the scalar epilogue for the
`K % 4` tail and (b) has just 27 taps to amortize the requantize-and-store over.
M5 predicted this overhead was "an artefact of the naive kernel and should
mostly vanish in a tuned one" — **that was half right**: conv0 still tracks the
overall 7.9× speedup, but its *relative* inefficiency survives tuning. It is
2.4× off the pack either way.

#### Where the missing 1.5× went

The plan expected ~2 s and got 3.36 s. The inner loop retires 8 MACs per
iteration — two output columns × four taps — in roughly 12 instructions plus
loop overhead, so ~1.75 cycles/MAC is the floor for this shape; measured 3.17 is
about 1.8× off it. Weight bytes are not the constraint: at `FGX_TILE = 32` the
frame re-sweeps ~5.2 MB from flash XIP, which over 3.36 s is ~1.5 MB/s and
nowhere near binding. The gap is loop and addressing overhead, and deeper
unrolling plus more columns per weight unpack (CMSIS-NN blocks four) would
close some of it. **Not pursued: M5b's job is to stop M6 being justified against
a strawman, and a further 1.5× on the MCU side moves the FPGA's multiple by
less than the uncertainty already in the M6 estimate.**

#### The reference got 21% faster without changing a line of arithmetic

M5 measured `encoder.c` at 31,798 ms. The same source, same clock, same flags,
re-run in the M5b binary: **24,969.6 ms**. The difference is not measurement
noise and not the arithmetic — it is that M5b had to make `fgx_conv` non-static
to call it from the test harness:

| | `fgx_run` | hot kernel |
|---|---|---|
| M5, `fgx_conv` static | 1,086 B *(conv inlined into it)* | — |
| M5b, `fgx_conv_ref` extern | 156 B | 636 B |

GCC had been inlining the whole convolution into `fgx_run`, interleaving the
inner loop with pool and head code that never executes during a conv. On a part
that fetches instructions from flash through the XIP cache, shrinking the hot
loop's footprint to 636 B was worth 21% — from a refactor done for testability,
with no intent to optimize.

**The lesson is about method, not about GCC: ratios quoted across builds on this
board are not measurements.** This is exactly why `m5b.c` re-runs the reference
in the same boot rather than dividing by M5's logged number. Had it not, M5b
would have claimed 9.5× instead of the true 7.4× — a 28% overstatement, in our
own favour, from a completely reasonable-looking arithmetic shortcut.

#### Off-target first, because a strap is expensive

Every reflash of this board needs a physical `PRG`–`GND` strap, so the SMLAD
path was proven on the laptop before the strap was spent.
[`firmware/dsp_shim.h`](../firmware/dsp_shim.h) transcribes `SXTB16`, `UXTB16` and
`SMLAD` from the Armv8-M ARM, so `cc -DFGX_DSP_SHIM` compiles *the same source
lines* the M33 executes — the tap pairing, the loop bounds, the `K % 4` tail —
and checks them against numpy on macOS. Both host runs were green before the
board was touched:

| host run | layer mismatches | vs `encoder.c` | vs numpy |
|---|---|---|---|
| portable im2col | 0 *(8 convs × 4 images)* | 2048 / 2048 | 2048 / 2048 |
| `SMLAD` via `dsp_shim.h` | 0 *(8 convs × 4 images)* | 2048 / 2048 | 2048 / 2048 |

That left only "does the silicon match the ARM ARM" for the strap, which is a
much smaller question than "is my loop right" — and it is why the on-device run
passed first time. The DSP path is still a *runtime* bool rather than a second
build, for the same reason: if the intrinsics had been wrong, the portable-fast
row would still have returned a usable tuned baseline and localized the fault.

Worth doing regardless of the FPGA: the same im2col + tiling decomposition is
what M6 has to implement in RTL, so writing it in C first is how the data layout
gets debugged somewhere with a debugger.

### M5c — make U1 talk ⬜ *closed: the vendor never fitted U1 on purpose and never tested it*
`psram_detect_size()` returns 0 on a chip that is physically present
([#10](history.md#verify-before-building)), and it discards the eight bytes that would say
why. [`firmware/psram_probe.c`](../firmware/psram_probe.c) issues the `0x9F` by hand
and prints the raw response. Four revisions, each overturning the last.

> **Closed 2026-07-30, and not by us.** Adiuvo confirmed U1 was *accidentally
> assembled* — never intended for the production run, never tested, no
> known-good result on any board. The chip is healthy and its ID is correct;
> **why the reply is 18 bit-times late is still unknown and now stays that way.**
> Everything below stands as written — it is how the part was identified and how
> the host was exonerated — but read it knowing the answer was one email away the
> whole time. [Full reply and the retrospective.](bring-up-log.md#vendor-reply-2026-07-30--10-closed-u1-was-never-meant-to-be-there)

**The answer first, because the route to it was long.** U1 is an **AP Memory
APS1604M, 2 MB, healthy, and it has been answering correctly the whole time** —
it parses the command and returns the right ID. What is wrong is our *framing*:
the reply lands 18 bit-times later than the datasheet places it, so a
byte-aligned read of `rx[5]` cuts through the middle of a correct answer and
sees garbage.

```
reply   00 00 00 00 5e 0c 03 57 46 f6 9c 06
        -> 0D 5D at bit 50, then EID 1B DA 70
           MFID 0x0D = AP Memory
           KGD  0x5D = known good die
           EID  0x1B -> size_id 0 -> 2 MiB   <- exactly an APS1604M
```

`0x9F` plus a 24-bit address should put MFID at bit 32. It is at bit 50.

**And it is 50 on both boards.** Running the same probe on board #1 — a
different unit, a different die — returns a *different* byte string that decodes
the same way at the same offset:

```
                raw record            rotate left 18       MFID KGD  EID
board #2   5e 0c 03 57 46 f6 9c 06 -> 0d 5d 1b da 70 19 78 30   0D  5D  1b da 70 19 78 30
board #1   95 17 43 57 46 f6 9c 06 -> 0d 5d 1b da 70 1a 54 5d   0D  5D  1b da 70 1a 54 5d
                                                    └ die serial ┘
```

Two chips, same manufacturer, same density, **sequential die serials** — from
the same reel — and the identical +18. That retires "we got a bad part" as an
explanation, and it does something the single-board result could not: it makes
the finding *reportable*. A defect on one unit is a warranty conversation; the
same offset on two is a design question for the vendor.

The two rows also close off the last way the decode itself could have been
wrong. Both records have **exactly one rotation out of 64** that yields
`0D 5D` — verified by brute force, not by inspection — and on both boards that
rotation is 18. A 16-bit pattern matching by luck is conceivable; the same
unique rotation landing twice, on records that differ in their leading bytes, is
not.

**Rev 1** printed the bytes and declared `U1 SILENT on every variant`. **That
verdict was wrong** — a reporting bug, not a measurement one. The classifier
recognized only an exact `0D 5D` pair and counted everything else as absence, so
a third outcome was laundered into one of the two it expected. The raw row one
line above said the opposite: `00 00 00 00 5e 0c 03 57`, identical on all five
reads, device driving from exactly `rx[4]`. Neither an idle bus nor a held line;
noise does not repeat and an open circuit does not answer.

**Rev 2** chased the sampling point, since `DIRECT_CSR.RXDELAY` is two bits that
reset to 0 and `flash_do_cmd_cs()` never writes them — so every rev 1 transfer
ran at whatever the bootrom left, tuned for the flash die *inside the RP2354A
package* rather than a SOIC-8 across the PCB. It drove the QMI directly and swept
`CLKDIV` ∈ {6, 10, 20, 40, 100} — 25 MHz down to 1.5 MHz SCK — against `RXDELAY`
0–3, with a CS0 control row at every divisor.

All twenty CS1 rows came back byte-identical while every control row decoded.
**That kills the timing hypothesis outright**: across a 16× change in SCK, no
sampling point would have worked. A quad-lane `0xF5` and a quad `READ ID` killed
QPI alongside it.

Rev 2 also read twelve bytes where rev 1 read eight, and those four extra bytes
are what cracked it — once the search ran at *bit* granularity instead of byte.
Two revisions went looking for `0D 5D` on byte boundaries where it was never
going to be.

One accidental datum worth keeping: the quad read returned `9f 00 00 00 00 cc cc
cc…` — our own drive read back, then the bus settling once released. `0xCC` means
SD3 and SD2 high, SD1 and SD0 low, so **SD1 has no pull-up and floats low**,
which is why every CS1 read opens `00 00 00 00`.

**Rev 3** stops proposing mechanisms for "18" — a number that is neither byte nor
nibble aligned and is stable across a 16× clock change, which rules out the
statistical faults, since ringing and marginal edges do not reproduce bit for bit
— and measures four things instead:

| | test | what its result decides |
|---|---|---|
| **A** | 48-byte read | Does the reply repeat? A period is a mechanism; one hit in 384 bits means 18 is a real latency needing another explanation |
| **B** | opcode `0x00` / `0xFF` | **The control this diagnostic never had.** If `0D 5D` still appears under a dead opcode, U1 is not what is talking and rev 1–3 are void |
| **C** | address phase 0–4 bytes | Offset tracks `n_addr` → a fixed dummy count. Offset pinned to bit 50 → the part is counting clocks we are not issuing |
| **D** | reply captured in quad | **Which wire is U1 driving?** Each RX byte is two nibbles, one clock each, nibble bit *k* = lane *k*; deinterleave and search all four |

**D is the hypothesis of last resort and the only one that explains a stable
non-aligned offset.** If U1's `SO` lands anywhere other than the QMI's `SD1`,
what `SD1` sees is capacitive crosstalk from the real data trace — a ghost
carrying the aggressor's transitions with skew. That is recognizable, corrupted,
and **edge-rate** dependent rather than frequency dependent, which is exactly why
rev 2's sweep came back invariant.

**Rev 3 ran, and three of its four tests answered.**

**A** — the reply repeats with a period of exactly **64 bits = 8 bytes**, hit
after hit at bits 50/114/178/242/306, spacing dead constant. Eight bytes is
precisely one AP Memory READ ID record, so this is not a latency to be explained
away: the part is streaming its ID over and over, correctly, and we are cutting
the stream in the wrong place.

**B is clean.** Opcode `0x00` and `0xFF` return all zeros, no `0D 5D` at any bit
offset. U1 really is responding to `0x9F`, and rev 1–3 stand.

**D is dead — and it was the hypothesis of last resort.** The per-lane capture
puts the data squarely on **SD1**, with SD0 idle low and SD2/SD3 idle high. U1 is
driving the wire the QMI is reading. There is no crosstalk ghost.

**C proved nothing, and that was my error in the test, not a result.** `frame()`
sends the same total byte count regardless of `n_addr`; the parameter only
decides whether the address bytes carry `00` or `FF`. Every row clocked an
identical number of edges, so five identical rows were guaranteed by
construction. The "expected" column moves across the rows and the measurement
does not, which reads like a finding and is an artifact. Test C is still in the
source, still void; it is left in place with this note rather than quietly
deleted.

**Rev 4 asks the one question rev 3's controls could not.** Every CS1 read went
through `raw_xfer()` while the CS0 control row went through `flash_do_cmd_cs()` —
so chip and code path changed together, and a slip born in our own driver would
have been indistinguishable from one born in the chip. Rev 4 replaces that with
a 2×2 matrix: both code paths × both chip selects.

| | chip | frames at | want | off |
|---|---|---|---|---|
| `flash_do_cmd_cs` | CS0 flash | bit 8 | 8 | **+0** |
| `raw_xfer` | CS0 flash | bit 8 | 8 | **+0** |
| `flash_do_cmd_cs` | CS1 U1 | bit 50 | 32 | **+18** |
| `raw_xfer` | CS1 U1 | bit 50 | 32 | **+18** |

**The host is exonerated.** Both code paths frame the stacked flash at exactly
bit 8; both produce the identical +18 on U1. Same silicon, same clock, same pads,
same code — only the chip select differs. Whatever inserts the 18 bits belongs to
the CS1 transaction, and my earlier "host-side framing bug" conclusion was wrong.

```sh
cmake --build firmware/build --target forgix_psram_probe
# PRG-GND strap while plugging in, then:
cp -X firmware/build/forgix_psram_probe.uf2 /Volumes/RP2350/
uv run host/mon.py --out /tmp/m5c4.log
```

*Check:* **B first** — it can invalidate everything else. Given B is clean, the
2×2 matrix says whether the slip is ours, and D says whether the right wire is
being read. The CS0 control row still leads the output: if it stops decoding, the
harness is broken and nothing below it means anything, which is how [#1](history.md#verify-before-building) went
astray twice.

**Where this stops.** Chip healthy, wire correct, opcode correct, host clean,
offset reproducible across two units and invariant across a 16× clock sweep, and
the datasheet explicitly gives `9Fh` no dummy cycles — "similar to Fast Read, but
without the wait cycles". Every mechanism reachable from the MCU has been
eliminated, and 18 is neither byte- nor nibble-aligned, so no QMI register
reaches it either: `RXDELAY` moves the sampling point *within* a bit, dummy-cycle
settings move things in *whole bytes*. The next real instrument is a scope on
SCLK and SD1 at the package. **Recommendation: stop here, report it upstream, and
carry on** — see the M5b note on why PSRAM is headroom rather than a dependency.

⚠️ **Six dead ends worth recording so they are not re-walked.**

*U1 is DNP.* It is not — a SOIC-8 photographed on the underside, and now
positively identified from its own reply. Retracted once already; see
[#1](history.md#verify-before-building).

*The SDK cannot size an APS1604M.* It can. `psram_eid_to_size()` is `__weak` and
its comment says it "currently supports APS6404 and ISSI PSRAM", which is
misleading — read the ladder and `size_id == 0 → 2 MiB` is exactly a 16 Mbit
part. The measured `EID[0] = 0x1B` gives `size_id 0`, so the SDK would have sized
this chip correctly if the byte had ever reached it. The failure is upstream of
the decode, in the transaction.

*Rev 1's `0xF5` ruled out QPI mode.* It did not, and this one was my error in
the design of the test. `0xF5` means exit-QPI **only when sent in QPI**, four
bits per clock. Sent single-lane to a chip already in QPI it is two garbage
nibbles — so "the reply did not change" was the predicted result under *both*
hypotheses and separated neither. Rev 2 sends it as a real quad transfer via
`DIRECT_TX`'s `IWIDTH` field, and also tries a fully quad `READ ID`; QPI is
properly excluded now.

*The bytes were wrong.* They never were. Every byte U1 sent was correct; only
the boundaries we cut them on were not. Two revisions searched for `0D 5D` on
byte boundaries while it sat at bit 50 — the diagnostic's own framing assumption
was the thing under test, and it was never tested.

*It is a host-side framing bug.* Stated here and in `firmware/m5.c` on the
strength of rev 3, and disproven by rev 4's 2×2 matrix: both code paths frame
CS0 at bit 8 and both slip +18 on CS1, so the driver is not what is adding them.
The claim was never measured — it was the last standing description after the
chip had been cleared, and "not the chip" got written down as "the host" without
a control that could tell those apart. Rev 4 exists only because the control was
missing.

*We got a bad part.* Board #1's U1 is a physically different die with a
different serial, and it slips by exactly the same 18. Whatever this is, it is
common to both units.

### M6 — FPGA GEMM tile ✅ *bit-exact on silicon, 2048 of 2048 accumulators*
One int8 matmul tile on the T8: weights + activations in over the bus,
accumulators out.
*Check:* **bit-exact** against the M5 C reference. Report LE and multiplier
utilization from the Efinity report. **Both met.**

> **Verdict, 2026-07-30.** The tile returns **2,048 of 2,048 int32 accumulators
> exactly**, on a real conv2 block, at every link rate from 38 to 75 MHz. It
> fits the T8 with `P·Q = 2048` and **8/8 multipliers**. The link ran at
> **75 MHz**, above the 64.973 MHz static model.
>
> **And the milestone found something it was not looking for: the link is no
> longer the bottleneck — the MCU-side driver is.** Measured 0.92 MB/s against
> the 8.94 MB/s the wire is capable of. See
> [the throughput finding](#the-90-that-is-not-the-link) — it is the single most
> important input to M7 and it changed what M7 had to build.
> *(Since fixed in [M7a](#m7a--the-o1-driver--done-20482048-still-bit-exact-at-every-rate):
> 4.05 MB/s, 12 ms/block, still 2048/2048.)*

Two things M5b handed over, both of which held. The **decomposition was already
debugged**: [`encoder_fast.c`](../firmware/encoder_fast.c) tiles im2col at 32 output
pixels and blocks the GEMM two columns at a time, and that is the layout the RTL
reproduces. And the **target is 3.36 s/frame**, flat at ~3.0 cycles/MAC across
every conv shape except conv0 — so the tile targets the general 3×3 case.

#### Why the front end generates im2col in fabric

This is the decision the whole milestone turned on, and it was made on
arithmetic before any RTL existed. With `P` output positions × `Q` output
channels resident and the full `K = CIN·9` swept per block, forward traffic is

```
traffic = input_bytes · (COUT/Q)  +  weight_bytes · (N/P)
```

— weights reused across positions, activations across channels, and on-chip
accumulators cap `P·Q`. That is a hard ceiling, and it decides the shape:

| tile front end | best MACs / forward byte | MB/frame | frame @ 8.94 MB/s |
|---|---|---|---|
| fed pre-expanded im2col columns | 16 (P=Q=32) | 9.9 | ~1.06 s — only 3× the MCU |
| **im2col generated in fabric** | **~45** | **3.57** | **~400 ms** |

Every input byte appears in up to 9 im2col columns. Sending the expanded columns
spends that 9× on the scarcest resource in the system; generating them on chip
from a ~1.8 KB strip buffer spends it on BRAM reads, which are free. **Floor for
context:** 1.42 MB of weights must cross the link at least once per frame =
151 ms, irreducible in configuration A. 3.57 MB is within 2.6× of that.

Per-layer blocking, `P·Q = 2048` throughout — one parameterized block, not eight
special cases:

| layer | in | CIN→COUT | N | P | Q | forward KB |
|---|---|---|---|---|---|---|
| 0 | 128² | 3→32 | 4096 | — | 32 | **50** — weights are 864 B, so the matrix stays resident |
| 1 | 64² | 32→64 | 1024 | 64 | 32 | 557 |
| 2 | 32² | 64→64 | 1024 | 128 | 16 | 557 |
| 3 | 32² | 64→128 | 256 | 64 | 32 | 557 |
| 4 | 16² | 128→128 | 256 | 128 | 16 | 557 |
| 5 | 16² | 128→192 | 64 | 64 | 32 | 418 |
| 6 | 8² | 192→192 | 64 | 64 | 32 | 406 |
| 7 | 8² | 192→256 | 16 | 16 | 128 | 467 |

#### M6a — simulation

`make -C rtl tb_gemm` and `tb_gemm_link`, both **PASS, 10,560 accumulators
bit-exact**, the second driving `gemm_top` through `link_mosi`/`link_miso` one
bit per clock with framing and CRCs. Six cases chosen to cover both strides,
both input signednesses, `K = 27` (not a multiple of anything), two *consecutive*
position blocks so the halo rows are shared, a partial final block, `Q = 128`
with 192 passes, and an accumulator driven to 26 bits.

Two things worth recording from building it.

**The strip is poisoned with `0xa5`, not zeroed.** A correct tile never reads the
strip rows that fall outside the image — `im2col_feed` asserts `zero` and
`gemm_tile` substitutes a literal zero — so the fill value is a don't-care, and
that is precisely why it must not *be* zero. Mutating the row-bounds test in
`im2col_feed.v` was caught by **2 of 6 cases against a zero-filled strip, and by
all 6 against a poisoned one.**

**The golden values come from `fgx_conv_acc()`, never from a loop written for the
testbench.** `fgx_conv_ref()` computes accumulators but does not expose them, and
its requantized float cannot stand in: max |acc| = 55,961,280 needs 26 bits and
float32 has a 24-bit mantissa, so the comparison would be silently lossy. The
accumulator loop was extracted into `fgx_conv_acc()` with the arithmetic
untouched, exactly as M5b extracted `fgx_pool_head()`, and re-running the
existing unmodified `test_encoder` is what proves the extraction inert.

The layout itself lives in **one** file, [`firmware/gemm_block.c`](../firmware/gemm_block.c),
which `gen_gemm_vec.c` and `m6.c` both link. It did not start that way — `m6.c`
carried its own transcription — and the merge was done specifically so that a
`tb_gemm_link` PASS is evidence about the code that ships to the MCU. The
regenerated vectors were byte-identical across all six cases and both benches
still passed, which is what makes the refactor safe to claim as inert.

#### M6b — Efinity synthesis

`./rtl/build.sh gemm_top`, seed 2, default optimization level. **These are the
current builds, not M6b's**, because M7f parameterised `gemm_link.v` by `WIDTH`
and configuration A is rebuilt from that same source. Everything that fits is
unchanged from M6b to the digit — 2,461 LE, 1,743 registers, 21 RAMs, 8
multipliers — and the one figure that moved is Fmax: **M6b measured 64.973 MHz
and the parameterised link gives 62.449**, a 3.9% cost for code that is
`WIDTH=1`-identical in behaviour and proven so by `tb_gemm_link`. The board runs
bit-exact at 75 MHz either way, so it is a report change rather than a
regression, but it is the kind of thing that is worth naming rather than quietly
overwriting.

| metric | config A `gemm_top` | config C `gemm_top_wide` | budget |
|---|---|---|---|
| Logic Elements | 2,461 | 2,483 | / 7,384 — **33.3% / 33.6%** |
| — LUTs / adders | 1,412 | 1,445 | / 7,384 — 19.1% / 19.6% |
| — Registers | 1,743 | 1,741 | / 5,280 — 33.0% |
| **Memory Blocks** | **21** | **21** | **/ 24 — 87.5%** |
| **Multipliers** | **8** | **8** | **/ 8 — 100%** |
| Inputs / Outputs / Clocks | 3 / 5 / 2 | 5 / 4 / 2 | / 48, / 133, / 16 |
| `link_clk` Fmax | **62.449 MHz** | **58.630 MHz** | constraint 75 MHz — **not met, runs anyway** |
| `link_clk` setup slack | **−2.680 ns** | **−3.723 ns** | |
| `clk_32m` Fmax | 146.757 MHz | 163.319 MHz | constraint 32 MHz — met |

Per module, from `gemm_top.res.csv` — the split is the part worth reading:

| module | FFs | adders | LUTs | RAMs | mults |
|---|---|---|---|---|---|
| `u_tile` (GEMM tile) | 1,074 | 346 | 356 | **21** | **8** |
| ⌞ `u_feed` (im2col) | 77 | 95 | 137 | 0 | 0 |
| `u_link` (config A) | 565 | 17 | 435 | 0 | 0 |
| `u_link` (config C, `WIDTH=3`) | 563 | 17 | **473** | 0 | 0 |

**The link costs more LUTs than the entire MAC array**, which is the same fact the
timing report states a different way: the critical path is in `gemm_link`'s
framing logic in both builds, never in `u_tile`. Widening it to three data lines
cost **38 LUTs and 22 LEs** — 0.3% of the device — for 2.9× the forward
bandwidth. The `link_wide` standalone build is 43 LEs total (0.58%), so
essentially all of `u_link`'s cost is framing, CRC and the response state
machine rather than the width.

`P·Q = 2048` fits; no fallback to 1024 was needed. **Every reported critical
path is 100% routing delay, 0% logic delay** — at 13.333 ns the design affords
about three routing hops, and the delay is congestion-dependent switch-box
routing rather than wire length (one 4-hop net measured 4.676 ns against a
10-hop net's 1.810 ns).

**All six named `optimization_level` values are worse than the default:**
CONGESTION_1 61.440, CONGESTION_2 61.143, CONGESTION_3 60.096, TIMING_3 49.579,
against the default's 64.973. `seed=N` is the only knob that helps, and
placement noise alone is ±2.5 MHz. Recorded so the next person does not spend
the afternoon re-deriving it.

Missing the constraint by 14% did **not** get the constraint relaxed.
`gemm_top.sdc` says in its own comment that the honest response is to lower the
PIO clock and record the slower frame time — so M6c swept the clock instead of
picking one safe rate.

#### M6c — on board: 2048 of 2048, at every rate

One PRG–GND strap for `forgix_m6.uf2`; after that the bitstream arrives over USB
CDC into SRAM, so **every later RTL revision costs zero straps** — the single
biggest lever on iteration speed on this board.

Only the bitstream crosses USB. The MCU already has `weights.bin` in flash, so
it runs layers 0–1 with the reference kernel to make conv2's real input tensor,
lays out the strip and weight stream itself, and computes its own golden values.
Had the host built the strip, a passing run would have proved the FPGA agrees
with `m6.py` rather than with `encoder.c`.

```
  sys MHz   link MHz        ms   kB moved      MB/s  status   exact
  150.0     75.000          53         49      0.92  61     2048/2048
  132.0     66.000          61         49      0.80  61     2048/2048
  128.0     64.000          62         49      0.78  61     2048/2048
  120.0     60.000          67         49      0.73  61     2048/2048
  100.0     50.000          80         49      0.61  61     2048/2048
   76.0     38.000         105         49      0.46  61     2048/2048
```

Status `0x61` is clean throughout: command echo `6` (the closing NOP), `busy`,
`bad_frame` and `underrun` all clear.

**75 MHz worked, 15% above the 64.973 MHz model.** Static timing at the C2
corner is conservative and this is one specific die. But the sweep never found a
failure edge — 75 MHz is the ceiling `m6.c` can *generate*, not a measured
limit, because the PIO x2 program makes `link_clk = sys_clk/2` and `sys_clk` was
capped at 150 MHz. The real margin is unknown.

The rate knob is `sys_clk` and not the PIO divider, which is not obvious. A
fractional `clkdiv` looks like the natural fine adjustment and cannot work: PIO
implements it by stretching *some* state-machine cycles and not others, so the
**shortest** link period stays two sys clocks however large the divider — and
Fmax is a constraint on the shortest period. USB survives the changes because
RP2350's USB runs from PLL_USB.

#### The 90% that is not the link

**0.92 MB/s measured, against 8.94 MB/s of wire.** Exact byte accounting for one
conv2 block:

| transaction | count | bytes |
|---|---|---|
| NOP + CFG | 1 | 124 |
| ACT 1,588 + WGT 1,204 + RUN 2,528 | 8 | 42,560 |
| DRAIN | 1 | 8,244 |
| closing NOP | 1 | 52 |
| **total** | | **50,980** |

At 75 MHz, one bit per clock, that is **5.44 ms of wire time inside 53 ms
elapsed — the link is idle 90% of the time.** The rest is `gemm_host.c` running
on the MCU, and the dominant term is `find_preamble()`: it scans the capture bit
by bit from offset 0, and for RUN and DRAIN the response sits at the *end* of a
20,000–66,000-bit buffer. Roughly 400 k bit-iterations per block, against ~172 k
for the CRC and ~65 k for the drain cursor.

**This is the most consequential number in M6, and it is not a link problem.**
The ~400 ms/frame the blocking predicts assumes the link binds. It does not. If
driver overhead stays proportional to traffic — and both `find_preamble` and the
bit cursor are O(bytes) — then 3.57 MB/frame lands near **3,900 ms, slower than
the 3,358 ms MCU baseline**, and the fabric would be pointless.

It is fixable and the fix is structural rather than micro-optimization: **we
drive the clock, so the return path's byte offset is a constant for this board.**
One NOP at init can measure it, after which every response is indexed directly
instead of hunted, and payloads come out a word at a time instead of a bit at a
time. With a table-driven CRC alongside, most of the 90% should come back. It is
a `gemm_host.c` change, so it costs one strap — folded into M7's first flash
rather than spent now.

> **Resolved by [M7a](#m7a--the-o1-driver--done-20482048-still-bit-exact-at-every-rate):
> 12 ms/block, 874 ms/frame.** The measured split came out close to the estimate
> above — locate 26.32 ms of the 42, CRC and cursor most of the rest — but the
> proposed fix was wrong in one place: the response offset is *not* a single
> constant, because RUN waits on `busy` and so carries the sweep. It took a
> per-command-class hint, verified on every use, to cover all six commands.

**Stopping the clock stops the tile, and that is a feature.** `gemm_top` is
entirely synchronous to `link_clk`, so between transactions the FPGA is frozen
mid-state rather than racing the host. It is why the driver can decode a
response at leisure, in C, with no ready line and no timing requirement of any
kind — and why the only real sizing question is how many idle bytes to append to
a command, since **those bytes are the tile's compute time.** Per `(k, g)`
iteration the tile spends 1 cycle in `S_LOAD`, `P` in `S_SWEEP` and 5 in
`S_FLUSH`, so the budget is `K·QG·(P+6)` clocks: 19,296 for conv2, against the
20,176 the driver supplies after the command.

#### What M6 changes for M7

- **The tile is not the risk.** Bit-exact in simulation, bit-exact on silicon,
  fits with 8/8 multipliers and 21/24 memory blocks.
- **The driver is.** M7's first job is the O(1) response indexing above, and its
  ≤250 ms target should not be treated as reachable until that is measured
  rather than modelled. *(It was not: M7a fixed the driver, and M7c then costed
  the whole frame and [retired the target](#the-road-to-280-ms) — 250 ms is below
  the tile's 265 ms MAC floor, which no driver work can reach.)*
- **Configuration C is still on the table.** The RTL takes a `WIDTH` parameter
  exactly as `link_core.v` does, so 3 data bits is a rebuild plus the
  PIN2↔PIN17 jumper. But at 10% link utilization, widening the wire would buy
  nothing until the driver is fixed — which is an argument for fixing the driver
  first and possibly never needing the jumper.
- **`sys_clk` above 150 MHz is unexplored free speed.** RP2350 commonly runs
  well past it; every 2 MHz of `sys_clk` is 1 MHz of `link_clk`, and the silicon
  showed no sign of stress at the top of the sweep.

### M7 — Full inference on the FPGA ✅ *a whole frame runs bit-exact at 917 ms on three data lines, 3.76× the MCU*
MCU sequences all layers through the T8; non-GEMM ops stay on the MCU.
*Check:* bit-exact end-to-end embedding vs M5 — and now vs M5b too, which is
free: `encoder_fast.c` is already byte-identical to `encoder.c` layer by layer,
so either can serve as the golden reference. Print ms/frame.

**The ≤250 ms target is retired: it is below the tile's compute floor.**
159 MMAC ÷ 8 MACs/clock ÷ 75 MHz = **265 ms of MAC time that cannot overlap
anything**, because the MCU is the tile's only clock. Three facts pin that, and
all three are in-tree rather than assumed:

- **8 multipliers, all used.** `rtl/build/gemm_top.res.csv` reports `8(8)` in
  `u_tile`. The packing escape was already refuted at `gemm_tile.v:29-32`: an
  18×18 cannot hold two int8 products, because they are 16 bits wide and 18 bits
  cannot give 16 bits of field separation.
- **The tile's only clock is `link_clk`** (`gemm_tile.v:34-35`). Y2's 32 MHz into
  the T8's PLL on ball B4 exists, and drives the heartbeat and the LEDs.
- **The fabric already misses timing at 75 MHz.** `gemm_top.timing.rpt` gives
  Fmax **62.449 MHz** and setup slack **−2.680 ns**, and [M6c](#m6c--on-board-2048-of-2048-at-every-rate)
  ran bit-exact anyway; the wide build is worse still at **58.630 MHz** and
  −3.723 ns, and *it* ran bit-exact too. Clocking the tile faster is a
  timing-closure project, not an SDC edit — and [M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero)
  found the path to close is in `gemm_link`'s framing logic, not in the MAC
  array, which is not where anyone would have started looking.

**And the 874 ms figure M7a extrapolated to was a single-block extrapolation.**
It assumed every block looks like conv2. [M7c ran the whole
frame](#what-the-board-did) and measured **2,164 ms** — see [the road to
280 ms](#the-road-to-280-ms) below, where the traffic is computed by `make -C rtl
test_plan` rather than transcribed and came out byte-exact against the board.
[M7d](#what-the-board-did-1) then brought it to **1,481 ms**, and
[M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only) — which put the
second CPU core to work for the first time in the project — to **1,292 ms**, and
[M7f item 1](#m7f-1--the-drain-decode-and-what-one-fifo-cost) to **1,197 ms**, and
[item 3](#m7g-1--the-same-code-out-of-sram--1197--1140-ms) to **1,140 ms**, and the
jumper plus [M7g-2](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms)
to **975 ms**, and
[M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40)
to **917 ms**.

**M7 stops here, and M7h is why.** 314 of those 917 ms are RUN — the tile
computing, at 89% occupancy whenever it is clocked — and M7h removed 211 ms of CPU
for 58 ms of frame. Both remaining firmware items in the ladder below are smaller
than that and would convert at the same 40% or worse.

**And the RTL gap closed too.**
[M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)
was going to give the tile the T8's own clock so RUN stopped being wire at all,
for a projected ~470 ms. That projection was wrong — RUN's bytes are the tile's
cycle count, so removing them removes no time unless the tile then runs faster —
and when the tile was finally synthesized standalone on 2026-07-31 it measured
**70 MHz**, capped at Logic Level 0 by a memory block feeding a hard multiplier.
**917 ms is the floor for this board**, in firmware and in RTL alike.

So M7 was five pieces of work in a fixed order, each meaningless before the one
above it:

1. **M7a — make `gemm_host.c` O(1) per response.** ✅ *done, 53 → 12 ms/block*
2. **[M7c](#m7c--the-per-layer-sequencer--done-all-8-layers-bit-exact-2164-ms-per-frame) — the per-layer sequencer.**
   ✅ *done, 2,164 ms/frame, all 8 layers bit-exact.* Turned the projection into a
   measurement, and found 273 ms of MCU work nothing was counting.
3. **[M7d](#m7d--stop-serializing-the-cpu-against-the-wire--2164--1481-ms-firmware-only) — stop serializing
   the CPU against the wire.** ✅ *done, 1,481 ms/frame, still bit-exact.* The DMA
   CRC sniffer was the lever (268 → 5 ms); the overlap was worth 400 ms rather
   than the 918 the model promised, and why is [the finding](#what-the-board-did-1).
4. **[M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only) — use the other
   core.** ✅ *done, 1,292 ms/frame, still bit-exact.* The strip/weight build and
   `scatter()` moved onto a Cortex-M33 that had been in the bootrom wait loop for
   five milestones. Worth 193 ms rather than the projected 380, because most of
   that work was already hidden inside a DMA window and hidden work is free on
   either core — [the finding](#m7e--use-the-other-core--1485--1292-ms-firmware-only).
5. **[M7f](#m7f--move-less-and-move-it-narrower--1292--917-ms-and-the-jumper-took-two-milestones-to-pay) — move
   less, and move it narrower.** ✅ *done, 917 ms/frame, still
   bit-exact.* DRAIN's decode went to core 1 — M7e's measurement had made it the
   largest thing left on core 0 by 3× — and needed a second mode to collect,
   because one FIFO put it ahead of the job core 0 was actually waiting for:
   [the finding](#m7f-1--the-drain-decode-and-what-one-fifo-cost). Then the whole
   per-transaction path came off flash XIP, which is worth more with two cores
   than M5b's 21% was with one because both share a QSPI interface:
   [the finding](#m7g-1--the-same-code-out-of-sram--1197--1140-ms). Then the
   config-C jumper, which was last because it needs a soldering iron and before
   core 1 was last on principle, since a narrower wire is a smaller window. It
   shortened the wire 923 → 637 ms and the frame by nothing at all, because
   three data lines exposed a reference computation that had been
   correct-by-coincidence at width 1 since M6:
   [the jumper](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero) and
   [what it was hiding](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms),
   which took the frame to **975 ms**. Core 1 was then the floor at 762 ms against
   641 of wire, so
   [M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40)
   took 211 ms off it — a cache over the weight build, hitting its 43% ceiling
   exactly, plus a packer that had been round-tripping through the stack. **The
   frame took 58 of the 211**, which is the measurement that closes M7: core 1's
   work was inside the wire window all along, and what is left of the frame is
   314 ms of tile compute and 127 ms of queue latency.

Everything else M7 needs is already in hand: [`gemm_block.c`](../firmware/gemm_block.c)
generates the strip, weight stream and golden accumulators for an arbitrary
block and is the same code both testbenches passed against, so the sequencer is
a loop over `gb_spec_t` values rather than new layout work.

#### The road to 280 ms

> **The title is now a historical one, and so is most of the ladder below it.**
> 280 ms was the FPGA-side-PSRAM row, withdrawn on 2026-07-31 because no QSPI
> PSRAM breakout is buyable and this project cannot fabricate one. The ~470 ms row
> below it was withdrawn the same day, when the tile was synthesized standalone for
> the first time and measured 70 MHz — see
> [M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms).
> The heading stays because half the README links to it. **The reachable figures
> are now just two: 917 ms measured and bit-exact, and roughly 780 ms if the
> bit-exact float contract is given up** — and even that second one is a `max()`
> against core 1's 593 ms, not a sum.

The byte column here is **measured** — `make -C rtl test_plan` predicted 8.151 MB
and the board moved 8.151 MB, 0.0% apart, across eight layer shapes that share no
dimension. The wire and CPU columns are the model's, from M7a's per-byte rates
(wire 8.94 MB/s, build 0.118 µs/B, CRC 0.067 µs/B, decode 0.113 µs/B); [what M7c
actually measured](#what-the-board-did) is beside them, and three of the four
rates held to within 4%.

The frame, by component:

| component | MB | wire ms | CPU ms |
|---|---|---|---|
| ACT | 1.757 | 206 | `gb_strip()` 206 |
| WGT | 2.219 | 261 | `gb_weights()` 263 |
| RUN — **idle bytes; these are the tile computing** | 2.778 | 326 | — |
| DRAIN | 1.368 | 161 | decode 167 |
| framing (NOP + CFG) | 0.029 | 3 | — |
| outbound CRC | — | — | 267 |
| requantize + pool/head | — | — | ~43 |
| **total** | **8.151** | **957** | **946** |
| *board* | *8.151* | *918* | *1,246* |

174 blocks, 1,856 passes, and CPU and wire serialized completely in M7c —
2,164 ms measured; [M7d](#what-the-board-did-1) overlapped 683 ms of the CPU into
the wire and brought that to 1,481, and
[M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only) moved another 193 ms
onto the second core for 1,292. RUN is the load-bearing row: 2.778 MB clocked out is 298 ms of
`link_clk` at the measured rate, against the 265 ms MAC floor, i.e. **the tile is
89% busy whenever it is clocked at all** and the rest of the frame is the 620 ms
of wire that is *not* RUN. That ratio is the single most important number here:
it says the accelerator is not the problem.

**Where the model was wrong is more useful than where it was right.** It
undercounted by 304 ms, and 273 ms of that is two costs it does not model at all
— `gh_frame()`'s untimed staging `memcpy`/`memset` plus `scatter()`, and
`gw_locate()`. Both are MCU-side, both are in code this project owns, and neither
needs hardware to fix. See [what the board did](#what-the-board-did) for the full
accounting.

The ladder, each rung deleting one component. **The first six rows are
measured** ([M7c](#what-the-board-did), [M7d](#what-the-board-did-1),
[M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only) and
[M7f](#m7f-1--the-drain-decode-and-what-one-fifo-cost) on the board,
2026-07-31); the rest are projected from them. The `CPU` column is
*exposed* CPU — the part on the critical path, i.e. not inside a DMA window and,
from the core-1 row on, not on the other core either. The wire column reads 918 in
the first three rows and 923 below because those are different boots moving the
same 8.151 MB; within each boot every mode measured the same wire, which is the
comparison that counts:

| step | wire | exposed CPU | frame | nature |
|---|---|---|---|---|
| **M7c as measured** | **918** | **1,246** | **2,164** | *measured; fully serialized* |
| **+ CRC sniffer, dirty-mark staging ([M7d](#m7d--stop-serializing-the-cpu-against-the-wire--2164--1481-ms-firmware-only))** | **918** | **963** | **1,881** | *measured; still serialized* |
| **+ non-blocking DMA ([M7d](#m7d--stop-serializing-the-cpu-against-the-wire--2164--1481-ms-firmware-only))** | **918** | **563** | **1,481** | *measured* |
| **+ build and `scatter()` on core 1 ([M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only))** | **923** | **369** | **1,292** | *measured; two cores* |
| **+ DRAIN decode on core 1 ([M7f](#m7f-1--the-drain-decode-and-what-one-fifo-cost))** | **923** | **319** | **1,242** | *measured; 101 ms of it came back as stall* |
| **+ two priorities on core 1 ([M7f](#m7f-1--the-drain-decode-and-what-one-fifo-cost))** | **923** | **274** | **1,197** | *measured* |
| **+ the hot path in SRAM ([M7g-1](#m7g-1--the-same-code-out-of-sram--1197--1140-ms))** | **917** | **223** | **1,140** | *measured; the only cross-build row* |
| **+ config-C jumper ([M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero))** | **860** | **284** | **1,144** | *measured; **286 ms of wire, 0 ms of frame*** |
| **+ round the reference, split the hint slots, scan by words ([M7g-2](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms))** | **641** | **334** | **975** | *measured; the jumper finally pays* |
| **+ cache the weight build, unpick `gw_pack3()` ([M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40))** | **644** | **241** | **917** | *measured; 211 ms of CPU, 58 ms of frame* |
| *+ pre-interleaved weights in `weights.bin`* | *644* | *~230* | *~905* | *`export.py`; worth ~6 ms, and only ~6 — see below* |
| ~~*+ the tile on the T8's own clock (M10)*~~ | ~~*330*~~ | ~~*~114*~~ | ~~*~630*~~ | **withdrawn 2026-07-31 — [the tile measures 70 MHz standalone](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms); the wire never reaches 330** |
| ~~*+ the interleave, now that it pays (M10)*~~ | ~~*330*~~ | ~~*~114*~~ | ~~*~470*~~ | **withdrawn 2026-07-31 — depended on the row above** |
| *+ requantize in fabric* | *~531* | *~114* | *~780* | *RTL; gives up the bit-exact float contract, **and see the note*** |
| ~~*FPGA-side PSRAM*~~ | — | — | ~~*~280*~~ | **withdrawn 2026-07-31 — no buyable breakout, no way to fabricate one** |
| *pure MAC time* | | | *265* | *the floor, and now known to be immovable* |

**Three of the last five rows were withdrawn on one day, and two of them for the
same reason.** The PSRAM row died of procurement. The two M10 rows died of
arithmetic: they took RUN's 314 ms of idle bytes for transport that a second clock
would delete, when those bytes are the tile's own cycle count. `f_tile` was the
only thing that mattered and it had never been measured; measured, it is 70 MHz
against the 75 the board already runs at.

**The requantize row moved too, and in the same way.** It used to read ~350 ms
because it was stacked on top of M10's 330 ms wire. On the real 644 ms wire it
takes DRAIN from 153 to ~40 and the wire to ~531 — at which point **core 1's
593 ms is the constraint, not the wire**, and the frame lands near ~780 rather
than anywhere near 350. That is a `max()` where the ladder wants a sum, for the
fifth time; the ladder's columns still add, and the machine still does not.

From the core-1 row down, "exposed CPU" is what stays on core 0 no matter how many
workers there are — `gw_stage()`, `gw_locate()` and the sniffer — plus however
long core 0 spends *stalled waiting for core 1*. That stall term is the
interesting one and it has not behaved: 37 ms at M7e, then **138** the moment
DRAIN's decode joined the queue, then 94 once the queue was split by priority,
141 once core 1 became the binding constraint, and **127** after M7h cut core 1's
work by 170 ms. That last pair is the point: making core 1 do a third less work
bought 14 ms of stall. It has been the largest single item in the column since
M7f-1 and it is now the largest by a wider margin, which is the argument that most
of it is a queue-depth bound rather than a core-1-throughput one.

**The jumper row's 860 ms of wire is not 860 ms of wire**, and the discrepancy is
the whole of [M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero).
`wire (elapsed)` brackets the transaction, so in a pipelined mode it includes
whatever the CPU did inside the window; with `gw_locate()` costing 389 ms that
mode read 860 while the *serialized* mode of the same boot, moving the same
16.791 MB, read **637**. The row is left as measured because that is what the
frame paid, but the wire itself did what the model said it would from the first
run, and the row below is the same hardware once the CPU stopped hiding in it.

The jumper row no longer goes *up* in exposed CPU, and the reason it used to is
worth keeping: on one core a narrower wire is a smaller window, so shrinking the
wire from 918 to 619 pushed ~170 ms of build and deferred decode back into the
open, and the jumper had to come last on principle. Core 1 removes that coupling
entirely — the build no longer lives in the window — so the jumper was last only
because it needs a soldering iron. Hardware last, but for a duller reason.

**The jumper row was projected at ~900 ms and measured 1,144 — it saved 286 ms of
wire and nothing at all off the frame.** The projection was right about the wire
to within 19 ms and wrong about everything downstream of it, and the reason was
not in any column of this table: `gw_locate()`, the hint-guided search for the
response's bit offset, went from 27 ms a frame to **390**. Three data lines made
a *reference computation* wrong that had been accidentally correct at width 1, so
every response missed its hint and fell back to a linear scan.
[M7g-2](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms) fixed that and two
other things it exposed, and the frame went 1,144 → 975 — **so the jumper is worth
165 ms, collected one milestone after the one that installed it.**

**The prediction that core 1 becomes the floor held, and then M7h showed it was
the wrong thing to call a floor.** Core 1 was busy 762 ms against 641 ms of wire,
so
[M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40)
went after the largest thing on it — `gb_weights()`, the larger half of core 1's
460 ms of builds — and removed 43% of it with a cache, without touching
`export.py`. It worked exactly as costed: builds 502 → 318 ms serialized, the
cache serving precisely the 43% of bytes the arithmetic promised. **The frame took
58 ms of the 211.** Core 1's work was inside the wire window, so making it smaller
made core 1 idle sooner rather than making the frame shorter. The last row above
is what the `export.py` rewrite would still be worth after that, and it is now
~15 ms of CPU which would convert to about 6 ms of frame — for a format change to
`weights.bin` and a second copy of the blob in flash. **It should not be done at
917 ms**, and the condition under which that would have flipped was a wire short
enough to stop hiding core 1: at 330 ms of wire the same change is worth ~160 ms
of frame rather than 6. That condition was
[M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms),
and **M10 closed on 2026-07-31 without ever producing it** — the 330 ms wire was
an arithmetic error, and the real one stays near 612 even if the tile is given
its own clock. So the interleave is worth ~6 ms, not ~160, and it is not waiting
for anything. See [the ladder](#the-road-to-280-ms) for the withdrawn rows.

**The ~683 ms this table used to promise for M7e was wrong by about 340 ms**, and
so was M7d's ~978. Both assumed the frame becomes `max(wire, CPU)` with perfect
overlap on one core. It does not: `scatter()` runs between blocks with nothing on
the wire, DRAIN's decode cannot be deferred because the caller reads `got[]`, and
per *transaction* the build is larger than the window it is offered. See
[what M7d did](#what-the-board-did-1) for the 563 ms, itemized.

**And then the two-core row was wrong by 190 ms in the other direction** — ~1,100
projected against 1,292 measured — because it counted core 1's movable load as the
*total* build + scatter rather than the *exposed* part. Work already hidden inside
a DMA window is free whichever core runs it; only the overrun can be recovered.
**And then M7f's DRAIN row missed by 131 ms** — ~1,110 projected against 1,242
measured — for a reason no version of this model contains: the cost of *queueing
order*. Moving 157 ms of decode off core 0 gave back 101 as stall, because one
FIFO ran it ahead of the job core 0 was blocked on.

**And then the jumper row missed by 244 ms, in a way none of the others did.**
Every previous miss was the model mispricing something it *knew about*. This one
was right about the wire to within 3% and wrong about the frame by 27%, because
shortening the wire exposed a latent defect in code the model does not describe
at all — a truncating division that had been correct-by-coincidence at width 1
since M6. There is no version of this table that would have caught that.

That is now five consecutive milestones where the projection missed and the
measurement was worth more than the model — twice optimistic, twice pessimistic,
once right about its own columns and wrong about the total. The remaining rows
are built the same way, which is the reason to read them as estimates rather than
promises.

Eight of those rungs are findings rather than plans, and each is easy to get
backwards:

- **On two cores, flash XIP is a shared resource, so moving code to SRAM is worth
  more than it was on one.** ✅ *measured,
  [M7g-1](#m7g-1--the-same-code-out-of-sram--1197--1140-ms).* M5b measured 21% from
  nothing but `__not_in_flash_func`, single-core. With core 1 running the builds
  against core 0's wire, the same move is **2.5× more valuable than the
  single-core control in the same binary** — 14.6% against 5.8% — because a miss
  on either core stalls the other through the one QSPI interface. **The corollary
  is the part to remember:** the contention does not vanish, it relocates. A
  decode that was already in SRAM and was not touched got 5% *slower*, and the
  untimed sweep pass — where core 1 is idle — reads it identically in both
  builds, so the cause is SRAM bank arbitration replacing QSPI arbitration.
- **Moving work off the critical path is not the same as moving it off the
  core.** ✅ *measured, [M7f](#m7f-1--the-drain-decode-and-what-one-fifo-cost).*
  DRAIN's decode went to core 1 — 157 ms, correctly measured — and the frame fell
  59. The other 101 came back as core-0 stall with core 1 only 68% busy, because
  a single FIFO served block *b*'s decode and scatter, which nothing waits for,
  ahead of block *b+1*'s strip build, which core 0 blocks on by name. Two rings
  with strict priority recovered 44 of it; the last ~54 is a high-priority job
  waiting out the low-priority one already running, measured at 0.16 ms against
  0.21 predicted. **The lesson generalizes past this queue:** a second core
  changes *where* work runs, and only a statement about who waits for what
  changes *when*.
- **Half the CPU was idle for the first five milestones — and it was worth 193 ms,
  not 380.** ✅ *measured,
  [M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only).* The RP2354A has two
  Cortex-M33s and nothing in `firmware/` referenced `multicore_launch_core1` or
  linked `pico_multicore`; core 1 sat in the bootrom wait loop through M7d. It was
  missed that long because M7d named its own optimisation "pipelined", which made
  the frame look concurrent when it was one thread filling its own gaps. Moving
  the build and `scatter()` onto core 1 took 1,485 → 1,292 ms — but only half what
  was projected, because **work already hidden inside a DMA window costs nothing
  whichever core runs it.** Core 1's movable load was never the 914 ms of total
  build + scatter; it was the ~306 ms of that which M7d left *exposed*. Core 1
  now finishes a frame at 54% occupancy with core 0 stalled on it for 37 ms, so
  the next core-1 job is free and the argument for pre-interleaved weights —
  which existed to buy core 1 slack — is gone.
- **The CPU idles for all 918 ms of wire — and reclaiming it is worth 400 ms,
  not 918.** ✅ *measured, [M7d](#what-the-board-did-1).* `gh_xfer()` armed both
  DMA channels and then blocked; the DMA — *not* the CPU — is what clocks the
  tile, so the MCU spent every transfer spinning on a flag while holding work it
  could be doing. Splitting it into arm/wait and running the caller's build plus
  the previous response's decode in between took the frame from 1,881 to 1,481 ms
  in one boot. **`max(wire, CPU)` was the wrong model**, and three things break
  it: `scatter()` runs between blocks with nothing in flight, DRAIN's decode
  cannot be deferred because the caller reads `got[]`, and the window is per
  *transaction* — ACT's is ~106 µs against a ~124 µs strip build, so the build
  overruns it by 117 ms across the frame.
- **4.35 MB of every frame is `memset` to zero for nothing — and it costs ~7 ms.**
  ✅ *measured, and this bullet was the wrong size.* The staging path is real
  waste and it is now a dirty-mark rather than a `memset`, but 4.35 MB of 32-bit
  stores is ~1.1 M cycles at 150 MHz. The 245 ms M7c could not attribute was
  `scatter()` almost all the way down. Both were invisible for two milestones
  because they sit outside every `prof` window, which is also why M7a's block
  profile added up so neatly — that part of the finding stands, and staging has
  had its own window since.
- **The RP2350's DMA can hash while it moves.** ✅ *measured: 268 → 5 ms, the
  largest single win on the ladder.* The sniffer configuration is probed against
  `gw_crc()` at boot over all eight `(CRC32 | CRC32R) × out_rev × out_inv`
  combinations rather than read off the datasheet, because a wrong guess presents
  as `GH_ERR_RXCRC` on hardware and costs a strap to iterate on. It also needs
  its *own* channel: the wire channel carries header and idle tail, but
  `gemm_link`'s `rxcrc` covers the payload alone.
- **Config C is worth less than [the performance model](#appendix-the-design-time-performance-model)
  claims — and the prediction of *how much* less was right for the wire and
  useless for the frame.** ✅ *measured,
  [M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero) and
  [M7g-2](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms).* RUN's bytes
  are *idle* bytes: they supply clocks, and three data lines carry three bits per
  clock, so RUN's byte count triples while its 315 ms does not move at all —
  measured at 23.30 Mclk on one line and 23.24 on three, 0.3% apart. DRAIN cannot
  widen either. So config C accelerates ACT + WGT alone: **453 → 167 ms measured** against 168 projected, and the wire as a whole 923 → 637. **The frame kept none
  of it** until the next milestone, for a reason no version of this model
  contains — see the two bullets below.
- **A reference computation can be wrong for years and only present when you
  change the wire.** ✅ *measured,
  [M7g-2](#m7g-2--the-reference-the-hint-slots-and-the-word-loop--config-c-1144--975-ms).* `gw_locate()`
  predicts where a response's payload begins and searches near there. The
  prediction used a truncating division; the correct one rounds up. **At width 1
  floor and ceil are the same number**, so configuration A never once exposed it
  across five milestones of daily use, and at width 3 it lost `2*len mod 3` bits
  and moved with the payload's residue class. That is the entire reason the
  jumper measured 0 ms, and it cost a strap to find because nothing on the laptop
  could have: the bug is in the agreement between two implementations, and only
  one of them is C.
- **`gb_weights()` can be deleted, not optimised — parked twice, unparked by the
  jumper, and then only worth 40% of itself.** ✅ *measured,
  [M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40).*
  It spent ~456 ms/frame gathering weights into an order fixed at export time and
  never varying. [M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only)
  parked it because all of it runs on core 1 and core 1 finished the frame 46%
  idle — deleting work from a core that is already waiting does not shorten a
  frame — on the stated condition *"unpark it only if core 1 becomes the
  bottleneck"*. Config C made it one: core 1 hit 762 ms against 641 of wire.
  **And the fix turned out not to be the `export.py` rewrite the item always
  assumed**, because the streams *repeat*: `gp_blocks()` enumerates position
  inside channel, so consecutive blocks rebuild byte-identical weights. An 18 KB
  cache got **exactly** the 43% the arithmetic promised, for no format change at
  all, and the build fell 502 → 318 ms. **M7e's stated condition was the right
  test and it still gave the wrong answer** — core 1 was the bottleneck by
  occupancy and *not* by critical path, and only the second of those shortens a
  frame. 58 ms of the 211 arrived.
- **Once the frame is pipelined, a cost-model column is worth only the fraction
  of it on the critical path — and this model cannot say what that fraction is.**
  ✅ *three measurements, three different mechanisms.* M7e: 380 projected, 193
  measured, because the work moved between cores rather than disappearing. M7f-2:
  286 ms of wire removed, 0 ms of frame, because a latent bug consumed it exactly.
  M7h: 211 ms of CPU removed, 58 ms of frame, because the work was already off the
  critical path. Every column in the ladder above is still a real quantity that
  was really removed; the arrow from a column to the frame is the part that has
  been wrong three times running. **The ladder is kept as a record of what was
  predicted, not as a tool for predicting the next rung** — there is no next rung
  it could predict, which is the honest reason M7 ends here.

What the ladder does *not* contain is a way to reach 250 ms on this board, or 280,
or 470. Every firmware row leaves the 314 ms of RUN untouched, because RUN is the
MCU clocking the tile one bit at a time — and
[M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms),
which was the way around that wall, closed on 2026-07-31 with **both** of its
halves withdrawn: the PSRAM half for want of a buyable part, and the second-clock
half because the tile measures 70 MHz on its own against the 75 it is clocked at
today. **917 ms bit-exact is the floor for this board**, with ~780 available only
by giving up the float contract.

#### M7a — the O(1) driver ✅ *done; 2048/2048 still bit-exact at every rate*
`firmware/gemm_wire.c` — framing, CRC and response decode as a pure function of
a byte buffer, so [`test_gemm_wire.c`](../firmware/test_gemm_wire.c) checks it on
the laptop at every bit offset and every failure mode before anything costs a
strap. Both decode paths ship in one binary behind `gh_set_fast()` and run at
every rate in the same boot, because *ratios quoted across two builds of this
firmware are not measurements* — [M5b learned that
already](bring-up-log.md#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped).

Measured at 150 MHz sys / 75 MHz link, per conv2 block, 28 transactions:

| phase | M6 path | M7a path |
|---|---|---|
| wire (DMA wait) | 5.48 ms | 5.47 ms |
| locate preamble | 26.32 | **1.68** |
| CRC, outbound | 1.45 | 1.45 |
| decode + inbound CRC | 6.27 | **0.95** |
| build (`gb_strip`+`gb_weights`) | 2.50 | 2.53 |
| **total** | **42 ms** | **12 ms** |

Three things worth stating precisely, because each is easy to overstate:

- **The "M6 path" column is not quite M6.** `gh_frame()` computes the outbound
  CRC before it branches, so both paths already use the table; that column is
  M6's `find_preamble()` and `cur_byte()` with M7a's TX CRC. It is why it reads
  42 ms where M6 itself measured 53. The 11 ms gap *is* the CRC table alone.
- **12.08 ms of phases against 12 ms elapsed.** Nothing is unaccounted for, which
  is the claim the table is actually making. The wire also came in at 5.47 ms
  against M6's 5.44 ms prediction — the one number that was already known.
- **Locate's residual is calibration, not steady state.** 24 hits cost ~900 bit
  operations, unmeasurable; the 2 misses each fall back to a full scan, and the
  first RUN sits ~20,000 bits into its capture. `gh_rate_changed()` only fires on
  a rate change, so a 72.9-block frame pays it once — putting the steady-state
  block near 10.4 ms and the frame near 758 ms. *That is arithmetic, not a
  measurement.* The measured extrapolation is **874 ms**, against 3,358 ms for
  the MCU baseline.

**The correction this milestone owes.** The old comment at `gemm_host.c:74-78`
argued a CRC table was not worth building "beside the 16 K link clocks the same
payload spends on the wire." That is true per byte *on the wire* and false in
elapsed time, because the CPU and the wire never overlap — the MCU is the FPGA's
only clock, so the tile is frozen for the whole decode. The table is 1 KB and
saved 11 ms a block.

**What the profile says binds next**, in order: the wire itself (5.47 ms, 45%),
then `gb_strip()`/`gb_weights()` at 2.53 ms — which is not driver cost at all and
overlaps the DMA in principle. Parked and now scheduled by evidence rather than
guess: requantising in fabric to shrink DRAIN 4×, the RP2350 DMA CRC sniffer, and
`link_narrow_x4` at sys 300 MHz for a 2× faster CPU at the same 75 MHz link.

#### M7c — the per-layer sequencer ✅ *done; all 8 layers bit-exact, 2,164 ms per frame*

All eight layers through the tile, bit-exact against `encoder_fast.c` layer by
layer, and **a measured frame time** to replace every projection above.

**The blocking table is computed, not hardcoded** —
[`gemm_plan.c`](../firmware/gemm_plan.c), Pico-free like `gemm_block.c` and
`gemm_wire.c`. Hand-drafting the eight rows produced two errors from
misremembered channel counts, and the table is not a matter of taste anyway:
summing what a layer sends gives

```
WGT   = (N/P) · COUT · CIN · 9                 -> wants P large
ACT   = (N/P) · (COUT/Q) · CIN · SROWS · W     -> wants P·Q large, Q more
DRAIN = N · COUT · 4                           -> constant, no knob
```

subject to `P·QG ≤ 256`, so `P` and `Q` compete for one budget and the answer is
a minimum rather than "as big as it fits". A brute-force sweep of the few dozen
legal `(P, QG, Cb)` triples per layer costs microseconds and cannot disagree with
the cost model the way a remembered table can. Independent check that it works:
for conv2 it picks **P=128, Q=16, Cb=8** — byte for byte the blocking chosen by
hand for M6 and run on the board in M6c. That was not seeded.

| L | shape | s | P | Q | Cb | blocks | passes | MB | wire ms | cpu ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 128×128×3 → 32 | 2 | 64 | 32 | 3 | 64 | 1 | 0.708 | 83 | 83 |
| 1 | 64×64×32 → 64 | 2 | 64 | 32 | 4 | 32 | 8 | 1.212 | 142 | 146 |
| 2 | 32×32×64 → 64 | 1 | 128 | 16 | 8 | 32 | 8 | 1.556 | 183 | 158 |
| 3 | 32×32×64 → 128 | 2 | 64 | 32 | 4 | 16 | 16 | 1.052 | 124 | 125 |
| 4 | 16×16×128 → 128 | 1 | 128 | 16 | 8 | 16 | 16 | 1.365 | 160 | 131 |
| 5 | 16×16×128 → 192 | 2 | 64 | 32 | 4 | 6 | 32 | 0.729 | 86 | 86 |
| 6 | 8×8×192 → 192 | 1 | 64 | 32 | 6 | 6 | 32 | 0.839 | 99 | 85 |
| 7 | 8×8×192 → 256 | 2 | 16 | 128 | 1 | 2 | 192 | 0.690 | 81 | 90 |
| | | | | | | **174** | **1,856** | **8.151** | **957** | **903** |

[`test_gemm_plan.c`](../firmware/test_gemm_plan.c) settles all of it on the laptop —
same argument as [off-target first, because a strap is
expensive](#off-target-first-because-a-strap-is-expensive). It asserts three
things against the real `weights.bin`, in increasing order of how badly they fail
on hardware:

1. every chosen block is one `gb_geom()` accepts;
2. **the blocks tile each output tensor exactly once.** This is the one property
   a sequencer can get wrong that per-block bit-exactness will *not* catch —
   every block can return all 2,048 of its accumulators correctly while the set
   of them misses a corner or writes one twice. It marks every `(oc, oy, ox)` the
   drain order would write and asserts no holes and no double-writes;
3. the cost model reproduces M6c's measured transaction lengths, so the framing
   constants `gemm_plan.c` restates from `gemm_host.c` are checked rather than
   trusted.

**One shared requantize.** `fgx_conv_acc()` stops at the accumulator — that is
the FPGA's contract — so the sequencer needs the epilogue on its own, and it
already existed twice (`fgx_conv_ref()`, plus a copy in `encoder_fast.c` marked
*"verbatim"*). Now `fgx_requant()` / `fgx_code()` in `encoder.h`, `static inline`
so the generated code and M5's 31,798 ms and M5b's 3,358 ms are untouched. Proof
it is inert: `test_encoder`, `test_encoder_fast`, `tb_gemm` and `tb_gemm_link`
all re-run **unmodified** and still bit-exact — 10,560 accumulators each,
straight into the tile and over the wire.

##### What the board did

`forgix_m7`, one strap, 2026-07-31. **PASS — all 8 layers bit-exact, 512/512
embedding floats exact, 2,164 ms/frame** against 3,507 ms for `encoder_fast` on
the same boot, so **1.62× the MCU**. The blocking `gp_choose()` picked on the
board is the table above, row for row.

| L | blocks | passes | ms | build ms | wire ms | crc |
|---|---|---|---|---|---|---|
| 0 | 64 | 64 | 240 | 19.1 | 79.5 | match |
| 1 | 32 | 256 | 317 | 61.8 | 136.4 | match |
| 2 | 32 | 256 | 368 | 69.5 | 174.7 | match |
| 3 | 16 | 256 | 277 | 73.6 | 118.6 | match |
| 4 | 16 | 256 | 325 | 83.1 | 153.5 | match |
| 5 | 6 | 192 | 195 | 62.6 | 82.2 | match |
| 6 | 6 | 192 | 216 | 71.0 | 94.6 | match |
| 7 | 2 | 384 | 213 | 85.2 | 78.5 | match |
| | **174** | **1,856** | **2,151** | **526** | **918** | + 13 ms pool/head |

**The traffic model is exact and the rates hold.** That was the question the
milestone existed to answer, and the answer is yes on both counts:

| | measured | assumed | |
|---|---|---|---|
| bytes moved | **8.151 MB** | 8.151 MB | **+0.0%** |
| wire | 107.4 ns/B | 112 | −4.1% |
| outbound CRC | 67.3 ns/B | 67 | +0.4% |
| response decode | 111.8 ns/B | 113 | −1.1% |
| strip + weight build | 132.1 ns/B | 118 | **+11.9%** |

Byte-for-byte agreement across eight shapes that share no dimension is not a
coincidence, and it means every *byte* count in the ladder above stands. Three of
the four rates stand too. So does the [265 ms floor](#m7--full-inference-on-the-fpga--a-whole-frame-runs-bit-exact-at-917-ms-on-three-data-lines-376-the-mcu):
RUN clocked 2.778 MB, which at the measured rate is 298 ms of `link_clk` against
265 ms of pure MAC — the tile is **89% busy** whenever it is being clocked at all.

**And yet the frame is 2,164 ms, not 1,860.** The +304 ms is fully accounted for,
which matters more than its size — an unexplained 16% would put every projection
in doubt:

| | ms | vs model |
|---|---|---|
| wire | 918 | −39 |
| strip + weight build | 526 | +57 |
| outbound CRC | 268 | +1 |
| response decode | 166 | −1 |
| `gw_locate()` | 28 | **+28, not in the model at all** |
| frame staging + `scatter()` | 245 | **+245, not in the model at all** |
| pool + head | 13 | +13 |
| | | **+304** |

**The two missing terms are the finding, and one of them is embarrassing.**
`gh_frame()` stages every transaction at `gemm_host.c:186-188` — `gw_hdr()`, a
`memcpy` of the payload, then a `memset` of the tail — and all three sit
*outside* every `prof` window, so M7a never saw them and neither did
`gemm_plan.c`. That `memset` zeroes **4.35 MB a frame**, and 2.778 MB of it is
RUN's idle tail: bytes that are zero, that stay zero, and that the tile reads as
*clock* rather than as data. Rewriting the same zeros 1,856 times is pure waste
and deleting it needs no new hardware, no jumper and no re-export. The rest of
the 245 ms is `scatter()`, the requantize epilogue, which does two integer
divisions and three multiplies per output element across 356,352 of them —
also mine, also fixable. Splitting the 245 ms between them is [M7d](#m7d--stop-serializing-the-cpu-against-the-wire--2164--1481-ms-firmware-only)'s
first job, because it has to touch the staging path anyway.

> **M7d split it, and this paragraph had the emphasis backwards.** Staging is
> 45 ms of the 245 — the `memcpy`, mostly; 4.35 MB of 32-bit stores is only
> ~7 ms at 150 MHz, which was arithmetic available before the strap. The other
> ~200 ms is `scatter()`, and it did not shrink when the divides came out.
> Left as written because the correction is more useful than the claim.

`gw_locate()`'s 28 ms is 348 misses in 6,264 transactions — 5.6%, where M7a saw
2 in 28. There are only two hint slots, `HINT_RUN` and `HINT_LEN`, and a frame
walks eight layer shapes through them, so the non-RUN slot thrashes. It is 1.3%
of the frame and is not worth chasing; it is recorded because *it was not in the
model*, which is the same defect as the 245 ms and only looks smaller.

**What this changes about the ladder: the CPU is now the binding constraint, not
the wire.** 1,246 ms of CPU against 918 ms of wire, where the model had them
neck and neck at 903 and 957. Every rung below moves, and the DMA CRC sniffer
stops being a bonus and becomes load-bearing — without it, overlapping the CPU
against the wire leaves the CPU still on top.

#### M7d — stop serializing the CPU against the wire ✅ *2,164 → 1,481 ms, firmware only*

Reordered by [what M7c measured](#what-the-board-did) rather than by what the
model predicted: the CPU is 1,246 ms against 918 ms of wire, so overlapping alone
lands at 1,246 and the CRC sniffer is what makes the milestone.

0. **Stop rewriting 4.35 MB of zeros.** `gh_frame()` `memset`s every transaction's
   idle tail, 2.778 MB of which is RUN's sweep budget — zeros that stay zero and
   that the tile consumes as *clock*. Zero `txb` once, keep a high-water mark, and
   the memset disappears. Cheapest item on the whole ladder and it was invisible
   until M7c, because it sits outside every `prof` window.
1. **Non-blocking `gh_xfer()`.** Arm the DMA, return, and let the caller build
   the *next* transaction while the current one is on the wire. Double-buffer the
   staging (~8 KB against the ~80 KB of heap `forgix_m7` leaves free — its bss is
   426 KB of the 520). The frame becomes `max(wire, CPU)`.
2. **DMA CRC sniffer.** The RP2350's DMA has a hardware CRC-32 accumulator that
   can snoop a channel in flight, which retires the **268 ms measured** of
   outbound hashing for the cost of configuring a sniff — the CRC is computed by
   the same DMA that is already moving the bytes. Promoted from optional: without
   it, `max(918, 1246)` is still 1,246 ms and item 1 buys nothing.
3. **Hoist `scatter()`'s address arithmetic.** Two integer divides and three
   multiplies per output element, 356,352 times a frame. Walking the row instead
   of recomputing `pos / OW` is a loop rewrite, not an algorithm.

*Check:* still 2048/2048 per block and still bit-exact end to end; the phase
table shows build and CRC overlapped rather than merely faster, and **it accounts
for the whole frame** — M7c's 245 ms of unattributed time is the defect this
milestone must not repeat, so the staging path gets a `prof` window of its own.

##### What the board did

`forgix_m7`, one strap, 2026-07-31. **PASS — all 8 layers bit-exact, 512/512
embedding floats exact, 1,481 ms/frame** against 3,487 ms for `encoder_fast` on
the same boot, so **2.35× the MCU** and **1.46× M7c**.

The harness now runs the whole frame **twice in one boot**, serialized then
pipelined, for the reason
[M5b learned](bring-up-log.md#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped):
ratios quoted across two builds of this firmware are not measurements. Both runs
execute identical work in an identical order — `gh_set_pipelined()` only decides
whether the CPU's half of it goes inside the DMA window or after it. Both were
bit-exact, and the serialized run reproduced M7c's 918 ms of wire **to the
millisecond**, which is what makes it a usable control.

| L | blocks | passes | serialized ms | pipelined ms | build ms | crc |
|---|---|---|---|---|---|---|
| 0 | 64 | 64 | 220 | 218 | 20.0 | match |
| 1 | 32 | 256 | 272 | 214 | 63.3 | match |
| 2 | 32 | 256 | 318 | 254 | 71.2 | match |
| 3 | 16 | 256 | 237 | 175 | 75.2 | match |
| 4 | 16 | 256 | 281 | 216 | 84.0 | match |
| 5 | 6 | 192 | 167 | 119 | 63.0 | match |
| 6 | 6 | 192 | 189 | 142 | 71.5 | match |
| 7 | 2 | 384 | 185 | 130 | 86.9 | match |
| | **174** | **1,856** | **1,881** | **1,481** | **531** | + pool/head |

**The CRC sniffer is the whole milestone: 268 → 5 ms.** The RP2350's DMA sniffer
retired 3.797 MB of outbound hashing at 1.2 ns/B, against 67.3 ns/B in software —
a 54× rate, and the largest single win on the ladder so far. Which of the eight
`(CRC32 | CRC32R) × out_rev × out_inv` settings reproduces `gemm_link.v`'s
reflected CRC-32 is ambiguous in the datasheet and a wrong guess costs a strap, so
`crc_probe()` tries all eight at boot against `gw_crc()` on two buffers of 259 and
61 bytes — neither a multiple of four, so a mode that only agrees on word
boundaries cannot pass — and falls back to software if none matches. The board
printed `calc=1 out_rev=1 out_inv=1`, i.e. **CRC32R with both output transforms
on**, and the run is self-describing about it.

The sniffer cannot simply snoop the wire channel, which is the non-obvious part:
that channel carries header + payload + idle tail, but `gemm_link`'s `rxcrc`
covers the **payload alone**. So the CRC gets a third DMA channel doing a
byte-wise transfer of just the payload into a one-byte sink, started before the
wire DMA and read after it — concurrent, not merely faster.

**Pipelining bought 1,881 → 1,481 ms, 1.27×, same boot.** That is real and it is
also well short of the `max(wire, CPU)` the model promised. Where the 563 ms of
CPU that is still *outside* the wire went:

| | ms | why it is not in a window |
|---|---|---|
| `scatter()` + pool/head | 189 | runs between blocks, after `gh_drain()` — no transaction is in flight |
| DRAIN decode + `gw_locate()` | ~158 | the caller reads `got[]`, so this one response cannot be deferred |
| build overrun | 117 | 531 ms of build offered ~918 ms of window, but per *transaction*: ACT's is ~106 µs against a ~124 µs strip |
| pass-0 build | 49 | the first pass of each block has no earlier window to hide in |
| staging | 45 | between the previous wait and the next arm, by construction |
| outbound CRC | 5 | what is left of the 268 |
| | **563** | frame = 918 wire + 563 |

The wire's *elapsed* time is what makes the overrun visible: 918 ms serialized,
1,035 ms pipelined, for byte-identical traffic. Those 117 ms are the DMA finishing
while the CPU is still building.

**Two of the four items were worth almost nothing, and saying so is the point.**
Splitting the frame delta:

| item | expected | measured |
|---|---|---|
| 2. DMA CRC sniffer | −268 | **−263** |
| 0. stop rewriting 4.35 MB of zeros | *"cheapest item on the ladder"* | **−25, with item 3** |
| 3. hoist `scatter()`'s address arithmetic | part of 245 ms | **↑** |
| 1. non-blocking `gh_xfer()` | −918 | **−400** |

Items 0 and 3 together moved the frame by ~25 ms, because **M7c's 245 ms of
unattributed time was `scatter()`, not the `memset`** — and I attributed it the
other way round. 4.35 MB of 32-bit stores is ~1.1 M cycles, ~7 ms at 150 MHz; the
whole staging path measures 45 ms including the 3.8 MB payload `memcpy`. So the
memset was never 150 ms and the arithmetic was there to be done before the strap
was spent. Hoisting the divides out of `scatter()` did not help either: at 189 ms
across 356,352 output elements it is ~74 cycles each, which is `fgx_requant()`'s
float multiply, rounding and clamp — not two divides. **`scatter()` is now the
largest non-wire item in the frame**, and it inherits M7e — which moved it onto
core 1 wholesale, for 113 of that 189.

The staging rewrite was still worth keeping for a reason that is not speed: the
board moved **8.151 MB, +0.0% against the model**, identical to M7c. A dirty-mark
that under-clears would show up here as a byte the tile reads, and in an idle tail
a stray non-zero byte is two bytes away from being a frame marker. `test_stage()`
in `test_gemm_wire.c` checks it against a model that `memset`s everything, over
4,000 randomized `(len, n)` sequences, on the laptop.

`gw_locate()`'s miss rate did not improve — 696 in 12,528, the same 5.6% M7c saw,
for the same reason: two hint slots, eight layer shapes.

#### M7e — use the other core ✅ *1,485 → 1,292 ms, firmware only*

Reordered again, and this time by something the ladder had never accounted for:
**there is a second Cortex-M33 on this part and nothing has ever run on it.**
`grep` finds no `multicore_launch_core1`, no `pico_multicore` in any
`target_link_libraries`, and no SIO-FIFO use anywhere in `firmware/`; the only
core-1 artifact in `forgix_m7.elf.map` is `__StackOneBottom`, which the SDK's
linker script reserves whether you use it or not. Core 1 has sat in the bootrom
wait loop for every measurement in this README. M7d's "pipelined" mode means *CPU
inside the DMA window*, not two threads.

`firmware/worker.c` is a single-producer/single-consumer job ring — core 0 posts,
core 1 runs, `__dmb()` pairs on both sides, tickets to wait on. `firmware/m7.c`
now runs the same frame **four times in one boot**, each mode adding one step, so
every ratio below is same-binary and same-bitstream. That mattered more here than
it has anywhere else in this project: a concurrency bug is visible or not
depending on timing, and two builds differ in exactly that.

| mode | frame | Δ | wire | exposed CPU |
|---|---|---|---|---|
| serialized (M7c) | 1,886 | — | 923 | 963 |
| pipelined, one core (M7d) | 1,485 | −401 | 923 | 562 |
| + build on core 1 | 1,405 | −80 | 923 | 482 |
| **+ scatter on core 1** | **1,292** | **−113** | 923 | **369** |

**1,292 ms, 2.71× the MCU's 3,506 ms this boot.** Bit-exact in all four modes —
eight layer CRC32s and 512/512 embedding floats — and then all 174 blocks'
accumulators swept against `gb_golden()` as an untimed fifth pass in the same
boot, which is the check that a dual-core run being right once does not satisfy.

**But the projection above was wrong, and how it was wrong is the finding.** It
predicted ~1,100 ms from core 1 alone, core 1 pinned at 99.6% utilization, and
~140 ms of core-0 stall. Measured: −193 ms total, core 1 at **54%**, and **37 ms**
of stall. One mistake produced all three. The table above computes core 1's
movable load as the *total* build + scatter, 914 ms — but M7d had already hidden
most of the build inside the DMA window, and its own profile said so: only 117 ms
of build *overran*. **Work already hidden inside a DMA wait costs nothing whichever
core runs it.** The movable-*and*-exposed figure was ~117 + 189 ≈ 306 ms, and core
1 recovered 193 of it. Re-deriving from total work looked more rigorous than M7d's
exposed-time accounting and was simply a worse model of the same machine.

Three plan changes fall out, and each reverses something written above:

- **Core 1 is idle 46% of the time, so pre-interleaved weights is now worth
  approximately nothing.** Its entire justification was raising core 1's slack
  from 0.4% to 33%; slack is not the scarce resource. Parked, with regret — it is
  still the most elegant item on the list.
- **The third strip buffer is not needed.** 37 ms of stall against a predicted
  140. Instrumenting `w1_stall_us()` rather than pre-optimizing the queue was the
  right call, and it was made on the precedent that this exact class of model had
  already been 340 ms wrong once.
- **DRAIN's decode — explicitly scoped *out* of this milestone as too invasive —
  is now the largest CPU item on core 0 by 3×.** Promoted to first in M7f.

The reason it is exposed at all is one line: `gemm_host.c:416` defers a response's
decode into the next transaction's window only when `!out && !nwords &&
!status_out`. DRAIN is the only command whose caller reads a payload, so it is the
only one that cannot defer — every *other* decode in the frame is already free.
Core 0's remaining 369 ms is DRAIN decode 168, `gw_stage()` 50, `gw_locate()` 29,
outbound CRC 9, stall 37, and ~76 of pool/head plus the pass-0 build that
`run_block()` still runs inline because nothing has been posted yet when it needs
it.

> **Corrected by [M7f](#m7f-1--the-drain-decode-and-what-one-fifo-cost):** the
> 29 ms of `gw_locate()` is *not* DRAIN's. Once the deferred decode had its own
> profile counter it measured DRAIN's locate at **0 ms, 869 hint hits, 0 misses** —
> the 29 belongs to RUN and the length-carrying commands and stays on core 0. So
> the item promoted to the front of M7f was worth 168, not 197.

#### M7f — move less, and move it narrower ✅ *1,292 → 917 ms, and the jumper took two milestones to pay*

Everything M7e's measurement left, reordered by it rather than by the model.

1. **DRAIN decode and `gw_locate()` onto core 1** ✅ — done, and it took two
   attempts to get the whole of it. [Below.](#m7f-1--the-drain-decode-and-what-one-fifo-cost)
2. **The configuration-C jumper** (PIN2↔PIN17) plus `link_wide`, which the RTL
   already parameterizes. ✅ — soldered and measured, and **the wire projection
   was right to within 3% while the frame projection was worth nothing**: 923 →
   637 ms of wire against 657 predicted, and 1,140 → 1,144 ms of frame against
   ~900. [Below.](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero)
3. **Requantize out of flash** ✅ — done, and the item as written was wrong twice
   over. `fgx_requant()` is a `static inline` in `encoder.h`, so
   `__not_in_flash_func` cannot apply to it at all; and disassembling the binary
   found the scope was far wider than one function. It became
   [M7g-1.](#m7g-1--the-same-code-out-of-sram--1197--1140-ms)
4. **Pre-interleaved weights** — **unparked by item 2, obviated by what M7h found
   while implementing it, and then measured into irrelevance.** The stated
   condition was *"only if core 1 becomes the bottleneck"*, and item 2 made it
   one: core 1 reached 762 ms against 641 of wire. But `export.py` never needed to
   change — the weight streams repeat across position blocks, so caching them
   removed 43% of the work with no new file format and no second copy of the blob
   in flash. What the cache left is the layers where nothing repeats, ~15 ms of
   CPU, which at M7h's measured 40% conversion is about 6 ms of frame.
   **Closed, not done.** See
   [M7h.](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40)

*Check:* the mode ladder plus the accumulator sweep, as M7e ran it, and
bit-exact at both link widths in the same boot — the reason
[M5b learned](bring-up-log.md#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped)
that ratios quoted across two builds of this firmware are not measurements.

##### M7f-1 — the DRAIN decode, and what one FIFO cost

Two modes, one strap, and the second exists only because the first was measured.

`gemm_host.c` gains a deferred-DRAIN path: `gh_drain_defer()` clocks the
transaction and returns without looking at the response, `gh_decode_defer()` is
the looking. **The capture buffer is the caller's**, which is the whole design —
the driver's `rxb[]` alternates every transaction, so a decode still running two
transactions later would be reading a buffer the DMA had begun to overwrite.
`m7.c` captures into its own `rxd[2][8244]` under the same double-buffer
discipline, and the same wait, that already protects `got[]`. The hint table
splits into three slots so the deferred decode's offset learning is core-1's
alone.

| mode | frame | Δ | wire | core 1 busy | core 0 stall |
|---|---|---|---|---|---|
| serialized (M7c) | 1,888 | — | 918 | idle | — |
| pipelined, one core (M7d) | 1,491 | −397 | 1,039 | idle | — |
| + build on core 1 | 1,414 | −77 | 923 | 537 (38%) | 5 |
| + scatter on core 1 | 1,301 | −113 | 923 | 712 (55%) | 39 |
| + DRAIN decode on core 1 | 1,242 | −59 | 923 | 848 (68%) | 138 |
| **+ two priorities on core 1** | **1,197** | **−45** | 923 | **851 (71%)** | **94** |

**1,197 ms, 2.93× the MCU's 3,504 ms this boot.** Bit-exact in all six modes,
then all 174 blocks swept against `gb_golden()` as an untimed seventh pass in the
same boot.

**Mode 4 moved 157 ms off core 0 and the frame fell 59, because 101 ms came back
as stall** — and core 1 was 68% busy, so it was not out of capacity, it was
serving in the wrong order. 101 ms over 174 blocks is 0.58 ms, which is exactly
block *b*'s decode plus its scatter, less the ~240 µs of wire core 0 has left
before its next wait. One FIFO put those two jobs — which nothing is waiting for
until the buffer is reused two blocks later — in front of block *b+1*'s strip
build, which core 0 blocks on by name.

So `worker.c` became two rings with strict priority, re-evaluated between every
pair of jobs. The split is not importance, it is *who waits*: `W1_HI` is the
strip and weight builds, `W1_LO` is the DRAIN decode and the requantize scatter.
It is a runtime flag rather than an edit for M5b's reason — with `c1_prio` off
every job goes to `W1_LO` and an empty `W1_HI` makes that the old single FIFO, so
mode 4 and mode 5 are the same binary in the same boot.

**It recovered 44 of the 101 ms, and the residual is the bound being paid rather
than a bug.** Stall went 138 → 94 against mode 3's 39, leaving ~54 ms. A `W1_HI`
job cannot preempt, so it waits out whatever `W1_LO` job was already running:
348 low-priority jobs at 0.90 ms each, and 54/348 = **0.16 ms** average — against
0.90/2 − 0.24 ≈ 0.21 ms predicted from first principles. Nothing further is
available here without preemption or shorter `W1_LO` jobs, and neither is worth
54 ms.

Two smaller findings:

- **The deferred decode's `gw_locate()` is free — 0 ms, 869 hint hits, 0 misses.**
  M7e attributed ~29 of core 0's 369 ms to DRAIN's locate; that was wrong. The
  31 ms of locate still on core 0 belongs to RUN and the length-carrying
  commands. Splitting the hint table into three slots turned out to be justified
  by core-safety alone, which is what its comment already claimed.
- **`decode` in the phase table falls 170 → 15 ms and its ns/B *rises* 115 → 271.**
  Both are right: what is left is 0.052 MB of short responses whose fixed
  per-call cost no longer has a 1.36 MB payload to amortize against.

Core 0's remaining 274 ms in mode 5: stall 94, `gw_stage()` 50, `gw_locate()` 31,
decode 15, outbound CRC 10, and ~74 of pool/head plus the pass-0 build that
`run_block()` still runs inline because nothing has been posted yet when it needs
it. **Nothing in that list is now larger than the 923 ms of wire**, which is why
item 2 is next and why every remaining firmware item is small.

##### M7g-1 — the same code, out of SRAM ✅ *1,197 → 1,140 ms*

Item 3 said *`__not_in_flash_func` on `fgx_requant()`*. That is impossible —
`fgx_requant()` is a `static inline` in `encoder.h`, so there is no symbol for
the attribute to land on — and disassembling `forgix_m7.elf` showed the real
scope was much wider. Still being fetched over flash XIP, once or more **per
transaction, 6,264 times a frame**: `gw_stage()`, the whole `gh_frame()` path
and its eight command wrappers, `w1_post/wait/drain`, and **every one of core 1's
job bodies** — `gb_strip()`, `gb_weights()`, `scatter()` and the four callbacks.
`w1_main()` had been in SRAM since M7e; the jobs it spent all its time calling
had not, which meant it was off XIP only in the sense that it had moved the miss
one frame down.

`run_block()` needed `__noinline` as well as the section attribute. GCC had
inlined it into `run_frame.constprop.0` — 3,876 bytes of flash — so marking it
alone would have moved nothing at all: the attribute lands on a symbol that is no
longer a call target. Marking `run_frame()` instead would have dragged the
per-layer scaffolding into SRAM for the sake of the per-pass loop, so the loop is
separated out and only it moves. **`arm-none-eabi-nm` is the check, not the
build log** — every intended symbol landing at `0x2…`, every excluded one still
at `0x1000…`.

**This one cannot be a runtime mode, and that is the whole difficulty.** Every
other rung of this ladder is a flag precisely so the A/B lives in one boot
([M5b's rule](bring-up-log.md#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped));
placement is decided by the linker, and worse, relinking perturbs flash layout
globally — the exact variable under test. So the evidence is the **per-phase
counters**, which measure the same code moving the same 8.151 MB and do not care
what else in the image moved:

| counter | M7f | M7g | Δ |
|---|---|---|---|
| `gw_stage()` | 50 | **44** | −12% |
| `W1_HI` busy — the builds | 536 | **458** | −15% |
| `W1_LO` busy less decode — the scatter | 158 | **144** | −9% |
| outbound CRC | 10 | **5** | −50% |
| `gw_locate()` | 31 | **28** | −10% |
| core-0 `decode` | 15 | **11** | −27% |
| **DRAIN decode — already in SRAM since M7e** | **156** | **164** | **+5%** |

**The two-core claim was checkable inside the run, and it checked out.** Mode 0
is serialized on core 0 with core 1 in its idle loop, so nothing contends for
XIP; there, the same two build functions got **5.8% faster** (533 → 502 ms). Mode
2 onward runs them on core 1 while core 0 spins the wire; there they got **14.6%
faster** (536 → 458 ms). Same functions, same inputs, same binary — the extra
~9 points is not the code getting faster, it is the code no longer competing with
core 0 for the one QSPI interface the two cores share. `scatter()` shows the same
sign more weakly: −6.7% uncontended, −8.9% contended.

**And the control got slower, which is the more interesting half.** The deferred
DRAIN decode has been in SRAM since M7e and was not touched, so it should not
have moved — it went 156 → 164 ms. The seventh, untimed sweep pass is what
explains it: there core 0 spends its time in `gb_golden()` and core 1 is
effectively idle, and *uncontended* that same decode reads **155 ms in both
builds**, to the millisecond. So the code is identical and the difference is
entirely contention — **filling SRAM with core 1's working set moved the
contention off the QSPI port and onto the SRAM banks**, which are 8-way
interleaved and arbitrated per bank. Net −57 ms on the frame, but not free, and
worth knowing before the next thing gets moved there.

| mode | M7f | M7g | Δ |
|---|---|---|---|
| serialized (M7c) | 1,888 | 1,831 | −57 |
| pipelined, one core (M7d) | 1,491 | 1,432 | −59 |
| + build on core 1 | 1,414 | 1,351 | −63 |
| + scatter on core 1 | 1,301 | 1,233 | −68 |
| + DRAIN decode on core 1 | 1,242 | 1,191 | −51 |
| **+ two priorities on core 1** | **1,197** | **1,140** | **−57** |

**1,140 ms, 3.07× the MCU.** Bit-exact in all six modes, 512/512 embedding floats
exact, 174/174 blocks swept exact. The frame column is a **cross-build** delta
and is the one number here M5b's rule says to distrust; the per-phase table above
is what it rests on.

**The MCU baseline held, which was the point of the exclusions.** `gw_decode_slow()`,
`gb_golden()`, `fgx_conv_fast()` and `fgx_pool_head()` were deliberately left in
flash — the first two because they run only in the A/B and the sweep, the encoder
path because **3,358 ms is a recorded baseline that every "vs the MCU" ratio in
this README is quoted against**, and making the reference faster would silently
deflate all of them. It read **3,503 ms this boot against 3,504 in M7f**: 0.03%,
so the ratios remain comparable across both runs.

Core 0's remaining 223 ms in mode 5: stall 86, `gw_stage()` 44, `gw_locate()` 28,
decode 11, outbound CRC 5, and ~49 of pool/head plus the inline pass-0 build.
**The wire is now 917 of 1,140 ms — 80%** — and core 1 dropped from 71% busy to
67%. Every firmware lever left is worth tens of milliseconds against a jumper
worth ~300. Cost: 4.0 KB of SRAM, leaving 30,732 B of headroom.

##### M7f-2 — the config-C jumper, and 300 ms that arrived as zero

Three forward data lines instead of one, through the PIN2↔PIN17 jumper. **The
wire projection was right to within 3%; the frame projection was worth nothing.**

| | config A, measured | config C, projected | config C, measured |
|---|---|---|---|
| ACT + WGT wire | 453 ms | 168 ms | **167 ms** |
| RUN + DRAIN wire — clocks, so unchanged by width | 467 ms | 486 ms | **466 ms** |
| whole wire | 923 ms | 657 ms | **637 ms** |
| bytes moved | 8.151 MB | 16.793 MB | **16.791 MB** (−0.01%) |
| **frame** | **1,140 ms** | **~900 ms** | **1,144 ms** |

`gp_block_cost_w()` / `gp_block_parts_w()` price a block at *w* lanes — bytes grow
wherever they were idle, and the time is bytes/*w* because what the link spends is
clocks — and `test_gemm_plan.c` asserts the CPU half does not move and that RUN's
bytes *do* grow, so the projection above was checkable on the laptop before the
iron came out.

**The RTL was the easy half and the P&R report was the surprise.** `gemm_link.v`
gains a `LINK_WIDE` parameter; `gemm_top_wide.v` / `.isf` / `.sdc` wire it to the
configuration-C pin set — clock on B3, data on G3/F3/F2, return on A4 (NSTATUS,
which is why the heartbeat output goes away in this configuration).
`tb_gemm_link` is parameterized over the width and passes at both, 10,560
accumulators bit-exact, the wide run finishing in 24.57 of the narrow run's 42.50
sim-µs. Then:

| build | Fmax | setup slack | critical path |
|---|---|---|---|
| `gemm_top` (config A) | 62.449 MHz | −2.680 ns | `u_link/state[2]` → `u_link/tx_en` |
| `gemm_top_wide` (config C) | 58.630 MHz | −3.723 ns | `u_link/rx_bc[1]` → `u_link/frame_ok` |

Both contradict what `gemm_top_wide.sdc`'s header expected, and the header now
says so. **The global clock on B3 bought back exactly zero skew** — launch and
capture share the whole clock path, so the 6.378 ns through pad, net and CLKBUF
cancels rather than helps — and **`u_tile` appears in neither report**: the path
to close is `gemm_link`'s framing logic in both builds, which is not where anyone
would have started looking for it. A six-seed sweep spread 1.52 MHz over six
*different* endpoints, so the miss is structural rather than placement luck.
Neither number predicts the board, which runs both bit-exact at 75 MHz, so
`m7.c` walks 150/130/110/90 MHz sys — 75/65/55/45 link — and stops at the first
rate that is bit-exact, and suppresses the headline ratio if the two
configurations did not end up at the same clock. A wrong guess costs ~2 s instead
of a strap.

**On the firmware side the width is a runtime switch, not a build flag** — M5b's
rule — so one boot runs the whole six-rung ladder and the accumulator sweep
twice, taking the second bitstream over the same USB CDC channel in between.
`gp_choose()` still ranks at width 1 so both configurations run the same 174
blocks and the comparison is two links rather than two frames. Two things that
looked like details and were not:

- **`link.pio`'s autopull threshold is 24 at width 3**, so a TX word carries
  three wire bytes and discards the fourth. `gw_stage()` therefore *packs*
  instead of `memcpy`ing, behind one `0x00` lead byte that makes the wire header
  six bytes — two whole words — so the payload starts phase-free and
  `gw_pack3()` can move twelve bytes to four words at a time. A byte-at-a-time
  packer would have cost ~110 ms a frame, most of what the third line buys. The
  lead byte is free on the wire: the receiver sits in `R_HUNT` until it matches
  SYNC.
- **`txb` is sized for header-plus-payload only**, with a second DMA channel
  chained behind it, `read_increment` off, feeding the idle tail from one zero
  word. Sizing `txb` for DRAIN at width 3 would have been 33 KB of zeros and left
  the image 4 KB of SRAM. As it stands bss came out 4 KB *below* where it started.

**And then the frame did not move: 1,140 → 1,144 ms.** The wire fell 286 ms and
every millisecond of it came back somewhere else. The per-command breakdown
`gh_prof_t` grew for this run — **in link clocks, not bytes, because bytes are
not comparable across widths and clocks are** — is what made it legible:

| command | ns/clk, config A | ns/clk, config C | |
|---|---|---|---|
| ACT | 13.60 | 14.00 | fine |
| RUN | 13.48 | **15.46** | 14% slow |
| WGT | 13.54 | **40.36** | **3× slow** |

A link clock costs 13.333 ns and cannot cost 40. What that column was measuring
was CPU time inside the pipelined window — and `gw_locate()` read **389 ms
against configuration A's 4**, on 11,099 hint misses. In the serialized mode,
where nothing runs inside the window, the same configuration C wire reads **637
ms** and matches the projection. So the jumper worked exactly as modelled and the
frame kept none of it, and the whole of the difference is one function.

Three harness fixes came out of the same run, each of which had cost something:
`host/m7.py` searched a fixed 64-character window for the board's
`SEND-WIDE-BITSTREAM` cue and the cue is followed immediately by a
~170-character prompt flushed with it, so the window kept only the tail and the
run reported configuration A alone; `gh_xfer_wait()` had no deadline, on an
argument about starvation that was true and never the risk — the risk is arming a
count the PIO will never satisfy, which giving the two directions *different*
word counts made possible for the first time, and 50 ms now turns it into
`GH_ERR_STALL` naming the command and the counts; and the host's 20 s idle
timeout gave up during a configuration C sweep whose quiet gaps are half a minute
long, so the board's own deadline is what detects a wedged link now and the host
just waits.

##### M7g-2 — the reference, the hint slots, and the word loop ✅ *config C 1,144 → 975 ms*

`gw_locate()` finds where a response's payload begins. It predicts the offset
from the last one and searches outward, so a good prediction is a few
comparisons and a bad one is a linear scan of the whole capture. Configuration C
made 390 ms a frame of it. **Three causes, and only the first is about width:**

1. **The reference was computed with a truncating division.** The last payload
   bit is wire bit `8*(hdr+len)-1`, carried by clock `(8*(hdr+len)-1)/w`, so the
   link enters `R_EXEC` at `ceil(8*(hdr+len)/w)`. **At width 1 floor and ceil are
   the same number** — which is exactly why this survived every configuration-A
   run since M6. At width 3 truncation loses `2*len mod 3` bits, so the reference
   moved with the payload's residue class while the hint's delta — pad flight and
   the synchroniser, both constant — could not track it.
2. **CFG, ACT, WGT and NOP shared one hint slot.** Configuration A never had the
   rounding bug and *still* missed 348 times a mode; all 348 were slot thrashing,
   and they are now zero in every mode of both configurations. This one was
   invisible because it was small and had always been there.
3. **`gw_scan()` and `gw_check()` worked a bit at a time.** A little-endian
   64-bit load holds all eight bit alignments of a byte at once, so one load now
   serves eight candidate positions. A *hit* was 32 bounds-checked bit extracts
   on a path that runs 6,090 times a frame — which is why configuration A's
   locate fell 28 → 4 ms with zero misses either side. The cost was in the hit
   path, not the miss path, and nothing in the miss numbers said so.

| | locate/frame | misses/mode | frame |
|---|---|---|---|
| config A | 28 → **4 ms** | 348 → **0** | 1,145 → **1,139 ms** |
| config C | 394 → **36 ms** | ~1,500 → ~300 | 1,144 → **975 ms** |

**So the third data line goes from 1.00× to 1.17×**, and the jumper's 286 ms of
wire finally reaches the frame — 165 ms of it, the rest absorbed by core 1
becoming the binding constraint. 174 blocks, all eight layers bit-exact in all
six modes of both configurations, 512/512 embedding floats exact, 174/174 blocks
swept exact.

**The residual misses are real and are not length-related.** They are 100% ACT
(522) and WGT (996), and configuration C's *serialized* mode misses 5 times in
6,264 transactions — the misses appear only once the pipeline is on, which shifts
when a response arrives rather than how long it is. That is a scheduling
artefact, worth ~30 ms, and it is not worth another reference model.

**Tested with guard pages rather than ASan**, which deadlocks in its own
initialiser on this machine's macOS — confirmed with `sample`, not assumed.
`gw_scan_slow()` stays compiled in as the oracle, and the property it checks is
sharper than "same answer on clean captures": the two must agree on which *false*
lock they find in noise, because that is what a wrong bound would change first.
Six mutations of the word loop's bounds are all caught, three of them by SIGSEGV.

##### M7h — the weight gather, and the packer's round trip ✅ *config C 975 → 917 ms, and a saving that converted at 40%*

Core 1 was the floor — 762 ms busy against 641 ms of wire — so this milestone is
the two largest things it does, plus the one core-0 phase configuration C made
bigger instead of smaller.

**`gb_weights()` is an XIP strided gather, which is a different fault from "slow
code".** `fgx_weights` is a linker symbol: the blob lives in flash and every read
of it goes over XIP. The loop walked `k` outermost, so its inner loop read `Q`
weights `CIN*9` bytes apart. Layer 7 is the worst of it — `CIN=192` makes the
stride 1,728 B and `Q=128` makes the span 221 KB against an **8 KB XIP cache**,
so *every one* of the `Q*K` loads is a line fill and the other 1,727 bytes of the
line are discarded. Swapping the two loops makes the source walk contiguous,
because `(ic0+icl)*9 + tap` is just `ic0*9 + k`, and moves the stride onto the
*write* — whose span is `w_len ≤ 2 KB` and is SRAM.

**And 43% of those gathers did not need to happen at all.** `gb_weights()` reads
nothing that varies with position: its output is a pure function of
`(wb, q0, Q, K, CIN, Cb, pass)`. `gp_blocks()` enumerates the channel block
outermost and the position block innermost, so the `nposblk` blocks of one `q0`
are consecutive and every one of them rebuilds a byte-identical stream. Across
the frame that is **847 of the 1,856 passes and 957,600 of the 2,230,272 bytes**.

It is not all of them, and the shape of what is left is the interesting part:

| layer | npass | nposblk | w_len | cacheable |
|---|---|---|---|---|
| 0–4 | 1–16 | 2–64 | ≤1,152 | ✅ |
| 5, 6 | 32 | **1** | 1,152 / 1,728 | ✗ nothing repeats |
| 7 | 192 | **1** | 1,152 | ✗ nothing repeats |

Layers 5–7 have one position block each, so their streams genuinely are used once
and there is nothing to reuse — and they are also the layers with the largest
`npass`, so a pool that held them would be 221 KB for no gain at all. **18,432 B
covers layers 0–4 exactly**, and the test that selects them is
`npass * w_len > sizeof wcache` rather than a layer number, so the pool
self-selects if the blocking ever changes. Caching also removes the double buffer
while it is active: pass *p* owns slot *p* for the whole block, so core 1 filling
*p+1* cannot be writing what core 0 is sending.

**`gw_pack3()` was paying a 16-byte round trip through the stack per twelve
bytes.** `memcpy(dst + o, d, 16)` with a `uint32_t d[4]` forces GCC 14 to make
`d` addressable: four `str` to the stack, an `ldmia.w ip!, {r0,r1,r2,r3}` to read
them straight back, and four more `str` to `dst`, on top of the three loads and
four stores the packing actually needs. Four named scalars and four 4-byte
`memcpy`s leave seven memory operations per twelve bytes, which is the floor for
this permutation. This is core-0 `stage`, which configuration C took from 44 to
97 ms — the one phase the jumper made worse, because it is the phase that exists
because of the jumper.

**A permutation that is wrong in a way the shapes still permit produces a
plausible tensor, not an error**, so neither change is checked by shape. The old
loop order is kept compiled in as `gb_weights_slow()` — the same arrangement
`gw_scan_slow()` and `gw_decode_slow()` already have — and
`test_gemm_plan.c` compares the two byte for byte on **every block of every pass
of the real model**, 1,856 passes over 2.23 MB. `make -C rtl vec` then
regenerates the golden vectors and `git status --porcelain rtl/` is clean, which
is the same claim made a second way through the RTL testbenches.

###### What the board did

Every component landed where it was predicted, and the frame moved less than half
as far. Both halves of that are the finding.

| quantity | M7g-2 | predicted | **M7h** |
|---|---|---|---|
| weight build, serialized (core 0) | 502 ms | ~300 | **318 ms** |
| weight build on core 1 (`W1_HI`) | 460 ms | ~260 | **292 ms** |
| bytes served from the cache | — | 43% | **43%** — 847 of 1,856 passes |
| `stage` (config C) | 97 ms | lower | **70 ms** |
| core 1 busy | 762 ms | ~590 | **593 ms** |
| core 0 stalled on core 1 | 141 ms | ~0 | **127 ms** |
| **frame (config C)** | **975 ms** | **~830** | **917 ms** |

The cache hit its ceiling **exactly** — `1009 of 1856 passes built, 847 served
from the cache (43% of bytes)`, in every one of the six modes and in both link
configurations. That is the number the arithmetic said was available and not one
pass less, which is the whole reason the report prints a fraction rather than a
hit count: a *lower* number would have meant something was evicting a key that
nothing should evict, and there is no such thing here.

`gb_weights()` and `gw_pack3()` therefore both delivered in full — 184 ms and
27 ms of CPU respectively, against a prediction of ~200 and "lower". **The frame
took 58 of those 211 ms.** Not because the measurement is noisy: config A moved
1,139 → 1,110 and configuration C's serialized mode, where nothing overlaps
anything, moved 1,582 → **1,374**, which is the full 208 ms showing up exactly
where a serial model says it should.

**The reason is that two thirds of the saving was already hidden.** `W1_HI` was
460 ms of builds running inside 641 ms of wire — core 1 was busy, but it was busy
in time core 0 was going to spend on the link regardless. Taking it to 292 ms made
core 1 idle earlier; it did not make the frame shorter, because the frame is
`max(wire, CPU)` plus whatever core 0 cannot overlap. What actually moved is the
part that was never hidden: `stage`'s 27 ms on core 0, and core 0's stall falling
141 → 127 ms.

So the frame decomposes, at 917 ms:

| | ms | overlappable |
|---|---|---|
| wire (elapsed) | 644 | — it *is* the overlap |
| `stage` + `locate` + `crc` + `decode` on core 0 | 114 | ✗ core 0, between transactions |
| core 0 stalled waiting on core 1 | 127 | ✗ |
| | **885** | *against 917 measured* |

**This is the third milestone in a row where a correctly-predicted component
saving converted at less than face value, and the mechanism was different every
time.** M7e's core-1 offload converted at ~50% because the work moved rather than
disappeared. [M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero)'s
jumper converted at 0% because a latent bug ate it. M7h converted at 40% because
the work was already off the critical path before it was made cheaper. The cost
model in this README still adds columns, and **once the frame is pipelined, a
column is only worth what fraction of it is on the critical path** — which the
model does not represent and, given three different mechanisms, probably cannot.

> Two days later it happened twice more, in the same paragraph of the same
> milestone, and neither one needed a board to expose it.
> [M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)
> as first written claimed the wire's 644 ms would fall to 330 by moving RUN's
> 314 ms of idle bytes off it — **instance four**, and the first one that was not
> a component saving at all but a component *relabelling*: RUN's bytes are the
> tile computing, so deleting the bytes deletes the transport and keeps the time.
> Then the requantize-in-fabric row of the ladder, which read ~350 ms, turned out
> to be ~350 only because it was stacked on M10's imaginary 330 ms wire; on the
> real 644 ms one it lands near ~780, because core 1's 593 ms becomes the binding
> term the moment the wire drops below it — **instance five**, and a `max()`
> misread rather than a critical-path one. The pattern is not "savings convert at
> less than face value." It is that **this document keeps writing sums where the
> hardware computes a maximum**, and five instances in, the ladder table now
> carries that warning inline rather than in a footnote.

The practical consequence is that the next 100 ms is not another CPU saving.

> **Wrong, and by 66 ms.** [M7i](#m7i--two-instructions-for-the-epilogue--config-c-917--851-ms-and-the-mcu-baseline-moved-too)
> was another CPU saving and it converted at 79% rather than 40%. What this
> paragraph got right is *where* to look — the stall. What it got wrong is
> assuming the stall had to be attacked as a scheduling problem. Shrinking the
> work that overruns is the same lever from the other end.

Core
1 is now busy 593 of 917 ms and core 0 still stalls 127 ms against it, so the
remaining levers are, in order: the 127 ms stall (a deeper queue, or splitting
DRAIN decode differently), `stage`'s remaining 70 ms, and then the wire's 644 —
of which **314 ms is RUN, and RUN is the tile computing.** That is the 265 ms
floor arriving in the measurement, and it was
[M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)'s
entire argument — until M10 measured the tile standalone at **70 MHz** against
the 75 it is clocked at today, which says the 314 ms is not the link's fault and
there is nowhere for it to go. RUN is a floor, not a wall.

Final, both configurations, one boot: **config A 1,633 → 1,110 ms** across the six
modes, **config C 1,374 → 917 ms**, third data line worth **1.21×** (was 1.17),
**3.76× the MCU's 3,448 ms**, all eight layers bit-exact in all twelve modes,
512/512 embedding floats exact, and the accumulator sweep clean on 174 of 174
blocks.

### M7i — Two instructions for the epilogue ✅ *config C 917 → 851 ms, and the MCU baseline moved too*

M7h left the next lever as "the 127 ms stall". This is that lever, reached from
the other end: instead of scheduling around `W1_LO`, make `W1_LO` smaller.

`scatter()` runs `fgx_code()` once per output element of the whole encoder —
**356,352 times a frame** — and `fgx_code()` was `lrintf()` followed by a clamp
ternary. Neither is what it looks like on a Cortex-M33:

- **`lrintf()` is a call.** `arm-none-eabi-gcc` will not inline it. `bl lrintf`
  is what comes out at `-O2` whether it is written as `lrintf()`, as
  `__builtin_lrintf()`, with `-fno-math-errno`, or with `-ffast-math` — all four
  were checked — and newlib's `lrintf` is ~30 instructions of exponent
  extraction and bit reassembly. `VCVTR.S32.F32` is **one** instruction.
- **The clamp is up to four branches.** `USAT` is one instruction and none.

The substitution has to be *exact*, not close, because the whole project rests on
bit-exactness against `encoder.c`. It is, for a reason that is checkable rather
than hopeful: `VCVTR` rounds by `FPSCR`'s current mode, which is
round-to-nearest-even out of reset, matching `lrintf()` and numpy's `rint()` in
`export.py` — and nothing in the linked image ever writes it. `grep -c
'vmsr.*fpscr' build/forgix_m7.dis` is **0**, ours, the SDK's and newlib's
together. Unsigned saturation to 8 bits is exactly `[0, 255]` with negatives
going to 0, which is what the ternary did. `fgx_code()` split into `fgx_rint()`
and `fgx_sat8()`, each with a host fallback behind `__ARM_ARCH_PROFILE == 'M'` /
`__ARM_FEATURE_SAT`, so `test_encoder` on the laptop still exercises the
contract. It passed 2048/2048 bit-exact before anything was flashed.

**Measured, same boot, config C:**

| | M7h | M7i |
|---|---|---|
| frame | 917 ms | **851** |
| `scatter` | 141 | **57** |
| `W1_LO` (decode + scatter) | 300 | 216 |
| `W1_HI` (builds + strips) | 292 | 293 |
| core 1 busy | 593 | 509 |
| core 0 stalled | 127 | **66** |
| wire, elapsed | 644 | 639 |
| RUN | 314 | 313 |
| MCU-only baseline | 3,448 | **3,359** |

Config A 1,633 → **1,081** across the six modes, config C 1,374 → **851**, third
data line worth **1.27×**, **3.94× the MCU**, all eight layers bit-exact in all
twelve modes, 512/512 embedding floats exact, 174 of 174 blocks swept exact.

Two things are worth carrying forward. **The conversion rate was 79%, not 40%** —
84 ms off core 1 bought 66 ms of frame, against M7h's 211-for-58. The difference
is which queue the saving came out of: `W1_HI` overruns hide in the wire's
shadow, `W1_LO` overruns *are* core 0's stall. Same amount of CPU, five times the
frame effect, depending only on which side of the priority split it sits.
**And the MCU-only baseline moved 89 ms**, from a figure that had been exactly
3,448 in each of M7f, M7g and M7h — because `encoder_fast` shares the epilogue.
So the 7.2% faster frame shows up as only a 4.8% better ratio. Comparing against
M5b's remembered 3,358, which predates the epilogue entirely, would have hidden
that; the in-boot control is the reason it did not.

**This is also the first milestone with no strap in it.** Every previous firmware
change cost a physical trip to the bench. `brew install picotool` supplied a
build with USB support — the SDK's own vendored one reports *"compiled without
USB support"*, which is the whole reason the reboot path had never worked — and
`picotool reboot -f -u` reaches BOOTSEL from a running application. See
[question 9](history.md#verify-before-building). `picotool load -x` hangs; copy
the `.uf2` to `/Volumes/RP2350` instead, ~23 s.

### M7b — Source the camera ⬜ *runs in parallel; it exists because of lead time*
Buying hardware is not engineering, but it is on the critical path in a way M6
and M7 are not: nothing about M8 can be debugged without the part in hand, and
the choice constrains the pin budget that M7 is spending. So it gets a milestone
and it happens now, while the GEMM tile is being built.

**Choice: Arducam Mega 3MP SPI** ([Switch Science SKU 8941](https://www.switch-science.com/products/8941),
¥4,840). Three properties decided it, in order:

- **4-wire SPI only.** The Mega carries sensor configuration as commands over
  the same SPI bus; the older Mini (OV5642 + ArduChip) needs a separate I²C
  bus. Four wires is exactly what the header has left over — see the pin budget
  in M8 — and it leaves GPIO22/23 free for the configuration-C link jumper.
- **Focus from 6 cm.** The 5MP Mini focuses from **2.6 m to infinity**, so it
  physically cannot resolve an object on a desk. For an appliance whose entire
  interaction is "point it at a thing," that is disqualifying, and it is not a
  number that appears in any comparison table until you go looking for it.
- **128×128 is a native mode.** Resolution register `0x21` value 11 is
  `CAM_IMAGE_MODE_128X128`, which is exactly `hdr.in_size`. No resize on the
  MCU, no JPEG decoder, and no scaling filter sitting between what the camera
  sees and what the student was distilled on.

  > **The constant is right for some modules and wrong for others**, found on
  > 2026-08-03 while writing [`cam_probe.c`](../firmware/cam_probe.c) against
  > ArduCAM's driver rather than the application note. `legacyMode()` in
  > `src/Arducam/ArducamCamera.c` remaps the whole resolution table when the
  > sensor ID reads below `0x85`: 128×128 is `0x0b` on the older dies and `0x01`
  > on the current ones. A 3MP module reports `0x82`, `0x84` **or** `0x86`, and
  > only the last of those takes the current table — so which constant is
  > correct is not a property of the part number. The probe reads the ID and
  > picks; a compile-time choice would have worked on some modules and quietly
  > captured a different size on others, which is the failure that produces a
  > FIFO of the wrong length and no error anywhere. The point stands: 128×128 is
  > native either way.

The ArduChip buffers the frame and holds it until flushed, so the MCU reads out
at whatever rate it likes: 128×128 RGB565 is 32,768 B, ~33 ms at the 8 MHz SPI
ceiling — under 4% of the **917 ms** the board measures today, and 4% of the
**~780 ms** that giving up the bit-exact float contract would buy, which after
[M10 closed](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)
on 2026-07-31 is the shortest frame still on the ladder. The camera is not going
to be the bottleneck at any point on it.

*Open before ordering:* the app note says raw formats top out at 1920×1080 and
lists 128×128 as a valid resolution, but never states that RGB565 **at** 128×128
is a supported combination. Fallbacks exist (YUV at 128×128, or 320×320
downsampled), so this does not change the choice — but it should be confirmed
against the [application note](https://blog.arducam.com/downloads/datasheet/Arducam_MEGA_SPI_Camera_Application_Note.pdf)
rather than assumed.

*Check:* part in hand, and a `docs/pinmap.md` entry for the six camera wires.

### M8 — Camera
Arducam Mega over SPI on **GPIO 8/9/12/13** (header PIN0, PIN1, PIN7, PIN8) —
CS, SCK, MOSI, MISO. Not time-multiplexed with the MCU↔FPGA link: the link
lives on the configuration pins (GPIO1/2/3/6, see `rtl/link_narrow_io.isf`), so
the two are independent, and GPIO22/23 and J3 both stay free.

**It has to be PIO SPI, not a hardware SPI instance.** Neither peripheral can
reach the header: SPI1's SCK (GPIO10/14/26) and TX (GPIO11/15/27) are *all*
unbonded on this board, and SPI0's RX options (GPIO0/4/16/20) are PSRAM CS,
FPGA nRESET, and two more unbonded pins. Not a problem — the link is already a
PIO program — but it is the kind of thing that is much cheaper to discover from
the pinmap than from a dead bus.

**J3 goes unused.** The 4-pin connector is I²C only (`SDA` → GPIO28, `SCL` →
GPIO29, the only exit those two pins have), which would have been the answer for
a Mini and is simply not needed for a Mega.

Capture → RGB565 unpack → quantize → inference.
*Check:* dump a captured frame over USB serial as PNG, eyeball it; then confirm
end-to-end latency holds.

#### M8a — bring-up ✅ *2026-08-03: bus up, pixels correct, one real fault found*

`firmware/cam_probe.c` + `firmware/cam_spi.pio` + `host/cam.py`. Soldered as a
six-wire flying harness; the four SPI signals were assigned to match the cable's
own wire order rather than any electrical constraint, so nothing crosses:

| wire | signal | GPIO | header |
|---|---|---|---|
| red | VCC | — | 3V3, short edge |
| black | GND | — | GND, long row |
| white | SCK | 8 | PIN0, silk `0` |
| brown | MISO | 9 | PIN1, silk `1` |
| yellow | MOSI | 12 | PIN7, silk `7` |
| orange | CS | 13 | PIN8, silk `8` |

**The sensor is `0x82`, which takes the legacy resolution table.** So 128×128 is
register `0x21` value **`0x0b`**, not `0x01`, and QVGA is `0x01`, not `0x03`.
ArduCAM's `legacyMode()` remaps the whole table below ID `0x85`, and the
application-note constant this milestone was written from would have captured the
wrong size with no error anywhere. Both FIFO lengths came back exactly right
(32,768 and 153,600), which is independent confirmation the codes are correct.
`cam_probe.c` picks the table from the ID at runtime for that reason.

Measured, 150 MHz sys:

| | 128×128 @ 8 MHz | 128×128 @ 16 MHz | 320×240 @ 8 MHz |
|---|---|---|---|
| setup (register writes) | 0.0 ms | 0.0 ms | 17.3 ms |
| exposure to `CAP_DONE` | 65.9 ms | 64.1 ms | 64.0 ms |
| FIFO read | 32.8 ms | **16.4 ms** | 153.8 ms |
| bus | 0.95 MB/s | **1.90 MB/s** | 0.95 MB/s |

**16 MHz holds on a sustained burst**, which the register sweep could not have
established — a three-byte register read and a 32,768-byte burst under one CS are
different claims. The proof is the picture, not a CRC: a burst that drops or
doubles a bit tears every row after it, which is obvious by eye and invisible in
a checksum the device computed over its own corrupted buffer. The 16 MHz frame is
indistinguishable from the 8 MHz one. Setup is 0.0 ms whenever the mode is
unchanged, for the reason in the fault below.

**Pixels, all four ways they could have been wrong, all settled by looking:**

- **Byte order: high byte first.** Both orders are rendered side by side; the
  low-first PNG is the blue-cast one.
- **Channel order is RGB, no swap.** A red object photographs red.
- **Orientation is correct** — no flip, no rotation. Established by elimination
  over two shots: a red object placed at the right of the scene appeared at the
  right, killing horizontal flip, 180°, and both 90° rotations; placed at the top
  it appeared at the top, killing the vertical flip that was left.
- **The device's int8 tensor matches numpy exactly**, both byte orders, every
  frame — which is what makes M5's "the encoder is bit-exact" mean something from
  the lens inward rather than only from the codes inward.

**THE FAULT: writing `CAM_REG_CAPTURE_RESOLUTION` with the value it already holds
blanks the next capture.** Symptom: every capture after the first returned a
constant fill — one 16-bit value repeated across all 32,768 bytes — with
`CAP_DONE` asserted, the length exactly right, `CAM_REG_SENSOR_STATE` reporting
IDLE throughout, and no error reported anywhere. It is a well-formed frame that
is not a photograph.

Two things about how it was found are worth keeping. First, it was established by
*controlled comparison*, not inspection: two runs against completely different
scenes produced byte-identical frames. A frame that does not depend on where the
lens is pointed is not a frame, and no amount of staring at a dark PNG says that.
Second, **the first fix made it worse and that is how the real cause surfaced.**
The initial reading was "the first frame after a mode change is invalid", so a
discarded warm-up capture went in — after which *every* kept frame was constant,
including the 128×128 one that had been fine, because the warm-up had moved every
kept frame out of position 1. A fix that spreads the symptom is aimed at the wrong
thing.

The matrix that settled it — seven captures, one boot, one scene, 128×128 at
8 MHz, differing only in the recipe, and reproduced identically across two boots
down to the constant's CRC:

| # | recipe | crc32 | verdict |
|---|---|---|---|
| 0 | as-was | *(varies)* | a picture — first write of the value, fine |
| 1 | as-was | `c80a8564` | **CONSTANT** — redundant write |
| 2 | no-rewrite | *(varies)* | a picture |
| 3 | flush | `c80a8564` | **CONSTANT** — `flushFifo()` does not rescue it |
| 4 | no-rewrite + flush | *(varies)* | a picture |
| 5 | no-rewrite + 300 ms settle | *(varies)* | a picture |
| 6 | everything | *(varies)* | a picture |

Row 3 is the one to remember. `flushFifo()` — `writeReg(ARDUCHIP_FIFO_2,
FIFO_CLEAR_MASK)`, which ArduCAM have **commented out** at the head of
`cameraSetCapture()` — was the leading hypothesis and is byte-for-byte
irrelevant: row 3 returns the *identical* constant to row 1. The vendor was right
to comment it out, and the thing that actually mattered was four lines up in a
different function. Row 5 rules out the dull explanation too: it is not a timing
problem, and there is nothing to poll for.

The fix is ArduCAM's own guard in `cameraTakePicture()` — write `CAM_REG_FORMAT`
and `CAM_REG_CAPTURE_RESOLUTION` **only when the value differs from the last one
written**. Three lines, and easy to read as defensive tidiness rather than as
load-bearing. `cam_probe.c` keeps the matrix as a boot-time regression check
rather than deleting it, because what it really tests is whether a different
module or a newer ArduChip firmware still behaves this way, and that costs one
second a boot.

**The 128×128 mode squashes, it does not crop — and it costs nothing.** Worth
knowing because it is not what the training pipeline does and the two could
easily have been assumed to match. `distill.student_transform()` at eval time is
`Resize(short side) → CenterCrop`, which preserves geometry; the Mega's 128×128
mode takes the whole 4:3 sensor field and compresses it into a square, so every
object arrives 1.333× narrower than the student was distilled on. Training
augmentation is `RandomResizedCrop(ratio=(0.8, 1.25))`, which puts 1.333 just
*outside* the range the student ever saw.

Established by measurement rather than by reading a spec: the native 128×128
frame was correlated against the QVGA frame of the same scene warped both ways.

| candidate | MAE | luma correlation |
|---|---|---|
| squash 320×240 → 128×128 | 23.45 | **0.963** |
| center-crop 240×240 → 128×128 | 31.72 | 0.866 |

And then the cost was measured rather than argued, with
`evaluate.py --geometry camera` (added for this; it center-crops each COCO image
to 4:3 and squashes that to square, reproducing the camera's geometry whatever
the source aspect ratio, and leaves int8 calibration on the training framing so
the geometry question is not confounded with a requantization):

| geometry | queries ≥ 0.80 | mean AUC | cos-to-teacher | retention |
|---|---|---|---|---|
| `crop` (training framing) | 59 / 67 | 0.899 | 0.846 | 59/63 = 94% |
| `camera` (squashed) | 59 / 67 | 0.898 | 0.843 | 59/63 = 94% |

**0.001 of mean AUC.** So the fix that was on the table — capture QVGA, center-crop
240×240, downscale on the MCU — is not worth its 121 ms per frame, and native
128×128 stays. This is the good outcome of asking: the work not done is the point.

**The colour cast was a camera default, and the register that fixes it is not the
one with "white balance" in the obvious place.** The first frames came out warm —
mean RGB (91, 82, 53), blue at 58% of red — which could equally have been the
scene, so it was swept rather than argued about. The metric is two numbers off
one frame and neither is a judgement about a PNG: the mean of the three channels
is exposure, the spread between them is white balance.

| setting | reg | mean R G B |
|---|---|---|
| as `cam_begin()` leaves it | — | 91 · 82 · **53** |
| auto-exposure on | `0x30` ← `0x81` | 95 · 89 · **31** |
| auto-gain on | `0x30` ← `0x80` | 102 · 97 · **36** |
| auto-white-balance **on** | `0x30` ← `0x82` | 109 · 104 · **42** |
| WB mode office | `0x26` ← `2` | 141 · 97 · 88 |
| WB mode home | `0x26` ← `4` | 123 · 98 · **133** |
| WB mode auto | `0x26` ← `0` | 121 · 100 · **131** |
| EV +1 / +2 | `0x25` | 115 · 104 · 129 / 121 · 111 · 132 |
| brightness +1 / +2 | `0x22` | 137 · 127 · 142 / 147 · 136 · 149 |

Turning auto white balance **on** through `CAM_REG_AUTO_CONTROL` barely moves it —
blue goes from 53 to 42 while red climbs to 109, so in relative terms it gets
*worse*. What fixes it is writing `CAM_REG_WB_MODE_CONTROL` **at all**, including
with `0`, the value its own enum documents as the default: blue goes 42 → 133 and
stays there. So either the register's power-on content is not 0, or writing it is
what kicks the AWB loop. Either way the write reads like a no-op and is not one.

`cam_image_defaults()` therefore sets auto-exposure, auto-gain, auto-white-balance
and WB mode `0`, and nothing else — EV, brightness, contrast and saturation all
move the mean, and none was needed once the white balance was right. Result:
**mean RGB (115, 107, 105)**, near-neutral, against (91, 82, 53) at the start.

**And a negative result worth the 100 ms it cost.** The sweep takes *two* captures
per setting, because every one of these controls is an I2C write to the sensor and
so is the cause of the blanking fault above — so "does a brightness change also
blank the next frame?" is the same question and was worth measuring rather than
assuming. It does not. No setting in the table blanks its first capture. The fault
is specific to `CAM_REG_CAPTURE_RESOLUTION`, which is why `cam_image_defaults()`
can write four registers and capture immediately afterwards with no throwaway
frame.

**Left for M8b:** nothing on the camera itself. What remains is wiring the capture
into the M7 frame loop.

#### M8b — into the frame loop ✅ *2026-08-03: the whole ladder runs on a live frame, bit-exact*

`firmware/cam.h` + `firmware/cam.c` — M8a's probe split into a driver, so there is
one copy of the register map, the bus and the capture sequence — plus
`acquire_image()` in `m7.c`, called between `fpga_configure()` and the reference
pass. When it returns `NULL` the run falls back to the flash test vector and says
so, and the PASS line names which input it ran on. Both exits are loud on purpose:
a silent fallback would turn "the camera is broken" into "the camera is fine".

**The frame had to be paid for out of the arena, because there is no RAM left.**
The naive version — a third array beside `arena` and `scratch_b` — overflowed
`.bss` by 38,296 bytes. But `arena` was 192 KB because it holds the **173,124-byte**
T8F49 bitstream, not because buffer A needs it; buffer A never exceeds `m.scratch`
= 131,072 B (conv0's output is 64×64×32). That is ~60 KB of slack that exists only
during the two downloads, and 48 KB of it is exactly a 128×128×3 int8 frame. So
both ping-pong buffers and the frame now carve out of one `pool[]`, and **the
order is the safety property**: the frame sits above both buffers and
`recv_bitstream()` caps the download at everything below it, so configuration C's
second bitstream — which arrives long after the camera ran — provably cannot reach
the frame its six modes are about to be scored on. `.bss` lands at
`0x20079d98`, ~25 KB clear.

**FAULT ONE: 8 MHz is for register writes, 16 MHz is for pixels, and M8a only
proved the second half.** The first version ran the whole sequence at 16 MHz and
got a constant fill of `08 01`. The interesting part is what was *right*: the FIFO
held exactly 32,768 bytes, so the `CAM_REG_CAPTURE_RESOLUTION` write had plainly
landed; the sensor stayed IDLE; `CAP_DONE` asserted on time. Not a dead bus, and
not M8a's blanking fault either. Moving only the register writes down to 8 MHz
fixed it, and re-reading `cam_probe.c` says why the probe never saw this: every
one of its register experiments runs at 8, and its one 16 MHz row (`f128fast`)
writes no registers at all because the rewrite guard suppresses them. So M8a's
"16 MHz holds on a sustained burst" was true and did not generalise. `m7.c`
configures at 8 and switches to 16 for the keeper capture, which is register-free
for the same reason `f128fast` was.

**FAULT TWO: auto-exposure converges over frames, and no register says
"converged".** With fault one fixed, the first frame came back non-constant and
**mean RGB 14 10 5** — a real photograph of a well-lit room, about four stops
under. `cam_probe` reports 100–155 on the same bench because it takes thirty-odd
captures before the one it keeps; `m7` took one. The tell was in the timing line
that was already being printed: `expose 36 ms` against the probe's `69.4 ms`, i.e.
AE pinned at the short end of its ramp.

`cam_image_defaults()` turns AE and AGC on and they walk towards the scene a frame
at a time, so the fix is a warm-up loop — which M8a had thrown out, and safely: the
warm-up it deleted predated the rewrite guard, so its throwaway capture rewrote
FORMAT and RESOLUTION and shoved the kept frame into the blanking fault. With the
guard, a repeat capture writes no registers at all.

**The predicate took two tries, and the first failure is the useful one.**
Breaking on the first pair of frames whose mean luma differs by ≤ 2 reported
"settled after 2 frames" and stopped at luma **9** — a sensor sitting at the bottom
of its ramp is perfectly stable for a frame or two before it starts climbing, so
"unchanged since last time" is not "converged". The loop now needs three
consecutive stable comparisons and a floor of six frames, bounded at 24, and
prints the whole ramp rather than a verdict:

```
camera : exposure ramp 5 27 64 71 82 89 95 101 102 113 123 128 128 129 130
camera : live 128x128 RGB565, id 0x82, 16.0 MHz, expose 36 ms, read 16 ms,
         exposure settled after 15 frames
         mean RGB 142 136 114
```

Stability and not a target brightness: a genuinely dark scene is a legitimate
answer, and the printed mean is how a human tells the two apart. The ramp costs
~15 captures ≈ 1.2 s, once, before the frame loop.

**Result: `PASS — all 8 layers bit-exact in all six modes of both link
configurations, 512/512 embedding floats exact, on a frame off the camera.`**
Config A 1542 → 1075 ms/frame, config C 1284 → **845 ms/frame** at a 75.0 MHz link
— the same ladder M7i measured on the flash test vector, now measured on light off
a lens. `forgix_cam_probe` was re-flashed after the `cam.c`/`cam.h` split and
reproduced M8a's matrix exactly, constant CRCs included.

**One operational note.** The board wedged deaf twice during this work — enumerated
as PID `0x0009`, answering neither `'B'` on stdin, nor the 1200-baud touch, nor
`picotool reboot -f -u`, which is the state `park()`'s comment already describes.
Only a BOOTSEL replug recovers it. Every software escape has now been tried and
none of them is a substitute for the button.

**Left for M8c:** run it in a loop. Everything above happens once per boot, inside
a harness whose job is to compare six modes against a reference — which is the
wrong shape for a demo. M8c is capture → encode → embed → repeat, with the
exposure ramp paid once at start-up and the reference pass gone.

#### M8c — capture → encode → embed, continuously ✅ *2026-08-03: the loop runs, and it is looking at the world*

`firmware/frame.h` + `firmware/frame.c` — the engine, extracted from `m7.c` and
**moved rather than rewritten**, prefix `ft_`. `m7.c` drops 1,726 → 884 lines and
keeps only its six-mode ladder and its reporting; `firmware/m8.c` is 374 lines of
loop over the same calls. `host/m8.py` drives it.

**The extraction was safe to attempt because `m7` grades it.** ~600 lines of
`m7.c` *were* the engine — `run_block()`, the weight cache, the requantize
scatter, the core-1 callbacks and the ping-pong pool — and they are the trickiest
concurrency code in the project. But `m7` computes every layer and all 512
embedding floats twice in one boot, once on the MCU and once with every
convolution on the T8, and compares CRCs layer by layer. A botched move prints
`FAIL`; it cannot hide. The regression bar was therefore the existing binary, run
unchanged, and it lands on the old numbers to the millisecond:

```
RESULT : PASS - all 8 layers bit-exact in all six modes of both link
         configurations, 512/512 embedding floats exact, on a frame off the camera
         config A: 1542 -> 1249 -> 1241 -> 1189 -> 1108 -> 1074 ms/frame
         config C: 1284 -> 1177 -> 1005 ->  942 ->  877 ->  845 ms/frame at 75.0 MHz
         174 of 174 blocks swept, every accumulator exact  (both configurations)
```

**One behavioural change, and it is the one that makes a loop possible.** A
library must not exit. Where the old code called `park()` — a plan that does not
tile, a geometry `gb_geom()` rejects, an accumulator that disagrees with
`gb_golden()` — `ft_layer()` now returns the reason as a printable sentence and
lets the caller decide. Nothing in `frame.c` prints in the frame path either,
because the two callers want different reports: `m7` prints a per-layer table
with wire and stall columns, `m8` prints one line a frame.

**`m8` works out which bitstream it got by running the whole test vector over the
wire.** There is no `--wide` flag and no cue for the host to watch, because a flag
can disagree with the hex file and a measurement cannot. It tries three forward
data lines, compares 512 floats against the MCU reference, and falls back to one:

```
probe : 3 forward data lines -> 512/512 floats exact, 852 ms     (gemm_top_wide.hex)
probe : 3 forward data lines -> no response preamble - link dead
probe : 1 forward data line  -> 512/512 floats exact, 1081 ms    (gemm_top.hex)
```

852 and 1081 are `m7`'s top rungs for configurations C and A. One self-test doing
two jobs: it identifies the wire *and* proves the tile is configured correctly
before the demo starts showing numbers nobody can check. The loop line is the
cosine of this frame's embedding against the last one — the same arithmetic M9's
text match will use — and it sits at 0.994–0.999 on a still scene at **~900
ms/frame in configuration C** (852 ms of tile and wire, 36 ms expose, 16 ms
read), capture included.

**And it is demonstrably looking at the world.** A cosine near 1.0 proves
nothing on its own: a frame of constant fill scores 1.000 against the previous
frame of constant fill, which is exactly how the fault below hid for 302 frames.
So the acceptance test is the *transition*. Covering the lens by hand and
uncovering it five seconds later:

```
frame 43 : cos to previous 0.996
frame 44 : cos to previous 0.939     <- hand goes over the lens
frame 45 : cos to previous 0.985
frame 46 : cos to previous 0.998     <- still scene again, in the dark
...
frame 49 : cos to previous 0.916     <- hand comes off
frame 50 : cos to previous 0.963
frame 51 : cos to previous 0.997     <- back to the room
```

Two dips, five frames apart, against a baseline that never left 0.996–0.999 in
the other 88 frames. Note the shape this metric has: frame-to-*previous*-frame
cosine only moves while the scene is changing, so a covered lens held still
reads as high as an uncovered one held still. That is correct behaviour and
worth stating, because M9 compares against a fixed text embedding instead and
will show the sustained level rather than the edges.

Endurance, on a working camera: **300 frames, zero dropped captures, no sticky
bit, no wedge**, cosines spread 0.994–1.000 with the mode at 0.999. The earlier
302-frame run had the same mechanical result but every cosine at exactly 1.000,
which is what sent us looking for the fault below.

**A third silent camera fault, found by walking into it.** M8b's exposure ramp
waits for the mean luminance to stop moving, with a floor of six frames, because
its predecessor stopped at luma 9 on a sensor that had not started climbing yet.
That fix was incomplete in the same direction. On a colder start the ramp read

```
camera : exposure ramp 5 5 5 5 5 5
camera : still a constant fill (08 01) after 6 frames - using the flash test vector
```

and **luma 5 is not a dark room — it is the mean of the `08 01` constant fill**,
which is what the FIFO returns when the sensor has not written a frame at all.
Six identical readings of *no frame* satisfied every stability test, so the run
fell back to the flash test vector on a bench where `cam_probe` was pulling clean
pictures off the same module minutes either side of it. Stability now only counts
once there is a frame to be stable about, and the bound goes 24 → 40. A genuinely
dark scene still breaks the loop, because a dark scene has sensor noise and is
therefore not constant: *constant* and *dark* are different states, and only one
of them means the camera has nothing to say.

**And the same fault came back through the other door one run later**, which is
the part worth keeping. The next cold start read `4 5 5 5 4 5` — *not* constant,
one pixel of noise away from it — and sailed through the new test as settled at
mean RGB `8 0 7`. Three hundred frames later it was still `8 0 8` with every
cosine pinned at `1.000`: 302 frames, 204 good, 921 ms/frame, no wedge and no
sticky bit, and not one of them looking at anything. So *constant or not* was
never the real predicate either. Both the good and the bad runs sit at the
bottom of the sensor's range; only the last bit of noise differs, and that bit
is not the difference between a camera that works and one that does not.

What actually separates every good run from every bad one is whether the
exposure ever **rose** — the good ones go `5 8 48 53 … 79` and are done in a
dozen frames, the bad ones never leave single digits however long they are
given. Early exit now needs a luma clear of a floor as well as a stable one, and
the floor is deliberately low: it is not *correctly exposed*, which is a
judgement this firmware has no business making, it is *the sensor is doing
something*. A sensor that never climbs now spends the whole 40-frame bound
trying, and is then **reported rather than refused** — the frame is returned
either way, with a sentence at start-up naming the likely causes, because
refusing would mean firmware deciding it knows the lighting better than the
person standing in it. On the next dead-sensor run that reads:

```
camera : exposure ramp 5 5 4 5 4 4 5 5 5 4 ... 5 5 5 5 4   (40 frames)
camera : exposure settled after 41 frames
         mean RGB 8 0 7
         ^ that is the bottom of the sensor's range after 41 frames of auto-exposure.
```

Three failures of one predicate, in three different directions, and the general
shape is the same each time: **a convergence test that cannot tell "the signal
stopped changing" from "there is no signal" will always eventually be handed the
second one.** The fix is never a better tolerance — it is an independent check
that the signal exists at all.

That fault also produced a **false performance finding worth recording**, because
it was reproducible three times and still wrong. The failed runs put the whole
ladder 6–7 ms per rung slower with every accounted phase unchanged — wire elapsed
identical to the millisecond, bytes moved identical, core-1 jobs identical — which
is exactly the signature of a flash-layout shift, and `m7g` documents that any
relink perturbs XIP globally. The diagnosis was wrong. It was the 40 aborted
captures leaving the camera bus running underneath the frame; with the camera
working the ladder is `c5d4b1a`'s to within 1 ms. **A flat per-frame cost with no
phase to account for it is not evidence of a relink — it is evidence that
something outside the accounted phases is still running.**

### M9 — Query loop (**fpga-open-vocab**, the demo everything else is for) ✅ *2026-08-07: describe it, the board spots it*

`firmware/m9.c` + `host/demo.py`. Type words on the laptop, and the board — which
has never seen those words and holds no vocabulary — starts answering with them.
That works only because of what M4 bought: the student was distilled into CLIP
ViT-B/16's embedding space, so a *text* vector from the same teacher is a point
in the same 512-d space as the device's *image* vector, and `cos(image, text)` is
a query. `m8.c` was already computing that cosine, pointed at the previous frame
instead of at a sentence.

| | |
|---|---|
| `model/evaluate.py` | `--emit-thresholds`, `--fpr`; the printed AUC table unchanged |
| `firmware/m9.c` | **new**, 700 lines — m8's three start-up checks, then receive, score, rank |
| `firmware/frame.{c,h}` | `ft_recv_exact()` made public; no behaviour change |
| `host/demo.py` | **new** — teacher, thresholds, framing, `--ask` |

**No RTL, no new bitstream, and D1 is not in it.** D1 is driven by the fabric
(`gemm_top.v:150-152`), so lighting it on a match needs a link command, a
register and a respin of both bitstreams plus a full `m7` ladder
re-verification. That is its own milestone. The verdict goes to the console.

**A set of queries, not one, and this is not a convenience.** Absolute CLIP
cosines sit in a narrow band — every number below is between 0.21 and 0.32 — so
one score against one threshold is illegible. Up to 6 resident vectors, all
scored every frame, ranked. The ranking is the product; the threshold only
decides whether to also say `MATCH`.

```
frame    41 :  book +1.35*  person +0.97  cup +0.87  laptop +0.81   MATCH book (cos 0.301)
```

**Wire format**, mirroring `ft_recv_bitstream()`'s framing:

```
"FGXQ" | len u32 LE | crc32 u32 LE | payload
payload = nq u32 | dim u32 | nq x { char name[24] | f32 z_threshold | f32 mean | f32 std | f32 vec[512] }
```

2084 bytes a record, and the first thing the board did with it was **refuse
it**: `queries : rejected - 8344 bytes for 4 512-d queries, expected 8360`. The
firmware had reserved 16 bytes for three floats. The host, the format comment and
the docstring all said 12. That rejection is verification 4 doing its job on the
very first run, and it is the argument for checking the length against `nq` and
`dim` rather than trusting the sender: a set that half-loaded would score against
whatever the other half used to be, which looks like a *wrong answer* rather than
a broken one.

**The frame loop takes a new set at any time.** The per-frame poll drains
buffered bytes through a static 4-byte shift register — static, because a 8 KB
write arrives as USB packets and a poll can land with two bytes of the magic in
the buffer and two still in flight. `--ask` re-encodes and re-sends to a running
board, and the demo becomes a conversation instead of a reflash.

#### The camera was a quarter turn out, and nothing said so

`FT_MOUNT_ROT`. The first pictures M9 rendered came out rotated, because the
module was being held a quarter turn to the left on the bench. Nothing in any log
could have shown this — a sideways tensor produces perfectly plausible cosines —
and it was found only by dumping a frame with `--snap` and *looking at it*. The
constant now lives in `frame.h` with `host/cam.py --rot` taking the same value,
and the comment says to check it against a rendered frame rather than the
datasheet, because the ribbon decides the orientation and the ribbon gets moved.

#### THE FINDING: COCO's negatives are the wrong background for one room

The thresholds came from `model/evaluate.py --geometry camera`: per query, the
mean and standard deviation of the int8 student's cosine over COCO's *negatives*,
and a z-threshold at a 10% false-positive rate. Standardising by COCO's negatives
is the obvious move and it is what the plan called for. It does not survive
contact with a bench.

Across five scenes — a blank whiteboard, a book's back cover, its front cover, a
wine glass, and a covered lens — **`laptop` led every single one**. Not narrowly,
and not as an artefact of standardising: it led on the *raw* cosine too (0.266
against `cup`'s 0.252 on the wine-glass frame). There is no laptop in any of
those pictures.

It is not a bug. COCO's `laptop` negatives are fields and food and dogs and
street scenes; every frame this camera has ever taken is an indoor desk in front of a
window, which is a fairly laptop-shaped prior. **The residue is a property of the
room, and COCO cannot know the room.** The 2 percentage points that made `laptop`
win are the difference between "not a laptop, compared to a dog" and "not a
laptop, compared to this desk".

There is exactly one place the room can be measured, and it is on the device.

```c
#define FGX_BG_TAU 200u                       // firmware/m9.c
```

Each query carries a running mean of its own cosine over roughly the last 200
frames, and the reported score is the deviation from that. Whatever produces a
constant per-query offset — scene prior, white balance, the student's own bias —
appears in both terms and cancels. `qsd` stays COCO's: only the *centre* was
wrong, the spread is a scale, one frame's worth of noise cannot estimate it, and
keeping it fixed is what leaves the thresholds meaning what `evaluate.py` said
they mean. The board prints the learned background periodically, so any `z` in a
log can be turned back into a cosine six months later:

```
background: after 8 frames, this room reads  cup 0.251 (COCO 0.241)  person 0.264 (COCO 0.264)
                                             book 0.251 (COCO 0.250)  laptop 0.266 (COCO 0.240)
```

There is the whole story in one line. Three queries land within 0.001–0.010 of
COCO. `laptop` is 0.026 high, and 0.026 was the entire margin.

**THE PRICE IS EXPLICIT, AND IT IS THE DESIGN CHOICE, NOT A SIDE-EFFECT.** This
measures *change*, not *presence*. Leave the same book in front of the lens for
200 frames and it becomes the background and its score decays to zero. That is
right for a demo where things are held up and taken away, wrong for a fixed
installation, and `FGX_BG_TAU` is where the choice lives. *(Superseded by
[M12](#m12--contrast-queries-and-a-background-that-stops-moving--2026-08-08-the-first-state-question-answered-backwards-and-reproducibly): the choice now
lives on the wire as `bg_tau` and `bg_hold`, the default freezes the baseline
after 30 frames, and `--bg-tau 200 --no-bg-hold` restores exactly what this
paragraph describes.)* A new query set resets
the estimate — index `i` now means a different sentence — so frame 0 after a
`--ask` is scored against COCO and is the one frame in a session that can still
show the old bias. It does, visibly, and then it is gone.

#### A deadlock that only `--ask` could reach

The endurance run stopped dead at frame 22, with no error on either end, seconds
after a live re-query. `ft_recv_exact()` times out at a second a byte and prints,
so the board could not have been stuck reading; it was stuck *writing*.

`host/demo.py` handed the port 4096 bytes at a time. A bitstream or a first query
set arrives while the board is sitting in a blocking read printing nothing, so a
big write streams straight in — which is why 4096 had worked all through M7 and
M8. A **re-query** arrives mid-loop, and then both ends are talking at once:
pyserial blocks until the device accepts all 4096, the CDC FIFO is 64 bytes and
the firmware only drains it between frames, so the host stops reading for the
~900 ms of a frame — and in that window the board fills its own TX buffer with
the frame line and blocks in `printf`. Neither side can move and neither side
times out. Only a USB replug recovered it.

512-byte chunks with a **non-blocking** drain between them. Non-blocking matters
twice: `pump()` waits out its full 0.5 s read timeout when the board is silent,
which across a 173 KB bitstream was 42 chunks of pure waiting, so the fix also
took **start-up from ~25 s to ~10 s**. A `write_timeout` is the backstop, so the
next one of these ends with a sentence rather than with somebody noticing.

**A protocol that works in every direction separately can still deadlock when
both directions are used at once**, and the two ends will be silent rather than
wrong.

#### What it does

Bench: an indoor desk in front of a window, daylight. `"cup" "person" "book"
"laptop"`, one book, held up and taken away.

| | `book` | runner-up | `book`'s rank |
|---|---|---|---|
| empty bench, f0–29 | +0.11 | `person` +0.28 | 3rd |
| **book held up**, f35–50 | **+1.22** | `person` +0.86 | **1st, `MATCH` ×7** |
| taken away, f62–84 | **−1.07** | `laptop` −0.64 | **last** |
| **held up again**, f85+ | +1.17 | `laptop`, level | 1st |

`book` swings −1.1 → +1.2 **in the single frame** the book comes back (f84 → f85).
The verdict tracks the world.

`laptop` is still a live competitor on the second presentation, and the reason is
visible in the table: bringing *anything* close to the lens lifts all four scores
together (at f41 the last-placed `laptop` is at +0.81). That is a brightness and
contrast effect, common to the whole set, and subtracting the frame's mean across
queries would remove it — but it is a constant shift, so **it would not change a
single ranking**. It would only make the `MATCH` threshold mean something
steadier. Noted, not done.

*Lens covered:* every score collapses from ~+2.5 to ~+0.5 in one frame, and then
the board stops answering altogether — `no usable frame off the camera` for 21
frames. Declining to score darkness is better than the negative control asked
for; what mattered was that the scores must not *freeze*, which is the pinned-
cosine fault M8c chased for 302 frames.

*Live re-query:* `book cup person laptop` → `banana umbrella clock dog` on a
running board, `2 query sets` in one log, no reflash.

*Queries the eval cannot back are still allowed*, because free text is the point.
They get the median calibrated threshold and the console says so next to the AUC:
`2 of 4 queries are below AUC 0.75 (person, book) - the demo will still rank
them, and the ranking is the part that holds up`.

### M10 — Take the tile off the link's clock ⬜ *closed: measured 70 MHz, and the prize is 32 ms*
**Rewritten 2026-07-31 because half of it lost its hardware, then closed the same
day because the other half was measured.** M10 was two ideas in one milestone: put
the operands on the FPGA's own bus — **two APS6404 QSPI PSRAMs** on the T8's
header, the option [rejected and half-taken back](history.md#rejected-fpga-alone-with-hyperram)
— and let the tile run its own loop on its own clock.

**The first half is withdrawn:** no off-the-shelf QSPI PSRAM breakout turns out to
be buyable, and this project has no way to fabricate one. **The second half is
closed on a measurement**, and the measurement is the useful part of the
milestone — five container builds, no board time, no firmware. What follows is the
whole of it, including the two errors this section carried for a day.

**The onboard PSRAM does not substitute, and the question is worth closing
properly.** U1 sits on the *MCU's* QMI, not on the FPGA's header, so operands
parked there would still cross the 3-bit link — which is the entire cost this
milestone exists to delete. It is also the wrong kind of help:
[M3](#m3--memory-bandwidth--answered-as-a-side-effect-of-m5) concluded PSRAM adds
capacity rather than bandwidth, the 2.23 MB of weights already fit in the 2 MB
stacked flash, and
[M5c](#m5c--make-u1-talk--closed-the-vendor-never-fitted-u1-on-purpose-and-never-tested-it)
never got a valid response out of the part. Adiuvo have said they will look for
the root cause after 2026-08-08 regardless of what this project does; **even a
fully working U1 would change nothing on this ladder.**

**Nor does anything on the FPGA.** The T8F49's whole on-die memory is 24 blocks
and `u_tile` already holds 21 of them, so no configuration of this device keeps
2.23 MB of weights FPGA-side. **Operand residency is off the table for this
board** — that is a property of the silicon now, not a scheduling decision.

#### Correction 1 — RUN's 314 ms is compute, not transport

This section first said RUN is "314 of the 644 ms of wire and it transmits no
information whatsoever", took the wire to 330 ms, and got a ~470 ms frame out of
it. **The premise is wrong.** RUN's idle bytes are not overhead that a second
clock deletes; they are the tile's cycle count, budgeted as such:

- `firmware/m7.c:490` — `sweep = K*QG*(P+6) + 512`. The host sizes RUN's idle
  bytes to be *exactly* the sweep the tile is about to perform, plus 512 clocks of
  slack per pass. `gh_run()` (`gemm_host.c:797`) turns that into
  `(sweep_clocks + 7)/8` bytes.
- M7h measured RUN at **23.24 Mclk over 314 ms = 74.0 MHz**, which is `link_clk`.
  Subtract the 1,856 × 512 = 0.95 Mclk of slack and the tile really does spend
  **≈22.3 M cycles computing**.

So giving the tile its own clock *at the same 75 MHz* deletes the bytes and keeps
the time — the MCU would idle-**wait** instead of idle-**clock**. The frame would
not move at all. **The entire prize is**

```
22.3e6 x (1/75MHz - 1/f_tile)
```

which is 0 ms at 75 MHz, 92 ms at 100 MHz and 137 ms at 125 MHz. Everything M10
was worth hung on `f_tile`, and **nobody had ever measured it** — the tile had
never been synthesized on its own.

**This is the fourth time this project has added a sum where the machine takes a
`max()`**, and the first time it happened in a projection rather than in a
measurement. [M7e](#m7e--use-the-other-core--1485--1292-ms-firmware-only) moved
work to core 1 and got ~50% of it. [M7f-2](#m7f-2--the-config-c-jumper-and-300-ms-that-arrived-as-zero)'s
jumper converted at 0%. [M7h](#m7h--the-weight-gather-and-the-packers-round-trip--config-c-975--917-ms-and-a-saving-that-converted-at-40)
converted at 40%. Each of those was caught by measuring afterwards; this one was
caught by reading the firmware while scoping the RTL, which is cheaper and is the
only reason the ~470 ms figure never reached a build.

#### Correction 2 — there was never a sequencer FSM to write

This section also called for a "self-sequencing tile" and costed "a sequencer FSM
and two FIFOs". The tile already sequences itself:

- `gemm_tile.v:92` — `input wire run, // one-cycle pulse`. The FSM walks the whole
  (k, g, p) loop from that single pulse and reports `busy = (state != S_IDLE)`
  (`gemm_tile.v:437`).
- `gemm_link.v:609-622` — `R_WAIT` already waits for `busy` to rise, then fall.

And the async FIFOs were not needed either. The three memories split cleanly along
the domain boundary (`gemm_tile.v:415-433`): `strip` is written by the link and
read by the tile, `wbuf` likewise, and `accram` is tile-only — two dual-clock
simple-dual-port RAMs and no extra blocks. M10's RTL was **clock separation plus
CDC**, which is smaller than what was written down, not larger.

#### Stage 0 — measure `f_tile` before writing any CDC

`rtl/tile_probe.v` wraps `gemm_tile` in a 168-bit LFSR (one flop per input bit, so
nothing is a constant and nothing folds away) and a two-stage XOR reducer
(shallower than anything inside the tile, so the report is about the tile).
`tile_probe.sdc` constrains `tile_clk` to 6.0 ns — deliberately unreachable, so
place-and-route keeps optimising instead of stopping at the first feasible
placement. The clock lands on B3, a global-clock ball, because under M10 it would
have come off the PLL.

The probe is only trustworthy if the tile survived synthesis, so that is checked
rather than assumed: `tile_probe.res.csv` reports **8 multipliers and 21 memory
blocks**, matching `gemm_top.res.csv` exactly.

**First result — 66 ± 3 MHz**, against a 110 MHz gate:

| seed | Fmax | worst path |
|---|---|---|
| default | 66.489 | `accram` RDATA → `dout[25]`, LL 2 |
| 2 | 69.823 | `accram` RDATA → `dout[24]`, LL 2 |
| 7 | 63.873 | `d_g[1]` → `d_g[2]` clock enable, LL 3 |
| 13 | 63.996 | `state[1]` → `d_g[1]` clock enable, LL 4 |

Below the 75 MHz the board already runs at. But **all four seeds named the drain
walk, never the MAC array** — and `gemm_tile.v` says why in a comment written
during M6: the readout is "a plain three-state walk and not a pipeline" because
the return path is one bit wide and the link spends ~32 clocks per word anyway.
That walk has ~30× slack in cycles and was spending it in nanoseconds. So the
first measurement was timing the readout FSM, not the compute loop, and it does
not answer the question.

**So it was measured again with the walk pipelined.** `gemm_tile` gained
`parameter integer DPIPE` (default 0, and 0 is what the shipped tops use) which,
at 1, splits the `accram`→lane-mux net with a register and resolves the three
end-of-loop compares one cycle early so the counter clock enables collapse from a
five-term AND to two. It costs one cycle per drained word, which is invisible
behind a 32-clock serializer.

| | default | 2 | 7 | 13 | mean |
|---|---|---|---|---|---|
| `DPIPE=0` | 66.489 | 69.823 | 63.873 | 63.996 | **66.0** |
| `DPIPE=1` | 67.015 | 71.439 | 71.195 | — | **69.9** |

**It worked, and it did not matter.** +3.9 MHz, which is inside the ±2.4 MHz seed
spread `build.sh` documents. The drain left the critical path in two of the three
seeds — and what replaced it is the answer.

#### The ceiling is the silicon, not the RTL

```
u_tile/wbuf|RDATA[6]    5.264 ns   memory block clock-to-out, at (21,82)
net u_tile/wreg[16]     6.802 ns   Manhattan 52
u_tile/mult_177|B[0]    2.716 ns   hard multiplier input setup, at (33,42)
                       14.782 ns   Logic Level 0
```

**Logic Level 0.** The weight memory's output goes down one wire into a hard
multiplier's input and there is no logic in between — nothing to pipeline, nothing
to retime, nothing to restructure. And the two fixed terms alone are

```
5.264 + 2.716 = 7.98 ns  ->  125 MHz with zero routing delay
```

which is an upper bound no RTL change can pass on this fabric. The 6.802 ns hop is
not reachable either: `u_tile` holds **21 of the T8F49's 24 memory blocks**, so the
placer has no freedom to move a weight store closer to one of the 8 multipliers,
and four seeds confirm it does not find one.

#### Verdict — closed

70 MHz reported. `gemm_top` reports 62.449 MHz and runs bit-exact at 75, so this
analyser carries ~20% pessimism on this device; call the real figure **~84 MHz**.
The prize is then

```
22.3e6 x (1/75MHz - 1/84MHz) = 32 ms  ->  917 to ~885 ms, 3.5%
```

against a PLL that has never been instantiated headlessly in this flow, two
dual-clock RAM conversions, three pulse synchronisers, a drain handshake FIFO, a
third top-level, a new bench with deliberately skewed clocks, and a firmware
change — days of work, all of it in the one area of the project with no test
coverage worth the name. **M10 is closed.** Not "deferred": the ceiling is a
property of the T8F49's memory-to-multiplier delay and the fact that `u_tile`
fills the device's block RAM, and neither will change on this board.

`DPIPE` stays in the tree at 0. It is the only way to reproduce the measurement
above, and its own comment block records that at 0 the resulting `gemm_top` is
*not* quite bit-identical to the pre-DPIPE netlist — 1743 → 1744 FFs, 930 → 931
LUTs, both attributed to the untouched `u_link`, RAMs and multipliers unchanged,
`link_clk` Fmax 62.449 → 64.737 (inside seed noise, and in the right direction).
All three benches still pass bit-exact on 10,560 accumulators. The flow was
verified deterministic first — the pre-DPIPE source built twice at seed 2 gives an
identical bitstream — so that 1-flop delta is a real re-decomposition and not
build noise.

#### What this leaves

**917 ms stands as the measured figure, and 314 ms of it is compute that this
board cannot speed up.** The consequences for everything downstream:

- **The `export.py` weight interleave is worth ~6 ms, not ~160.** That number came
  from a 330 ms wire that is not going to exist. M7h's verdict — not worth doing —
  was right and stays right.
- **The 265 ms MAC floor is not movable**, which the
  [performance model](#appendix-the-design-time-performance-model) treated as an open question. It is now
  a measured property: 8 multipliers, ~75 MHz, and a fabric that caps at 125 MHz
  even in the limit.
- **~470 ms and ~280 ms are both off the table bit-exact on this board.** The only
  remaining lever of that size is requantizing in fabric, which takes DRAIN's
  1.368 MB to ~0.36 MB and the wire to ~531 ms. Note what that does *not* buy:
  core 1 is busy 593 ms, so below ~593 the wire stops being the constraint and the
  frame lands near **~780**, not near the ~350 the ladder used to claim on top of
  M10's imaginary 330 ms wire. And it gives up the bit-exact float contract every
  milestone since M6 has been checked against. A deliberate change of contract for
  ~140 ms; not queued.
- **The next real work is M7b and the camera**, which is what the ladder should
  have said once the software ladder bottomed out.

Note this was never the Tier 2 rewrite, and closing it does not foreclose one. The
student, the tile and the arithmetic contract are all unchanged.

*If a PSRAM part is ever wanted again,* the fabrication objection is softer than
it looks: the APS6404 is a SOIC-8 at 1.27 mm pitch, hand-solderable onto a
SOIC-to-DIP adapter by anyone who has already soldered the configuration-C jumper,
and Microchip's 23LC1024 (128 KB SPI/SQI SRAM) ships in PDIP-8 and needs no
soldering at all. 128 KB is far short of the weights but would hold the activation
ping-pong. Neither has been priced or checked for stock, and neither is on the
critical path of anything above.

---

### M11 — D1 as a score meter ✅ *2026-08-07: the board says what it sees without a laptop*

M9 answers questions, but the answer only existed on a USB console. D1 — the RGB
LED at E1/F1/G1 — still said what it said during bring-up. M11 makes it a
**continuous** indicator: green when nothing matches, fading through to red as the
winning query's score rises. Not a threshold lamp, a meter. It is the only output
this device has that a person in the room can read.

M9 excluded D1 and said why: D1 is driven by the fabric, so host control needs a
new link command, a register, and **a respin of both bitstreams plus a full m7
re-verification**. That cost is identical whether the LED is binary or a gradient,
which is the reason to build the gradient.

#### The split, and why it is where it is

**The MCU sends two pre-mapped 8-bit duty bytes. The fabric owns only PWM and a
slew limiter.** Everything that gets re-tuned by eye on the bench — the clamp
range, which query drives it, the gamma curve, the red/green balance that R7 =
680 Ω against R8 = 360 Ω makes non-obvious — lives in C, where a change costs
`ninja` and a reflash. Everything in the fabric is a thing that will never change.

That split paid for itself before the bench test even ran. The RTL is
curve-agnostic by construction, so the respin never depended on the firmware
result, and both could proceed in parallel while the board was wedged.

The mode switch is what makes it safe: a sticky `led_own`, set by the first
`CMD_LED`. Until then D1 behaves *bit-identically* to before — green heartbeat,
blue link-seen, red sticky fault. m7 and m8 never send an LED command, so their
D1 behaviour is unchanged by construction, and every meaning in
[the bring-up log](bring-up-log.md) still holds for the whole of bring-up, which
is exactly when they are the only diagnostic there is.

#### A seed is not portable across netlists

The generalizable finding, and it nearly cost the feature. Re-running the shipped
`PNR_OPTS=seed=2` on the new netlist gave **59.934 MHz** against M10's 64.737 —
a 4.8 MHz drop, past the ±2.4 MHz noise band `build.sh` documents, and by the
plan's own rule a reason to back the change out.

It was not a regression. Three things settled it:

| | |
|---|---|
| the critical path never touches the LED logic | `u_tile/state[0] → u_link/tph[0]|CE`, pre-existing in both builds |
| `gemm_top_wide` is the control | the identical RTL delta moved it 58.630 → 58.555 MHz, which is nothing |
| re-rolling seeds | 3 → 61.904, 1 → 63.243, **4 → 63.922** |

The whole band moved down about 1 MHz and seed 2 went from the top of it to the
bottom. **Shipped: `gemm_top` seed 4 (63.922 MHz, slack −2.311 ns — better than
M10's −2.680), `gemm_top_wide` seed 2 (58.555 MHz, −3.745 ns).** So: re-roll three
or four seeds before believing any single number, and pin the seed *per top*
rather than globally. Recorded in `rtl/build.sh`.

It also changed what gets committed. `rtl/build/` is gitignored and `build.sh`
overwrites in place, which was an acceptable tradeoff only while the RTL was
believed to determine the image. It does not — a rebuild at the recorded settings
can hand back something meaningfully worse — so the verified images are now
tracked in **[`rtl/bitstreams/`](../rtl/bitstreams/)**, M10 and M11 side by side,
with their seeds, slack and sha256. About 2 MB of ASCII hex against the
alternative of rediscovering this under pressure.

As always, the design runs past its reported fmax and is bit-exact anyway — the
m7 ladder below is the proof, at a 75.0 MHz `link_clk`.

#### Verification

| gate | result |
|---|---|
| simulation, both widths | **PASS** — 6/6 vectors bit-exact, plus the new LED checks: legacy mode held, duties latched, slewing, over-long frame dropped |
| m7 ladder, both bitstreams, one boot | **`RESULT : PASS`** — all 8 layers bit-exact in all six modes of both link configurations, 512/512 embedding floats exact, on a camera frame. Config A 1,074 ms, config C **845 ms at 75.0 MHz** |
| m8, 50 frames | **PASS** — cos 0.995–0.999, no faults, start-up lines unchanged |
| m9, 90 frames | **PASS** — meter used its range: median heat 0.13, p90 0.53, crossover 58/53 at h = 0.51 |
| D1 by eye | **PASS** — glides rather than steps (the slew limiter is running); blue on at boot and out at the first frame (the mode switch); intermediate colours visible. Bright room |
| fault path | **PASS** — red *blinks* with green off, NOP clears it, meter resumes |

`FGX_LED_GAMMA` frozen at 2.2 and `FGX_LED_GTRIM` at 1.0. Gamma 2.2 on both
channels means the middle of the sweep is dim on both (0.5^2.2 = 0.217), so the
transition passes through a dark region rather than a bright amber; on this bench,
in a bright room, that was not a problem. If it ever is, lower the gamma — it is a
firmware constant, which is the entire point of the split.

#### Three corrections

**A deferred response is not an acknowledgement.** `gh_led()` has no return
payload, so `gemm_host.c:674` defers its response and `GH_OK` means *queued*, not
*acked*; a failure surfaces on the following call and outranks it
(`gemm_host.c:608`). An earlier write-up here read a clean `!led` column as "every
frame acknowledged with a good CRC" — it is weaker than that, and the frame number
is off by one. What actually proves the path end to end is the `'E'` hotkey, which
forces the deferred failure to land somewhere known: the malformed frame returns
`GH_OK`, and the *next* NOP reports no preamble with `bad_frame` set. Both are the
pass.

**`'F'` is the first byte of `"FGXQ"`.** The fault hotkey was `'F'` for one build,
and `poll_host()` tests hotkeys ahead of the magic's shift register — so it ate
the query set's first byte and the magic could never match. The board sat at the
query prompt while the host pushed 12 KB into a buffer nobody drained. The comment
two lines above already said `'B'` and `'R'` were chosen because neither appears
in the magic. It is `'E'` now, and that constraint is marked load-bearing.

**There are three USB wedges, not one**, and the README long claimed a physical
replug was the fix for all of them. See [the recovery notes](#recovering-a-wedged-board)
below.

#### Recovering a wedged board

| wedge | what you see | the fix |
|---|---|---|
| soft | the bitstream prompt, one dot a second | `m7.py --bootsel` / `bootsel.py`, which retry **`'B'` then a 1200-baud DTR touch** for 40 s. Worth knowing why it is a loop of two things: `ft_recv_bitstream()` swallows stdin, so `'B'` is eaten in this state, and [question 9](history.md#verify-before-building) records the 1200-baud touch as not firing on this board either. **Which of the two actually lands has never been isolated** — the loop was written to stop caring, and that is why it still works |
| hard | completely silent, still enumerated | **`uhubctl`**. The Apple internal hub reports `ppps`, so `uhubctl -l 2-1 -p 1 -a cycle` drops VBUS on the board's port alone and leaves the Digilent cable up. Nothing over the wire recovers this one |
| the flash path | `cp` to `/Volumes/RP2350` hangs uninterruptibly | **`picotool load -x`**. The BOOTSEL volume can go stale while staying in the mount table, and then every access blocks in the kernel — `cp`, a bare `ls` of the mount point, even `diskutil unmount force`. *Asking what state the board is in is itself what hangs*, which is what makes it confusing. picotool speaks PICOBOOT to the USB interface and touches no filesystem |

`host/bootsel.py` does all three in that order — cheap cooperative paths first,
power last, because a power cycle also drops whatever the FPGA was holding. A
power cycle leaves the T8 unconfigured, which costs nothing: every host script
re-sends the bitstream.

#### The caveat this makes visible

M9's background estimate means the meter measures **change, not presence**: a book
left in shot for `FGX_BG_TAU` = 200 frames becomes the background and D1 fades
back to green with the book still there. That is M9's documented choice and not a
new fault — but it used to be a number in a log and it is now a light in the room,
so it will be reported as a bug by someone. It is in the table above rather than
buried because that is the honest place for it.

*M12 fixed this half of it: the baseline now freezes after a short warm-up and the
book keeps its score. The constant is `FGX_BG_TAU_DEFAULT` = 30 and it is no longer
a constant — see below.*

---

### M12 — Contrast queries, and a background that stops moving ✅ *2026-08-08: the first state question, answered backwards and reproducibly*

The end goal is state alerts — *"the book was opened"*, *"water was poured into
the glass"*, *"the box is upside down"*. M9–M11 built a board that answers
**object identity** well. The first state question failed at the bench, and failed
*backwards*: a **closed** book lit D1 red, an **opened** book only orange.

M12 is the milestone that took that seriously instead of tuning around it. Two
changes, both cheap, and **no RTL change** — `ninja` and `picotool load`, no respin.

#### Root cause, established before writing any code

Six probe scripts, now in `tools/` because they are the evidence for everything
below and were otherwise going to be lost in `/tmp`:

| script | what it answers |
|---|---|
| `probe_open.py` | teacher vs student on the two labelled bench PNGs |
| `probe_noise.py` | the same axes over all 93 captured frames — the noise floor |
| `probe_states.py` | text geometry, plus val2017 AUC |
| `probe_states2.py` | train2017 AUC, negatives framed as "object present, not in state X" |
| `probe_negatives.py`, `probe_student_neg.py` | five negative strategies, teacher and student |

Fed **the same 128×128 board PNGs**, the teacher separates opened from closed on
the `spread-front` axis (**+0.0401** vs **−0.0291**) and the student does not
(**+0.0065** vs **+0.0052**, a 0.32 sd gap). On the `opened − closed` axis the
student is **anti-correlated at −4.67 sd**, with the open book the *minimum* of all
93 frames. Identical input, so this is **distillation capacity** — not resolution,
not the prompt, not int8, not the link, not the fabric.

**One methodology note that changed the answer.** The first run of `probe_open.py`
applied `distill.camera_transform()` to the board PNGs. That function takes a COCO
source and *simulates* the Mega's 4:3 crop and squash; the PNGs had already been
through the real thing, so it cropped and squashed a second time — and made the
student look **better** than it is. With the double-processing removed the
student's numbers got worse and started reproducing the bench exactly, which is
how the result became trustworthy. The comment is load-bearing and stays in the
file.

**A second bug, found by running the script rather than assuming.**
`probe_states.py` reported zero-negative rows as `good`. `auc()` returns `nan`
when one class is empty, and `nan < 0.65` is `False`, so glass (19 positives, 0
negatives) and door (19/0) fell straight through the verdict ladder to the best
verdict it has. COCO captions say "a glass of water" often and "an empty glass"
almost never. Now guarded explicitly on both class counts.

#### Change 1 — a query can say what it is *not*

```
uv run host/demo.py "an opened book / a closed book / a book"
```

Positive first, negatives after, and the board is sent
`normalize(e_pos − mean(e_neg))` — one 512-d vector like any other. **Board cost:
zero.** The record layout did not move a byte.

**Why the help text shows three terms and not two**, which is the
counter-intuitive part and the reason this needed measuring first. Five strategies
on the student, mean AUC over five state tests:

| strategy | mean AUC |
|---|---|
| raw prompt, no negative | 0.610 |
| vs the opposite state — *the obvious thing to type* | **0.609** |
| vs the bare object | 0.590 |
| vs an empty scene | 0.601 |
| **vs all three, averaged** | **0.646** |

Naming only the opposite state is the **worst** fixed strategy — worse than
supplying no negative at all — and on "a glass of water" it inverts the ranking
outright (teacher 0.617 → 0.371). Averaging several negatives is the only choice
that helps everywhere.

**And why `"nothing"` is not appended automatically**, tempting as that is: an
empty-scene negative helps book (+0.111) and glass (+0.061) and hurts pouring
(−0.117) and posture (−0.124). There is no safe default, so the user gets the knob
and the docs get the numbers.

**A wart removed on the way.** A difference axis is synthetic and will never be in
`thresholds.json`, so it would always have taken demo.py's *median of every other
query* fallback — and for a difference vector that is not an approximation of
anything: its cosines are centred near zero with a much smaller spread than a raw
prompt's, so the median `std_neg` would make z far too small to ever cross a
threshold. `model/evaluate.py --emit-embeddings` now writes the 5000×512 **int8
student, camera geometry** eval matrix and demo.py measures mu/sd against it
directly.

It has to be *that* matrix. The plan for this milestone said "compute mu/sd from
the cached teacher embeddings", and that is wrong: the board's cosines come from
the int8 student looking through the camera's squash, and the per-query offset
being cancelled is a property of **that** pipeline, not the teacher's. Stated
because it is the exact failure the calibration exists to prevent.

Stated too, because it is a real difference and not a rounding one: the table's
`mean_neg`/`std_neg` are over each query's **negatives**, and this is over **all
eval images**. For a COCO class those nearly coincide; for a contrast axis there
is no membership to define negatives with, so all-images is the only population
available. The table still wins wherever it has a row, and demo.py prints which
of the two it used.

#### Change 2 — the background stops moving

`bg_update()` already switched from a plain running mean to an exponential one at
`bg_n == FGX_BG_TAU`. **Hold is just: decline to take the second rule.** One
branch, no new state.

- `#define FGX_BG_TAU 200u` became runtime `bg_tau` + `bg_hold`, carried on the
  wire: `nq | dim` → `nq | dim | bg_tau | bg_hold`, `FGX_HDR` 8 → 16.
- Host: `--bg-tau N`, `--bg-hold` / `--no-bg-hold`. **`--bg-tau 200 --no-bg-hold`
  is M9's exact behaviour**, kept reachable by one flag.
- Two hotkeys, avoiding **F, G, X, Q** — `poll_host()` tests hotkeys ahead of the
  `"FGXQ"` shift register, which cost M11 a build. `'H'` toggles hold, `'N'`
  forgets the background and learns it again from now.

**Tau and hold are one change, not two.** Under hold, `bg_tau` is a *warm-up
length* and not an averaging window, so the default shrank **200 → 30** (~27 s at
0.9 s/frame). 200 frames is three minutes of standing still — long enough that a
book set down mid-warm-up gets absorbed into the baseline it was supposed to stand
out from. `bg_tau = 0` under hold is legal rather than degenerate: `bg_n` never
leaves 0, `zscore()` keeps using COCO's `qmu` forever, and that is M8's behaviour
as a control.

**`'N'` exists because the plan's mitigation was wrong.** The approved plan listed
"`'R'` resets the baseline" as the escape from a background frozen around the
wrong scene. Grepping `poll_host()`'s call sites: `'R'` **reboots**, `'B'` is
BOOTSEL, and before M12 there was no way to reset the baseline short of re-sending
12 KB of query set. `'N'` makes the mitigation real.

#### The flag day, made loud on purpose

The header grew, so an old host against new firmware is a hard incompatibility.
Every rejection in `recv_queries()` is a print and a `false`, never a partial
accept, so the existing length check already turns it into one legible line — and
it now carries a hint naming the cause. Verified against the pre-M12 `demo.py`
checked out of git:

```
queries   : rejected - 2092 bytes for 1 512-d queries, expected 2100
            that is exactly the M9 payload, 8 bytes of header short. Update host/demo.py.
```

#### The bench, 2026-08-08

`--snap-every 20` throughout, so every number below has the image that produced
it. 300 frames, one query set, room lit by a window.

**Hold works.** Pass 1, closed book placed into an empty-scene baseline at frame
128 and left alone: the contrast score held **+0.704 over 40 frames** and did not
decay — first ten frames **+0.640**, last ten **+0.733**. Pass 2's frozen baseline
sat at **+0.019 ± 0.182 over 130 frames** on a static scene. Under M9's tracking
background both would have drifted toward zero.

**The state question separates perfectly, and backwards.** Pass 2, baseline frozen
on the empty desk, then two open/close cycles:

| phase | frames | contrast axis | `book` |
|---|---|---|---|
| baseline — empty desk, the frozen scene | 130 | **+0.019** ± 0.182 | +0.044 |
| book **opened**, illustrated page | 30 | **−2.443** ± 0.181 | +1.054 |
| book **closed**, front cover | 60 | **−1.327** ± 0.126 | +1.266 |
| book **opened**, text pages | 31 | **−2.095** ± 0.242 | +0.662 |

**AUC(opened > closed) = 0.000** over 61 × 60 frame pairs. Every opened frame
ranks below every closed frame. Not chance — *perfect separation with the sign
reversed*, in two independent cycles, exactly as `probe_open.py` predicted offline
at −4.67 sd.

That is a better result than the offline AUCs suggested (0.58–0.75) and it is
still **not a detector**, for a reason the same table gives: picking the book up
and putting it back down moved the axis **−1.33** (baseline → closed), against the
**≈ −1.0** that opening it is worth. *The nuisance is the size of the signal.* A
sign flip would score 1.000 on this bench and would be a constant fitted to one
book in one room, which is the thing this project keeps refusing to do.

The generalizable finding: **the distinction is present in the student and
linearly separable; what is wrong is the direction of CLIP's text axis for it.**
Prompt engineering cannot fix a sign, and neither can a bigger negative set. The
path is a change/event formulation, which is the next milestone and not this one.

#### Three smaller things the bench turned up

**The tally regex ate multi-word names.** `MATCH (\S+)` stops at the first space,
so the run summary reported `MATCH an` for `an opened book~`. It had been
mis-tallying `wine glass` as `wine` since M9; the contrast mark is what made it
visible. Now anchored on the `(cos` that `m9.c` always prints.

**The board dropped off USB entirely** between `picotool load -x` and demo.py
opening the port — not the silent wedge, gone from the bus. `uhubctl -l 2-1 -p 1
-a cycle` brought it back, and `host/bootsel.py` recovered the silent wedge twice
more without hands on the board. The recovery table in
[M11](#recovering-a-wedged-board) covered all three cases; it is worth saying that
it did, because this is the first session that leaned on it end to end.

**A 22.7 s flash is the gate.** [Question 9](history.md#verify-before-building)
records `picotool` reporting success in ~4 s having written nothing. Every load in
this session was timed.

#### Deferred, and recorded rather than quietly dropped

**`qsd` is still COCO-derived while `qbg` is live.** `zscore()` is
`(cos − qbg[i]) / qsd[i]`: the running background supplies the **mean**, and the
**scale** still comes from COCO negatives. A running variance would close the
asymmetry and is deliberately **not** in this milestone — variance collapses
toward 0 on a static scene, so z explodes, and the floor that fixes it becomes
another bench-tuned constant. Keeping M12 to one change per axis preserves the
ability to attribute cause. It is in the README's open questions.

---

### M13 — The embedding, read back in words ✅ *2026-08-08: the student never says "book"*

M12 ended with a number — AUC 0.000, the two book states perfectly separated
and inverted — and a suspicion that could not be checked with numbers: what is
the student's 512-d vector *actually* about? This milestone answers that in
English, and the answer turns out to be worse than M12 assumed.

**No RTL change, no bitstream respin.** One hotkey, one new host script, one
new cache. Two flashes, both 22.7 s.

#### Why retrieval and not a caption model

The obvious build is a decoder — [ClipCap](https://arxiv.org/abs/2111.09734),
[DeCap](https://arxiv.org/abs/2303.03032) — that generates a sentence from the
embedding. Retrieval was chosen instead, for a reason worth stating because it
also bounds what this tool can ever be:

**A caption is strictly downstream of the embedding.** Ten tokens against 512
dimensions at full precision. Any signal that survives into the words was
already in the dot product the board computes, so a caption can never reveal
something the cosine missed. M12 had *already measured* that the vector
separates opened from closed perfectly; the fault was in which way the text
axis points, and a text decoder sits on the broken side. This is an
interpretability readout, and calling it anything else would be a category
error.

Given that, generation buys nothing over retrieval and costs a model. And one
bank needed **no building at all**: `emb_val2017_*.npy` is 5000 COCO images the
teacher already embedded, and COCO ships five human captions per image, so the
nearest neighbours arrive with English attached.

#### The hub problem, which nearly made this ship broken

The first working run looked plausible and was meaningless. Four completely
different frames — an open book, a closed book, a close-up, an empty desk —
retrieved neighbours at cosine **0.87–0.90**, and `000000051598.jpg`, *"a black
trash bag in a restroom next to a sink"*, was top-1 for **three of the four**.

The cause is geometry, not the pictures:

| measurement | value |
|---|---|
| norm of the bank's mean direction | **0.738** |
| cosine between two random COCO images | 0.544 |
| cosine among the four bench frames (student) | 0.89 – 0.96 |
| query-to-bank spread, before | 0.685 ± 0.080 |
| query-to-bank spread, after centring | 0.007 ± 0.181 |

CLIP embeddings occupy a narrow cone; roughly three quarters of any vector is a
shared "this is a photograph" component carrying no information about *which*
photograph. Rank on that and one image becomes everyone's nearest neighbour.
Centring on the bank mean fixed it and changed three of the four top-1s.

Worth noting that **the board already does this** — `zscore()` is
`(cos − qbg[i]) / qsd[i]`, subtract what this scene scores anyway. The host
readout was simply missing the same move.

The second bank, caption *text*, needed the same fix applied correctly and got
it wrong first: the image query was centred on the **text** bank's mean.
Symptom, on the *teacher* as well as the student, which is what made it
obviously a bug rather than a finding — the top hits for every frame were four
long "a photo of X on a Y background" captions. Each side must be centred on
its **own** modality's mean; that is what the modality gap is.

#### The result

Same open-book frame, same 128×128 board capture, both readouts:

| | teacher | student (what the board holds) |
|---|---|---|
| nearest-image consensus | `desk(9) table(7) sitting(6) laptop(5) books(5)` | `sink(18) bathroom(14) white(9) mirror(7)` |
| nearest caption | *"A woman holding a book about the topic fun."* | *"A phone mounted to a wall next to a doorway."* |
| 2nd | *"A person holding a book with a bird sitting on the book."* | *"A room in a home with a chair set up against the wall."* |
| 3rd | *"A manual or book about ten-speed bicycles"* | *"View of a public men's room with a cot on the side."* |

The teacher says *book* three times out of three, on the closed frame too, and
does **not** say it on the empty desk — so the readout discriminates. The
student never says *book* on any frame. It recovers the room — indoor,
furniture, a corner, a window — and loses the object on the desk.

**This reframes M12.** That milestone concluded the student could not answer the
*state* question. It is worse than that: on these frames the student does not
represent the *object*. The `book` query still scores +1.27 against a frozen
background, and that is not a contradiction — a z-score against a frozen
baseline measures **change**, and the change is real. But in absolute terms the
vector does not live near book photographs. Those two facts had been
indistinguishable until there were words to read.

#### The bug the verification caught

The firmware defers `'V'` by one frame exactly as `'P'` does, so the image and
the vector describe the same capture. The first build did not: `poll_host()`
returns on the first key it recognises and each handler ended in `continue`, so
`demo.py --emb`'s two-byte `"PV"` write left the `'V'` in the buffer until the
next iteration. **Image at frame 26, vector at frame 27** — off by one, with
both dumps healthy and both CRCs matching. The comment in the source asserted
the pairing held while the code did not. Fixed by draining the keys in a loop;
re-measured, both now land on 26 and both on 51.

`host/cam.py` also had to learn to skip the `m9emb` tag. Sharing the BEGIN/END
envelope is deliberate — one emitter, one parser — but 512 floats rendered as
RGB565 produce a plausible violet PNG that means nothing, which is worse than
an error.

#### Verified

| gate | result |
|---|---|
| flash | 22.697 s, twice — the real write, not the 4 s phantom |
| dump integrity | 2048 bytes, CRC matches announced, all finite |
| it is a live frame | cos between consecutive dumps 0.998825, not 1.0 |
| P/V pairing | image and vector both frame 26, both frame 51 |
| readout discriminates | teacher says *book* on both book frames, not on the empty one |
| `cam.py` on a vector-only log | says so and exits 0, writes no PNG |

---

### M14 — int4 weights, and the packed MAC closed without building it ✅ *WGT 6.66 → 3.76 Mclk on the board; 121 ms of frame in config A, 35 in config C*

`tools/probe_int4.py` measured int4 weights as free: with MSE-searched
per-channel scales and `conv0` + `head` pinned to 8 bits, both students retain
exactly what int8 retains — **94%** shipped, 91% `so400m-full-a05` — at 4.38
bits per weight. int3 is not close (59% / 55%). That opens two doors, and this
milestone's first job was to find out that one of them is bricked up.

#### Stage 0, P1 — two MACs per multiplier: **NO-GO, 34 memory blocks against 24**

`rtl/gemm_tile.v`'s header has said since M6 that two int8 products cannot share
an 18×18 multiplier. That is right — `|255·128|` needs 16 bits and 18 cannot
give 16 bits of field separation. **At int4 the arithmetic changes and the trick
works.** With `|a| ≤ 255` and `|w| ≤ 8` the product `|a·w| ≤ 2040` fits in 12
signed bits, so `B = w_lo + (w_hi << 12)` separates cleanly:

```verilog
wire signed [26:0] p_j  = a_q * b_j;
wire signed [11:0] lo_j = p_j[11:0];
wire signed [14:0] hi_j = $signed(p_j[26:12]) + p_j[11];   // + the borrow
```

Verified exhaustively over a ∈ [−128, 255] × w_lo, w_hi ∈ [−8, 7] — **98,304
combinations, 0 mismatches**. `b` needs 17 of the 18 available
bits and `p` needs 24 of 27. So 16 MAC/cycle is arithmetically real, and it would
halve RUN's 313 ms.

**It dies on memory, and the number is Efinity's rather than an argument.**
`tile_probe` was built at `APACK=2, WNIB=1, ADEPTH=128` — 128 × 512 is the same
2048 accumulators as today's 256 × 256, so the only question was whether it is
also the same number of blocks:

```
ERROR    : The following block types exceed device resources:
INFO     : 	memory          : 34/24 resources used
ERROR    : Device does not have enough resources for this netlist.
```

| array | control (`DPIPE=1`) | P1 (`APACK=2`) |
|---|---|---|
| `accram` | 13 | **26** |
| `strip` | 4 | 4 |
| `wbuf` | 4 | 4 |
| **total** | **21 / 24** | **34 / 24** |
| `EFX_MULT` | 8 | **8** |

`EFX_MULT` held at 8, which is the part worth keeping: the field split *did* map
onto the same eight hard multipliers exactly as designed. Nothing about the
arithmetic failed. The route dies on `accram` alone.

**The escape hatch that had to be measured.** Trion memory blocks have
configurable aspect ratios, so the obvious rescue was that a shallower array
might be allowed to be wider — if a 5 kbit block could be 128 × 40, then 128 ×
512 would cost the same 13 blocks as 256 × 256 and P1 would have been *feasible*.
That could not be settled by arithmetic, which is why this build happened instead
of a paragraph. `tile_probe.place.rpt` answers it directly: every one of the 26
`accram` blocks is instantiated **20 bits wide**, at depth 128.

```
| u_tile/accram__D$p12 | SDP  |     20     |     20      | READ_FIRST |   false    |
```

**20 bits is the cap, and it does not move with depth.** At 256 deep a block
holds 256 × 20 = 5120 bits and is full; at 128 deep it holds 2560 and half of it
is wasted. The cost of an accumulator array is `ceil(width / 20)` blocks and
nothing else — 512 / 20 = 25.6 → 26. Halving the depth bought exactly zero. So
this is not "P1 missed by 10 blocks and a narrower accumulator might save it":
even a 24-bit accumulator needs 384 bits and 20 blocks, still 28 in total. The
packed route needs a bigger device.

Recorded in M10's form — the code stays in the tree behind `APACK`, defaulting
to 1, as the evidence that the arithmetic was sound and the fabric was not.
`make tb_gemm` still passes 10,560 accumulators bit-exact, so `gemm_top` and
`gemm_top_wide` are untouched by the parameterisation.

#### Stage 0, P2 — nibbles in `wbuf`: measured **GO**, then **overturned by conv0**

P2 stores weights as nibbles, so `wbuf` narrows from 64 bits to 32 and the tile
reads 4-bit weights directly. `tile_probe` said don't:

| seed | control | P2 | delta |
|---|---|---|---|
| default | 67.015 | 66.238 | −0.777 |
| 2 | 71.439 | 70.862 | −0.577 |
| 7 | 71.195 | 64.300 | −6.895 |
| 13 | 73.041 | 69.711 | −3.330 |
| **mean** | **70.672** | **67.778** | **−2.895 (−4.1%)** |

Four seeds out of four negative, which is suggestive — but the control's own
spread is 6.0 MHz and `build.sh`'s standing rule is that under ~3 MHz is noise.
More to the point, **the probe is the wrong instrument for this question.** P2's
payment is Fmax and its purchase is two memory blocks, and blocks only buy
anything where the placer is short of them. `tile_probe` has 21 of 24 and no
congestion worth the name; `gemm_top` has 21 of 24 and a second clock domain,
gemm_link's framing, and the LED and flag dividers competing for the same
fabric. So `WNIB` was plumbed through `gemm_top` (default 0, bit-identical) and
the comparison rebuilt on the design that actually ships:

| seed | control | `WNIB=1` | delta |
|---|---|---|---|
| 2 | 59.934 | 62.952 | **+3.018** |
| 4 | 63.922 | 62.865 | −1.057 |
| 7 | 64.416 | 64.454 | +0.038 |
| **mean** | **62.757** | **63.424** | **+0.666 (+1.1%)** |

**The regression does not replicate.** In the congested design nibble storage is
a wash on Fmax — up on two seeds of three — and it returns `accram` + `strip` +
`wbuf` from **21 blocks to 19**. The probe's −2.9 MHz was an artefact of an
uncongested placement, which is exactly the failure mode `tile_probe.v`'s own
header warns about in the other direction ("the probe is smaller than
gemm_top_tclk will be… the placer has room this design will not have").

On the numbers, P2 wins: two blocks for nothing. **P2 was then chosen, and it
was the wrong answer, and no amount of further measurement would have said so.**

One consequence of the parameterisation for the record before the correction:
`gemm_top` seed 4 now reads **63.922 MHz**, not the 62.449 MHz on file, because
adding the parameter changed the netlist. That is the third time this repo has
confirmed a seed is not portable across a source edit, and the reason every
table above is paired against a control built from *this* tree at the *same*
seed.

#### Stage 0, corrected — **P3 ships**, because `conv0` is 8-bit and runs on the tile

The gate asked which of two storage forms is cheaper and got a clean answer. It
never asked whether either one can hold the weights it has to hold.

`ends8` pins **`conv0` to 8-bit weights** — that is the whole reason the int4
result clears the accuracy gate at all, and it is recorded three sections up.
`frame.c:641` offloads **all eight convolutions**, `conv0` included. P2 narrows
`wbuf` to `4*NMAC` = 32 bits. A 32-bit weight word physically cannot hold
`conv0`'s eight 8-bit weights, so a P2 bitstream cannot run the layer set this
board runs. The two blocks and the +1.1% are real and they are unavailable.

Both ways of rescuing P2 are worse than the thing it was rescuing:

- **Store `conv0`'s bytes as two nibble-words each.** Doubles `WDEPTH` for the
  8-bit layers, which spends the two blocks it just saved and then some, and
  puts a second word-assembly path on the `wbuf` read side — the Logic-Level-0
  path M10 measured, which is exactly where P2's Fmax case was that nothing
  lands.
- **Feed `NMAC` lanes over two cycles for 8-bit layers.** Halves the weight load
  rate for `conv0` to buy nothing, and adds a second `S_LOAD` timing to a
  sequencer whose cycle budget `frame.c` computes from the outside.

So the tile keeps its 64-bit `wbuf` and expands nibbles **at `wgt_we`**, under a
runtime `cfg_w4` from a spare bit of the CFG payload. Two sign-extended bytes
shift in per wire byte and a weight word completes in `WBYTES/2` bytes instead of
`WBYTES`; everything from `wbuf`'s read port onward is bit-for-bit the design
that has shipped since M6b, which is why `tb_gemm` needed no second golden arm.
The write side runs at one eighth of the sweep rate and has never been near
critical.

**The wire format is identical for P2 and P3**, which is the one piece of luck
here: the host half of Stage 1 was already written against it and none of it had
to change. What it cost was the two blocks, the Fmax, and an afternoon of
synthesis — and the general lesson is the cheap one to state and the expensive
one to learn, that a gate answers only the question you put to it. The question
"which is faster" had an answer. "Which can run `conv0`" was never asked, and it
was decidable from `frame.c` and the accuracy table without building anything.

`WNIB` is gone from `gemm_top`. It survives in `gemm_tile` and `tile_probe`
because `APACK=2`'s NO-GO needs it to elaborate, and that gate stays
reproducible.

#### Two tooling notes, both of which cost an hour

**`efx_map --top-params` cannot be reached from `efx_run.py`.** `--map_opts`
takes `nargs='+'`, and argparse treats any value starting with `-` as the next
option; `--map_opts=--top-params=...` gets past that layer and fails identically
at `efx_run_map.py --opt`; a leading space survives both and arrives at `efx_map`
as the single token `-- --top-params=...`, which boost::program_options rejects.
`build.sh` now rewrites the parameter defaults in the *staged* `/tmp` copy, which
is what `--top-params` would have done, leaves the repo tree untouched, and
echoes each rewritten line into the build log.

**`sed -i` on a bind-mounted file breaks the mount.** The first P1 build failed
with `[EFX-0010 VERI-ERROR] cannot open Verilog file '/work/tile_probe.v'` for a
file that was plainly there and that an identical second container read without
complaint. `sed -i` renames a new inode into place and virtiofs caches by inode.
The write is now `sed > tmp; cat tmp > file`, which truncates in place and keeps
the inode. A misleading failure — it reads as a source error and is a filesystem
one.

#### Stage 1 — int4 weights end to end: **verified on the laptop, not yet on the board**

Everything below was settled without touching hardware, which is the whole
reason the plan ordered it this way — including both respins, which are a laptop
job even though their output only means anything on the board. The rows that are
**not done** are the two that need the board in front of someone: `m7` bit-exact
in six modes of two link configurations, and the frame time measured beside its
control. Those are the only claims about M14 that will be about speed.

| gate | result |
|---|---|
| `test_gemm_plan` framing against M6c's measured lengths | PASS |
| `test_gemm_wire` including the new CFG bit position | PASS |
| `tb_gemm` — the tile alone | PASS, 10,560 accumulators bit-exact |
| `tb_gemm_link` — 1 forward lane | PASS, 10,560 bit-exact |
| `tb_gemm_link_wide` — 3 forward lanes | PASS, 10,560 bit-exact |
| `test_encoder_fast` vs `encoder.c` and vs numpy | PASS, 2048/2048 both |
| `probe_int4.py --run train2017`, `int4 mse ends8` | 59/67, **0.886 mean AUC, 94% retention, 4.38 bits/w** |
| the exported blob's per-layer widths | `FGX5` v2, 780,720 B, `8 4 4 4 4 4 4 4 8` |

The plan named `evaluate.py` for the accuracy gate and that was the wrong tool:
it quantizes on the fly from the checkpoint and reports **fp32 and simulated
int8 only** — 59/67 and 0.899 for both, which is a true statement about a model
this board is no longer running. Nothing in it reads `weights.bin`. The row that
had to be reproduced is `probe_int4.py`'s, and it comes back at 0.886 / 94% /
4.38 bits with `cos-fp32` 0.9801 against the 0.9800 on file. What ties that to
the artifact is the export itself: same `pick_w_scale()` MSE search, same
`pin_to_8()` policy, and a blob whose descriptors carry exactly the widths the
probe's `ends8` row pins.

The three simulations are the load-bearing ones, and what makes them so is that
the same six cases cover both widths in one design: `conv2`, `conv5` and `conv7`
run as nibbles and `conv0` runs as bytes, against golden values from
`fgx_conv_acc()` on the shipped blob. That is precisely the mixed-width property
P3 exists for, and it would have failed on a P2 bitstream.

**The wire saving is 139 → 53 ms in configuration C**, `gp_block_cost()`'s
model, against a predicted ~46 ms off a 93 ms base. WGT is now the smallest data
component of the frame.

Two things that were wrong and are worth recording, because neither announced
itself:

**The numpy golden was a more accurate pipeline than the one that ships.**
`run_int()` accumulated the requantize epilogue in float64 and pooled with
`x.mean()`; `encoder.c` is float32 with a sequential pool. `(acc + bias)` needs
26 bits and float32 carries 24, so the reference rounded once where the target
rounds twice. **They agreed bit-for-bit at int8 by luck, not by construction.**
int4 makes `mult` coarser, coarser means more ties, and one of four test images
found one — a single output code off by 1, 507 of 512 embedding floats
different, `1-cos = 1.3e-06`. The fix is not a tolerance: `run_int()` now models
float32 in `encoder.c`'s evaluation order, which took int4 to `1-cos = 1.11e-16`
and int8 to exactly **0.00e+00**. A latent fragility that predated M14 and would
have surfaced at the next thing that perturbed a rounding.

**`gp_block_parts()` charged `gb_weights()` per output byte.** At 4 bits the
byte count halves and the weight count does not — the nibble arm halves the
strided stores and does two extracts per store — so the cost model quietly told
`gp_choose()` that a 4-bit layer's stream was half as expensive to build.
Charging per weight restores the recorded int8 figure of **263 ms** exactly,
which is the invariant that should hold: int4 moves fewer bytes and builds the
same number of weights.

Two smaller notes. The nibble unpack in `encoder_fast.c` must be **byte at a
time**: a nibble at a time cost 19% of the tuned kernel's host frame (8.3 →
9.9 ms), the paired-byte version costs 4–5% (8.6 ms against an 8.2 ms int8
control built from the same tree). And the synthetic `sat 255x-128` vector case
is now pinned to 8 bits, because at 4 the blob byte `0x80` reads as the nibble
pair `(0, -8)` and `K * 255 * -8` no longer reaches the accumulator extreme the
case exists to reach — there is no int4 version of a widest-product test, so the
8-bit one stays.

#### Both respins: **P3 costs 7 LEs, no memory blocks, and no Fmax**

Four seeds per top, because a seed is not portable across netlists and this
netlist has certainly changed — `cfg_w4` threaded from `gemm_link` into
`gemm_tile`, and the nibble expander at `wgt_we`. `link_clk` Fmax, C2 model:

| seed | `gemm_top` before | `gemm_top` P3 | `gemm_top_wide` before | `gemm_top_wide` P3 |
|---|---|---|---|---|
| 1 | — | 63.894 | — | 58.086 |
| 2 | 59.934 | **62.066** | 58.555 *(shipped)* | **59.214** |
| 3 | — | 57.817 | — | 55.717 |
| 4 | 63.922 *(shipped)* | **64.733** | — | **59.916** |
| 7 | 64.416 | — | — | — |

Read the shared seeds, not the means: the two seed sets are different sizes and
a mean over different rolls is not a comparison. **Every seed the two netlists
have in common came out faster after M14** — `gemm_top` seed 2 +2.13 MHz and
seed 4 +0.81, `gemm_top_wide` seed 2 +0.66. Three for three is not proof of an
improvement — the whole point of the seed band is that a single roll under
~3 MHz is noise — but it is a clean statement that P3 did not cost anything, and
the reason it did not is structural rather than lucky: the expansion sits at
`wgt_we`, one eighth of the sweep's rate, and the read side that M10 measured at
Logic Level 0 is bit-for-bit unchanged.

Both tops now ship **seed 4** (`gemm_top` 64.733, `gemm_top_wide` 59.916);
`gemm_top_wide` moves off seed 2, which happens to be the roll it has shipped
since M7f.

Resources: **21 / 24 memory blocks and 8 / 8 multipliers in both**, unchanged,
which is exactly the claim P3 makes — the storage was never touched. Logic
elements 2,606 → 2,613 in `gemm_top` and 2,596 → 2,616 in `gemm_top_wide`, so
between 7 and 20 LEs out of 7,384 for the expander, the width mux and the CFG
bit. The 2 memory blocks P2 would have freed remain unavailable, and the two
rows above are the price of not having them: nothing.

#### On the board

Run against an int8 control, which needed `export.py --out` first: weight width
is a property of the exported blob (`d->wbits`), not a runtime toggle and not a
bitstream parameter, so the control is a second export plus a second firmware
rather than a mode. Both halves run the **same** M16 bitstreams at
`GP_KPACK = 1`, so the only difference between them is `--wbits`.

The two runs are different boots, which the M15 → M16 pair was not. That would
be a weakness if the milestone's claim were about the frame, but three of the
four commands are untouched by weight width and serve as internal controls -
**`ACT` 5.37, `RUN` 16.26 and `DRAIN` 3.02 Mclk, identical to the clock in both
runs.** Only `WGT` moves. Configuration C, rq on, the `+priorities` mode:

| | int4 (`--ends8`) | int8 control |
|---|---|---|
| `WGT` | 82 ms / **3.76 Mclk** | 102 ms / **6.66 Mclk** |
| `ACT` / `RUN` / `DRAIN`, Mclk | 5.37 / 16.26 / 3.02 | 5.37 / 16.26 / 3.02 |
| `W1_HI`, core 1's builds | **284 ms** | **290 ms** |
| bytes moved | 10.244 MB | 11.281 MB |
| config A frame, rq | **718 ms** | 839 ms |
| config C frame, rq | **569 ms** | 604 ms |

Both PASS: all 8 layers bit-exact in all six modes of both link configurations,
512/512 embedding floats exact.

**The wire number closes to the byte.** Weight payload is 1,142,784 B at int4
against 2,230,272 at int8 - only conv0 is pinned, the "head" `--ends8` also pins
being L8, a 1x1 linear outside the eight convs the tile runs. The measured
2.90 Mclk between them over 1,087,488 bytes is **2.667 clocks per byte, which is
8 bits over three data lines exactly**, and the residue is ~384 clocks of
framing on each of 1,856 transactions. Nothing is unaccounted for.

**And `W1_HI` did not move: 284 → 290 ms, +2%, across a doubling of weight
bytes.** That is the cost-model correction above, measured rather than asserted
- `gb_weights()` is charged per weight and not per byte, so int4 was never going
to buy build time. It is also the sharper statement of the same thing: the
builder reads half as much flash at int4 and does an extract-and-pack the byte
arm does not, and the two cancel to within 2%. **The builder is bound by the
permutation it performs, not by the bytes it reads**, which is what says where
M17 has to aim.

So int4 is worth **121 ms of frame in configuration A and 35 ms in
configuration C**, from one and the same blob. The narrow link is where it pays,
because what it removes is wire bytes and configuration C's wire is three times
faster. This is the third reading of the rule this repo keeps rediscovering, and
the first one in the shrinking direction: **a component saving is worth face
value only until it uncovers the next thing, and the wire getting faster can
take value back off a saving already banked.**

---

### M15 — requantize in the tile, DRAIN at int8 ✅ *DRAIN 153 → 41 ms on the board, config C 798 → 631*

M14 finished the weights. What is left on the wire is not weights: it is
**DRAIN**, and DRAIN is now the single largest line in the frame. `make -C rtl
test_plan` prices config C at **617 ms with DRAIN 161**, and the arithmetic
behind that is exact rather than modelled — 174 blocks × `P·Q = 2,048`
accumulators × 4 B = 1,425,408 B, and at the measured 8.94 MB/s that is 159.4 ms.
The return path is **one lane and physically unwidenable** (GPIO6 has no
contiguous neighbour), so no faster MAC touches it. The only lever is **sending
fewer bytes**, and the bytes are int32 accumulators that the MCU immediately
turns into single codes — `frame.c`'s `scatter()` throws 24 of every 32 bits
away, 356,352 times a frame.

Moving the epilogue into the fabric means the fabric has to compute it, and
`fgx_requant()` is `(float)(acc + bias) * mult`. Reproducing IEEE-754 in 24 LEs
is three times the RTL with round-half-even correct in three separate places.
The alternative is to **change the contract to fixed point** — and that decision
is a laptop measurement, not a synthesis run, which is what Stage 0 is.

#### Stage 0 — the fixed-point contract: **GO, retention unmoved at 94%**

```c
#define FGX_RQ_MBITS 18
M = round(mult * 2^s)  in [2^17, 2^18)
code = clamp(((acc + bias) * M + 2^(s-1)) >> s, 0, 255)
```

This is not an approximation of the float path — it is a *different* one, and
strictly the more accurate of the two: `(acc + bias) * M` is an exact integer
product where `(float)(acc + bias) * mult` rounds twice, once fitting the
accumulator into a 24-bit mantissa and once on the product. So "how far from
float" is the wrong question asked alone; the question is whether retention
moves.

**`s` is a closed form, not a search.** `frexpf` splits `mult` into `f · 2^e`
with `f ∈ [0.5, 1)`, so `f · 2^(e+s)` lands in `[2^17, 2^18)` exactly when
`e + s = 18` — hence `s = 18 - e`, no loop and no comparison. Both scalings are
by powers of two and so exact, and `v + 0.5f` for `v < 2^18` needs 20 mantissa
bits against float32's 24, so the whole pick is exact in single precision and the
RP2350 never calls a soft-float double. Checked against the obvious alternative —
grow `s` until `mult · 2^s` clears 2^17 — on **all 1,568 exported channels, 0
disagreements**, and again on the real data path in C, where both forms produce
the identical 49 mismatches with the identical per-layer split.

**`relu` is provably dead for code layers.** `fgx_sat8()` maps every negative to
0, so relu before an unsigned saturate to `[0,255]` changes nothing. The fabric
needs no relu logic at all. **`conv7` and the head keep the float multiplier** —
`fgx_emits_float()` is true only for them, and a layer that emits float has no
byte for the tile to send.

| measurement | result |
|---|---|
| code mismatches, fixed vs float, 4 images | **49 of 1,409,024 = 0.0035%** |
| how far off | every one **±1**; `\|d\| > 1` is **0** |
| per layer | L0 0.0013% · L1 0.0019% · L2 0.0103% · L3 0.0008% · L4 0.0031% · L5 0.0020% · L6 0.0081% |
| worst rel. error of `M·2^-s` against `mult` | 3.2e-06 … 3.7e-06, every layer |
| `M min` | ≥ 131,084 — always in the top octave, so the pick never failed |
| `s` over all 1,568 channels | **20 … 35**; 6 bits carries 0..63 |
| `acc + bias`, observed | **−283,149 … 153,388 → 20 signed bits** |
| `acc + bias`, provable bound | `1728·255·127 + 277,460` → **28 signed bits** |

The last two rows are what Stage 1's RTL is sized by: 28×18 → a 46-bit product,
even though real activations sit seven bits below the bound.

**The gate itself was 5,000 val2017 images and 67 queries**, both epilogues run
through `export.run_int()` so that the *only* difference between the columns is
the epilogue:

```
epilogue                       AUC>=0.80   mean AUC   retention
float  (acc+b)*mult               59 / 67     0.8856         94%
fixed  ((acc+b)*M+r)>>s           59 / 67     0.8856         94%

largest single-query change    : 0.00117      (scissors −0.00117, donut −0.00106,
                                               kite +0.00064 — both directions)
embedding cosine, float vs fixed: min 0.9999523  mean 0.9999960

RESULT : GO - the fixed-point contract does not cost retention
```

Both directions matters: the differences are noise around zero, not a bias, which
is what a contract that is *differently* rounded rather than *more coarsely*
rounded should look like.

#### Two departures from the approved plan, and why

**No `FGX_VERSION` bump, and no blob change at all.** The plan had `(M, s)`
exported as a fourth section with `FGX_VERSION` 2 → 3. It is not needed:
`(M, s)` is a pure function of the `mult` already in the blob, and one `frexp`
derives it identically on both sides. Re-exporting confirms it — `weights.bin`
after `--wbits 4 --wsearch --ends8` is **byte-identical** to the pre-M15 file
(`cmp`, no output); only `testvec.bin` changed, as it must. So there is no
fourth section length, no version bump, and no stale-blob hazard to design
around.

**`tools/probe_rq.py` in place of `model/evaluate.py`.** The plan's step 3 was to
re-run `evaluate.py` and reproduce M14's numbers. That harness scores
`quantize.quantize(folded)` — the PyTorch fake-quant model, which computes in
float and **never forms an int32 accumulator**. It is structurally blind to this
change, so re-running it would have "passed" while proving nothing.
`export.run_int()` is the numpy integer reference and is where the accumulator
actually exists, so it grew a `fixed=` flag and `probe_rq.py` scores both
settings through it. A useful side-effect: the integer pipeline's own mean AUC,
0.8856, reproduces `probe_int4.py`'s fake-quant 0.886 — the first time the two
pipelines have been put beside each other.

#### The substitution, and what it cost

One epilogue in the project, not four copies of one: `fgx_rq_pick()` and
`fgx_code_fixed()` live in `encoder.h` and are called from `fgx_conv_ref()`,
`encoder_fast.c` and `frame.c`'s `scatter()`. `scatter()` *has* to share it — the
whole point of `m7` is that the fabric and the MCU produce the same bytes, and an
epilogue that differed there would break that in a way no accumulator sweep could
see.

**The libm calls were priced rather than assumed away.** arm-none-eabi-gcc emits
`bl frexpf` and two `bl ldexpf` per pick instead of inlining them, and unlike
`fgx_rint()` that is left alone, because the call count is bounded by *channels*
and not by pixels. The pick is hoisted out of the pixel loops: `fgx_conv_ref()`
picks once per channel (1,376 a frame), and `fgx_conv_fast()` once per tile and
channel, `Σ ceil(N/32)·cout ≈ 11,008` a frame — about **5.9 ms at 150 MHz against
M5b's recorded 3,358, so 0.2%**. That is cheaper than bit-twiddling that would
need its own correctness argument. On the MCU the epilogue itself is `SMULL` +
`ASR` + `USAT` against `VMUL` + `VCVTR` + `USAT`, so the substitution is not a
slowdown even though its reason for existing is the fabric, not that loop.

| check | result |
|---|---|
| `test_encoder` | **2048/2048 bit-exact, 1−cos = 1.11e-16, PASS** |
| `make -C rtl vec test_plan test_wire` | PASS, PASS |
| `make -C rtl tb_gemm tb_gemm_link tb_gemm_link_wide` | PASS ×3 — "10560 accumulators over the wire, all bit-exact" in both link configurations |
| `ninja forgix_m7 forgix_m5b forgix_m9` | clean, no warnings |
| `weights.bin` | byte-identical, `FGX_VERSION` stays 2 |

`test_encoder` passing at 1−cos = 1.11e-16 is the cross-language statement: the C
fixed-point epilogue and the numpy one agree **exactly**, in two independent
implementations of the same closed form.

#### Stage 1 — the fabric gate: **resources GO, and Fmax is the real bill**

One parameter, `RQ`, defaulting to 0. At `RQ = 0` the tile is bit-identical to
what M14 shipped — `rq_on` is a constant zero, both new muxes fold, the engine
lives inside `if (RQ != 0)`, and no FSM state was added, so `state` stays 3 bits.
`tb_gemm`, `tb_gemm_link` and `tb_gemm_link_wide` pass bit-exact after every
revision below, which is the standing proof of that claim.

**Both resource criteria pass, and memory passes better than the plan predicted.**

| read | plan's requirement | measured |
|---|---|---|
| `res.csv` memory blocks | ≤ 24, **22 expected** | **21 — unchanged** |
| `res.csv` multipliers | still 8 | **8** |
| tile FFs | — | 1,408 → **2,001** |
| tile LUTs | — | 500 → **1,453** |

The plan budgeted a 22nd memory block for the `(bias, M, s)` table. It is not
needed, and that matters beyond this milestone: **M16's LE MACs need `wbuf`
4 → 7 blocks**, which is 24/24 on its own, so a block spent here would have been
a block M16 could not have. The table lives in **the strip buffer's dead top 192
bytes** instead. `STRIPD` is 2048 and the largest strip use in any blocking is
conv2's 8×6×32 = 1,536, so `RQBASE = 2048 − 6·32 = 1856` is unreachable in every
plan the host generates. Six bytes per channel × 32 channels, laid out
`bias[23:0]` · `M[15:0]` · `{s[5:0], M[17:16]}`, arrive as ordinary ACT writes —
no new memory, no new port, no new command payload destination.

**Fmax is where it costs.** A `tile_probe` build has to be read against a
`tile_probe` control, not against `gemm_top`'s shipped band — the plan named the
latter, and it is the wrong control for this netlist:

| seed | control `DPIPE=1` | `DPIPE=1,RQ=1` |
|---|---|---|
| default | 67.015 | **59.620** |
| 2 | 71.439 | **62.838** |
| 7 | 71.195 | **60.438** |
| 13 | 73.041 | **60.920** |

Band against band: **71 ± 3 → 61 ± 2, about 13–15%**. That is a real cost and it
is recorded as one. Two things bound how much it means. The engine is not on the
MAC array's path — every worst path in every one of these builds names an `rq_*`
register, so the compute loop is untouched — and **the board already runs above
its modelled Fmax**: M7h measured RUN at 23.24 Mclk over 314 ms = 74.0 MHz on a
`gemm_top` whose C2 report says 62.4. The C2 model is a relative gauge here, not
an absolute limit. What the number does say is that Stage 2 owes a `gemm_top`
build with the link's `cfg_rq` actually driven, because a top-level probe cannot
be faked: with `cfg_rq` tied to `1'b0` the whole engine folds away and the build
would measure nothing.

**Four builds, and the first three were the design.** The first gate build came
in at 50.4 against 67.0, and the report named
`rq_a3[7] → 8:1 mux (3 LUT levels, 10.4 ns of net) → 42-bit carry chain (6.1 ns)
→ rq_p[48]`, 19.692 ns. The lesson in that line is the one that shaped
everything after it: **Efinity's C2 model puts essentially all delay in nets.**
Every LUT in every report on this device shows 0.000 ns; nets are 1.3–5.1 ns a
hop. A 15 ns cycle buys roughly **four hops**, and depth in LUT levels is a proxy
for hop count and nothing else.

Three restructures, each aimed at hop count rather than at gate count:

- **The partial-product mux runs one cycle ahead of the accumulator**, so the
  mux and the add are separate flop-to-flop paths and neither carries the other's
  routing. The last multiply step computes a mux output nobody uses — a register,
  not a cycle.
- **Carry-save accumulation.** Six radix-8 digits compress into `{ps, pc}` with a
  3:2 compressor, one LUT level and no carry chain, and the single
  carry-propagate resolve is hoisted out of the loop into its own cycle. The
  left-shift is safe in carry-save form because
  `{ps[46:0],3'b0} + {pc[46:0],3'b0} = (ps+pc)·8 mod 2^50` and true partial sums
  are bounded by 2^44.
- **Saturation by comparison against a registered limit.** `256 << s` and
  `1 << (s-1)` are decodes of `rq_s`, which is loaded at `RQ_BIAS` and never
  moves, so both are registered at `RQ_PRE` for zero extra cycles. The second
  build's worst path was `rq_s[5] → 256<<s → 51-bit compare → rq_sat` at −9.748
  slack; registering the decode is the whole fix. The shifter is likewise split
  coarse (`8·s[5:3]`, 15 bits kept) then fine (`s[2:0]`).

That took the default seed 50.4 → 63.5 → 63.8. The fourth build removed the
param-address counter `rq_sa`, which the third had named as its worst path
(`rq_c[3] → rq_ld → rq_sa[0]|CE`, 14.383 ns across three LUT levels including a
Manhattan X:2/Y:40 hop) — the six fetch addresses are just `rq_qb + rq_c[2:0]`,
since `rq_c` is already walking 0..5 over exactly the cycles the addresses are
needed. **The band did not move**: 59.0/59.9/61.4/63.8 before, 59.6/60.4/60.9/62.8
after. The counter was a real path and removing it was right, but what is left is
placement, not logic — the current worst path is
`rq_c[1] → 3 LUT levels → rq_asel[23]|CE` at 15.490 ns of which **15.4 ns is
net**, a 29-pin enable dragged 26 columns across the die. Further Fmax on this
design is a floorplanning problem, and it is not worth solving before Stage 2
says whether the top-level build even inherits the loss.

**The arithmetic was verified independently of synthesis.** A standalone harness
replicates the staged pipeline expression-for-expression against a one-line
64-bit reference `clamp(((a·M + 2^(s-1)) >> s), 0, 255)`, over 200,000 random
`(a ∈ ±56,238,740, M ∈ [2^17,2^18), s ∈ [20,35])` plus corners at
`a = ±56,238,740, 0, 1` across every `s`: **PASS**. It is not in the repo on
purpose — the shipping check is `tb_gemm` against goldens from
`fgx_code_fixed()` itself, once Stage 2 wires `cfg_rq` to the link.

#### One correction to the plan's savings estimate

The plan priced DRAIN at "8 link clocks per code where it used to spend 32" and
projected **161 → ~43 ms**. That is the wire's cost, not the engine's. The engine
runs inside `S_DWAIT`, so in *this* build a word costs `1 + 15 + 8 = 24` clocks
against today's 32, and DRAIN goes **161 → ~121 ms**. Overlapping the engine with
the previous word's 8 wire clocks — prefetch, deliberately not in this build
because it is control logic and would have muddied the resource read — caps the
word at the engine's own 22 steps and takes DRAIN to **~80 ms**. So the honest
figure for the milestone is a **~80 ms saving on the wire, not ~118**, and it
needs Stage 2's prefetch to get there. The CPU-side savings the plan projected
(`GW_BODY` decode, `scatter()`) are unaffected — those follow from the byte
count, which is exactly as predicted.

#### Stage 2 — two lanes, because the wire cannot wait

Stage 1's correction said the engine, not the wire, would set the pace: a word
would cost `1 + 15 + 8 = 24` clocks against today's 32, and prefetching the
engine behind the previous word's wire time would cap it at the engine's own 22.
Both of those are still one engine, and one engine is not fast enough. **The
wire spends 8 link clocks per code and the engine spends 15 per pair**, and
`gemm_link` has no back-pressure on the return path — `T_DATA`'s `!have` arm
sets `underrun` sticky and jumps to `T_TXC`. The engine's period, not the wire's,
has to be the shorter of the two. Built as one engine and measured, the probe
said exactly that: `PROBE code 3: 16 clocks` and `PROBE underrun at 923005000`.

So the engine was **interleaved two ways**, and the reason it is two *lanes* and
not two engines is the walk. The drain walk is position-innermost, so codes `2k`
and `2k+1` are two positions of the *same* output channel and share `(bias, M,
s)` exactly. Shared: the parameter store and its fetch, `rq_rcq`, `rq_limq`, the
strip read, the step counter. Duplicated: the bias adder, the odd multiples, the
8:1 partial-product mux, the carry-save accumulator, the two shift stages, the
clamp. **Lane 1 runs one step behind lane 0** — originally `k = rq_c - 1`, now
nine registers, which is cheaper and exactly the same thing. Fifteen clocks per
pair against the wire's sixteen, with a two-slot output buffer to absorb the
one-clock margin; the engine stalls at `RQ_DONE` when the buffer is full.

Three things fell out of that shape and all three are load-bearing:

- **`P` must be even**, or a pair straddles a channel boundary and the two
  positions no longer share their parameters. `gb_geom()` enforces it, next to
  the checks that already refuse an oversized `Q` and an overlong strip, and it
  disqualifies nothing: every rq case ships an even `P` (64, 128, 20, 16).
- **Parameter prefetch is mandatory, not an optimization.** Without it the slack
  per channel is `P/2 − 6` clocks — negative for `P < 12` and *cumulative*.
  `RQ_PF0 = 16` was chosen so `rq_c[2:0]` counts 0..5 over 16..21 exactly as it
  does over the prologue's 0..5, so one address expression serves both.
- **`RQP` is a command, not a DRAIN payload**, and `GW_CMD_RQP = 0x08` lands in
  `gemm_host.c`'s `cmd & 7` profiling slot 0 — the one the 1..7 command codes
  left empty. It gets a private slot without the table growing.

Measured: **8 clocks per code, five consecutive, no underrun.** Wire-limited,
which was the target. `tb_gemm`, `tb_gemm_link` and `tb_gemm_link_wide` are
bit-exact against goldens from `fgx_code_fixed()` itself, and `make vec test_plan
test_wire` prices it:

| component | int32 | rq |
|---|---|---|
| DRAIN, wire | 161 ms | **42** |
| RQP | 0 | **1** |
| response decode, CPU | 167 ms | **48** |
| wire total, one forward lane | 835 ms | **718** |
| wire total, three forward lanes | 617 ms | **499** |

That is the plan's original ~43 ms DRAIN, not Stage 1's corrected ~80. Two lanes
bought back the whole of the correction.

#### Two Fmax regressions, and only one of them was the engine

The first two-lane gate build put `tile_probe` at **55 ± 2 MHz** (53.6 / 56.8 /
55.2 / 54.1) against the single lane's 61 ± 2 and the no-rq control's 71 ± 3. The
four seeds disagreed about which path was worst while agreeing about where it
started: **every one of them began at `rq_c`** and ended at some flop's D or CE —
`u_rq0/pc[42]|D`, `rq_slot[1][4]|CE`, `u_rq0/ps[33]|CE`. That is one cause and
not four. A five-bit compare is two LUT levels on a LUT4 device, the schedule has
nine of them, and two lanes were building each one twice over longer nets.

The fix is that **the step is decoded once, into flops, and the lanes are told**.
`gemm_rq_lane` now takes a nine-bit strobe vector and holds no comparator and no
step number of its own; `rq_e1 <= rq_e0` is lane 1's copy. `rq_second` and the
`rq_c == RQ_DONE` test became registers for the same reason — both sat
combinationally in front of something that has to be stable at a clock edge, one
an accumulator RAM's read address (`state[1] → 3 LUTs → rq_second → 2 LUTs →
accram|RADDR`, 18.5 ns) and one the output buffer's clock enable. That moved
`tile_probe` to **58 ± 2** (55.3 / 60.1 / 58.3 / 58.5) and moved the worst paths
off `rq_c` onto `rq_sf[1]|CE` and `u_rq1/asel[*]|D`, which is what it was for.

**And `gemm_top` did not move at all** — 54.5 before, 54.7 after. The reason is
worth recording because it is a trap this repo can fall into again: `tile_probe`
instantiates the tile at `DPIPE = 1` and `gemm_top` at `DPIPE = 0`, and
`d_lastj` / `d_lastg` are `(DPIPE != 0) ? r_last_j : (d_j == JLAST)`. Written
with `d_last*`, the rq walk is **a different circuit in the two netlists**, so
the probe could not see `gemm_top`'s actual worst path — which was never in the
engine at all. It was `d_g[3] → 5 LUT levels → d_g[4]`, 18.1 ns: two
combinational compares chained in front of the walk counter's own enable,
`!r_last_pp → !(d_j == JLAST) → !(d_g == qg_m1) → d_g + 1`.

Under rq the registered copies are refreshed unconditionally every drain cycle,
so they are available whatever `DPIPE` says. The walk now reads `r_last_*`. They
are one cycle stale by construction, which cannot matter: `rq_adv` fires at
`RQ_PADV` once and then only at `RQ_DONE`, and the schedule between two
`RQ_DONE`s is fifteen steps long. The int32 walk still reads `d_last*` and is
untouched.

| netlist, 4 seeds | M14 shipped | rq, first cut | + registered strobes | + `r_last_*` walk |
|---|---|---|---|---|
| `tile_probe` | 71 ± 3 (no rq) | 55 ± 2 | **58 ± 2** | unchanged¹ |
| `gemm_top` | 57.8 / 62.1 / 63.9 / 64.7 | 54.5 | 54.7 | **53.9 / 57.6 / 57.2 / 57.3** |
| `gemm_top_wide` | 58.6 | — | — | **56.8 / 54.5 / 53.6 / 56.8** |

¹ `DPIPE = 1` already selected `r_last_*`, so the edit is a literal no-op there —
which is the whole point.

**The honest statement is that rq costs about 7 MHz on `gemm_top`** — 56.5-ish
against M14's 62-ish — and the remaining worst paths are scattered across four
different endpoints (`d_addr`, `state`, the link's `state|CE`, `asel`) rather
than sharing a cause, which is where chasing stops being logic work and starts
being floorplanning. On `gemm_top_wide` M15 costs less and did not cause what is
left: **all four seeds end in `u_link`** (`state[1]|CE`, `cmd[2]|CE`,
`hunt_sr[11]|SR`), the link's own framing FSM, which M15 did not touch. Neither
number is a pass or a fail yet — the C2 model is a relative gauge here, and M7h
measured this board running `RUN` bit-exact at 74.0 MHz on a netlist whose report
said 62.4. The board decides.

Resources are unchanged from the Stage 1 gate and both criteria hold: **memory
21 of 24** — better than the 22 the plan budgeted — and **multipliers still 8**,
so the requantize stole none. The second lane cost `+312` FFs and `+748` LUTs
against the option-2 estimate of `+600` / `+950`; each lane is 366 FFs and ~610
LUTs.

`m7.c` now runs **four ladders in a boot** — each link configuration at int32 and
again at rq, with the 174-block accumulator sweep on the int32 pass only, since
`ft_set_sweep()` forces int32 and there are no accumulators on the wire at rq to
compare. Both wire formats live in the one bitstream because rq is a CFG bit, so
the comparison is one boot, one clock, one plan, and the difference is the wire
format and nothing else. `host/m7.py` is unchanged and deliberately so: it does
not know what a bitstream contains, so an RQ=0 image presents loudly on the first
rq transaction rather than being probed for.

**On the board.** `m7` on `gemm_top_rq4_s0.hex` / `gemm_top_wide_rq4_s0.hex`:
all 8 layers bit-exact in all six modes of both link configurations, int32
DRAIN and rq alike, 512/512 embedding floats exact, and 174 of 174 blocks in
the accumulator sweep. The claim, measured in one boot against its own control:

```
    by command           ms       Mclk     ns/clk
  DRAIN (int32)         153      11.48      13.36
  DRAIN (rq, byte)       41       3.02      13.43
```

**153 → 41 ms**, against 161 → 42 modelled. The frame keeps most of it —
config A 958 → 812 ms, config C 798 → 631 ms, 1.18× and 1.26×, same boot —
and config C at rq is **5.46× the MCU**'s 3,449 ms.

---

### M16 — LE-built MACs, two K steps per lane ✅ *RUN 314 → 220 ms on the board, config C 631 → 569*

M15 took DRAIN from 161 ms to 42 and by doing so exposed the next line, which is
much larger than the one it fixed. `make -C rtl test_plan`, config C with rq on:

```
  ACT     75 ms    WGT  53 ms    DRAIN  42 ms    RQP 1 ms    framing 3 ms
  RUN    325 ms  <- the host idling while the tile computes
  total  499 ms
```

**RUN is 65% of the frame's wire time and it is the one line no link change can
touch.** It is not bytes, it is tile cycles. Config A and config C report the
same 325/326 ms for it against 2.778 MB and 8.312 MB respectively — three
forward lanes make the idle bytes cheaper and the wait exactly as long.

The tile does `NMAC = 8` MACs per cycle on 8 hard multipliers and the T8F49 has
exactly 8. Every one is committed, so more arithmetic has to come out of logic
elements. **This milestone is that trade, and its Stage 1 gate is where three of
four attempts died.**

#### What is paired, and why it is free at the accumulator

Every conv in this model is 3×3, so the tap walk is `kx ∈ {0,1,2}` innermost,
then `ky`, then the input-channel group. Pairing on `kx` turns three sweeps into
two per `(ky, ic)` — 9 taps become 6 sweeps per channel block. `kx` is the axis
because it is free on three counts at once:

- **One accumulator.** Both steps target the same `(g, p)`, so the two products
  sum into one accumulator word. `p`-pairing would need two write ports on
  `accram`, which is 13 of the 21 memory blocks; `g`-pairing is the axis the
  eight lanes already use.
- **Adjacent strip bytes.** `im2col_feed.v`'s `addr = rowb + ix_pad` and
  `step_inc` applies to positions, not taps, so for a fixed `p` taps `kx` and
  `kx+1` are exactly one byte apart at any stride.
- **Adjacent `wbuf` words.** The walk is `k` outer, `g` inner, so tap `k` group
  `g` lives at `k·QG + g`. The pair is at `wcnt` and `wcnt + cfg_QG`.

**Bit-exactness is free**, because integer addition is associative: summing two
products before the accumulator is identical to accumulating them on consecutive
sweeps. No golden regeneration, no wire-format change, no host change. That is
what makes the whole milestone checkable against vectors that already exist.

**APACK's packing trick does not generalise to this.** `gemm_tile.v` gets two
products from one multiplier by packing two *weights* against a *shared*
activation. K-packing has two different activations and two different weights;
packing both operands produces four terms, and the two cross terms land on top
of both wanted fields at every legal shift. The second product has to be an LE
multiplier — a 10×8 signed one, since `--ends8` keeps the end layers at 8 bits.

#### Two things the earlier task description had wrong

Both were settled against the shipped netlist's own placer report rather than
modelled, and both were load-bearing — the task had this milestone as 24/24 on
memory, which would have been a NO-GO before it started.

**1. `wbuf` does not need widening.** It is read **once per sweep**, not once per
cycle: `S_LOAD` is a single cycle in front of `S_SWEEP`'s `P`. A second weight
word costs 64 flip-flops, not four more RAM blocks.

**2. Banking the strip is free.** The flat 2048×8 array places as four blocks in
the ×2 configuration — width-bound. Two banks of 1024×8 take two blocks each in
×5. Four either way, and `place.rpt` confirms it: **memory stayed at 21 of 24**
in every build below.

#### Stage 1 — the gate, and the three builds that failed it

`TOP_PARAMS="RQ=1,KPACK=1" ./rtl/build.sh tile_probe`, against M15's `tile_probe`
control at four place-and-route seeds. Resources passed on the first build and
never moved. **Fmax failed three times**, and the sequence is the substance of
this milestone:

| build | what it was | LE | Fmax, 4 seeds |
|---|---|---|---|
| — | M15 control | 4,148 | 55.3 / 60.1 / 58.3 / 58.5 |
| k1 | LE multiplier as one combinational tree at stage 2b | 5,654 | **39.8** |
| k2 | tree split at the existing s2 → s2b boundary, no new stage | 5,603 | **40.7 / 43.8 / 40.9 / 42.3** |
| k3 | stage 2c added, whole multiplier moved into 2b | 5,670 | **50.7** |
| k4 | k3 + `wreg1_r` | **5,724** | **58.9 / 56.7 / 58.7 / 58.2** ✅ |

**Why 40 MHz was fatal rather than merely disappointing.** The board clocks the
tile at 75 MHz and this analyser runs about 1.3× pessimistic on this device —
M7h measured `RUN` bit-exact at **74.0 MHz on a netlist reporting 56.5**. So 40
reported is ~52 real, and k1/k2's 1.444× speedup at 52 MHz against today's
1.000× at 75 is **1.00**. k1 and k2 were correct, bit-exact, and worth exactly
nothing.

**k2 is the interesting failure.** The obvious reading of k1's 17-level path is
"the adder tree is too deep", and k2 split it in half without spending a stage —
`le_mul_lo`/`le_mul_hi` at stage 2, the final add at 2b. It bought 0.9 MHz. The
critical path had simply moved: strip RAM clock-to-out 5.264 ns, then 4.648 ns of
net to the parity swap, 3.679 to the sign extend, 2.516 to the tap select, then
the adds — 18.774 ns of which **~10.8 ns is pure wire** at 76% occupancy.
Splitting a path that is mostly routing between two points that are far apart
does not shorten it. Both halves still cross the same fabric.

k3 spends the stage the plan had reserved: `a_q1` registers the second tap's
operand exactly where `a_q` registers the first, the whole multiplier moves into
stage 2b, and **stage 2c** finishes it. That is 40.7 → 50.7, and it costs
`FLUSH` 5 → 6 — one clock per sweep, which is the only cycle price in the
milestone. The plan's assumed `+1` for a two-cycle `S_LOAD` never materialised:
the partner weight is fetched on the *first* `S_SWEEP` cycle, so the sweep is not
longer for that reason.

k4 is 64 flip-flops. k3's worst path began at `wbuf`'s RDATA pin and ran 3.527 ns
of net to the LE multiplier's tap select — because `wreg1` **is** the block RAM's
output register, and the LE lanes were reading a memory pin twenty tiles away.
The hard multiplier never saw that path: `wreg` is already a register copy
sitting near its lanes. `wreg1_r` gives the LE side the same treatment. It costs
no cycle — it is valid at `L+3` and the multiply is at `L+4`.

> An earlier build failed place-and-route outright — flip-flops 2,513 → 19,154,
> LUTs 2,193 → 15,857, RAM 21 → 17, and *"Architecture does not have enough
> resource to legalize the carry chains"*. The strip banks were the natural
> suspect and were innocent; splitting their shared `always` block changed
> nothing. `map.out` named the real one: `Mapping into logic memory block
> 'u_tile/wbuf' because read port is not synchronous`. The partner fetch had
> written two destination registers from one memory read. **A block RAM has one
> output register**; the read has to land in one place and be copied from there.
> That is why `wreg1` is the output register and `wreg` the copy — and it is also
> what set up k4's timing bug, three builds later.

#### The cycle model, and where the duplication was

`tb_gemm` counts busy cycles, because bit-exactness alone would happily pass a
build that had paired nothing — and the first `KPACK=1` run finished at exactly
the same picosecond as `KPACK=0`, which turned out to be `-P` naming the *file*
rather than the module and being ignored in silence. With the counter:

**1,241,964 → 882,252 busy cycles = 1.408×**, at 19,072 words bit-exact against
the same goldens, `KPACK` 0 and 1, in `tb_gemm`, `tb_gemm_link` and
`tb_gemm_link_wide`. The link testbenches clock until the response arrives rather
than for a fixed budget, so their wall time is evidence too: 40.1 → 36.5 Gps
narrow, 28.8 → 25.2 wide.

The host has to *know*, because RUN's idle bytes are the tile's only clock and
under-budgeting strands it mid-sweep. That expression existed in three files —
`gemm_plan.c`'s cost model, `frame.c`'s sequencer and `m6.c`'s clock sweep — with
different failure modes: the model being wrong prints a wrong projection, the
sequencer being wrong hangs the board. It is now one `gp_sweep_cycles()` in
`gemm_plan.h`, keyed on a `GP_KPACK` that must match the bitstream. The two are
built together, and the direction that could strand the tile is the one a stale
`.hex` cannot produce.

`test_gemm_plan.c`'s M6c anchor is pinned at `kpack = 0` explicitly, so 19,808
stays a check against a board measurement rather than against the code's own
arithmetic, and the block byte total is stated as *the measurement plus each
modelled delta* — int4 weights, then K-packing — for the same reason.

#### The shipping builds

Both tops at `TOP_PARAMS="RQ=1,KPACK=1"`, four seeds each, against M15's shipped
`rq4` builds:

| | `gemm_top` M15 | `gemm_top` M16 | `gemm_top_wide` M15 | `gemm_top_wide` M16 |
|---|---|---|---|---|
| Logic Elements | 4,650 | **6,265** / 7,384 | 4,671 | **6,232** / 7,384 |
| LUTs / Adders | 3,473 | 5,018 | 3,506 | 4,995 |
| Memory Blocks | 21 | **21** / 24 | 21 | **21** / 24 |
| Multipliers | 8 | **8** / 8 | 8 | **8** / 8 |
| `link_clk`, 4 seeds | 53.9 / 57.6 / 57.2 / 57.3 | **56.7 / 55.3 / 57.0 / 55.6** | 56.8 / 54.5 / 53.6 / 56.8 | **52.9 / 54.9 / 53.1 / 51.8** |

**+1,615 LEs on the narrow top and +1,561 on the wide**, against a Stage 1
estimate of 1,600–2,100 and a budget of 2,734. Memory and multipliers did not
move, which is the part the task description had wrong and the part that would
have made this a NO-GO.

`gemm_top`'s band sits inside M15's. **`gemm_top_wide`'s does not, quite** — its
worst seed is 51.8 against M15's 53.6, about 3% low, and its band no longer
overlaps M15's at the top. That is the one number in this milestone that is not
comfortable. It is small enough to be seed noise on a device at 84% occupancy,
and the ×1.3 pessimism puts even 51.8 at ~68 reported-to-real — but 75 is the
requirement and the analyser cannot say whether it is met. **The board decides
this one**, and it decides it for the wide top specifically.

#### What the model says it is worth

| config C, rq on | M15 | M16 |
|---|---|---|
| ACT | 75 | 75 |
| WGT | 53 | 53 |
| **RUN** | **325** | **228** |
| DRAIN | 42 | 42 |
| RQP + framing | 4 | 4 |
| **wire total** | **499 ms** | **401 ms** |

**~98 ms off the wire**, against the plan's ~105 estimate. How much of that the
frame keeps is a separate question — core 1's `W1_HI` is 293 ms and untouched —
and this repo's record is that predicted component savings convert at less than
face value. The claim being made is about the wire, which is measured.

#### On the board

All 8 layers bit-exact in all six modes of both link configurations, int32 DRAIN
and rq alike, 512/512 embedding floats exact, **174 of 174 blocks** in the
accumulator sweep, in both configurations. The two runs below are back to back
in one session on the same board, `GP_KPACK` matched to each bitstream:

| | M15 board | M16 board |
|---|---|---|
| `RUN` | 314 ms / **23.30 Mclk** | 220 ms / **16.30 Mclk** |
| config A frame, rq | 812 ms | **718 ms** |
| config C frame, rq | 631 ms | **569 ms** |
| vs the MCU (3,449 ms, `encoder_fast`) | 5.46× | **6.05×** |

**23.30 → 16.30 Mclk is 1.429×**, slightly better than `tb_gemm`'s 1.408 — and
every other command's cycle count is identical to the clock, which is what says
the pairing and nothing else produced it.

**The Fmax worry was unfounded, and by a wider margin than the narrow top's.**
`gemm_top_wide` reports 52.874 MHz and the board ran its link at **75.0 MHz**
bit-exact — a ratio of 1.42, against 1.30 for `gemm_top` (56.654 reported,
73.8 measured on `RUN`). Shipping a netlist 30% under the target clock is not a
habit to acquire, but on this device and this analyser it is now measured twice.

**Where the saving went is the interesting part, because the two configurations
disagree.** Config A converted 93 ms of `RUN` into 94 ms of frame — all of it.
Config C converted 94 ms into 62 ms, about two thirds. The `WGT` row says why:

```
  config C, rq          ms       Mclk     ns/clk
  WGT   M15             54       3.76      14.32
  WGT   M16             82       3.76      21.82
```

Identical cycles, 52% more wall time. The link did not slow down; **core 0
started waiting on core 1's weight builds.** `W1_HI` is 284 ms of builds against
a 569 ms frame, and config C's wire is three times as fast as config A's, so it
is the configuration that runs out of gaps to hide them in first. RUN was the
bottleneck for three milestones; **the weight builder is the next one**, and it
is firmware rather than fabric.

---

### Between M16 and M17 — the 150 MHz ceiling was never measured ✅ *220 MHz sys / 110 MHz link, config C 569 → 387 ms*

Task #70 had sat open since M14 as "try `sys_clk` above 150 MHz", filed under
things worth an hour if there was ever nothing better to do. It was mis-filed.

**Every clock sweep this project has ever run descends from 150000 kHz.** M6's
did, M7's config C ladder did, and both step *down* on failure. So the fastest
rate ever attempted was 75 MHz on the link, and every log saying "bit-exact at
75.0 MHz" was reporting the top row of its own table. That got read back, for
three milestones, as *bit-exact up to* 75 — including by the M16 plan, which
states that RUN "is the one line no link change can touch". The reasoning behind
that sentence is sound; the premise underneath it had simply never been tested.

`link_clk = sys_clk / 2`, hard-tied — `gemm_host.c:346` loads the ×2 PIO program
at clkdiv 1.0, and the comment there explains why a fractional divider cannot
help: PIO stretches some state-machine cycles and not others, so the *shortest*
link period stays at the undivided rate, and the tile's Fmax is a constraint on
the shortest period. One knob, not two. Raising it therefore moves the MCU, the
wire and the tile together.

#### What it took

Three rows appended to `m6.c`'s sweep and three rungs prepended to `m7.c`'s
config C ladder, plus `vreg_set_voltage(VREG_VOLTAGE_1_20)` — 150 MHz is
RP2350's guaranteed maximum at the default 1.10 V, and 1.20 V is four steps
below the 1.30 V the SDK will pass without disabling POWMAN's limit. Voltage
before frequency going up, frequency before voltage coming down; the reverse
order is what browns out the core mid-instruction. Flash is not a constraint:
`PICO_FLASH_SPI_CLKDIV` is 4 on RP2350, so XIP at 220 MHz sys is 55 MHz, below
the rate it already runs at without complaint.

What made it safe to attempt at all is `host/bootsel.py --power-cycle`, which
cuts VBUS on the hub port via `uhubctl`. A hang costs eight seconds, not a walk
to the bench.

#### On the board

`m6` first — one block, 28 transactions, 2048 accumulators. 176/200/220 MHz sys
all came back **2048/2048 bit-exact**, three runs running, row for row
identical. That is 88/100/110 MHz on the link against the static analyser's
56.654 MHz, a **1.94× overshoot** where this project's measured pessimism has
been 1.3–1.4×, so it wanted a frame before it was believed.

`m7` then ran the whole thing: 174 blocks, all 8 layers, six modes, both link
configurations, rq off and rq on, the 512 embedding floats and the
174-of-174 accumulator sweep — about 1,856 transactions per mode against m6's
28. **PASS on the top rung, first try, and on three separate boots.**

The comparison below is config C with rq on, mode 5, against the M16 log at the
same settings. Config A is untouched at 150/75 in the same boots and reads 718
ms in every one of them, which is the within-boot control:

| | 150 MHz sys / 75 MHz link | 220 MHz sys / 110 MHz link |
|---|---|---|
| `ACT` | 75 ms / 5.37 Mclk | **51 ms** / 5.37 Mclk |
| `WGT` | 82 ms / 3.76 Mclk | **55 ms** / 3.76 Mclk |
| `RUN` | 220 ms / 16.26 Mclk | **150 ms** / 16.26 Mclk |
| `DRAIN` | 41 ms / 3.02 Mclk | **28 ms** / 3.02 Mclk |
| ns/clk on `RUN` | 13.55 | **9.24** |
| wire, elapsed | 422 ms | **287 ms** |
| `W1_HI`, core 1's builds | 284 ms | **193 ms** |
| bytes moved | 10.244 MB | 10.244 MB |
| **config C frame, rq** | **569 ms** | **387 ms** |

**Every cycle count is identical to the digit** — `RQP` 0.02, `CFG` 0.08, `ACT`
5.37, `WGT` 3.76, `RUN` 16.26, `DRAIN` 3.02, `NOP` 0.13 — as are the transaction
counts (1856 / 1856 / 1856 / 174 / 348) and the bytes. Nothing did less work.
Every wall-clock figure fell by 0.680–0.683, against a clock ratio of
150/220 = **0.6818**. The three repeat frames landed at 386, 386 and 387 ms.

The `ns/clk` column is the one worth reading twice. It is 9.24 ns at 220 MHz,
which is 108.2 MHz against a nominal 110 — the same ~1.6% of fixed per-
transaction framing that shows as 13.55 against 13.33 at 150. The overhead did
not grow. The link is not straining; it is simply going faster.

#### Why RUN moved, which M16 said it could not

RUN is tile cycles, not bytes — M16 established that, and it is why config A and
config C both reported ~325 ms for it against 2.778 MB and 8.312 MB. What was
missed is that the tile is clocked *by* the link, so `sys_clk` is a lever on it
after all. 16.26 Mclk at 75 MHz is 217 ms; at 110 MHz it is 148. The measurement
says 220 → 150.

So the frame's two largest lines both moved for the same reason, from one knob:
RUN by 70 ms because the fabric runs faster, and `W1_HI` by 91 ms because core 1
does. **1.47× on the frame, 1.76 → 2.58 fps**, for about forty lines of firmware
and no RTL at all.

#### What this is not

It is one die. RP2350's 150 MHz is a *guarantee*, not a limit, and 220 MHz at
1.20 V is inside what many RP2350s do — but the Trion T8 side is the part that
should be held loosely. It is closing paths at **1.94× its signed-off Fmax**,
on a supply that was not raised, with margin that is temperature-dependent in
ways three passes at room temperature cannot survey. Bit-exactness over ~5,600
transactions per boot is strong evidence about *this board today*; it is not
timing closure.

So the ladder is the shipping form of the result, and deliberately: `m7` tries
220, then 200, 176, 150, 130, 110, 90, and reports the rung it landed on. A
board that cannot hold 220 costs two seconds and falls back. Config A stays
pinned at 150 so the primary verdict stays comparable to every prior milestone,
and the summary refuses to print an A-vs-C ratio when the two ran at different
rates — it prints the rate instead.

The honest way to state the finding is not "the tile runs at 110 MHz". It is
**"nobody had asked it to"**. A ladder that only steps down cannot find a
ceiling; it can only confirm the rung it starts on. That failure mode is cheap
to create and, on this evidence, expensive to leave in place.

One item is *removed* from the roadmap by this: the unused `link_narrow_x4` /
`link_wide_x4` PIO programs in `link.pio` were the planned way to buy CPU
headroom without moving the link. They are still the right tool if the MCU and
the fabric ever need to run apart — but the premise that they must was the same
untested premise as everything else here.

---

### M17 — a 2% win, and the counter that explained it ✅ *config C 387 → 385 ms, and the milestone's own premise falsified*

This is a small milestone with a large finding, and the finding is not the 2%.

M17 was scoped as "the weight builder, now the exposed line". After the clock
push, core 1's `W1_HI` was 193 ms against a 387 ms frame — the same ~50% share
it held before — so deleting `gb_weights()` looked like the obvious next lever.
It was not, and the reason it was not had been invisible for three milestones
because of a single merged counter.

#### Stage 1 — the wide store, which worked exactly as designed

The int4 arm of `gb_weights()` wrote one byte at a time: a byte holds two
nibbles, so two output channels shared a store, strided by `QB` down the taps.
But `dst[k*QB]` for four consecutive channel pairs are four *adjacent* bytes, so
eight channels are one 32-bit store.

The guard question — what happens when `Q` is not a multiple of 8 — turned out
not to be a guard at all. `gb_geom()` sets `g->Q = g->QG * GB_NMAC` with
`GB_NMAC` 8, so **Q is a multiple of 8 by construction** and the wide arm never
declines. That matters more than it sounds: an arm that silently fell back on
some passes would have shown up as an unexplained few percent and would never
have been found. The pair loop is kept below it anyway, as the definition the
wide arm is an optimisation of.

Stores 4× fewer, instruction count ~1.4× lower, `make -C rtl vec` byte-identical,
and `test_gemm_plan.c` already checks `gb_weights()` against `gb_weights_slow()`
byte for byte on the real model — so the gate was free and ran on the laptop.
Deliberately breaking the arm produced FAILs on five layers at byte 0, which is
how the gate was confirmed to have teeth.

On the board: **`W1_HI` 193 → 189 ms, frame 387 → 385.** Two percent.

#### The second 2%, and why that was the signal

M14's int8 control had *doubled* the weight blob — 1,142,784 → 2,230,272 payload
bytes — and moved `W1_HI` by 2%. Stage 1 cut the instruction count 1.4× and moved
it by 2%. Two very different large changes to the same function, the same tiny
result. The available reading was "the weight builder is immovable", and it had
already been used once, to justify M14's conclusion.

`W1_HI` carries **two** callbacks, `build_wgt_cb` and `build_strip_cb`, and both
accumulated into one `us_build`. Splitting them took one counter:

| | `gb_weights` | `gb_strip` | total |
|---|---|---|---|
| config C, 220 MHz | 82 ms | **124 ms** | 206 ms |
| config A, 150 MHz | 121 ms | **181 ms** | 302 ms |

**`gb_strip` is the larger half, by 1.5×.** The milestone was named for the wrong
function. The design-time model had this right the whole time and nobody read it
that way — it predicts 150 ms of `gb_weights` against 206 ms of `gb_strip`, vs
121/181 measured, the usual pessimism in the usual direction.

The split reads 82/124/206 identically in all four core-1 modes, which is the
consistency check: the same work, scheduled four different ways.

#### What deleting it would have been worth: ~20 ms, not 82

Stage 2 was to emit the blob already in tile order and delete `gb_weights()`
outright — `gemm_plan.h` has said since M7c that this work is "deletable rather
than tunable", and it is: a pure function of geometry and the static blob, with
nothing image-dependent in it.

But core 1 is only **56% busy** (217 ms of 388) and core 0's stall counter reads
**7 ms**, so core-1 time is not frame time at face value. The conversion rate is
legible in the `ns/clk` column, because fixed per-transaction framing shows up
larger the fewer clocks it is spread over:

| cmd | clk/txn | ns/clk | excess ns/txn | × txn |
|---|---|---|---|---|
| `DRAIN` | 17356 | 9.16 | 1199 | 0.2 ms |
| `RUN` | 8761 | 9.25 | 1394 | 2.6 ms |
| `ACT` | 2893 | 9.55 | 1328 | 2.5 ms |
| **`WGT`** | 2026 | **15.12** | **12214** | **22.7 ms** |

Three commands agree on 1.2–1.4 µs of framing against a 9.09 ns nominal link
period. `WGT` carries 12.2 µs. The difference — ~10.9 µs × 1856 ≈ **20 ms** — is
core 0 waiting on the builder *inside* the `WGT` window, where the stall counter
cannot see it, because core 0 blocks on the job by name before it sends.

So Stage 2 buys at most 20 ms of a 385 ms frame, 5%, in exchange for coupling
`export.py` to the blocking `gp_choose()` picks at **run time** — either a
blocking contract in the blob header with a firmware assert, or a second
tile-ordered section at 838 KB across the model, 442 KB for layer 7 alone.
**Descoped.** The PSRAM cross-frame cache dies with it, and for a better reason
than last time: the board *does* have 2 MB of APS1604M on QMI CS1 and the 838 KB
distinct stream set fits comfortably, so that idea was never dead on capacity —
it is dead on value.

#### What this cost and what it is worth

Two percent of frame, and a method. The ns/clk column had been printed since M7
as a link-health readout; using it as a *wait detector* — one command's excess
over the framing the other three agree on — is what converted "core 1 spends
82 ms here" into "core 0 waits 20 ms for it", and those are the two numbers this
project keeps confusing.

The recurring lesson recurs, from the other side. **A component saving is worth
face value only until it uncovers the next thing** — and its mirror: *a component
cost is worth its face value only if something is actually waiting on it.* 82 ms
of core-1 work bought 20 ms of frame at the absolute most. Two milestones were
planned against the un-split figure.

The narrower lesson is cheaper to act on and worth more than the milestone:
**two callbacks sharing one counter is enough to misdirect two experiments in a
row.** Neither M14 nor Stage 1 was wrong about what it measured; both were wrong
about what the measurement was *of*.

#### Housekeeping, and one thing deliberately not fixed

`GP_NS_*` were calibrated by M7a at 150 MHz and are ~47% high whenever the
config C ladder lands on 220 — 118 ns/B of build is really ~80. They were **not**
rescaled. Ranking is scale-invariant, so one uniform factor on all four cannot
reorder candidate triples and `gp_choose()` picks the same blocking at any rate;
`gp_cost_t.bytes` carries no rate at all, which is why the bytes-projected line
still matches the board to −0.1% at 220 while the ms line does not; and
`gemm_plan.c` is free of Pico headers precisely so `test_gemm_plan.c` can settle
the table on the laptop, which reading `clock_get_hz()` would end. What the rate
change actually requires is that anything *printing* those ms names its basis, so
`m7`'s plan table now does.

#### Where the frame stands

At 220 MHz sys / 110 MHz link, config C with rq on, **385 ms**:

| | ms | share |
|---|---|---|
| **`RUN`** | **150** | **39%** |
| `WGT` | 57 | 15% |
| `ACT` | 51 | 13% |
| `DRAIN` | 28 | 7% |

`RUN` is the line again, and it is tile cycles — no link change, no clock change
short of another rung, and no firmware change touches it. All 8 hard multipliers
are committed (`gemm_top.place.rpt`: 8/8), so anything further comes out of logic
elements, as M16's did.

---

### After M17 — the audit, and a ladder that is not monotonic ✅ *280 MHz sys / 140 MHz link, config C 385 → 304 ms*

The previous clock section ended by naming its own follow-up: *worth auditing any
other place where a measured pass has been quietly read as a measured bound.*

The first such place was that section's own ladder. **220000 had become the top
row**, so "bit-exact at a 110 MHz link" once again meant only that nothing above
it had been asked. The audit costs three array entries, so it was run before
anything more interesting.

#### On the board

`m6` first, three boots, one block and 2048 accumulators per row:

| sys / link | result |
|---|---|
| 220 / 110 | 2048/2048 |
| **240 / 120** | **2048/2048** |
| **260 / 130** | **link error, all three boots** |
| **280 / 140** | **2048/2048** |

Then `m7` — 174 blocks, all 8 layers, six modes, both link configurations, rq off
and rq on, the embedding and the accumulator sweep. **Three clean runs, 304 /
304 / 303 ms**, plus two that truncated (one during config A's accumulator sweep
at the stock 150 MHz, before the ladder was reached; one that produced no log at
all because the previous truncation had left the board parked). Neither
truncation is an overclock symptom and neither is counted as evidence either way.

Config C with rq on, against the 220 MHz operating point, config A pinned at
150/75 in the same boots as the control:

| | 220 MHz / 110 MHz | 280 MHz / 140 MHz |
|---|---|---|
| `ACT` | 51 ms / 5.37 Mclk | **40 ms** / 5.37 Mclk |
| `WGT` | 57 ms / 3.76 Mclk | **44 ms** / 3.76 Mclk |
| `RUN` | 150 ms / 16.26 Mclk | **118 ms** / 16.26 Mclk |
| `DRAIN` | 28 ms / 3.02 Mclk | **22 ms** / 3.02 Mclk |
| `W1_HI`, core 1's builds | 189 ms | **148 ms** |
| `gb_weights` / `gb_strip` | 82 / 124 ms | **64 / 98 ms** |
| bytes moved | 10.244 MB | 10.244 MB |
| config A, same boot | 718 ms | 718 ms |
| **config C frame, rq** | **388 ms** | **304 ms** |

Every cycle count identical to the digit, every transaction count identical,
bytes identical. Wall clock fell by **0.784** against a clock ratio of 220/280 =
**0.786**. **11.33× the MCU, 3.29 fps.**

Two notes on reading that table honestly. The 220 column is the **same firmware**
as the 280 column, taken from the boot that first carried the split build
counters, so nothing in it is a cross-build comparison; that boot measured 388 ms
where M17's section quotes 385, which is the run-to-run spread at this operating
point and not a regression. And `WGT` came back 44 / 44 / 43 across the three
runs, so 44 is quoted rather than the best of them.

#### 260 MHz fails and 280 MHz passes — the PLL explanation, and its refutation

A rung failing while a *faster* rung passes looks like noise, and the temptation
is to call 260 a marginal board and move on. It is not noise. But the explanation
first written into this section was wrong, and this is the record of it being
tested and dropped.

**The theory.** `check_sys_clock_khz()` counts `fbdiv` **down** from 320, so it
returns the **highest** VCO that produces the requested frequency. From a 12 MHz
reference:

| sys | VCO | postdiv | note |
|---|---|---|---|
| 240 MHz | 1440 MHz | 6/1 | |
| **260 MHz** | **1560 MHz** | 6/1 | within 2.5% of the 1600 MHz VCO maximum |
| 280 MHz | **840 MHz** | 3/1 | no higher multiple of 280 lands on an integer `fbdiv` |

260 was the only one of the three whose VCO ran against the top of its range, and
280 gets an *easier* configuration than either neighbour because 280 × 6 is out of
range and 280 × 4 and × 5 are not integer multiples of 12, so the search falls all
the way to 840. That made a clean story, and the section committed it while
labelling the falsifiable half honestly: *260 would pass if forced through the
lower VCO at `set_sys_clock_pll(780 MHz, 3, 1)`* — **not tested, an open claim.**

**The test.** 260 is also reachable at VCO 780 / 3 / 1 (`fbdiv` 65), which the SDK
will never choose because 1560 is found first. `m6` now carries both rows
adjacent in one boot, differing in nothing but the PLL configuration, and prints
the VCO it read back out of `pll_sys` rather than the rate it asked for.

**The forced row fails too.** Three boots, both paths, 1560 and 780 alike.

**And the same table refutes the theory a second way, for free.** With 4 MHz steps
filled in between 240 and 280, `link_clk = sys/2`:

| sys / link | VCO | result, 3 boots |
|---|---|---|
| 240 / 120 | 1440 | 2048/2048 |
| **248 / 124** | 1488 | **link error** |
| **252 / 126** | 1512 | **link error** |
| **256 / 128** | 1536 | **link error** |
| **260 / 130** | 1560 | **link error** |
| **260 / 130** | **780, forced** | **link error** |
| 264 / 132 | **1584** | 2048/2048 |
| 268 / 134 | 804 | 2048/2048 |
| 272 / 136 | 816 | 2048/2048 |
| 280 / 140 | 840 | 2048/2048 |

**264 MHz passes at VCO 1584 — higher than the 1560 that was supposed to be too
close to the ceiling.** So does 176 MHz, which has been in the table at 1584 since
the sweep existed and passes in every boot ever recorded. The ceiling argument was
available for refutation in the original data and was not checked against it.

**What it actually is: a band, not a hole.** The failure is not one rung, it is a
contiguous **dead band from 124 to 130 MHz link**, bounded by passes at 120 and
132 in all three boots. It ignores the VCO split entirely — 248–264 sit on the
high VCO family and 268–280 on the low one, and the band edge falls in the middle
of the high family rather than at the boundary between them. What tracks the
failures is `link_clk`, and nothing else in the table does.

A contiguous band that is periodic in frequency is what a **sampling-phase** fault
looks like. The return path has a roughly fixed flight time, so the phase of the
response against the sampling clock rotates as the rate rises, and one window per
rotation lands badly. The failure text supports this over a dead link: some rows
saw no preamble at all, but others reached **`command CRC mismatch` with 2–13 kB
moved** — bytes crossed the wire and arrived wrong, which is a mis-sampled bit,
not an unclocked link. This is the current hypothesis and it is **not tested**;
saying so is the only thing this section got right the first time.

The shipping consequence is unchanged but now has a measured margin rather than an
assumed one: **140 MHz link sits about 10 MHz above the top of the band**, with
132 / 134 / 136 all passing in between. If the band edge moves with temperature or
part, that is the margin it has to eat.

Two transferable forms, and the second one cost a published claim:

- *When a sweep is non-monotonic, the first suspect is the thing that translates
  the swept variable into hardware, not the hardware.* Still good advice — it just
  happened to point at the wrong translator here.
- **A hole in a coarse sweep is not a hole until the sweep is fine enough to see
  its edges.** 240 / 260 / 280 at 20 MHz spacing showed one failing rung and
  invited a per-rung explanation. 4 MHz spacing showed a band, and the band ruled
  out the per-rung explanation by itself. *Measure the width before explaining the
  cause.*

#### The uniform grid: two bands, and a model with numbers in it

The rungs stopped being hand-picked. **76 → 400 MHz sys in uniform 4 MHz steps**,
which is 2 MHz of link — a band at least 6 MHz wide cannot hide between samples.
Rates the PLL cannot express skip at run time and cost nothing, so nothing is
pre-filtered by hand, which is how a rung gets quietly omitted.

Three boots, identical to the row. **Two bands, not one:**

| | link | sys | measured at |
|---|---|---|---|
| **band 1** | **39.0 – 41.0 MHz** | 78 – 82 | 1 MHz steps, 1.10 V |
| **band 2** | **122 – 130 MHz** | 244 – 260 | 4 MHz steps, 1.25 V |

Everything else from link 38 to 172 passes. The "exactly one band" prediction
written into `m6.c` before the run was **wrong**, and what replaced it is better
because it is quantitative:

| quantity | value | from |
|---|---|---|
| band centres | 40.0 and 126 MHz | measured |
| spacing | **86 MHz** | the two centres |
| round-trip delay τ | **11.7 ns** | 1/spacing |
| bad sampling window Δt | **0.73 ns** / **0.83 ns** | band 1 / band 2 widths, independently |

The widths are the part that picks a mechanism, and they were worth resolving
properly: band 1 was one failing sample in the 4 MHz grid, which is exactly the
evidence quality that produced the last wrong explanation, so it was re-swept at
1 MHz — **39.0 to 41.0, centre 40.0, width 2.5 MHz.**

A bad phase window that is a fixed *fraction* of a bit would make both bands the
same width in MHz. A fixed *absolute* setup-and-hold window Δt gives a width of
`Δt · f / τ`, which grows linearly with frequency: 126/40 = **3.15× wider** at the
upper band. Predicted band 2 width from band 1 is **7.9 MHz**; measured is 8–10.
Solving each band for Δt independently gives **0.73 ns and 0.83 ns** — two numbers
from two unrelated measurements, 13% apart, both an ordinary size for a
setup-plus-hold window. The model is not proven, but it is no longer a story.

The next band sits at link 212 MHz (sys 424), past the wall below, so it cannot be
used as a third test.

#### Does the band move? Core voltage, three steps, cold and warm

This is the question that mattered, because 140 MHz link is **shipped** and sits
9 MHz above band 2's upper edge — an edge that had been measured at exactly one
voltage and one temperature. τ includes the MCU's own pad-and-flop delay, so a few
percent of τ is several MHz of band position.

The same rates, run at 1.20 / 1.25 / 1.30 V, cold and warm, in one boot. Three
boots, **identical to the row in all three**:

| link | sys | 1.20 V | 1.25 V | 1.30 V |
|---|---|---|---|---|
| 118 | 236 | pass | pass | pass |
| 120 | 240 | pass | pass | pass |
| 122 | 244 | **fail** | **fail** | pass |
| 124 | 248 | **fail** | **fail** | **fail** |
| 126 | 252 | **fail** | **fail** | **fail** |
| 128 | 256 | **fail** | **fail** | **fail** |
| 130 | 260 | **pass** | **fail** | **fail** |
| 132 | 264 | pass | pass | pass |
| | **band centre** | **125** | **126** | **127** |

**It moves, monotonically, +1 MHz of centre per 0.05 V** — and upward with
voltage, which is the direction the model requires: more voltage, faster silicon,
smaller τ, bands at higher frequencies. Width stays 8–10 MHz across all three,
as a fixed Δt at fixed frequency should.

**Cold versus warm is a null result.** Every rate agrees between the probe run
before the grid and the probe run after 344 MHz of it, in all three boots. (An
earlier build, whose rate list was missing 252 and 256, showed one warm failure at
240 / 1.20 V. It did not recur in three boots of the corrected list and is
recorded here as unreproduced rather than quietly dropped.)

**And the punchline: 260 MHz passes at 1.20 V.** Twelve for twelve — two paths,
cold and warm, three boots — and through **VCO 1560**, the exact PLL configuration
this section originally blamed. The rate that started the whole investigation was
never broken. It sits inside a band whose position the firmware itself chooses,
by choosing a core voltage.

For the shipped point the answer is reassuring and now has a number: moving band
2's upper edge from 131 to 140 would take about **+0.45 V**, and the sanctioned
ceiling is 0.05 V away. **The band cannot reach the shipping rate within the
voltage range this firmware is allowed to use.**

#### The upper bound, at last

280 was a pass, so it was not a bound — the same mistake, caught for the third
time. The grid ran to 400 and let the board answer.

**344 MHz sys / 172 MHz link is bit-exact, three boots.** The Trion is returning
2048/2048 at **2.65×** its signed-off Fmax of 64.973 MHz.

**348 takes the board with it** — and *how* it fails is the informative part. No
`link error` row is printed at all; the output simply stops. This binary runs from
flash (it does not fit in SRAM, so there is no `copy_to_ram`) and XIP timing was
fixed by boot2 at the boot clock, so flash is a real candidate alongside the core.
Either way it is **not** the link and **not** the tile: a printed `link error`
proves the MCU is alive and executing, and only silence is ambiguous. The MCU
quits while the fabric is still exact.

The stop rule was the core voltage and it held: **1.30 V is `VREG_VOLTAGE_MAX`**,
`vreg_disable_voltage_limit()` is never called, and a `static_assert` now prevents
a future edit from walking past it by changing one constant.

What this does **not** claim is a new operating point. 344 has *zero* margin — the
next 4 MHz step hangs — so it is a bound, not a rate to ship. The gap between the
band's top (131) and the wall (172) is where a future operating point lives, and
picking one is an `m7` question that has not been run. For reference, the clock
ratio alone would put config C at **~266 ms / 3.76 fps at 320 MHz**, against 304 ms
today.

#### The voltage floor: what an operating point would actually cost

Every rate above 280 up to this point was measured at 1.30 V, because that is what
`VREG_ABOVE_280` says — and **`VREG_ABOVE_280` is a threshold this firmware invented
rather than measured.** So is `VREG_ABOVE_220 = 1.25`. Both were set generously on
purpose, to stop the MCU becoming the confound in a question about the Trion, and
that was right for those runs. It is the wrong thing to inherit into a shipping
rate: whether an appliance sits permanently at `VREG_VOLTAGE_MAX` or two steps under
it is a reliability decision, and nothing here had measured which one 320 MHz needs.

276–344 by 4, at each rail. Three boots per arrangement, identical every time:

| core V | bit-exact to | first failure |
|---|---|---|
| 1.30 | **344** | 348 — the wall, and `VREG_VOLTAGE_MAX` is already here |
| 1.25 | **340** | 344 wedges |
| 1.20 | **332** | 336 marginal, 340 wedges |
| 1.15 | **312** | 316 wedges, mid-row |
| 1.10 | *not run* | excluded by monotonicity — cannot beat 1.15's 312 |

**Zero non-exact transactions in any boot at any rail.** Every failure up here is
the core going quiet; the link and the tile never got a single answer wrong.

The shape is the result. The top three rails are worth 4 and 8 MHz; dropping from
1.20 to 1.15 costs **20**. The ceiling *saturates* — the rail buys a lot up to 1.20
and almost nothing after — and the first half of this sweep, which had only the top
three rows, was written up here as "barely a function of the rail at all." That was
wrong, and it was wrong in the ordinary way: three points on the flat part of a
curve look like a flat curve.

Two things follow, and they are why the probe was run:

- **A 320 MHz operating point needs 1.20 V and cannot have 1.15.** Monotonicity
  across every measured step settles 1.10 V without spending a boot on it.
- **1.20 V is two steps below `VREG_VOLTAGE_MAX`.** So 320/160 would not sit at the
  regulator's ceiling — it sits one step above the minimum that works, with 12 MHz
  of clean margin under it and the whole of 1.25 V still in reserve. The reliability
  objection to an elevated rate is answered, and answered with numbers.

None of which makes 320 an operating point yet. All of it is `m6`: one block, ~4 ms,
a 39 kB burst on one core. `m7` is a sustained ~300 ms two-core frame moving
10.244 MB, and its own ladder still stops at 280000 with raise-only voltage
handling. The 348 wall may not even transfer — `m6` runs entirely from XIP flash
while M7g places its hot paths in SRAM.

##### A third case for the silence rule

The diagnostic rule had two branches: a printed `link error` proves the MCU is alive,
so the fault is the link or the tile; silence is the core or the flash. **336 MHz at
1.20 V is neither.** Three consecutive `printf`s vanished — both of the rate's rows
and the following voltage-transition line — and then the board carried on and printed
the *next* rate correctly. It printed those rows in one boot of three and swallowed
them in the other two. Execution plainly continued, so the core had not stopped.

So: **a missing row is not proof of a stopped board.** Only the wedge means what the
rule said silence meant — silence that never ends, with the board still enumerated
and refusing both the `B` hotkey and the 1200-baud touch, which is what
`host/bootsel.py` calls the hard wedge and clears with a real power cycle.

##### The ordering was the experiment design, and it changed once

Under-volting does not fail politely: it stops the board, forfeiting every row after
it. The first list ran three passes grouped by voltage, descending, rates ascending
inside each — chosen against the specific fear that 1.20 V might quit down at 290 and
take the untested top of the other two curves with it. It cost one pass to learn the
fear was unfounded: 1.30 went clean to 344, 1.25 went clean to 340 and then stopped
dead, forfeiting all of 1.20.

With the ceilings then known to sit within 12 MHz of each other, **interleaving
became strictly better** — rate outermost, the rails inside — because a wedge can
then only truncate rates above itself, and every rate below comes back with all of
its readings. That is a reordering justified by data that did not exist when the
first order was picked.

And then it changed **back** for the 1.15/1.10 half, which is the same principle and
not a change of mind. Interleaving wins when the curves end near each other. These
two were expected to end far apart, and interleaved, the weaker rail would truncate
the stronger one's curve at every rate above its own ceiling. Grouped, each gets the
full range, safer rail first.

Attribution needed no extra printing in either arrangement: the list is deterministic
and every row carries its own rail in the `V` column, so the row that wedged is the
first one not printed.

#### The prediction, and how it did

Written into `m6.c` before the run: **the MCU will be fine and the Trion will be
what stops.** Half right, and wrong in the interesting half. Nothing stopped —
the Trion closed at 140 MHz, **2.15×** the static analyser's 64.973 MHz, up from
1.94× — and the one rung that did fail failed on the *MCU* side, at the PLL. The
device that was expected to be the limit has now not been the limit twice.

Voltage gained a second step, 1.25 V above 220 MHz, purely to keep the MCU from
being the confound. It does nothing for the Trion, which is a separate device on
a supply this firmware cannot reach. While adding it, the older comment claiming
1.20 V was "four steps below" the 1.30 V cap was corrected: `hardware/vreg.h`
goes 1.20 = `0b01101`, 1.25, 1.30 = `VREG_VOLTAGE_MAX`. It is **two**. The margin
was half what the comment claimed, which is the sort of thing worth finding
before rather than after raising it again.

#### What this is still not

Everything the previous clock section said about holding this loosely applies
more, not less. It is one die, three boots, at room temperature, with the Trion
now at 2.15× its signed-off Fmax on an unraised supply. Bit-exactness over
~5,600 transactions per boot is strong evidence about *this board today* and is
not timing closure. The shipping form remains a ladder that falls back in two
seconds — 280, 240, 220, 200, 176, 150, 130, 110, 90 — and config A stays pinned
at 150 so the primary verdict remains comparable to every prior milestone.

---

### M18 — the teacher swap, and a guard for a mistake that does not look like one ✅ *2026-08-10: shipped and bit-exact on the board; the guard fired, and was broken until it did; and one real book, opened then closed, ranks the way the swap was for — by 2.42 sd on the difference axis, from 0.40 of the frames*

Every milestone from M5 to the clock work was about making the board produce the
*same* 512 floats faster. This one changes which 512 floats they are.

#### Why

The shipped student was distilled from **CLIP ViT-B/16**, and ViT-B/16 reads a
query as a bag of words. `tools/probe_teacher.py` gate 2 is the cheapest
demonstration: asked to rank *"an opened book"*, it prefers the CLOSED frame. So
does the student that inherited it — at **−4.64 sd** on the opened-closed axis,
which is not a near miss, it is a confident inversion. For an appliance whose
entire input is a typed sentence, that is the wrong failure to keep.

`tools/probe_project.py` showed **SigLIP 2 SO400M** passes gate 2 and keeps
passing it after a joint PCA down to the board's 512, and
`tools/probe_inherit.py` showed on a 30 000-image sieve run that the property
survives distillation. `model/runs/so400m-full-a05/` is that recipe at full
scale: 118 287 images, centring alpha 0.5.

#### What shipped

`so400m-full-a05`, epoch 37, quantized and exported at the same int4 + `ends8`
settings as the incumbent. **780,720 bytes, crc32 `0xF368CC6E`** — byte-identical
across two exports, and the same size as the ViT-B/16 blob, because the
architecture did not move. `firmware/CMakeLists.txt` and `rtl/Makefile` now both
point at it.

#### `model/quantize.py` FAILED, and the FAIL was not moved

The gate is `mean > 0.995 and min > 0.98` on the embedding cosine between fp32
and simulated int8. On the new checkpoint:

| | `train2017` | `so400m-full-a05` |
|---|---|---|
| embedding cos, mean | 0.99838 | **0.99151** |
| embedding cos, min | 0.98526 | **0.88732** |
| head layer | 0.99864 | **0.99003** |
| gate | PASS | **FAIL** |

Three things were checked before anything was concluded. Quadrupling the
calibration set (512 → 2048 images) moved the mean 0.99151 → 0.99275, so it is
not calibration. Weight scales are already per-output-channel
(`model/quantize.py:77`), so it is not granularity. What it is:
`tools/probe_project.py:141 fit_pca` returns `vt[:out].T` **unwhitened**, so the
a05 output components have strongly anisotropic variance and a single activation
scale per tensor fits them worse. It is a property of the space, not of the
export.

The gate was **not loosened**. It had already been re-tuned once after seeing
numbers (`model/quantize.py:308-319`), and a threshold that moves whenever it
fires is not a threshold. Instead the proxy was replaced by the measurement it
is a proxy *for* — `model/evaluate.py`, camera geometry, all of val2017:

| | queries@AUC≥0.80 | mean AUC | cos-to-teacher |
|---|---|---|---|
| teacher SO400M | 67 / 67 | 0.983 | 1.000 |
| student fp32 | 61 / 67 | 0.892 | 0.666 |
| **student int8** | 60 / 67 | **0.896** | 0.665 |

**int8 is not worse than fp32 on the task.** A 0.887 embedding cosine is a real
loss of fidelity to the fp32 student and it does not reach object accuracy. So
the FAIL stands in the log as a fact about the space, overridden by evidence
rather than by an edit. `tools/probe_int4.py` puts the shipped int4 + `ends8`
row at 61/67 and **91% retention** — against the ViT-B/16 student's 94%, the
price named in the plan and paid.

#### Gate 2 did not reproduce, and the honest version is better than the claim

The reason for the whole swap was gate 2, so it was re-run on the checkpoint
being flashed rather than on the sieve student that made the case.

  It fails. `'an opened book'` scores OPEN **−0.1466** against CLOSED
**−0.1457** — wrong by **0.0009**, which is **0.09 sd** against the 93-frame
noise floor. A tie, broken against us.

The axes say the opposite, loudly. On the deployed path — seven ensembled
templates and M12's difference axis, which is what `host/demo.py` actually sends:

| student | teacher | opened-closed | spread-front |
|---|---|---|---|
| `train2017` | ViT-B/16 | −4.64 sd | −0.22 sd |
| `nce0.3` | ViT-B/16 | +0.19 | +2.73 |
| `so400m-s30k` | SO400M a1, 30k | +3.35 | +3.40 |
| `so400m-full` | SO400M a1, 118k | +1.32 | +1.93 |
| **`so400m-full-a05`** | SO400M a0.5, 118k | **+5.88** | **+7.33** |

First student to be far outside the noise on both axes at once, and it moves
*both* frames (OPEN +2.79 sd, CLOSED −3.09 sd) rather than finding one of them
odd — the teacher's shape, which is what `tools/probe_noise.py` was written to
tell apart. So the swap is carried, but by the axis and not by the gate, and
`tools/probe_inherit.py:92-120` records it that way. Quoting the gate here would
be quoting a measurement that did not happen.

#### The hazard this creates, which is the real work

Both spaces are **512-d**. A ViT-B/16 text vector dotted against an a05 image
vector raises nothing, produces no NaN, and returns plausible scores that are
noise. `tools/teacher_swap.py` names it: *not a weaker comparison, a meaningless
one, and it will not look like an error.* `host/demo.py` hardcoded ViT-B/16 and
its docstring asserted that space as the reason the comparison was valid — an
assertion that becomes false at the moment of reflash, silently.

Two mechanisms, chosen over the alternatives because together they cover both
halves of the question:

**1. `export.json`, beside the blob.** `model/export.py` writes the run, the
`open_clip` spec, the basis filename, the width, and the CRC of the exact bytes
in `weights.bin`. `host/demo.py --export` reads it and **derives** its encoder;
it can no longer pick the wrong space because it no longer picks. The lookup
from teacher string to (spec, basis) lives once, in the new stdlib-only
`model/spaces.py`, which `tools/probe_inherit.py` now imports instead of owning —
the count of copies went down while a consumer was added.

**2. The board declares its own blob.** `firmware/m9.c` hashes `fgx_weights[]`
with the existing `ft_crc32()` (`firmware/frame.c:190` — IEEE `0xedb88320`, init
and final invert, bit-compatible with `zlib.crc32`) and prints it. `demo.py`
parses that line and **refuses to send the query set** on mismatch, printing both
values. This is the half the sidecar cannot cover: a *stale flash* is a correct
file against the wrong silicon.

A missing CRC line is also a refusal, deliberately and with no override flag. The
dangerous case is a build old enough to predate the check, which is exactly a
build old enough to be the previous student.

The calibration files got the same treatment, because they were the original
version of this trap: `model/cache/thresholds_val2017.json` and
`eval_int8_val2017_camera.npy` are ViT-B/16-space and **neither filename carried
the run**. They are now `thresholds_<run>.json` and `eval_int8_<run>_camera.npy`,
`demo.py` derives both defaults from the export rather than holding fixed paths,
and each sidecar's `run` is checked against the export's — a filename is a
convention, and this is a check. `rtl/Makefile`'s `MODEL` default was repointed
for the same reason: `$(VEC)/cases.hex` depends on `$(MODEL)/weights.bin`, so the
two files disagreeing is not an error, it is a testbench passing against a model
the board is not running.

#### Verified on the laptop

- `test_encoder` bit-exact against the numpy golden on the new blob.
- `tb_gemm`, `tb_gemm_link`, `tb_gemm_link_wide`, `test_plan`, `test_wire` all
  green on vectors **regenerated from** the new `weights.bin` — re-derived, not
  reused.
- The a05 export is byte-identical across two runs, and re-exporting `train2017`
  after the sidecar change produced a byte-identical `weights.bin`, so the new
  write touched nothing.
- `demo.py` refuses a `thresholds`/`embeddings` file from the other run, refuses
  an export directory with no `export.json`, and encodes correctly on both
  paths — SigLIP 2 + PCA for a05, plain ViT-B/16 for `train2017`.

#### Verified on the board, 2026-08-10 — and the guard could not have passed

- **`m6`: 31 rows bit-exact 2048/2048, zero non-exact**, 150 through 316 MHz.
  The single link error is 320 MHz at 1.15 V, above the 1.15 V ceiling M17's
  sweep already recorded at 312 — a floor being found, not a regression.
- **`m7`: PASS** — all 8 layers bit-exact in all six modes of *both* link
  configurations, int32 DRAIN and rq alike, **512/512 embedding floats exact**,
  on a frame off the camera. **303 ms/frame, 11.37× the MCU**, against 304 and
  11.33× on the old student: the swap costs no time, which is what an identical
  architecture predicted and is worth having measured rather than assumed.
- **`m9`: `weights : 780720 B, crc32=0xF368CC6E (270 ms to hash)`**, equal to
  `export.json`. 270 ms is well inside the budget, so `frame.c`'s CRC stays
  bitwise and the table stays unwritten.
- **The negative test fires.** `--export model/runs/train2017/export` against
  the a05 flash refuses, prints both CRCs, names the run and spec it was
  encoding for, and exits 1 — *before* the vectors go over the wire. It reaches
  the guard rather than dying earlier on `train2017`'s absent threshold and
  embedding caches, which is exactly what deriving those defaults from the
  export's own run was for.

**And the guard was broken.** The first run refused a board whose CRC matched,
printing `board 0xF368CC6E` against `export.json 0xF368CC6E` — identical — and
refusing anyway. The comparison was `board != export["crc32"].upper()`, where
`board` was built as `f"0x{...upper()}"`: `.upper()` uppercases the `0x` prefix
too, so one side read `0X` and the two strings could never be equal. It failed
closed, which is the right direction and means nothing unsafe was ever sent, but
**a check that cannot pass is not a check** — it would have been switched off by
the first person it inconvenienced, and that person would have been right. Now
compared as integers.

Worth naming why the offline tests missed it: all four of them exercised the
*mismatch* path, because that is the path the milestone is about. Not one
asserted that a matching pair is accepted. A guard needs both halves tested, and
the half that looks boring is the one that decides whether the guard survives
contact with a bench.

The bench cost one other thing, and it was self-inflicted twice over: the first
`m6` run errored at *every* rate, which reads exactly like a dead board. The
bitstream was `rtl/build/gemm_top.hex`, the last untagged build, frozen at M14 —
copied out of `host/m6.py`'s own docstring example, which was still stale even
though the `argparse` default beside it had been repointed to
`gemm_top_m16.hex`, *with a comment explaining that this precise mistake had
already cost a flash and a full sweep once*. The crc32 line at the top of the
log is what tells the two apart. `demo.py` and `m8.py` turned out to still
default to the M14 wide netlist as well; all four files, defaults and docstrings
both, are repointed now.

#### A real book, opened and then closed

The last check is the one the swap was for, and it is the only one that cannot
be run without a hand on the bench. One 400-frame run, background frozen on the
empty scene, then **the same volume** open for 150 frames and closed for 130 —
same book, same light, same position, so the only thing that changes is the
thing being asked about. The first attempt at this compared two *different*
books across two runs, and the control prompt `a book` preferred the closed one
by 0.0417, which is exactly the confound a single volume removes.

| segment | `an opened book~` | `a closed book~` | ranked first |
|---|---|---|---|
| empty, 30–89 | −0.06 | +0.03 | — (no match, 0 false fires) |
| **open**, 100–249 | **+1.20** | −1.71 | opened |
| **closed**, 270–399 | −1.21 | **−0.79** | closed |

Read down a column rather than across a row — one prompt against two frames is
background-free, where one frame against two prompts carries each prompt's own
offset. `an opened book` scores **+2.42** higher on the open frames;
`a closed book` scores **0.92** higher on the closed ones. Both orderings are
right, and they are right about the same object.

Three things this does not say, all of which matter more than the two that it
does. **It sits on the threshold**: only **60 of the 150** open frames cleared
z 1.23, because the mean of +1.20 lands just under it. The ranking is stable and
the detection is not. **`a closed book` never fires at all** — −0.79 across the
closed segment, nowhere near the threshold; it wins its comparison without ever
crossing the bar, which is a ranking result and not a detector. And this is the
*contrast* query, `an opened book / a closed book / a book`. The bare prompt,
measured from the board's own dumped 512 floats on two frames, loses the open
book by **0.0001** — reproducing the laptop's gate-2 FAIL (0.0009) on hardware,
to the same sign and nearly the same magnitude. Resting the M18 claim on M12's
difference axis rather than on gate 2 was the right call, and this is the
measurement that shows why: the axis clears by 2.42, and the gate does not clear
at all.

---

### M19 — the bench itself was the instrument, and it was not calibrated ✅ *2026-08-10: boundaries become data, the error bar starts measuring what varies, and a wording sweep says the wording is not the lever*

M18 ended on a run whose boundaries had been reverse-engineered from the score
trace by eye. This milestone is about the fact that this was normal, and that
every conclusion drawn that way is an interpretation of a measurement rather
than a measurement.

#### The thing that broke

Two A/B glass-of-water runs on 2026-08-10 **disagreed about the sign of the
effect.** The board was not the reason. The operator was told "place it in two
minutes", placed it at some unrecorded moment, and the segments were then
guessed afterwards. That fails exactly when the signal is weak — which is when
the run was worth doing.

#### `host/cue.py`

Spawns `demo.py`, watches its frames go by, speaks each scene change (macOS
`say`, with a banner and a bell), and **records the frame number at the moment
it cued** into an `<out>.cues` sidecar. Frames within `--settle` of a cue are
dropped, because a hand is in shot. `ab.sh` derives the whole invocation from
two phrases and an optional neutral.

Replayed against the recorded M18 book run it reproduces **+2.41 against the
+2.42 measured by hand**, the difference being the 20 settle frames it drops.
That is the check that says the tool did not change the measurement.

Three bugs in it are worth keeping, because each is a way an instrument lies
without erroring:

- **It scheduled off the board's `background:` line.** `m9` reprints that line
  about every 100 frames, so the first one to arrive announces frame 99, not
  frame 30. The first hand run cued 70 frames late and truncated its second
  scene to 55 of the 120 frames it asked for. Nothing needed parsing — the
  freeze happens at `--bg-tau`, a number the caller chose, so cue.py now takes
  it and forwards it to demo.py so the two cannot disagree.
- **argparse handed an unknown flag's value to the query list.**
  `--snap-every 15 "a hand"` forwarded a valueless `--snap-every` and asked the
  board about a prompt reading `"15"`. Nothing errored. The `=` form survives
  intact, so it is now required.
- **The `.cues` sidecar recorded cue.py's own settings and not demo.py's**,
  which is backwards: cue.py's are visible in the segment boundaries and
  demo.py's were visible nowhere. A run came back with no dumps in it and the
  artifacts could not say whether the flag had been passed.

#### `--hold 120` was a guess, and the error bar was measuring the wrong term

Three runs say **13 frames already pin a segment mean to ±0.05**, while two runs
of the same experiment an hour apart **disagreed by 0.85 on an effect of 0.08.**
The variance is *between visits*, not within them. So the frames were moved:
hold 30, and `--repeat 3` to spend the rest cycling A/B/A/B/A/B, with the report
pairing by label across repeats. Averaging harder inside one visit buys nothing
here; visiting more times is the only thing that shrinks the interval that
matters.

#### `tools/probe_prompts.py` — and the answer it gave

Scores arbitrary phrasings against the 512-d vectors **the board already
dumped**, so a wording experiment costs a laptop minute instead of a 3.5-minute
board session, and every candidate is measured against *the same frames* —
removing the between-visit term that had just been shown to be the larger one.
`host/demo.py` gains `--emb-every N`: the 512 floats without the picture, 2.8 KB
against 46.8 KB, so vectors can be collected for a whole session.

Validated twice before being believed. Against the M18 book logs it reproduces
that milestone's numbers to the digit (bare `an opened book` loses by 0.0001,
the contrast form wins by 0.0809); against a fresh run it parses the board's own
frozen `background:` line and agrees to 0.001, which is what says the sweep is
in the board's space and not some other 512-d one.

**Result of 30 candidates over 60 frames: the best wording in the list is the
one already shipped**, at d −3.72 and AUC 0.00 — a perfect discriminator. `a
fist`, the hypothesis the list was built around, cannot tell the two scenes
apart at all (d −0.00). The wording is not what is wrong, which is what sent
M20 after the *rule* instead.

---

### M20 — gate on presence, rank the states ⚠️ *2026-08-10: shipped, and its premise is falsified on hardware — 16.1% against 79.4% for the rule it replaced*

**This milestone shipped and should not be relied on.** It is kept in full
because the reasoning was sound, the offline evidence was real, and the way it
failed is the useful part.

#### The observation it was built on

Two queries, two different questions, and the board had been asking only one.
A **bare prompt is a presence detector**: `a hand` reads z +9..+24 whether the
hand is open or shut, with d between the two scenes about zero. A **contrast
query is a classifier**: `an open hand / a closed hand / a hand` gets d −3.72
between the scenes but z ≤ 0 in *both*, because its baseline is an empty desk,
and an empty desk is as much "not an open hand" as a fist is. So ranking was
right 176/180 while the board fired 21/180 — **it knew, and said nothing.**

#### What shipped

`FGX_Q_GATE` queries gate, on the **weakest** of them so several gates mean all
must hold; `FGX_Q_CLASS` queries are then ranked against each other with no
threshold of their own. The record grows a role word, and `recv_queries()`
computes the expected length from the dim actually sent so an older host is
named rather than silently misparsed.

The LED gets the same two axes: brightness from gate z over threshold, hue from
the state margin. **The sign has to come from a fixed query, not the winner** —
the first cut passed lead-minus-runner, which is a maximum minus a runner-up and
therefore never negative, and 159 frames of bench printed h0.57..h1.00 without
once reaching green.

`cue.py` had the matching bug in its display: one softmax over every query, so
the gate took ~100% on every frame and the two states sat at 0.0% whatever the
hand did. **That is what "it can only detect a hand" looked like from the
operator's chair while the MATCH column was switching correctly with margins up
to 17.**

#### On the board, and the premise inverting

The gate works: **180/180 against 21/180.** Then open-vs-closed ranking came
back **104/180**, not the 176/180 the offline sweep promised, and three decision
rules score the same — so it is not the rule. The state queries move 0.002 and
0.008 in cosine between the scenes, against a margin that drifts by 4 z.

And in that same run **the plain gate query separated the two poses perfectly**,
AUC 1.000 at d 3.96 — the opposite of what M20 was built on.

#### M20b — make the next hang name its stage ✅

Eight hangs had gone by with no cause. The frame loop marks which call it is
inside in `watchdog_hw->scratch[0]` and the frame in `[1]`, and an 8 s watchdog
reboots and names the stage on the next banner.

**Armed at the banner, not at the frame loop**: the 17:58 wedge happened during
start-up with the watchdog still disarmed and took a `uhubctl` at the wall. Not
one line earlier either — above it is the wait for `stdio_usb_connected()`, and
a board powered up with no host attached is not hung, it is waiting. The two
legitimate waits feed it: the host's bitstream, and the exposure ramp, whose 40
frames at ~150 ms would otherwise spend six of the eight seconds. Measured
start-up headroom at its worst stage is **4.6 s**, at the 3378 ms reference
encode.

`'W'` hangs on purpose, so the recovery has been seen to work once. **Stalling
the host does not do it** — SIGSTOP on demo.py ran the board through to frame
49, because pico's `stdio_usb` drops output rather than blocking, which retires
the theory that a dead host is what wedges the board. `'S'` had been in the
banner since M19 and was never in `poll_host`'s list, so it did nothing.

This buys the diagnosis, not the fix. **It had not caught one as of this
milestone**: the board vanished from USB three more times with no watchdog
report and a fresh banner each time, which points at USB enumeration rather than
a firmware hang. That thread continues in
[#2](https://github.com/kazunori279/fpga-open-vocab/issues/2), where the host
turns out to have been reading a port the rebooted board no longer had.

---

### After M20 — the premise, falsified twice, and the drift that turned out not to exist ✅ *2026-08-11: two-stage 16.1%, bare pair ranked 79.4%, and the board is stable to 0.065 z over four minutes*

#### `tools/score_cue.py`, because one run supported three conclusions

The hand run fired its gate 180/180, got the two-stage answer right 104/180, and
its gate query alone told the two poses apart 180/180. **M20 works, M20 does not
work, and M20 is aimed at the wrong query — all true of the same 180 frames.**
So the scorer prints all three, with the per-segment table underneath, because
that table is where the between-segment drift that swallows the effect is
visible.

**AUC rather than accuracy** for the separation, since accuracy needs a
threshold and the threshold is the question; the best fitted cut is printed
beside it and the gap says how much of the score is the run. Raw cosine is
recovered from z, mu and sd rather than re-measured, so the ranking can be asked
in the teacher's own units too.

Two bugs in the scorer, both of the kind that produce a plausible number:

- **The fitted cut tried only one direction.** Every query separating the scenes
  the other way came back at exactly 50.0%, which reads as useless. `a book`
  printed 50.0% while separating opened from closed at **AUC 0.999**.
- **The ranking set was read off `role == "state"`**, so a run of two plain
  phrases printed no ranking line at all — and ranking two plain phrases is the
  run that finally beat the two-stage rule. A query belongs to the ranking set
  if a scene is named after it, contrast `~` or not.

And `cue.py` now **keeps the previous run** instead of overwriting it. demo.py
opens `--out` for writing, so starting a run destroyed the last one, and that
had cost two logs that were still the input to an analysis not yet done.

#### The scoreboard, one book, 180 frames

| rule | accuracy |
|---|---|
| two-stage, gate + contrast — **what M20 shipped** | **16.1%** |
| contrast pair ranked, no gate | 66.1% |
| **bare pair ranked, no gate** | **79.4%** |
| contrast pair + one visit per state, held out | 90.0% |
| bare pair + one visit per state, held out | 84.2% |
| bare pair, best cut fitted to the whole run | 100.0% |

A gate can only remove answers, so the gap is its price: 119/180 down to 29/180,
because **`a book` reads −4.87 z with an opened book in shot** — the gate shuts
on one of the two classes it exists to admit. A state-dependent gate is not a
weak gate, it is the wrong mechanism.

The user's report that the camera distance was constant through the bench ruled
out apparent size as the confound, and the direction was backwards for it
anyway: the compact form — fist, closed book — is *smaller* yet scores higher.

#### Ordering is right; zero is in the wrong place

The bare book pair separates **every** opened frame from **every** closed one —
AUC 1.000 — and still scores 79.4% at a margin of zero, because the right cut is
**−3.79**. Same in cosine and in cos-minus-baseline, both also AUC 1.000 and
both worse at their own zero, so the room's spread is not what displaces it.

#### The drift, measured and dismissed

Margins on the *same* state wandered from −0.90 to +11.61 over four minutes,
which read as an instrument going stale — and if true, no fixed cut could ever
hold. **It is not true.** 500 frames on a closed book nobody touched, background
frozen at frame 30 so z ≡ 0 by construction there:

| query | Q1 | Q2 | Q3 | Q4 | span |
|---|---|---|---|---|---|
| `a closed book` | −0.24 | −0.91 | −1.56 | −1.55 | −1.31 |
| `a red apple` | −0.31 | −0.77 | −1.82 | −1.92 | −1.61 |
| `an empty desk` | −0.27 | −1.02 | −1.83 | −2.05 | −1.78 |
| `an opened book` | −0.34 | −1.00 | −1.63 | −1.58 | −1.24 |

Every query falls together by about 1.5 z and settles by Q3; take the common
mode out and none moves more than 0.3. The camera's own snapshot line agrees it
is the sensor: mean RGB creeps 129 122 122 → 132 126 126 over the same 470
frames, a 1.5% global lift, which at a frozen sd of ~0.005 cos is exactly the
1.5 z seen.

**A common-mode shift cancels in a difference, and it does.** Closed-minus-
opened reads −0.099 / −0.085 / −0.069 / −0.034 across the quarters: **0.065 of
drift in four minutes against 0.557 of frame-to-frame noise**, an eighth of a
single frame's jitter. So the 12.5 z between visits to the same state was the
scene being physically re-staged, not calibration decay.

**What this run cannot say, recorded because the temptation to read it is real.**
It says nothing about accuracy. The background was frozen on the same closed
book it then scored, so mu *is* that scene and all four queries sit at z ≈ 0 with
only noise between them: `a closed book` is top-1 in **24.7%** of frames against
a chance of 25.0%, and that is the arithmetic of the setup rather than a
measurement of the model. The stillness that makes a held-still scene a clean
drift probe is the same property that leaves no signal in it to discriminate.
Score discrimination on a run where the scene changes and the background was
frozen somewhere else.

#### `host/board.py` — asking whether the board is there

`cue.py` and `ab.sh` both checked for the board by globbing `/dev/cu.usbmodem*`.
This desk always has a Tiliqua R5 on the same hub enumerated as exactly that, so
**neither check could fail**: they passed with the board absent, skipped the
`uhubctl` recovery they existed to trigger, and handed the failure to demo.py a
minute later — the whole minute they were written to save. It cost two runs of a
scene an operator was holding still.

cue.py's comment argued the glob was fine because being fooled costs nothing,
since demo.py runs the real check anyway. **That is true of a false negative and
false of a false positive**, and only the false positive was reachable here.
`RP2350_VID` and `pick_port()` moved into `host/board.py`, which imports pyserial
and nothing else so a caller can ask before paying a minute for the teacher, and
cue.py runs the recovery rather than printing it.

#### What M21 has to be

Not a better wording (M19), not a better rule over the same scores (M20), and
not continuous recalibration (ruled out above). The ordering is already right
and the boundary is not at zero, so **M21 is a learned per-scene reference** —
and the gate has to come off the state queries entirely. Its bench must change
the scene with the background frozen somewhere else.

---

### M21 — learn the reference, and put two edges on the presence stage ✅ *2026-08-11: 120/120 held out on the board, against 90/180 for ranking the same frames*

M20 gated on a *query* and ranked in raw z. Both halves were wrong for the same
reason, and M21 replaces both with two axes taken out of the frame itself:

```
level = mean(z)          "is anything here at all"
c[i]  = z[i] - level     "which of the things I was shown is it"
```

The common mode cancels out of `c[]` for any number of queries, the way a
difference of two queries cancels it for two — worth **44.2 → 84.2%** and
**87.5 → 95.8%** on the two recorded book runs (`tools/probe_rule.py`). And
`level` is precisely what centring throws away, which is why presence is that
leftover and not a third centred class: enrolling "absent" as a class instead
scores **42.3%** and **73.1%**, losing 56 and 33 real frames to it.

Neither axis carries a threshold. The operator **shows** the board each scene —
`'1'`..`'6'` for the class named by that query, `'0'` for the empty one — and it
keeps what it saw. `host/cue.py --enrol` presses the keys on schedule, so the
first visit to each scene teaches and every later visit is held out by
construction, and the frames go into the `.cues` sidecar so the offline scorer
knows which visit was spent teaching.

#### Three benches, and the first two were mostly about the bench

**Bench 1 measured M21 in the one configuration where it is an identity.**
`ab.sh` built the contrast form `"A / B"` and `"B / A"`, and two contrast queries
from two phrases are **exact negatives** of each other — `"an opened book~"` read
−0.082 ±0.0061 against `"a closed book~"` +0.082 ±0.0061. Their mean is 0 on
every frame: `lvl+0.00` was the only value in 300 frames, `level` could not move
and centring subtracted nothing. It still beat ranking, **75.0% held out against
65.0%**, because the reference is not at zero — but that is one of two claims and
the other was untestable, and **nothing in the log said so**. A degenerate
enrolment is invisible in the frame lines; the board goes on printing confident
verdicts. Three guards came out of it: the board prints its enrolment's geometry
(`2 classes, nearest pair X apart, presence span ±X`) and shouts in capitals if
either is under 0.05, `ab.sh --enrol` sends the phrases **bare**, and
`probe_rule.py` flags a flat presence axis rather than reporting the majority
class as an accuracy.

**Bench 2 was a real measurement, and the presence stage failed M20's way.**

| | held out |
|---|---|
| the board's M21 as shipped | **89/120 74.2%** |
| the same frames, presence stage removed | **108/120 90.0%** |
| ranking the two bare phrases, no gate | 180/180 100.0% |

The 19 frames the stage shut on were all `an opened book` — **one of the two
classes it exists to admit, which is exactly M20's failure in a new costume.**
The cause is the one centring was introduced for: drift lives in the common mode,
the presence axis **is** the common mode, and the third visit to that scene
walked from 1.12 of the enrolled span down to 0.25 and crossed a cut sitting at
0.50. The centred axis was 30/30 on those same frames.

It also settled a question the earlier probes had left open. `probe_rule.py` had
found one captured frame beating a thirty-frame average, 88%/97% against 75%/79%,
by a mechanism nobody could name. On the board, with the board's own arithmetic:

| frames averaged | 1 | 5 | 8 | 12 | 16 | **20** |
|---|---|---|---|---|---|---|
| held out | 90.0 | 89.2 | 92.5 | 96.7 | 99.2 | **100.0%** |

So the earlier result was about **which** frames, not how many — probe_rule's
thirty spanned a whole visit including the parts where the operator's hand was
still leaving. Averaging wins once the window is placed, and placing it is
cue.py's job. The empty scene's window had to move: widening it forward from two
frames before the baseline ends walks straight into the object being put down,
and the absent level drifted **+0.21 → −4.81** as the window grew 1 → 28. That is
the object's level, not the room's. It now *ends* on the last baseline frame, and
cue.py refuses a `--hold` or `--baseline` too short to contain a window.

#### Two edges, not one

A single cut has no defence against an axis that drifts. Fitting it lower is not
the fix either — that trades the 19 frames for false presence later. So the stage
became **hysteresis on a fraction of the enrolled span**, 0 where the empty scene
read and 1 where the objects did, which also lets the division carry the sign
that `(span >= 0) ? lvl >= cut : lvl <= cut` carried by hand. On all three
recorded runs the objects read **lower** than the empty scene — +0.11 against
−10.52, +0.05 against −4.45, −8.26 against +0.21 — so a hardcoded `lvl >= cut`
would have been wrong every time.

| enter | leave | held out | empty desk called present |
|---|---|---|---|
| 0.50 | 0.50 *(one cut)* | 101/120 84.2% | 0/26 |
| 0.50 | 0.25 | 105/120 87.5% | 0/26 |
| **0.50** | **0.15** | **120/120 100.0%** | **0/26** |

0.15 sits in a gap the run **measures** rather than assumes: the empty baseline
reached +0.091 and the lowest object frame +0.245. A span under 0.05 has no axis
to measure on at all, so the stage stays open and lets the geometry guard do the
talking — 150 lines of "nothing there" is a worse failure than a readable log.

#### Bench 3 — on the board, and this time ranking had something to lose

Enrolment: empty −0.10, `an opened book` −7.32, `a closed book` −7.63, nearest
pair **2.35** apart, presence span **−7.37**. No guard fired.

```
enrolled  : rule live from frame 134 (second reference lands there);
            54 earlier scored frames are the old rule's and are not counted
enrolled  : MATCH correct on 126/126 (100.0%)
            enrolled from   6/6   (100.0%)
            HELD OUT      120/120 (100.0%)

rules       rank the states, no gate    90/180 (50.0%)
```

**Ranking is at chance on this run**, and that is the result that makes the other
one mean something. `an opened book` scores higher than `a closed book` in **all
six segments**, closed ones included, so a fixed ranking rule answers "opened"
every time and is right exactly half the time. Bench 2's ranking was 180/180 on
the same two phrases and the same student. The difference is the scene: this
book, this desk, this light carry a per-scene offset of roughly +1.7, and **a
rule with no reference cannot absorb an offset while a learned reference measures
it.** That is the whole argument for enrolment, and it took a run where ranking
lost to state it as a measurement rather than a preference.

The hysteresis did work rather than merely not hurting. **34 frames sat in the
0.15–0.50 band** — below the old single cut, kept open only by the low edge — and
they are segment 220–259, `an opened book`, the same class and the same position
in the schedule that failed in bench 2:

| segment | | b (fraction of span) | shut by a single cut at 0.50 |
|---|---|---|---|
| 100–139 | closed | 0.95–1.00 | 0 |
| 140–179 | opened | 0.76–0.99 | 0 |
| 180–219 | closed | 0.82–1.00 | 0 |
| **220–259** | **opened** | **0.22–0.49** | **30** |
| 260–299 | closed | 0.70–0.94 | 0 |

Same log, one cut at 0.50: **96/126 (76.2%)** — within half a point of what the
old firmware actually scored on bench 2 (76.7%). Two edges: **126/126.** Zero
frames were called "nothing there" in 172 live frames.

**What this run still does not measure is the presence stage's benefit.** It
never fired, because the only empty desk in the schedule is the baseline, which
runs before the rule engages. Replaying the baseline against the run's own
references says it would have held — `b` −0.245..+0.098 across 30 frames, worst
case 0/30 called present, against a lowest object frame of +0.221 — and that gap
lands in the same place bench 2's did, which is a second independent look at the
0.15 edge rather than a refit. But a stage whose cost is measured at zero and
whose benefit is inferred is not the same as one that has been seen to work. **A
bench with an empty segment after enrolment is what it needs.**

> **That bench was run on 2026-08-16 and the stage failed it: 17.8% and 24.4% of
> the empty frames held, where the replayed baseline had promised 100%** — the
> replay above is training accuracy, scored against references taken from the
> same segment. The cause is structural, not an edge: the presence axis is the
> common mode, and the common mode is the term `c[]` subtracts because that is
> where the drift lives. Everything in this subsection describes a rule that has
> since been **removed** —
> [#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) replaced it
> with open-set rejection on `min_k ‖c[] − qref[k]‖`, which scores 90.0% and
> 87.8% replayed on those same two runs, and deleted the `'0'` enrolment key
> along with it. The two-edge table, the 0.15 gap and the 120/120 stay here
> because the shape of this mistake is the argument for the shape of that fix.
> See [architecture](architecture.md#from-the-embedding-to-an-answer) for the
> rule the board runs now, and
> [#19](https://github.com/kazunori279/fpga-open-vocab/issues/19) for why the
> 120/120 in the header of this section is itself in doubt.

#### `tools/score_cue.py` stopped charging M21 for frames from before it exists

M21 needs two references, so every frame before the second one lands is the *old*
rule's output. Bench 2's report read `enrolled from 26/60 (43.3%)` and 30 of
those 60 frames were scored before the board had a second reference to be nearest
to. The sidecar now records the window, the scorer computes the frame the rule
goes live, and says how many it dropped and why. On bench 2's log, unchanged:
`HELD OUT 89/120`. On the same log's teaching segments: **25/25** rather than
26/60. The held-out number was always the one to quote; the point is that the
other one is now also true.

#### Also from these three benches

- **`picotool load -x` left the board in BOOTSEL with `Program Information:
  none`.** `load` followed by `reboot` flashed and ran the same UF2 without
  complaint. That, and the earlier finding that `ft_recv_bitstream()`
  (`firmware/frame.c:224-240`) has no `'B'` handler — so a board idling at
  "waiting for a bitstream" cannot be put into BOOTSEL by `demo.py --bootsel` at
  all — are [#3](https://github.com/kazunori279/fpga-open-vocab/issues/3).
- `FGX_ENROL_N` in `firmware/m9.c` and `ENROL_FRAMES` in `host/cue.py` are
  mirrored constants, which is a trap. It is a **visible** one: the board prints
  `(N frames)` on every enrol line and the sidecar records what the host assumed.

---

## Appendix: the design-time performance model

This was the README's `### Performance model` section until 2026-08-01, when the
README was cut back to what is currently true. It is the link/frame model that
chose the architecture, written before any of it existed, with the two
*superseded* notes it accumulated as measurements came in. The frame row it
predicted — 150-250 ms at the assumed bus rate — is not reachable on this board,
and the reason is not in the table: it counts forward traffic only, so the
largest component of a real frame (RUN idle bytes, the tile computing) is
missing entirely. See [the road to 280 ms](#the-road-to-280-ms) for where the
measured ladder stops.

**Both columns are now measured — A at 8.94 MB/s, C at 26.4 MB/s** (see
[Bus rate](architecture.md#bus-rate)). The point of the table is the shape of the gap, not the
digits — and the shape did not change when either measurement came in.

Per frame: ~1.5 MB weights out, ~2 MB activations out, ~2 MB results back —
call it 3.5 MB forward and 2 MB back. The link is full duplex in both
configurations, so the two directions overlap and the frame is bounded by the
slower one.

| | @ 50–70 MB/s (assumed) | **A** — no modification | **C** — one jumper |
|---|---|---|---|
| Forward rate | 50–70 MB/s | **8.94 MB/s** *(measured)* | **26.4 MB/s** *(measured)* |
| Return rate | 50–70 MB/s | **8.94 MB/s** *(measured)* | 8.94 MB/s *(measured)* |
| Forward time (3.5 MB) | 50–70 ms | 393 ms | **133 ms** |
| Return time (2.0 MB) | 29–40 ms | 225 ms | 225 ms |
| Link time (max of the two) | 79–110 ms | **393 ms** | **225 ms** |
| Weight read (1.4 MB from flash XIP) | *(overlaps)* | *(overlaps)* | *(overlaps)* |
| Compute (250 MMAC @ 3–4 GMAC/s) | ~70 ms *(overlaps)* | ~70 ms *(overlaps)* | ~70 ms *(overlaps)* |
| **Frame** | **150–250 ms** | **~400 ms** | **~230 ms** |

> **Superseded in part by [M6c](#the-90-that-is-not-the-link).** Every row here
> is a *link* model, and M6c measured the link running at 10% duty because the
> MCU-side driver, not the wire, sets the pace. These numbers remain the right
> floor — no arrangement of software beats them — but the "Frame" row is not a
> prediction until the driver is O(1) per response. Read them as lower bounds.
>
> **Superseded further by [M7c](#the-road-to-280-ms), and not only because the
> driver was slow — the traffic figure itself was too small.** "3.5 MB forward,
> 2 MB back" counts *forward* traffic only: no RUN idle bytes, which are the tile
> computing and are the single largest component at 2.78 MB, and no DRAIN framing.
> The frame is **8.151 MB** — computed by `make -C rtl test_plan` and then
> [confirmed byte-for-byte on the board](#what-the-board-did) — so even the
> ~230 ms column C row was never reachable. It is also the wrong *shape*:
> widening the link cannot touch RUN at all, because idle bytes buy clocks and
> three data lines carry three bits per clock. Column C is worth 448 → ~149 ms on
> ACT+WGT and nothing elsewhere.

Note what happens in column C: widening the forward path 3× moves the bottleneck
onto the **return** path, which cannot be widened at all (GPIO6 has no
contiguous neighbour). Past that point the only lever left is moving less data,
and the traffic that has to shrink is the traffic coming *back* — which argues
for pushing whole layers to the FPGA and reading one result, rather than
round-tripping every tile.

RP2354A alone with [CMSIS-NN](https://github.com/ARM-software/CMSIS-NN) would be ~1.7 s/frame, so the FPGA is worth roughly
**4×** in configuration A and **7×** in configuration C, if the link measures at
its ceiling. It very likely will not; that is what M2 is for.

> **Measured since:** the MCU baseline is **3.36 s/frame**, not 1.7 s
> ([M5b](#m5b--tuned-mcu-baseline--3358-msframe-bit-exact-74-the-reference)) —
> a straightforward [`SMLAD`](https://arm-software.github.io/acle/main/acle.html) kernel at 3.17 cycles/MAC. CMSIS-NN's deeper
> blocking would likely reach ~2 s, so treat 1.7 s as a floor rather than a
> baseline. Every FPGA multiple below is correspondingly conservative.

---

## Appendix: the frame-time target and its nine restatements

The project carried a frame-time target from the first feasibility pass to
2026-07-31, and revised it nine times. Every revision came from a measurement
except one, which came from a supplier. The README no longer names a target at
all — 917 ms is measured, and the tile's 265 ms of non-overlapping MAC time is
a property of the board rather than a goal — so the sequence is kept here.

| # | restated to | what forced it |
|---|---|---|
| — | 150–250 ms | the original [design-time model](#appendix-the-design-time-performance-model), before any hardware existed |
| 1 | ~680 ms | before M7c ran: the first estimate built on measured link behaviour rather than the assumed bus rate |
| 2 | ~1,020 ms | M7d measured how little of the CPU actually overlapped the wire |
| 3 | ~750 ms | core 1 counted for the first time |
| 4 | revised upward *(the figure was not written down at the time)* | M7e measured what core 1 was *actually* worth: 193 ms of the projected 380 |
| 5 | ~800 ms | after M7f item 1 |
| 6 | ~760 ms | M7g-1 forced the weight interleave into the same job as the jumper |
| 7 | ~830 ms | the jumper's frame gain came in at 165 ms rather than 240 |
| 8 | **~280 ms withdrawn** | procurement, not measurement: it required FPGA-side PSRAM, no QSPI PSRAM breakout is buyable, and this project cannot fabricate one — so the number was never testable |
| 9 | **~470 ms withdrawn**, and no bit-exact target left | [M10](#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms) synthesized `u_tile` standalone and it closed at 70 MHz, against the 75 the link already clocks it at |

One further over-claim belongs here: **874 ms/frame**, which was a single-block
extrapolation and never a frame measurement. The measured ladder is 2,164 ms for
M7c, 1,481 for M7d, 1,292 for M7e, 1,197 for M7f item 1, 1,140 for M7g-1, 1,144
for the jumper, 975 for M7g-2 and **917 for M7h**.
