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

add_executable(forgix_m2
    main.c
    fpga_config.c
    link_test.c
    ${CMAKE_CURRENT_BINARY_DIR}/bitstream.c)

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
# products are Efinix tool output and this repo does not redistribute them. So
# this target is the one thing here a fresh clone cannot make: ninja fails on the
# missing DEPENDS rather than skipping it, which is worth knowing before you
# type `ninja` with no target.
set(DIAG_IMAGES
    vendor:${CMAKE_CURRENT_LIST_DIR}/../vendor/plasm_led/plasm_led.hex
    probe_a:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_a.hex
    probe_b:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_b.hex
    probe_c:${CMAKE_CURRENT_LIST_DIR}/../rtl/build/probe_c.hex)

set(DIAG_SOURCES "")
foreach(entry IN LISTS DIAG_IMAGES)
    string(REGEX MATCH "^[^:]+" sym ${entry})
    string(REGEX REPLACE "^[^:]+:" "" path ${entry})
    add_custom_command(
        OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c
        COMMAND uv run ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py
                ${path} ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c --symbol ${sym}
        DEPENDS ${CMAKE_CURRENT_LIST_DIR}/../tools/hex2c.py ${path}
        COMMENT "Embedding ${sym} <- ${path}"
        VERBATIM)
    list(APPEND DIAG_SOURCES ${CMAKE_CURRENT_BINARY_DIR}/${sym}.c)
endforeach()

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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

    target_include_directories(forgix_m7 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    target_compile_definitions(forgix_m7 PRIVATE GW_PICO=1)
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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

    target_include_directories(forgix_m9 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
    target_compile_definitions(forgix_m9 PRIVATE GW_PICO=1)
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
        ${CMAKE_CURRENT_BINARY_DIR}/blobs.S)

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
add_executable(forgix_psram_probe psram_probe.c)
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
    ${DIAG_SOURCES})

target_compile_definitions(forgix_diag PRIVATE LINK_CFG=${LINK_CFG_VALUE})
target_include_directories(forgix_diag PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(forgix_diag pico_stdlib hardware_spi hardware_clocks)
pico_enable_stdio_usb(forgix_diag 1)
pico_enable_stdio_uart(forgix_diag 0)
pico_add_extra_outputs(forgix_diag)
