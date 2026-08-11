// M8: the frame-dump wire format, as one definition.
//
// Its own header for the same reason cam_pixel.h is: so the host side can be
// tested against the real emitter rather than against a re-typing of it. The
// alternative is a format that lives in cam_probe.c and a parser in
// host/cam.py that agree until the day one of them is edited, and the failure
// mode of a mismatched dump format is a garbled frame that looks exactly like a
// camera fault. Debugging that on the bench, with a soldering iron in reach, is
// how a wiring problem gets invented to explain a parser bug.
//
// printf-only, no Pico SDK, so test_cam_pixel.c can emit a synthetic capture on
// the laptop and host/cam.py can be run over it before the board is touched.
//
// Base64 rather than hex: 1.33 bytes per byte against 2, and the difference on
// a 150 KB QVGA frame is 50 KB of terminal. Framed with literal BEGIN/END
// markers rather than a length-prefixed binary block because the dump shares a
// CDC endpoint with the probe's own report - the frames have to survive being
// interleaved with printf, and a parser that can be pointed at a whole session
// log and pick out however many frames it contains is what makes the fallback
// captures worth taking in the same boot.

#ifndef CAM_DUMP_H
#define CAM_DUMP_H

#include <stdint.h>
#include <stdio.h>
#include <stddef.h>

// zlib's, byte for byte m7.c's, which is what host/cam.py's binascii.crc32
// computes.
static inline uint32_t cam_crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
    }
    return ~c;
}

static inline void cam_dump_b64(const uint8_t *p, size_t n)
{
    static const char B64[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char line[65];
    int col = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)p[i] << 16;
        if (i + 1 < n) v |= (uint32_t)p[i + 1] << 8;
        if (i + 2 < n) v |= (uint32_t)p[i + 2];
        line[col++] = B64[(v >> 18) & 0x3f];
        line[col++] = B64[(v >> 12) & 0x3f];
        line[col++] = (i + 1 < n) ? B64[(v >> 6) & 0x3f] : '=';
        line[col++] = (i + 2 < n) ? B64[v & 0x3f] : '=';
        if (col == 64) { line[64] = 0; printf("%s\n", line); col = 0; }
    }
    if (col) { line[col] = 0; printf("%s\n", line); }
}

//   BEGIN <tag> <w> <h> <nbytes> <crc32>
//   <base64, 64 columns>
//   END <tag>
static inline void cam_dump_frame(const char *tag, const uint8_t *p, size_t n,
                                  uint32_t w, uint32_t h)
{
    printf("BEGIN %s %u %u %u %08x\n", tag, (unsigned)w, (unsigned)h,
           (unsigned)n, (unsigned)cam_crc32(p, n));
    cam_dump_b64(p, n);
    printf("END %s\n", tag);
}

#endif
