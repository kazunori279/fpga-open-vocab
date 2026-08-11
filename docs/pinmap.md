# Forgix pin map (confirmed)

Sources, in order of authority:

1. **KiCad source** — `RP2350_FPGA_eensy-main/` from the
   [developer repo](https://bitbucket.org/adiuvo-engineering/forgix_public),
   sheets `RP2350_FPGA_eensy` (root), `sch/RP2354A`, `sch/T8F49I2X`.
   Extracted with `../tools/kicad_netlist.py`.
2. `plasm_led.pinout.csv` — Efinity 2025.2 pinout report for T8F49 (ball → bank
   → IO standard), from the vendor's `plasm_led` example in that same repo. The
   example's build products are Efinix tool output and are not redistributed
   here; build it yourself, or read the reports there.
3. `plasm_led_io.isf` — the same example's pin assignments
4. Forge Loader firmware `include/board_config.h` (RP2354A GPIO numbers)

Status: **M0 resolved.** The header ↔ RP GPIO map is settled and the answer is
worse than hoped — see [RP ↔ FPGA bandwidth](#rp--fpga-bandwidth).

The extractor recovers connectivity geometrically (wire endpoints unioned,
labels naming the point they sit on). It reproduces all eleven RP GPIO
assignments already known from the PDF, which is the check that makes the rest
of its output trustworthy.

---

## RP2354A

| GPIO | Net | Role |
|---|---|---|
| 0 | `QSPI_PSRAM_CS` | PSRAM chip select (QMI CS1) |
| **1** | `FPGA.CS` | T8 config SPI `SS_N` (G3) |
| **2** | `FPGA.CLK` | T8 config SPI `CCK` (F3) |
| **3** | `FPGA.MOSI` | T8 config SPI `CDI0` (F2) |
| **4** | `FPGA.nRESET` | T8 `CRESET_N` (G4), active low |
| **5** | `FPGA.DONE` | T8 `CDONE` (F4), input |
| **6** | `FPGA.nSTATUS` | T8 `GPIOL_21_NSTATUS` (A4), input, active low |
| 8 | `RP.UART1_RX` | header PIN0 |
| 9 | `RP.UART1_TX` | header PIN1 |
| 12 | `RP.UART0_TX` | header PIN7 |
| 13 | `RP.UART0_RX` | header PIN8 |
| 17 | `ON/OFF` | header pad 19 |
| **19** | `FPGA.OSC_EN` | Y2 output-enable, active high, R6 = 1 MΩ pull-down |
| 22 | `PIO22` | header PIN2 |
| 23 | `PIO23` | header PIN3 |
| 28 | `SDA` | I²C / ADC2 |
| 29 | `SCL` | I²C / ADC3 |
| — | `QSPI_SS` (pin 60) | header pad 18, labelled `PROGRAM` |

The bold rows match the loader firmware's `board_config.h` exactly (SPI0, mode 3,
8 MHz default, MISO = −1).

**Unconnected — no pad anywhere on the board:** GPIO 7, 10, 11, 14, 15, 16, 18,
20, 21, 24, 25, 26, 27. These are unreachable without soldering to the QFN, so
the RP's usable IO is exactly the table above.

### Reset and recovery

There is no reset or BOOTSEL *button*. But header pad 18 (`PROGRAM`) is wired to
the RP2354A's `QSPI_SS`, which is the standard RP2350 BOOTSEL strap — grounding
it while power is applied should drop the chip into USB mass-storage mode for a
UF2 drag-and-drop.

This is inferred from the netlist plus the RP2350 boot ROM behaviour. The
RP2350 datasheet is explicit that `QSPI_SS` driven low at reset or power-up
selects BOOTSEL, and that the QSPI pads on the RP2354A reach the package pins
as well as the internal flash die — so the strap should work.

**Two bench attempts failed, and neither one actually tested the strap.** Both
bridged the pads silkscreened `17` and `18` on the long row — which are
`PIN17`/`PIN18`, pads 24/25, going to FPGA balls B3 and B7. Shorting two
pulled-up FPGA GPIO together does nothing, so the board booted normally each
time. See [Pad number ≠ silkscreen number](#-pad-number--silkscreen-number).

The strap is still **untested.** The correct bridge is `PRG` to the `GND`
immediately beside it, on the five-pad group on the short bottom edge.

Raspberry Pi's own reference wiring for a BOOTSEL button is a button **plus a
1 kΩ series resistor** to `QSPI_SS`, because the boot ROM actively drives that
pin once it starts accessing flash. A direct short should still win the
sampling window, but the resistor is the documented practice.

Worth the effort because it is the difference between "a bad firmware flash
bricks the board" and "a bad firmware flash costs a reflash." Note that the
fallback already works: a **1200-baud touch** on the CDC port drops the loader
firmware into BOOTSEL and mounts `RP2350`, verified on this Mac. That covers
every case except a hang before USB enumerates.

## Trion T8F49 — configuration

| Ball | Function | Bank | Goes to |
|---|---|---|---|
| G3 | `GPIOL_01_SS_N` | 1A | RP GPIO1, **10 kΩ to GND** |
| F3 | `GPIOL_02_CCK` | 1A | RP GPIO2 |
| F2 | `GPIOL_04_CDI0` | 1A | RP GPIO3 |
| F4 | `CDONE` | 1A | RP GPIO5, 10 kΩ to +3V3 |
| G4 | `CRESET_N` | — | RP GPIO4, **10 kΩ to GND** |
| A4 | `GPIOL_21_NSTATUS` | 1B | RP GPIO6 |

The two pull-**downs** are the important part and they explain a result that
looked anomalous during bring-up:

- `CRESET_N` low by default holds the FPGA in reset until the RP drives it high,
  so the T8 cannot start configuring before the RP is ready.
- `SS_N` low at the release of `CRESET_N` selects **passive** SPI configuration.
  The board is strapped passive in hardware. That is why the vendor's
  `active (x1)` bitstream configured perfectly over the passive path — the mode
  in the Efinity report describes how the *project* was compiled, not how this
  board boots.

Passive SPI x1 uses `CDI0` only, so `CDI1`–`CDI7` are free as user IO after
configuration — which is why the example design can drive the RGB LED from them.

## Trion T8F49 — header pins

**18 FPGA pins reach the header** (PIN4–6, PIN9–23).

| Header | Ball | FPGA function | Bank | Notes |
|---|---|---|---|---|
| PIN4 | A5 | `GPIOR_05` | 2A | |
| PIN5 | D7 | `GPIOR_17_CTRL6_CBUS2` | 2A | 10 kΩ pull-up |
| PIN6 | C7 | `GPIOR_16_CTRL7_CBUS1` | 2A | 10 kΩ pull-up |
| PIN9 | D6 | `GPIOR_15_CBUS0` | 2A | 10 kΩ pull-up |
| PIN10 | G7 | `GPIOR_37_TEST_N` | 2B | 10 kΩ pull-up |
| PIN11 | G5 | `GPIOR_34_CSI` | 2B | 10 kΩ pull-up |
| PIN12 | G2 | `GPIOL_03_CDI4` | 1A | |
| PIN13 | F5 | `GPIOR_24` | 2B | |
| PIN14 | F6 | `GPIOR_26_CBSEL0` | 2B | internal weak pull-up only |
| PIN15 | E5 | `GPIOR_23_CTRL4` | 2B | |
| PIN16 | C6 | `GPIOR_13` | 2A | |
| PIN17 | B3 | `GPIOL_16_CLK2` | 1B | **global clock ball — see below** |
| PIN18 | B7 | `GPIOR_10` | 2A | |
| PIN19 | A7 | `GPIOR_07` | 2A | |
| PIN20 | A3 | `GPIOL_18_CTRL2` | 1B | |
| PIN21 | C2 | `GPIOL_12_CTRL0` | 1A | |
| PIN22 | D2 | `GPIOL_11_CDI3` | 1A | |
| PIN23 | E2 | `GPIOL_09_CDI2` | 1A | |

Bank totals: **2A × 7, 2B × 5, 1A × 4, 1B × 2.**

### Clock-capable balls

The T8F49 has five: **B3** (`CLK2`), **C3** (`CLK0`), **E4** (`CLK4`), **E6**
(`CLK6`) on the global clock network, and **B4** (`PLLIN`) into the PLL. On this
board:

| Ball | Resource | Reaches |
|---|---|---|
| B4 | PLL input | Y2, the 32 MHz oscillator |
| **B3** | GCLK | **header PIN17 (pad 24)** |
| C3 | GCLK | nothing — no pad, no trace |
| E4 | GCLK | nothing (this is the `CBSEL1` ball, left unconnected) |
| E6 | GCLK | nothing |

So **B3 via PIN17 is the only global-clock input the RP2354A can ever drive**,
and only through a jumper, since no header pad touches both chips.

This matters more than it looks. The on-board config clock `F3` (`CCK`) is *not*
clock-capable, so a link clocked on F3 routes on general fabric.

Implications for M2:

- The widest **single-bank** group is 7 bits (bank 2A: PIN4, 5, 6, 9, 16, 18,
  19). An 8-bit bus needs one pin from a second bank; a 7-bit or 4-bit bus can
  stay inside 2A and avoid cross-bank skew entirely.
- `PIN5`/`PIN6`/`PIN9` are `CBUS[2:0]`, the passive-mode bus-width strap, held
  at `111` (= x1) by the pull-ups. They are sampled at `CRESET_N` release, so
  anything driving them must be **tri-stated or high during reconfiguration.**
  Reconfiguring the FPGA is a routine operation in this project, which makes
  those three pins the most fragile members of an otherwise attractive bank-2A
  bus.
- `PIN10` is `TEST_N` and `PIN11` is `CSI` — both config-critical and both
  pulled up for a reason. Prefer to leave them alone.
- **A clean 4-bit bank-2A bus is PIN4 / PIN16 / PIN18 / PIN19** — no config
  strapping, one bank, no skew. This is the low-risk default.
- `PIN14` is `CBSEL0` with only the T8's internal weak pull-up — the weakest
  strap on the board and the easiest to disturb.
- All unused GPIO default to **weak pull-up** (per the Efinity pinout report), so
  a floating jumper reads as 1, not as noise.

## Trion T8F49 — on-board peripherals (do **not** consume header pins)

| Part | Ball | FPGA function | Bank |
|---|---|---|---|
| LED red | E1 | `GPIOL_10_CDI7` | 1A |
| LED green | F1 | `GPIOL_06_CDI1` | 1A |
| LED blue | G1 | `GPIOL_05_CDI5` | 1A |
| SW1 (push) | G6 | `GPIOR_35_CSO` | 2B |
| Y2 osc out | B4 | `GPIOL_20_PLLIN` | 1B |

- D1 is a **common-anode** RGB LED: anode to +3V3, cathodes through R7 = 680 Ω
  (red), R8 = 360 Ω (green), R9 = 300 Ω (blue). Drive is **active low**, matching
  the `led_*_n` names in the vendor example.
- **The vendor example has red and blue swapped.** `plasm_led_io.isf` assigns
  `led_r_n` → G1 and `led_b_n` → E1, but the schematic wires **E1 = R** and
  **G1 = B**. The `.isf` is authoritative for what the bitstream drives; the
  schematic is authoritative for what colour actually lights. Green (F1) agrees.
- **D1 has two meanings, and which one is in force depends on the host.** From
  power-up it is the bring-up indicator it has always been — green blinks at
  3.81 Hz (the fabric is configured and clocked), blue is solid (`link_clk` has
  ticked at least once), red latches on a frame or drain fault. `m7` and `m8`
  never leave this mode.

  The first `CMD_LED` (0x07) hands D1 to the host for good, and from then on red
  and green are a **PWM meter** — `m9` drives them from the winning query's
  score, green at nothing and red at "this would print MATCH" — while blue goes
  out. A fault still overrides both, but it *blinks* red with green forced off,
  because solid red now means a hot score. See `gemm_top.v`'s `led_own`.

  The two brightnesses are not comparable at equal duty: R7 = 680 Ω on red
  against R8 = 360 Ω on green means green wins by roughly 2:1 into the same
  number. `FGX_LED_GTRIM` in `m9.c` is the by-eye correction for it.
- **SW1 is on `CSO` (G6), not `CBSEL1`** — one side to the ball, the other to
  GND, active low, relying on the T8's internal weak pull-up. `CSO` is only used
  in *active* configuration; this board is strapped passive, so the pin is free
  and the button is harmless. (`CBSEL1` at E4 is not connected to anything.)

`GPIOR_34_CSI` (G5) reaches the header but `CSO` (G6) is consumed by SW1, and
there is no config flash footprint — **the FPGA cannot self-boot on this board.**
The RP2354A is the only configuration path.

## Memory

**U1 = APS1604M-SQR-SN, 16 Mbit (2 MB) QSPI PSRAM — populated, on the underside.**
Visually confirmed 2026-07-30: a SOIC-8 soldered just past the Tag-Connect SWD
pad array, centred across the width of the board. It is wired to the RP2354A's
QSPI bus (`QSPI_SD0..3`, `QSPI_SCLK`) with its own chip select on **GPIO0**, i.e.
the QMI second CS — the standard RP2350 XIP-PSRAM arrangement, textbook.

⚠️ **Ignore the exclusion flags in the vendor KiCad source.** U1 carries
`dnp exclude_from_pos_files exclude_from_bom` in `RP2350_FPGA_eensy.kicad_pcb`,
and is therefore also absent from the generated `build/positions.csv`. Neither
describes the board that shipped: **`J3` carries the identical flags and is
likewise fitted.** Those two files are not independent — the CPL is generated
*from* the flags — so their agreement is not corroboration. This document
asserted "NOT populated" on that basis for one day; it was wrong. For a
populated-or-not question about this board, look at the board.

**It does not enumerate, though — and the reason is not the chip.**
`psram_detect_size()` returns **0** on hardware (M5, 2026-07-30): the QMI clocked
out a `0x9F` READ ID and read a KGD byte that was not `0x5D`. M5c printed the raw
reply, and searched at *bit* rather than byte granularity it says something the
byte-aligned read could not:

```
cs1  00 00 00 00 5e 0c 03 57 46 f6 9c 06   (identical on 20 reads,
                                            SCK 25 MHz down to 1.5 MHz)

  bit 50 ->  0D 5D 1B DA 70
             MFID 0x0D  AP Memory
             KGD  0x5D  known good die
             EID  0x1B  -> size_id 0 -> 2 MiB
```

**U1 is a healthy APS1604M and has been answering correctly all along.** Exactly
the part and density on the BOM. That retires GPIO0 pad isolation, solder faults,
QPI mode, and the sample delay alike — a 16× sweep of SCK against all four
`DIRECT_CSR.RXDELAY` values produced twenty byte-identical rows, which no timing
fault survives.

What is left is unexplained, and it is narrow: `0x9F` plus a 24-bit address
should put MFID at bit 32, and it arrives at bit 50 — 18 bit-times late, neither
byte- nor nibble-aligned, invariant under clock rate.

Four further things are now known about those 18 bits, and none of them explain
it:

- **It is not our driver.** Rev 4 ran both code paths against both chip selects.
  `flash_do_cmd_cs()` and a hand-rolled `raw_xfer()` each frame the stacked flash
  at exactly bit 8, and each slip +18 on U1. Only the chip select differs.
- **It is not a bad part.** Board #1's U1 is a different die — EID
  `1b da 70 1a 54 5d` against board #2's `1b da 70 19 78 30`, sequential serials
  off the same reel — and slips by the same 18.
- **It is not the wrong wire.** A per-lane quad capture puts the data on **SD1**,
  with SD0 idle low and SD2/SD3 idle high. No crosstalk ghost.
- **It is not a dummy-cycle miscount.** AP Memory specifies `9Fh` as "similar to
  Fast Read, but without the wait cycles" — there are no dummies to miscount, and
  the reply repeats with a clean 64-bit period, one whole ID record at a time.

Also noted from the quad-lane probe: the bus settles to `0xCC` when released,
i.e. SD3/SD2 high and **SD1/SD0 low**, so SD1 has no pull-up and floats low —
that is the `00 00 00 00` prefix.

No QMI register reaches an 18-bit offset (`RXDELAY` moves the sampling point
within a bit; dummy-cycle settings move whole bytes), so the next instrument is a
scope on SCLK and SD1 at the package. README open question #10 is closed as
bounded and reported upstream.

**So the weight store today is the RP2354A's 2 MB stacked flash.** Budget:
1.42 MB int8 weights + 173 KB T8 bitstream + ~60 KB firmware = 1.65 MB of
2.10 MB, so the ~1.5 M-parameter model fits with roughly 25 % headroom. U1 would
sit behind the same QMI and so would add no bandwidth; what it adds is 2 MB of
**writeable** space, which is what the model needs to grow past that budget.

## Clock

`Y2 = ECS-2520MV`, **32 MHz** — confirmed by `plasm_led_io.isf`, which names the
`B4` input `clk_32m`. Its tri-state/OE pin is `FPGA.OSC_EN` (RP GPIO19, active
high) with a 1 MΩ pull-down, so **the oscillator is off until the RP enables
it.** The loader firmware raises it and waits 1 ms before releasing `CRESET_N`.

32 MHz into the T8 PLL is the base for any MAC-array clock.

## Debug

`J2` is a Tag-Connect **TC2030-IDC-NL** SWD footprint — a bare pad array, no
connector to populate, so SWD is available with a TC2030-NL pogo cable and
nothing else. `SWDIO` = RP pin 25, `SWCLK` = RP pin 24.

---

## Header

The board is in the **Teensy 4.0 form factor** (the root schematic literally
places a `Teensy4.0` symbol as the footprint), which is why the header pins are
numbered PIN0–PIN23.

| Pad | Name | Connects to |
|---|---|---|
| 1 | GND | — |
| 2 | PIN0 | RP GPIO8 (UART1_RX) |
| 3 | PIN1 | RP GPIO9 (UART1_TX) |
| 4 | PIN2 | RP GPIO22 |
| 5 | PIN3 | RP GPIO23 |
| 6 | PIN4 | FPGA A5 |
| 7 | PIN5 | FPGA D7 |
| 8 | PIN6 | FPGA C7 |
| 9 | PIN7 | RP GPIO12 (UART0_TX) |
| 10 | PIN8 | RP GPIO13 (UART0_RX) |
| 11 | PIN9 | FPGA D6 |
| 12 | PIN10 | FPGA G7 |
| 13 | PIN11 | FPGA G5 |
| 14 | PIN12 | FPGA G2 |
| 15 | VBAT | — (no-connect in the schematic; silk `NC`) |
| 16 | 3V3 | +3V3 (silk `3V3`) |
| 17 | GND | — (silk `GND`; absent from the schematic symbol, present on the board) |
| 18 | PROGRAM | RP `QSPI_SS` (pin 60) — **silk `PRG`** |
| 19 | ON/OFF | RP GPIO17 (silk `EN`) |
| 20–30 | PIN13–PIN23 | FPGA (see table above) |
| 33 | VIN | VIN |

## ⚠️ Pad number ≠ silkscreen number

**This is the single easiest way to wreck a bring-up session on this board, and
it already cost two failed BOOTSEL attempts.** The `Pad` column above is the
KiCad symbol pin number. The board is silkscreened with **Teensy signal names**,
which are different numbers entirely:

| Silk | Pad | Goes to |
|---|---|---|
| `0`–`12` | 2–14 | pad = silk + 2 |
| `13`–`23` | 20–30 | pad = silk + 7 |
| **`17`** | **24** | **FPGA ball B3** — not GND |
| **`18`** | **25** | **FPGA ball B7** — not PROGRAM |

Because every unused T8 GPIO comes up with a weak pull-up, silk `17` measures
**+3.3 V**, which reads convincingly like a power rail and is not one.

The recovery pads are **not numbered on the silkscreen at all.** They are the
five on the short bottom edge, away from USB, reading left to right with the
USB connector at the top:

```
EN    PRG    GND    3V3    NC
19     18     17     16     15      <- pad numbers, descending
```

So `PRG` and `GND` are **physically adjacent** — the BOOTSEL strap is a bridge
between two neighbouring pads in the middle of that row, with no counting
involved. Pins 31 and 32 are absent from the schematic symbol; the board carries
`GND` and `VIN` at the far end of the left row, so treat those as unverified.

**No header pin touches both chips.** The RP owns PIN0–3, 7, 8; the FPGA owns
PIN4–6, 9–23. Any RP↔FPGA link beyond the config SPI has to be a jumper wire
between two header pads.

## RP ↔ FPGA bandwidth

This is the finding that constrains the whole M2 design.

**On-board, after configuration** the two chips share six wires, of which four
are usable as general IO:

| RP GPIO | FPGA ball | Usable as user IO? |
|---|---|---|
| 1 | G3 `SS_N` | yes |
| 2 | F3 `CCK` | yes |
| 3 | F2 `CDI0` | yes |
| 6 | A4 `NSTATUS` | yes |
| 4 | G4 `CRESET_N` | no — dedicated, must stay as reset |
| 5 | F4 `CDONE` | no — dedicated config output |

GPIO **1, 2, 3 are contiguous** and all land in FPGA bank 1A. GPIO6 is a fourth
wire but is not contiguous with them and sits in bank 1B.

**Over the header**, the RP can offer at most six more wires — GPIO 8, 9, 12, 13,
22, 23 — and they form three isolated pairs. Because GPIO 10, 11 and 14–21 have
no pad, **no jumper arrangement can extend the contiguous run past three bits.**

Consequences:

- A RP2350 PIO state machine needs contiguous pins for an `out` or `in` group,
  so the maximum parallel width between the two chips is **3 bits (GPIO1–3)**,
  however many jumpers are added. Nothing below changes that.

### But the clock does not have to be one of those three bits

PIO takes `out_base`, `in_base` and `sideset_base` from **three independent
registers**. Contiguity is required only *within* a group. The clock is a
side-set pin, so it can sit anywhere in the GPIO map — including on a header pad
that has been jumpered to the FPGA.

That gives two configurations, and the difference between them is one wire:

| | **A — narrow** | **C — wide** |
|---|---|---|
| Board modification | none | jumper pad PIN2 ↔ pad PIN17 |
| Clock | GPIO2 → F3 `CCK` | GPIO22 → **B3 `CLK2`, on the GCLK network** |
| Data out | GPIO3 → F2 (**1 bit**) | GPIO1,2,3 → G3,F3,F2 (**3 bits**) |
| Data in | GPIO1 ← G3 (1 bit) | GPIO6 ← A4 (1 bit) |
| Spare | GPIO6 ← A4, used as a heartbeat | — |
| Bits per link clock | 1 out, 1 in | **3 out**, 1 in |
| Ceiling at `sys_clk`/2 = 75 MHz | 8.9 MB/s each way | **26.8 MB/s out**, 8.9 MB/s back |

Configuration C is worth the jumper twice over: it triples the forward path
*and* moves the clock off general routing onto the only global-clock ball the RP
can reach.

The return path stays 1 bit in both cases and **cannot be widened** — GPIO5 is
`CDONE` and GPIO7 has no pad, so GPIO6 has no contiguous neighbour. The link is
inherently asymmetric. That happens to suit the traffic: an accelerator is fed
weights and activations and asked for a much smaller result.

Caveats, so these numbers are not mistaken for measurements:

- 75 MHz is the **PIO ceiling** (2 instructions per bit at a 150 MHz `sys_clk`),
  not a rate anything has run at. Whether the T8 fabric closes timing there, and
  whether the board's signal integrity holds, is exactly what M2 measures.
- Configuration C's clock crosses a jumper while its data does not, so clock and
  data see different flight times. A few cm of wire is ~0.25 ns against a 13 ns
  period, so this should be immaterial — but it is an assumption, not a fact.
- The 18 FPGA header pins are still fully available for a **camera**; using
  PIN17 for the link clock costs one of them.
