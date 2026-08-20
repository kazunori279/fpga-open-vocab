// One flash sector for what the board was doing when the bus went away.
//
// WHY THIS EXISTS, AND WHY THE WATCHDOG SCRATCH IS NOT ENOUGH.
//
// m9 already records the stage, the frame and a POWMAN_CHIP_RESET copy in
// watchdog scratch, and wd_report_last() reads them at the next banner. That
// works for every reboot the firmware or the watchdog causes, and it is the
// right place for those: free, instant, and no wear.
//
// It does not work for issue #9. The one recovery known to bring the board back
// from that outage is cutting VBUS, the scratch registers live in the always-on
// domain, and cutting VBUS is exactly what takes that domain away. So **an
// outage that ends in `uhubctl` is unattributable by construction** - the board
// comes back with a cold scratch, which reset_report() correctly refuses to
// read as anything more than "power, or the bootrom".
//
// Flash survives it. That is the whole argument for paying the costs below.
//
// ---------------------------------------------------------------------------
// The costs, and how each is kept small
//
// AN ERASE IS ~50 MS WITH XIP DOWN. Nothing that runs from flash may execute
// during it, interrupts included, which on a live frame loop is not a cost this
// wants to pay. So the erase happens once, at boot, in lw_take() - before
// w1_start(), while core 1 is still in the bootrom wait loop and there is no
// second core to lock out. The outage path only ever *programs*, which is one
// page and about a millisecond.
//
// A PROGRAM STILL STOPS XIP. Core 1 is running by then and w1_main() is
// __not_in_flash_func, but its job bodies are ordinary calls and betting the
// board on all of them staying out of flash is not a bet worth making.
// lw_write() takes multicore_lockout_start_timeout_us() and gives up rather
// than programming without it: a missing record is a bad outcome, a hung board
// during a USB outage is a worse one.
//
// FLASH WEARS. The sector is erased at boot only when it is dirty, so erases
// are proportional to records written and not to reboots - and records are
// written on an outage, which is the event this whole file is about. A run that
// never drops the bus never touches the part.
//
// SIXTEEN RECORDS PER SECTOR, and then it stops writing until the next boot
// clears it. Running out is itself reported rather than silently dropped.

#ifndef LASTWORDS_H
#define LASTWORDS_H

#include <stdbool.h>
#include <stdint.h>

#define LW_MAGIC    0x4c584746u   // 'FGXL' little-endian
#define LW_VERSION  1u

// Why the record was written. Not a stage - the stage is carried separately, in
// the same encoding m9's watchdog scratch[0] uses, so the two can be compared
// when both survive.
enum {
    LW_WHY_KICK   = 1,   // first re-attach attempt: the bus has been gone a while
    LW_WHY_GIVEUP = 2,   // about to reboot deliberately, bus never came back
};

// 48 bytes into a 256-byte page. The slack is deliberate: a field added later
// costs nothing and does not move any existing record, and a version that has
// to grow the record past a page would be a redesign anyway.
typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t why;
    uint32_t stage;        // watchdog scratch[0] verbatim, tag and all
    uint32_t frame;        // the frame the loop was on when this was written
    uint32_t chip_reset;   // POWMAN_CHIP_RESET's HAD_* bits, live, at write time
    uint32_t uptime_ms;    // since boot
    uint32_t gone_ms;      // how long the bus had been gone already
    uint32_t from_frame;   // the frame it went away on
    uint16_t drops;        // outages counted this run, before this one
    uint16_t kicks;        // re-attaches issued this run
    uint32_t sys_khz;      // which clock the run was at
    uint32_t seq;          // 1, 2, 3 ... within one boot; picks the newest
    uint32_t crc;          // ft_crc32 over everything above. A torn program
                           // fails this rather than reading as a fact.
} lw_rec_t;

// Read the newest valid record and clear the sector, in that order.
//
// CALL THIS ON CORE 0 BEFORE w1_start(), and only there: it erases, and the
// erase is safe precisely because core 1 has not been launched yet.
//
// Returns true and fills `out` when the last life left something behind.
// Returns false when the sector was blank, which is the ordinary case and says
// nothing went wrong. `*note` is set to a line worth printing when something
// other than "blank" or "a good record" happened - the sector held bytes that
// were not a record, or the region is unusable - and to NULL otherwise.
bool lw_take(lw_rec_t *out, const char **note);

// Program one record. Safe to call from the frame loop with core 1 running.
// Fills in magic, version, crc and seq; the caller fills the rest.
//
// Returns false if it did not write, which happens when the sector is full,
// when the region is unusable, or when core 1 would not stop. None of those are
// worth failing a run over, so the caller is expected to carry on.
bool lw_write(lw_rec_t *r);

// How many records lw_write() has declined to write this boot, and why the last
// one was declined. For the `stopped :` line, so a run that quietly lost its
// last words says so while the log is still being read.
uint32_t    lw_declined(void);
const char *lw_declined_why(void);

#endif
