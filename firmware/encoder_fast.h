// M5b: the same encoder as encoder.c, restructured as im2col + a blocked int8
// GEMM, with a Cortex-M33 DSP inner loop.
//
// **encoder.c stays the contract.** This file is an optimization and is only
// ever as good as `fgx_conv_fast()` being byte-for-byte equal to
// `fgx_conv_ref()` - which is checkable, per layer, on the host, and is what
// test_encoder_fast.c does. If the two ever disagree, encoder.c is right.
//
// Why it exists: M5 measured 31,798 ms/frame, 30 cycles/MAC. That is the cost
// of a loop that re-tests a flag and bounds-checks two axes on every tap, not
// the cost of the arithmetic. Quoting it as "the MCU baseline" would make the
// T8 look like a 140x win when the honest figure is 8-10x, and M6 has to be
// justified against the honest one. Measured: 3,358 ms/frame, 3.17 cycles/MAC,
// 7.4x over the same-boot reference, and bit-exact with it.
//
// It is also the M6 rehearsal. The im2col + tiling decomposition here is the
// one the FPGA has to implement in RTL; doing it in C first is how the data
// layout gets debugged somewhere with a debugger.

#ifndef ENCODER_FAST_H
#define ENCODER_FAST_H

#include "encoder.h"

// Output pixels per im2col tile. Full im2col is not an option - conv2 alone
// would want 64*9*1024 = 576 KB against 520 KB of SRAM - so the column buffer
// covers a block of output positions and is refilled per block.
//
// 32 rather than 16 because the weight matrix is re-swept from flash XIP once
// per tile: at 32 that is ~5.2 MB per frame, at 16 it doubles. The cost of
// going the other way is 55 KB of SRAM, which is affordable today. Dial it
// back here if a wider first stage ever needs the room.
#define FGX_TILE 32

// Bytes for the im2col column buffer, 4-byte aligned, worst case over layers.
size_t fgx_fast_col_bytes(const fgx_model_t *m);

// Whether this build has the DSP inner loop compiled in at all. False on the
// host (aarch64 does not define __ARM_FEATURE_DSP), so `use_dsp` below is
// silently ignored there and the portable path is what the host test proves.
bool   fgx_fast_have_dsp(void);

// One convolution. Same signature as fgx_conv_ref() plus the column buffer and
// the path selector, so the two can be swapped in a test loop.
//
// `use_dsp` is a runtime flag rather than a build flag deliberately. Reflashing
// this board costs a physical PRG-GND strap, and one strap has to return both
// rows: if the intrinsics turn out to be wrong, the portable-fast row still
// gives a usable tuned baseline and localizes the fault to the DSP loop.
void   fgx_conv_fast(const fgx_model_t *m, const fgx_desc_t *d,
                     const void *in, void *out, bool out_float,
                     void *col, bool use_dsp);

// Drop-in for fgx_run(). Calls fgx_layer_mark() identically, so the per-layer
// breakdown lines up row for row against the reference profile.
void   fgx_run_fast(const fgx_model_t *m, const int8_t *input, float *embed,
                    void *a, void *b, void *col, bool use_dsp);

#endif
