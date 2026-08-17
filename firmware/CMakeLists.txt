cmake_minimum_required(VERSION 3.13)

set(PICO_PLATFORM rp2350-arm-s)
set(PICO_BOARD forgix)
list(APPEND PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/boards)

include(pico_sdk_import.cmake)
project(forgix_m2 C CXX ASM)
pico_sdk_init()

# Which board configuration to build for. NARROW needs no modification; WIDE
# needs one jumper from header pad PIN2 to pad PIN17. See docs/pinmap.md.
set(LINK_CFG "NARROW" CACHE STRING "NARROW or WIDE")
if(LINK_CFG STREQUAL "WIDE")
    set(LINK_CFG_VALUE 3)
    set(BITSTREAM_DEFAULT ${CMAKE_CURRENT_LIST_DIR}/../rtl/build/link_wide.hex)
else()
    set(LINK_CFG_VALUE 1)
    set(BITSTREAM_DEFAULT ${CMAKE_CURRENT_LIST_DIR}/../rtl/build/link_narrow.hex)
endif()

# The bitstream is compiled in rather than streamed over USB. M2 rebuilds
# firmware far less often than it reruns the sweep, and one fewer protocol
# between the host and the measurement is one fewer thing to debug.
set(BITSTREAM ${BITSTREAM_DEFAULT} CACHE FILEPATH "Efinity .hex or .bin to embed")

# M16. Must match whichever bitstream actually reaches the FPGA, which for the
# m7.py path is chosen at run time rather than here - see gemm_plan.h for why
# only one of the two mismatches hangs the board. Default 1, matching the
# KPACK=1 tops that build.sh now ships; set 0 to drive an M14- or M15-era .hex.
set(GP_KPACK "1" CACHE STRING "tile pairs kernel taps on kx: 1 or 0")
add_compile_definitions(GP_KPACK=${GP_KPACK})

# The m9 appliance's operating point; link_clk is half of it. 320000 was chosen
# on m9's own path rather than inherited from m6 and m7: the 2026-08-15 sweep
# ran 280, 300, 320, 332 and 340 on the appliance, and 320 is where the useful
# part of that curve ends. 332 measures the same 420 ms and sits one step from
# 340, where the link stops answering entirely.
#
# THAT SWEEP WAS RE-RUN (#13), AND 332 IS REAL - AND STILL NOT SHIPPED. 332
# tied 320 only because both sat in one cell of the sensor's ~34 ms grid, and
# #10 took the sensor out of the frame. Four rates, 150 frames each, one build
# per rate differing in this variable alone, by the board's own clock:
#
#   280/140  encode 303   frame 331   shutter to LED 414
#   300/150         283         310                  403
#   320/160         265         291                  377   <- ships
#   332/166         256         281                  379
#
# encode x f is 84,840 / 84,900 / 84,800 / 84,992 - 1/f to within 0.2%, and the
# whole frame now tracks it too, with ~10 ms of LED-and-CDC that does not scale.
# 332 is worth 10 ms and it is bit-exact: m7 passes all six modes of both link
# configurations at 332/166, and m9 ran 151 of 151 good.
#
# It is still not what ships, and that is a margin decision rather than a
# measurement. 332 buys 3.4% and sits 8 MHz below 340, where the link stops
# answering entirely and deterministically. #9 (the board drops off USB) and
# #12 (a byte lost on the camera bus at 280/140 and never at 150/75) are both
# open and both are unexplained flakiness on the fast side; one 320 run in the
# #13 session dropped off USB as well. 10 ms is not worth spending the margin
# those two issues have not finished asking about.
#
# This is a cache variable rather
# than an edit to m9.c so that the 150 MHz control can be built without touching
# source - the two images have to differ in exactly one thing to be worth
# comparing. m6 and m7 sweep the rate themselves and ignore this.
set(FGX_SYS_KHZ "320000" CACHE STRING "m9 system clock in kHz; link_clk is half")
add_compile_definitions(FGX_SYS_KHZ=${FGX_SYS_KHZ})

# And the rail that operating point runs on, in millivolts. 0 means "derive it
# from the rate", which is what ships and what m9.c's ladder does. A number
# overrides that ladder, and exists for one question: the ladder's thresholds
# were set generously so the MCU would not become the confound in a measurement
# about the Trion, and nobody had measured what the appliance actually needs.
# Answering that takes two builds differing in the rail alone - the same
# argument FGX_SYS_KHZ is a cache variable for. Values: 1100 1150 1200 1250
# 1300, and m9.c rejects anything else at compile time rather than picking a
# neighbour. There is no way to go above 1300: vreg_disable_voltage_limit() is
# never called here.
set(FGX_CORE_MV "0" CACHE STRING "m9 core rail in mV; 0 derives it from the rate")
add_compile_definitions(FGX_CORE_MV=${FGX_CORE_MV})

# Depend on the bitstream only once it exists. Efinity cannot be installed
# unattended, so "no bitstream yet" is the normal state for this tree and the
# firmware still has to build: hex2c.py emits an empty placeholder and main.c
# refuses to run the sweep. Listing a missing file in DEPENDS would instead stop
# ninja before it compiled a single line.
set(BITSTREAM_DEPS ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py)
if(EXISTS ${BITSTREAM})
    list(APPEND BITSTREAM_DEPS ${BITSTREAM})
else()
    message(WARNING "Bitstream ${BITSTREAM} not found - embedding a placeholder. "
                    "Run Efinity, then re-run cmake.")
endif()

add_custom_command(
    OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/bitstream.c
    COMMAND uv run ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py
            ${BITSTREAM} ${CMAKE_CURRENT_BINARY_DIR}/bitstream.c
    DEPENDS ${BITSTREAM_DEPS}
    COMMENT "Embedding bitstream ${BITSTREAM}"
    VERBATIM)

# EVERY TARGET BELOW LISTS qspi_park.c, AND A NEW ONE MUST TOO (#17).
#
# It parks GPIO0, U1's chip select, whose pad reset state otherwise holds the
# PSRAM selected on the same QSPI bus as the flash for the whole run - which is
# #9/#16, cost four bench runs and an afternoon, and looks like corrupt flash
# that verifies fine after a power cycle. The file registers itself through the
# SDK's preinit array, so listing it here is the entire integration: there is no
# call to add to main() and nothing to remember at run time.
#
# It is listed even on forgix_m5 and forgix_psram_probe, which link
# hardware_psram and actually want the part. That is safe and deliberate:
# runtime_init_setup_psram() sits at priority "11080" against this file's
# "00601", so the PSRAM's own init runs later and takes the pin back. Listing it
# unconditionally is what makes "did this target get the fix?" a question nobody
# has to ask.
add_executable(forgix_m2
    main.c
    fpga_config.c
    link_test.c
    ${CMAKE_CURRENT_BINARY_DIR}/bitstream.c
    qspi_park.c)

target_compile_definitions(forgix_m2 PRIVATE LINK_CFG=${LINK_CFG_VALUE})
target_include_directories(forgix_m2 PRIVATE ${CMAKE_CURRENT_LIST_DIR})

pico_generate_pio_header(forgix_m2 ${CMAKE_CURRENT_LIST_DIR}/link.pio)

target_link_libraries(forgix_m2
    pico_stdlib
    hardware_spi
    hardware_pio
    hardware_dma
    hardware_clocks)

pico_enable_stdio_usb(forgix_m2 1)
pico_enable_stdio_uart(forgix_m2 0)
pico_add_extra_outputs(forgix_m2)

# --- forgix_diag -----------------------------------------------------------
# Links the vendor's known-good plasm_led alongside ours so one boot can tell
# a broken fpga_config.c from a broken bitstream. Reflashing this board needs a
# physical PRG-GND strap, so the diagnostic is built to need only one.
#
# The rungs, in the order diag.c walks them. Each adds one more reused
# configuration pin, so the first one that fails names the pin that breaks
# configuration - and if even `vendor` or `probe_a` fails, the fault is not a
# pin assignment at all.
#
# None of the four are committed. Three are rtl/build/ outputs, and plasm_led.hex
# is the vendor's, from the developer repo linked in the README - Efinity build
# products are Efinix tool output and this repo does not redistribute them.
#
# So each one gets the same treatment the main bitstream gets above (#5): a path
# that does not exist is left out of DEPENDS, hex2c.py embeds an empty array, and
# diag.c prints "skipped (image missing from the build)" for that rung. Listing a
# missing file in DEPENDS instead stops ninja before it compiles a line - which
# made `ninja` with no target fail on every clean checkout, and made the one tool
# for the problem that has cost the most bench time the one tool nobody could
# build. A diagnostic with three of four rungs is worth more than a build error.
set(DIAG_IMAGES
    vendor:${CMAKE_CURRENT_LIST_DIR}/../vendor/plasm_led/plasm_led.hex
    probe_a:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_a.hex
    probe_b:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_b.hex
    probe_c:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_c.hex)

set(DIAG_SOURCES "")
set(DIAG_MISSING "")
foreach(entry IN LISTS DIAG_IMAGES)
    string(REGEX MATCH "^[^:]+" sym ${entry})
    string(REGEX REPLACE "^[^:]+:" "" path ${entry})
    set(deps ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py)
    if(EXISTS ${path})
        list(APPEND deps ${path})
    else()
        list(APPEND DIAG_MISSING ${sym})
    endif()
    add_custom_command(
        OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c
        COMMAND uv run ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py
                ${path} ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c --symbol ${sym}
        DEPENDS ${deps}
        COMMENT "Embedding ${sym} <- ${path}"
        VERBATIM)
    list(APPEND DIAG_SOURCES ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c)
endforeach()

# Said once, at configure time, because a ladder quietly missing its control rung
# is a diagnostic that answers a different question than the one it was asked.
if(DIAG_MISSING)
    string(REPLACE ";" " " diag_missing_str "${DIAG_MISSING}")
    message(STATUS "forgix_diag: no image for ${diag_missing_str} - "
                   "those rungs will be skipped at run time. rtl/build.sh makes "
                   "the probe_* ones; plasm_led.hex is the vendor's.")
endif()

# --- forgix_m5 -------------------------------------------------------------
# M5: the int8 student encoder, timed on the MCU. Weights and golden vectors are
# linked in from model/export.py's output rather than streamed, so the
# measurement needs one PRG strap and no working host protocol.
# so400m-full-a05 since M18: distilled from SigLIP 2 SO400M through a frozen
# PCA-512, which is the teacher that passes gate 2. The incumbent train2017
# export is still on disk and reverting is one -DFGX_EXPORT.
#
# This is a CACHE variable, so editing the line above does nothing to a build
# tree that already configured - firmware/build/CMakeCache.txt keeps whatever it
# was first given, and ninja will happily go on linking the old model. Pass
# -DFGX_EXPORT=... to move an existing tree.
set(FGX_EXPORT ${CMAKE_CURRENT_LIST_DIR}/../model/runs/so400m-full-a05/export
    CACHE PATH "directory holding weights.bin and testvec.bin")
set(FGX_WEIGHTS ${FGX_EXPORT}/weights.bin)
set(FGX_TESTVEC ${FGX_EXPORT}/testvec.bin)

# This export is committed - it is the one exception to the model/runs/ ignore,
# see .gitignore - so the guard below normally passes. It stays because
# -DFGX_EXPORT can point anywhere, and a typo should skip the target rather than
# fail configure, the same reason the bitstream is optional.
if(EXISTS ${FGX_WEIGHTS} AND EXISTS ${FGX_TESTVEC})
    configure_file(${CMAKE_CURRENT_LIST_DIR}/blobs.S.in
                   ${CMAKE_CURRENT_BINARY_DIR}/blobs.S @ONLY)
    set_source_files_properties(${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        PROPERTIES OBJECT_DEPENDS "${FGX_WEIGHTS};${FGX_TESTVEC}")

    add_executable(forgix_m5
        m5.c
        encoder.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m5 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    # Creates the PSRAM linker region. Only this target needs it, and without it
    # the region is zero-length and __uninitialized_psram will not link.
    pico_override_psram_size(forgix_m5 2097152)
    # Detect the size even though the board header declares it: this is the
    # first firmware to touch U1, and "the chip answered" is worth more than
    # "the header says 2 MB".
    target_compile_definitions(forgix_m5 PRIVATE PICO_AUTO_DETECT_PSRAM_SIZE=1)
    target_link_libraries(forgix_m5 pico_stdlib hardware_psram hardware_clocks)
    pico_enable_stdio_usb(forgix_m5 1)
    pico_enable_stdio_uart(forgix_m5 0)
    pico_add_extra_outputs(forgix_m5)

    # M5b: the same encoder tuned as im2col + a blocked GEMM with an SMLAD inner
    # loop, timed against encoder.c in the same boot. Both kernels are linked in
    # - the point of the target is the ratio between them, and one PRG strap has
    # to return it. No hardware_psram: U1 does not enumerate and m5b.c runs from
    # flash XIP only.
    add_executable(forgix_m5b
        m5b.c
        encoder.c
        encoder_fast.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m5b PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    target_link_libraries(forgix_m5b pico_stdlib hardware_clocks)
    pico_enable_stdio_usb(forgix_m5b 1)
    pico_enable_stdio_uart(forgix_m5b 0)
    pico_add_extra_outputs(forgix_m5b)

    # M6c: the GEMM tile on the T8, checked against encoder.c on the same boot.
    # Inside this branch because the golden values come from weights.bin - the
    # whole point of the harness is that nothing but the bitstream crosses USB,
    # and without blobs.S there is nothing to be exact against.
    #
    # No bitstream.c here, unlike forgix_m2 and forgix_diag. m6.c receives the
    # image over USB CDC into SRAM, so the firmware does not have to be rebuilt
    # - or the board re-strapped - for a new RTL revision.
    #
    # gemm_block.c is shared with firmware/gen_gemm_vec.c, which builds the
    # vectors rtl/tb_gemm and rtl/tb_gemm_link check the RTL against. One copy of
    # the strip and weight layout, exercised by both the simulator and the board.
    #
    # gemm_wire.c is the same idea applied to the driver: the framing, CRC and
    # response decode are a function of a byte buffer and nothing else, so
    # firmware/test_gemm_wire.c checks them on the host, at every bit offset,
    # against the M6 implementation - before anything costs a strap.
    add_executable(forgix_m6
        m6.c
        gemm_host.c
        gemm_wire.c
        gemm_block.c
        encoder.c
        fpga_config.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m6 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    # Lets gemm_wire.c put its hot path in SRAM without needing a Pico header
    # in the host build. M5b measured 21% from this alone.
    target_compile_definitions(forgix_m6 PRIVATE GW_PICO=1)
    pico_generate_pio_header(forgix_m6 ${CMAKE_CURRENT_LIST_DIR}/link.pio)
    target_link_libraries(forgix_m6
        pico_stdlib
        hardware_spi
        hardware_pio
        hardware_dma
        hardware_clocks)
    pico_enable_stdio_usb(forgix_m6 1)
    pico_enable_stdio_uart(forgix_m6 0)
    pico_add_extra_outputs(forgix_m6)

    # M7c: the same tile, but a whole frame of it - 174 blocks across eight
    # layers, sequenced from the descriptors rather than from a hand-written
    # table. forgix_m6 stays exactly as it is; a finished milestone that still
    # builds and still passes is worth more than one fewer target.
    #
    # Two additions over forgix_m6, and both earn their place. gemm_plan.c
    # chooses the blocking, and firmware/test_gemm_plan.c has already settled
    # every row of that choice on the host - including that the blocks tile each
    # output tensor exactly once, which no amount of per-block bit-exactness can
    # establish. encoder_fast.c supplies the reference frame: byte-identical to
    # encoder.c by test_encoder_fast.c, and 7.4x faster, so the on-boot reference
    # pass costs 3.4 s instead of 31.8 s.
    #
    # M8b adds cam.c: the frame comes off the camera when one is attached and
    # falls back to the flash vector when none is. Two PIO programs in one
    # binary, on two different PIO instances - link.pio on pio0, cam_spi.pio on
    # pio1 - which is why both headers are generated here.
    #
    # M8c moves the engine into frame.c, which m7 and m8 both link. m7 stays the
    # measuring harness and the regression test for the move; m8 is the loop.
    add_executable(forgix_m7
        m7.c
        frame.c
        worker.c
        cam.c
        gemm_host.c
        gemm_wire.c
        gemm_block.c
        gemm_plan.c
        encoder.c
        encoder_fast.c
        fpga_config.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m7 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    # The stdout timeout for the same reason forgix_m9 pins it - see the block
    # there. m7 publishes ms/frame too, so a printf that can stand still for half
    # a second behind a slow reader is the same hazard to the same numbers.
    target_compile_definitions(forgix_m7 PRIVATE GW_PICO=1
        PICO_STDIO_USB_STDOUT_TIMEOUT_US=50000)
    pico_generate_pio_header(forgix_m7 ${CMAKE_CURRENT_LIST_DIR}/link.pio)
    pico_generate_pio_header(forgix_m7 ${CMAKE_CURRENT_LIST_DIR}/cam_spi.pio)
    target_link_libraries(forgix_m7
        pico_stdlib
        pico_multicore     # M7e. The first target in this project to link it.
        hardware_spi
        hardware_pio
        hardware_dma
        hardware_clocks)
    pico_enable_stdio_usb(forgix_m7 1)
    pico_enable_stdio_uart(forgix_m7 0)
    pico_add_extra_outputs(forgix_m7)

    # M8c: the same engine, in a loop. Identical sources to forgix_m7 with m8.c
    # for m7.c - which is the property the frame.c split exists to have, and the
    # reason the two targets are written next to each other rather than in a
    # function: if these two lists ever stop matching, something moved back into
    # a harness that should have stayed in the engine.
    add_executable(forgix_m8
        m8.c
        frame.c
        worker.c
        cam.c
        gemm_host.c
        gemm_wire.c
        gemm_block.c
        gemm_plan.c
        encoder.c
        encoder_fast.c
        fpga_config.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m8 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    target_compile_definitions(forgix_m8 PRIVATE GW_PICO=1)
    pico_generate_pio_header(forgix_m8 ${CMAKE_CURRENT_LIST_DIR}/link.pio)
    pico_generate_pio_header(forgix_m8 ${CMAKE_CURRENT_LIST_DIR}/cam_spi.pio)
    target_link_libraries(forgix_m8
        pico_stdlib
        pico_multicore
        hardware_spi
        hardware_pio
        hardware_dma
        hardware_clocks)
    pico_enable_stdio_usb(forgix_m8 1)
    pico_enable_stdio_uart(forgix_m8 0)
    pico_add_extra_outputs(forgix_m8)

    # M9: the same loop again, with the cosine pointed at a text vector instead
    # of at the previous frame. Third copy of the same source list, and the same
    # reason as above: m9.c is a *caller* of the engine, so if this list ever
    # needs a file the other two do not, the demo has grown machinery that
    # belongs in frame.c.
    add_executable(forgix_m9
        m9.c
        frame.c
        worker.c
        cam.c
        gemm_host.c
        gemm_wire.c
        gemm_block.c
        gemm_plan.c
        encoder.c
        encoder_fast.c
        fpga_config.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_m9 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    # PICO_STDIO_USB_STDOUT_TIMEOUT_US IS PINNED, NOT INHERITED (#9).
    #
    # Issue #9 asks whether a board that is off the bus and still looping is
    # writing into a pipe that drops or a pipe that blocks - i.e. whether the
    # disappearance is cosmetic or a hang waiting to happen. The answer is in the
    # SDK: stdio_usb_out_chars() leaves its loop the instant
    # stdio_usb_connected() goes false, so an unmounted board prints at full
    # speed into nothing and this timeout never applies. It applies to the other
    # case only - a host that stays attached and stops reading - where the SDK
    # default of 500,000 us means one printf can stand still for half a second.
    # A frame is ~290 ms, so that default is long enough to distort the only
    # numbers this binary exists to produce.
    #
    # 50 ms instead. It is ~600x what a 90-byte frame line needs at 12 Mbit/s
    # with macOS polling every millisecond, so a healthy run never reaches it,
    # and it bounds the damage a stalled reader can do to one sixth of a frame.
    # What it costs when it does fire is the rest of that one write - a truncated
    # line in the log - which is the correct trade against a frame time that
    # silently became a measurement of the host.
    target_compile_definitions(forgix_m9 PRIVATE GW_PICO=1
        PICO_STDIO_USB_STDOUT_TIMEOUT_US=50000)
    pico_generate_pio_header(forgix_m9 ${CMAKE_CURRENT_LIST_DIR}/link.pio)
    pico_generate_pio_header(forgix_m9 ${CMAKE_CURRENT_LIST_DIR}/cam_spi.pio)
    target_link_libraries(forgix_m9
        pico_stdlib
        pico_multicore
        hardware_spi
        hardware_pio
        hardware_dma
        hardware_clocks)
    pico_enable_stdio_usb(forgix_m9 1)
    pico_enable_stdio_uart(forgix_m9 0)
    pico_add_extra_outputs(forgix_m9)

    # M8a: the camera, on its own, before anything downstream depends on it.
    # Inside this guard and linking encoder.c for one field - the blob's
    # in_scale. The probe quantizes a captured frame the way fgx_run() will eat
    # it, and hard-coding that constant would be a copy of a number export.py
    # owns, in the one binary whose entire job is to catch exactly that class of
    # mismatch.
    add_executable(forgix_cam_probe
        cam_probe.c
        cam.c
        encoder.c
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S
        qspi_park.c)

    target_include_directories(forgix_cam_probe PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    pico_generate_pio_header(forgix_cam_probe ${CMAKE_CURRENT_LIST_DIR}/cam_spi.pio)
    target_link_libraries(forgix_cam_probe
        pico_stdlib hardware_pio hardware_clocks)
    pico_enable_stdio_usb(forgix_cam_probe 1)
    pico_enable_stdio_uart(forgix_cam_probe 0)
    pico_add_extra_outputs(forgix_cam_probe)
else()
    message(WARNING "No export at ${FGX_EXPORT} - skipping forgix_m5. "
                    "Run: uv run model/export.py --run train2017")
endif()

# M5c: dump the raw READ ID bytes from U1. Standalone rather than folded into
# m5.c because it is meant to be flashed, read, and thrown away - and because
# keeping it small means it can ride along with any other strap.
add_executable(forgix_psram_probe psram_probe.c qspi_park.c)
target_include_directories(forgix_psram_probe PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_compile_definitions(forgix_psram_probe PRIVATE PICO_AUTO_DETECT_PSRAM_SIZE=1)
target_link_libraries(forgix_psram_probe
    pico_stdlib hardware_psram hardware_flash hardware_clocks)
pico_enable_stdio_usb(forgix_psram_probe 1)
pico_enable_stdio_uart(forgix_psram_probe 0)
pico_add_extra_outputs(forgix_psram_probe)

add_executable(forgix_diag
    diag.c
    fpga_config.c
    ${CMAKE_CURRENT_BINARY_DIR}/bitstream.c
    ${DIAG_SOURCES}
    qspi_park.c)

target_compile_definitions(forgix_diag PRIVATE LINK_CFG=${LINK_CFG_VALUE})
target_include_directories(forgix_diag PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(forgix_diag pico_stdlib hardware_spi hardware_clocks)
pico_enable_stdio_usb(forgix_diag 1)
pico_enable_stdio_uart(forgix_diag 0)
pico_add_extra_outputs(forgix_diag)
