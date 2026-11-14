// Offline RGB565/codes -> embedding using the shipped firmware kernels.
// cc -O2 -DFGX_DSP_SHIM -Ifirmware tools/encode_stills.c firmware/encoder.c
//    firmware/encoder_fast.c -lm -o /tmp/encode_stills
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cam_pixel.h"
#include "encoder_fast.h"

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) return 2;
    const int codes_mode = argc == 3 && strcmp(argv[2], "--codes") == 0;
    if (argc == 3 && !codes_mode) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 2; }
    if (fseek(f, 0, SEEK_END)) return 2;
    long len = ftell(f);
    if (len <= 0 || fseek(f, 0, SEEK_SET)) return 2;
    void *blob = malloc((size_t)len);
    if (!blob || fread(blob, 1, (size_t)len, f) != (size_t)len) return 2;
    fclose(f);
    fgx_model_t model;
    if (!fgx_open(&model, blob, (size_t)len) || model.hdr->in_size != 128 ||
        model.hdr->in_ch != 3 || model.hdr->embed_dim != 512) return 2;
    void *a = malloc(model.scratch), *b = malloc(model.scratch);
    void *col = malloc(fgx_fast_col_bytes(&model));
    if (!a || !b || !col) return 2;
    uint8_t frame[128 * 128 * 2];
    int8_t codes[128 * 128 * 3];
    float embed[512];
    const size_t size = codes_mode ? sizeof(codes) : sizeof(frame);
    void *input = codes_mode ? (void *)codes : (void *)frame;
    for (;;) {
        size_t n = fread(input, 1, size, stdin);
        if (!n && feof(stdin)) break;
        if (n != size) { fprintf(stderr, "short input frame\n"); return 2; }
        if (!codes_mode)
            cam_frame_to_chw(frame, 128, 128, true, model.hdr->in_scale, codes);
        fgx_run_fast(&model, codes, embed, a, b, col, true);
        if (fwrite(embed, 1, sizeof(embed), stdout) != sizeof(embed)) return 2;
    }
    free(col); free(b); free(a); free(blob);
    return fflush(stdout) ? 2 : 0;
}
