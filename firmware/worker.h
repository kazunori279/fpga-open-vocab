// A single-consumer job queue running on core 1.
//
// **The RP2354A has two Cortex-M33s and until M7e this project used one.** No
// `multicore_launch_core1` anywhere in firmware/, no `pico_multicore` in any
// target, core 1 in the bootrom wait loop for every measurement up to that
// point in docs/milestones.md.
//
// What makes that expensive is the shape M7d measured. A frame is 963 ms of CPU
// work, and 918 ms of it is core 0 *spinning on a DMA flag* - which is not waste
// that can be deleted, because the wire is the tile's only clock and that spin
// is the accelerator running. But 720 of the remaining ms need nothing except
// buffers: `gb_strip()`, `gb_weights()`, and the requantize scatter. They have
// simply never had a thread to run on.
//
// So this is deliberately the smallest thing that can hold them: one producer
// (core 0), one consumer (core 1), and no locks. There is no work stealing and
// no second consumer, because there is no second idle core to give one to.
//
// ---------------------------------------------------------------------------
// Two queues, because M7f measured what one costs
//
// M7f moved the DRAIN decode here and the frame fell 57 ms instead of the 157
// the decode was worth: **101 ms came back as core 0 stalling.** Core 1 was 68%
// busy, so it was not out of capacity - it was serving in the wrong order. One
// FIFO puts block b's decode and scatter, which nothing is waiting for, in front
// of block b+1's strip build, which core 0 blocks on ~240 us later. 101 ms over
// 174 blocks is 0.58 ms, which is exactly those two jobs.
//
// So there are two rings and core 1 always drains W1_HI first. The split is not
// "important vs unimportant", it is a statement about who waits:
//
//   W1_HI  core 0 will block on this job by name, soon. The strip and weight
//          builds: every pass ends in a w1_wait() for one.
//   W1_LO  nothing waits on this until the buffer it uses is reused, two blocks
//          later. The DRAIN decode and the requantize scatter.
//
// Low-priority work therefore runs in core 1's gaps, and it fits: measured,
// W1_HI is 536 ms and W1_LO 314 ms against a 1,197 ms frame. If that ever stops
// being true the symptom is not starvation - it is core 0 stalling at the
// two-block reuse guard, and w1_stall_us() is where it shows up.
//
// **The split recovered 44 of the 101 ms, not all of it, and the residual is
// the non-preemption bound being paid rather than a bug.** Stall went 138 -> 94
// against mode 3's 39, so ~54 ms survives; W1_LO ran 348 jobs at 0.90 ms each,
// and 54/348 = 0.16 ms is what a build waits, on average, for the low-priority
// job that was already running when it was posted. Half of 0.90 ms less the
// ~0.24 ms of wire core 0 has left before it blocks is ~0.21 ms, so the measured
// number is that bound and there is nothing further to win here without either
// preemption or shorter W1_LO jobs.
//
// ---------------------------------------------------------------------------
// The rules, which are the whole file
//
// 1. **Only core 0 calls w1_post(), w1_wait() and w1_drain().** `head[]` is
//    written by core 0 alone and `tail[]` by core 1 alone, and that is the only
//    reason this needs no lock. A second producer breaks it silently.
//
// 2. **Jobs are copied into the ring, not referenced.** The sequencer's geometry
//    is a loop local that the next iteration overwrites, and its build
//    descriptor is a stack temporary; passing either by pointer would hand core
//    1 a dangling read that happens to work whenever core 1 is fast enough. So
//    w1_post() takes `nbytes` and memcpy's, and traps anything over W1_ARGMAX.
//
// 3. **A job must not touch the link, stdio, or a buffer core 0 is using.**
//    Nothing here enforces it. The link is core 0's because it owns the PIO and
//    the DMA; stdio is core 0's because the SDK's USB stack is not being driven
//    from two cores for a profiling `printf`.
//
// 4. **Wait before you read what a job wrote.** w1_post() returns a ticket and
//    w1_wait() blocks until that job has run. The __dmb() pairs below make the
//    job's stores visible to core 0 at that point and not before - on an M33
//    both cores will happily reorder around an unfenced flag, and the failure
//    mode is not a fault but a *plausible wrong tensor*, which is the one kind
//    of bug this project's bit-exact contract is bad at catching.
//
// 5. **Order holds within a queue and nowhere else.** Two W1_LO jobs run in the
//    order they were posted, which is what lets m7.c post a DRAIN decode and
//    then the scatter that reads its output. A W1_HI job posted later than a
//    W1_LO job will usually run first, so a ticket from one queue says nothing
//    about the other. M7e leaned on the single FIFO for exactly one thing -
//    "waiting on block b+1's build implies block b's scatter retired" - and had
//    already written the explicit guard anyway, on the grounds that the loop
//    should not depend on which modes the ladder contains. That guard is now
//    load-bearing rather than defensive, which is the whole reason it survived.
//
// Every previous failure here was deterministic: a wrong bit boundary or a wrong
// lane order returns the same wrong answer every run, so one strap settles it.
// These will not be. That asymmetry is why the rules are written down.
// ---------------------------------------------------------------------------

#ifndef WORKER_H
#define WORKER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Largest job payload. 64 bytes holds the sequencer's build descriptor (24) and
// its scatter descriptor (40) with room to spare; w1_post() panics rather than
// truncating, because a truncated descriptor is rule 2 failing quietly.
#define W1_ARGMAX 64u

// Jobs that may be outstanding at once, per queue. Power of two: the ring index
// is the low bits of a free-running counter.
#define W1_SLOTS  8u

// The queues. Core 1 runs every ready W1_HI job before any W1_LO job.
#define W1_HI     0
#define W1_LO     1
#define W1_NQ     2

// Launches core 1. Idempotent, and safe to call before anything is posted.
void w1_start(void);

// Copies `nbytes` of `arg` into a free slot of queue `q` and queues `fn`. Blocks
// only if that ring is full. Returns the job's sequence number, which is a
// ticket for `q` and means nothing to the other queue.
uint32_t w1_post(int q, void (*fn)(void *), const void *arg, size_t nbytes);

// Blocks until the job with this ticket, in this queue, has finished. Jobs run
// in the order they were posted *within a queue*, so waiting on one implies
// every earlier job of the same queue is done - and says nothing about the
// other one. See rule 5.
void w1_wait(int q, uint32_t ticket);

// Blocks until both rings are empty. The barrier at a layer boundary, where the
// scratch buffers swap and core 1 must not still be scattering into the tensor
// core 0 is about to read.
void w1_drain(void);

// Profiling. `busy` is accumulated by core 1 and is only stable to read after a
// w1_wait()/w1_drain(); `stall` is core 0's own time inside those two calls plus
// any spent waiting for a free slot, and is the number that says whether core 1
// is keeping up.
uint32_t w1_busy_us(void);
uint32_t w1_stall_us(void);
uint32_t w1_jobs(void);

// Busy time in one queue's jobs, so "did the low-priority work fit in the gaps"
// is answerable without inferring it from two other counters.
uint32_t w1_busy_us_q(int q);

void     w1_prof_reset(void);

#endif
