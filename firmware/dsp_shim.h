// Host emulation of the four ACLE intrinsics encoder_fast.c's inner loop uses.
//
// **This exists because reflashing the board costs a physical PRG-GND strap.**
// Without it the SMLAD path would be untestable off-target - macOS aarch64 does
// not define __ARM_FEATURE_DSP - and the first time anyone found out whether
// the tap pairing, the loop bounds or the K%4 tail were right would be on the
// bench, with one shot.
//
// With it, `cc -DFGX_DSP_SHIM` compiles the *same source lines* the M33 will
// run and proves them bit-exact against encoder.c on the laptop. What is left
// for the strap is only "does the silicon match the ARM ARM", which is a much
// smaller question than "is my loop right".
//
// Definitions are transcribed from the Armv8-M Architecture Reference Manual,
// not from what would make the test pass:
//
//   SXTB16 Rd,Rm   Rd[15:0]  = SignExtend(Rm[7:0])
//                  Rd[31:16] = SignExtend(Rm[23:16])
//   UXTB16 Rd,Rm   same, zero-extended
//   SMLAD  Rd,Rn,Rm,Ra
//                  Rd = Ra + SInt(Rn[15:0])*SInt(Rm[15:0])
//                          + SInt(Rn[31:16])*SInt(Rm[31:16])
//
// The types mirror arm_acle.h exactly (int16x2_t is int32_t, uint16x2_t is
// uint32_t) so the call sites need no #ifdef of their own.

#ifndef DSP_SHIM_H
#define DSP_SHIM_H

#include <stdint.h>

typedef int32_t  int16x2_t;
typedef uint32_t uint16x2_t;
typedef int32_t  int8x4_t;
typedef uint32_t uint8x4_t;

static inline uint32_t __ror(uint32_t x, uint32_t n)
{
    n &= 31u;
    return n ? ((x >> n) | (x << (32u - n))) : x;
}

static inline int16x2_t __sxtb16(int8x4_t a)
{
    const uint32_t x = (uint32_t)a;
    const uint32_t lo = (uint32_t)(int32_t)(int8_t)(x & 0xffu) & 0xffffu;
    const uint32_t hi = (uint32_t)(int32_t)(int8_t)((x >> 16) & 0xffu);
    return (int16x2_t)((hi << 16) | lo);
}

static inline uint16x2_t __uxtb16(uint8x4_t a)
{
    const uint32_t x = (uint32_t)a;
    return (uint16x2_t)((((x >> 16) & 0xffu) << 16) | (x & 0xffu));
}

static inline int32_t __smlad(int16x2_t a, int16x2_t b, int32_t acc)
{
    const int32_t a0 = (int16_t)((uint32_t)a & 0xffffu);
    const int32_t a1 = (int16_t)((uint32_t)a >> 16);
    const int32_t b0 = (int16_t)((uint32_t)b & 0xffffu);
    const int32_t b1 = (int16_t)((uint32_t)b >> 16);
    return acc + a0 * b0 + a1 * b1;
}

#endif
