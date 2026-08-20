// See lastwords.h.

#include <string.h>

#include "pico.h"
#include "pico/multicore.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include "hardware/timer.h"

#include "frame.h"        // ft_crc32
#include "lastwords.h"

// The last sector of the part. PICO_FLASH_SIZE_BYTES comes from boards/forgix.h
// and is 2 MB - the RP2354A's stacked die, not a separate chip.
#define LW_OFF   (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define LW_SLOTS (FLASH_SECTOR_SIZE / FLASH_PAGE_SIZE)   // 16

// NOT A CONSTANT ANYWHERE ELSE, AND CHECKED RATHER THAN ASSUMED. The image is
// about 1.04 MB today and the sector is at 2 MB - 4 KB, so there is a megabyte
// of slack and this can only fail if something very large lands in .rodata. But
// "there is plenty of room" is exactly the kind of claim that stays in a comment
// after it stops being true, and the failure mode is erasing the weights.
extern char __flash_binary_end;

static bool     usable;
static bool     checked;
static bool     full;
static uint32_t seq;
static uint32_t declined;
static const char *declined_why;

static bool region_ok(void)
{
    if (checked) return usable;
    checked = true;
    const uintptr_t end = (uintptr_t)&__flash_binary_end;
    usable = (end >= XIP_BASE) && (end - XIP_BASE) <= LW_OFF;
    return usable;
}

static const uint8_t *slot_at(int i)
{
    return (const uint8_t *)(XIP_BASE + LW_OFF + (uintptr_t)i * FLASH_PAGE_SIZE);
}

static bool slot_valid(const uint8_t *p, lw_rec_t *out)
{
    lw_rec_t r;
    memcpy(&r, p, sizeof r);
    if (r.magic != LW_MAGIC || r.version != LW_VERSION) return false;
    if (ft_crc32((const uint8_t *)&r, sizeof r - sizeof r.crc) != r.crc)
        return false;
    if (out) *out = r;
    return true;
}

// Erase with interrupts off. Only ever called from lw_take(), which the header
// requires to run before core 1 exists - so there is deliberately no lockout
// here, and adding one later would be a sign this moved somewhere it should not
// have.
static void erase_sector(void)
{
    const uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(LW_OFF, FLASH_SECTOR_SIZE);
    restore_interrupts(ints);
}

bool lw_take(lw_rec_t *out, const char **note)
{
    if (note) *note = NULL;

    if (!region_ok()) {
        if (note)
            *note = "the image has grown into the last flash sector, so there "
                    "is nowhere to write last words. Nothing was erased.";
        return false;
    }

    bool dirty = false, found = false;
    lw_rec_t best;
    best.seq = 0;

    for (int i = 0; i < LW_SLOTS; i++) {
        const uint8_t *p = slot_at(i);
        // Blank is 0xff after an erase. Checking the whole page rather than the
        // magic alone is what separates "never written" from "written and torn".
        bool blank = true;
        for (uint32_t b = 0; b < FLASH_PAGE_SIZE; b++)
            if (p[b] != 0xffu) { blank = false; break; }
        if (blank) continue;
        dirty = true;

        lw_rec_t r;
        if (slot_valid(p, &r) && (!found || r.seq >= best.seq)) {
            best  = r;
            found = true;
        }
    }

    if (dirty) erase_sector();

    if (found) {
        if (out) *out = best;
        return true;
    }
    if (dirty && note)
        *note = "the last-words sector held bytes that are not a record - a "
                "program torn by the power going away mid-page. Erased.";
    return false;
}

bool lw_write(lw_rec_t *r)
{
    if (!region_ok()) { declined++; declined_why = "no usable sector"; return false; }
    if (full)         { declined++; declined_why = "sector full this boot";  return false; }

    // The first blank page. Re-scanned each time rather than kept in a counter:
    // this runs during an outage, and a counter that disagreed with the flash
    // would overwrite a record that is already the only copy of something.
    int at = -1;
    for (int i = 0; i < LW_SLOTS && at < 0; i++) {
        const uint8_t *p = slot_at(i);
        bool blank = true;
        for (uint32_t b = 0; b < FLASH_PAGE_SIZE; b++)
            if (p[b] != 0xffu) { blank = false; break; }
        if (blank) at = i;
    }
    if (at < 0) { full = true; declined++; declined_why = "sector full this boot"; return false; }

    r->magic   = LW_MAGIC;
    r->version = LW_VERSION;
    r->seq     = ++seq;
    r->crc     = ft_crc32((const uint8_t *)r, sizeof *r - sizeof r->crc);

    uint8_t page[FLASH_PAGE_SIZE];
    memset(page, 0xff, sizeof page);
    memcpy(page, r, sizeof *r);

    // 2 ms is generous for a core that is either spinning on __wfe() in SRAM or
    // inside one ~0.9 ms job. Timing out means core 1 is stuck, which is a
    // different and worse problem than the one being recorded - so this reports
    // and returns rather than waiting on it.
    if (!multicore_lockout_start_timeout_us(2000)) {
        declined++;
        declined_why = "core 1 would not stop for the write";
        return false;
    }
    const uint32_t ints = save_and_disable_interrupts();
    flash_range_program(LW_OFF + (uint32_t)at * FLASH_PAGE_SIZE,
                        page, FLASH_PAGE_SIZE);
    restore_interrupts(ints);
    multicore_lockout_end_blocking();

    // Read it back over XIP before claiming it landed. The cache was invalidated
    // by the program, this costs microseconds, and the alternative is a run that
    // believes it left a record it did not.
    if (!slot_valid(slot_at(at), NULL)) {
        declined++;
        declined_why = "the page did not read back";
        return false;
    }
    return true;
}

uint32_t    lw_declined(void)     { return declined; }
const char *lw_declined_why(void) { return declined_why; }
