// M8: RGB565 out of the camera -> int8 CHW into fgx_run().
//
// Its own header, free of Pico SDK includes, for the reason encoder.h gives:
// correctness gets settled on the host. This is a pure function of 16 bits and
// one scale, so there is nothing about it that needs a board, and
// test_cam_pixel.c checks it against the numpy in host/cam.py without one.
//
// **It is the last unverified link in the chain.** M5 proved the encoder is
// bit-exact against numpy from the codes inward. Nothing has ever checked the
// codes themselves, because until now they came out of a test vector that the
// host also produced. A camera makes the input real, and every way this file
// can be wrong - byte order, 5/6/5 expansion, channel order, the normalization
// constants - yields a tensor that is perfectly well-formed and not the image.
// None of them raise an error anywhere downstream.
//
// The chain being reproduced, from model/distill.py:44 and model/export.py:310:
//
//     ToTensor    v / 255
//     Normalize   (x - 0.5) / 0.5          PIXEL_MEAN = PIXEL_STD = 0.5
//     quantize    clip(rint(x / in_scale), -127, 127)
//
// which is `clip(rint((v / 127.5 - 1) / in_scale), -127, 127)`. That the first
// two compose to v/127.5-1 exactly in float32 is not obvious and is not assumed:
// host/cam.py checks it for all 256 byte values.

#ifndef CAM_PIXEL_H
#define CAM_PIXEL_H

#include <stdbool.h>
#include <stdint.h>

#include "encoder.h"   // fgx_rint: VCVTR.S32.F32 on the target, lrintf on the host

// 5/6/5 -> 8 by bit replication rather than round(v * 255 / 31).
//
// The two differ by at most one LSB, which sounds ignorable and is not: it is
// an LSB at the *input* of a pipeline whose whole claim is bit-exactness, and
// it would show up as a handful of codes off by one with no way to tell whether
// the camera, the host or the encoder introduced them. Replication wins on the
// merits anyway - exact at both endpoints, 0 -> 0 and 31 -> 255, and no divide -
// but the reason it is written down here is so host/cam.py can make the same
// choice on purpose instead of by coincidence.
static inline void cam_rgb565_expand(uint16_t px, uint8_t out[3])
{
    const uint8_t r5 = (uint8_t)((px >> 11) & 0x1f);
    const uint8_t g6 = (uint8_t)((px >>  5) & 0x3f);
    const uint8_t b5 = (uint8_t)( px        & 0x1f);
    out[0] = (uint8_t)((r5 << 3) | (r5 >> 2));
    out[1] = (uint8_t)((g6 << 2) | (g6 >> 4));
    out[2] = (uint8_t)((b5 << 3) | (b5 >> 2));
}

// One channel's code. float32 throughout: the target has no float64, so a host
// check done in double would pass a device that is wrong by an LSB.
static inline int8_t cam_code(uint8_t v, float in_scale)
{
    const float x = ((float)v / 127.5f - 1.0f) / in_scale;
    int32_t q = fgx_rint(x);
    if (q < -127) q = -127;
    if (q >  127) q =  127;
    return (int8_t)q;
}

// How far counter-clockwise the delivered frame has to be turned to stand up.
// This is a property of how the module is *mounted*, not of the sensor, so it
// lives next to the pixel maths rather than in cam.h: it is the last step of
// "FIFO bytes -> the tensor the model was trained on", and getting it wrong
// costs exactly what a channel swap costs.
//
// WHICH IS TO SAY: A SIDEWAYS FRAME IS NOT A COSMETIC PROBLEM. The student was
// distilled from the teacher on upright photographs, and it is not rotation
// invariant - a turned image is simply a different point in the embedding
// space, one that no text vector was ever fit against. It does not error, it
// does not look broken in any log, and it does not pin the cosine. It just
// moves every score into a narrow band where the ranking is noise, which is
// what M9's first bench run looked like before anyone rendered the picture.
typedef enum {
    CAM_ROT_0   = 0,
    CAM_ROT_90  = 1,   // quarter turn counter-clockwise
    CAM_ROT_180 = 2,
    CAM_ROT_270 = 3,   // quarter turn clockwise
} cam_rot_t;

// Source pixel index for destination (ox, oy) under `rot`. Square only for the
// quarter turns, which is not a limitation worth removing: the encoder's input
// is square by construction and a non-square rotate would also have to swap the
// caller's w and h, i.e. it would be a different function with a different
// contract pretending to be this one.
static inline uint32_t cam_rot_src(cam_rot_t rot, uint32_t w, uint32_t h,
                                   uint32_t ox, uint32_t oy)
{
    switch (rot) {
    case CAM_ROT_90:  return ox * w + (w - 1u - oy);
    case CAM_ROT_180: return (h - 1u - oy) * w + (w - 1u - ox);
    case CAM_ROT_270: return (h - 1u - ox) * w + oy;
    default:          return oy * w + ox;
    }
}

// A whole frame. `src` is w*h*2 FIFO bytes, `dst` is 3*w*h int8 in CHW order.
//
// `hi_first` is the one thing this file cannot settle by reasoning: which of the
// two FIFO bytes carries RRRRRGGG. The ArduChip's datasheet does not say. Both
// are computed on the board and the answer comes from looking at the rendered
// PNG, which is why cam_probe.c dumps the raw bytes rather than the tensor.
static inline void cam_frame_to_chw_rot(const uint8_t *src, uint32_t w,
                                        uint32_t h, bool hi_first,
                                        cam_rot_t rot, float in_scale,
                                        int8_t *dst)
{
    const uint32_t plane = w * h;
    for (uint32_t oy = 0; oy < h; oy++) {
        for (uint32_t ox = 0; ox < w; ox++) {
            const uint32_t i = cam_rot_src(rot, w, h, ox, oy);
            const uint16_t px = hi_first
                ? (uint16_t)(((uint16_t)src[2 * i] << 8) | src[2 * i + 1])
                : (uint16_t)(((uint16_t)src[2 * i + 1] << 8) | src[2 * i]);
            uint8_t v[3];
            cam_rgb565_expand(px, v);
            const uint32_t j = oy * w + ox;
            for (int c = 0; c < 3; c++)
                dst[c * plane + j] = cam_code(v[c], in_scale);
        }
    }
}

// The unrotated case, kept as its own name because cam_probe.c and
// test_cam_pixel.c are asking about the pixel maths and have no opinion about
// where the module points.
static inline void cam_frame_to_chw(const uint8_t *src, uint32_t w, uint32_t h,
                                    bool hi_first, float in_scale, int8_t *dst)
{
    cam_frame_to_chw_rot(src, w, h, hi_first, CAM_ROT_0, in_scale, dst);
}

#endif
