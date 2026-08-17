// M9: fpga-open-vocab. Point the camera at something, type what you are looking for,
// and the board answers.
//
// This is m8.c with the comparison re-pointed, and that is the whole trick. The
// student was distilled into the teacher's 512-d embedding space, so a *text*
// vector from the same teacher lands in the same space as the image vector the
// tile produces - which means `cos(image, text)` is a query, computed with the
// same 512-term dot product m8 was already running against the previous frame.
// Nothing about the frame path changes: same frame.c, same eight convolutions,
// same ~900 ms.
//
// ---------------------------------------------------------------------------
// Why a set of queries rather than one
//
// The obvious shape is one sentence, one number, one threshold. It does not
// work in practice, because CLIP cosines live in a narrow band - a good match
// might be 0.28 and a bad one 0.19, and a viewer shown "0.24" learns nothing.
// So the device holds up to six vectors and scores all of them every frame,
// ranked. The ranking is the legible part and it is also the robust part: it
// survives lighting, exposure and the int8 pipeline in a way an absolute level
// does not.
//
// The threshold is still there, per query, and it still comes from data -
// model/evaluate.py places it at a chosen false-positive rate on the same int8
// student it grades. But it arrives from the host with the vector rather than
// being compiled in, because it is a property of the query, not of the firmware.
//
// ---------------------------------------------------------------------------
// Four start-up checks
//
// m8's three, unchanged and for the same reason - a loop printing numbers
// nobody can check is worse than no loop:
//
//   1. THE REFERENCE. encoder_fast.c on the flash test vector, ~3.3 s.
//   2. THE WIDTH PROBE, which identifies the wire by running the whole test
//      vector over it and comparing all 512 floats.
//   3. THE CAMERA, through ft_acquire().
//
// And a fourth, because M9 adds a second thing that can be silently wrong:
//
//   4. THE QUERY SET. Length, CRC, count and embedding dimension all checked
//      before a single verdict is printed. A truncated set would otherwise
//      score against whatever was left in the buffer and look like an opinion.
//
// The loop itself checks nothing against a reference - the frame is new - so
// what it watches is the link's sticky bits and the cosine to the previous
// frame. That last one is not printed. M8c spent 302 frames reporting cos 1.000
// at a sensor that had never started, so this keeps the number and prints a
// sentence only when it has been pinned for ten frames running: a column that
// reads 0.999 forever is a column nobody reads.

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/structs/powman.h" // chip_reset, for the reason the watchdog cannot give
#include "hardware/structs/usb.h"   // sof_rd, and the pull-up bit the sim drops
#include "hardware/vreg.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"
#include "tusb.h"        // tud_mounted / tud_suspended, for the #9 watch below

#include "cam_dump.h"    // the BEGIN/END frame format, shared with host/cam.py
#include "encoder.h"
#include "encoder_fast.h"
#include "fpga_config.h"
#include "frame.h"
#include "gemm_host.h"
#include "worker.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// The MCU reference, and the two most recent tile embeddings - m8's, unchanged.
static float ref_embed[1024];
static float emb[2][1024];

// The resident query set. Six is a budget, not a design limit: the linker leaves
// ~20 KB below the stacks after emb[] and ref_embed[], and six 512-d vectors
// with their names and thresholds is 12.2 KB of it. Raising it means checking
// __bss_end__ again, which is what the assert is for.
#define FGX_MAX_Q   6
#define FGX_DIM     512
#define FGX_NAME    24
#define FGX_CAL     12                       // f32 z_threshold | f32 mean | f32 std
#define FGX_ROLE    4                        // u32, see FGX_Q_* below
#define FGX_REC     (FGX_NAME + FGX_CAL + FGX_ROLE + 4 * FGX_DIM)
#define FGX_HDR     16u                      // u32 nq | dim | bg_tau | bg_flags

// M20. WHAT A QUERY IS FOR, which turns out not to be one thing.
//
// Thirty wordings were swept against 60 recorded frames of an open hand and a
// closed one (tools/probe_prompts.py, and the sweep is in that file's header).
// The result split cleanly by *form* and not by wording, every row the same way:
//
//   bare prompts       z +9 to +24 against the empty room, on BOTH scenes,
//                      and Cohen's d between the scenes of about zero
//   contrast queries   z at or below zero on both scenes, and d up to -3.72
//                      at AUC 0.00 - every closed frame above every open one
//
// That is not two grades of the same thing. A bare prompt answers "is a hand
// here", emphatically and with no opinion on which; a contrast query answers
// "which hand is it", nearly perfectly and with no opinion on whether one is
// there at all. An empty desk is as much "not an open hand" as a fist is, which
// is why the contrast query cannot clear a threshold measured against the empty
// desk - and it fired 0/90 on the bench for exactly that reason.
//
// The firmware had been using the wrong half for each job: thresholding the
// contrast query, which has no z to threshold, and ignoring the bare one, which
// has all of it. Counted over the same run, the ranking was right 176/180 times
// with a mean margin of +1.77 to +7.56, while 21/180 frames crossed a
// threshold. The answer was already there and the decision rule was throwing it
// away. So the role travels with the query.
//
// GATE and CLASS are only meaningful together, and PLAIN is what every query
// was before this. A set with no GATE, or no CLASS, scores exactly as it did.
#define FGX_Q_PLAIN 0u    // threshold on its own z, the pre-M20 rule
#define FGX_Q_GATE  1u    // presence: its z decides whether anything is called
#define FGX_Q_CLASS 2u    // discrimination: ranked against the other CLASSes
#define FGX_Q_ROLE_MAX FGX_Q_CLASS

static float qvec[FGX_MAX_Q][FGX_DIM];
static float qthr[FGX_MAX_Q];      // in units of qsd, not of cosine
static float qmu[FGX_MAX_Q];       // that query's background mean, from evaluate.py
static float qsd[FGX_MAX_Q];       // and its spread
static uint32_t qrole[FGX_MAX_Q];
static char  qname[FGX_MAX_Q][FGX_NAME];
static uint32_t nq;
static uint32_t n_gate, n_class;   // recounted by recv_queries, read every frame

// ---------------------------------------------------------------- M21
// M20 saw two questions and put each on the other one's axis.
//
// It gated on a QUERY, which makes the gate state-dependent - "a book" reads
// -4.87 z with an opened book in shot, so the gate shut on one of the two
// classes it existed to admit, and the two-stage rule scored 16.1% against
// 79.4% for ranking with no gate at all. And it compared raw z, where the
// drift lives: the sensor warms and moves every query together by about 1.5 z
// over four minutes (2026-08-11, 500 static frames).
//
// Both questions are answerable. Split the frame's z into its mean across
// queries and what is left:
//
//   level  = mean(z)            the common mode
//   c[i]   = z[i] - level       "which of the things I was shown is it"
//
// The common mode cancels out of c[] for any query count, the way a difference
// of two queries cancels it for two - which is why removing it is worth
// 44.2 -> 84.2 and 87.5 -> 95.8 on the two recorded book runs
// (tools/probe_rule.py).
//
// M21 PUT PRESENCE ON `level` AND THAT WAS WRONG, measured 2026-08-16 on frames
// it was not fit to: 16/90 and 22/90 of a held-out empty desk (#15). `level` is
// precisely what centring throws away, and the reason to throw it away is that
// it is where the sensor's drift lives - 1.5 z in four minutes. A presence axis
// built out of the discarded term inherits the drift the state axis was made
// immune to, and the bench watched it happen: three visits to the same empty
// desk read 0.21 / 0.32 / 0.44 of the span, monotonically, and the stage never
// let go after the first one.
//
// SO PRESENCE IS A DISTANCE NOW, in the same space the state stage decides in
// (#18):
//
//   absent  <=>  min_k || c[] - qref[k] ||  >  radius
//
// which is open-set rejection rather than a second axis - the frame is absent
// when it does not look like anything it was shown. It inherits the state
// stage's drift immunity because it IS the state stage's arithmetic: `m21_d`
// below is already computed for the classifier and the presence test is one
// comparison on it. Replayed off both 08-16 logs (tools/probe_reject.py) at
// radius 2.0 sep it holds 81/90 and 79/90 of the empty desk while keeping
// 118/120 and 102/120 of the classes, against 16/90 and 22/90 for the level -
// AUC 0.956 and 0.909 - and the three empty visits read 3.03 / 3.48 / 3.06 and
// 2.31 / 2.87 / 2.54 sep, middle visit highest, so there is no trend to follow.
//
// The earlier objection to this shape was that enrolling "absent" as a third
// centred class scores 42.3% and 73.1%, losing 56 and 33 real frames. That
// finding stands and this is not that: there is no absent reference. Nothing is
// enrolled for it, which is also why the empty scene stops being something the
// operator has to keep valid - and for a product that matters more than the
// accuracy does.
//
// Neither axis carries a threshold. The operator SHOWS the board each class -
// '1'..'6' for the class named by that query - and it keeps what it saw. That is
// the point: measurement says the ordering was already right and the boundary
// was not at zero (AUC 1.000 and 79.4% at a margin of zero, because the right
// cut was -3.79), so the boundary is the thing to learn and the ranking is not.
//
// A reference is FGX_ENROL_N frames averaged, not one frame. The first version
// captured one, on probe_rule.py's finding that one frame beat thirty on two
// runs (88%/97% against 75%/79%) - and the first bench of M21 that was not
// degenerate says the opposite, on the board, with the board's own arithmetic:
//
//     frames averaged      1     5     8    12    16    20
//     held out          90.0  89.2  92.5  96.7  99.2  100.0 %
//
// probe_rule.py's thirty-frame mean spanned a whole visit including the parts
// of it where the operator's hand was still leaving; twenty frames starting two
// after the cue does not. So the earlier result was about which frames, not how
// many, and averaging wins once the window is the right one. host/cue.py places
// it - see the schedule there, which is also what keeps the window from running
// off the end of a visit and averaging in the next scene.
//
// 'N' forgets the enrolment along with the background.
#define FGX_ENROL_N 20u

// AND A CLASS IS MORE THAN ONE VISIT, which is a separate claim and the one
// that took nine benches to see. Twenty averaged frames pin down where an
// object sits WHILE IT SITS THERE; they say nothing about where it lands when
// it is staged again, and that second quantity is what decides a run. The
// 08-17 09:18 and 09:33 benches enrolled at 2.85x and 2.75x of within-window
// scatter - indistinguishable - and scored 91.7% and 59.2%, because 09:33's
// opened book came back 1.03 further out on its second visit and crossed the
// boundary. Press the digit again on a later visit and both visits fold into
// the same class: the reference moves to the middle of the class instead of
// wherever the first visit happened to sit, and - the actual point - the
// spread below starts measuring the staging variance too. See FGX_ENROL_SNR.
//
// Two is what host/cue.py schedules and what the guard asks for. More is
// allowed and costs nothing but bench time; the arithmetic is a running sum.
#define FGX_ENROL_V 2u

static float qref[FGX_MAX_Q][FGX_MAX_Q];  // [class][query], centred
static float qref_scat[FGX_MAX_Q];        // RMS frame-to-centre spread, below
static bool  qref_on[FGX_MAX_Q];
// WHAT A SECOND VISIT COSTS IN RAM, which is the constraint that shaped this:
// eight bytes a class, because the guard reads a scalar and so only a scalar
// has to be accumulated. Summing the per-query squares would be the obvious
// way and it does not link - this firmware has about twenty bytes of headroom
// against `RAM` and a [FGX_MAX_Q][FGX_MAX_Q] float array is 144 of them.
//
// The identity that makes it a scalar: the spread the guard wants is the RMS
// distance of a frame from its class centre, and
//
//     sum_j Var(x_j) = (1/n) sum_i |x_i|^2  -  |mu|^2
//
// so one running sum of |cz|^2 and the mean that qref[] already holds are the
// whole of it. The mean itself is updated in place rather than kept as a sum,
// for the same reason.
static float qref_sqsum[FGX_MAX_Q];       // sum over frames of |cz|^2
static uint8_t qref_vis[FGX_MAX_Q];       // visits folded in; frames = *N
static int   enrol_want = -1;             // class being captured
static float enrol_acc[FGX_MAX_Q];        // running sum of cz over the window
static float enrol_sq_acc;                // and of |cz|^2, scalar - see above
static float enrol_acc_lvl;               // only to print the level; not a rule
static uint32_t enrol_left;               // frames still to fold in, 0 = idle
static bool  m21_present;                 // the presence stage's sticky state

#define FGX_ENROL_NONE   (-1)

// The presence stage's two edges, as MULTIPLES OF `sep` - the closest that two
// enrolled references sit to each other - rather than absolutes, so they carry
// the room's calibration the way z already does and there is no constant here
// to be wrong about in a different room.
//
// TRIP is where a frame stops being present; STAY is where it starts again. The
// grid is in tools/probe_reject.py and was swept on both 08-16 runs; 2.0 is the
// single-edge optimum on both and the two-edge pairs 1.5/2.0 and 1.0/2.0 came
// out within 0.4 points of each other averaged over the pair, so the band is
// chosen narrow rather than fitted.
#define FGX_ABSENT_TRIP  2.0f
#define FGX_ABSENT_STAY  1.5f

// IS THE ENROLMENT WORTH ANYTHING, asked in the only units that can answer it.
// `sep` alone cannot: it is the yardstick everything else is quoted in, so a
// collapsed pair does not read as small, it makes every other distance read as
// huge. The 08-17 08:55 bench enrolled two references 0.20 apart, called every
// frame of the run absent - and its origin guard stayed quiet, because those
// references sat 26 *sep* out. The missing measurement is the scale the frames
// themselves set: the RMS distance of a frame from its class's centre, which
// averaging already pays for. A frame lands nearer the wrong reference once
// that noise exceeds half the gap, so sep < 2 * spread means one blob.
//
// WHICH FRAMES GO IN THE SPREAD IS THE WHOLE QUESTION, and the first version of
// this guard got it wrong. Measured over ONE window it sees only how still the
// scene was held, and a scene can be held perfectly still in the wrong place;
// measured over FGX_ENROL_V visits it also sees how far the object moves when
// it is staged again, which is the term that actually decides runs. Every cue
// bench there is, sorted by the two-visit ratio, scored by the live HELD OUT
// line that tools/score_cue.py reprints - NOT by its one-visit replay column,
// which is a different measurement and disagrees by up to 83 points. Two rows
// here were briefly filled in from the wrong one; bench/README.md carries both
// columns for every log so that cannot happen again.
//
//     run          held out   sep(1 visit)  ratio(1)  ratio(2)  ratio(all)
//     08-17 07:33    96.7 %       2.40         2.52      3.24      3.03
//     08-11 07:22   100.0 %       2.28        13.91      2.94      3.49
//     08-17 09:18    91.7 %       3.61         2.17      2.64      2.29
//     ------------------------------------ the void -----------------------
//     08-17 09:33    59.2 %       3.83         2.71      1.24      1.06
//     08-17 09:57    74.2 %       3.69         1.81      0.94      1.05
//     08-17 08:57    76.7 %       5.83         3.69      0.87      0.95
//     08-17 09:55    47.5 %       0.84         0.67      0.44      0.04
//     08-16 17:22    58.3 %       0.17         0.05      0.22      0.28
//     08-16 17:35    57.5 %       0.26         0.10      0.15      0.09
//
// THE VOID IN THAT TABLE WAS FILLED BY THE FIRST RUN THAT TESTED IT, and this
// is the retraction. 08-17 11:26 is the first bench whose references the BOARD
// built from two visits, rather than a log replayed afterwards, and it read
// 1.12 - inside the void, on the reject side. It scored 92.5% held out, the
// best in the project, while the board printed THE CLASSES OVERLAP and told the
// operator to throw it away. The next run read 0.92 and scored 68.3%. Eleven
// runs now:
//
//     3.24 -> 96.7 %      0.94 -> 74.2 %
//     2.94 -> 100.0 %     0.92 -> 68.3 %   <- 08-17 11:44
//     2.64 -> 91.7 %      0.87 -> 76.7 %
//     1.24 -> 59.2 %      0.44 -> 47.5 %
//     1.12 -> 92.5 %      0.22 -> 58.3 %   <- 08-17 11:26, the one that broke it
//                         0.15 -> 57.5 %
//
// THAT IS THE THIRD QUANTITY MEASURABLE AT ENROLMENT TO FAIL THE SAME WAY -
// `sep`, then ratio(1), then ratio(2). Each sorted the benches it was fitted to
// and broke on the next one, and the reason is structural rather than a bad
// choice of statistic three times running: what decides a run is where the
// object lands on visits that have not happened yet, and no measurement taken
// at the enrolment can contain that.
//
// SO THIS IS NOW ONE-SIDED. High still means something - 2.64, 2.94 and 3.24
// scored 91.7%, 100.0% and 96.7%, three for three - and the board says so. Low
// means nothing at all: below the bar the eight runs span 92.5% to 47.5%, so
// the message says that and stops, instead of telling the operator to throw
// away an enrolment that may be the best one they will get today. The constant
// is 2.6 rather than the 2.0 it was, because 2.64 is the lowest ratio that has
// ever certified anything and the interval below it is not measured; erring
// upward costs a line of praise, erring downward is what just cost a bench.
// Three runs is what it rests on. Do not read the bar as a promise.
//
// The collapse check below (sep > 0.05) is untouched and is not part of this:
// two references on top of each other is a degeneracy, not a prediction.
//
// tools/probe_sepscale.py is where the table comes from and how to redo it.
#define FGX_ENROL_SNR    2.6f

// The digits index the enrollable queries in the order the host sent them: the
// FGX_Q_CLASS ones when roles came with the set, every query when they did not.
// For ab.sh's "an opened book / a closed book / a book", '1' is the opened book
// and '2' the closed one - and "a book" is deliberately not something you can
// show the board, because it is not a state the scene can be in. k is 1-based.
static int enrol_slot(uint32_t k)
{
    uint32_t seen = 0;
    for (uint32_t i = 0; i < nq; i++) {
        if (n_class && qrole[i] != FGX_Q_CLASS) continue;
        if (++seen == k) return (int)i;
    }
    return -1;
}

static uint32_t enrol_count(void)
{
    uint32_t n = 0;
    for (uint32_t i = 0; i < nq; i++) if (qref_on[i]) n++;
    return n;
}

// Forgetting the background without forgetting the enrolment would leave
// references measured against a mu that no longer exists - centred vectors are
// immune to a shift shared by every query, which is the point, but not to a
// per-query one, and 'N' re-learns each qmu separately. So the two are one key.
static void enrol_forget(void)
{
    for (uint32_t i = 0; i < FGX_MAX_Q; i++) {
        qref_on[i]    = false;
        qref_vis[i]   = 0;
        qref_sqsum[i] = 0.0f;
    }
    enrol_want  = FGX_ENROL_NONE;
    enrol_left  = 0;
    m21_present = false;
}

// The background the scores are actually measured against, learned here.
//
// COCO's negatives turned out to be the wrong background for a device that only
// ever looks at one room. Across five bench scenes - a blank whiteboard, a
// book's back cover, its front cover, a wine glass, and a covered lens -
// `laptop` led every single one, and
// it led on the raw cosine, not just after standardising. The reason is not a
// bug: the mean of COCO's laptop-negatives is taken over fields and food and
// dogs, while every frame this camera has ever taken is an indoor desk in front
// of a window, which is a fairly laptop-shaped prior. The residue is a property
// of *the room*, and there is exactly one place it can be measured.
//
// So each query also carries a running mean of its own cosine over roughly the
// last bg_tau frames, and the reported score is the deviation from that.
// Whatever produces a constant per-query offset - scene prior, white balance,
// the student's own bias - it appears in both terms and cancels.
//
// THE PRICE, AND WHY M12 MADE IT A CHOICE THE CALLER MAKES. A running mean
// measures change, not presence: leave the same book in front of the lens long
// enough and it becomes the background and its score decays toward zero. M9
// hard-coded that behaviour with a 200-frame window, and the M9 comment here
// said "this constant is where the choice lives". It now lives on the wire.
//
//   bg_hold = false   the M9 behaviour. A plain running mean for bg_tau frames,
//                     an exponential one after, forever. Right for a demo where
//                     things are held up and taken away.
//   bg_hold = true    learn for bg_tau frames, then FREEZE. Right for a fixed
//                     installation asking "has this state appeared, and is it
//                     still there" - which is what the alerts this project is
//                     aimed at actually ask.
//
// Hold is the default because that is the use case, and because a decaying
// score is the more surprising of the two failures: M11 put the score on D1, so
// under M9's rule the LED fades back to green with the book still in shot and
// looks like a bug. `--bg-tau 200 --no-bg-hold` restores M9 exactly.
//
// THE DEFAULT WINDOW SHRANK WITH IT, and the two changes are one change. Under
// hold, bg_tau is a warm-up length rather than an averaging window, and it wants
// to be short: whatever is in shot while it runs is what gets frozen in as
// furniture. 200 frames is three minutes of standing still, long enough that a
// book set down mid-warm-up is substantially absorbed before the freeze. 30 is
// ~27 s, which is long enough for the mean of a static scene to be tight and
// short enough that a person will actually leave the scene alone for it.
//
// bg_tau = 0 with hold is legal and is not a degenerate case worth rejecting:
// bg_n never leaves 0, so zscore() below keeps using COCO's qmu forever. That is
// "no bench background at all", which is the M8 behaviour and a useful control.
#define FGX_BG_TAU_DEFAULT  30u
#define FGX_BG_HOLD_DEFAULT true

// M19. The fourth header word was a bool and is now a flag set, and the
// widening needs no flag day: hold=1 is FGX_BG_HOLD and hold=0 is nothing, so
// an M12-era host lands on exactly the behaviour it asked for and the two new
// bits stay clear. That is the whole migration.
//
// Both new bits exist for the same measurement. On the 2026-08-10 hand run the
// empty scene's frame-to-frame spread was 0.0039 in cosine, while the qsd this
// divides by - COCO's, over 5000 unrelated photographs - is 0.034. Nine times
// too wide, so every z was nine times too small and a threshold meant for the
// 90th percentile of the background sat at the 99.99th. `an open hand` had
// AUC 0.861 against everything else in the room and fired on 6 frames of 90.
#define FGX_BG_HOLD    0x1u
#define FGX_BG_ROOM_SD 0x2u    // divide by the room's spread, not COCO's
#define FGX_BG_SMOOTH  0x4u    // EMA the z before thresholding it
#define FGX_BG_FLAGS   (FGX_BG_HOLD | FGX_BG_ROOM_SD | FGX_BG_SMOOTH)

// Two guards on the room's spread, and neither is a tuning knob.
//
// A handful of frames cannot estimate a spread - that objection was right when
// this comment said qsd must stay COCO's, and it is still right. What changed
// is that the warm-up is 30 frames, not one. Below FGX_BG_SD_MIN_N the estimate
// is not used at all and COCO's stands, so a bg_tau too short to measure with
// degrades to the old behaviour rather than to a wrong scale.
//
// The floor catches the degenerate room: a lens cap, a frozen camera, a scene
// with no noise in it at all drives the spread toward zero and z toward
// infinity, which would paint every query as a match. 0.001 is four times below
// the tightest spread this bench has produced, so it bounds the divide without
// reaching up into the range real rooms occupy.
#define FGX_BG_SD_MIN_N  8u
#define FGX_BG_SD_FLOOR  0.001f

// THE SMOOTHING IS NOT A SEPARATE IMPROVEMENT. IT IS WHAT PAYS FOR THE OTHER
// BIT, and the two were nearly shipped as independent options before an empty
// room was pointed at them.
//
// The room's spread is measured over 30 consecutive frames, so it is the
// short-timescale noise and nothing else. The threshold is then applied for
// minutes, over which the scene also *drifts* - exposure, daylight, the
// background frozen at one moment of a room that keeps moving. COCO's spread
// was wide enough to hide that drift; the room's is not, and on 75 frames of
// empty bench `an open hand` reached the 90th percentile at z 2.08 and fired on
// 24% of them. A threshold advertised as 10% delivering 24% is worse than the
// insensitivity it replaced, because it is insensitivity's opposite failure
// wearing the same number.
//
// An EMA at 0.35 - about a five-frame memory, 2.5 s here - averages away the
// fast noise while leaving a real state intact, and drift is slow enough to
// stay small next to a threshold expressed in the *unsmoothed* spread. Measured
// on the same empty room after the change: 0/75 and 2/75. The AUC gain that
// motivated it first (0.861 -> 0.914 on the hand run) is the smaller half of
// what it does.
//
// So FGX_BG_ROOM_SD without FGX_BG_SMOOTH is a combination that measures
// something, and it is not a 10% false-positive rate. demo.py sets both by
// default and --no-smooth exists for measuring, not for running.
#define FGX_Z_EMA_A     0.35f
// A sanity bound, not a resource one: bg_tau costs no memory. It exists so a
// garbled or byte-swapped field is rejected by name instead of silently meaning
// "never finish warming up", which under hold is indistinguishable from a board
// that has stopped scoring. 100k frames is ~25 hours.
#define FGX_BG_MAX_TAU      100000u

static uint32_t bg_tau  = FGX_BG_TAU_DEFAULT;
static bool     bg_hold = FGX_BG_HOLD_DEFAULT;
static bool     bg_room_sd;   // both default off, so a build that is never sent
static bool     bg_smooth;    // a query set behaves exactly as M12 did

static float    qbg[FGX_MAX_Q];
static float    qm2[FGX_MAX_Q];   // Welford's sum of squared deviations, from
                                  // which the room's own spread comes
static float    zema[FGX_MAX_Q];  // the smoothed z, when FGX_BG_SMOOTH is set
static uint32_t bg_n;     // frames in the estimate, saturating at bg_tau
static uint32_t bg_seen;  // frames since this query set arrived, and it does not
                          // saturate - which is the only reason it exists. The
                          // first 300-frame run scheduled its background line off
                          // bg_n, and printed one every frame from 200 onwards.

_Static_assert(sizeof(qvec) + sizeof(qthr) + sizeof(qmu) + sizeof(qsd)
                   + sizeof(qbg) + sizeof(qm2) + sizeof(zema) + sizeof(qname)
                   <= 14u * 1024u,
               "the query set has outgrown the space below the stacks - check "
               "__bss_end__ against 0x20080000 before raising FGX_MAX_Q");

// WHAT IS RANKED IS NOT THE COSINE. A CLIP cosine carries a per-query offset
// that belongs to the sentence rather than to the picture: across the 67 scored
// COCO queries the background mean spans 0.213 to 0.274, and on this bench the
// difference between "a book is on the table" and "it is not" is 0.009. The
// offset is seven times the signal, so the raw ranking is a ranking of the
// wording. Standardising each query against its own background - mean and
// spread measured on that query's negatives by model/evaluate.py - cancels it,
// and lands every threshold near 1.28, which is what the 90th percentile of a
// background is.
//
// The cosine is not lost by printing z instead: the matching line prints the
// raw cosine beside the name, and bg_print() dumps the whole learned background
// periodically, so any z in the log can be turned back into a cosine.
//
// M12 KEPT qsd AT COCO'S, AND M19 STOPPED. The old argument was that only the
// *centre* was wrong and that one frame's worth of noise cannot estimate a
// spread. The second half is true and is why FGX_BG_SD_MIN_N exists; the first
// half was an assumption nobody had measured, and when it was measured it was
// wrong by 9x - see FGX_BG_ROOM_SD.
//
// WHICH SPREAD IS RIGHT IS A QUESTION ABOUT THE INSTALLATION, NOT ABOUT THE
// MODEL, and that is why it is a flag rather than a fix. A threshold is a
// false-positive rate, and a rate is only defined against a population of
// negatives. COCO's spread answers "how often would this fire on an arbitrary
// photograph" - the right question for a device carried between scenes. The
// room's spread answers "how often would this fire on this room, empty" - the
// right question for one bolted to a wall, which is what this is. The number
// evaluate.py calibrated does not change meaning; the population it is measured
// against does, and a log that cannot tell which was used is a log that cannot
// be re-read, so bg_print() names it every time.
//
// The room's spread is also the narrower claim of the two, and worth stating as
// such: it is the frame-to-frame noise of a static scene. A negative that is a
// *different* scene - the hand closed rather than open - is a harder negative
// than that, and on the hand run the 10% point against those sat at 1.49 room
// sd rather than 1.23. Close, but the threshold is no longer conservative for
// them the way COCO's was, and the false fires it does allow will be the
// near-misses rather than the furniture.
static inline float bg_spread(uint32_t i)
{
    if (bg_room_sd && bg_n >= FGX_BG_SD_MIN_N) {
        const float s = sqrtf(qm2[i] / (float)(bg_n - 1u));
        return s > FGX_BG_SD_FLOOR ? s : FGX_BG_SD_FLOOR;
    }
    return qsd[i];
}

static inline float zscore(uint32_t i, float cos)
{
    const float sd = bg_spread(i);
    if (!(sd > 0.0f)) return 0.0f;
    // Before this bench has said anything, COCO's estimate is the best there
    // is. After one frame it is not.
    return (cos - (bg_n ? qbg[i] : qmu[i])) / sd;
}

// A plain running mean while there are fewer than bg_tau frames, then either an
// exponential one - the same thing with its window pinned - or nothing at all.
//
// The warm-up is not a detail. Seeded from COCO and left to converge at
// 1/bg_tau, the first minutes of every session would show exactly the offset
// this exists to remove. Seeded from the bench, the estimate is usable within a
// handful of frames, at the cost that whatever is in shot at boot is taken to be
// furniture. Which it usually is - and under hold that assumption stops being
// revisable, which is what 'N' is for.
//
// Hold costs one branch and no state: the warm-up already ends at bg_n == bg_tau
// and already switches rule there, so freezing is just declining to take the
// second rule. bg_seen keeps counting either way, because it schedules the
// background line and a frozen background still deserves printing.
//
// THE SPREAD IS LEARNED DURING THE WARM-UP AND ONLY THERE, under either rule.
// While bg_n < bg_tau the mean is a plain running one, so Welford's update is
// exact and qm2 is the sum of squared deviations it is supposed to be. After
// that the mean is either frozen or exponential, and an exponential mean's
// residuals carry its drift as well as the frame noise - a slow change in the
// light would inflate the "spread" of a scene that is not varying at all. That
// is a different quantity from the one the threshold wants, so it is not
// measured. The consequence is worth being plain about: under tracking, the
// centre keeps up with the room and the scale does not.
static void bg_update(const float *cos)
{
    if (bg_hold && bg_n >= bg_tau) { bg_seen++; return; }
    const uint32_t n = bg_n < bg_tau ? bg_n : bg_tau;
    const float a = 1.0f / (float)(n + 1u);
    const bool warming = bg_n < bg_tau;
    for (uint32_t i = 0; i < nq; i++) {
        const float d0 = cos[i] - qbg[i];
        qbg[i] += d0 * a;
        if (warming) qm2[i] += d0 * (cos[i] - qbg[i]);
    }
    if (warming) bg_n++;
    bg_seen++;
}

// Forget the room and learn it again. Under hold this is the only way back from
// a baseline that froze around the wrong scene - somebody's hand in shot, the
// book already on the desk - and it costs nothing to offer, since recv_queries()
// has to do exactly this on every new set anyway.
static void bg_reset(void)
{
    bg_n = 0;
    bg_seen = 0;
    for (uint32_t i = 0; i < FGX_MAX_Q; i++) {
        qbg[i] = 0.0f;
        qm2[i] = 0.0f;
        // The smoothed z goes with them. Carrying a five-frame memory of the
        // old scale across a 'N' would put the first frames after the reset on
        // a mixture of two backgrounds, which is exactly what 'N' is for
        // escaping.
        zema[i] = 0.0f;
    }
}

// What the room currently looks like, in cosines. Printed rarely and on its own
// line: it is the number that explains every verdict above it, and a log
// without it cannot be re-read six months later.
static void bg_print(void)
{
    // Whether it is still moving is half of what this line is for. Under hold a
    // reader who cannot tell a frozen estimate from a converged one cannot tell
    // a stable score from a decaying one either, and those are the two outcomes
    // the whole flag exists to choose between.
    // And which spread the z above was divided by, because that is now a
    // choice and a reader six months out cannot recover it from anything else
    // in the log. It prints the room's estimate either way - unused, it is
    // still the number that says whether the choice was the right one.
    printf("background: after %u frames (%s, %s spread), this room reads",
           (unsigned)bg_n,
           bg_hold ? (bg_n >= bg_tau ? "frozen" : "warming up")
                   : "tracking",
           (bg_room_sd && bg_n >= FGX_BG_SD_MIN_N) ? "room" : "COCO");
    for (uint32_t i = 0; i < nq; i++)
        printf("  %s %.3f +-%.4f (COCO %.3f +-%.4f)", qname[i], (double)qbg[i],
               (double)(bg_n >= 2u ? sqrtf(qm2[i] / (float)(bg_n - 1u)) : 0.0f),
               (double)qmu[i], (double)qsd[i]);
    printf("\n");
    stdio_flush();
}

// ---------------------------------------------------------------------------
// M11 stage 1. D1 as a score meter: green when nothing matches, red when the
// board would say MATCH, continuously in between.
//
// Nothing here reaches the LED yet. Stage 1 computes the two duties and prints
// them, so the curve can be settled on the bench before any RTL is written -
// see below for why that ordering is the whole point.
//
// WHY THE MAPPING IS ON THIS SIDE AND THE PWM IS NOT. D1's three cathodes are
// T8 balls (E1/F1/G1), so the MCU's only route to them is the link, and the
// fabric has to own the pulse widths either way. The question is where the
// *colour* is decided. It is decided here, because everything in this block is
// a thing that gets re-tuned by eye - the top of the scale, the curve, the
// red/green balance - and an RTL change costs a resynthesis of both bitstreams
// plus a full m7 ladder, while a change here costs a ninja and a reflash. The
// fabric gets the part that will never change. Put the moving parts where they
// are cheap to move.
//
// THE TOP OF THE SCALE IS THE WINNING QUERY'S OWN THRESHOLD, not a fixed z.
// That is what keeps the constant count at two: full red means exactly "this
// frame would print MATCH", per query, at whatever false-positive rate
// model/evaluate.py calibrated for that query. Below the learned background,
// heat is 0 and the LED is green.
//
// AND THE METER INHERITS WHATEVER THE BACKGROUND IS DOING. Under M9's rule the
// meter measured change rather than presence: leave the book in shot for bg_tau
// frames and its score decays, so the LED fades back to green with the book
// still there. Making that fault visible from across the room is what got it
// fixed - M12 froze the background by default, and the same LED now holds red.
// Press 'H' to watch it fade again; the LED is the cheapest demonstration of
// the difference the flag makes.

// Perceived brightness goes as roughly the 1/2.2 power of light output, and an
// LED's output is linear in duty. Raising the duty to 2.2 therefore makes
// *perceived* red track heat linearly and perceived green track (1 - heat), so
// the two sum to a constant and the fade passes through yellow at the halfway
// point instead of jumping there and sitting. A linear ramp does the latter.
#define FGX_LED_GAMMA  2.2f

// R7 = 680 R on red against R8 = 360 R on green, so equal duties are not equal
// light and the neutral point is not 50/50. This is the eye-calibrated constant
// and it cannot be settled until D1 is actually driven; 1.0 is a placeholder
// and stage 2 trims it. Being a firmware constant, that trim costs a reflash.
#define FGX_LED_GTRIM  1.0f

static uint8_t led_r, led_g;
static float   led_heat;
static float   led_lit = 1.0f;    // the second axis, see led_two() below

// One place where hue and brightness become duties, so the gamma argument above
// is made once. Both channels are raised together, which is what keeps
// *perceived* brightness proportional to `lit`: gamma-correcting the hue and
// then scaling the result linearly would make a half-lit LED look about 73% as
// bright, and the dimming would read as barely there.
static void led_duty(float hue, float lit)
{
    if (hue < 0.0f) hue = 0.0f;
    if (hue > 1.0f) hue = 1.0f;
    if (lit < 0.0f) lit = 0.0f;
    if (lit > 1.0f) lit = 1.0f;
    led_heat = hue;
    led_lit  = lit;
    led_r = (uint8_t)(255.0f * powf(lit * hue, FGX_LED_GAMMA) + 0.5f);
    led_g = (uint8_t)(255.0f * FGX_LED_GTRIM
                      * powf(lit * (1.0f - hue), FGX_LED_GAMMA) + 0.5f);
}

static void led_map(float z, float thr)
{
    led_duty((thr > 0.0f) ? z / thr : 0.0f, 1.0f);
}

// M20, and the two axes are the two stages of the decision rather than two
// decorations on one.
//
// BRIGHTNESS IS THE GATE. Dark means "nothing is there", and it is dark because
// the presence query's z is low, not because the score faded. This is the axis
// the meter never had: under the old rule an empty desk and a hand the board
// could not classify both showed some colour, and there was no way to tell them
// apart from across the room. Now the first is off and the second is yellow.
//
// HUE IS THE CLASSIFIER, and it is bipolar where the old one was unipolar. Full
// red is the first state query, full green whichever other one is beating it,
// and the middle is a hand the two cannot separate. The old meter's green meant
// "no match", which collided with a legitimate answer the moment a second query
// existed.
//
// THE SIGN COMES FROM A FIXED QUERY, NOT FROM THE WINNER, and the first cut of
// this got that wrong: it passed z[lead] - z[runner], which is a maximum minus a
// runner-up and therefore never negative, so the hue could only ever run red to
// yellow. The 2026-08-10 bench printed h0.57..h1.00 across 159 frames and never
// once went below 0.5 - green was unreachable. The reference is now the lowest-
// indexed FGX_Q_CLASS query, which for ab.sh is the first phrase on the command
// line, so red means phrase A and green means the other side won.
//
// THE HALF-SPAN IS THE REFERENCE QUERY'S OWN THRESHOLD, keeping the constant
// count where M11 left it: the colour saturates exactly when the margin reaches
// what that query calls a match. EXPECT IT SATURATED. On the hand bench the
// measured margins run to +17 against a threshold of 1.23, so the LED will sit
// hard red or hard green nearly all the time. That is the measurement and not a
// scale error - the classifier really is that confident - and the interesting
// frames are the few that are not, which is what the middle of the scale is for.
static void led_two(float margin, float thr, float gate_z, float gate_thr)
{
    const float half = (thr > 0.0f) ? thr : 1.0f;
    led_duty(0.5f + 0.5f * (margin / half),
             (gate_thr > 0.0f) ? gate_z / gate_thr : 0.0f);
}

// M21 keeps led_two()'s two axes and takes both scales from the enrolment
// instead of from a threshold, which is the only change that matters here: the
// board no longer has a constant to be wrong about.
//
// HUE. `margin` is (distance to the nearest other reference) minus (distance to
// the reference class), so it is signed for the reason led_two()'s comment
// gives - the first cut of M20 passed a maximum minus a runner-up, which is
// never negative, and green was unreachable for 159 frames. `sep` is the
// closest that two enrolled references sit to each other, so the colour
// saturates exactly when the frame is sitting on one of them. Both are
// distances in the centred z space, so the scale is the enrolment's own.
//
// BRIGHTNESS. `lit` is now 1 - d/(TRIP * sep): full on when the frame is sitting
// on a reference, dark when it is a trip radius away from every one of them, and
// the same distance the presence decision is made on, so the LED and the printed
// verdict cannot disagree. It used to be the frame's place on the level span,
// which was the axis #18 removed. With fewer than two references there is no
// scale and it stays lit; the board is not claiming presence in that case, it is
// declining to.
static void led_ref(float margin, float sep, float lit)
{
    const float half = (sep > 0.0f) ? sep : 1.0f;
    led_duty(0.5f + 0.5f * (margin / half), lit);
}

// ---------------------------------------------------------------------------
// M20b. THE FRAME LOOP SAYS WHERE IT IS, SO THE NEXT HANG IS LOCATED.
//
// The board has wedged eight times now and not one of those has a cause, for a
// single reason: the evidence is a log that stops after a complete frame line
// and says nothing else. Every account of it so far - USB, the link, the camera
// - is a guess, and the 2026-08-10 run that died after frame 108 had no `!led`,
// no fault, and a clean MATCH on its last line.
//
// So the loop writes which call it is inside into a watchdog scratch register,
// which survives a reset, and arms the watchdog. A wedge now reboots the board
// after FGX_WD_MS and the next boot prints the stage and the frame number. Two
// things that were not true before: the recovery no longer needs uhubctl at the
// wall, and the eighth hang becomes the first one with an address.
//
// THE RUN IS STILL LOST. A reboot forgets the frozen background, so the bench
// has to start over - this buys the diagnosis, not the measurement.
//
// FGX_WD_MS is 8 s against a ~304 ms frame, which is loose on purpose. 'P'
// dumps 46.8 KB down the CDC and blocks until the host drains it; a timeout
// tight enough to be prompt would call a slow reader a hang. Eight seconds of
// no progress in this loop is not something a working host does.
#define FGX_WD_MS   8000u
#define FGX_WD_TAG  0x57440000u          // 'WD', so a stale scratch is not read

enum {
    FGX_ST_CAPTURE = 0, FGX_ST_ENCODE, FGX_ST_SCORE, FGX_ST_LED,
    FGX_ST_PRINT, FGX_ST_POLL, FGX_ST_DUMP_PIC, FGX_ST_DUMP_EMB,
    FGX_ST_BITSTREAM, FGX_ST_FPGA, FGX_ST_MODEL, FGX_ST_REFERENCE,
    FGX_ST_LINK, FGX_ST_CAMERA, FGX_ST_QWAIT, FGX_ST_N
};

// Not a stage: a reason. usb_watch() below reboots deliberately after the bus
// has been gone long enough to be unrecoverable, and that must not come back
// wearing a hang's clothes - the loop was never stuck, and saying it was would
// point the next reader at the wrong thing. Kept out of the enum so
// FGX_ST_N stays "how many stages there are".
#define FGX_ST_USBGONE 0x100u
static const char *const fgx_stage[FGX_ST_N] = {
    "ft_capture - the camera",
    "encode - the 8 convs over the link, then pool and head",
    "scoring - cosines and the background update, all local",
    "gh_led - the LED transaction over the link",
    "printf/stdio_flush of the frame line - USB CDC, blocks on the host",
    "poll_host - waiting on stdin for a key or a new query set",
    "cam_dump_frame - 46.8 KB of picture down the CDC",
    "the 512-float dump down the CDC",
    "ft_recv_bitstream - the FPGA image coming down the CDC",
    "fpga_config - clocking the image into the T8, waiting on CDONE",
    "ft_crc32 over the weights, and the model header",
    "the reference encode on the flash test vector (about 3.4 s of it)",
    "probe - running the test vector over the wire to pick a link width",
    "ft_acquire - bringing the camera up and ramping its exposure",
    "waiting for a query set from host/demo.py",
};

static inline void wd_stage(uint32_t s, uint32_t frame)
{
    watchdog_hw->scratch[0] = FGX_WD_TAG | (s & 0xffffu);
    watchdog_hw->scratch[1] = frame;
    watchdog_update();
}

// ---------------------------------------------------------------------------
// AND A REASON THE WATCHDOG CANNOT GIVE.
//
// wd_report_last() below only speaks when watchdog_caused_reboot() is true. A
// 280 MHz soak on 2026-08-15 found a third shape at frame 273: the board left
// the bus, came back with a fresh banner, and printed neither `hang :` nor
// `usb :`. Nothing above the firmware rebooted it, so something below it did,
// and from the log that was indistinguishable from a hand on the cable.
//
// POWMAN_CHIP_RESET's HAD_* bits name it. They are read-only and they
// accumulate - the HAD_POR from the morning's first power-up is still set an
// hour later - so the register alone cannot say what happened on THIS boot.
// Keep a copy in watchdog scratch[2] (free; the SDK's own reboot path uses
// 4..7) and report the difference. Losing the copy is itself an answer: the
// scratch survives a watchdog reboot and does not survive the always-on domain
// going away, so an untagged scratch means power, not software.
#define FGX_RS_TAG  0x52530000u          // 'RS', same trick as FGX_WD_TAG
#define FGX_RS_MASK 0x1fff0000u          // every HAD_* bit, and nothing else

static void reset_report(void)
{
    static const struct { uint32_t bit; const char *what; } had[] = {
        { POWMAN_CHIP_RESET_HAD_POR_BITS,
          "power-on reset - the supply arrived" },
        { POWMAN_CHIP_RESET_HAD_BOR_BITS,
          "BROWN-OUT - the supply sagged past the detector" },
        { POWMAN_CHIP_RESET_HAD_GLITCH_DETECT_BITS,
          "GLITCH DETECT - a fast step on the supply" },
        { POWMAN_CHIP_RESET_HAD_RUN_LOW_BITS,
          "the RUN pin was pulled low" },
        { POWMAN_CHIP_RESET_HAD_DP_RESET_REQ_BITS,
          "a reset request from the debug port" },
        { POWMAN_CHIP_RESET_HAD_RESCUE_BITS,
          "a rescue reset from the debugger" },
        { POWMAN_CHIP_RESET_HAD_HZD_SYS_RESET_REQ_BITS,
          "a system reset from the hazard debugger" },
        { POWMAN_CHIP_RESET_HAD_SWCORE_PD_BITS,
          "a switched-core powerdown" },
        { POWMAN_CHIP_RESET_HAD_WATCHDOG_RESET_PSM_BITS,
          "the watchdog, resetting the power-on state machine" },
        { POWMAN_CHIP_RESET_HAD_WATCHDOG_RESET_SWCORE_BITS,
          "the watchdog, resetting the switched core" },
        { POWMAN_CHIP_RESET_HAD_WATCHDOG_RESET_POWMAN_BITS,
          "the watchdog, resetting the power manager" },
        { POWMAN_CHIP_RESET_HAD_WATCHDOG_RESET_POWMAN_ASYNC_BITS,
          "the watchdog, resetting the power manager asynchronously" },
    };

    const uint32_t now  = powman_hw->chip_reset & FGX_RS_MASK;
    const uint32_t prev = watchdog_hw->scratch[2];
    const bool     kept = (prev & 0xffff0000u) == FGX_RS_TAG;
    const uint32_t was  = kept ? (prev & 0xffffu) << 16 : 0u;
    watchdog_hw->scratch[2] = FGX_RS_TAG | (now >> 16);

    // What is new since the last banner. On a plain 'R' or a watchdog reboot
    // this is usually empty, because those reset the core without touching
    // powman, and an empty set is a fact worth printing next to a reboot.
    const uint32_t fresh = now & ~was;

    // "Cold" is weaker evidence than it first looked, and saying so is the
    // point of the line. A watchdog reboot keeps the copy - verified, a
    // deliberate FGX_ST_USBGONE reboot reads "nothing new" - but a BOOTSEL
    // round trip does not: `picotool reboot` came back cold on 2026-08-16 with
    // nothing wrong at all. So a cold scratch narrows this to "power, or the
    // bootrom", and only the caller knows whether anybody typed picotool.
    printf("reset     : chip_reset %08x%s\n", (unsigned)now,
           kept ? "" : "  (scratch was cold: either the always-on domain went "
                       "away - power, a brown-out - or this boot came through "
                       "the bootrom, which clears it too)");
    for (size_t i = 0; i < count_of(had); i++)
        if (fresh & had[i].bit)
            printf("            new this boot: %s\n", had[i].what);
    if (kept && !fresh)
        printf("            nothing new since the last banner - whatever reset "
               "the core left powman alone.\n");
    stdio_flush();
}

// Called once, before anything is armed. Reads what the last life left behind
// and then clears it, so a later intentional reboot ('R') does not come back
// wearing a hang's clothes.
static void wd_report_last(void)
{
    const uint32_t s = watchdog_hw->scratch[0];
    const uint32_t f = watchdog_hw->scratch[1];
    watchdog_hw->scratch[0] = 0;
    if (!watchdog_caused_reboot() || (s & 0xffff0000u) != FGX_WD_TAG)
        return;
    const uint32_t k = s & 0xffffu;
    if (k == FGX_ST_USBGONE) {
        printf("usb       : the last run rebooted itself at frame %u because this "
               "port stopped answering.\n"
               "            The loop was fine - see issue #9 - and the reboot is "
               "how the board got back\n"
               "            on the bus. That run's background and scores are "
               "gone.\n", (unsigned)f);
        stdio_flush();
        return;
    }
    printf("hang      : the last run stopped for %u ms at frame %u, inside %s\n"
           "            The watchdog rebooted it. That run's background and "
           "scores are gone;\n"
           "            what survives is this line.\n",
           (unsigned)FGX_WD_MS, (unsigned)f,
           k < FGX_ST_N ? fgx_stage[k] : "an unknown stage");
    stdio_flush();
}

// ---------------------------------------------------------------------------
// ISSUE #9. THE BOARD DROPS OFF USB AND KEEPS COMPUTING, AND UNTIL NOW THAT WAS
// SOMETHING THE HOST INFERRED AFTERWARDS FROM A LOG THAT RESUMED.
//
// The event: at 150/75 and at 280/140, roughly once in nine runs, the port
// vanishes mid-run and nothing with VID 2E8A comes back. It is not a hang - the
// next attach found the loop at frame 244 having left at 71, same enrolment,
// same background, no banner - and it is not the watchdog, which never fired.
// The two cores are fine; the USB device has stopped being on the bus.
//
// WHAT THIS DOES ABOUT IT, in the order it does it.
//
// 1. SAY IT HAPPENED, AND ASK THE BUS RATHER THAN THE STACK. tud_mounted() is
//    the device's own view of whether the host has it configured, and it is the
//    one signal that separates this fault from the ordinary case of an operator
//    closing the terminal - that drops DTR, so stdio_usb_connected() goes false
//    while the device stays mounted.
//
//    IT IS ALSO NOT ENOUGH, WHICH THE FIRST BUILD OF THIS WATCH PROVED BY
//    MISSING TWO OUTAGES IN A ROW. Both times the board vanished from the host
//    (`uhubctl` showed the port powered with nothing connected), stayed gone
//    past the 30 s giveup, and never rebooted itself - so the watch never fired
//    at all, which means tud_mounted() was still true. TinyUSB only learns of a
//    detach from an interrupt, and nothing here delivered one: the D+ pull-up
//    stopped being presented while the stack went on believing it was mounted.
//
//    So the liveness test is the bus itself. usb_hw->sof_rd is the frame number
//    of the last start-of-frame packet the SIE saw, and a host that has this
//    device on an enabled, unsuspended port sends one every millisecond,
//    unconditionally, whatever either end thinks. If that number stops moving
//    for FGX_USB_SOF_MS the bus is gone, mounted or not - and the case where it
//    is gone WHILE mounted is exactly the fault above, and the one the kick in
//    step 2 can actually fix, since writing the pull-up bit again is all it
//    takes. Every outage longer than FGX_USB_QUIET_MS is counted and, when the
//    bus returns, printed with the frames it spanned and which of the two
//    shapes it had. That line is the direct evidence the issue had to
//    reconstruct.
//
// 2. TRY TO COME BACK BY ITSELF. Toggling the pull-up (tud_disconnect() then
//    tud_connect()) is a fresh attach as far as the host is concerned, and
//    host/demo.py has re-resolved by VID rather than by path since 43801dd - so
//    a board that re-enumerates inside the host's 45 s window rejoins a run that
//    is still going, background and enrolment intact. Two attempts, at 2 s and
//    at 8 s, because the first one may land while the host is still tearing the
//    old device down.
//
// 3. FAILING THAT, REBOOT - deliberately, and saying so. After
//    FGX_USB_GIVEUP_MS the run is unrecoverable anyway (nothing printed in it
//    reached anyone) and the choice is between a board computing invisibly until
//    somebody notices and a board that is back on the bus in a few seconds.
//    wd_report_last() prints FGX_ST_USBGONE as a reason, not as a hang.
//
// 4. WHILE IT IS DOWN, BE VISIBLE. D1 alternates hard red and off once a frame,
//    which is the heartbeat the issue asked for: at ~3 frames/s it is an
//    unmistakable blink across the room, it runs over the link rather than over
//    USB, and it proves the loop is turning without anyone having to infer it
//    from a log that resumes later.
//
// WHAT IT DELIBERATELY DOES NOT DO: act on tud_suspended(). A host that suspends
// the bus is a laptop with its lid shut, and yanking the pull-up at it would be
// a device that wakes the machine up. The state is reported and nothing else -
// and it is also the one legitimate reason for the SOFs above to stop, so a
// suspended bus is held to be alive rather than counted as an outage.
//
// AND THE QUESTION THE ISSUE ASKED ABOUT stdio_usb: it is cosmetic, not a future
// hang. stdio_usb_out_chars() breaks out of its loop the moment
// stdio_usb_connected() is false, so a board off the bus prints at full speed
// into nothing - which is why the frame counter ran from 71 to 244. The only
// blocking case is a host that stays attached and stops draining, and that is
// bounded by PICO_STDIO_USB_STDOUT_TIMEOUT_US, which CMakeLists.txt now pins
// rather than inheriting. m9's 'W' note above records the same result from the
// other direction: SIGSTOP on demo.py did not stop the board.
#define FGX_USB_QUIET_MS     500u   // shorter than this is a blink, not an event
#define FGX_USB_KICK_MS     2000u
#define FGX_USB_KICK2_MS    8000u
#define FGX_USB_GIVEUP_MS  30000u
// A thousand SOFs' worth of silence, sampled once a frame at ~330 ms. The frame
// number is 11 bits and wraps every 2,048 ms, so a sample that happens to read
// the same value twice is possible; three in a row without a single change is
// not, unless the packets really have stopped.
#define FGX_USB_SOF_MS      1000u

static uint32_t usb_drops;      // outages this run
static uint32_t usb_kicks;      // re-attaches issued
static uint32_t usb_gone_ms;    // total time off the bus

// THE ESCALATION ABOVE IS ONLY WORTH ANYTHING IF IT HAS BEEN SEEN TO RUN, and a
// real outage is a poor test rig: it happens once every few hundred frames, it
// takes the log with it, and when it does happen there is no way to tell "the
// board rebooted itself and the host still could not see it" from "the reboot
// never fired". 'U' and 'I' remove that ambiguity by dropping the pull-up on
// purpose, which is the same thing the bus sees in a real outage.
//
//   'U' - the recoverable half. The watcher should notice, blink D1, re-attach
//         at 2 s, and print the `back after ~2000 ms` line.
//   'I' - the unrecoverable half: the reconnect in the kick is suppressed, so
//         the watcher has to walk all the way to the deliberate reboot at 30 s
//         and the next banner has to name it as `usb :` and not as a hang.
//
// AND THE PULL-UP IS DROPPED BEHIND TinyUSB'S BACK, with a write to SIE_CTRL
// rather than through tud_disconnect(), because that is the fault this is
// standing in for. A tud_disconnect() sim tests a detach the stack knows about,
// which is the easy case and not the one that has been happening: two outages
// in this session left the stack still believing it was mounted, which is why
// the SOF test above exists. Dropping the bit directly reproduces that exactly -
// the host loses the device, tud_mounted() stays true, and only sof_rd notices.
//
// Both miss F, G, X and Q, per poll_host()'s rule. `usb_sim_hard` does not need
// clearing: the only exit from it is the reboot, which clears it by resetting.
static volatile bool usb_sim_hard;

static void usb_sim_drop(bool hard)
{
    usb_sim_hard = hard;
    hw_clear_bits(&usb_hw->sie_ctrl, USB_SIE_CTRL_PULLUP_EN_BITS);
}

// Is the bus answering? Not "does the stack think so" - see step 1 above.
//
// `silent` is the interesting half of the answer: true means the SOFs stopped
// while TinyUSB still had the device mounted, which is the fault shape #9 keeps
// producing and the one a pull-up rewrite can fix. It is a file static rather
// than an out-parameter because the reporting path wants it two calls later.
static bool usb_silent;

static bool usb_alive(uint64_t now)
{
    static uint32_t last_sof;
    static uint64_t sof_at;

    const uint32_t sof = usb_hw->sof_rd & 0x7ffu;
    if (!sof_at || sof != last_sof) {
        last_sof = sof;
        sof_at   = now;
    }
    if (!tud_mounted()) { usb_silent = false; return false; }
    // A suspended bus has no SOFs by definition and is not an outage.
    if (tud_suspended()) { sof_at = now; usb_silent = false; return true; }
    if (now - sof_at < (uint64_t)FGX_USB_SOF_MS * 1000u) {
        usb_silent = false;
        return true;
    }
    usb_silent = true;
    return false;
}

static void usb_watch(uint32_t frame)
{
    static uint64_t down_at;    // when the bus went away; 0 means it is up
    static uint32_t from_frame;
    static int      kicked;
    static bool     blink;
    static bool     pend;       // an outage measured but not yet reportable
    static uint32_t pend_ms, pend_from, pend_at;
    static bool     pend_silent, was_silent;

    const uint64_t now = time_us_64();
    const bool mounted = usb_alive(now);

    if (mounted) {
        if (down_at) {
            const uint32_t ms = (uint32_t)((now - down_at) / 1000u);
            down_at = 0;
            kicked  = 0;
            blink   = false;
            if (ms >= FGX_USB_QUIET_MS) {
                usb_drops++;
                usb_gone_ms += ms;
                // HELD, NOT PRINTED, and the first run of this code is why:
                // the report went out the instant the device was mounted and
                // never reached the log, because enumeration is not a reader.
                // stdio_usb throws away anything written before the host opens
                // the port and raises DTR, so the one line that explains the
                // gap in the log was the one line the gap swallowed.
                pend        = true;
                pend_ms     = ms;
                pend_from   = from_frame;
                pend_at     = frame;
                pend_silent = was_silent;
            }
        }
        if (pend && tud_cdc_connected()) {
            pend = false;
            printf("\nusb       : back after %u ms off the bus - gone at frame "
                   "%u, here at frame %u.\n"
                   "            %s\n"
                   "            The loop never stopped; everything it printed "
                   "in that window is lost. Issue #9.\n"
                   "            %u re-attach%s issued, %u outage%s this run.\n",
                   (unsigned)pend_ms, (unsigned)pend_from, (unsigned)pend_at,
                   pend_silent
                     ? "The stack still had it MOUNTED and the SOFs had "
                       "stopped, so only sof_rd saw this one."
                     : "The device was detached, which TinyUSB reported "
                       "itself.",
                   (unsigned)usb_kicks, usb_kicks == 1 ? "" : "es",
                   (unsigned)usb_drops, usb_drops == 1 ? "" : "s");
            stdio_flush();
        }
        return;
    }

    if (!down_at) {
        down_at    = now;
        from_frame = frame;
        kicked     = 0;
        blink      = false;
        was_silent = usb_silent;
        return;
    }

    blink = !blink;
    gh_led(blink ? 255u : 0u, 0u);

    const uint32_t ms = (uint32_t)((now - down_at) / 1000u);
    if ((kicked == 0 && ms >= FGX_USB_KICK_MS) ||
        (kicked == 1 && ms >= FGX_USB_KICK2_MS)) {
        kicked++;
        usb_kicks++;
        tud_disconnect();
        busy_wait_ms(120);
        if (!usb_sim_hard) tud_connect();
        return;
    }
    if (ms >= FGX_USB_GIVEUP_MS) {
        watchdog_hw->scratch[0] = FGX_WD_TAG | FGX_ST_USBGONE;
        watchdog_hw->scratch[1] = frame;
        watchdog_reboot(0, 0, 0);
        for (;;) tight_loop_contents();
    }
}

// ---------------------------------------------------------------------------
// Where a failed start-up goes to sit. m8.c's park(), verbatim: the watchdog is
// the exit that depends on nothing, because a board that has gone deaf on stdin
// has no other one.
static void park(void)
{
    printf("\nparked - 'B' for BOOTSEL, 'R' to re-run; otherwise this reboots\n"
           "         to the bitstream prompt in 8 s\n");
    stdio_flush();
    watchdog_enable(8000, 1);
    for (;;) {
        const int c = getchar_timeout_us(200000);
        if (c == 'B' || c == 'b') { printf("bootsel\n"); sleep_ms(50);
                                    reset_usb_boot(0, 0); }
        if (c == 'R' || c == 'r') { printf("reboot\n");  sleep_ms(50);
                                    watchdog_reboot(0, 0, 0); }
    }
}

// ---------------------------------------------------------------------------
// One frame through the tile, from `image` to `embed`. m8.c's, unchanged.
static const char *encode(const void *image, float *embed)
{
    ft_frame_reset();

    const void *src = image;
    void *dst = ft_arena();

    for (uint32_t i = 0; i < ft_nconv(); i++) {
        const ft_err_t r = ft_layer(i, src, dst);
        if (r.fault) return r.fault;
        if (r.link)  return gh_strerror(r.link);
        src = dst;
        dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                          : (void *)ft_arena();
    }
    ft_pool_head((const float *)src, embed);
    return NULL;
}

// The cosine of two embeddings. The query vectors arrive L2-normalized and the
// tile's output is not, so this normalizes both rather than assuming either.
static double cosine(const float *a, const float *b, uint32_t n)
{
    double dot = 0.0, na = 0.0, nb = 0.0;
    for (uint32_t i = 0; i < n; i++) {
        dot += (double)a[i] * (double)b[i];
        na  += (double)a[i] * (double)a[i];
        nb  += (double)b[i] * (double)b[i];
    }
    if (na <= 0.0 || nb <= 0.0) return 0.0;
    return dot / (sqrt(na) * sqrt(nb));
}

// ---------------------------------------------------------------------------
// Which wire this is, measured rather than declared. m8.c's probe().
static unsigned probe(const fgx_model_t *m, const void *image)
{
    static const unsigned tries[2] = { 3, 1 };

    for (int k = 0; k < 2; k++) {
        if (!gh_set_width(tries[k])) continue;

        const uint64_t t0 = time_us_64();
        const char *why = encode(image, emb[0]);
        const uint32_t ms = (uint32_t)((time_us_64() - t0) / 1000u);

        int bad = 0;
        if (!why)
            for (uint32_t o = 0; o < m->hdr->embed_dim; o++)
                if (emb[0][o] != ref_embed[o]) bad++;

        printf("probe     : %u forward data line%s -> ", tries[k],
               tries[k] == 1 ? "" : "s");
        if (why)          printf("%s\n", why);
        else if (bad)     printf("%d of %u embedding floats wrong, %u ms\n",
                                 bad, (unsigned)m->hdr->embed_dim,
                                 (unsigned)ms);
        else              printf("%u/%u floats exact, %u ms\n",
                                 (unsigned)m->hdr->embed_dim,
                                 (unsigned)m->hdr->embed_dim,
                                 (unsigned)ms);
        stdio_flush();

        if (!why && !bad) return tries[k];
    }
    return 0;
}

// ---------------------------------------------------------------------------
// The query set, off the wire:
//
//   "FGXQ" | len u32 LE | crc32 u32 LE | payload
//   payload = nq u32 | dim u32 | nq x { char name[24] | f32 zthr | f32 mean |
//                                       f32 std | f32 vec[dim] }
//
// Same framing as the bitstream, deliberately - one host script, one shape to
// get right, and ft_recv_exact() and ft_crc32() are already shared rather than
// copied. The magic has been consumed by the caller.
//
// The payload lands in the arena rather than in a static buffer. The arena is
// scratch between frames by construction: ft_capture() writes RGB565 into it and
// encode() writes layer 0 over that, so nothing here can outlive its own frame,
// and 12 KB of .bss that exists only during a download would be 12 KB the query
// set itself could have had.
//
// Every rejection below is a print and a `false`, never a partial accept. A set
// that half-loaded would score against whatever the other half used to be, and
// would look exactly like a wrong answer rather than a broken one.
static bool recv_queries(uint32_t dim)
{
    uint8_t hdr[8];
    if (!ft_recv_exact(hdr, 8)) return false;

    uint32_t len, crc;
    memcpy(&len, hdr + 0, 4);
    memcpy(&crc, hdr + 4, 4);

    if (len < FGX_HDR || len > FGX_HDR + (uint32_t)FGX_MAX_Q * FGX_REC) {
        printf("\nqueries   : rejected - %u bytes, and the most %d queries can "
               "be is %u\n", (unsigned)len, FGX_MAX_Q,
               (unsigned)(FGX_HDR + (uint32_t)FGX_MAX_Q * FGX_REC));
        return false;
    }

    uint8_t *buf = (uint8_t *)ft_arena();
    if (!ft_recv_exact(buf, len)) return false;

    const uint32_t have = ft_crc32(buf, len);
    if (have != crc) {
        printf("\nqueries   : rejected - CRC %08x, expected %08x\n",
               (unsigned)have, (unsigned)crc);
        return false;
    }

    uint32_t n, d, tau, flags;
    memcpy(&n,     buf + 0,  4);
    memcpy(&d,     buf + 4,  4);
    memcpy(&tau,   buf + 8,  4);
    memcpy(&flags, buf + 12, 4);

    if (n == 0 || n > FGX_MAX_Q) {
        printf("\nqueries   : rejected - %u queries, and this build holds %d\n",
               (unsigned)n, FGX_MAX_Q);
        return false;
    }
    if (d != dim || d > FGX_DIM) {
        printf("\nqueries   : rejected - %u-d vectors, and the model emits %u\n",
               (unsigned)d, (unsigned)dim);
        return false;
    }
    // d, not FGX_DIM: the record is as wide as the vectors actually sent, so a
    // 512-d host talking to a build compiled for more is a length match rather
    // than a rejection. FGX_REC is the worst case and only bounds the buffer.
    const uint32_t rec = FGX_NAME + FGX_CAL + FGX_ROLE + 4u * d;
    if (len != FGX_HDR + n * rec) {
        // M12 widened the header from 8 bytes to 16 and M20 put a role word in
        // every record, so an older host lands here and is told the number it
        // should have sent. That is the whole migration story: a flag day that
        // fails loudly on the first query set rather than one that mis-parses
        // the records and reports wrong scores.
        printf("\nqueries   : rejected - %u bytes for %u %u-d queries, expected "
               "%u\n", (unsigned)len, (unsigned)n, (unsigned)d,
               (unsigned)(FGX_HDR + n * rec));
        if (len == FGX_HDR + n * (rec - FGX_ROLE))
            printf("            that is exactly the M12 payload, %u bytes short "
                   "- one role word per query. Update host/demo.py.\n",
                   (unsigned)(n * FGX_ROLE));
        else if (len == 8u + n * (rec - FGX_ROLE))
            printf("            that is exactly the M9 payload, 8 bytes of "
                   "header and one role word per query short. Update "
                   "host/demo.py.\n");
        return false;
    }
    if (tau > FGX_BG_MAX_TAU) {
        printf("\nqueries   : rejected - background window %u frames, and the "
               "most this build allows is %u\n",
               (unsigned)tau, (unsigned)FGX_BG_MAX_TAU);
        return false;
    }
    // A bit this build does not know about means the host is asking for a
    // policy it will not get, and silently scoring under the wrong one is the
    // failure this whole milestone is about. Refuse and name the bits.
    if ((flags & ~FGX_BG_FLAGS) != 0u) {
        printf("\nqueries   : rejected - background flags 0x%X, and this build "
               "knows 0x%X. Update firmware/m9.c.\n",
               (unsigned)flags, (unsigned)FGX_BG_FLAGS);
        return false;
    }

    for (uint32_t i = 0; i < n; i++) {
        const uint8_t *p = buf + FGX_HDR + i * rec;
        memcpy(qname[i], p, FGX_NAME);
        qname[i][FGX_NAME - 1] = '\0';
        memcpy(&qthr[i], p + FGX_NAME + 0, 4);
        memcpy(&qmu[i],  p + FGX_NAME + 4, 4);
        memcpy(&qsd[i],  p + FGX_NAME + 8, 4);
        memcpy(&qrole[i], p + FGX_NAME + FGX_CAL, 4);
        memcpy(qvec[i], p + FGX_NAME + FGX_CAL + FGX_ROLE, 4u * d);
        if (qrole[i] > FGX_Q_ROLE_MAX) {
            printf("\nqueries   : rejected - '%s' has role %u, and this build "
                   "knows 0..%u. Update firmware/m9.c.\n",
                   qname[i], (unsigned)qrole[i], (unsigned)FGX_Q_ROLE_MAX);
            return false;
        }
        // A zero or negative spread would make zscore() a divide by zero, and a
        // query whose background never varied is not one this can standardise.
        // Saying so beats scoring it as a flat 0 without comment.
        if (!(qsd[i] > 0.0f))
            printf("\nqueries   : '%s' has no background spread, so it will "
                   "score a flat 0 and never match\n", qname[i]);
    }
    nq = n;
    n_gate = n_class = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (qrole[i] == FGX_Q_GATE)  n_gate++;
        if (qrole[i] == FGX_Q_CLASS) n_class++;
    }
    // Half of the pair is a host bug, not a policy. Scoring it as if the roles
    // had not been sent would be the M19 mistake again - a run under a rule
    // nobody asked for - and the two counts are the only thing standing between
    // that and a log that looks fine.
    if ((n_gate != 0u) != (n_class != 0u)) {
        printf("\nqueries   : rejected - %u presence and %u state queries, and "
               "the two-stage rule needs\n"
               "            at least one of each. Send neither for the plain "
               "per-query threshold.\n",
               (unsigned)n_gate, (unsigned)n_class);
        return false;
    }
    bg_tau     = tau;
    bg_hold    = (flags & FGX_BG_HOLD)    != 0u;
    bg_room_sd = (flags & FGX_BG_ROOM_SD) != 0u;
    bg_smooth  = (flags & FGX_BG_SMOOTH)  != 0u;
    // A new set is a new set of backgrounds. Carrying the old estimate over
    // would be worse than starting cold, because index i now means a different
    // sentence - and --ask makes that happen in the middle of a run. It is also
    // what makes bg_tau/bg_hold runtime-settable for free: re-sending the set is
    // already a full reset, so there is nothing extra to unwind.
    bg_reset();
    // And a reference is a vector over the OLD set's queries. Index i means a
    // different sentence now, and nq may not even be the same, so a kept
    // enrolment would be compared component-by-component against sentences it
    // was never measured on - arithmetic that works and means nothing, which is
    // the failure mode this project keeps meeting. Forget it with the rest.
    enrol_forget();

    printf("\nqueries   : %u accepted, %u-d, crc ok\n", (unsigned)n, (unsigned)d);
    if (bg_hold)
        printf("            scored as z = (cos - background) / std, where the "
               "background is this room's\n"
               "            own mean over its first %u frames and is then FROZEN"
               " - so a thing left in\n"
               "            shot keeps its score instead of decaying into the "
               "furniture. 'N' re-learns\n"
               "            it, 'H' switches to tracking. The COCO figures below"
               " are only the seed for\n"
               "            frame 0 and the fixed std.\n", (unsigned)bg_tau);
    else
        printf("            scored as z = (cos - background) / std, where the "
               "background is this room's\n"
               "            own running mean over the last %u frames, tracking "
               "forever - so this\n"
               "            measures change, not presence. 'H' freezes it. The "
               "COCO figures below are\n"
               "            only the seed for frame 0 and the fixed std.\n",
               (unsigned)bg_tau);
    // Which spread, said at the top rather than left to be inferred from the
    // background line 30 frames later. It changes what every '*' in the run
    // means and it is the first thing to check when a log looks unfamiliar.
    if (bg_room_sd)
        printf("            std is THIS ROOM's, measured over the same %u "
               "frames ('S' switches to COCO's).\n"
               "            The thresholds below still mean the 90th percentile "
               "of a background; the\n"
               "            background they now mean it against is this room "
               "empty, not 5000 COCO photos.\n", (unsigned)bg_tau);
    else
        printf("            std is COCO's, fixed ('S' switches to this room's, "
               "which is usually much\n"
               "            tighter and therefore much more sensitive).\n");
    if (bg_smooth)
        printf("            z is EMA-smoothed at %.2f (~5 frames) before "
               "ranking, so a state has to\n"
               "            persist for about two seconds to be called.\n",
               (double)FGX_Z_EMA_A);
    if (n_gate && n_class)
        printf("            TWO-STAGE: the presence queries gate, and the state"
               " queries are then RANKED\n"
               "            against each other with no threshold of their own -"
               " a contrast query's z\n"
               "            is measured against an empty room, which is as much"
               " 'not an open hand' as\n"
               "            a fist is, so it has no absolute level to clear. D1"
               " reads the same two\n"
               "            stages: brightness is the gate, red/green is which "
               "state leads.\n");
    for (uint32_t i = 0; i < n; i++) {
        const char *r = qrole[i] == FGX_Q_GATE  ? "presence, gates above z"
                      : qrole[i] == FGX_Q_CLASS ? "state, ranked; nominal z"
                      :                           "match above z";
        printf("            %-20s %s %.2f   (COCO background %.3f "
               "+- %.3f, so cos %.3f)\n", qname[i], r, (double)qthr[i],
               (double)qmu[i], (double)qsd[i],
               (double)(qmu[i] + qthr[i] * qsd[i]));
    }
    stdio_flush();
    return true;
}

// Drain whatever the host has sent and say what it was. Every hotkey is a single
// character that cannot appear in the magic, so it costs one compare and keeps
// working while a download is not in progress. THAT CONSTRAINT IS LOAD BEARING -
// see the note on 'E' below for what it costs to break it, and the note above
// poll_host() itself for the five keys that need a second condition as well.
//
// `w` is static on purpose. A 12 KB write arrives as USB packets, and a poll can
// land with two bytes of "FGXQ" in the buffer and two still in flight; a local
// shift register would drop those two and never match again. Static, the window
// simply spans the gap - and it is self-correcting, since any four bytes that
// are not the magic shift straight back out.
//
// Returns 0 for nothing, 'B', 'R', 'P' for "dump the next frame", 'E' to
// provoke D1's fault display, 'H' to toggle the background between frozen and
// tracking, 'N' to re-learn it from now, 'O' to overlap the capture with the
// compute, 'Q' for a query set accepted, or 'q'
// for one rejected. `us` is the per-byte wait: 0 in the frame loop, where this
// must not block, and a second during the initial hunt, where there is nothing
// else to do.
//
// 'H' and 'N' are M12's, and they are hotkeys rather than flags because the
// measurement they serve is a back-to-back one: hold and track have to be
// compared on the same book under the same lamp, and re-sending 12 KB of
// queries between the two halves changes the scene while the hands are in it.
//
// 'E' for "error", and NOT 'F' for "fault", which is the obvious choice and is
// broken: 'F' is the first byte of "FGXQ", so a hotkey test ahead of the shift
// register eats it and the magic can never match again. The board then sits at
// the query prompt forever while the host pushes 12 KB into a buffer nobody
// drains. Any new hotkey has to miss all four of F, G, X and Q - which is what
// the note at the top of this comment is there to prevent forgetting.
//
// AND FIVE OF THEM NOW NEED MORE THAN A COMPARE, which cost a bench session to
// learn: a board already in the loop was sent a 173 KB bitstream, because the
// host had restarted and the board had not, and somewhere in the first packets
// of it was a 0x42. 'B'. The board went to BOOTSEL mid-download and looked, from
// the host end, exactly like issue #9 - a port that vanished and never came
// back. It was not #9; it was a hotkey test reading data as a keypress.
//
// So the five keys that END THE RUN - 'B' to the bootloader, 'R' to a reboot,
// 'W' to a deliberate hang, 'U' and 'I' off the bus - are only hotkeys when the
// byte ARRIVED ALONE. Everything else keeps costing one compare: 'P', 'V', 'O',
// 'D', the digits and the rest are recoverable, and some of them are sent in
// pairs on purpose (demo.py writes "PV" as one write), so a quiet-time rule
// there would break a working thing to guard against nothing. This is
// frame.c's FT_HOTKEY_QUIET_US argument, one prompt further on.
#define FGX_HOTKEY_QUIET_US 100000u

static int poll_host(uint32_t dim, uint32_t us)
{
    static uint32_t w = 0;
    static uint64_t prev_us = 0;

    for (int i = 0; i < 512; i++) {
        const int c = getchar_timeout_us(us);
        if (c == PICO_ERROR_TIMEOUT) return 0;
        const uint64_t now = time_us_64();
        const bool alone = now - prev_us >= FGX_HOTKEY_QUIET_US;
        prev_us = now;
        if (alone) {
            if (c == 'B' || c == 'b') return 'B';
            if (c == 'R' || c == 'r') return 'R';
            if (c == 'W' || c == 'w') return 'W';
            if (c == 'U' || c == 'u') return 'U';
            if (c == 'I' || c == 'i') return 'I';
        }
        if (c == 'P' || c == 'p') return 'P';
        if (c == 'V' || c == 'v') return 'V';
        if (c == 'E' || c == 'e') return 'E';
        if (c == 'H' || c == 'h') return 'H';
        // 'S' has been in the loop banner and in report()'s handler since M19
        // and was never in this list, so pressing it did nothing at all - the
        // byte fell through to the "FGXQ" shift register and was discarded.
        // Nothing measured the spread toggle on the board; that is now possible.
        if (c == 'S' || c == 's') return 'S';
        // 'C' stays a bare compare with 'W', 'U' and 'I' above it: a stalled
        // camera bus costs one frame by design, so a stray 0x43 costs a
        // diagnostic line rather than the run.
        if (c == 'C' || c == 'c') return 'C';
        if (c == 'N' || c == 'n') return 'N';
        // #10's toggle. 'O' for overlap, and it misses all four of F, G, X and
        // Q. A hotkey rather than a build flag for M5b's reason - see frame.h -
        // and here that reason is not a nicety: the question 'O' answers is
        // whether overlapping the capture makes the *frame* shorter or only the
        // sum of its named parts, and two builds cannot answer it, because the
        // baseline and the overlap would then differ in more than the overlap.
        if (c == 'O' || c == 'o') return 'O';
        // #14's, for the same argument one level down: 'O' asks whether to
        // overlap at all and 'D' asks where inside the overlap the trigger
        // belongs. Both are one-boot questions.
        if (c == 'D' || c == 'd') return 'D';
        // M21's enrolment keys. Digits miss all four of F, G, X and Q, which is
        // the constraint the note above exists to enforce.
        if (c >= '0' && c <= '0' + (int)FGX_MAX_Q) return c;
        w = (w << 8) | (uint8_t)c;
        if (w == 0x46475851u) {                   // "FGXQ"
            w = 0;
            return recv_queries(dim) ? 'Q' : 'q';
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// What a frame cost, over a window of `k` timed frames. Two lines, and they are
// two measurements of the same thing rather than a figure and its breakdown:
// `us` is a sum of parts and `wall` is a clock, and the reason both are printed
// is that only their agreement makes either one trustworthy (see the note at the
// accumulators). Factored out because 'O' closes a window mid-run and the final
// summary closes the last one, and a second copy would be a second thing to keep
// in step - which is how the four bitstream defaults drifted apart.
//
// `wall` divides by k-1, not k: it accumulates *intervals* between arrivals, and
// n points bound n-1 of them.
//
// `lead` carries its own punctuation, since one caller is a labelled line and
// the other is a continuation of one.
static void report_cost(uint32_t k, uint64_t us, uint64_t enc,
                        uint64_t wait, uint64_t wall, uint64_t age,
                        bool overlap, const char *lead)
{
    printf("%s%u frames timed, %u ms/frame mean (capture included)\n",
           lead, (unsigned)k, k ? (unsigned)(us / k / 1000u) : 0u);
    printf("            %u ms encode + %u ms waiting for the sensor + "
           "%u ms burst; %u ms/frame by the clock\n",
           k ? (unsigned)(enc / k / 1000u) : 0u,
           k ? (unsigned)(wait / k / 1000u) : 0u,
           k ? (unsigned)((us - enc - wait) / k / 1000u) : 0u,
           k > 1 ? (unsigned)(wall / (k - 1) / 1000u) : 0u);
    // #14, and it is a third line rather than a column because it is a third
    // *quantity*: the two above are throughput and this is latency. Under the
    // overlap they move in opposite directions, so a report that showed only one
    // of them would make either change look free.
    // The lead only describes where the trigger went if the schedule is the
    // thing issuing it. Under 'D' it is still being adapted and still printable,
    // and printing it there would read as an explanation of an age it had no
    // hand in - so say what actually happened instead.
    printf("            %u ms shutter to LED, ", k ? (unsigned)(age / k / 1000u) : 0u);
    if (!overlap)
        printf("triggering inline\n");   // serial: nothing arms anything
    else if (ft_cap_is_eager())
        printf("arming at the collect\n");
    else
        printf("arming with %u ms of compute left\n",
               (unsigned)(ft_cap_lead_us() / 1000u));
}

// ---------------------------------------------------------------------------
// One frame's verdict. `cos` is the raw cosine per query; everything below is
// in standardised units, for the reason zscore() gives. Ranked, `*` on anything
// over its own threshold, and the match is the best of those that cleared - not
// simply the best, because a top-ranked query that cleared nothing is not an
// answer.
//
// Now that the queries share a scale, "the best of those that cleared" and
// "the best" almost always agree. Almost: they still differ when a query is
// resident whose background could not be measured, so the loop stays.
//
// M20 keeps that loop for a PLAIN set and puts a second rule beside it. The
// ranking below is still over every query, because the frame line reports all
// of them and the order is what makes it readable; what changed is that the
// *decision* no longer comes off the top of that ranking when roles are set.
// See FGX_Q_PLAIN and led_two() for the measurements behind the split.
static void report(uint32_t n, const float *cos, uint32_t frame)
{
    float z[FGX_MAX_Q];
    for (uint32_t i = 0; i < nq; i++) z[i] = zscore(i, cos[i]);

    // The smoothing is applied here, before the ranking and the threshold and
    // the LED, so that what this line prints is what the board decided on. A
    // log showing the raw z beside a MATCH taken on a smoothed one is a log
    // that makes the firmware look broken - the reader can find frames over
    // threshold with no match and matches with no frame over it.
    //
    // Seeded at zero rather than at the first z, which costs about five frames
    // of climb after every reset. That is the honest seed: zero is "reads like
    // the empty room", the same thing the background says before it has seen
    // anything, and seeding from one frame would let a single startup transient
    // set the level for the next two seconds.
    if (bg_smooth)
        for (uint32_t i = 0; i < nq; i++) {
            zema[i] += (z[i] - zema[i]) * FGX_Z_EMA_A;
            z[i] = zema[i];
        }

    // M21's two axes, computed every frame whether or not anything is enrolled.
    // A run that never enrols still records the level after `led`, so the two
    // book runs that made this design could be re-scored off their own logs -
    // and so the next one can be, without knowing in advance what to ask of it.
    float lvl = 0.0f;
    for (uint32_t i = 0; i < nq; i++) lvl += z[i];
    lvl = nq ? lvl / (float)nq : 0.0f;
    float cz[FGX_MAX_Q];
    for (uint32_t i = 0; i < nq; i++) cz[i] = z[i] - lvl;

    if (enrol_want != FGX_ENROL_NONE) {
        if (enrol_left == 0) {                    // first frame of the window
            for (uint32_t j = 0; j < FGX_MAX_Q; j++) enrol_acc[j] = 0.0f;
            enrol_sq_acc  = 0.0f;
            enrol_acc_lvl = 0.0f;
            enrol_left    = FGX_ENROL_N;
        }
        for (uint32_t j = 0; j < nq; j++) {
            enrol_acc[j]  += cz[j];
            enrol_sq_acc  += cz[j] * cz[j];
        }
        enrol_acc_lvl += lvl;
        enrol_left--;
    }

    if (enrol_want != FGX_ENROL_NONE && enrol_left == 0) {
        const float w = (float)FGX_ENROL_N;
        const float alvl = enrol_acc_lvl / w;
        // The window is folded into the class HERE and not frame by frame, so
        // that abandoning a window part-way (a second digit during one - see
        // the key handler) leaves the class exactly as it was rather than
        // carrying a few frames of whatever was in shot when the operator
        // changed their mind.
        const uint32_t c = (uint32_t)enrol_want;
        const float nold = (float)(qref_vis[c] * FGX_ENROL_N);
        const float n    = nold + w;
        // The mean, moved rather than recomputed: nold is 0 on a first visit,
        // so this is the plain window average then and a weighted merge after.
        float mu2 = 0.0f;
        for (uint32_t j = 0; j < nq; j++) {
            qref[c][j] = (qref[c][j] * nold + enrol_acc[j]) / n;
            mu2 += qref[c][j] * qref[c][j];
        }
        qref_sqsum[c] += enrol_sq_acc;
        qref_vis[c]   += 1u;

        // E[|x|^2] - |mu|^2, clamped: the two terms are within a few ulp of
        // each other on a window that barely moved, and sqrtf of -1e-9 is a
        // NaN that would then poison every comparison below. Over more than
        // one visit this is no longer the window's own scatter but the spread
        // of every enrolled frame about the class centre, which is the
        // within-visit noise AND the between-visit staging variance added in
        // quadrature. That second term is the point.
        const float var = qref_sqsum[c] / n - mu2;
        qref_scat[c] = var > 0.0f ? sqrtf(var) : 0.0f;
        qref_on[c] = true;
        // The level is printed and not kept. Nothing decides on it any more
        // (#18) - it is here because it is free, it is what the two runs that
        // killed the level-based stage were diagnosed from, and a log that
        // stops recording a quantity cannot be asked about it later.
        printf("enrol     : %s, level %+.2f, scatter %.2f (%u frames, "
               "visit %u of %u)\n",
               qname[c], (double)alvl, (double)qref_scat[c],
               (unsigned)(qref_vis[c] * FGX_ENROL_N), (unsigned)qref_vis[c],
               (unsigned)FGX_ENROL_V);
        enrol_want = FGX_ENROL_NONE;

        // A new reference moves the presence scale, so the sticky state below
        // is measured against something that no longer exists. Drop it and let
        // the next frame re-enter through the high edge.
        m21_present = false;

        // What the enrolment actually bought, said once, at the moment it
        // becomes a rule. A DEGENERATE ENROLMENT IS INVISIBLE IN THE FRAME
        // LINES - the board goes on printing confident verdicts - and the
        // 2026-08-11 bench spent six minutes measuring a presence axis that was
        // identically zero. Two contrast queries built from the same two
        // phrases are exact negatives of each other ("an opened book~" read
        // -0.082 +-0.0061 against "a closed book~" +0.082 +-0.0061), so their
        // mean is 0 on every frame: `level` cannot move, centring subtracts
        // nothing, and both of M21's axes collapse to the raw pair. It still
        // beat ranking on that run, 75.0% held out against 65.0%, because the
        // reference is not at zero - but that is one of the two claims and the
        // other one was untestable, and nothing in the log said so.
        if (enrol_count() >= 2u) {
            float sep = INFINITY, orig = INFINITY;
            uint32_t no = 0, near_o = 0, near_a = 0, near_b = 0;
            for (uint32_t i = 0; i < nq; i++) {
                if (!qref_on[i]) continue;
                no++;
                // Distance from the ORIGIN of the centred space, which is not
                // an arbitrary point: c[] = 0 means every query moved together,
                // and that is what "nothing has changed since the background
                // was frozen" reads as. See the guard below.
                float o = 0.0f;
                for (uint32_t j = 0; j < nq; j++) o += qref[i][j] * qref[i][j];
                o = sqrtf(o);
                if (o < orig) { orig = o; near_o = i; }
                for (uint32_t k = i + 1u; k < nq; k++) {
                    if (!qref_on[k]) continue;
                    float s = 0.0f;
                    for (uint32_t j = 0; j < nq; j++) {
                        const float e = qref[i][j] - qref[k][j];
                        s += e * e;
                    }
                    s = sqrtf(s);
                    if (s < sep) { sep = s; near_a = i; near_b = k; }
                }
            }
            // The noise the closest pair has to clear, not the average one: a
            // steady reference does not rescue the twitchy one it is being
            // told apart from.
            const float scat = qref_scat[near_a] > qref_scat[near_b]
                             ? qref_scat[near_a] : qref_scat[near_b];
            // Whether the pair has been shown often enough for `scat` to mean
            // what the guard below reads it as. One visit measures stillness
            // only, and stillness is the term that does NOT decide runs.
            const uint32_t vmin = qref_vis[near_a] < qref_vis[near_b]
                                ? qref_vis[near_a] : qref_vis[near_b];
            printf("enrol     : %u classes, nearest pair %.2f apart, spread "
                   "%.2f (%.1fx over %u visit%s), absent beyond %.2f "
                   "(%.1f sep)\n",
                   (unsigned)no, (double)sep, (double)scat,
                   (double)(scat > 0.0f ? sep / scat : INFINITY),
                   (unsigned)vmin, vmin == 1u ? "" : "s",
                   (double)(FGX_ABSENT_TRIP * sep), (double)FGX_ABSENT_TRIP);
            if (!(sep > 0.05f))
                printf("            THE CLASSES ARE ON TOP OF EACH OTHER. "
                       "Whatever was in shot for the two\n"
                       "            captures was the same thing, or close "
                       "enough that this cannot separate them.\n");
            else {
            // ONE VISIT MEASURES ONLY HOW STILL IT WAS HELD, so nothing is
            // claimed either way until there are two. This comes before the bar
            // rather than after it: 08-17 09:33 passed at 2.71 on one visit and
            // scored 59.2%, and 08-17 11:26 read 0.9x on one visit and went on
            // to score 92.5%, so on one visit the ratio is wrong in both
            // directions and the only honest output is to say so.
            if (vmin < FGX_ENROL_V)
                printf("            Measured over %u visit%s, so this ratio "
                       "cannot see how far a class moves\n"
                       "            when it is staged again - which is what "
                       "decides the run. Show each class\n"
                       "            again ('1'/'2' on a later visit) to make "
                       "it mean something.\n",
                       (unsigned)vmin, vmin == 1u ? "" : "s");
            // THE BAR, AND IT ONLY EVER SAYS THE GOOD NEWS. See FGX_ENROL_SNR
            // for why this stopped being a rejection: three benches have
            // cleared it and all three scored above 91%, while below it the
            // eight runs measured so far span 92.5% to 47.5%.
            else if (sep >= FGX_ENROL_SNR * scat)
                printf("            This enrolment clears the bar: %.2f apart "
                       "against %.2f of spread within\n"
                       "            a class, over %u visits. The three benches "
                       "that have done that scored\n"
                       "            91.7%%, 96.7%% and 100.0%%. Three runs is "
                       "all it rests on.\n",
                       (double)sep, (double)scat, (unsigned)vmin);
            // AND BELOW THE BAR, DELIBERATELY NO ADVICE. The version that told
            // the operator to enrol again threw away 08-17 11:26, which scored
            // 92.5% - higher than one of the three that cleared the bar -
            // because it read 1.12 here. The numbers are still printed; what is
            // removed is the instruction that was wrong.
            else
                printf("            Below the bar (%.2f apart against %.2f of "
                       "spread), which on its own\n"
                       "            predicts nothing: benches below this line "
                       "have scored 92.5%% and 47.5%%.\n"
                       "            Look at the enrolment pictures before "
                       "re-enrolling on this number.\n",
                       (double)sep, (double)scat);
            // THE FAILURE MODE OF #18's RULE, said at the enrolment rather than
            // discovered in the log six minutes later - which is the lesson the
            // presence-span guard above it was written for. A reference sitting
            // near the origin cannot be fenced off from a still scene, because
            // a still scene IS the origin, so the board will call an untouched
            // desk that class and no radius can stop it. Measured: on the
            // 2026-08-11 bench 'a closed book' landed 0.49 sep from the origin
            // and its baseline was inseparable (AUC 0.624); on both 08-16 runs
            // the nearest reference sat 3.16 and 0.97 sep out and they worked.
            //
            // NOT PART OF THE CHAIN ABOVE any more. It used to hang off the end
            // of it, so an enrolment that cleared the ratio never got told its
            // reference sat on the origin - two unrelated failure modes sharing
            // one `else`. They are independent and both are worth saying.
            if (orig < 0.5f * sep)
                printf("            '%s' SITS %.2f SEP FROM THE ORIGIN, which is "
                       "where a scene identical to\n"
                       "            the frozen background lands. Presence cannot "
                       "separate the two, so an empty\n"
                       "            desk will read as that class. Re-freeze the "
                       "background ('N') on a scene\n"
                       "            that is actually empty, or enrol a class "
                       "that looks less like it.\n",
                       qname[near_o], (double)(orig / sep));
            }
        }
        stdio_flush();
    }

    uint32_t order[FGX_MAX_Q];
    for (uint32_t i = 0; i < nq; i++) order[i] = i;
    for (uint32_t i = 1; i < nq; i++)               // insertion sort; nq <= 6
        for (uint32_t j = i; j && z[order[j]] > z[order[j - 1]]; j--) {
            const uint32_t t = order[j]; order[j] = order[j - 1]; order[j - 1] = t;
        }

    printf("frame %5u :", (unsigned)frame);
    for (uint32_t k = 0; k < nq; k++) {
        const uint32_t i = order[k];
        printf("  %s %+.2f%s", qname[i], (double)z[i], z[i] >= qthr[i] ? "*" : "");
    }

    // The meter follows the *ranking*, not any one query: whatever is winning
    // is what the board is claiming to see, and that claim is what D1 reports.
    //
    // Sent here, at the end of the frame, because the link is idle here and the
    // two bytes cost microseconds against 900 ms. A frame that failed early
    // never reaches this line, so D1 holds its last colour rather than dropping
    // to green - which is the honest answer: nothing new was measured.
    //
    // A failure is reported and then dropped. The LED is the one part of this
    // program whose output nobody computes anything from, so a lost frame of it
    // is not worth disturbing the loop over; `!led` in the line says it went
    // wrong, and the next frame tries again.
    //
    // READ `!led` CAREFULLY: gh_led() has no return payload, so its response is
    // deferred, and GH_OK means the frame was queued rather than acknowledged. A
    // deferred failure surfaces on the following link call and outranks it, so
    // `!led` usually means "the *previous* transaction's response never arrived"
    // - the flag is real, its frame number is off by one, and a clean column is
    // therefore weaker evidence than it looks. What actually proves the LED path
    // works end to end is 'E': it forces the deferred failure to land somewhere
    // known and prints the status byte behind it.
    // M20's two stages, when the set carries both roles. The gate is the
    // weakest of the presence queries rather than the best, because a set with
    // two gates is asking for both to hold; with the one gate that ab.sh sends,
    // the distinction costs nothing and does not have to be revisited later.
    //
    // ref/rest are for the LED and only for it: ref is the first state query in
    // the order the host sent them, rest the best of the others. led_two() needs
    // a signed margin and lead-minus-runner cannot supply one - see its comment.
    int best = -1, lead = -1, runner = -1, gate = -1, ref = -1, rest = -1;
    bool open_gate = true;

    // M21 goes first, and only once it has two enrolled classes: one reference
    // cannot be nearest to anything, so below two there is no rule here at all
    // and the blocks after this run untouched. That is deliberate. Both rules
    // live in one boot, on one background, and the bench can flip between them
    // by pressing a digit - which is the only comparison worth having after two
    // benches disagreed with what the repo claimed.
    bool  m21 = false, m21_here = true;
    float m21_d = 0.0f, m21_run = 0.0f, m21_sep = 0.0f, m21_margin = 0.0f;
    float m21_lit = 1.0f;
    if (enrol_count() >= 2u) {
        m21 = true;

        float dc[FGX_MAX_Q];
        for (uint32_t i = 0; i < nq; i++) {
            if (!qref_on[i]) continue;
            float s = 0.0f;
            for (uint32_t j = 0; j < nq; j++) {
                const float e = cz[j] - qref[i][j];
                s += e * e;
            }
            dc[i] = sqrtf(s);
        }

        int   nearest = -1;
        float d1 = INFINITY, d2 = INFINITY;
        for (uint32_t i = 0; i < nq; i++) {
            if (!qref_on[i]) continue;
            if (dc[i] < d1)      { d2 = d1; d1 = dc[i]; nearest = (int)i; }
            else if (dc[i] < d2) { d2 = dc[i]; }
        }
        m21_d   = d1;
        m21_run = d2;

        // The hue's reference is the lowest-indexed enrolled class, fixed for
        // the whole run, so red always means the same thing. Reading it off the
        // winner is the M20 bug, and it is not a bug you can see in a log
        // without looking for it.
        int rc = -1;
        for (uint32_t i = 0; i < nq; i++) if (qref_on[i]) { rc = (int)i; break; }
        float dother = INFINITY;
        for (uint32_t i = 0; i < nq; i++)
            if (qref_on[i] && (int)i != rc && dc[i] < dother) dother = dc[i];
        m21_margin = dother - dc[rc];

        m21_sep = INFINITY;
        for (uint32_t i = 0; i < nq; i++) {
            if (!qref_on[i]) continue;
            for (uint32_t k = i + 1u; k < nq; k++) {
                if (!qref_on[k]) continue;
                float s = 0.0f;
                for (uint32_t j = 0; j < nq; j++) {
                    const float e = qref[i][j] - qref[k][j];
                    s += e * e;
                }
                s = sqrtf(s);
                if (s < m21_sep) m21_sep = s;
            }
        }

        // PRESENCE, AS A DISTANCE. m21_d is already the distance to the nearest
        // reference and m21_sep the closest two references sit to each other,
        // both computed just above for the classifier and the LED, so the whole
        // stage is one comparison. The frame is absent when it does not look
        // like anything the board was shown.
        //
        // WHAT THIS REPLACES, and why it is not a retune. Until 2026-08-16 this
        // was a fraction of the span between an enrolled empty scene and the
        // enrolled classes, entered at 0.50 and left at 0.15, and on paper it
        // was the best thing in the repo: 120/120 held out, 0/26 on the empty
        // desk. Both of those were the baseline scored against references taken
        // from the baseline. The first bench that put an empty desk AFTER the
        // enrolment - #15, and it needed a schedule change in host/cue.py to
        // exist at all - held 16/90 and 22/90, released once 24 and 18 frames
        // in, and never released again.
        //
        // Two faults, and only the second one is interesting. absent_lvl stored
        // the background freeze rather than an empty desk, because key '0's
        // window sat right after the freeze and so measured it against itself:
        // -0.46 and +0.16 on the two runs, which is what "nothing has changed"
        // reads as whatever is on the desk. And the level IS the common mode,
        // the exact term cz[] subtracts above to make the state stage immune to
        // the sensor's 1.5 z of warm-up in four minutes. The three empty
        // revisits read 0.21 / 0.32 / 0.44 of the span, monotonically, past a
        // leave edge of 0.15. Worst cases overlapped by 0.87 of a span, so no
        // pair of edges separated them; the axis was the problem.
        //
        // Replayed off the same two logs (tools/probe_reject.py), the rule
        // below at 2.0 sep holds 81/90 and 79/90 while keeping 118/120 and
        // 102/120 of the classes, AUC 0.956 and 0.909, and the same three
        // empty visits read 3.03 / 3.48 / 3.06 and 2.31 / 2.87 / 2.54 sep with
        // the MIDDLE visit highest - no trend, because there is no common mode
        // left in here to drift. #18.
        //
        // Two edges survive the change, for the reason they were added: the
        // single cut at 0.50 shut on 19 frames of "an opened book" when a later
        // visit drifted across it, and a stage that excludes a class it exists
        // to admit is M20's failure in a new costume. Enter absent only on
        // strong evidence (TRIP), return on weaker (STAY).
        if (m21_sep > 0.05f) {
            const float trip = FGX_ABSENT_TRIP * m21_sep;
            const float stay = FGX_ABSENT_STAY * m21_sep;
            m21_present = m21_present ? (m21_d <= trip) : (m21_d <= stay);
            m21_here = m21_present;
            m21_lit  = 1.0f - m21_d / trip;
            if (m21_lit < 0.0f) m21_lit = 0.0f;
            if (m21_lit > 1.0f) m21_lit = 1.0f;
        } else {
            // References on top of each other: there is no scale to measure a
            // radius in. The geometry guard has already said so in capitals,
            // and staying open keeps the rest of the run readable rather than
            // turning it into 300 lines of "nothing there".
            m21_here = true;
            m21_lit  = 1.0f;
        }

        // No threshold on the winner, for M20's reason, which survives it: once
        // presence says something is there, one of the enrolled classes IS the
        // answer. What "nearest" is measured against here is not a background,
        // it is the other references, and the gap printed below is what says
        // how much to believe it.
        open_gate = m21_here;
        if (m21_here) best = nearest;
    } else if (n_gate && n_class) {
        for (uint32_t i = 0; i < nq; i++) {
            if (qrole[i] == FGX_Q_GATE) {
                if (gate < 0 || z[i] - qthr[i] < z[gate] - qthr[gate]) gate = (int)i;
            } else if (qrole[i] == FGX_Q_CLASS) {
                if (lead < 0 || z[i] > z[lead]) { runner = lead; lead = (int)i; }
                else if (runner < 0 || z[i] > z[runner]) runner = (int)i;
                if (ref < 0) ref = (int)i;
                else if (rest < 0 || z[i] > z[rest]) rest = (int)i;
            }
        }
        open_gate = z[gate] >= qthr[gate];
        // No threshold on the winner. A CLASS query's z is not comparable to a
        // background - that is the whole finding - so the only thing its
        // absolute value could be tested against is the wrong quantity. Once
        // the gate says something is there, one of the classes IS the answer,
        // and a forced choice is the right shape for a forced-choice question.
        // The margin printed below is what says how much to believe it.
        if (open_gate) best = lead;
    } else {
        for (uint32_t k = 0; k < nq; k++)
            if (z[order[k]] >= qthr[order[k]]) { best = (int)order[k]; break; }
    }

    if (nq) {
        if (m21)
            led_ref(m21_margin, m21_sep, m21_lit);
        else if (n_gate && n_class && ref >= 0)
            led_two(z[ref] - (rest >= 0 ? z[rest] : z[ref]), qthr[ref],
                    z[gate], qthr[gate]);
        else
            led_map(z[order[0]], qthr[order[0]]);
        wd_stage(FGX_ST_LED, frame);
        const gh_err_t le = gh_led(led_r, led_g);
        printf("   led %3u/%3u h%.2f", (unsigned)led_r, (unsigned)led_g,
               (double)led_heat);
        if (m21 || (n_gate && n_class)) printf(" b%.2f", (double)led_lit);
        // The level goes here, after `led`, and not in the score column - the
        // log parsers read that column as "name, number" pairs up to the word
        // `led`, so a "lvl +0.12" in it would arrive in tools/score_cue.py as a
        // seventh query with a plausible score. After `led` it is free.
        printf(" lvl%+.2f", (double)lvl);
        // THE DECISION VARIABLE GOES IN THE LOG. The whole of #15 was that the
        // presence stage could not be scored after the fact, and half of why
        // was that the log recorded the verdict and not the quantity it was
        // reached from. `d` is the distance to the nearest reference in units
        // of sep, so it is directly comparable across runs and rooms, and the
        // edges it is tested against are constants any scorer can read here.
        if (m21 && m21_sep > 0.0f) printf(" d%.2f", (double)(m21_d / m21_sep));
        if (le != GH_OK) printf(" !led");
    }
    wd_stage(FGX_ST_PRINT, frame);

    if (best >= 0) {
        printf("   MATCH %s (cos %.3f", qname[best], (double)cos[best]);
        // The margin over the runner-up, not the z. Under the two-stage rule
        // the z is the wrong number to quote for a MATCH - it is routinely
        // negative on a frame the board is right about - and the margin is the
        // one that carried the 176/180.
        if (m21) printf(", nearer by %.2f", (double)(m21_run - m21_d));
        else if (runner >= 0) printf(", by %.2f", (double)(z[best] - z[runner]));
        printf(")\n");
    } else if (!open_gate) {
        printf("   - (nothing there)\n");
    } else {
        printf("   -\n");
    }
    stdio_flush();

    // Scored first, folded in second. A frame that contributed to its own
    // background would be measuring itself against a mean it had just pulled
    // toward itself, which costs real signal at small counts - at frame 1 it
    // would halve it.
    bg_update(cos);
    if (bg_seen == 8u || (bg_seen % 100u) == 0u) bg_print();
    (void)n;
}

// ---- the operating point ----------------------------------------------------
//
// 150 MHz was never a decision. It is the stock rate m8 came up at and m9
// inherited from m8; the clock ladder only ever got built into m6 and m7, and
// those two are where the top of it was measured. 280 MHz system / 140 MHz link
// is bit-exact there on the same M16 images this file runs - m6 at 2048/2048
// over three boots, m7 at 304 / 304 / 303 ms a frame over three - and the wire
// is what the clock buys, because the frame is dominated by weight traffic over
// it. On this path that was 802 ms at 150 and is 420 at 320: 1.25 fps to 2.38.
//
// **320, NOT 280, AND THE DIFFERENCE WAS MEASURED HERE.** m6 and m7 validated
// 280; issue #1 then ran 280, 300, 320, 332 and 340 on this path, with the
// camera, and the answer is not the one the other two harnesses imply:
//
//   280 -> 454 ms      300 -> 455 ms      320 -> 420      332 -> 420
//   340 -> the link never answers, at three data lines or at one, twice
//
// The inference frame scales perfectly with the clock - the probe reads 400,
// 374, 350, 338 ms against 400 x 280/f - and the appliance frame does not. It
// steps. 280 and 300 share a number, 320 and 332 share the next one down, and
// the step is 34 ms, which is about one frame of a ~29 fps sensor. The camera
// finishes when the camera finishes, so anything that gets faster inside one of
// those cells buys exactly nothing. See #10: that is why the road to 300 ms is
// overlapping the capture, not raising the clock.
//
// So 320 is where the curve stops paying. 332 costs 12 MHz for 0 ms and puts
// the next step at 340, where the link is dead - and M17's "340 is bit-exact at
// 1.25 V" is an m6 result that does not transfer here, which is itself worth
// remembering before quoting a rate one harness measured at another.
//
// **"332 COSTS 12 MHz FOR 0 ms" WAS THE GRID TALKING, AND #13 RE-RAN IT.** With
// the capture overlapped there is no grid to hide inside, and the same four
// rates - 150 frames each, one image per rate differing in FGX_SYS_KHZ alone -
// read by the board's own wall clock:
//
//   280 -> 331 ms      300 -> 310 ms      320 -> 291      332 -> 281
//
// encode x f is 84,840 / 84,900 / 84,800 / 84,992, so the encode is 1/f to
// within 0.2% and the frame now tracks it, with ~10 ms of LED and CDC that
// scales with nothing. 332 is worth a real 10 ms and it is bit-exact - m7
// passes all six modes of both link configurations at 332/166, m9 ran 151 of
// 151 good.
//
// IT STILL DOES NOT SHIP, AND THAT IS NOW A DECISION RATHER THAN A TIE. 10 ms
// is 3.4%, and spending it puts the operating point 8 MHz below a rate where
// the link stops answering deterministically, while #9 (the board drops off
// USB) and #12 (a byte lost on the camera bus at 280/140 and never at 150/75)
// are both open and both are unexplained flakiness on the fast side. One 320
// run in the #13 session dropped off USB. Nothing here forbids 332; the cache
// variable builds it in one line if the margin question ever closes.
//
// The rail stays at what the ladder gives 320, which is 1.25 V. The sweep found
// 320 clean at 1.20 as well, and 280 clean all the way down to 1.15 - but 320
// *wedges* at 1.15, so 1.20 is one step from the cliff and 1.25 is two, for the
// same 420 ms. Margin is the only thing the extra 0.05 V is being bought with
// and the only thing it is being bought for. -DFGX_CORE_MV=1200 is there if a
// power budget ever wants that step back.
//
// Voltage up before frequency up, frequency down before voltage down. That
// ordering is from m7.c and it is not stylistic: under-volting the core does
// not print an error, it stops the board - and see main(), which now brings USB
// up first so that when it does, the board can still be re-flashed.
#ifndef FGX_SYS_KHZ
#define FGX_SYS_KHZ 320000
#endif

// 0 = the ladder below decides. A number is a bench override, in millivolts,
// and it goes through the same "voltage up before frequency up" path rather
// than around it - an override that skipped the ordering would be a way of
// browning out the core from a build flag.
#ifndef FGX_CORE_MV
#define FGX_CORE_MV 0
#endif
#if FGX_CORE_MV != 0 && FGX_CORE_MV != 1100 && FGX_CORE_MV != 1150 && \
    FGX_CORE_MV != 1200 && FGX_CORE_MV != 1250 && FGX_CORE_MV != 1300
#error "FGX_CORE_MV must be 0, 1100, 1150, 1200, 1250 or 1300"
#endif

static enum vreg_voltage sys_rail = VREG_VOLTAGE_DEFAULT;

static const char *volt_name(enum vreg_voltage v)
{
    switch (v) {
    case VREG_VOLTAGE_1_10: return "1.10";
    case VREG_VOLTAGE_1_15: return "1.15";
    case VREG_VOLTAGE_1_20: return "1.20";
    case VREG_VOLTAGE_1_25: return "1.25";
    case VREG_VOLTAGE_1_30: return "1.30";
    default:                return "?";
    }
}

// Returns the rate actually reached, which is not always the one asked for:
// check_sys_clock_khz() counts the feedback divider down from 320 and some
// rates have no exact solution. 150000 always does, and is the fallback.
static uint32_t sys_clock_bring_up(uint32_t khz)
{
    const enum vreg_voltage want =
#if   FGX_CORE_MV == 1300
        VREG_VOLTAGE_1_30;
#elif FGX_CORE_MV == 1250
        VREG_VOLTAGE_1_25;
#elif FGX_CORE_MV == 1200
        VREG_VOLTAGE_1_20;
#elif FGX_CORE_MV == 1150
        VREG_VOLTAGE_1_15;
#elif FGX_CORE_MV == 1100
        VREG_VOLTAGE_1_10;
#else
        khz > 220000 ? VREG_VOLTAGE_1_25 :
        khz > 150000 ? VREG_VOLTAGE_1_20 :
                       VREG_VOLTAGE_DEFAULT;
#endif
    if (want > sys_rail) {
        vreg_set_voltage(want);
        sleep_ms(10);
        sys_rail = want;
    }
    if (khz != 150000u && set_sys_clock_khz(khz, false)) {
        sleep_ms(50);
        return khz;
    }

    set_sys_clock_khz(150000, true);   // frequency first coming down
    sleep_ms(50);
    if (sys_rail != VREG_VOLTAGE_DEFAULT) {
        vreg_set_voltage(VREG_VOLTAGE_DEFAULT);
        sleep_ms(10);
        sys_rail = VREG_VOLTAGE_DEFAULT;
    }
    return 150000u;
}

int main(void)
{
    // PARK THE PSRAM'S CHIP SELECT BEFORE ANYTHING ELSE. THIS IS ISSUE #9.
    //
    // GPIO0 is U1's chip select - the APS1604M PSRAM sharing the RP2354A's QSPI
    // bus with the in-package flash, on QMI CS1 (docs/pinmap.md). CS is active
    // low, PADS_BANK0_GPIO0_RESET is 0x116 so the pad comes out of reset with
    // PDE set and PUE clear, and m9 does not link hardware_psram - it has no
    // .psram_load or .psram_noload to place - so the QMI never takes the pin and
    // nothing else in the tree touches it either: the firmware's pins start at
    // GPIO1. The pull-down therefore holds U1 SELECTED for the entire run, which
    // is orders of magnitude past the part's ~8 us tCEM, watching every read the
    // flash answers and free to decide one of them was addressed to it and drive
    // SD0..3 back.
    //
    // That is #9, and 2026-08-16 caught it in the act. At the outage the flash
    // stops answering: `picotool info` says "Program Information: none", three
    // reads of the same 4 KB come back as three different high-entropy strings,
    // and `picotool verify` fails - then a VBUS cycle, and the SAME flash
    // verifies OK against the SAME image. Nothing was ever corrupted; the bus
    // was jammed, and only removing the 5 V clears it, because only that power
    // cycles U1. It also explains both shapes the outage takes, since XIP dies
    // instantly (D1 goes dark mid-frame) and the watchdog's reboot 8 s later
    // hands a bootrom that cannot read the image either: 280 MHz frame 1554 fell
    // through to USB boot, 150 MHz frame 1478 did the same, and the run before
    // that never got its pull-up back up at all.
    //
    // Two clocks, 76 frames apart, is also the answer to whether this was the
    // clock: it is not. Nor is it the rail - the meter read 5.09 V and 0.16 A
    // through the failure, with no sag and no spike.
    //
    // Driving it high here rather than linking hardware_psram is deliberate: m9
    // has no use for the 2 MB, and psram_detect_size() returns 0 on this board
    // for reasons docs/pinmap.md still calls unexplained, so initialising a part
    // we do not need would buy a new way to fail. Value before direction, so the
    // pin never drives low on its way up. It cannot be first - this code is
    // itself running from XIP, so the window between the pad leaving isolation
    // and this line is unavoidable - but it takes the exposure from a whole run
    // down to a few milliseconds of boot.
    gpio_init(PICO_PSRAM_CS_PIN);
    gpio_put(PICO_PSRAM_CS_PIN, 1);
    gpio_set_dir(PICO_PSRAM_CS_PIN, GPIO_OUT);

    // USB FIRST, THEN THE CLOCK, AND THE ORDER IS A RECOVERY PATH.
    //
    // This used to raise the clock before stdio_init_all(), which is fine right
    // up until the operating point does not work: 320 MHz at 1.15 V wedges the
    // core, and a board that wedges before USB exists cannot be told to enter
    // BOOTSEL, cannot be re-flashed, and comes back only on the PRG-GND strap -
    // i.e. on somebody's hands and a cable. That happened on 2026-08-15, from a
    // one-line build flag, and it is the failure the rail sweep exists to find,
    // so finding it must not cost the board.
    //
    // Bringing USB up first costs one enumeration at the stock rate and buys a
    // window - the grace below - in which `picotool reboot -f -u` always works,
    // whatever the point that follows does. m7 has raised the clock with USB
    // already up on every rung of its ladder since M7a, so the ordering itself
    // is not new here; only the reason is.
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== M9: fpga-open-vocab - describe it, the board spots it ===\n\n");

    // The grace. Long enough for a host that is watching to get a reset request
    // in, short enough to be invisible against a two-minute run - and it is the
    // last moment this binary is guaranteed to be alive, so it says so.
    const uint32_t want_khz = (uint32_t)FGX_SYS_KHZ;
    printf("clock     : %u MHz requested; BOOTSEL is reachable for 1.5 s\n",
           (unsigned)(want_khz / 1000u));
    stdio_flush();
    sleep_ms(1500);

    const uint32_t sys_khz = sys_clock_bring_up(want_khz);

    // Every timing this run prints is a timing at this operating point, so it
    // goes in the log before anything else does. A frame time with no rate
    // beside it is not a measurement of anything.
    printf("clock     : %u MHz system, core %s V%s%s\n",
           (unsigned)(sys_khz / 1000u), volt_name(sys_rail),
           FGX_CORE_MV ? "  (rail pinned by the build, not by the rate)" : "",
           sys_khz == want_khz ? "" : "  (FALLBACK - requested rate refused)");
    reset_report();
    wd_report_last();

    // Armed HERE, not at the frame loop, and the 2026-08-10 17:58 wedge is why:
    // the board went silent with the watchdog still disarmed, sat that way for
    // an hour, and took a uhubctl at the wall. Every stage from this line to the
    // end of the run is now covered. Not one line earlier, though - above this
    // is the wait for stdio_usb_connected(), and a board powered up with no host
    // attached is not hung, it is waiting, and rebooting it every 8 s would be
    // the wrong answer to that.
    wd_stage(FGX_ST_BITSTREAM, 0);
    watchdog_enable(FGX_WD_MS, 1);

    printf("waiting for a bitstream on USB CDC (host/demo.py)");
    stdio_flush();

    const size_t blen = ft_recv_bitstream(0);
    if (!blen) {
        printf("\nRESULT : FAIL - no usable bitstream\n");
        park();
    }

    wd_stage(FGX_ST_FPGA, 0);
    fpga_config_pins_init();
    const int cerr = fpga_configure(ft_arena(), blen);
    printf("configure : %s   CDONE=%d nSTATUS=%d\n",
           fpga_strerror(cerr), fpga_done(), fpga_nstatus());
    if (cerr != FPGA_OK) {
        printf("\nRESULT : FAIL - the tile never came up\n");
        park();
    }

    fpga_release_link_pins();
    gemm_host_init();
    w1_start();

    {
        uint32_t md; bool rv, iv;
        if (gh_crc_sniffer(&md, &rv, &iv))
            printf("crc       : DMA sniffer, calc=%u out_rev=%d out_inv=%d\n",
                   (unsigned)md, (int)rv, (int)iv);
        else
            printf("crc       : software - no sniffer mode matched gw_crc()\n");
    }

    fgx_model_t m;
    if (!fgx_open(&m, fgx_weights, (size_t)(fgx_weights_end - fgx_weights))) {
        printf("\nRESULT : FAIL - weights.bin is malformed\n");
        park();
    }
    const char *why = ft_init(&m);
    if (why) {
        printf("\nRESULT : FAIL - %s\n", why);
        park();
    }
    // Which weights this board is *actually* running, said out loud.
    //
    // The student now emits into SigLIP 2 SO400M's space squeezed to 512 by a
    // frozen PCA. The incumbent emitted into CLIP ViT-B/16's. Both are 512-d, so
    // a host that encodes its text queries with the wrong one gets a dot product
    // that succeeds and means nothing - there is no shape to catch it. model/
    // export.py writes this same crc32 into export.json beside the blob, and
    // host/demo.py refuses to open the query port unless the two agree.
    //
    // The sidecar alone could not do this. It says which space the *file* is;
    // only the board can say which space the *flash* is, and a stale flash is
    // the failure that costs an afternoon.
    const size_t w_len = (size_t)(fgx_weights_end - fgx_weights);
    const absolute_time_t crc_t0 = get_absolute_time();
    wd_stage(FGX_ST_MODEL, 0);
    const uint32_t w_crc = ft_crc32(fgx_weights, w_len);
    const int64_t crc_us = absolute_time_diff_us(crc_t0, get_absolute_time());
    printf("model     : %u layers, %u-d embedding, %u B/buffer\n",
           (unsigned)ft_nlayer(), (unsigned)m.hdr->embed_dim,
           (unsigned)m.scratch);
    printf("weights   : %u B, crc32=0x%08X  (%lld ms to hash)\n",
           (unsigned)w_len, (unsigned)w_crc, (long long)(crc_us / 1000));

    const uint32_t dim = m.hdr->embed_dim;
    if (dim > FGX_DIM) {
        printf("\nRESULT : FAIL - the model emits %u-d and the query set holds "
               "%d-d\n", (unsigned)dim, FGX_DIM);
        park();
    }

    ft_set_mode(true, true, true, true, true);
    ft_set_sweep(false);
    // M15's epilogue, and #14 (2) found it switched off. Nothing argued for
    // that: the appliance was simply never moved over when M15 shipped, and
    // because the only breakdown in the tree predated M15 the cost never showed
    // up anywhere. m7's ladder at 320/160, one boot, six modes, both link
    // configurations, all bit-exact: 349 ms with the tile draining int32 and
    // 270 ms with it draining codes. DRAIN is 72 ms of the frame and becomes 19,
    // the wire carries 10.244 MB instead of 13.259, and the decode that core 1
    // was doing behind it drops from 66 ms to 17.
    //
    // Safe by construction rather than by assertion: probe() below runs the
    // whole test vector through the tile in this mode and refuses to start the
    // demo unless all 512 embedding floats match encoder_fast. The accumulator
    // sweep that guards the MAC array is unaffected - see ft_set_rq(), a sweep
    // pass overrides this back to int32.
    ft_set_rq(true);

    // --- check 1: the reference ---------------------------------------------
    wd_stage(FGX_ST_REFERENCE, 0);
    printf("\nreference : encoder_fast on the flash test vector");
    stdio_flush();
    const void *testvec = (const void *)(fgx_testvec + 12);
    const uint64_t t0 = time_us_64();
    {
        const void *src = testvec;
        void *dst = ft_arena();
        for (uint32_t i = 0; i < ft_nconv(); i++) {
            const bool as_float = fgx_emits_float(&m, i);
            fgx_conv_fast(&m, &m.desc[i], src, dst, as_float, ft_col(), true);
            src = dst;
            dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                              : (void *)ft_arena();
        }
        ft_pool_head((const float *)src, ref_embed);
    }
    printf("  (%u ms)\n", (unsigned)((time_us_64() - t0) / 1000u));

    // --- check 2: the wire ---------------------------------------------------
    printf("\nlink      : probing the wire by running the whole test vector "
           "over it\n");
    wd_stage(FGX_ST_LINK, 0);
    const unsigned w = probe(&m, testvec);
    if (!w) {
        printf("\nRESULT : FAIL - neither width reproduced the reference, so "
               "the tile and this driver disagree about the wire\n");
        park();
    }
    printf("link      : configuration %s, %u forward data line%s, %.1f MHz\n",
           w == 3 ? "C" : "A", w, w == 1 ? "" : "s",
           clock_get_hz(clk_sys) / 2e6);

    // --- check 3: the camera -------------------------------------------------
    printf("\n");
    wd_stage(FGX_ST_CAMERA, 0);
    if (!ft_acquire(m.hdr->in_scale)) {
        printf("\nRESULT : FAIL - no camera. There is no demo without one: a "
               "loop over the flash test vector would hold the same opinion "
               "about the same picture forever\n");
        park();
    }

    // --- check 4: the query set ----------------------------------------------
    // Before the loop rather than inside it, because a frame line with nothing
    // to compare against is not a shorter answer, it is a different program.
    printf("\nqueries   : waiting for a query set on USB CDC (host/demo.py)");
    stdio_flush();
    while (!nq) {
        wd_stage(FGX_ST_QWAIT, 0);
        const int c = poll_host(dim, 1000000);
        if (c == 0)   { printf("."); stdio_flush(); }
        if (c == 'B') { printf("\nbootsel\n"); sleep_ms(50); reset_usb_boot(0, 0); }
        if (c == 'R') { printf("\nreboot\n");  sleep_ms(50); watchdog_reboot(0, 0, 0); }
    }

    // --- the loop ------------------------------------------------------------
    printf("\nloop      : capture, %u convs on the T8, pool and head, then one "
           "cosine per query. 'B' for BOOTSEL, 'R' to restart,\n"
           "            'P' to dump the next frame for host/cam.py, 'V' to dump "
           "its 512 floats for host/caption.py,\n"
           "            'E' to provoke a fault so D1 shows its fault display, "
           "'H' to freeze/unfreeze the background,\n"
           "            'S' to switch the spread between this room's and "
           "COCO's, 'W' to hang on purpose and see\n"
           "            the watchdog name the stage, 'C' to stall the camera "
           "bus on purpose and see it cost one frame,\n"
           "            'U' to drop off USB on purpose and watch it come back, "
           "'I' to drop off and refuse to,\n"
           "            'N' to forget it and learn it again from now. 'P' and "
           "'V' together describe the same frame.\n"
           "            'O' closes the timing window, prints it and flips the "
           "capture between overlapped and serial;\n"
           "            'D' does the same and flips the trigger between late "
           "and at-the-collect, which is #14's A/B.\n",
           (unsigned)ft_nconv());
    printf("            scores are z against this room's background, ranked; "
           "'*' means over its threshold.\n");
    printf("            '1'..'%u' enrol the next %u frames as that class, and "
           "press again on a LATER visit\n"
           "            to fold a second one in - %u visits is what makes the "
           "enrolment guard mean\n"
           "            something. Two classes in and the board decides by "
           "nearest reference instead of\n"
           "            by threshold, and calls a frame absent when it is "
           "further than %.1f sep from every\n"
           "            one of them - see M21. The empty scene is not enrolled "
           "and '0' does nothing; see #18.\n",
           (unsigned)FGX_MAX_Q, (unsigned)FGX_ENROL_N, (unsigned)FGX_ENROL_V,
           (double)FGX_ABSENT_TRIP);
    if (bg_hold)
        printf("            The first %u frames set the baseline and it is then "
               "frozen, so anything left in\n"
               "            shot KEEPS its score. Point the camera at the empty "
               "scene for those frames.\n", (unsigned)bg_tau);
    else
        printf("            The baseline keeps tracking over the last %u frames, "
               "so anything left in shot\n"
               "            joins it and fades. This measures change, not "
               "presence.\n", (unsigned)bg_tau);
    printf("            Send a new set at any time to re-query - that resets the"
           " baseline too.\n\n");
    stdio_flush();

    wd_stage(FGX_ST_CAPTURE, 0);

    // From here to the end of the run, and not before: ft_acquire()'s exposure
    // ramp wants each capture to be one self-contained thing. See frame.h - the
    // sensor now exposes underneath encode() instead of in front of it, which
    // costs no memory and, since #14 put the trigger at the end of the compute
    // rather than the start, 59 ms of freshness rather than 290.
    bool overlap = true;
    ft_pipeline(overlap);

    uint32_t n = 0, cur = 0, good = 0, pinned = 0;
    // `timed` and not `good`: a dropped frame does not reach the accumulators
    // but its 200 ms does land in the wall clock, so dividing the sums by `good`
    // and the wall by `good - 1` was quietly comparing two different windows.
    // 'O' zeroes all five together, which is what makes a back-to-back A/B in
    // one boot mean anything.
    uint32_t timed = 0;
    uint64_t sum_us = 0, sum_wall_us = 0, sum_wait_us = 0, sum_enc_us = 0;
    uint64_t sum_age_us = 0;
    uint64_t last_acc_us = 0;
    bool said_sticky = false, said_pinned = false, want_pic = false;
    bool want_emb = false;

    for (;;) {
        // #9, and at the top so it also runs on the two `continue` paths below:
        // a board that drops off the bus while its camera is failing is exactly
        // the run that needs the watch most.
        usb_watch(n);

        wd_stage(FGX_ST_CAPTURE, n);
        if (!ft_capture(m.hdr->in_scale)) {
            printf("frame %5u : no usable frame off the camera\n", (unsigned)n);
            stdio_flush();
            sleep_ms(200);
            n++;
            continue;
        }
        uint32_t exp_us, rd_us;
        ft_cap_stats(NULL, &exp_us, &rd_us);

        // Here and nowhere else. ft_capture() leaves the RGB565 in the arena and
        // encode() writes layer 0 straight over it, so this is the only point in
        // the loop where the bytes the scores below are about still exist. The
        // format is cam_probe.c's, so host/cam.py renders it unchanged - and it
        // has to be the *same* frame rather than a second capture, because "what
        // the camera sees" and "what the tile was handed" differing is exactly
        // the class of fault a picture is being asked to rule out.
        if (want_pic) {
            want_pic = false;
            wd_stage(FGX_ST_DUMP_PIC, n);
            int mn[3];
            ft_cap_stats(mn, NULL, NULL);
            printf("\nsnapshot  : frame %u, mean RGB %d %d %d\n",
                   (unsigned)n, mn[0], mn[1], mn[2]);
            cam_dump_frame("m9", ft_arena(),
                           (size_t)FT_FRAME_W * FT_FRAME_H * 2u,
                           FT_FRAME_W, FT_FRAME_H);
            stdio_flush();
        }

        wd_stage(FGX_ST_ENCODE, n);
        const char *stopped = encode(ft_frame(), emb[cur]);
        if (stopped) {
            // Survivable, as in m8: the engine returns rather than parking, the
            // next capture is a fresh frame, and a demo that drops one frame and
            // carries on beats one that stops.
            printf("frame %5u : %s\n", (unsigned)n, stopped);
            stdio_flush();
            sleep_ms(200);
            n++;
            continue;
        }

        // M13. The 512 floats themselves, in the same BEGIN/END/base64 envelope
        // cam_dump_frame() already uses for pixels - so the emitter is the one
        // that M8 verified rather than a second one written to look like it.
        // Deferred by a frame exactly as 'P' is, and dumped here, after the
        // encode whose input 'P' dumped: press both and the image and the
        // vector describe the same frame n. That pairing is the whole point -
        // what this is for is reading the embedding back in words next to the
        // picture that produced it - and it holds only because the keys are
        // drained in a loop below rather than one per frame.
        if (want_emb) {
            want_emb = false;
            wd_stage(FGX_ST_DUMP_EMB, n);
            printf("\nembedding : frame %u, %u floats\n", (unsigned)n,
                   (unsigned)dim);
            cam_dump_frame("m9emb", (const uint8_t *)emb[cur],
                           (size_t)dim * sizeof(float), dim, 1u);
            stdio_flush();
        }

        // wait_us, not exp_us. They were the same number for as long as the
        // capture was serial - the poll started the instant the trigger did -
        // and cam.h explains why they stop being the same once the trigger is
        // issued a compute early. exp_us is now an elapsed time that spans
        // encode(); adding it to encode() would count the same 350 ms twice.
        const uint32_t wait_us = ft_cap_wait_us();
        sum_us      += ft_frame_us() + wait_us + rd_us;
        sum_enc_us  += ft_frame_us();
        sum_wait_us += wait_us;
        timed++;

        // THE FRAME IS NOW TIMED TWICE, AND THAT IS THE POINT. Every ms/frame
        // figure this project has ever published is `sum_us` - a sum of three
        // measured parts, never a clock - and a sum of parts is precisely the
        // thing an overlap can flatter by moving work out of the parts instead
        // of out of the frame. So: the interval between successive arrivals
        // here, which is one whole trip round the loop including the host poll
        // and everything the parts do not name. It is deliberately not reset by
        // the `continue` paths above, so a dropped frame's 200 ms and a 'P'
        // snapshot's extra capture both land in it and inflate it - honest, and
        // the reason to read it beside sum_us rather than instead of it.
        {
            const uint64_t now = time_us_64();
            if (last_acc_us) sum_wall_us += now - last_acc_us;
            last_acc_us = now;
        }

        wd_stage(FGX_ST_SCORE, n);
        float score[FGX_MAX_Q];
        for (uint32_t i = 0; i < nq; i++)
            score[i] = (float)cosine(emb[cur], qvec[i], dim);
        report(nq, score, n);

        // #14. Here and not earlier: report() is where the LED changes, so this
        // is the instant the answer becomes visible and the only honest place to
        // ask how old the photons behind it are. Everything else in this loop
        // measures throughput; this is the one number that measures latency, and
        // the two move in opposite directions under the overlap - which is
        // exactly why the overlap needed it and shipped without it.
        sum_age_us += ft_cap_age_us();

        // The liveness check M8c had to learn the hard way. Two consecutive
        // frames off a live sensor are never bit-identical - there is always
        // noise - so a cosine of exactly 1.0 means the tile is being handed the
        // same bytes twice, and every score above is an opinion about a picture
        // the camera did not take.
        if (good) {
            pinned = cosine(emb[cur], emb[cur ^ 1], dim) > 0.999999 ? pinned + 1 : 0;
            if (pinned >= 10 && !said_pinned) {
                said_pinned = true;
                printf("            ^ the last 10 frames were bit-identical. The "
                       "capture is not reaching the tile,\n"
                       "              so these scores describe one frozen frame: "
                       "cold-power-cycle the board.\n");
                stdio_flush();
            }
        }

        const uint8_t st = ft_status();
        if (!said_sticky && (st & (GH_ST_UNDERRUN | GH_ST_BADFRAME))) {
            said_sticky = true;
            printf("            sticky link fault (%02x) at frame %u - the "
                   "embeddings after this are not to be trusted\n",
                   st, (unsigned)n);
            stdio_flush();
        }

        cur ^= 1;
        n++;
        good++;

        wd_stage(FGX_ST_POLL, n);
        int c = poll_host(dim, 0);
        // Drained in a loop rather than one key per frame, and that is the
        // whole correctness of the P/V pairing. poll_host() returns on the
        // first key it recognises, so `if (c=='P') { ...; continue; }` leaves a
        // 'V' sent in the same breath sitting in the buffer until the next
        // iteration - and demo.py --emb writes "PV" as one call. Measured
        // before this loop existed: the image was frame 26 and the vector was
        // frame 27, quietly off by one, with both dumps looking perfectly
        // healthy and their CRCs matching. Whatever key ends the burst falls
        // through to the handlers below.
        while (c == 'P' || c == 'V') {
            if (c == 'P') want_pic = true;
            else          want_emb = true;
            c = poll_host(dim, 0);
        }
        if (c == 0 && (want_pic || want_emb)) continue;
        // M12's two background controls. Both print the state they landed in
        // rather than the state they were asked for, because 'H' pressed during
        // warm-up does nothing visible for a while and a log that says "frozen"
        // when bg_n is still climbing would be a lie the reader cannot check.
        if (c == 'H') {
            bg_hold = !bg_hold;
            printf("\nbackground: now %s, after %u frames of %u\n",
                   bg_hold ? (bg_n >= bg_tau ? "FROZEN" : "warming up, then frozen")
                           : "TRACKING",
                   (unsigned)bg_n, (unsigned)bg_tau);
            stdio_flush();
            continue;
        }
        // M19's. Toggling the scale mid-run is the cheapest demonstration there
        // is that it matters: the same frozen background, the same scene, and
        // the z column moves by the ratio the two spreads happen to have. It
        // prints that ratio for query 0 rather than making the reader divide.
        if (c == 'S') {
            bg_room_sd = !bg_room_sd;
            const float room = bg_n >= 2u
                ? sqrtf(qm2[0] / (float)(bg_n - 1u)) : 0.0f;
            printf("\nbackground: spread now %s",
                   (bg_room_sd && bg_n >= FGX_BG_SD_MIN_N) ? "the ROOM's" : "COCO's");
            if (nq && bg_n >= FGX_BG_SD_MIN_N && room > 0.0f)
                printf(" - %s reads +-%.4f here against COCO's +-%.4f, so every "
                       "z scales by %.1fx", qname[0], (double)room,
                       (double)qsd[0], (double)(qsd[0] / room));
            else if (bg_room_sd)
                printf(" - but only %u frames are in the estimate and it takes "
                       "%u, so COCO's is still in use",
                       (unsigned)bg_n, (unsigned)FGX_BG_SD_MIN_N);
            printf("\n");
            stdio_flush();
            continue;
        }
        // #10. Close the window that was running, print it, flip the overlap and
        // start a fresh one - so a run can be read as "these N frames serial,
        // then these N overlapped", on one boot, one build and one scene.
        //
        // THE RE-ARMED CAPTURE SURVIVES THE FLIP. Turning overlap off does not
        // discard a frame already in the ArduChip's FIFO: the next ft_capture()
        // still collects it, and only the one after that goes back to
        // trigger-then-wait. So the first frame of a serial window is fast and
        // the number is a frame per window too generous - which is why the
        // window is quoted over tens of frames and not over the boundary.
        if (c == 'O') {
            report_cost(timed, sum_us, sum_enc_us, sum_wait_us, sum_wall_us,
                        sum_age_us, overlap, "\noverlap   : ");
            overlap = !overlap;
            ft_pipeline(overlap);
            printf("            capture is now %s the compute. Counters "
                   "zeroed; the next window starts at frame %u.\n",
                   overlap ? "OVERLAPPED with" : "SERIAL with",
                   (unsigned)(n + 1));
            timed = 0;
            sum_us = sum_enc_us = sum_wait_us = sum_wall_us = sum_age_us = 0;
            last_acc_us = 0;
            stdio_flush();
            continue;
        }
        // #14, and the same shape as 'O' for the same reason: the only before
        // worth quoting is one taken on this boot and this scene. It moves the
        // trigger back to where it sat before the schedule existed, which changes
        // the age and should change nothing else - the frame time is the same
        // work either way, and if it is not, the schedule is costing throughput
        // and that is the finding.
        if (c == 'D') {
            report_cost(timed, sum_us, sum_enc_us, sum_wait_us, sum_wall_us,
                        sum_age_us, overlap, "\nre-arm    : ");
            const bool eager = !ft_cap_is_eager();
            ft_cap_eager(eager);
            printf("            the trigger now goes out %s. Counters zeroed; "
                   "the next window starts at frame %u.\n",
                   eager ? "AT THE COLLECT, an encode early (pre-#14)"
                         : "LATE, on the schedule (#14)",
                   (unsigned)(n + 1));
            timed = 0;
            sum_us = sum_enc_us = sum_wait_us = sum_wall_us = sum_age_us = 0;
            last_acc_us = 0;
            stdio_flush();
            continue;
        }
        if (c == 'N') {
            bg_reset();
            enrol_forget();
            printf("\nbackground: forgotten - re-learning over the next %u "
                   "frames. Whatever is in shot now becomes furniture.\n"
                   "            The enrolment went with it; see enrol_forget().\n",
                   (unsigned)bg_tau);
            stdio_flush();
            continue;
        }
        // M21. The board is being SHOWN a class rather than told a threshold,
        // so the key means "what is in front of the camera right now is this",
        // and the capture starts on the very next frame - not this one, which
        // was scored before the key was read - and runs for FGX_ENROL_N of them.
        // A second digit during a window abandons the first: enrol_left goes
        // back to 0 so the window accumulator restarts rather than mixing two
        // scenes, and since a window is only folded into its class once it
        // completes, the abandoned frames reach nothing.
        if (c >= '0' && c <= '0' + (int)FGX_MAX_Q) {
            const uint32_t k = (uint32_t)(c - '0');
            if (k == 0u) {
                // ACCEPTED AND IGNORED, deliberately. '0' used to enrol the
                // empty scene and #18 removed the thing it fed. Rejecting it as
                // an unknown key would be quieter and worse: host/cue.py logs
                // are replayed months later and a session that silently skipped
                // a step reads exactly like one that did not have the step.
                printf("\nenrol     : '0' is gone. The empty scene is not "
                       "enrolled any more - presence is a\n"
                       "            distance from the classes now, so there is "
                       "nothing to teach it. #18.\n");
            } else {
                const int i = enrol_slot(k);
                if (i < 0) {
                    printf("\nenrol     : there is no class %u. The set has %u "
                           "enrollable %s.\n", (unsigned)k,
                           (unsigned)(n_class ? n_class : nq),
                           n_class ? "CLASS queries" : "queries");
                } else {
                    enrol_want = i;
                    enrol_left = 0;
                    // A REPEAT PRESS ADDS A VISIT, it does not replace one -
                    // that is FGX_ENROL_V, and it is the one key-level
                    // behaviour that changed, so the console says which it is
                    // doing rather than leaving the operator to infer it from a
                    // frame count. 'N' is still how you throw an enrolment
                    // away, and now the only way.
                    printf("\nenrol     : the next %u frames are '%s'%s. HOLD "
                           "THE SCENE STILL until it lands.\n",
                           (unsigned)FGX_ENROL_N, qname[i],
                           qref_vis[i] ? " AGAIN - they join the visit(s) it "
                                         "already has" : "");
                }
            }
            stdio_flush();
            continue;
        }
        // M20b gate: hang on purpose, so the recovery that is supposed to catch
        // the real hangs has been seen to work at least once. Stalling the host
        // does not do it - SIGSTOP on demo.py was tried and the board ran
        // straight through to frame 49, because pico's stdio_usb drops output
        // rather than blocking on a reader that has gone away. That result also
        // retired the theory that a killed host is what wedges the board.
        //
        // The stage this reports is POLL, since that is the last one marked.
        if (c == 'W') {
            printf("\nwedge     : spinning on purpose. The watchdog should "
                   "reboot the board in about %u ms\n"
                   "            and the next banner should name the stage and "
                   "this frame, %u.\n",
                   (unsigned)FGX_WD_MS, (unsigned)n);
            stdio_flush();
            for (;;) tight_loop_contents();
        }
        // The camera-bus twin of 'W', and the opposite outcome on purpose: 'W'
        // proves the watchdog names a stage, this proves the stage no longer
        // needs naming. Issue #8's hang was cam_xfer() spinning on a byte that
        // never arrived; the loop is bounded now, so this should cost one frame
        // and a diagnostic line rather than the run.
        if (c == 'C') {
            printf("\ncamera    : stalling the bus on purpose on the next "
                   "transfer. Expect a `camera bus stalled` line, then\n"
                   "            `no usable frame off the camera` for frame %u, "
                   "then frame %u as if nothing happened.\n",
                   (unsigned)n, (unsigned)(n + 1));
            stdio_flush();
            ft_cam_fault_inject();
            continue;
        }
        // #9's twin of 'C', one layer out: the camera bus and the USB bus are
        // the two things this loop depends on and cannot see inside, and both
        // now have a key that breaks them on purpose. Print first - after the
        // pull-up goes the host hears nothing, so the last line the log holds
        // has to be the one that says what is about to happen.
        if (c == 'U' || c == 'I') {
            const bool hard = (c == 'I');
            printf("\nusb       : dropping off the bus on purpose%s.\n"
                   "            %s\n",
                   hard ? " and refusing to come back" : "",
                   hard ? "Expect ~30 s of silence, then a reboot and a banner "
                          "whose `usb :` line names frame "
                        : "Expect ~2 s of silence, D1 blinking red, then a "
                          "re-attach and a `back after` line.");
            if (hard) printf("            %u. If the banner says `hang :` "
                             "instead, the reason tag is wrong.\n", (unsigned)n);
            stdio_flush();
            usb_sim_drop(hard);
            continue;
        }
        if (c == 'Q' || c == 'q') {
            // A rejected set leaves the old one resident and running, which is
            // the right failure: the board keeps answering the last question it
            // understood rather than going quiet.
            printf("\n");
            stdio_flush();
            continue;
        }
        // M11 gate 7, on demand. Provoke a fabric fault, hold it long enough to
        // be seen, then clear it - and print what D1 should be doing at each
        // step, because the whole point is comparing the LED against a claim
        // made independently of it.
        if (c == 'E') {
            // GH_OK here is the expected answer, not a surprise: gh_led_badlen()
            // has no return payload, so its response is deferred and this only
            // reports that the frame was queued. The proof that the fabric threw
            // it away is the NOP below failing to find a preamble - the response
            // that never came - with bad_frame set behind it.
            const gh_err_t fe = gh_led_badlen();
            printf("\nfault     : sent a 3-byte LED frame -> %s (deferred; "
                   "GH_OK expected)\n"
                   "            D1 should now be BLINKING RED with green off, "
                   "not solid red.\n", gh_strerror(fe));
            stdio_flush();
            sleep_ms(6000);

            uint8_t st = 0;
            const gh_err_t ce = gh_nop(&st);
            printf("fault     : NOP -> %s | status %02x, bad_frame %s\n"
                   "            Both are the pass: no preamble means the frame "
                   "was dropped, not answered.\n"
                   "            D1 should be back to the meter now.\n",
                   gh_strerror(ce), (unsigned)st,
                   (st & GH_ST_BADFRAME) ? "SET" : "NOT SET - unexpected");
            stdio_flush();
            continue;
        }
        if (c == 'B' || c == 'R') {
            int mean[3];
            ft_cap_stats(mean, NULL, NULL);
            printf("\nstopped   : %u frames, %u good, capture %s the compute, "
                   "configuration %s\n",
                   (unsigned)n, (unsigned)good,
                   overlap ? "overlapped with" : "serial with",
                   w == 3 ? "C" : "A");
            // The split #10 asked for, and the wall clock that keeps it honest.
            // encode + wait + read is sum_us by construction; wall is measured
            // independently and includes everything none of the three names.
            // This is the window since the last 'O', not necessarily the run.
            report_cost(timed, sum_us, sum_enc_us, sum_wait_us, sum_wall_us,
                        sum_age_us, overlap, "            ");
            printf("            last frame mean RGB %d %d %d\n",
                   mean[0], mean[1], mean[2]);
            // #12 and #9, both of which are about things that happen twice in
            // five runs and are therefore only ever seen in a summary.
            //
            // The camera figure is the margin, not a fault count: the deadline
            // in cam_xfer() fires at 2,000 us, so this says how close the worst
            // gap in the whole run came to it. A run that reads 60 us and a run
            // that reads 1,900 us both look identical frame by frame and mean
            // completely different things about #12.
            printf("            camera bus: worst gap %u us against the %u us "
                   "deadline (#12)\n"
                   "            usb: %u outage%s, %u ms off the bus, %u "
                   "re-attach%s (#9)\n",
                   (unsigned)ft_cam_gap_us(false),
                   (unsigned)ft_cam_stall_us(),
                   (unsigned)usb_drops, usb_drops == 1 ? "" : "s",
                   (unsigned)usb_gone_ms,
                   (unsigned)usb_kicks, usb_kicks == 1 ? "" : "es");
            stdio_flush();
            sleep_ms(50);
            watchdog_hw->scratch[0] = 0;   // asked for, so not a hang
            if (c == 'B') reset_usb_boot(0, 0);
            watchdog_reboot(0, 0, 0);
        }
    }
}
