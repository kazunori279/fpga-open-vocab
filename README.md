# fpga-open-vocab — describe it, the board spots it

**fpga-open-vocab** points a camera at something, you type **"a person smiling"** on the host, and a $50 board answers — by running a distilled [CLIP-style](https://arxiv.org/abs/2103.00020) image encoder on a [Forgix](https://forgix.tech/) board, which pairs an [RP2354A](https://www.raspberrypi.com/products/rp2350/) MCU with an [Efinix Trion T8](https://www.efinixinc.com/products-trion.html) FPGA.

It is built and it runs. All eight convolution layers execute on the FPGA tile, off a live camera, and **bit-exact** against the plain-C reference — a whole camera frame in **282 ms**, of which **265 ms** is the encoder. Against the same model on the MCU alone, measured in the same boot and at the same 320 MHz, the tile is **5.97×** faster.

```
ArduCam SPI ──▶ RP2354A ──3-bit on-board link──▶ Trion T8 ──▶ RGB LED / USB serial
                   │                                 │
              768 KB int4 weights               int8 MAC array
              in 2 MB stacked flash             16 MACs × 160 MHz
```

---

## What it does

1. **Once per query, on the host.** You type a phrase. The host runs the teacher's real text tower and sends the resulting **512-d embedding** to the board over USB. The text side never runs on the device.
2. **Once per frame, on the board.** A 128 × 128 × 3 image comes off the camera. A 1.40 M-parameter int4 CNN — distilled to imitate the teacher's *image* tower — runs eight 3 × 3 convolution stages over it.
3. **The MCU cuts each layer into blocks and feeds the FPGA.** The tile holds 2,048 int32 accumulators and a 2 KB slice of input, so a frame becomes 174 blocks, 1,856 passes, 6,264 transactions across a link that is **3 bits out and 1 bit back**. The MCU is also the tile's only clock.
4. **The MCU finishes what the tile does not do** — requantize, scatter, and after the eighth layer an average pool and one 256 → 512 linear. That is the image's own embedding, in the same space as step 1's.
5. **The board decides.** Cosine against each query, standardized against the background *this room* reads at, then answered in two parts — **which** of the scenes it was shown this is, by nearest reference, and **whether** it is any of them at all, by how far the nearest one is — both learned by being **shown** the scenes rather than given a threshold. The answer comes out on the RGB LED and on the serial log.

The long version, with the numbers and the reasons, is [`docs/architecture.md`](docs/architecture.md).

**Thinking of using it for something?** Read
[`docs/fit.md`](docs/fit.md) first. Step 5 is the catch: the board is a
*one-scene, enrolled* discriminator, not a detector that generalises to a room
it was never shown, and **what it does depends heavily on what you ask it to
tell apart** — four pairs benched back to back on one afternoon scored 95.8,
90.8, 50.0 and 34.2%. That page is the three screens between an idea and an
answer. The first is a wording rule that costs nothing and eliminates most
ideas; the second is one command and no hardware:

```sh
uv run --script tools/fit_check.py --cat person \
    --pos "an open hand" --neg "a fist" \
    --pos-edit "the hand is open, fingers spread flat" \
    --neg-edit "the hand is closed into a tight fist"
```

## Quickstart

```sh
uv sync

# firmware -> firmware/build/forgix_m9.uf2  (WIDE = the jumpered 3-bit link)
cmake -S firmware -B firmware/build -G Ninja -DLINK_CFG=WIDE \
  -DPICO_TOOLCHAIN_PATH=$HOME/toolchains/arm-gnu-toolchain-14.2.rel1-darwin-arm64-arm-none-eabi
ninja -C firmware/build

picotool reboot -f -u
picotool load firmware/build/forgix_m9.uf2 -x

# the FPGA has no config flash, so the bitstream goes over USB at every run
uv run host/demo.py --bitstream rtl/bitstreams/m16/gemm_top_wide.hex \
                    "cup" "person" "book" "laptop"
```

The model itself is in the tree: `model/runs/so400m-full-a05/export/` holds a
780,720-byte blob — 768 KB of int4 weights and their headers — the test vector
the firmware checks itself against, and the
`export.json` that names the embedding space they belong to. It is the one thing
committed under the otherwise-gitignored `model/runs/`, because without it none of
the above builds. The board prints the blob's crc32 at boot; if that disagrees
with `export.json`, the weights and the host's text tower are not in the same
space — and a cosine between two 512-d vectors from different spaces returns a
plausible-looking number rather than an error.

A contrast query asks for one thing *as against* the others, and needs nothing new on the device:

```sh
./ab.sh "an opened book" "a closed book" --enrol
```

Full instructions — toolchain, the Efinity container, flashing, recovery, every
harness, and rebuilding the model — are in [`docs/building.md`](docs/building.md).

## Where it stands

| | |
|---|---|
| **frame time** | **282 ms end to end** on the camera appliance at 320/160, and **293 ms** by an independent wall clock on the board — 300 of 300 frames in one run. The encoder inside it is **265 ms**; the rest is a 16 ms burst read off the camera plus the queries, the z-scoring and a CDC line per frame. It was 429 ms until [#10](https://github.com/kazunori279/fpga-open-vocab/issues/10) overlapped the capture with the compute — the frame used to *step* in units of one sensor frame rather than scale, so 332 MHz measured the same 420 as 320 — and 373 ms until [#14](https://github.com/kazunori279/fpga-open-vocab/issues/14) found the appliance still draining int32 accumulators off the tile, three milestones after M15 shipped an epilogue that drains codes. One line, 80 ms. The `m7` harness, which runs no camera and answers no queries, is **270 ms** at 320/160 |
| **latency** | **390 ms shutter to LED**, which is a different question from the frame time and moves the other way: overlapping the capture makes frames come faster and each one older. [#14](https://github.com/kazunori279/fpga-open-vocab/issues/14) moved the trigger from the start of the compute to the end, 725 → 494 with the frame time unchanged, and then shortened the compute the trigger waits behind, 494 → 390. The serial capture it is measured against was 435 ms before that second change |
| **bit-exactness** | 512 of 512 embedding floats identical to `firmware/encoder.c` |
| **speedup** | **5.97×** the same model on the MCU alone, both at 320 MHz in the same boot — `encoder_fast` 1,583 ms against the tile's 265 ms. The 11.33× this row claimed until 2026-08-15 is `m7`'s, and it is not one clock: `m7` measures its MCU baseline at its 150 MHz boot rate and its frame at 280 |
| **clocks** | **320 MHz sys / 160 MHz link** on the appliance, core 1.25 V, bit-exact there. **332 is bit-exact too and 10 ms faster, and is deliberately not shipped** — it buys 3.4% and sits 8 MHz below 340, where the link stops answering, twice, deterministically. [#13](https://github.com/kazunori279/fpga-open-vocab/issues/13) has the four-rate sweep that says so |
| **model** | 1.40 M int4 parameters in 768 KB, 159 MMAC, distilled from SigLIP 2 SO400M through a frozen PCA to 512-d |
| **retention** | 91% of the queries the teacher itself gets right, at int4 |
| **fabric** | **6,265 of 7,384 LE (85%)**, 8/8 multipliers, **21 of 24 memory blocks** — it started at 33% with memory the only thing running out; three milestones of arithmetic later both are nearly full |
| **link** | 26.4 MB/s forward measured, 8.9 MB/s back, and it cannot be widened either way |
| **decision rule** | **95.8% and 90.8% held out on the board** on 2026-08-17, on hands and on bags — the first runs since 08-11's 120/120 to reproduce 90%+, and the first on pairs that are not the book. Four pairs run back to back that afternoon scored 95.8 / 90.8 / 50.0 / 34.2%, and that spread is the honest headline: **what the appliance does depends on what you ask it to tell apart.** Neither of the two that failed is the decision rule, and neither is what it was first written up as. `tools/probe_ceiling.py` measures the ceiling — with two queries the centred space is one-dimensional, so the margin's own separability is a hard bound on any rule the board can run — and against it the glass **collected 60.0% of a 67.5% ceiling** and the person **78.3% of an 80.4%** one. The glass pair reads margin AUC 0.301, which is inverted rather than absent (0.199 from chance, named backwards) and folds to 0.699; that is low, but the book pair has read 0.599 on a morning when the encoder was demonstrably fine, so **one run cannot tell a model limit from a bad morning** and the glass has one run. The person's scatter (−0.77/+0.77/+0.91/+1.22) is real and cost 2.1 points; what took it from 78.3% to the 50.0% quoted above is #18's presence gate calling 34 held-out class frames absent, and `enrolled from 0/6` is the same gated artefact. The book's own collapse — 58.3% and 57.5% on 2026-08-16, then 57.5% again on 08-17 — is [#19](https://github.com/kazunori279/fpga-open-vocab/issues/19) and still open, but that last figure came from a control run with the empty rotation taken back out, which is #19's own outcome (2): the re-staging is real, the bench schedule is not what causes it. The visit centres say what is: the opened book's four visits walked +1.96, +3.25, +4.64, +4.39 toward the closed book sitting flat at +5.8, **without ever leaving the frame**, so 9 of 60 held-out opened frames were called right against 66 of 66 closed ones. Not the encoder — the margin still separates those scenes at 93.8%, AUC 0.970, and the weights and bitstream crc32s are byte-identical across all three dates. Replaying the ceiling over all eighteen archived pair benches narrows #19 to **four** of them: 08:55, 13:35, 17:22 and 09:33 lost 63, 36, 27 and 19 points the margin was offering, and every other low score collected what was on offer and was low because the ceiling was low that morning. One of #19's own two founding runs, 08-16 17:35, had a ceiling of 0.599 and lost 5.6 points, so it never tested the rule at all. The same replay shows the book pair's ceiling spanning **1.000 to 0.579 across fourteen runs of the same desk** — 42 points decided before the decision rule is reached. The rule's other half, presence, is settled in shape and not in constant: the level-based one failed at 17.8% and 24.4%, and [#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) replaced it with open-set rejection, which scores **90.0% and 87.8%** replayed on the very frames that killed it. **Replayed across all fourteen archived benches** by `tools/probe_presence.py`, six are inverted — the empty desk nearer the references than the objects, which no threshold repairs — and of the eight that are not, five sit at AUC 0.90–0.96. But fit the radius on thirteen benches and score it on the fourteenth and the best unit gains 2.5 points over the shipped `2.0 sep` while paying **12.0** for not having seen that bench — and that gain has shrunk every time a bench was added (6.0, 3.9, 2.3, 2.5) while the cost has not moved, so there is nothing worth changing it to |
| **the flaky two** | both now measured rather than guessed at. The camera bus's **worst gap is 16 µs against a 2,000 µs deadline** over 152 frames at 280/140, and 15 µs over 101 at 320/160 — the same at both rates, 125× clear, so [#12](https://github.com/kazunori279/fpga-open-vocab/issues/12) is not the fast side running out of time. A USB outage is now caught **by the board** off `sof_rd` rather than inferred by the host from a log that stopped: it re-attaches itself in ~2.8 s, says which frames it lost, and reboots deliberately at 30 s. `'U'` and `'I'` make both halves happen on demand, which is how they were checked. [#9](https://github.com/kazunori279/fpga-open-vocab/issues/9) is still open, and it now has a third shape in it: a reset from underneath the firmware, which the banner separates from a watchdog reboot by diffing `POWMAN_CHIP_RESET` |

Two GO/NO-GO gates were passed on the way: **M2** (is the on-board link fast and
clean enough to be worth using) and **M4** (can a model small enough to fit still
tell the queries apart). What is left is not speed — it is the decision rule, and
the one honest gap that was left in it has now been measured and it came back
badly. The presence stage's *benefit* had never been tested, because it had never
fired on a bench — the only empty scene in the schedule was the one the rule was
taught from, so the 0/30 that stood in for it was training accuracy. The bench
now returns to an empty desk after every class is enrolled and scores it, and on
two runs the stage held **17.8% and 24.4%** of those frames. The cause is
structural rather than a threshold: the presence axis is the common mode, which
is exactly the part the state axis subtracts because it is where the sensor's
drift lives. [#18](https://github.com/kazunori279/fpga-open-vocab/issues/18)
replaces it with open-set rejection in the centred space — absent when the frame
is further than 2.0 `sep` from every reference the board was shown — and because
that quantity is recoverable from the logs, the replacement was scored on the two
failing runs **before** the board was touched: **90.0% and 87.8%** held, keeping
98.3% and 85.0% of the class frames. That is now the firmware, and fourteen
benches have since run it: the shape holds where the scene lets it, the radius is
not worth retuning — the leave-one-out gain over the shipped constant has shrunk
with every bench added while the cost of blindness has not moved — and what is
left of #18 is a geometry question. The same runs put the state stage's 120/120
in doubt as well
([#19](https://github.com/kazunori279/fpga-open-vocab/issues/19)), which eight
benches later is still open with the schedule ruled out, though two of those
eight came back at 95.8% and 90.8% on pairs that are not the book.

Several benches have since tried to confirm it and none of them measured the
rule — each was spent on a bad enrolment instead, in a way nothing was checking
for. So the board now checks: it compares the gap between the two references
against the **spread of the frames each was averaged from**, and prints both at
the console before the first held-out frame. It first called a run on hardware
on 2026-08-17 at 0.7×, ninety seconds in, on one that went on to score 47.5% —
right, and nine minutes earlier than the scoring pass.

**The first version measured that spread inside one enrolment window, and that
was the wrong quantity** — two runs at 2.85× and 2.75× scored 91.7% and 59.2%.
One window says how still the operator held the scene; what decides a run is
where the *same object staged again* lands, which at the moment a reference is
taken has never been observed. So enrolment takes **two visits** now: press the
class's digit again later and both windows fold into one reference.

**Then it was tested prospectively eight times and came out backwards at both
ends.** Measured over two visits the ratio put nine benches on the correct side
of 2.0 with nothing between 1.24 and 2.64 — and the tenth read 1.8× on the
board, was told to throw itself away, and scored 92.5%, the best in the project.
That left the bar as good news only, until the run that finally cleared it did
so at **3.7×, the highest this arithmetic has ever produced on any bench**, and
scored **57.5%**. Every prospective test it has had, in order:

| board ratio | held out | the bar's call |
| --- | --- | --- |
| 5.5× | 95.8% | certify — right |
| 3.7× | 57.5% | certify — **wrong** |
| 2.4× | 90.8% | reject — **wrong** |
| 2.3× | 74.2% | reject — right |
| 1.8× | 92.5% | reject — **wrong** |
| 1.2× | 68.3% | reject — right |
| 0.5× | 50.0% | reject — right |
| 0.4× | 34.2% | reject — right |

Four of those eight are calls that mattered — the two best runs and the two
worst — and the bar gets **one of the four** right. The extremes line up, which
is exactly what the previous three mistakes looked like at the moment they were
made, and 15:42 is the counterexample to remember: two tenths under the bar, and
90.8%. **So `FGX_ENROL_SNR` is deleted rather than re-fitted.** Four quantities
measurable at enrolment have now failed the same way — `sep`, the one-window
ratio, the two-visit ratio, and the two-visit ratio read one-sided — because
what decides a run is where the object lands on visits that have not happened
yet. The board still computes the ratio and prints it, and no longer says
anything about what it means. `sep` in particular carries no signal at all: its
largest value belongs to a 76.7% run, and since #18's absent radius is quoted in
`sep`, that radius is still not being touched.

What the board prints *before* the ratio has done better than the ratio: the
visit centres, and the distance each reference enrolled from the origin. The
two runs no ratio explains are both plain there — the book's centres walking in
one direction (drift), the person's scattering around a mean that is empty
(variance) — and every bench where the empty desk was absorbed by a class has a
reference sitting almost on the origin, which the board names at enrolment. It
is right five times out of the eight that could have carried the warning, wrong
twice and silent once. Better than any ratio, still not a rule, and that
geometry — not the radius — is what is left of #18.

**Open work is in [issues](https://github.com/kazunori279/fpga-open-vocab/issues)**,
labelled `P0`/`P1`/`P2`. The docs here record what was measured; what is still owed
is tracked there.

The milestone-by-milestone record is [`docs/milestones.md`](docs/milestones.md);
the frictions, the rejected designs and what they taught are in
[`docs/history.md`](docs/history.md).

## Documentation

| where | what |
|---|---|
| **this file** | what the thing is and where it stands |
| [`docs/fit.md`](docs/fit.md) | **will it work for what you want** — what the board is and is not, the three screens between an idea and an answer, and why the first two are free |
| [`docs/architecture.md`](docs/architecture.md) | **how it works** — the board, the model, the decision rule, the pipeline, the link, the fabric, the two cores, and where each of them lives in the tree |
| [`docs/building.md`](docs/building.md) | **how to build and run it** — toolchain, firmware, bitstream, tests, flashing, every harness |
| [`docs/history.md`](docs/history.md) | **how it got here** — the timeline, the frictions, the learnings, the rejected alternatives, the risks |
| [`docs/milestones.md`](docs/milestones.md) | the dev plan, M0 through M21: what each milestone was scoped to do, what it actually measured, and where the two differed |
| [`docs/bring-up-log.md`](docs/bring-up-log.md) | dated bench entries, newest first, including several that exist only to record a claim that later turned out to be false |
| [`docs/pinmap.md`](docs/pinmap.md) | M0's output: the confirmed pin and bank map, extracted from the vendor's KiCad source |
| [`rtl/README.md`](rtl/README.md) | the Efinity flow, and the four things it does not tell you |
| [`slides/index.html`](slides/index.html) | a 50-minute conference deck on all of the above, [published here](https://kazunori279.github.io/fpga-open-vocab/slides/) — or open the file in a browser, no build step ([notes](slides/README.md)) |
| [`slides/index.ja.html`](slides/index.ja.html) | the same deck [in Japanese](https://kazunori279.github.io/fpga-open-vocab/slides/index.ja.html) — a translation, not a fork; a link in the corner of each deck switches to the other |

`milestones.md` and `bring-up-log.md` are **append-only**: their numbers are what
was true when that entry closed, and nothing there is edited after the fact.
`history.md` is the curated counterpart — when a claim in it is overturned, the
overturning goes in beside it. Where any of them disagrees with this README or
with `architecture.md`, these two are the current ones, and the disagreement is
usually the interesting part.

The figures are [Mermaid](https://mermaid.js.org/), which GitHub renders from the
markdown so they cannot drift from what is committed. The one exception is the
wire timing diagram, which Mermaid has no notation for: that one is
[WaveDrom](https://wavedrom.com/), rendered to a committed SVG by `make -C docs`.

`uv run tools/check_links.py` checks every cross-file link and heading anchor
across all of them, and `--check-figures` additionally re-renders
`docs/img/wire.svg` from its source and fails if the committed copy has drifted.
It exists because the first README split silently broke 151 anchors.

## Layout

```
fpga-open-vocab/
├── README.md          # this file
├── LICENSE            # Apache-2.0
├── .github/workflows/ #   pages.yml — publishes slides/ to GitHub Pages
├── ab.sh              # one A/B scene experiment, from two phrases
├── pyproject.toml     # host tooling deps (uv sync / uv run)
├── schematic.pdf      # local only — *.pdf is gitignored; re-fetch from the
│                      #   Bitbucket link below (Forgix rev 2026-02-24)
├── bench/             # the cue benches themselves — every accuracy number in
│   ├── README.md      #   this repo came out of one of these logs, and they
│   └── cue/           #   lived in /tmp until 2026-08-17. The README is the
│                      #   manifest: which run is which, and which of the two
│                      #   held-out figures each table quotes
├── docs/              # architecture, building, history, milestones, bring-up
│   ├── diagrams/      #   wire.json — WaveDrom source
│   ├── img/           #   wire.svg — generated from it, committed
│   └── Makefile       #   `make -C docs` regenerates it
├── model/             # PyTorch: distillation, quantization, weight export
│   ├── teacher.py     #   the teacher's towers; spaces.py resolves WHICH teacher
│   ├── student.py     #   the 1.40 M-param CNN, and its budget table
│   ├── distill.py     #   the training loop
│   ├── quantize.py    #   int8 calibration
│   ├── evaluate.py    #   retention, thresholds, eval embeddings
│   ├── export.py      #   -> the flat int8 blob + export.json naming the space
│   ├── data.py        #   COCO fetch / resize / query lists
│   ├── captions.py    #   caption embeddings, for host/caption.py
│   └── runs/…/export/ #   the shipped blob — weights.bin, testvec.bin,
│                      #     export.json. The only committed thing under runs/
├── firmware/          # RP2354A, Pico SDK
│   ├── encoder.c      #   THE REFERENCE: the int8 encoder in plain, slow C
│   ├── encoder_fast.c #   the same maths with an SMLAD inner loop, 7.4×
│   ├── gemm_*.c       #   block layout, link protocol, framing, blocking plan
│   ├── frame.{c,h}    #   one frame through the tile, shared by every demo
│   ├── cam.{c,h}      #   ArduCam Mega over PIO SPI
│   ├── worker.{c,h}   #   the two job rings core 1 runs
│   ├── m5..m9.c       #   the on-device harnesses; m9.c is the appliance
│   ├── test_*.c       #   laptop-side tests: encoder, wire, plan, pixels
│   ├── link.pio       #   4 PIO programs: {narrow,wide} × {2,4} cycles per bit
│   └── boards/        #   the PICO_BOARD header
├── host/              # everything that talks to the board over USB CDC
│   ├── demo.py        #   phrase -> text tower -> 512 floats -> USB (the demo)
│   ├── cue.py         #   the same, run as a cued A/B scene experiment
│   ├── m6/m7/m8.py    #   the per-milestone harness drivers
│   ├── cam.py         #   render a dumped frame to PNG, or a live preview
│   ├── caption.py     #   the reverse direction: 512 floats read back in English
│   ├── load.py        #   stream a bitstream to the T8
│   ├── forge.py       #   the Forge Loader USB CDC protocol
│   ├── board.py       #   which serial port is the board — one answer for all
│   ├── bootsel.py     #   get the board back, from any state
│   ├── mon.py         #   read a report off the port and tee it to a file
│   └── probe.py       #   report loader state
├── tools/
│   ├── check_links.py #   every markdown link and heading anchor, GitHub's rules
│   ├── kicad_netlist.py # pin -> net extractor for the vendor .kicad_sch files
│   ├── hex2c.py       #   Efinity .hex/.bin -> C array for embedding
│   ├── score_cue.py   #   score a cue.py run against the boundaries it recorded
│   ├── score_drift.py #   measure what moves when nothing moves
│   ├── teacher_swap.py # re-encode a split with SigLIP 2 through a frozen PCA
│   └── probe_*.py     #   offline probes, each carrying its results in its
│                      #     docstring, so a stale one is visibly stale
├── slides/            # the conference deck, published to Pages from here
│   ├── index.html     #   English; index.ja.html is the same 39 slides in
│   └── index.ja.html  #     Japanese. Hand-kept in step — nothing checks them
└── rtl/               # Trion T8
    ├── gemm_tile.v    #   16 int8 MACs/clk, accumulator banks, weight buffer, FSM
    ├── im2col_feed.v  #   strip buffer + address generator + zero injection
    ├── gemm_link.v    #   command framing, preamble + CRC on the return path
    ├── gemm_top*.v    #   config A and config C tops, with their .sdc and .isf
    ├── link_*.v       #   the M2 link cores and tops
    ├── tb_*.v         #   iverilog testbenches
    ├── probe_*.v      #   timing probes; tile_probe.v is the tile's own Fmax
    ├── build.sh       #   macOS -> container -> rtl/build/*.hex
    ├── bitstreams/    #   the images actually verified on hardware, per milestone
    └── docker/        #   Efinity 2026.1 on Linux/amd64
```

Two things this tree does **not** carry, both third-party and both re-fetchable
from Adiuvo's [developer repo](https://bitbucket.org/adiuvo-engineering/forgix_public):
the `plasm_led` example (its bitstream and its Efinity pinout reports, which are
Efinix tool output), and the Forge Loader `.uf2`. `docs/pinmap.md` cites the
first as a source and `docs/building.md` names the second as the un-bricking
path; download them before you need them.

## References

- [Forgix product page](https://forgix.tech/) · [developer repo](https://bitbucket.org/adiuvo-engineering/forgix_public)
- [Trion T8 datasheet DST8-v5.5](https://www.efinixinc.com/docs/trion8-ds-v5.5.pdf) · [Trion overview v3.3](https://www.efinixinc.com/docs/trion-overview-v3.3.pdf)
