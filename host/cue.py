# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Run host/demo.py as an A/B scene experiment, and tell the operator when to move.

    uv run host/cue.py --scene "glass WITH water" --scene "glass WITHOUT water" \\
        "a glass with water / a glass without water / a glass" \\
        "a glass without water / a glass with water / a glass"

**The problem this solves is not timing, it is bookkeeping.** demo.py freezes its
background over the first 30 frames, so anything in shot during those frames is
subtracted back out for the rest of the run. That makes the experiment a
sequence - empty scene, then object A, then object B - and every measurement
afterwards depends on knowing which frame belongs to which scene.

Done by hand, that knowledge does not exist. The operator is told "place it in
two minutes", places it at some unrecorded moment, and afterwards the segment
boundaries get reverse-engineered from the score trace by eye. That is not a
measurement, it is an interpretation of one, and it fails exactly when the
signal is weak - which is when the experiment was worth running. Two glass-of-
water runs on 2026-08-10 disagreed about the sign of the effect, and neither had
a boundary anyone could point to.

So: this watches demo.py's own frames go by, cues each scene change out loud,
and records the frame number at the moment it cued. The boundaries are then
data rather than opinion. Frames within --settle of a cue are dropped, because
the operator's hand is in shot.

It adds nothing to the measurement. demo.py still does all of it; this only
decides when to speak and which frames to count.
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402  (after the path insert)

# demo.py's frame line, e.g.
#   frame   125 :  an opened book~ +1.35*  a closed book~ -1.86   led 255/  0 ...
FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led\s")
SCORE = re.compile(r"([^+\-]+?)\s+([+-][\d.]+)(\*?)(?=\s\s|\s*$)")

# demo.py's per-query provenance line, which is where the roles come from:
#   query     : a hand               presence z> 1.23 (background -0.134 ...
# Reading them off demo.py's own output rather than re-deriving them from
# --gate keeps one definition of what a query is for. demo.py prints these
# before the board is even open, so they always arrive before the first frame.
QUERY = re.compile(r"^query\s+:\s+(\S.*?)\s{2,}(plain|presence|state)\s+z>\s*"
                   r"([-\d.]+)")

# MUST MATCH FGX_ENROL_N in firmware/m9.c. The board owns the number - it is the
# one doing the averaging - and this side only needs it to place the cues so the
# windows land where they are supposed to. A mismatch is visible rather than
# silent: the board prints "(N frames)" on every enrol line, and the sidecar
# records what this side assumed, so tools/score_cue.py can compare the two.
ENROL_FRAMES = 20

# The schedule keys off the frame number and not off the board's "background:
# after N frames (frozen)" line, which was the first thing this tried. That line
# is reprinted about every 100 frames (firmware/m9.c:234, and m9.c:937 explains
# why - a warm-up that says nothing is indistinguishable from a dead board), so
# waiting for the first one starts the baseline at frame 99 rather than 30 and
# shoves the whole run 70 frames late. The 2026-08-10 hand run lost 65 of the
# 120 frames of its second scene that way. The freeze is at a frame number the
# caller already chose; use the number.


def cue(text: str, *, speak: bool) -> None:
    """A banner on the terminal and, if it can, a voice.

    The operator is looking at the scene, not at the screen - that is the whole
    point of the exercise - so a printed line is the fallback, not the cue.
    """
    bar = "=" * 60
    print(f"\n{bar}\n>>> {text}\n{bar}\n", flush=True)
    sys.stdout.write("\a")
    sys.stdout.flush()
    if speak:
        try:
            subprocess.Popen(["say", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass  # not macOS; the banner and the bell still fired


def parse_scores(body: str) -> dict[str, float]:
    return {m.group(1).strip(): float(m.group(2)) for m in SCORE.finditer(body)}


class Bars:
    """A bar per query, redrawn in place, showing each one's share of the frame.

    **The number on the bar is not a probability and it is important not to read
    it as one.** demo.py's z is (cos - background) / std against a background
    this room froze in its first 30 frames; it is signed, unbounded, and its
    zero is "looks like the empty scene", not "absent". A softmax over those
    z values turns the set into shares that sum to 100%, which is legible, and
    that is the entire claim. Nothing calibrated it against how often the answer
    is right - `model/evaluate.py` does that, offline, and its output is the
    threshold demo.py already prints.

    What the share *is* good for is the comparison the bare z hides: two queries
    on one frame, where the offsets that make a lone z hard to read are common to
    both and cancel. Weak evidence sits near 50/50 and looks weak, which is the
    honest picture of the glass-of-water runs and the reason to show it this way.

    z is smoothed before the softmax, not after. Smoothing the shares would let
    a frame where everything moved together drag the bars, which is the one
    thing the ratio is supposed to be immune to.

    **A TWO-STAGE SET GETS TWO KINDS OF BAR, and mixing them was the 2026-08-10
    bug.** When the board is given a presence query plus state queries, one
    softmax over all three is meaningless: a bare prompt like "a hand" scores
    z +9..+24 against an empty room while a contrast query scores around zero by
    construction, so the gate takes ~100% of the share on every single frame and
    the two states sit at 0.0% whatever the hand is doing. The operator watching
    that display reads "it can only detect a hand", and the run above says
    exactly that - while its own MATCH column was switching between open and
    closed with margins up to 17. The display was wrong, not the board.

    So the gate gets an absolute bar - its z against its own threshold, the same
    quantity the board's LED uses for brightness - and the softmax runs over the
    state queries only, which is the comparison it was ever valid for. With the
    gate shut the state bars are still drawn but marked, because a forced choice
    between two states of a thing that is not there has an answer and no meaning.
    """

    GLYPH_FULL = "█"
    GLYPH_EMPTY = "·"
    GLYPH_SHUT = "─"

    def __init__(self, names: list[str], *, temp: float, alpha: float,
                 roles: dict[str, str] | None = None,
                 thr: dict[str, float] | None = None) -> None:
        self.names = names
        self.temp = temp
        self.alpha = alpha
        self.roles = roles or {}
        self.thr = thr or {}
        self.ema: dict[str, float] = {}
        self.height = 0
        self.label_w = max(len(n) for n in names)
        cols = shutil.get_terminal_size((100, 24)).columns
        self.width = max(12, min(46, cols - self.label_w - 34))
        self.gates = [n for n in names if self.roles.get(n) == "presence"]
        self.states = [n for n in names if self.roles.get(n) == "state"]
        # Both halves or neither. A set with only one of them is not the
        # two-stage rule and the board will not have scored it that way either.
        self.two_stage = bool(self.gates and self.states)

    def smooth(self, scores: dict[str, float]) -> None:
        for n, v in scores.items():
            prev = self.ema.get(n)
            self.ema[n] = v if prev is None else prev + self.alpha * (v - prev)

    def shares(self, names: list[str]) -> tuple[list[float], list[float]]:
        z = [self.ema.get(n, 0.0) for n in names]
        if not z:
            return [], []
        top = max(z)
        e = [math.exp((x - top) / self.temp) for x in z]
        s = sum(e) or 1.0
        return [x / s for x in e], z

    def bar(self, frac: float, glyph: str = GLYPH_EMPTY) -> str:
        fill = max(0, min(self.width, int(round(frac * self.width))))
        return self.GLYPH_FULL * fill + glyph * (self.width - fill)

    def update(self, frame: int, scene: str, scores: dict[str, float]) -> None:
        self.smooth(scores)
        lines = [f"frame {frame:4d}   scene: {scene}"]
        if self.two_stage:
            lines += self.two_stage_rows()
        else:
            p, z = self.shares(self.names)
            lead = max(range(len(p)), key=p.__getitem__)
            for i, (n, pi, zi) in enumerate(zip(self.names, p, z)):
                lines.append(f"  {n:<{self.label_w}} |{self.bar(pi)}| "
                             f"{pi * 100:5.1f}% {'<' if i == lead else ' '}"
                             f"   z {zi:+6.2f}")
        self.draw(lines)

    def two_stage_rows(self) -> list[str]:
        # The gate is the weakest presence query, matching firmware/m9.c's
        # report(): several gates mean all of them have to hold.
        rows, shut = [], False
        for n in self.gates:
            z = self.ema.get(n, 0.0)
            t = self.thr.get(n, 1.0) or 1.0
            ok = z >= t
            shut = shut or not ok
            rows.append(f"  {n:<{self.label_w}} |{self.bar(z / t)}| "
                        f"{'THERE' if ok else '  -  '}    z {z:+6.2f} > {t:.2f}")
        p, z = self.shares(self.states)
        lead = max(range(len(p)), key=p.__getitem__) if p else -1
        for i, (n, pi, zi) in enumerate(zip(self.states, p, z)):
            if shut:
                rows.append(f"  {n:<{self.label_w}} |"
                            f"{self.GLYPH_SHUT * self.width}|   --      "
                            f"z {zi:+6.2f}")
            else:
                rows.append(f"  {n:<{self.label_w}} |{self.bar(pi)}| "
                            f"{pi * 100:5.1f}% {'<' if i == lead else ' '}"
                            f"   z {zi:+6.2f}")
        # Anything the host called neither: scored and shown, never in a share.
        for n in self.names:
            if n not in self.gates and n not in self.states:
                rows.append(f"  {n:<{self.label_w}} |{' ' * self.width}| "
                            f" (idle)    z {self.ema.get(n, 0.0):+6.2f}")
        return rows

    def draw(self, lines: list[str]) -> None:
        out = sys.stdout
        if self.height:
            out.write(f"\x1b[{self.height}A")
        for ln in lines:
            out.write("\x1b[2K" + ln + "\n")
        out.flush()
        self.height = len(lines)

    def release(self) -> None:
        """Leave the block where it is and stop owning those lines.

        Called before a cue banner or the final report, so they scroll normally
        instead of being overwritten by the next frame.
        """
        self.height = 0


def softmax(z: list[float], temp: float = 1.0) -> list[float]:
    top = max(z)
    e = [math.exp((x - top) / temp) for x in z]
    s = sum(e) or 1.0
    return [x / s for x in e]


def report(scores: dict[int, dict[str, float]],
           segments: list[tuple[str, int, int]],
           settle: int, temp: float = 1.0,
           roles: dict[str, str] | None = None) -> None:
    names: list[str] = []
    for d in scores.values():
        for k in d:
            if k not in names:
                names.append(k)
    if not names:
        print("\nno frame lines parsed - nothing to report")
        return

    # Which columns the share is taken over. Same rule as Bars, same reason:
    # a bare presence prompt and a contrast query are not on one scale, and a
    # softmax that includes both reports the gate winning every segment.
    roles = roles or {}
    shared = [n for n in names if roles.get(n) == "state"]
    two_stage = bool(shared) and any(r == "presence" for r in roles.values())
    if not two_stage:
        shared = names

    rows = []
    for label, lo, hi in segments:
        if label != "baseline":
            lo += settle                  # the operator's hand was in shot
        keep = [i for i in range(lo, hi) if i in scores]
        if not keep:
            continue
        rows.append((label, lo, hi - 1, len(keep),
                     {n: mean(scores[i][n] for i in keep if n in scores[i])
                      for n in names}))

    print("\n" + "=" * 60)
    print("segments, boundaries recorded at the cue - not inferred afterwards")
    print("=" * 60)
    w = max(len(r[0]) for r in rows) + 2
    head = f"{'scene':<{w}}{'frames':>12}{'n':>6}"
    print(head + "".join(f"{n:>24}" for n in names))
    print(" " * (w + 18) + "".join(f"{'share    z':>24}" for _ in names))
    for label, lo, hi, n, m in rows:
        best = max(shared, key=lambda nm: m[nm])
        p = dict(zip(shared, softmax([m[nm] for nm in shared], temp)))
        line = f"{label:<{w}}{f'{lo}-{hi}':>12}{n:>6}"
        line += "".join(f"{p[nm] * 100:>17.0f}% {m[nm]:>+6.2f}" if nm in p
                        else f"{'-':>18} {m[nm]:>+6.2f}" for nm in names)
        print(line + f"   {best}")
    print("\n  share is a softmax over the z values in that segment. It is a way to")
    print("  read two numbers as one split, not a probability - nothing calibrated")
    print("  it against being right. Near 50/50 means the evidence is weak.")
    if two_stage:
        print("  The presence query has no share and shows '-': its z is against an")
        print("  empty room and the state queries' are not, so one softmax over both")
        print("  would say nothing except that the bare prompt scores higher.")

    # One prompt across two scenes. This is the axis that does not carry each
    # prompt's own offset, so it is the one worth reading; a row of the table
    # above compares two prompts on one frame and those offsets do not cancel.
    #
    # Paired by repeat, and that is the whole reason --repeat exists. Within one
    # visit to a scene the frame-to-frame noise is tiny - sd about 0.13 on the
    # 2026-08-10 book run, 64 effective frames, so the mean is good to +-0.016.
    # Between two *runs* of the same experiment it is not: two glass-of-water
    # runs an hour apart disagreed by 0.85 on an effect of 0.08, a hundred times
    # the within-run error. Lighting, placement, and whatever the background
    # froze on dominate, and no number of frames touches any of it. So: hold
    # each scene briefly, visit it several times, and let the spread across
    # visits be the error bar. It is the only one here that is measuring the
    # thing that actually varies.
    ab = [r for r in rows if r[0] != "baseline"]
    if len(ab) < 2:
        return
    order: list[str] = []
    for r in ab:
        if r[0] not in order:
            order.append(r[0])
    if len(order) != 2:
        return
    la, lb = order
    pairs = list(zip([r for r in ab if r[0] == la], [r for r in ab if r[0] == lb]))
    if not pairs:
        return

    print(f"\none prompt across the two scenes ({la} minus {lb}):")
    if len(pairs) > 1:
        print(f"  {'':<32}" + "".join(f"{f'rep {k + 1}':>9}" for k in range(len(pairs)))
              + f"{'mean':>9}{'spread':>9}")
    for n in names:
        ds = [a[4][n] - b[4][n] for a, b in pairs]
        if len(pairs) > 1:
            lo, hi = min(ds), max(ds)
            agree = "" if lo * hi > 0 else "   <- sign flips between repeats"
            print(f"  {n:<32}" + "".join(f"{d:>+9.2f}" for d in ds)
                  + f"{mean(ds):>+9.2f}{hi - lo:>9.2f}{agree}")
        else:
            a, b = pairs[0]
            print(f"  {n:<32}{a[4][n]:>+8.2f}{b[4][n]:>+8.2f}   diff {ds[0]:>+7.2f}")

    print("\n  A positive diff means the prompt preferred the first scene.")
    if len(pairs) > 1:
        print("  Spread across repeats is the error bar worth quoting. If it is not")
        print("  small next to the mean, the effect is not there - more frames per")
        print("  visit would only have measured the same wrong number more precisely.")
    else:
        print("  One visit each, so there is no error bar. --repeat 3 gives one.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="cue an A/B scene change and record where the boundary fell")
    ap.add_argument("queries", nargs="+",
                    help="query strings, passed to demo.py unchanged - "
                         "'/' still separates a positive from its negatives")
    ap.add_argument("--scene", action="append", default=[], metavar="LABEL",
                    help="a scene to cue, repeat for each; the empty scene "
                         "before the first one is always measured as 'baseline'")
    ap.add_argument("--hold", type=int, default=30, metavar="N",
                    help="frames counted per visit to a scene, default 30 "
                         "(about 15 s). Measured on three 2026-08-10 runs, 13 "
                         "frames already pins the mean to +-0.05; the old "
                         "default of 120 was buying a third decimal place on "
                         "effects of 1.14 and 2.42")
    ap.add_argument("--repeat", type=int, default=3, metavar="K",
                    help="times to cycle through the scenes, default 3. This "
                         "is where the time saved by a short --hold goes, and "
                         "it buys the only error bar that measures what varies")
    ap.add_argument("--baseline", type=int, default=30, metavar="N",
                    help="frames of empty scene to keep after the background "
                         "freezes, before the first cue; default 30")
    ap.add_argument("--settle", type=int, default=10, metavar="N",
                    help="frames dropped after each cue while a hand is in "
                         "shot; default 10 (about 5 s). It is pure overhead and "
                         "there are 2K of them, so it is worth not being "
                         "generous with - a hand leaves the frame in two")
    ap.add_argument("--bg-tau", type=int, default=30, metavar="N",
                    help="demo.py's --bg-tau, forwarded; the baseline starts "
                         "at this frame because that is where the background "
                         "freezes. Default 30, which is demo.py's own")
    ap.add_argument("--out", type=Path, default=Path("/tmp/m9_cue.log"),
                    help="demo.py's --out")
    ap.add_argument("--quiet", action="store_true",
                    help="banner and bell only, no spoken cue")
    ap.add_argument("--raw", action="store_true",
                    help="print demo.py's frame lines instead of the bars; "
                         "the default when stdout is not a terminal")
    ap.add_argument("--temp", type=float, default=1.0, metavar="T",
                    help="softmax temperature for the bars; below 1 sharpens "
                         "the split and does not add evidence, default 1.0")
    ap.add_argument("--smooth", type=float, default=0.3, metavar="A",
                    help="EMA weight on z before the softmax, 1.0 for none; "
                         "default 0.3, roughly a 3-frame memory")
    ap.add_argument("--enrol", action="store_true",
                    help="M21. Show the board each scene once and let it decide "
                         "by nearest reference for the rest of the run. The "
                         "enrolling visit is the FIRST visit to each scene, so "
                         "every later one is held out - which is what --repeat "
                         "was already producing and nothing was using")
    ap.add_argument("--python", default=None, help=argparse.SUPPRESS)
    args, extra = ap.parse_known_args()

    # The queries are positional and the pass-through flags are unrecognised, and
    # argparse cannot know that an unrecognised flag takes a value. So
    # `--snap-every 15 "a hand"` puts --snap-every in extra, hands 15 to the
    # query list, and runs: demo.py gets a flag with no value and the board gets
    # a query set with a prompt reading "15" in it. Nothing errors. The `=` form
    # is unambiguous and argparse keeps it in one piece, so require it, and say
    # which token was about to become a prompt rather than just naming the rule.
    argv = sys.argv[1:]
    BOOLEAN = {"--emb", "--ask", "--bootsel", "--wsearch", "--bg-hold",
               "--no-bg-hold", "--room-sd", "--coco-sd", "--smooth",
               "--no-smooth"}
    for flag in extra:
        if not flag.startswith("-") or "=" in flag or flag in BOOLEAN:
            continue
        i = argv.index(flag) if flag in argv else -1
        nxt = argv[i + 1] if 0 <= i < len(argv) - 1 else None
        if nxt is not None and not nxt.startswith("-") and nxt in args.queries:
            print(f"{flag} {nxt}: write it as {flag}={nxt}.\n"
                  f"  {flag} is demo.py's, not cue.py's, so argparse does not "
                  f"know it takes a value\n"
                  f"  and {nxt!r} was about to be sent to the board as a "
                  f"query.", file=sys.stderr)
            return 2

    base = args.scene or ["scene A", "scene B"]
    scenes = base * max(1, args.repeat)

    # Ask before demo.py does, because demo.py loads the teacher first and looks
    # for the board a minute later - so a board that hung after the previous run
    # costs a minute to find out about.
    #
    # This used to glob /dev/cu.usbmodem* and call itself a cheap heuristic, on
    # the reasoning that being fooled costs nothing since demo.py runs the real
    # check anyway. That reasoning was wrong in one direction. A false *negative*
    # costs nothing. A false *positive* skips the recovery below and hands the
    # failure to demo.py a minute later, which is the entire minute this check
    # exists to save - and on this desk it was not an unlucky case but the only
    # case, because the Tiliqua on the same hub is always enumerated. It passed
    # every time, including twice on 2026-08-11 with the board absent and an
    # operator holding a scene still. board.pick_port matches VID 2E8A.
    if not board.find_port():
        print(f"no RP2350 (VID {board.RP2350_VID}) - the board is not "
              f"enumerating. Other modems: {board.neighbours()}", file=sys.stderr)
        if not shutil.which("uhubctl"):
            print(f"  {board.RECOVER}", file=sys.stderr)
            return 1
        # Do it rather than advise it. ab.sh already power-cycled on this
        # condition; moving it here means a bare cue.py run recovers too, which
        # is how the board is usually driven now.
        print("  power cycling the hub", file=sys.stderr)
        subprocess.run(["uhubctl", "-l", "2-1", "-p", "1", "-a", "cycle"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(15):
            time.sleep(1)
            if board.find_port():
                break
        else:
            print("  still not enumerating after 15 s - unplug it.",
                  file=sys.stderr)
            return 1
        print(f"  back as {board.find_port()}", file=sys.stderr)

    # Keep the previous run. demo.py opens --out for writing, so starting a run
    # destroys the one before it, and that has now cost two: a hung run whose
    # log was the only record of the hang, and the hand run that the book run
    # landed on top of an hour later. Both were the input to an analysis that
    # had not been done yet.
    #
    # Stamped with the old file's mtime rather than with now, so the name says
    # when that run happened and not when it was pushed aside. The .cues sidecar
    # moves with it or the pair stops meaning anything -
    # tools/score_cue.py refuses a log without one, which is the behaviour that
    # makes moving them together matter.
    if args.out.exists():
        when = time.strftime("%Y%m%d-%H%M%S", time.localtime(args.out.stat().st_mtime))
        keep = args.out.with_name(f"{args.out.stem}-{when}{args.out.suffix}")
        args.out.replace(keep)
        old_cues = args.out.with_suffix(args.out.suffix + ".cues")
        if old_cues.exists():
            old_cues.replace(keep.with_suffix(keep.suffix + ".cues"))
        print(f"kept      : the previous run is now {keep}")

    # demo.py counts every frame including the 30 it spends freezing the
    # background. Ask for exactly what the schedule needs and no more, so the
    # run ends when the last scene ends rather than a minute later.
    frames = args.bg_tau + args.baseline + len(scenes) * (args.settle + args.hold) + 5

    # M21's enrolment, and the schedule below is why it can be computed up front
    # rather than driven by hand: the cue times are arithmetic on --bg-tau,
    # --baseline, --settle and --hold, and the board numbers its frames from 0,
    # so every boundary is known before the run starts. That is the same
    # property that makes the .cues sidecar worth writing.
    #
    # WHERE EACH ENROLMENT LANDS, and every offset here is load-bearing. A key
    # sent at board frame F is read during F+1 and the window it opens covers
    # F+2 .. F+1+ENROL_FRAMES - the two-frame lag is measured, not assumed: the
    # 2026-08-11 bench scheduled '0' for frame 58 and the board captured 60.
    #   - the empty scene, so its window ENDS on the last baseline frame. It used
    #     to be a single capture two frames before that, and widening a forward
    #     window from there walked straight into the object being put down: the
    #     absent level drifted +0.21 -> -4.81 as the window grew 1 -> 28, which
    #     is the object's level, not the room's.
    #   - each scene, --settle + 2 frames into its FIRST visit, which is the
    #     first frame the operator's hand is guaranteed to be out of.
    # Later visits are then held out by construction. The offline scorer needs
    # to know which visit was spent enrolling, so the frames go in the sidecar.
    enrol: list[tuple[int, str]] = []
    if args.enrol:
        enrol.append((args.bg_tau + args.baseline - 2 - ENROL_FRAMES, "0"))
        for k, label in enumerate(base):
            start = args.bg_tau + args.baseline + k * (args.settle + args.hold)
            enrol.append((start + args.settle + 2, str(k + 1)))
        if len(base) < 2:
            print("--enrol with one scene: the board needs two enrolled classes "
                  "before the M21 rule engages, so it will stay on the old one.",
                  file=sys.stderr)
        # A window that runs off the end of its scene averages in the NEXT one
        # and nothing downstream can see that it did - the reference is just
        # quietly wrong. Only this side knows the schedule, so only this side
        # can say so.
        if ENROL_FRAMES + 2 > args.baseline:
            print(f"--enrol: --baseline {args.baseline} is too short for the "
                  f"board's {ENROL_FRAMES}-frame window; the empty-scene "
                  f"reference will start before the background freezes. Use "
                  f"--baseline {ENROL_FRAMES + 2} or more.", file=sys.stderr)
        if ENROL_FRAMES + 2 > args.hold:
            print(f"--enrol: --hold {args.hold} is too short for the board's "
                  f"{ENROL_FRAMES}-frame window; each reference will average in "
                  f"the start of the next scene. Use --hold {ENROL_FRAMES + 2} "
                  f"or more.", file=sys.stderr)

    cmd = ["uv", "run", str(ROOT / "host/demo.py"),
           "--frames", str(frames), "--out", str(args.out),
           "--bg-tau", str(args.bg_tau),
           *[f"--enrol={f}:{k}" for f, k in enrol],
           *extra, *args.queries]

    print("cue       : " + "  ->  ".join(["empty (baseline)"] + base)
          + (f"   x{args.repeat}" if args.repeat > 1 else ""))
    print(f"schedule  : freeze {args.bg_tau}, baseline {args.baseline}, then "
          f"{args.settle} settle + {args.hold} held per visit, "
          f"{len(scenes)} visits = {frames} frames")
    print(f"            about {frames * 0.5 / 60:.1f} min of frames, plus a minute of startup")
    if enrol:
        print("enrol     : " + ", ".join(
            f"frames {f + 2}-{f + 1 + ENROL_FRAMES} = "
            + ("the empty scene" if k == "0" else base[int(k) - 1])
            for f, k in enrol))
        print(f"            the first visit to each scene teaches the board; "
              f"the other {args.repeat - 1} are held out")
    print("            LEAVE THE SCENE EMPTY until the first cue.\n")

    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)

    scores: dict[int, dict[str, float]] = {}
    segments: list[tuple[str, int, int]] = []   # label, first frame, one past last
    pending = list(scenes)
    open_seg: tuple[str, int] | None = None
    bars: Bars | None = None
    roles: dict[str, str] = {}
    thr: dict[str, float] = {}
    drawing = not args.raw and sys.stdout.isatty()
    scene_now = "empty (leave it that way until the cue)"

    assert proc.stdout is not None
    for line in proc.stdout:
        q = QUERY.match(line)
        if q:
            roles[q.group(1)] = q.group(2)
            thr[q.group(1)] = float(q.group(3))
        m = FRAME.match(line)

        if m is None or not drawing:
            if bars is not None:
                bars.release()          # let this line scroll, redraw below it
            sys.stdout.write(line)
            sys.stdout.flush()

        if not m:
            continue
        i = int(m.group(1))
        scores[i] = parse_scores(m.group(2))

        if open_seg is None and not segments and i >= args.bg_tau:
            open_seg = ("baseline", i)

        if drawing and scores[i]:
            if bars is None:
                bars = Bars(list(scores[i]), temp=args.temp, alpha=args.smooth,
                            roles=roles, thr=thr)
            bars.update(i, scene_now, scores[i])

        if open_seg is None:
            continue
        label, start = open_seg
        held = i - start
        due = args.baseline if label == "baseline" else args.settle + args.hold
        if held >= due and pending:
            segments.append((label, start, i))
            nxt = pending.pop(0)
            if bars is not None:
                bars.release()
            cue(f"{nxt}. Now.", speak=not args.quiet)
            open_seg = (nxt, i)
            scene_now = f"{nxt}  (settling, {args.settle} frames dropped)"
        elif held >= due and not pending:
            segments.append((label, start, i))
            open_seg = None
        elif label != "baseline" and held == args.settle:
            scene_now = f"{label}  (counting)"

    proc.wait()
    if bars is not None:
        bars.release()

    # demo.py exits 3 when the board wedged and its watchdog rebooted it. That
    # is not a short run, it is a DIFFERENT run: the reboot forgot the frozen
    # background, so every frame after it was scored against a baseline this
    # side knows nothing about, and the cues below point at scene boundaries the
    # board stopped honouring partway through. The segments are still written -
    # they are the only record of where the operator was told to move, and the
    # frames before the wedge are real - but they are written marked, because a
    # sidecar that cannot say this is one that lets a wedge become an accuracy.
    void = proc.returncode == 3
    if void:
        bar = "=" * 60
        print(f"\n{bar}\n>>> VOID: the board wedged mid-run and rebooted.\n"
              f">>> The boundaries below are recorded, the measurement is not.\n"
              f">>> Re-run it; the hang line above says where the board was.\n"
              f"{bar}", flush=True)
    if open_seg is not None and scores:
        segments.append((open_seg[0], open_seg[1], max(scores) + 1))

    # baseline has no hand in it - nothing to settle out.
    report(scores, segments, args.settle, args.temp, roles)

    # The boundaries are the one thing here that cannot be recovered from
    # demo.py's log afterwards, and the terminal they were printed to is gone as
    # soon as it scrolls. Write them down.
    cues = args.out.with_suffix(args.out.suffix + ".cues")
    # The pass-through flags belong here too. They were left out at first, and
    # the cost showed up immediately: a run came back with no dumps in it and
    # the artifacts could not say whether --snap-every had been passed and the
    # board ignored it, or whether it had never been passed at all. cue.py's own
    # settings were recorded and demo.py's were not, which is exactly backwards -
    # cue.py's are visible in the segment boundaries below, and demo.py's are
    # visible nowhere.
    cues.write_text(
        f"# host/cue.py, settle {args.settle}, hold {args.hold}, "
        f"baseline {args.baseline}, bg-tau {args.bg_tau}\n"
        f"# demo.py {' '.join(extra) if extra else '(no extra flags)'}\n"
        f"# log {args.out}\n"
        + ("# VOID the board wedged mid-run and its watchdog rebooted it, so "
           "the background froze twice and these boundaries outlived the run "
           "they describe. tools/score_cue.py refuses this unless forced.\n"
           if void else "")
        # A run where the board learned from some of its own frames is not the
        # same run as one where it did not, and a scorer that cannot tell will
        # quietly report training accuracy. The frames are here so it can. The
        # window goes with them because the rule does not engage until the
        # SECOND reference has finished landing, and everything before that is
        # the old rule's output wearing the new rule's name.
        + (f"# enrol-window {ENROL_FRAMES}\n" if enrol else "")
        + "".join(f"# enrol {f} {k}\n" for f, k in enrol)
        + "".join(f"{lo}\t{hi - 1}\t{label}\n" for label, lo, hi in segments))

    print(f"\nlog       : {args.out}")
    print(f"cues      : {cues}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
