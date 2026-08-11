<!-- moved out of README.md on 2026-08-11; see ../README.md#documentation -->

# Building, flashing and running

Everything you have to install, everything you can build, and how to get each
piece onto the board and talking. Nothing here explains *why*; that is
[`architecture.md`](architecture.md) for the design and
[`history.md`](history.md) for the reasoning.

Three pieces build independently and only meet on the board: **the model** (host
PyTorch → an int8 blob compiled into the firmware), **the fabric** (Verilog →
an Efinity `.hex` bitstream, streamed to the FPGA over USB at runtime), and
**the firmware** (Pico SDK → a `.uf2`).

[← back to the README](../README.md) · [architecture](architecture.md) ·
[history](history.md) · [dev plan](milestones.md) · [bring-up log](bring-up-log.md)

---

## Toolchain

| Step | Where | Tool |
|---|---|---|
| Model training / distillation | host | [PyTorch](https://pytorch.org/), [`uv`](https://docs.astral.sh/uv/) |
| Quantization + weight export | host | PyTorch → flat int8 blob |
| FPGA synthesis | **Linux/amd64 Docker** | Efinix **Efinity** (T8 is supported in the free tier) |
| RTL simulation | host | [Icarus Verilog](https://steveicarus.github.io/iverilog/) — `make -C rtl sim`, no vendor tools needed |
| MCU firmware | host | [Pico SDK](https://github.com/raspberrypi/pico-sdk) + [`arm-none-eabi-gcc`](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads) — see the note below |
| FPGA config | host | `uv run host/load.py <image.hex>` over USB CDC |
| MCU reflash | host | **`picotool reboot -f -u`**, then **`picotool load`** the `.uf2` — see [flashing](#flashing-the-mcu) |

Host Python is managed by `uv` and never by hand:

```sh
uv sync                            # once, from pyproject.toml
```

Several scripts under `host/` and `tools/` carry [PEP 723](https://peps.python.org/pep-0723/)
inline dependency blocks and are run with `uv run --script <file>` instead, so a
one-off probe that needs `torch` does not pull `torch` into the shared
environment.

## Building the firmware

**Do not use Homebrew's `arm-none-eabi-gcc`.** It ships without newlib and there
is no `arm-none-eabi-newlib` formula, so the link fails with `cannot find -lg /
-lc` after compiling everything. Use the official Arm GNU Toolchain:

```sh
# one-time: unpack the Arm GNU Toolchain (darwin-arm64) into ~/toolchains
cmake -S firmware -B firmware/build -G Ninja -DLINK_CFG=NARROW \
  -DPICO_TOOLCHAIN_PATH=$HOME/toolchains/arm-gnu-toolchain-14.2.rel1-darwin-arm64-arm-none-eabi
ninja -C firmware/build
```

`LINK_CFG=WIDE` builds the jumpered 3-bit variant — **configuration C, which is
what every current number is measured in**. Both configurations build without a
bitstream present: `tools/hex2c.py` embeds an empty placeholder and the firmware
refuses to run the sweep.

One `ninja` produces every on-device harness as its own `.uf2`:

| target | what it is |
|---|---|
| `forgix_m9` | **the appliance.** Camera → encoder → tile → the decision rule → LED |
| `forgix_m8` | capture → encode → embed, forever, with no queries |
| `forgix_m7` | the full-frame inference harness: modes, profiling, clock ladder |
| `forgix_m6` | one tile block over the link, against golden accumulators |
| `forgix_m5` / `forgix_m5b` | the MCU-alone encoder and its `SMLAD` twin — the 11.33× baseline |
| `forgix_m2` | the link sweep: LFSR, offset correlator, error rate per clock |
| `forgix_cam_probe` | the camera alone, dumping a frame down the CDC |
| `forgix_psram_probe` | the raw `0x9F` read from U1. Kept for the record; U1 is unusable |
| `forgix_diag` | the wedge locator. **Currently unbuildable** — it wants `rtl/build/probe_a.hex`, which is gitignored |

## Building the fabric

**Efinity ships Windows and Linux builds only — no macOS**, and sits behind an
Efinix account login, so the installer cannot be fetched unattended. Build in a
container and talk to the board from the host; programming is plain USB-C to the
RP2354 rather than JTAG, so there is no USB-passthrough problem — the container
only has to emit a `.hex` file. **v2026.1.132 is downloaded and working**, and
the free Bronze licence covers the T8 until 2027-07-28.

```sh
export EFINITY_TARBALL=~/Downloads/efinity-2026.1.132-linux-x64.tar.bz2
export EFINITY_VERSION=2026.1
rtl/build.sh narrow            # or wide; -> rtl/build/link_*.hex + reports
```

First run builds a ~6 GB image; after that a compile is well under a minute.
[`rtl/README.md`](../rtl/README.md) documents the four undocumented things the
headless flow needs — in particular that it will skip pin assignment entirely,
with a single-line warning, if no `.peri.xml` exists.

**You often do not have to build it at all.** `rtl/bitstreams/` holds the images
actually verified on hardware, one directory per milestone, because a P&R seed
does not carry across netlists: a rebuild at the recorded settings is not
guaranteed to give the same file back.

## Tests that need no board

All of these run on the laptop, and between them they are why a bench trip is
rare:

```sh
make -C rtl sim                 # tb_link: both widths + a shorted-line control
make -C rtl tb_gemm             # golden vectors straight into the tile
make -C rtl tb_gemm_link        # the same vectors through the wire, 1 bit/clock
make -C rtl tb_gemm_link_wide   # ditto, 3 bits/clock
make -C rtl test_wire           # every bit offset and every failure mode
make -C rtl test_plan           # the blocking tiles every tensor exactly once
uv run tools/check_links.py --check-figures
```

`make -C rtl vec` regenerates the `$readmemh` vectors the testbenches read, from
`firmware/gen_gemm_vec.c` — which links `firmware/gemm_block.c`, so the RTL is
checked against the same code the MCU runs rather than against a second
description of it.

## Flashing the MCU

```sh
picotool reboot -f -u                              # into BOOTSEL, over USB
picotool load firmware/build/forgix_m9.uf2 -x      # write and run
```

Use **Homebrew's `picotool`**, not the SDK's, which is built without USB
support. Three things about this are load-bearing:

- **Never copy the `.uf2` to `/Volumes/RP2350`.** That is what hangs.
- **A real write takes ~22 s.** Check `picotool info` afterwards: a fast
  "success" wrote nothing.
- `picotool load -x` can leave the board sitting in BOOTSEL with a flash that
  looks empty. If that happens, load again.

`uv run host/bootsel.py` automates the recovery path and will power-cycle the
hub if asked (`--power-cycle`), which is the hammer. Fallbacks in order, if USB
will not do it: the **`PRG`–`GND` strap** on the short bottom edge while
plugging USB in — *not* the silkscreen `17`/`18` pads on the long row, which go
to FPGA balls B3/B7 — then SWD via the J2 pogo pads (Tag-Connect TC2030-IDC-NL).
The 1200-baud CDC touch is wired but does not fire.
Adiuvo's `forge_fpga_loader.uf2`, from the
[developer repo](https://bitbucket.org/adiuvo-engineering/forgix_public),
restores the vendor loader the same way. It is not in this tree — fetch it
before you need it, not while the board is bricked.

## Running it

The FPGA has no configuration flash, so **every run starts by streaming a
bitstream into it** — that is the `--bitstream` argument below, and it is why
RTL revisions cost no reflash of the MCU.

```sh
# the appliance
uv run host/demo.py --bitstream rtl/bitstreams/m11/gemm_top_wide.hex \
                    "cup" "person" "book" "laptop"

# a contrast query: the first phrase AS AGAINST the others
uv run host/demo.py "an opened book / a closed book / a book"

# one A/B scene experiment, cued and scored
./ab.sh "an opened book" "a closed book" --enrol
uv run --script tools/score_cue.py /tmp/m9_cue.log
```

`demo.py` holds the teacher resident, so `--ask` re-queries a **running** board
without re-encoding anything. It refuses to send a query set unless the crc32
the board prints over its own weights matches `export.json` — a board running
last week's student is the failure that catches.

At the board's console: `'0'`..`'6'` enrol the empty scene and each class,
`'N'` forgets the room and the enrolment and learns them again, `'H'` toggles
background hold, `'E'` forces a deferred LED failure to land somewhere visible.

The other harnesses, each with its own host script:

```sh
uv run host/m8.py  --bitstream rtl/bitstreams/m11/gemm_top_wide.hex --out /tmp/m8.log
uv run host/m7.py  --wide      rtl/bitstreams/m11/gemm_top_wide.hex --out /tmp/m7.log
uv run host/m6.py  --bitstream rtl/bitstreams/m11/gemm_top.hex      --out /tmp/m6.log
uv run host/mon.py --out /tmp/m5.log            # anything that just prints
uv run host/probe.py                            # loader state
uv run host/load.py <image.hex>                 # bitstream only
uv run host/cam.py --rot <n> < snap.b64          # render a dumped frame to PNG
```

`host/cam.py`'s `--rot` must match `firmware/frame.h`'s `FT_MOUNT_ROT`, and a
mismatch is invisible in every log the board prints.

`host/board.py` answers "which serial port is the board" for all of the above,
so none of them needs a hard-coded `/dev/cu.usbmodem*`.

## Rebuilding the model

Only needed if you are changing the student, the teacher or the quantization.
The board is flashed with whatever `model/export.py` last wrote.

```sh
uv run model/data.py fetch --split train2017      # COCO
uv run model/teacher.py embed --split train2017   # or tools/teacher_swap.py
uv run model/distill.py --split train2017 --epochs 40
uv run model/quantize.py --run train2017
uv run model/evaluate.py --split val2017 --emit-thresholds --emit-embeddings
uv run model/export.py --run train2017            # -> the int8 blob + export.json
```

**`export.json` is not optional.** Two 512-d embedding spaces ship, and a query
encoded by the wrong teacher produces a well-formed number that means nothing —
no exception, no NaN. `model/spaces.py` resolves the pairing from one string,
`export.json` records it beside the weights, and the board's weight crc32 lets
the host refuse a stale flash. All three exist for the same mistake.

## Regenerating the figures

Every diagram in these docs is Mermaid, which renders from the markdown and
therefore cannot drift — except one WaveDrom timing figure:

```sh
make -C docs                    # docs/diagrams/wire.json -> docs/img/wire.svg
```

`uv run tools/check_links.py --check-figures` re-renders it and fails if the
committed SVG does not match, which is the only thing making a committed
artifact a claim anybody checks.
