# fpga-open-vocab — describe it, the board spots it

**fpga-open-vocab** points a camera at something, you type **"a person smiling"** on the host, and a $50 board answers — by running a distilled [CLIP-style](https://arxiv.org/abs/2103.00020) image encoder on a [Forgix](https://forgix.tech/) board, which pairs an [RP2354A](https://www.raspberrypi.com/products/rp2350/) MCU with an [Efinix Trion T8](https://www.efinixinc.com/products-trion.html) FPGA.

It is built and it runs. All eight convolution layers execute on the FPGA tile, off a live camera, and **bit-exact** against the plain-C reference — a whole camera frame in **363 ms**, of which **346 ms** is the encoder. Against the MCU running the same model alone, at the same clock, the tile is **11.33×** faster.

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
5. **The board decides.** Cosine against each query, standardized against the background *this room* reads at, then split into a presence axis and a state axis, both learned by being **shown** the scenes rather than given a threshold. The answer comes out on the RGB LED and on the serial log.

The long version, with the numbers and the reasons, is [`docs/architecture.md`](docs/architecture.md).

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
| **frame time** | **363 ms end to end** on the camera appliance at 320/160, and **373 ms** by an independent wall clock on the board — 300 of 300 frames in one run. The encoder inside it is **346 ms** and scales exactly with the clock; the rest is a 16 ms burst read off the camera plus the queries, the z-scoring and a CDC line per frame. It was 429 ms until [#10](https://github.com/kazunori279/fpga-open-vocab/issues/10) overlapped the capture with the compute: the frame used to *step* in units of one sensor frame rather than scale, so 332 MHz measured the same 420 as 320. The `m7` harness, which runs no camera and answers no queries, is **304 ms** at 280/140 |
| **latency** | **494 ms shutter to LED**, which is a different question from the frame time and moves the other way: overlapping the capture makes frames come faster and each one older. [#14](https://github.com/kazunori279/fpga-open-vocab/issues/14) moved the trigger from the start of the compute to the end and took 231 ms off it — 725 → 494, with the frame time unchanged at 373 — leaving a 59 ms penalty over a serial capture's 435, which is the exposure itself |
| **bit-exactness** | 512 of 512 embedding floats identical to `firmware/encoder.c` |
| **speedup** | **11.33×** the same model on the MCU alone (`encoder_fast`, 3,359 ms, measured in the same boot) |
| **clocks** | **320 MHz sys / 160 MHz link** on the appliance, core 1.25 V, bit-exact there. 340 is not a higher setting to try: the link stops answering, twice, deterministically |
| **model** | 1.40 M int4 parameters in 768 KB, 159 MMAC, distilled from SigLIP 2 SO400M through a frozen PCA to 512-d |
| **retention** | 91% of the queries the teacher itself gets right, at int4 |
| **fabric** | **6,265 of 7,384 LE (85%)**, 8/8 multipliers, **21 of 24 memory blocks** — it started at 33% with memory the only thing running out; three milestones of arithmetic later both are nearly full |
| **link** | 26.4 MB/s forward measured, 8.9 MB/s back, and it cannot be widened either way |
| **decision rule** | 120/120 held out on the board at M21, against 90/180 for ranking the same frames |

Two GO/NO-GO gates were passed on the way: **M2** (is the on-board link fast and
clean enough to be worth using) and **M4** (can a model small enough to fit still
tell the queries apart). What is left is not speed — it is the decision rule, and
one honest gap in it: the presence stage's *benefit* has never been measured,
because it has never fired on a bench.

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
│   ├── cam.py         #   render a dumped frame to PNG
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
