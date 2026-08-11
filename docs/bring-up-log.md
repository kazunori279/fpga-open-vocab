<!-- moved out of README.md on 2026-08-01; see ../README.md#documentation -->

# Bring-up log

Dated entries, newest first — what was straps, what was measured, and what was
got wrong on the way. **Append-only**, and deliberately so: several entries here
exist only to record a claim that later turned out to be false.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [history](history.md) · [dev plan](milestones.md)

---

### 2026-07-31 — M10 closed on a timing report, for 32 ms

**A milestone died twice in one day, and the second death cost five container
builds and no hardware at all.** M10's PSRAM half had already gone in the
morning, for want of a buyable QSPI breakout. The surviving half — give `u_tile`
its own clock so the MCU stops clocking it — was written up as worth ~450 of the
wire's 644 ms. Reading the RTL to scope the CDC work found that number was
arithmetic, not measurement, and wrong in two independent ways.

**RUN's 314 ms is compute, not transport.** `firmware/m7.c:490` sizes RUN's idle
bytes as `sweep = K*QG*(P+6) + 512` — the tile's own cycle count plus slack — and
23.24 Mclk over 314 ms is 74.0 MHz, which is `link_clk`. The bytes *are* the tile
computing. Give the tile an independent clock at the same speed and the MCU
idle-**waits** instead of idle-**clocking**: the bytes leave the wire, the time
stays. The entire prize was `22.3e6 x (1/75MHz - 1/f_tile)` — **zero at 75 MHz**.
And `f_tile` had never been measured, because `gemm_tile` had never been
synthesized on its own; both shipped builds report a critical path inside
`gemm_link`'s framing logic, so neither number was the tile's.

So `tile_probe.v` — an LFSR wrapped around the real `u_tile`, three pins, a 6.0 ns
constraint meant to fail, and `res.csv` checked for 8 multipliers and 21 memory
blocks so a folded-away tile could not report a fictional Fmax. The first four
seeds came back at **66 ± 3 MHz** and every one of them named a path in the drain
walk, which `gemm_tile.v:592-594` records as deliberately unpipelined. That is
the wrong answer to the question: the drain walk is not what runs for 314 ms.

Pipelining the walk behind a `DPIPE` parameter (default 0, and the compute loop
never sees it) moved the critical path in two seeds of three and lifted Fmax to
**69.9 MHz** — proving the first measurement was measuring the walk, and
producing the real one. **The new worst path is `wbuf` RAM output straight into a
`mult_18x18` input at logic level 0**: 5.264 ns of RAM clock-to-out, 6.802 ns of
net across a 52-unit hop, 2.716 ns of multiplier setup. There is no logic on it
to pipeline, retime or restructure, and the two hard blocks alone are 7.98 ns —
**125 MHz is the fabric's ceiling before a single track is routed.** `u_tile`
holds 21 of the T8F49's 24 memory blocks, so the placer has nowhere to put them
closer.

Reported 70 MHz, and the analyser is pessimistic by about 20% on this device
(`gemm_top` reports 62.449 and runs bit-exact at 75), so call it ~84 MHz real.
**Prize: 32 ms of 917, or 3.5%, for a second clock domain, two dual-clock RAMs,
four synchronisers, a drain handshake, a new skewed-clock testbench and a PLL
this repo has never instantiated.** Closed.

One correction worth recording against myself: `DPIPE` was promised to leave
`gemm_top` bit-identical and it does not. Built twice at seed 2 against the
pre-`DPIPE` source — flow verified deterministic first, by building the original
twice and diffing — the netlist gains one flop and one LUT, both attributed to
`u_link`, a module that was not touched. `u_tile` is unchanged in every column
that matters. The bitstreams differ; Fmax moved 62.449 → 64.737, inside the
±2.4 MHz seed spread. All three benches still pass bit-exact on 10,560
accumulators, which is the contract that actually holds, and the comment in
`gemm_tile.v` now states the measured truth rather than the intended one.

**917 ms is now the floor for this board, not just for this firmware.** The
lesson is the cheap one: the gate cost five builds because it was built as a
gate. Had Stage 1 been written first, the CDC would have been correct, the
testbench would have passed, and the board would have reported 885 ms.

### 2026-07-31 — 211 ms of CPU, 58 ms of frame, and why M7 ends here

**M7h landed both of its items exactly as costed and the frame kept 27% of
them.** The weight cache served **precisely** 43% of weight bytes — `1009 of 1856
passes built, 847 served from the cache`, identical in all six modes of both link
configurations, which is the arithmetic's ceiling and not one pass short of it.
The build fell 502 → **318 ms** serialized and 460 → **292 ms** on core 1.
`gw_pack3()`, freed of its 16-byte stack round trip, took `stage` from 97 → **70
ms**. Config C's frame went 975 → **917 ms**; config A 1,139 → 1,110; the third
data line is now worth **1.21×** and the whole thing **3.76×** the MCU's 3,448 ms.
All eight layers bit-exact in all twelve modes, 512/512 embedding floats exact,
174 of 174 blocks swept accumulator by accumulator.

**58 of 211 ms.** The serialized mode is the control and it behaves perfectly —
1,582 → **1,374 ms**, the full 208 ms, exactly where a model that adds columns
says it should be. The pipelined mode does not, because `W1_HI`'s 460 ms of
builds were running *inside* 641 ms of wire. Core 1 was busy; core 1 was not the
critical path. Making it 168 ms less busy made it idle sooner.

That is the third milestone in a row to remove a real, correctly-predicted
quantity and get a fraction of it at the frame. M7e moved work between cores and
got half. M7f-2 removed 286 ms of wire and got nothing, because a latent bug ate
precisely that much. M7h removed 211 ms of CPU and got 40%, because the work was
already hidden. **Three mechanisms, one shape**, and the shape is that this
project's cost model is a sum and the machine is a `max()`.

So M7 closes at 917 ms rather than continuing. Not because the remaining items
are done — pre-interleaved `weights.bin` is still there, ~15 ms of CPU, which
this milestone just priced at ~6 ms of frame — but because **every remaining
firmware item is smaller than the one that converted at 40%.** What is left of
917 ms is 644 of wire, of which **314 is RUN and RUN is the tile computing**, and
127 ms of core 0 stalled on a queue that got 14 ms shorter when core 1's load
dropped by a third. The first of those is the 265 ms floor showing up in a
measurement for the first time. The second is a queue-depth bound. Neither is
something more C will fix, and both are what
[M10](milestones.md#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)
goes around.

One harness lesson, cheap: M7h gave `park()` an eight-second watchdog so a
finished board always returns to the bitstream prompt — the previous firmware
could go deaf on stdin while staying enumerated, which left a `park()` whose only
exits were all through stdin, i.e. no exits. It worked, and then the reboot it
causes dropped the CDC device mid-`read()` and crashed `m7.py` *after* a PASS.
**A completed run exiting 1 with a traceback is worse than the wedge it fixed**,
so `pump()` now treats a vanished port as quiet and lets the idle timeout end the
run in the ordinary way.

### 2026-07-31 — one jumper, soldered, and the 300 ms it did not deliver

**The PIN2↔PIN17 jumper is fitted, and configuration C is measured at last: three
forward data lines, 16.791 MB in 637 ms, 26.4 MB/s against M2's 26.8 MB/s
prediction and 8.94 MB/s on one line.** Bit-exact at 75 MHz through all six rungs
of the ladder plus the accumulator sweep, in the same boot as configuration A —
the board takes the second bitstream over the same USB CDC channel between the
two runs, so the comparison is two links and not two builds.

**The soldering was the part with a wrong assumption in it, and it was not about
soldering.** Pad number is not silkscreen number on this board: silk `0`–`12` is
pad = silk + 2 and silk `13`–`23` is pad = silk + 7, so silk `17` is pad 24 and
ball B3 — *not* the GND the silk sequence invites you to read it as. That was
checked with a meter before the iron came out, not after, which is the only
reason this paragraph is short. The finished joint measures 0 Ω to silk 2 and
100 kΩ to its neighbours.

**And then the frame did not move: 1,140 → 1,144 ms.** 286 ms of wire vanished
and every millisecond of it came back somewhere else. The command that made it
legible was one added the same afternoon for a different reason —
`gh_prof_t` splitting the wire *by command in link clocks*, because bytes are not
comparable across widths and clocks are. It read **WGT at 40.36 ns per 13.333 ns
clock**. A link clock cannot cost 40 ns; what that column was measuring was CPU
time inside a pipelined window, and the CPU was in `gw_locate()`, at 389 ms
against configuration A's 4.

**The cause is the kind that only a hardware change can surface.** `gw_locate()`
predicts where a response begins, and the prediction used a truncating division
where it needed `ceil`. **At width 1 those are the same number.** Five milestones
of daily use on configuration A could not have found it, and neither could any
host test, because the property is an agreement between the C and the Verilog and
only one of them runs on the laptop. Fixed, along with two things it exposed — a
shared hint slot that had been costing configuration A 348 misses a mode
unnoticed, and a bit-at-a-time scan whose cost was in the *hit* path — the frame
went 1,144 → **975 ms**, and the third data line went from 1.00× to **1.17×**.

The lesson is the same shape as [the USB hub](#2026-07-29--two-boards-one-alive-one-dead-corrected-2026-07-30), from the
other direction. There the variable that mattered was never moved; here moving it
was what revealed that something else had been wrong all along. **A measurement
that does not change when you change the hardware is not a null result — it is a
second thing to find.**

### 2026-07-30 — the driver stops scanning, and the FPGA finally beats the MCU

**M7a: 42 → 12 ms per block, still 2,048 of 2,048 bit-exact at every rate from 38
to 75 MHz.** The extrapolated frame goes from ~3,900 ms — *slower than the CPU* —
to **874 ms against a 3,358 ms MCU baseline**. That is the number M6 was supposed
to produce and did not. Phase table in [M7a](milestones.md#m7a--the-o1-driver--done-20482048-still-bit-exact-at-every-rate).

**The bug worth writing down is one the obvious fix would have missed.** The plan
this milestone inherited said to "measure the return path's byte offset once at
init with a NOP — it is a constant, because the MCU drives the clock." Reading
`gemm_link.v` before writing any code showed that is true for five of the six
commands and false for the one that matters: non-RUN responses start in `R_EXEC`
a fixed number of clocks after the last payload bit, but `is_run` branches to
`R_WAIT` (`gemm_link.v:487`) and holds the preamble until `busy` has risen *and*
fallen. RUN's offset therefore carries the sweep, not just the frame length — and
RUN is 8 of 28 transactions and **39% of the bits being scanned**. A single
NOP-measured constant would have left the largest share of the cost in place and
looked like a fix.

What shipped instead is a **signed, self-calibrating, per-command-class hint**:
latch `delta = preamble_end − ref` on first use, verify the full 32-bit preamble
at the predicted position on *every* subsequent use, and on mismatch rescan,
re-latch, and count the miss. Signed because a RUN response arrives *before* its
idle budget ends. The property that makes it safe to ship is that **it cannot be
wrong, only slow** — which is the right trade here, because a wrong bit boundary
does not raise an error, it returns a plausible wrong tensor. The board reported
`24 hit, 2 miss`: exactly one miss per class, which is the cost of learning.

**The measurement design mattered as much as the fix.** Both decode paths are in
one binary behind a runtime flag and run at every rate in the same boot, because
[M5b's own entry](#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped)
warns that ratios quoted across builds of this firmware are not measurements. And
the decode was made a pure function of a byte buffer in a Pico-free file, so
`test_gemm_wire.c` could check it on the laptop at every bit offset, on all six
command codes, and on five distinct failure modes — before a strap was spent. It
passed on the first run, which is not evidence of anything, so four deliberate
mutations were injected (drop the high half of the funnel shift; mask the CRC
index to `0x7f`; corrupt one CFG field; misalign the payload copy) and all four
were caught. *One strap covered the whole milestone.*

**And the comment that was wrong.** `gemm_host.c:74-78` argued a CRC table was
not worth building "beside the 16 K link clocks the same payload spends on the
wire." True per byte on the wire; false in elapsed time, because the CPU and the
wire never overlap — we are the FPGA's only clock, so the tile is frozen for the
entire decode. Cost of that reasoning: 11 ms a block, which is most of the gap
between M6's 53 ms and this milestone's 42 ms baseline column.

The wire is now the largest single phase (5.47 of 12.08 ms) for the first time in
the project. That is the correct thing to be bottlenecked by, and it makes the
next levers — requantising in fabric, overlapping the strip build with the DMA —
choosable by evidence instead of by guess.

### 2026-07-30 — the tile is bit-exact on silicon, and it moved the bottleneck

**M6c: 2,048 of 2,048 int32 accumulators bit-exact, at every link rate from 38
to 75 MHz.** One real conv2 block, run on the T8, compared against
`fgx_conv_acc()` computed on the MCU in the same boot — no tolerance, no
sampling. Status `0x61` at all six rates: no underrun, no bad frame, no sticky
fault. Full results in
[M6c](milestones.md#m6c--on-board-2048-of-2048-at-every-rate).

**The result that matters is the one we were not looking for.** The block moves
50,980 bytes and takes 53 ms; at 75 MHz and one bit per clock that is 5.44 ms of
wire. **The link is idle 90% of the time and the MCU-side driver is the
bottleneck** — 0.92 MB/s measured against 8.94 MB/s of measured wire. Extrapolate
the per-frame blocking through that driver and a frame costs ~3,900 ms, which is
*slower than the 3,358 ms MCU baseline M6 exists to beat*. The tile is not the
problem and the wire is not the problem; `find_preamble()` scanning 66,000-bit
capture buffers from offset 0 is. That is a fixable, structural mistake — the MCU
drives the clock, so the response offset is a constant that can be measured once
at init — but it had to be measured to be believed, and no amount of simulation
would have surfaced it. Analysis in
[The 90% that is not the link](milestones.md#the-90-that-is-not-the-link).

**Three procedural things paid for themselves**, and all three are worth keeping.

*The strap was spent once, for the whole milestone.* `fpga_configure()` takes a
plain pointer, so `m6.c` receives the 173 KB bitstream over USB CDC into SRAM
instead of having it compiled in. Reflashing the MCU costs a physical `PRG`–`GND`
strap; reconfiguring the FPGA does not. Every RTL revision after the first — and
the entire six-point clock sweep — cost **zero straps**. On this board that is
the single biggest lever on iteration speed, and it is why the sweep happened at
all rather than being replaced by one measurement and an argument.

*The simulator and the board were made to run the same layout code.* The strip
and weight layout used to live inside `gen_gemm_vec.c`, which writes the vectors
`tb_gemm` and `tb_gemm_link` check the RTL against. Transcribing it into `m6.c`
would have put an unverified second copy on the hardware path — and a strip bug
there presents as "0 of 2048 accumulators match", which localises nothing. It
was pulled out into [`firmware/gemm_block.c`](../firmware/gemm_block.c) and both
callers now link it. The regenerated vectors were **byte-identical** and both
testbenches still PASS, so the refactor is provably inert and those two PASSes
are now evidence about the code the MCU actually runs.

*The padding buffer was poisoned rather than zeroed.* Strip rows outside the
image are a don't-care — a correct tile never reads them — and that is exactly
why filling them with zero is wrong: it makes a stray read of a pad row return
the right answer. Filled with `0xa5` instead. Mutating the row-bounds test in
`im2col_feed.v` was caught by **2 of 6 cases against a zero-filled strip and by
all 6 against a poisoned one.** A don't-care that is cheap to make loud should
be made loud.

**One caveat recorded so it is not misread later.** 75 MHz is 15% past the
64.973 MHz the static timing model predicts, which says something about C2-corner
conservatism — but the sweep found **no failure edge**. 75 MHz is the ceiling
`m6.c` can generate (sys_clk 150 MHz ÷ 2 in the PIO), not a measured limit of the
fabric. The honest statement is "correct everywhere we could reach", not "correct
up to 75 MHz". sys_clk above 150 MHz is unexplored, and free if it works.

### 2026-07-30 — the tuned baseline, and a 28% error we nearly shipped

**M5b: 3,357.6 ms/frame, 3.17 cycles/MAC, still 2048/2048 bit-exact.** im2col
plus a blocked int8 GEMM with an `SMLAD` inner loop, 7.4× the reference kernel,
flat at 7.2–7.9× across every conv shape. M6 now has an honest number to beat.
Full results in
[M5b](milestones.md#m5b--tuned-mcu-baseline--3358-msframe-bit-exact-74-the-reference).

**Two things went right for procedural reasons rather than lucky ones**, and
both are worth keeping.

*The strap was spent last, not first.* Reflashing this board needs a physical
`PRG`–`GND` strap, and the one thing that could not be tested on macOS was the
`SMLAD` path — aarch64 does not define `__ARM_FEATURE_DSP`. So
[`dsp_shim.h`](../firmware/dsp_shim.h) transcribes the four intrinsics from the
Armv8-M ARM and `cc -DFGX_DSP_SHIM` compiles *the same source lines* the M33
runs. The tap pairing, the loop bounds and the `K % 4` tail were all proven
against numpy on the laptop, per layer, before the board was touched. What was
left for the strap was "does the silicon match the ARM ARM", and it did — first
try, no second strap.

*The reference was re-run in the same boot rather than quoted.* This one nearly
cost us. `encoder.c` runs at **24,970 ms** in the M5b binary against the
**31,798 ms** M5 logged — same source, same clock, same flags. The cause is that
M5b had to drop `static` from `fgx_conv` to call it from the harness, which
stopped GCC inlining it into `fgx_run`; a 1,086-byte monolith became a 636-byte
hot kernel, and on a part that fetches instructions from flash XIP that was
worth 21% with **no change to a single arithmetic operation**.

Dividing 31,798 by the new figure would have produced "9.5×" — a 28%
overstatement, in our own favour, from an arithmetic shortcut that would have
looked completely reasonable in review. The true figure is 7.4×. **On this board,
ratios quoted across builds are not measurements**, and the only defence is to
pay the ~25 s to re-run the baseline inside the same boot.

One consequence beyond the number: the MCU baseline is 3.36 s, not the ~1.7 s
CMSIS-NN estimate the tier table was built on, so the FPGA's honest multiple is
~15× rather than ~7×. **The argument for M6 got stronger by being made
honestly** — which is not the direction that correction usually runs, and is the
reason to keep making it this way.

### 2026-07-30 — the second board was never broken, and it settles the PSRAM

Two findings, and the first is what made the second possible.

**Board #1 works.** It had been written off as dead on 2026-07-29 — no USB
enumeration across two cables and two 4-minute hotplug watches — and a repair
plan had been drawn up around SWD on J2, a pogo cable we do not own, and a
suspect list of the 1V1 buck, `RUN`, the crystal, and the USB ESD array.
Plugged **directly into the Mac rather than through the hub**, it enumerated
instantly as `2E8A:0009` serial `118E1FFA149C9E95`, and its factory loader
answered `forge-loader rp2350 ready`, state IDLE. The whole suspect list was
imaginary.

That is worth more than a board. The 2026-07-29 entry recorded two cable swaps
as evidence, and two cable swaps *feel* like independent trials — but every
attempt went through the same hub, so the variable that mattered never moved. It
was one experiment run twice. A negative result across N retries is only worth
N if the retries differ in the way that counts, and the cheapest way to find the
untested constant is to ask what every failed attempt had in common. Here it was
sitting in the sentence "across two cables".

**And with two boards, the PSRAM question resolves.** The rev 4 probe on board
#1 returns a *different* byte string that decodes identically:

```
                raw record                MFID KGD  EID
board #2   5e 0c 03 57 46 f6 9c 06   ->   0D   5D   1b da 70 19 78 30
board #1   95 17 43 57 46 f6 9c 06   ->   0D   5D   1b da 70 1a 54 5d
                                                    └ common ┘└serial┘
```

Two APS1604M dies with sequential serials, both healthy, both 2 MiB, both
answering correctly — and both landing **exactly 18 bit-times out of frame**.
Each record has precisely one rotation out of 64 that yields the `0D 5D` header,
and on both boards that rotation is 18. So the offset is not a bad part; rev 4's
path × chip matrix already showed it is not our driver either, since
`flash_do_cmd_cs()` and `raw_xfer()` both frame CS0 at bit 8 and both slip +18 on
CS1.

Chip cleared, wire cleared (the quad capture puts the data on SD1), opcode
cleared (dead opcodes return nothing), host cleared, datasheet says `9Fh` takes
no dummy cycles. **Nothing left is reachable from the MCU** — `RXDELAY` moves the
sampling point inside a bit and dummy settings move whole bytes, so no register
addresses an 18-bit shift. Open question #10 closes as bounded: the next
instrument is a scope at the package, and PSRAM was always headroom rather than
a dependency. Two units reproducing it is also what turns this from a warranty
claim into something worth sending Adiuvo.

**Reported to `support@adiuvoengineering.com` on 2026-07-30.** The headline ask
is the cheap one — *has U1 ever been brought up successfully on a Forgix board?*
If the answer is "we route it but never validated it", that closes this outright.
Secondary questions: whether CS1 needs an init step the forge-loader performs,
and whether the U1 stub off the RP2354A's shared QSPI pads is known-good at
speed. No replacement requested; two dies with the same offset is a design
question, not a warranty one.

#### Vendor reply, 2026-07-30 — **#10 closed: U1 was never meant to be there**

Adam Taylor (founder, Adiuvo Engineering) answered the same day, and the
headline ask landed:

> "The PSRAM was not intended to be fitted to the boards in the production run,
> but they were accidentally assembled as such we left them fitted but have done
> no testing of them as the RP2354A does not need the external PSRAM for
> operation."

**So there was never a working configuration to find.** U1 is an accidental
population — unvalidated, never brought up, no known-good `psram_detect_size()`
exists on any Forgix board. Question 1 is answered completely, and question 3
with it: he confirms nothing in the schematic or layout would produce the delay,
and is himself "at a loss as to why the signal would arrive back 18 clock times
later than expected". Question 2 (a CS1 init step the loader performs) went
unanswered, which no longer matters. He offered to investigate on his return
from the US after 8 August, and offered a refund.

**Refund declined, investigation not requested.** The boards do everything this
project needs — the weights live in the 2 MB stacked flash, M5 and M5b both ran
from XIP, and [the bandwidth analysis](milestones.md#m3--memory-bandwidth--answered-as-a-side-effect-of-m5)
shows PSRAM would have added capacity and not speed. Asking a founder to spend
bench time on an unvalidated part we have no use for would be spending his time
to satisfy our curiosity.

**The 18 bits remain genuinely unexplained**, and that is now the permanent
state of this question rather than a to-do. Worth being precise about what was
and was not established: everything reachable from the MCU was eliminated
rigorously, and the vendor has confirmed there is nothing in the board design to
find. What was never done is the one measurement that could actually answer it —
a scope at the package. Nobody knows why it is 18. That is an acceptable place
to leave it, but it is not the same as knowing.

*The retrospective value is in the ratio.* Four probe revisions, a
photograph that overturned a documented "DNP", and a full 2×2 host-exoneration
matrix — all spent on a component that was **fitted by accident on a board that
does not need it**. Every step was locally justified, and the whole was
disproportionate. The one question that would have capped the effort at zero was
the one sent last: *has this ever worked for you?* **Ask the vendor before
out-debugging the vendor** — especially about a peripheral whose only
advertisement is a product page.

### 2026-07-30 — M5 is bit-exact on silicon, and the PSRAM stays a mystery

Two results from one `PRG` strap, and they point in opposite directions.

**The good one: 2048 / 2048 bit-identical float32 outputs, on the device.** The
Cortex-M33 running `encoder.c` produced embeddings that `memcmp` equal to the
numpy int8 golden vectors — every bit of every one of 512 floats, on all four
test images. `lrintf`'s round-half-to-even, the FPU's rounding mode, and the
int32 accumulators all agree with the host. **The integer contract M6 has to
reproduce is now pinned on the target silicon**, and a cosine of 1.000000 would
not have shown that: a small systematic scale error also produces cosine 1.0.

**The bad one: `psram_detect_size()` returned 0**, and the hour spent explaining
that is the part of this entry worth reading, because the explanation was wrong.

The search for a reason found `dnp exclude_from_pos_files exclude_from_bom` on
U1 in the vendor `.kicad_pcb` — the same trio as `U8`, which is the *Teensy
form-factor outline*, a part that does not exist — and U1 absent from
`build/positions.csv`. Since JLCPCB places from the CPL, that read as conclusive:
**U1 is not populated.** The README, `docs/pinmap.md` and `m5.c` were all edited
to say so, and it was committed.

Then a photograph of the underside took five seconds to disprove it. **U1 is
soldered on**, a SOIC-8 just past the Tag-Connect pads. So is **J3**, which
carries the same `dnp` flags. The premise was false: *the exclusion flags in this
repo do not describe the manufactured board.* And `positions.csv` is generated
from those flags, so it was never a second source — it was the same claim wearing
a different hat, which is exactly what made two agreeing files feel like
corroboration.

The first answer to [#1](history.md#verify-before-building) (populated, from a BOM row)
was right for a poor reason. The second (not populated, from CAD metadata) was
wrong for a reason that felt much better, which is the more expensive failure
mode. Two real-world signals — the vendor product page and the press coverage —
were pointing the right way the whole time and were argued down. *"Is this part
on the board"* is a question about an object; the photograph should have been
step one.

What actually remains is [#10](history.md#verify-before-building): a populated, correctly
wired APS1604M that will not return an ID. **M5c** is the probe for it — print
the raw bytes the SDK throws away — and it ran the same evening. **It turns out
U1 was never failing to return an ID. It was returning the correct one, in a
place we were not looking.** `00 00 00 00 5e 0c 03 57 46 f6 9c 06` carries
`0D 5D 1B DA 70` at bit offset 50: AP Memory, known good die, 2 MiB. The part on
the BOM, the density on the BOM, answering correctly. What is wrong is that the
reply arrives 18 bit-times late, so every byte-aligned read of it cuts a good
answer in half. Details in
[M5c](milestones.md#m5c--make-u1-talk--closed-the-vendor-never-fitted-u1-on-purpose-and-never-tested-it).

There are two lessons inside that, both variants of the big one. The first: M5c
rev 1 printed the bytes *and then printed its own verdict*, `U1 SILENT on every
variant` — because its classifier only recognized an exact `0D 5D` and treated
everything else as absence. The raw row and the summary line disagreed, and the
summary was the wrong one. A diagnostic that interprets is more useful than one
that dumps, right up until its interpretation is narrower than reality; then it
launders a third outcome into one of the two it was built to expect. The bytes
were on screen the whole time.

The second is sharper, because it cost two more revisions. Both rev 1 and rev 2
searched for `0D 5D` **on byte boundaries** — and byte alignment was not a
property of the data, it was an assumption the probe inherited from the SDK it
was written to debug. The one thing a diagnostic must not import from the system
under test is the system's own framing. Rev 2's real contribution was not its
timing matrix, which found nothing; it was reading twelve bytes where rev 1 read
eight, which is the only reason bit 50 was inside the window at all.

**Tier 3 is unaffected either way, because it never depended on the PSRAM.**
1.42 MB of int8 weights + a 173 KB T8 bitstream + ~60 KB of firmware is 1.65 MB
of the RP2354A's 2 MB stacked flash, and M5's per-layer table shows the flash
fetch is nowhere near binding: weight bytes per MAC vary 64× across conv1–conv7
while cost per MAC stays flat at ~195 ms/MMAC. U1 would sit behind the same QMI,
so it was never going to be faster — it is 2 MB of *writeable* headroom, which
matters for growing the model past the flash budget and not much else.
[M3](milestones.md#m3--memory-bandwidth--answered-as-a-side-effect-of-m5) is dissolved into
this finding rather than run.

**The latency number needs a caveat louder than the number.** 31.8 s/frame, or
30 cycles/MAC — that is the cost of an inner loop that re-tests a flag and
bounds-checks two axes on every tap. `encoder.c` was written to be obviously
correct and the bit-exact row is what that bought. Quoting 31.8 s as "the MCU
baseline" would make the FPGA look like a 140× win. Hence **M5b**, blocking M6:
the tuned kernel gets `encoder.c` as its golden reference for free, and its
im2col decomposition is the same one M6 has to build in RTL.

*Resolved the same day:* M5b measured **3,358 ms/frame**, still bit-exact,
7.4× the same-boot reference. The FPGA's honest multiple is **~15×**, not 140×
and not the ~8–10× this section assumed — the tuned MCU came in 2× slower than
the ~1.7 s CMSIS-NN estimate the tier table was built on, so the argument for
M6 got *stronger* by being made honestly.

Also worth recording: the graceful-degradation path in `m5.c` earned its keep.
It was written on the assumption that a dead PSRAM was *unlikely*, and it is the
only reason a strap spent on a board whose PSRAM stayed silent still came back
with the correctness result and the full per-layer profile.

### 2026-07-30 — both gates pass: the link is real and so is the student

**M2 measured: 8.94 MB/s each way, zero errors at every operating point.** But
getting there took finding out why nothing we built would configure.

**The bug was clocks, not bits.** `fpga_configure()` released `CRESET_N`, waited
`sleep_us(100)`, and started sending. The T8 needs to be *clocked* during that
window before it will start matching the sync pattern, and an idle SPI master
emits no clock — so the part got time and zero edges. Every bitstream from
`rtl/build.sh` had been arriving ~2048 clocks too early since the day the script
was written.

**What made it invisible for two sessions** is that the vendor's `plasm_led.hex`
configured perfectly, first try, every time. Efinity normally prepends a 256-byte
ASCII banner (`Version:`, `Generated:`, …) to a `.hex`, and `rtl/build.sh` passes
`generate_header=off` to strip it — reasoning, in a comment I wrote, that it was
a banner the programmer discards and our firmware "would happily shift into the
FPGA". Exactly backwards. AN 006 Figure 15 draws the CDI0 waveform as
`Header, D, D, D, …`: that banner **is** the lead-in clocking, and the vendor
image only worked because it still carried one.

**The bisect that caught it** was putting the byte-identical vendor payload on
the ladder twice — once whole, once with its first 256 bytes removed. Whole:
CDONE high in 73 µs. Stripped: never. Same bytes, same device, same driver. That
is the entire finding, and it took one flash. My standing hypothesis until that
moment was that reusing configuration pins (F3/CCK, F2/CDI0, G3/SS_N, A4/NSTATUS)
was upsetting the config engine; the `probe_a`/`probe_b`/`probe_c` ladder was
built to bisect *which pin*, and it refuted the whole idea instead — `probe_a`
reuses nothing and failed too. Pin reuse was never the problem.

**Two lessons worth more than the fix.** First, a control that passes is only
informative if you also test the *minimal difference* from it — "vendor works,
ours doesn't" and "vendor works, vendor-minus-header doesn't" cost the same flash
and only the second one names a cause. Second, when every flash costs a physical
`PRG` strap, batch the whole matrix: `firmware/diag.c` walks six rungs without
stopping on success, and the bring-up firmware sweeps lead-in sizes ascending
from zero, which is how the **measured minimum of 32 bytes / 256 clocks** came
for free alongside the fix.

The fix lives in `fpga_config.c` (`LEADIN_BYTES` = 256, 8× margin) rather than in
the build script, so configuration no longer depends on a bitstream-generation
flag. Two vestigial things went with it: the `FPGA_ERR_NSTATUS` timeout loop,
which pin-probing showed was waiting on a line that is externally driven high and
never dips, and the single immediate `CDONE` read, which could not tell "failed"
from "needed another microsecond".

**Then the link swept clean on the first try** — all six operating points, 0
errors, up to 75 MHz. The correlator's alignment offset walking 8 → 9 → 10 as the
rate climbed is exactly the behaviour predicted in the M2 section, which is a
small vindication of building the offset search instead of asserting a latency.

**M4 also came in, and it is a clear GO:** 1.40 M params retain **94%** of the
queries CLIP ViT-B/16 clears (30 of 32), against a 60% threshold. The result that
matters most for M5 is that **simulated int8 is free** — identical mean AUC to
fp32 at three decimals. The result that deserves suspicion is `person` and
`chair`, where the student *beats* the teacher; those are the two near-chance
queries, and the student is fitting dataset bias, not out-reasoning CLIP. Noted
in the M4 section so nobody later reads them as headroom.

So both GO/NO-GO gates are behind us. What is *not* settled is whether 8.94 MB/s
(or 26.8 with the jumper) makes the FPGA worth using at all — M2 proved the link
is clean, not that it is fast enough. That is still M6/M7's question.

### 2026-07-29 — two boards, one alive ~~one dead~~ (corrected 2026-07-30)

| | Board #1 | Board #2 |
|---|---|---|
| USB enumeration | `2E8A:0009` "Pico", serial `118E1FFA149C9E95` | `2E8A:0009` "Pico", serial `4A7C7EFE9A15CFD6` |
| 3V3 rail | present (meter) | — |
| Loader responds | yes, `forge-loader rp2350 ready`, state IDLE | yes, `forge-loader rp2350 ready` |
| FPGA configured | — | **yes**, CDONE + nSTATUS high |

**Both boards work.** Board #1 was written off here as dead — no enumeration
across two cables and two 4-minute hotplug watches, with 3V3 confirmed present —
and a suspect list was drawn up around it: the `1V1` rail (RP2350 internal buck
via L1), `RUN` held low, the 12 MHz crystal Y1, the USB data path (R4/R5 27 Ω
series, U3 USBLC6 ESD array). All of it was wrong. Plugged **directly into the
Mac instead of through the USB hub**, board #1 enumerates immediately and its
factory loader answers HELLO. Every suspect above is exonerated; nothing was
ever wrong with the board.

The lesson is narrower than "check your cables", because the cables *were*
checked: two of them, twice. What went unchallenged was the hub, and the hub was
the one element of the path shared by every failed attempt. Swapping the cable
twice felt like two independent trials and was really one trial run twice — the
variable that mattered was never moved. A negative result across N retries only
buys you something if the retries differ in the way that counts.

Note the board has **no power LED**; D1 is FPGA-driven. A dark board proves
nothing about power. That is what made the hub theory so easy to skip past:
there was no cheap signal distinguishing "not powered" from "not enumerating",
so the investigation jumped straight to the rails.

**Board #2 reached M1.** Streamed the vendor `plasm_led.hex` several times, always
ending CDONE = 1 / nSTATUS = 1, plus the four control loads in the table above.
A power cycle clears the configuration, as expected for SRAM.

**Known issue: the loader can wedge.** Once, after a successful load and a
close/reopen of the CDC port, the firmware stopped servicing USB — the device
stayed enumerated but host writes *and* `close()` blocked indefinitely.
`protocol_send_response()` uses `putchar_raw()`/`stdio_flush()`, which can stall
on a stale `stdio_usb` connection. With no reset button, recovery is a USB
unplug/replug.

**The trigger is not pinned down.** After the replug, six consecutive
open/load/close cycles ran clean, so it is intermittent rather than a
deterministic consequence of reopening the port. Mitigations now in
`host/forge.py`: a settle delay after opening, a `write_timeout`, and an
`ABORT`-first retry in `probe.py`. Note `write_timeout` does **not** rescue a
blocked `close()` — if it recurs, expect to unplug.

### 2026-07-29 — one jumper triples the forward link

Went to write M2's loopback as a 1-bit link and stopped at the pin table. The
constraint that produced "1 bit" was real — GPIO1–3 is the only contiguous run —
but the step from there to "so the link is 1 bit" quietly assumed the clock had
to sit inside that run. It does not. PIO keeps `out_base`, `in_base` and
`sideset_base` in three separate registers; contiguity binds only *within* a
group. Put the side-set clock on any pin at all and GPIO1/2/3 are three data
bits.

The pin to put it on is not arbitrary. Cross-referencing the T8F49 ball list
against the header shows four global-clock balls — B3, C3, E4, E6 — and exactly
one of them, **B3 (`GPIOL_16_CLK2`)**, is wired to a header pad (PIN17). The
other three are unconnected on this board, and F3 `CCK`, which configuration A
uses for the clock, is not clock-capable at all. So a single jumper from pad
PIN2 (RP GPIO22) to pad PIN17 buys both halves of the improvement at once: the
clock lands on the global network *and* it vacates GPIO1–3.

Forward ceiling goes from 8.9 to 26.8 MB/s. The return path stays at 1 bit and
always will — GPIO5 is `CDONE` and GPIO7 has no pad, so GPIO6 has no neighbour to
be contiguous with. That asymmetry is now the binding constraint on the dataflow,
and it is the right shape for an accelerator anyway: feed it a lot, ask it for a
little.

Built both configurations from one parameterized `link_core`. Two things about
the testbench are worth recording:

- **A "must fail at 125 MHz" check passed, and it was the check that was
  wrong.** With a correlator searching sample offsets, overclocking a
  source-synchronous link does not corrupt data — it slides the alignment, and
  the correlator finds the new offset. Errors only appear when the sample instant
  lands inside the return line's transition window, which depends on real
  `T_co`. Simulation cannot answer "how fast" honestly. Deleted the check and
  replaced it with a negative control that *can* fail: short a data line straight
  to the return line, bypassing the fabric. The deliberate inversion in
  `link_core` turns that into ~1985/4096 errors, so a solder bridge cannot
  masquerade as a passing link.
- The heartbeat check measured 1 edge in 2 ms and looked broken. A 488 Hz signal
  needs a much longer window than a link test does; the property under test is
  scale-invariant, so the testbench scales the dividers down rather than
  simulating 40 ms.

M1b folded into M2 rather than staying a separate milestone: the link test has to
repurpose GPIO1/2/3 the moment `CDONE` rises, and the vendor loader owns those
pins as an external service. `firmware/fpga_config.c` does passive x1 SPI config
from an embedded bitstream and then hands the pins over.

Both configurations compile clean and the RTL simulates clean. What is missing is
the bitstream — Efinity is not installed, and it sits behind an Efinix account
login. The firmware handles this deliberately: `hex2c.py` emits an empty
placeholder, and `main.c` prints the failure and stops rather than running a
sweep that would measure nothing.

### 2026-07-29 — Efinity, and both bitstreams

Registered an Efinix account, got the free Bronze licence (T8 covered, valid to
2027-07-28), pulled Efinity 2026.1.132 for Linux and containerized it. Both M2
configurations now place, route and generate a bitstream on T8F49C2.

Getting a *headless* flow working took longer than the synthesis. Four things,
none of them in the docs:

1. `efx_run.py` does not need a project `.xml` — `--family Trion -d T8F49
   --timing_model C2 -v <sources>` covers it. Yesterday I declined to hand-write
   that XML on the grounds that guessing at a version-specific schema would
   mislead. That was right, but for the wrong reason: the file is not needed.
2. It *does* need a `.peri.xml`, and nothing shipped can make one from scratch.
   `efx_run_pt_import_isf.py` merges an ISF into an existing design; a project
   never opened in the GUI has no existing design. `DesignAPI.create()` is the
   missing call, so `rtl/mk_peri.py` is fifteen lines that unblock the whole
   flow. Without it place-and-route runs with no pin assignments at all, after a
   single-line warning that scrolls past.
3. Constraint files are found by filename. `link.sdc` was silently ignored — and
   an ignored SDC does not fail, it defaults every clock to a 1 ns period, so the
   first timing report showed −5.7 ns slack and looked like a real problem.
   `build.sh` now stages it as `<top>.sdc`.
4. Efinity puts an ASCII banner *inside* the bitstream by default. Its own
   programmer strips it; our firmware would have shifted `Version: 2026.1.132`
   into the FPGA. `generate_header=off`.

The numbers, and what they are not. Configuration A costs 34 FFs / 22 adders /
5 LUT4s; C costs 38/22/11 — half a percent of 7,384 LEs. Fabric Fmax is 365 MHz
(A) and 228 MHz (C) on `link_clk`. **That is not a link rate.** The SDC has no
`set_input_delay`/`set_output_delay`, because those need the RP2354A's PIO
clock-to-out, which the FPGA toolchain cannot know; the analysis covers internal
paths only. What it does establish is that the fabric is nowhere near binding,
which is the useful fact for M6.

Two smaller findings:

- **C is slower in the fabric than A** (228 vs 365 MHz) because XOR-reducing
  three lines adds a LUT level ahead of the shift register. Irrelevant at PIO
  speeds, but it is the first concrete instance of the width-vs-depth trade that
  M6 will live inside.
- **The jumper buys almost nothing in clock quality.** On B3, a real GCLK ball,
  pad-to-global-buffer routing is 2.64 ns; on F3, which is not clock-capable, it
  is 3.99 ns, and both pay the same 3.32 ns through the buffer. So B3 is 1.35 ns
  better — not a different class of routing. Yesterday's argument for the jumper
  had two halves, "a real clock ball" and "a third data bit"; only the second
  half survives contact with the router. It is still worth doing, for that
  reason alone.

`mode=passive` and `mode=active` produce byte-identical bitstreams on this
device — checked, rather than assumed, because the firmware's whole configuration
path depends on the distinction. Passing `mode=passive` anyway, as documentation.

Firmware now links with the real 173,124-byte image: 417 KB `.uf2` against 2 MB
of flash. Everything that can be done away from the board is done.

### 2026-07-29 — netlist, and the 8-bit bus dies

`kicad-cli` is not installed and the vendor ships no netlist, so
[`tools/kicad_netlist.py`](../tools/kicad_netlist.py) recovers connectivity
geometrically from the `.kicad_sch` files: union wire endpoints, attach labels
and power symbols to the points they sit on, project each symbol's library pins
through its placement transform. It reproduces all eleven RP GPIO assignments
already known from the PDF, which is what makes the rest of its output usable.

Two parser bugs were worth the time to fix, because both would have produced
confident wrong answers rather than obvious failures:

- **Multi-unit symbols.** KiCad places each unit of a resistor pack separately.
  Merging all four units onto one instance put three quarters of the pins at
  fabricated coordinates and invented nets that do not exist.
- **Power symbols.** `power:+3V3` is referenced through a local `lib_name`
  override, so the naive lookup missed its pin and every rail read as an unnamed
  net — which made a 10 kΩ pull-up indistinguishable from a pull-down. That
  distinction is exactly what tells you `SS_N` is strapped to passive mode.

Findings, in descending order of how much they hurt:

1. **Only 6 header pins reach the RP**, in three isolated pairs, and 13 GPIO are
   unbonded. The widest contiguous RP↔FPGA run is **3 bits** (GPIO1–3, the config
   SPI pins reused after `DONE`). The 8-bit parallel dataplane is not buildable.
2. Bank spread on the FPGA side is a non-issue — 18 header pins, 7 of them in
   bank 2A.
3. `CRESET_N` and `SS_N` have 10 kΩ **pull-downs**: the board is hard-strapped to
   passive SPI configuration. That retroactively explains why the vendor's
   `active (x1)` bitstream configured fine.
4. The PSRAM is populated — **confirmed by photograph 2026-07-30**, after a day
   spent wrongly retracting it on the strength of the layout's `dnp` flag. Treat
   `dnp` and `positions.csv` in this repo as **not evidence either way**: `J3`
   carries the same flags and is also fitted. U1 is nonetheless silent on the
   bus, which is [#10](history.md#verify-before-building). The SWD pads need no connector,
   and header pad 18 is `QSPI_SS` — a probable BOOTSEL escape hatch.
5. Two earlier claims corrected: SW1 is on **G6 (`CSO`)**, not E4/`CBSEL1`; and
   the schematic independently confirms the vendor `.isf` has red and blue
   swapped (E1 = R, G1 = B).
