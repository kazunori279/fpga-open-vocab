// Host test for cam_pixel.h. No Pico, no board, no camera.
//
//   cc -O2 -Wall -Wextra -Ifirmware -o /tmp/test_cam_pixel \
//      firmware/test_cam_pixel.c firmware/encoder.c -lm
//   /tmp/test_cam_pixel > /tmp/cam_pixel.csv
//   uv run host/cam.py --selftest /tmp/cam_pixel.csv
//
//   /tmp/test_cam_pixel --frame > /tmp/cam_synth.log
//   uv run host/cam.py /tmp/cam_synth.log --weights <blob>
//
// The point of splitting it that way. host/cam.py recomputes the device's int8
// tensor in numpy and compares CRC32s, and a CRC comparison is only evidence if
// the two sides arrived at their answer independently. They do not: I wrote
// both, from the same three lines of model/export.py, on the same afternoon.
// The way two independent-looking implementations of the same formula agree is
// that they contain the same mistake.
//
// So this emits the *device's* answer - the real C, compiled from the real
// header, through the real fgx_rint() - as a table, and host/cam.py checks its
// numpy against that table rather than against my memory of the formula. If
// they disagree the board has not been touched yet, which is the whole point:
// a wrong preprocessor is much cheaper to find here than in a picture that
// looks slightly wrong.
//
// The table is 256 x 3 rows and not 65,536: cam_code() is a function of one
// byte and one scale, and cam_rgb565_expand() is a function of 16 bits with no
// arithmetic in it at all, so the two are checked separately. Enumerating all
// 65,536 pixel values would test the same 256 conversions 256 times each.
//
// Scales: three, spanning what export.py plausibly produces. in_scale is a
// per-run calibration output, so pinning the table to today's value would make
// this test pass for the wrong reason after the next re-export.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cam_dump.h"
#include "cam_pixel.h"

static const float SCALES[] = { 0.0078125f, 0.011f, 0.02f };

// `--frame`: emit exactly what cam_probe.c emits, for a picture whose contents
// are known. Same cam_dump_frame(), same cam_frame_to_chw(), same CRC - so
// host/cam.py can be run end to end, parser and PNG writer and all, on a laptop
// with no camera attached.
//
// It is worth being clear about what this does and does not prove. It cannot
// tell you the camera works. What it rules out is the whole tail of stupid
// reasons a real capture might look broken: a base64 line-length bug, a CRC
// computed over the wrong range, a PNG with its rows transposed, a regex that
// silently matches nothing. Those all present, on the bench, as "the camera is
// returning garbage" - and the difference between a parser bug and a wiring
// fault is an hour with the wrong tool.
//
// The picture is a diagonal gradient with a bright corner, so a rotation, a
// flip, a transpose and a channel swap are all distinguishable by eye in the
// output PNG. A flat colour or a checkerboard would not be.
static int emit_frame(void)
{
    enum { W = 64, H = 48 };
    static uint8_t raw[W * H * 2];
    static int8_t  chw[3 * W * H];

    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            uint16_t r = (uint16_t)(x * 31 / (W - 1));
            uint16_t g = (uint16_t)(y * 63 / (H - 1));
            uint16_t b = (x < 8 && y < 8) ? 31 : 0;   // bright corner marker
            uint16_t px = (uint16_t)((r << 11) | (g << 5) | b);
            raw[2 * (y * W + x)]     = (uint8_t)(px >> 8);   // high byte first
            raw[2 * (y * W + x) + 1] = (uint8_t)(px & 0xff);
        }
    }

    printf("=== synthetic capture, not a camera ===\n");
    printf("in_scale  : %.9g\n", (double)SCALES[0]);
    printf("\n-- f128: %dx%d RGB565 --\n", W, H);
    cam_dump_frame("f128", raw, sizeof raw, W, H);
    for (int hi = 0; hi < 2; hi++) {
        cam_frame_to_chw(raw, W, H, hi != 0, SCALES[0], chw);
        printf("  chw crc32 : %08x   (%s byte first)\n",
               (unsigned)cam_crc32((const uint8_t *)chw, sizeof chw),
               hi ? "high" : "low");
    }
    fprintf(stderr, "emitted a %dx%d synthetic frame at in_scale %.9g\n",
            W, H, (double)SCALES[0]);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--frame") == 0)
        return emit_frame();

    // Part 1: the 5/6/5 expansion, as a function of the three subfields. Every
    // r5/b5 and every g6, with the endpoints called out because that is the
    // property that chose bit replication over a divide.
    printf("# expand5,code,out\n");
    for (int i = 0; i < 32; i++) {
        uint8_t v[3];
        cam_rgb565_expand((uint16_t)(i << 11), v);   // red field only
        printf("expand5,%d,%u\n", i, v[0]);
    }
    printf("# expand6,code,out\n");
    for (int i = 0; i < 64; i++) {
        uint8_t v[3];
        cam_rgb565_expand((uint16_t)(i << 5), v);    // green field only
        printf("expand6,%d,%u\n", i, v[1]);
    }

    // Part 2: the quantizer, every byte value at every scale.
    printf("# code,in_scale,v,q\n");
    for (size_t s = 0; s < sizeof SCALES / sizeof SCALES[0]; s++)
        for (int v = 0; v < 256; v++)
            printf("code,%.9g,%d,%d\n",
                   (double)SCALES[s], v, (int)cam_code((uint8_t)v, SCALES[s]));

    // Part 3: the byte-order and CHW-layout plumbing around them. A 4x2 frame
    // of known bytes, both orders, printed as the flat tensor - this is what
    // catches a transposed plane or an off-by-one row stride, which the
    // per-byte tables above cannot see.
    const uint8_t src[] = {
        0x00, 0x00,  0xff, 0xff,  0xf8, 0x00,  0x07, 0xe0,
        0x00, 0x1f,  0x12, 0x34,  0xab, 0xcd,  0x80, 0x01,
    };
    int8_t dst[3 * 8];
    printf("# layout,hi_first,index,q\n");
    for (int hi = 0; hi < 2; hi++) {
        cam_frame_to_chw(src, 4, 2, hi != 0, SCALES[0], dst);
        for (int i = 0; i < 3 * 8; i++)
            printf("layout,%d,%d,%d\n", hi, i, dst[i]);
    }

    fprintf(stderr, "wrote the device-side table; check it with host/cam.py "
                    "--selftest\n");
    return 0;
}
