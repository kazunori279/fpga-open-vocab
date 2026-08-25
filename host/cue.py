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

The rotation returns to the empty scene once per cycle. That is not symmetry
for its own sake: it is the only held-out test of the half of the rule that
decides whether anything is there at all, and until it existed that half had
never been scored on a frame it was not fitted to. It found the level-based
stage holding 16/90 and 22/90, which is what #18 replaced. --no-revisit-empty
takes it back out.

It adds nothing to the measurement. demo.py still does all of it; this only
decides when to speak and which frames to count.

**What the camera saw is part of the record too.** `--frame-check` runs no
experiment and just keeps one PNG showing the live scene, for aiming and
lighting before a ten-minute run; `--preview N` does the same during one; and
with `--enrol` a picture of each enrolment window is kept beside the log
whether or not anybody asked, because "what was in shot while the board
learned" is the first question a bad reference raises and nothing used to
answer it.
"""
from __future__ import annotations

import argparse
import contextlib
import math
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Below the insert above, necessarily.
import board
import bootsel  # for its power cycle, which knows where the board is

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

# WHAT THE BOARD DECIDED, read off the tail of the frame line rather than
# re-derived. Everything after `led` on that line is the #18 rule's own
# arithmetic - `d` is the distance to the nearest enrolled reference in units of
# `sep`, and the MATCH names which one it was and by how much (in raw units, not
# in sep). The bars used to ignore all of it and run a softmax over the raw z
# instead, which is the pre-#18 quantity: it has no way to say "nothing there",
# it carries the sensor drift the centred space subtracts, and it disagreed with
# both the LED and the board's own MATCH on the same frame. Mirror, do not
# recompute - that is the only way the two displays cannot drift apart.
DIST    = re.compile(r"\sd([\d.]+)(?:\s|$)")
MATCHED = re.compile(r"\sMATCH (.+?) \(cos [-+]?[\d.]+, nearer by "
                     r"([-+]?[\d.]+)\)")
ABSENT  = re.compile(r"\s-\s\(nothing there\)")

# `sep` and which queries carry a reference, from the board's enrolment lines.
# The distances above are quoted in sep, so this side needs it to put the
# runner-up's raw gap on the same scale.
ENROL_ONE  = re.compile(r"^enrol\s+:\s+(.+?), level [-+][\d.]+"
                        r"(?:, scatter [\d.]+)? \(\d+ frames"
                        r"(?:, visit \d+ of \d+)?\)")
ENROL_PAIR = re.compile(r"^enrol\s+:\s+\d+ classes, nearest pair ([\d.]+) apart")

# MUST MATCH FGX_ABSENT_TRIP / FGX_ABSENT_STAY in firmware/m9.c. Only the trip
# radius is used for drawing - the board owns the verdict and this side never
# re-decides it - but a bar with no scale printed on it is the thing #15 was
# about, so both edges are shown next to the number.
ABSENT_TRIP = 2.0
ABSENT_STAY = 1.5

# MUST MATCH FGX_ENROL_N in firmware/m9.c. The board owns the number - it is the
# one doing the averaging - and this side only needs it to place the cues so the
# windows land where they are supposed to. A mismatch is visible rather than
# silent: the board prints "(N frames)" on every enrol line, and the sidecar
# records what this side assumed, so tools/score_cue.py can compare the two.
ENROL_FRAMES = 20

# MUST MATCH FGX_ENROL_V in firmware/m9.c. How many visits to each class get a
# key press: the board folds them into one reference, and the second one is what
# lets its enrolment guard see staging variance rather than only stillness. This
# side owns the schedule, so this side is what makes the second visit happen.
# Costs one visit of held-out data per class, which is why it is not three.
ENROL_VISITS = 2

# The label for a return visit to the empty scene, and the reason it is not
# called "baseline" is the whole point of #15. The baseline segment at the head
# of the run used to TEACH the empty reference, so scoring the presence stage on
# it was scoring it on its own training frames; #18 has since deleted that
# reference, but the baseline is still the frames the background was frozen on
# and so is still not a test of anything. A visit later in the rotation is held
# out by construction, exactly the way the second and third visits to a class
# already are - and it is the only segment in the schedule where the presence
# stage has anything to suppress. tools/score_cue.py keys off this string.
EMPTY = "empty"

# The schedule keys off the frame number and not off the board's "background:
# after N frames (frozen)" line, which was the first thing this tried. That line
# is reprinted about every 100 frames (firmware/m9.c:234, and m9.c:937 explains
# why - a warm-up that says nothing is indistinguishable from a dead board), so
# waiting for the first one starts the baseline at frame 99 rather than 30 and
# shoves the whole run 70 frames late. The 2026-08-10 hand run lost 65 of the
# 120 frames of its second scene that way. The freeze is at a frame number the
# caller already chose; use the number.


def cue_text(label: str) -> str:
    """What to say for a scene change. "empty. Now." is not an instruction."""
    return ("Empty. Take it all out." if label == EMPTY
            else f"{label}. Now.")


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
        # FileNotFoundError means not macOS; the banner and the bell still fired.
        with contextlib.suppress(FileNotFoundError):
            subprocess.Popen(["say", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_scores(body: str) -> dict[str, float]:
    return {m.group(1).strip(): float(m.group(2)) for m in SCORE.finditer(body)}


def parse_verdict(tail: str) -> dict | None:
    """The #18 rule's numbers, off the part of the frame line after `led`.

    None when the board is not deciding that way - fewer than two references
    enrolled, or a build older than #18 - and the caller then falls back to the
    softmax display, which is the right one for the rule the board IS running.

    The board prints the nearest distance in sep and the runner-up as a raw gap,
    so the second distance is `d + gap / sep` and needs the enrolment's sep from
    the caller. With more than two classes enrolled only those two are
    recoverable, which is honest: the board did not print the rest.
    """
    d = DIST.search(tail)
    if not d:
        return None
    v: dict = {"d": float(d.group(1)), "match": None, "gap": None}
    if m := MATCHED.search(tail):
        v["match"], v["gap"] = m.group(1), float(m.group(2))
    return v


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

    **ONCE TWO CLASSES ARE ENROLLED, NONE OF THE ABOVE IS WHAT THE BOARD IS
    DOING, and until 2026-08-17 these bars went on drawing it anyway.** The
    display and the LED were then two different quantities on the same frame and
    the operator had no way to know which to believe:

      * The LED reads `c[] = z[] - lvl`, the centred space (`led_ref()`,
        `firmware/m9.c:700`). These bars read the raw z. The sensor's ~1.5 z
        warm-up over four minutes is common to every query, so it cancels out of
        the first and moves the second - the bars drift on a scene that is
        sitting still and the LED does not.
      * `ab.sh --enrol` drops the gate query, so `two_stage` is False and the
        softmax has no way to express "nothing there": the shares sum to 100%
        on every frame, so an empty desk still reads 91% / 9% while the LED goes
        dark and the log says `- (nothing there)`. That is the largest of the
        disagreements and it is on exactly the frames #18 exists for.
      * Red is pinned to one class for the whole run and saturates at `sep`;
        the softmax's leader arrow follows whoever is ahead and saturates at
        `--temp`. Even when they agree on WHICH, they disagree on how strongly.

    So with a reference geometry in hand the rows switch to it: distance to each
    reference in units of sep, filled the way the LED's brightness is filled
    (`1 - d / TRIP`, full on a reference and empty at the absent radius), and
    the presence verdict taken verbatim from the board's own MATCH rather than
    re-decided here. Not smoothed, unlike the softmax rows - the verdict is
    hysteretic and a filtered `d` beside an unfiltered THERE/absent would be a
    third quantity again.
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
        # "presence" gets a row of its own once the board is on #18's rule, and
        # it has to sit in the same column as the query labels.
        self.label_w = max(len(n) for n in [*names, "presence"])
        self.sep: float | None = None       # from the board's enrolment summary
        self.refs: set[str] = set()         # queries that carry a reference
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
        fill = max(0, min(self.width, round(frac * self.width)))
        return self.GLYPH_FULL * fill + glyph * (self.width - fill)

    def enrolled(self, line: str) -> None:
        """Pick the reference geometry out of the board's own enrolment lines.

        Nothing here is derived: `sep` and which queries carry a reference are
        both things the board prints, and taking them from anywhere else is how
        the display and the LED got out of step in the first place.
        """
        if m := ENROL_ONE.match(line):
            self.refs.add(m.group(1))
        elif m := ENROL_PAIR.match(line):
            self.sep = float(m.group(1)) or None

    def update(self, frame: int, scene: str, scores: dict[str, float],
               verdict: dict | None = None) -> None:
        self.smooth(scores)
        lines = [f"frame {frame:4d}   scene: {scene}"]
        if verdict is not None and self.sep and len(self.refs) >= 2:
            lines += self.ref_rows(verdict)
        elif self.two_stage:
            lines += self.two_stage_rows()
        else:
            p, z = self.shares(self.names)
            lead = max(range(len(p)), key=p.__getitem__)
            for i, (n, pi, zi) in enumerate(zip(self.names, p, z, strict=False)):
                lines.append(f"  {n:<{self.label_w}} |{self.bar(pi)}| "
                             f"{pi * 100:5.1f}% {'<' if i == lead else ' '}"
                             f"   z {zi:+6.2f}")
        self.draw(lines)

    def ref_rows(self, v: dict) -> list[str]:
        """#18's geometry, in the board's numbers. See the class docstring.

        No percentages here, deliberately. A share is what the softmax rows show
        and it is the reading that had to be argued with above; a distance in
        sep is the quantity the rule actually cuts on, the LED's brightness is
        the same `1 - d / TRIP` these bars are filled with, and the two edges are
        printed beside it so the bar has a scale rather than a vibe.
        """
        assert self.sep
        d, here = v["d"], v["match"] is not None
        rows = [(f"  {'presence':<{self.label_w}}  "
                 f"{'THERE        ' if here else 'nothing there'}"
                 f"  nearest {d:5.2f} sep   "
                 f"(absent > {ABSENT_TRIP:.2f}, back at {ABSENT_STAY:.2f})")]
        # The nearest is printed in sep, the runner-up as a raw gap. With more
        # than two classes the rest are simply not in the log, and a bar for a
        # number nobody measured is the thing #15 was about.
        dist: dict[str, float] = {}
        if here:
            dist[v["match"]] = d
            rest = [n for n in self.names if n in self.refs and n != v["match"]]
            if len(rest) == 1 and v["gap"] is not None:
                dist[rest[0]] = d + v["gap"] / self.sep
        for n in self.names:
            z = self.ema.get(n, 0.0)
            if n not in self.refs:
                # Scored by the board, but nothing was enrolled on it, so it is
                # not part of this decision. Shown, never given a bar.
                rows.append(f"  {n:<{self.label_w}} |{' ' * self.width}| "
                            f" (idle)      z {z:+6.2f}")
            elif n in dist:
                rows.append(f"  {n:<{self.label_w}} "
                            f"|{self.bar(1.0 - dist[n] / ABSENT_TRIP)}| "
                            f"{dist[n]:5.2f} sep {'<' if n == v['match'] else ' '}"
                            f"  z {z:+6.2f}")
            else:
                glyph = self.GLYPH_SHUT if not here else self.GLYPH_EMPTY
                rows.append(f"  {n:<{self.label_w}} |{glyph * self.width}| "
                            f"   --       z {z:+6.2f}")
        return rows

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
        for i, (n, pi, zi) in enumerate(zip(self.states, p, z, strict=False)):
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
        p = dict(zip(shared, softmax([m[nm] for nm in shared], temp), strict=False))
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
    # The empty revisit is a scene like any other in the table above - it has a
    # mean per query and that is worth seeing - but it is not one of the two
    # being compared, and letting it into `order` below turns a two-scene run
    # into a three-label one and silently drops this whole section.
    ab = [r for r in rows if r[0] not in ("baseline", EMPTY)]
    if len(ab) < 2:
        return
    order: list[str] = []
    for r in ab:
        if r[0] not in order:
            order.append(r[0])
    if len(order) != 2:
        return
    la, lb = order
    pairs = list(zip([r for r in ab if r[0] == la], [r for r in ab if r[0] == lb], strict=False))
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


# One fixed path, overwritten in place, because that is what makes a viewer
# usable as a live preview: VS Code's image tab reloads a file that changes
# underneath it, and cannot follow a new filename per frame.
PREVIEW_PNG = Path("/tmp/fgx_preview.png")
# --frame-check gets its own log. It is not a measurement and it would
# otherwise push the last real run out of /tmp/m9_cue.log through the rotation
# above - a run that was kept precisely because it was worth keeping.
FRAMECHECK_LOG = Path("/tmp/fgx_framecheck.log")

# MUST MATCH the board's header at firmware/m9.c:2401, and host/cam.py's
# SNAPSHOT, which parses the same line for the same number. Only the frame is
# wanted here; the mean RGB is cam.py's business.
SNAPSHOT = re.compile(r"^snapshot\s*:\s*frame (\d+)")


def open_viewer(png: Path) -> str:
    """Put the PNG on screen, once."""
    if shutil.which("code"):
        subprocess.run(["code", "-r", str(png)], check=False,
                       capture_output=True)
        return "opened in VS Code; the tab reloads on its own as it is rewritten"
    if sys.platform == "darwin":
        # -g so the run keeps the keyboard: the operator is holding a scene,
        # not clicking on a window.
        subprocess.run(["open", "-g", str(png)], check=False)
        return "opened in Preview.app"
    return "open it in a viewer that reloads a changed file"


class Preview:
    """Catch the board's frame dumps going past, and keep one PNG current.

    Both halves of this have to live here rather than in cam.py. A dump is
    ~44 KB of base64 on the same stream as the score lines, so it has to be
    taken out of the stream before it reaches the bars, or the display scrolls
    away for ten seconds every time a picture arrives. And rendering costs a
    fraction of a second of numpy, which must not happen on the thread reading
    demo.py's pipe: a blocked reader is a full pipe, and a full pipe stalls the
    board's run - the thing being measured - to draw a picture of it.

    Nothing is lost by swallowing the block. demo.py writes every line to its
    own log first, so the dumps are all still there afterwards for the full
    host/cam.py, quantizer check and both byte orders included.

    cam.py is run as a subprocess rather than imported because its numpy is
    declared in its own PEP 723 header and not in this project's, and because
    the alternative - a second copy of the PNG writer in here - is the kind of
    duplicate constant this repo keeps being bitten by.
    """

    def __init__(self, png: Path, keep_stem: Path | None = None,
                 keep_at: set[int] | None = None):
        self.png = png
        self.keep_stem = keep_stem
        self.keep_at = keep_at or set()
        self.buf: list[str] | None = None
        self.head = ""
        self.jobs: queue.Queue = queue.Queue()
        self.out: queue.Queue = queue.Queue()
        self.shown = False
        self.busy = False
        threading.Thread(target=self._work, daemon=True).start()

    def feed(self, line: str) -> bool:
        """True if this line was part of a dump and must not be printed."""
        if self.buf is not None:
            self.buf.append(line)
            if line.startswith("END "):
                block, self.buf = self.buf, None
                # 'V' vectors ride the same envelope and are not pictures.
                if not block[0].startswith("BEGIN m9emb"):
                    self.jobs.put((self.head, block))
            return True
        if line.startswith("BEGIN "):
            self.buf = [line.rstrip("\n")]
            return True
        if SNAPSHOT.match(line):
            # Kept, and also printed: it is one line, and it carries the frame
            # number the picture belongs to.
            self.head = line.rstrip("\n")
        return False

    def drain(self) -> list[str]:
        msgs = []
        while True:
            try:
                msgs.append(self.out.get_nowait())
            except queue.Empty:
                return msgs

    def finish(self, timeout: float = 30.0) -> list[str]:
        """Wait for the dumps still in flight, then drain.

        A run can end within a second of a dump arriving, and the worker is a
        daemon thread - so without this the last picture is dropped, and with
        it the copy kept off to the side, which is the one that cannot be
        re-made afterwards.
        """
        end = time.monotonic() + timeout
        while (self.busy or not self.jobs.empty()) and time.monotonic() < end:
            time.sleep(0.05)
        return self.drain()

    def _work(self) -> None:
        frag = self.png.with_name(self.png.name + ".block")
        while True:
            head, block = self.jobs.get()
            self.busy = True
            # Skip to the newest waiting dump. A preview that is three pictures
            # behind is worse than useless for aiming a camera - it shows the
            # scene as it was before the last two things the operator tried.
            skipped = 0
            while True:
                try:
                    head, block = self.jobs.get_nowait()
                    skipped += 1
                except queue.Empty:
                    break
            frag.write_text("\n".join([head] + block) + "\n")
            # --rot is left at cam.py's 0 to match FT_MOUNT_ROT = CAM_ROT_0 in
            # firmware/frame.h:64. If the camera is ever remounted, that is the
            # knob, and the picture is what says it is wrong.
            r = subprocess.run(
                ["uv", "run", "--script", str(ROOT / "host/cam.py"),
                 "--preview", str(self.png), str(frag)],
                cwd=ROOT, capture_output=True, text=True, check=False)
            lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
            msg = lines[-1] if lines else f"preview   : cam.py said nothing (rc {r.returncode})"
            if skipped:
                msg += f"  ({skipped} older dump{'s' if skipped > 1 else ''} skipped)"
            if r.returncode == 0:
                if not self.shown:
                    self.shown = True
                    msg += f"\n            {open_viewer(self.png)}"
                msg += self._keep(head)
            self.out.put(msg)
            self.busy = False

    def _keep(self, head: str) -> str:
        """Copy off the pictures worth having after the run - the enrolment
        windows. Not every dump: a --preview run makes hundreds and they are
        all the same desk."""
        m = SNAPSHOT.match(head)
        if not (self.keep_stem and m):
            return ""
        bf = int(m.group(1))
        # Within a few frames, because the board defers 'P' to the next frame
        # and a dropped frame line moves it further.
        if not any(abs(bf - k) <= 3 for k in self.keep_at):
            return ""
        dst = self.keep_stem.with_name(f"{self.keep_stem.name}-f{bf:04d}.png")
        shutil.copyfile(self.png, dst)
        return f"\n            kept as {dst} (an enrolment window)"


def frame_check(args, extra: list[str]) -> int:
    """Show what the camera is pointed at, and measure nothing.

    Aiming the camera used to be done by running an experiment and reading the
    scores, which is a slow way to find out that the object is half out of
    frame. This runs demo.py with no schedule, no enrolment and no cues, asks
    for a picture every --preview frames, and keeps one PNG current until
    Ctrl-C. The queries still have to be given because demo.py needs something
    to score, and their scores are ignored here.
    """
    every = max(1, args.preview)
    cmd = ["uv", "run", str(ROOT / "host/demo.py"),
           "--frames", "0", "--out", str(FRAMECHECK_LOG),
           f"--snap-every={every}", *extra, *args.queries]
    print(f"framing   : a picture every {every} frames "
          f"(about {every * 0.5:.0f} s), Ctrl-C when the scene sits right")
    print(f"            {PREVIEW_PNG} is the live one; nothing here is "
          f"measured and the log goes to {FRAMECHECK_LOG}\n")

    prev = Preview(PREVIEW_PNG)
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if prev.feed(line):
                continue
            # The frame lines are the one thing not wanted: this is about the
            # picture, and 120 score lines a minute bury it.
            if not line.startswith("frame "):
                sys.stdout.write(line)
            for msg in prev.drain():
                print(msg)
            sys.stdout.flush()
    except KeyboardInterrupt:
        proc.terminate()
    proc.wait()
    # Short, because the operator has just pressed Ctrl-C and is waiting: one
    # more picture is worth a couple of seconds and not twenty.
    for msg in prev.finish(timeout=5.0):
        print(msg)
    print(f"\nframing   : last picture {PREVIEW_PNG}, log {FRAMECHECK_LOG}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="cue an A/B scene change and record where the boundary fell")
    ap.add_argument("queries", nargs="+",
                    help="query strings, passed to demo.py unchanged - "
                         "'/' still separates a positive from its negatives")
    ap.add_argument("--scene", action="append", default=[], metavar="LABEL",
                    help="a scene to cue, repeat for each; the empty scene "
                         "before the first one is always measured as "
                         "'baseline', and the rotation returns to it once per "
                         "cycle as 'empty'")
    ap.add_argument("--hold", type=int, default=30, metavar="N",
                    help="frames counted per visit to a scene, default 30 "
                         "(about 15 s). Measured on three 2026-08-10 runs, 13 "
                         "frames already pins the mean to +-0.05; the old "
                         "default of 120 was buying a third decimal place on "
                         "effects of 1.14 and 2.42")
    ap.add_argument("--repeat", type=int, default=4, metavar="K",
                    help="times to cycle through the scenes, default 4. This "
                         "is where the time saved by a short --hold goes, and "
                         "it buys the only error bar that measures what varies. "
                         "--enrol spends the first ENROL_VISITS cycles teaching, "
                         "so 4 is what leaves the two held-out visits per class "
                         "that the state figure used to be measured on")
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
    ap.add_argument("--no-revisit-empty", dest="revisit_empty",
                    action="store_false",
                    help="drop the empty scene from the rotation. It is in "
                         "there by default: a run that never goes back to an "
                         "empty desk after the rule is live cannot measure what "
                         "the presence stage buys, which is #15. Costs one "
                         "visit per repeat")
    ap.add_argument("--enrol", action="store_true",
                    help="M21. Show the board each scene and let it decide by "
                         "nearest reference for the rest of the run. The "
                         "enrolling visits are the FIRST ENROL_VISITS visits to "
                         "each scene - two of them, so the board's guard can see "
                         "how far a class moves between stagings and not only "
                         "how still it was held - and every later visit is held "
                         "out")
    ap.add_argument("--lock-camera", action="store_true",
                    help="issue #30. On the last frame of the baseline - before "
                         "any enrolment - freeze the camera's exposure, gain "
                         "and white balance where the room has just settled "
                         "them, and leave them frozen for the run. Off by "
                         "default, because the whole archive was taken with "
                         "them free-running and this is the A/B against it")
    ap.add_argument("--preview", type=int, default=0, metavar="N",
                    help=f"ask the board for a picture every N frames and keep "
                         f"{PREVIEW_PNG} showing the newest one. Costs ~44 KB "
                         f"of log and about a second of run time per picture, "
                         f"so N below ~4 starts eating the run")
    ap.add_argument("--frame-check", action="store_true",
                    help="don't run an experiment at all - just show what the "
                         "camera is pointed at, every --preview frames (4 by "
                         "default), until Ctrl-C. For aiming and lighting a "
                         "scene before spending ten minutes measuring it")
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
    if EMPTY in base:
        print(f"--scene {EMPTY!r}: that label is taken - the schedule already "
              f"returns to the empty scene once per cycle and records it under "
              f"that name. Call the scene something else, or "
              f"--no-revisit-empty.", file=sys.stderr)
        return 2

    # THE EMPTY SCENE GOES LAST IN THE CYCLE, and #15 is why it is in the cycle
    # at all. Every scene after the baseline used to have an object in it, so
    # the presence stage - the half of the rule that decides whether anything is
    # there - had nothing to say on any frame that was scored, and its benefit
    # was inferred by replaying the baseline it was taught from. Last in the
    # cycle rather than first means the first one lands after the final class
    # has been enrolled, so the rule is already live on it; and there is one per
    # repeat, so the spread across visits is an error bar on the same terms as
    # everything else here.
    rotation = base + ([EMPTY] if args.revisit_empty else [])
    scenes = rotation * max(1, args.repeat)

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
            print(f"  {board.recover()}", file=sys.stderr)
            return 1
        # Do it rather than advise it. ab.sh already power-cycled on this
        # condition; moving it here means a bare cue.py run recovers too, which
        # is how the board is usually driven now.
        #
        # THROUGH bootsel.power_cycle RATHER THAN A LITERAL `-l 2-1 -p 1`, which
        # is what this used to run. That constant went stale on 2026-08-16 when
        # a neighbour was unplugged and the board moved to port 2, and cycling
        # the wrong port does not fail loudly - it succeeds at doing nothing and
        # then this reports that even the hammer could not bring the board back.
        # bootsel finds it by VID, which also works with the board in BOOTSEL
        # (it enumerates as 2e8a:000f there, and the PID is why that matters).
        print("  power cycling the hub", file=sys.stderr)
        if not bootsel.power_cycle():
            print(f"  {board.recover()}", file=sys.stderr)
            return 1
        for _ in range(15):
            time.sleep(1)
            if board.find_port():
                break
        else:
            print("  still not enumerating after 15 s - unplug it.",
                  file=sys.stderr)
            return 1
        print(f"  back as {board.find_port()}", file=sys.stderr)

    # Before the log rotation below, because --frame-check has its own log and
    # has no business pushing a measured run aside to get one.
    if args.frame_check:
        # A picture every 4 frames, about every two seconds, unless asked
        # otherwise: --frame-check IS the preview, so 0 cannot mean "none".
        args.preview = args.preview or 4
        return frame_check(args, extra)

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
        # The enrolment pictures move with it too, and for the same reason the
        # sidecar does: they are named after the log, so the next run silently
        # overwrites them. Not hypothetical - the 08:55 run on 2026-08-17 kept
        # its pair for two minutes, until the control run started, and the
        # collapsed-reference geometry that made that run worth looking at was
        # exactly what the pictures were wanted for. They were recoverable
        # from the rotated log with `cam.py`, because the base64 is in there;
        # this is so nobody has to notice that in time.
        stem = args.out.with_suffix("").name
        shots = sorted(args.out.parent.glob(f"{stem}-f[0-9]*.png"))
        for shot in shots:
            shot.replace(keep.with_name(
                f"{keep.with_suffix('').name}{shot.name[len(stem):]}"))
        print(f"kept      : the previous run is now {keep}"
              + (f" (+{len(shots)} enrolment pictures)" if shots else ""))

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
    # 2026-08-11 bench scheduled a key for frame 58 and the board captured 60.
    # Each scene is enrolled --settle + 2 frames into a visit, which is the
    # first frame the operator's hand is guaranteed to be out of. The offline
    # scorer needs to know which visits were spent enrolling, so they go in the
    # sidecar and the visits that are left are held out by construction.
    #
    # THE FIRST ENROL_VISITS VISITS TO EACH SCENE, not just the first. A single
    # visit pins down where an object sits while it sits there and says nothing
    # about where it lands when it is staged again - and the second quantity is
    # what decides runs, so the board cannot even report the enrolment's spread
    # honestly without it. It still cannot PREDICT from it - see THE ENROLMENT
    # RATIO in firmware/m9.c, which is the record of four attempts and four
    # failures. This costs a visit per class off the held-out count, which is why
    # --repeat has to be at least ENROL_VISITS + 1 for the run to test anything.
    #
    # THERE IS A '0' HERE AGAIN, AND IT IS NOT THE OLD ONE. The pre-#18 '0' sat
    # at the end of the BASELINE, so its window measured the background freeze
    # against itself and read about zero whatever was in shot; it fed a level and
    # #18 deleted both. The one below sits in a real empty visit in the rotation
    # and feeds a centred reference, which is a different quantity reached by a
    # different key press at a different time. --baseline still only has to be
    # long enough to freeze the background.
    # WHICH DIGIT ENROLS WHICH SCENE, AND WHY IT IS NOT k + 1. `--scene` sets
    # the rotation and the cue labels; the board's classes come from the
    # positional `queries` argument, which is a DIFFERENT list, and the board
    # binds '1'..'6' to that one. This loop used to press k + 1 for the k-th
    # cued scene, which is only right when the two lists were typed in the same
    # order. Nothing checked that they were.
    #
    # 2026-08-23 07:10 is what it costs when they are not. The rotation opened
    # with 'an opened book' so cue.py pressed '1' there, and the board had '1'
    # bound to 'a closed book', so both references went in swapped and the run
    # is VOID - see bench/cue/m9_cue-20260823-0710.log.cues. Both halves were
    # internally consistent and each reported a plausible number: score_cue.py
    # read the board and got 15.0%, probe_ceiling.py relabelled off the cues
    # and got 85.0%, and the truth is that the bench never happened.
    #
    # So the digit is now looked up in the query list rather than assumed from
    # the cue order, and the two can be typed in any order. Line 1047's
    # `base[int(k) - 1]` was the same assumption printed as a fact; it reads
    # the map now.
    #
    # A cued scene that is not a query at all is left positional and said out
    # loud rather than refused. That combination is deliberate on 08-20 13:12
    # and 14:22, whose queries carry a `~` the cue labels do not, and those two
    # benches are already skipped by every tool that would have to interpret
    # them.
    enrol: list[tuple[int, str]] = []
    digit = {lab: str(i + 1) for i, lab in enumerate(args.queries)}

    # #30's arm, and WHERE it goes is the whole design. The sensor's exposure,
    # gain and white balance run for the entire bench today, so the references
    # are enrolled under one set of the sensor's decisions and the held-out
    # frames scored under another. Freezing them has to happen BEFORE the first
    # enrolment key or it fixes nothing - it would just move the mismatch.
    #
    # The last frame of the baseline is where that is. By then the board has had
    # `--bg-tau` plus `--baseline` frames of the room to converge on, which
    # matters because the register takes a switch and not a number: 'L' freezes
    # whatever the loops last chose, so a lock taken cold is a lock on the
    # settling. And the baseline is empty desk, which is the scene the operator
    # can guarantee is the same in both arms of an A/B.
    #
    # It is off by default. Issue #30 is a measurement before it is a fix, and a
    # default that silently locked the camera would make every bench after it
    # incomparable with all 49 before it.
    #
    # AND IT IS KEPT OUT OF `enrol`. demo.py takes it as one more --enrol=F:KEY
    # because that flag is already a scheduled press, but the SIDECAR's enrol
    # list is a different thing: eight tools in tools/ read it as "frame F
    # taught reference K", and every one of them splits on `k != "0"` or calls
    # int(k). An 'L' in there would be counted as a class enrolment by all of
    # them and would crash the two that do the int(). It gets its own field.
    lock_at = args.bg_tau + args.baseline - 1 if args.lock_camera else None
    if args.enrol:
        loose = [lab for lab in base if lab not in digit]
        if loose:
            print(f"--enrol: {', '.join(repr(x) for x in loose)} "
                  f"{'is' if len(loose) == 1 else 'are'} cued but not in the "
                  f"query list {args.queries}, so the enrolment digit for "
                  f"{'it' if len(loose) == 1 else 'them'} is being assigned by "
                  f"position. If the board binds that digit to a different "
                  f"class the references go in swapped and the run is VOID.",
                  file=sys.stderr)
        visits = min(ENROL_VISITS, max(1, args.repeat))
        for v in range(visits):
            for k, label in enumerate(base):
                scene = v * len(rotation) + k
                start = (args.bg_tau + args.baseline
                         + scene * (args.settle + args.hold))
                enrol.append((start + args.settle + 2,
                              digit.get(label, str(k + 1))))
        # AND '0' IS BACK, ON EXACTLY ONE EMPTY VISIT (2026-08-25). #18's band
        # cannot reject a scene that lands between the two class references -
        # with two queries the centred space is one-dimensional, so "further
        # than r from both" is an interval and the empty desk sits inside it on
        # ten of 28 archived benches. Enrolling it as a third reference and
        # taking the nearest of three scores 79.1% against the band's 54.6%
        # (tools/probe_third.py), and this is the press that makes that
        # possible on the board.
        #
        # WHICH VISIT, and it is not the first. tools/probe_third.py enrolled
        # from the first empty span AFTER the rule engaged, so this matches it
        # exactly: cycle ENROL_VISITS - 1, the last of the teaching cycles, by
        # which point both classes have all their visits and the board is
        # already deciding. Earlier would enrol before the rule exists; later
        # would spend a held-out visit that the replay kept.
        #
        # ONE VISIT, NOT ENROL_VISITS, for the same reason: one is what was
        # measured. The firmware folds a repeat press in exactly like a class,
        # so a second visit is a one-line change here when somebody wants to
        # test whether it helps - `drift` says it might, r = -0.427 - but it is
        # not what the 79.1% figure is a figure for.
        if args.revisit_empty and len(base) >= 2:
            cyc   = min(ENROL_VISITS, max(1, args.repeat)) - 1
            scene = cyc * len(rotation) + len(base)
            start = (args.bg_tau + args.baseline
                     + scene * (args.settle + args.hold))
            enrol.append((start + args.settle + 2, "0"))
        elif args.enrol and not args.revisit_empty:
            print("--enrol --no-revisit-empty: there is no empty visit to enrol "
                  "from, so the board falls back to #18's band and scores what "
                  "the band scores. That is a valid control and a bad bench.",
                  file=sys.stderr)
        if len(base) < 2:
            print("--enrol with one scene: the board needs two enrolled classes "
                  "before the M21 rule engages, so it will stay on the old one.",
                  file=sys.stderr)
        # Enrolling from every visit there is leaves nothing to score, and the
        # run would still print a state figure - one measured on its own
        # training frames. Say so here rather than let it read as a result.
        if args.repeat <= visits:
            print(f"--enrol with --repeat {args.repeat}: all {visits} visits to "
                  f"each scene are enrolment windows, so the state stage has no "
                  f"held-out frames left. Use --repeat {visits + 1} or more.",
                  file=sys.stderr)
        elif visits < ENROL_VISITS:
            print(f"--enrol with --repeat {args.repeat}: only {visits} visit per "
                  f"class can be enrolled, so the board's enrolment guard will "
                  f"measure stillness and not staging. See FGX_ENROL_V.",
                  file=sys.stderr)
        # A window that runs off the end of its scene averages in the NEXT one
        # and nothing downstream can see that it did - the reference is just
        # quietly wrong. Only this side knows the schedule, so only this side
        # can say so.
        if args.hold < ENROL_FRAMES + 2:
            print(f"--enrol: --hold {args.hold} is too short for the board's "
                  f"{ENROL_FRAMES}-frame window; each reference will average in "
                  f"the start of the next scene. Use --hold {ENROL_FRAMES + 2} "
                  f"or more.", file=sys.stderr)

    # A PICTURE OF EACH ENROLMENT WINDOW, unasked-for and on by default with
    # --enrol. A reference is 20 frames of whatever was in shot, and when a run
    # comes back wrong the first question is what the board was actually
    # looking at while it learned - which, until now, nothing recorded. The
    # 08-17 run is the case: it enrolled 'an opened book' 0.14 sep from the
    # origin, and whether the book was badly framed, badly lit or simply not
    # very different from the desk is a question a picture answers and a score
    # trace does not. Mid-window, so it is one of the frames that was averaged.
    # Two dumps a run, ~88 KB of log; the alternative costs a whole re-run.
    # In frame order, because '0' is appended after the class loop and the
    # sidecar, the pictures and demo.py's own schedule all read this list as a
    # timeline. Nothing downstream sorts it.
    enrol.sort()
    snap_at = [f + 2 + ENROL_FRAMES // 2 for f, _ in enrol]

    cmd = ["uv", "run", str(ROOT / "host/demo.py"),
           "--frames", str(frames), "--out", str(args.out),
           "--bg-tau", str(args.bg_tau),
           *[f"--enrol={f}:{k}" for f, k in enrol],
           *([f"--enrol={lock_at}:L"] if lock_at is not None else []),
           *[f"--snap-at={f}" for f in snap_at],
           *([f"--snap-every={args.preview}"] if args.preview else []),
           *extra, *args.queries]

    print("cue       : " + "  ->  ".join(["empty (baseline)"] + rotation)
          + (f"   x{args.repeat}" if args.repeat > 1 else ""))
    print(f"schedule  : freeze {args.bg_tau}, baseline {args.baseline}, then "
          f"{args.settle} settle + {args.hold} held per visit, "
          f"{len(scenes)} visits = {frames} frames")
    print(f"            about {frames * 0.5 / 60:.1f} min of frames, plus a minute of startup")
    if lock_at is not None:
        print(f"camera    : 'L' at frame {lock_at}, the last baseline frame - "
              f"exposure, gain and white balance frozen\n"
              f"            where the empty desk left them, before anything is "
              f"enrolled. Issue #30's locked arm")
    else:
        print("camera    : exposure, gain and white balance left free-running "
              "for the whole run, as every\n            bench in bench/cue/ was "
              "taken. Issue #30's control arm - pass --lock-camera for the other")
    if enrol:
        # '0' is not an index into `queries` - it is the empty scene, which has
        # no query and never will. Reading it as one lands on queries[-1] and
        # prints the LAST class's name against the empty window, which is the
        # 08-23 07:10 failure in a new place: a plausible line describing a run
        # that is not the run.
        def enrol_name(k: str) -> str:
            if k == "0":
                return EMPTY
            return (args.queries[int(k) - 1]
                    if int(k) <= len(args.queries) else "?")
        print("enrol     : " + ", ".join(
            f"frames {f + 2}-{f + 1 + ENROL_FRAMES} = key {k} = {enrol_name(k)}"
            for f, k in enrol))
        held = args.repeat - visits
        print(f"            the first {visits} visit{'' if visits == 1 else 's'} "
              f"to each scene teach the board and fold into one reference; "
              f"{held or 'none'} {'is' if held == 1 else 'are'} "
              f"held out")
        if args.revisit_empty and any(k == "0" for _, k in enrol):
            print(f"            ONE of the {args.repeat} '{EMPTY}' visits is "
                  f"spent teaching now - that is what the third reference "
                  f"costs -\n            and the other "
                  f"{args.repeat - 1} are what measure the presence stage")
        elif args.revisit_empty:
            print(f"            the {args.repeat} '{EMPTY}' visits are held out "
                  f"too - nothing is enrolled for them at all, and they are "
                  f"what measures the presence stage")
    if snap_at:
        print("            a picture of each enrolment window is dumped at "
              + ", ".join(str(f) for f in snap_at)
              + f", kept beside the log as {args.out.with_suffix('')}-fNNNN.png")
    if args.preview:
        print(f"preview   : every {args.preview} frames "
              f"(about {args.preview * 0.5:.0f} s) -> {PREVIEW_PNG}")
    print("            LEAVE THE SCENE EMPTY until the first cue.\n")

    # The enrolment dumps are wanted whether or not anybody is watching, so the
    # renderer runs for those alone; with --preview it also gets the periodic
    # ones. Without either there is nothing to catch and no thread to run.
    prev = (Preview(PREVIEW_PNG, keep_stem=args.out.with_suffix(""),
                    keep_at=set(snap_at))
            if args.preview or snap_at else None)

    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)

    scores: dict[int, dict[str, float]] = {}
    segments: list[tuple[str, int, int]] = []   # label, first frame, one past last
    pending = list(scenes)
    open_seg: tuple[str, int] | None = None
    bars: Bars | None = None
    last_i = -1
    enrol_lines: list[str] = []
    roles: dict[str, str] = {}
    thr: dict[str, float] = {}
    drawing = not args.raw and sys.stdout.isatty()
    scene_now = "empty (leave it that way until the cue)"

    assert proc.stdout is not None
    for line in proc.stdout:
        # First, before anything else looks at it: 44 KB of base64 through the
        # bars would scroll the run off the screen, and through parse_scores
        # would be a waste of a regex on every one of six hundred lines.
        if prev is not None:
            if prev.feed(line):
                continue
            for msg in prev.drain():
                if bars is not None:
                    bars.release()
                print(msg, flush=True)
        q = QUERY.match(line)
        if q:
            roles[q.group(1)] = q.group(2)
            thr[q.group(1)] = float(q.group(3))
        # Kept as well as forwarded, because the first frame line arrives long
        # before the first enrolment on a real run but nothing guarantees it -
        # a replay, or a board that was already enrolled, would otherwise lose
        # the geometry and silently fall back to the softmax display.
        if line.startswith("enrol"):
            enrol_lines.append(line)
            if bars is not None:
                bars.enrolled(line)
        m = FRAME.match(line)

        if m is None or not drawing:
            if bars is not None:
                bars.release()          # let this line scroll, redraw below it
            sys.stdout.write(line)
            sys.stdout.flush()

        if not m:
            continue
        i = int(m.group(1))

        # THE BOARD'S FRAME COUNTER WENT BACKWARDS, so this is a different
        # session and everything above belongs to the previous one. It happens
        # on every run that finds the board still looping: demo.py sends 'R',
        # which is watchdog_reboot(), and the frames it printed before that -
        # frame 1114 on the run that caught this - are already through here.
        # The baseline then opened at 1114, the first cue fired against the old
        # loop, and after the reboot `i - start` was negative forever, so no
        # further cue ever came. The enrolment keys still landed, because those
        # ride demo.py's own schedule off the board's counter, which is exactly
        # why the failure read as "cues are broken" and not "wrong session".
        # Dropping the record and re-arming is right either way: if this was a
        # real mid-run wedge, demo.py voids the measurement regardless.
        #
        # THE BARS BELONG TO THE OLD SESSION TOO, and that is not cosmetic. The
        # first book bench of 2026-08-20 opened on a board still looping the
        # previous run's glass queries, so `bars` was built with names
        # "an empty glass~" / "a glass with tea~". After the reboot the board
        # scored the book queries, `smooth()` filed those under their own keys,
        # and `shares()` went on reading `self.names` - which still said glass.
        # The rows then showed the last glass EMA, frozen, for the whole run:
        # 97.3% / 2.7% at z +-1.79 identical thirty-three frames apart, while
        # the board's own `background:` line named the books correctly. A
        # display that keeps a dead session's labels and never moves is worse
        # than no display, because it reads as a confident measurement.
        # `enrol_lines` goes with it: those references were enrolled against
        # queries that are no longer loaded.
        if i < last_i:
            scores.clear()
            segments.clear()
            pending = list(scenes)
            open_seg = None
            scene_now = "empty (leave it that way until the cue)"
            enrol_lines.clear()
            if bars is not None:
                bars.release()
                bars = None
        last_i = i

        scores[i] = parse_scores(m.group(2))
        verdict = parse_verdict(line[m.end():])

        if open_seg is None and not segments and i >= args.bg_tau:
            open_seg = ("baseline", i)

        if drawing and scores[i]:
            # Belt as well as braces: the labels have to be the queries the
            # board is scoring *now*, and the frame counter going backwards is
            # only the way that was observed to break. Any change of the key set
            # rebuilds - the EMA is five frames deep, so the cost of being wrong
            # here is a couple of seconds of settling, against a run's worth of
            # a stale name over somebody else's number.
            if bars is not None and set(bars.names) != set(scores[i]):
                bars.release()
                bars = None
            if bars is None:
                bars = Bars(list(scores[i]), temp=args.temp, alpha=args.smooth,
                            roles=roles, thr=thr)
                for el in enrol_lines:
                    bars.enrolled(el)
            bars.update(i, scene_now, scores[i], verdict)

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
            cue(cue_text(nxt), speak=not args.quiet)
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
    if prev is not None:
        for msg in prev.finish():
            print(msg, flush=True)

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
        # #30's arm, on its OWN key and not folded into `enrol` above. Every
        # tool in tools/ reads an `enrol` line as "frame F taught reference K"
        # and splits the list on `k != "0"`; an 'L' in there would be counted as
        # a class. This line is written in both arms - "camera-lock none" is a
        # fact about a run, and the 49 benches that predate the flag can say
        # nothing at all, which is exactly the difference worth recording.
        + (f"# camera-lock {lock_at}\n" if lock_at is not None
           else "# camera-lock none\n")
        # Where the pictures are, for the same reason the flags above are: an
        # artifact that cannot say whether a dump was asked for cannot be told
        # apart from one where the board ignored the request.
        + "".join(f"# snap {f}\n" for f in snap_at)
        + "".join(f"{lo}\t{hi - 1}\t{label}\n" for label, lo, hi in segments))

    print(f"\nlog       : {args.out}")
    print(f"cues      : {cues}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
