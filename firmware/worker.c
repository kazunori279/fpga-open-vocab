// See worker.h. One producer on core 0, one consumer on core 1, no locks.

#include <string.h>

#include "pico.h"
#include "pico/multicore.h"
#include "hardware/sync.h"
#include "hardware/timer.h"

#include "worker.h"

typedef struct {
    void (*fn)(void *);
    uint64_t arg[W1_ARGMAX / 8];   // 8-aligned: a job payload may hold doubles
} w1_slot_t;

static w1_slot_t slot[W1_NQ][W1_SLOTS];

// Free-running counters, never reset. `head[q]` is jobs posted to that queue and
// only core 0 writes it; `tail[q]` is jobs completed and only core 1 writes it.
// Unsigned wraparound is defined and the difference stays correct across it, so
// there is no rollover case to handle - which is the point of counting jobs
// rather than tracking indices.
//
// Two independent counters and therefore two independent ticket spaces. Nothing
// here relates a W1_HI ticket to a W1_LO one, and rule 5 is that promise.
static volatile uint32_t head[W1_NQ], tail[W1_NQ];

static volatile uint32_t busy_us_q[W1_NQ];   // core 1 writes
static uint32_t          stall_us;           // core 0 writes; core 1 never sees it
static bool              started;

// Core 1's whole life.
//
// __not_in_flash_func because this loop runs between every pair of jobs and both
// cores share one XIP cache and one QSPI: a spin loop fetched over flash would
// contend with core 0 for the very resource core 0 is using, and the cost would
// land in core 0's numbers rather than core 1's. M5b measured 21% from nothing
// but moving code off XIP.
//
// M7g extended that to the producer side - w1_post(), w1_wait(), w1_drain() -
// and to the job bodies themselves. The argument for w1_main() was always
// really an argument about the whole queue: a consumer in SRAM that spends its
// time calling flash is not off XIP, it just moved the miss one frame down.
static void __not_in_flash_func(w1_main)(void)
{
    // So core 0 can stop this core to write flash. lastwords.c is the only
    // caller today and only m9 links it, but the init belongs here rather than
    // there: it has to run *on core 1*, and this is the only function that
    // does. It installs a SIO FIFO handler that never fires unless somebody
    // asks, and worker.c does not otherwise use the inter-core FIFO - the two
    // rings below are plain arrays - so there is nothing for it to collide
    // with. Costs m7 and m8 an unused handler and no cycles.
    multicore_lockout_victim_init();

    while (true) {
        // Strict priority, re-evaluated between every pair of jobs: a W1_HI job
        // posted while a W1_LO one was running is picked up the moment it ends.
        // It cannot preempt - there is no scheduler here - so the worst a W1_HI
        // job waits is one W1_LO job, ~0.9 ms. That bound is the reason this is
        // two rings and not a thread with a priority.
        int q;
        while (true) {
            if (head[W1_HI] != tail[W1_HI]) { q = W1_HI; break; }
            if (head[W1_LO] != tail[W1_LO]) { q = W1_LO; break; }
            __wfe();
        }

        // Pairs with the __dmb() before head[q]++ in w1_post(): do not read the
        // slot before the post that filled it is visible.
        __dmb();

        w1_slot_t *s = &slot[q][tail[q] & (W1_SLOTS - 1u)];
        const uint64_t t = time_us_64();
        s->fn(s->arg);
        busy_us_q[q] += (uint32_t)(time_us_64() - t);

        // Pairs with the __dmb() after the spin in w1_wait()/w1_drain(): the
        // job's stores must be visible to core 0 before the ticket retires.
        __dmb();
        tail[q] = tail[q] + 1u;
        __sev();
    }
}

void w1_start(void)
{
    if (started) return;
    started = true;
    for (int q = 0; q < W1_NQ; q++) head[q] = tail[q] = 0;
    __dmb();
    multicore_launch_core1(w1_main);
}

uint32_t __not_in_flash_func(w1_post)(int q, void (*fn)(void *),
                                      const void *arg, size_t nbytes)
{
    // Rule 2. A payload that does not fit is a caller bug and there is no
    // sensible partial answer, so this stops here rather than truncating.
    if (nbytes > W1_ARGMAX) panic("w1_post: %u-byte job, max %u",
                                  (unsigned)nbytes, (unsigned)W1_ARGMAX);

    const uint32_t h = head[q];   // stable: core 0 is the only writer
    if (h - tail[q] >= W1_SLOTS) {
        const uint64_t t0 = time_us_64();
        while (head[q] - tail[q] >= W1_SLOTS) __wfe();
        stall_us += (uint32_t)(time_us_64() - t0);
    }

    w1_slot_t *s = &slot[q][h & (W1_SLOTS - 1u)];
    s->fn = fn;
    if (nbytes) memcpy(s->arg, arg, nbytes);

    __dmb();          // the slot must be written before the job is visible
    head[q] = h + 1u;
    __sev();
    return h;
}

// `tail[q]` counts *completed* jobs of that queue, so ticket h is done once it
// has passed. The comparison is signed on the difference so it survives the
// counters wrapping, which they will not in this harness but would in a shipped
// one.
static inline bool done(int q, uint32_t ticket)
{
    return (int32_t)(tail[q] - ticket) > 0;
}

void __not_in_flash_func(w1_wait)(int q, uint32_t ticket)
{
    if (done(q, ticket)) { __dmb(); return; }
    const uint64_t t0 = time_us_64();
    while (!done(q, ticket)) __wfe();
    __dmb();
    stall_us += (uint32_t)(time_us_64() - t0);
}

static inline bool empty(void)
{
    return head[W1_HI] == tail[W1_HI] && head[W1_LO] == tail[W1_LO];
}

void __not_in_flash_func(w1_drain)(void)
{
    if (empty()) { __dmb(); return; }
    const uint64_t t0 = time_us_64();
    while (!empty()) __wfe();
    __dmb();
    stall_us += (uint32_t)(time_us_64() - t0);
}

uint32_t w1_busy_us(void)      { return busy_us_q[W1_HI] + busy_us_q[W1_LO]; }
uint32_t w1_busy_us_q(int q)   { return busy_us_q[q]; }
uint32_t w1_stall_us(void)     { return stall_us; }
uint32_t w1_jobs(void)         { return head[W1_HI] + head[W1_LO]; }

// Only ever called with both rings empty, so busy_us_q[] is not being written.
void w1_prof_reset(void)
{
    busy_us_q[W1_HI] = busy_us_q[W1_LO] = 0;
    stall_us = 0;
}
