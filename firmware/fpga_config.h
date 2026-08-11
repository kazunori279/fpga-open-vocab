#ifndef FPGA_CONFIG_H
#define FPGA_CONFIG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FPGA_OK            0
#define FPGA_ERR_NSTATUS  -1
#define FPGA_ERR_NO_DONE  -2

void        fpga_config_pins_init(void);
int         fpga_configure(const uint8_t *image, size_t len);

// As fpga_configure(), but with the number of lead-in clock bytes chosen by the
// caller. Only bring-up code should need this; the default is right.
int         fpga_configure_leadin(const uint8_t *image, size_t len, size_t leadin);
void        fpga_release_link_pins(void);
bool        fpga_done(void);
bool        fpga_nstatus(void);
const char *fpga_strerror(int err);

// Provided by the generated bitstream.c (see tools/hex2c.py).
extern const uint8_t  fpga_bitstream[];
extern const size_t   fpga_bitstream_len;
extern const char    *fpga_bitstream_name;

#endif
