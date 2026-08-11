#ifndef LINK_TEST_H
#define LINK_TEST_H

#include <stdbool.h>
#include <stdint.h>

// Which board configuration the firmware was built for. See docs/pinmap.md and
// rtl/link_narrow.v / rtl/link_wide.v.
#define LINK_CFG_NARROW 1   // no board modification, 1 bit each way
#define LINK_CFG_WIDE   3   // PIN2 <-> PIN17 jumper, 3 bits out / 1 bit back

#ifndef LINK_CFG
#define LINK_CFG LINK_CFG_NARROW
#endif

void link_test_init(void);
void link_test_sweep(void);

#endif
