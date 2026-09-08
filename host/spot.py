# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Two phrases in, one board telling them apart. The whole appliance, in order.

    uv run host/spot.py "an opened book" "a closed book"

This is the demonstration the rest of the repo was built to make possible, and
it is deliberately the only file here that assumes an operator rather than a
bench. It writes no measurement, holds nothing back for scoring and computes no
verdict of its own: every decision on the screen is the board's, mirrored.

**Why it exists as a separate file.** host/demo.py already does the work - it
loads the teacher, encodes the phrases, sends them, and forwards what comes
back - but it hands the operator a bare pipe. The two things that actually
decide whether a run works are invisible in that pipe:

  1. The background freezes over the first 30 frames and is then never
     revised, so ANYTHING in shot during the warm-up is subtracted out of every
     score afterwards. Leave the book on the desk while demo.py starts and the
     board spends the rest of the run unable to see it. Nothing warns you.
  2. The board has no idea what the phrases mean until it is SHOWN each one.
     M21's two-axis rule carries no threshold: `level = mean(z)` is presence,
     `c[i] = z[i] - level` is class, and both are compared against references
     the operator enrols by pressing a key while holding the object. An
     un-enrolled board prints scores that look fine and mean nothing.

So this walks the sequence: empty scene, phrase A, phrase B, empty again, live.
That is not ceremony, it is the order the firmware requires, and getting it
wrong is the single most common way a run is wasted on this desk.

**How the presses get there.** demo.py's `--enrol FRAME:KEY` schedules a press
against a frame number, which is right for a bench - there the schedule IS the
measurement - and useless for someone holding a book who decides when it is
steady. `--ask` grew a `!KEY` line form for this: a line beginning `!` on
demo.py's stdin is a keypress rather than a query set, so the timing goes back
to whoever is looking at the scene. This file is what drives it.

**Against host/watch.py, which also enrols.** watch.py is the monitor: it
confirms a state over several frames, emits an event on transition, and
restarts through a USB hiccup, which is what you want from something left
running for a day. Its `--enrol` builds demo.py's frame schedule out of
`--enrol-lead` seconds, so the operator is racing a countdown, and it presses
'1'..'N' only. This is the opposite trade and the reason both exist:

  * the operator says when, so a window never closes on a hand still in shot;
  * every press is confirmed against the board's own receipt before the next
    prompt, so a press that did not land stops the run instead of silently
    costing it a reference;
  * `--visits` shows each class more than once, which is the difference
    between measuring how still your hand was and measuring the staging;
  * '0' is pressed, so the empty scene becomes the third reference and
    presence is decided by #18's rule instead of the fitted band.

None of that helps a monitor and all of it helps a demonstration. If watch.py's
enrolment ever wants the operator's timing, this is the path to give it.

Ctrl-C stops it and leaves the board running, so the next start skips the
flash. Nothing here is archived: the log under /tmp is for reading back if a
run surprised you, not a bench record. Use host/cue.py if you want one of those.
"""
from __future__ import annotations

import argparse
import contextlib
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Below the insert above, necessarily.
import board

# MUST MATCH host/demo.py. The board stores 23 bytes of name plus a terminator,
# so a longer phrase comes back clipped on every line it prints and the match
# below would never fire. Clipping the same way here is not cosmetic.
NAME_LEN = 24

# MUST MATCH demo.py's BG_TAU and firmware/m9.c's baseline window. This is the
# only number in this file that gates anything, and it is not a threshold on a
# measurement - it is how many frames the board spends before it stops learning
# the room, so it is how long the operator must keep the scene empty.
BG_TAU = 30

# What the board answers with, read off the tail of the frame line. Same
# patterns host/cue.py uses, and for the same reason: the verdict belongs to the
# firmware and recomputing it here would eventually disagree with it.
FRAME = re.compile(r"^frame\s+(\d+) :")
MATCHED = re.compile(r"\sMATCH (.+?) \(cos [-+]?[\d.]+, nearer by "
                     r"([-+]?[\d.]+)\)")
ABSENT = re.compile(r"\s-\s\(nothing there\)")

# The board's own enrolment receipt, which is the only proof a press landed:
#   enrol     : an opened book, level +0.12, scatter 0.66 (20 frames, visit 1 of 2)
#
# THE TAIL IS NOT THE SAME FOR THE EMPTY SCENE, and matching it exactly cost a
# whole smoke run. m9.c prints "visit 1 of 2" for a class and "visit 1 - one is
# what the rule was measured on" for '0', deliberately: the empty reference is
# taken from a single visit, which is the shape #18's 79% was measured in, and
# "of 2" there would read as an instruction to press '0' again. A pattern that
# insists on the class form waits out the full timeout on the one press that
# did land and then reports it missing. So the tail is anything up to the
# bracket, and what this actually checks is the NAME.
ENROL_ONE = re.compile(r"^enrol\s+:\s+(.+?), level [-+][\d.]+"
                       r"(?:, scatter [\d.]+)? \(\d+ frames[^)]*\)")
# And the summary it prints once two classes exist, which says in one line
# whether the two references are far enough apart to be worth anything:
#   enrol     : 2 classes, nearest pair 6.46 apart, spread 0.66 (9.8x over 1 visit)
ENROL_PAIR = re.compile(r"^enrol\s+:\s+\d+ classes, nearest pair")

EMPTY_NAME = "the empty scene"
UNKNOWN_NAME = "something else"


def board_name(phrase: str) -> str:
    """The phrase as the BOARD will print it, clipped to what fits in a slot."""
    return phrase.encode("utf-8")[:NAME_LEN - 1].decode("utf-8", "ignore")


def cue(text: str, *, speak: bool) -> None:
    """A banner and, if it can, a voice.

    Lifted from host/cue.py deliberately: the operator is looking at the desk,
    not at the terminal, which is the whole premise of an appliance you show
    things to. A printed line is the fallback, not the cue.
    """
    bar = "=" * 60
    print(f"\n{bar}\n>>> {text}\n{bar}", flush=True)
    sys.stdout.write("\a")
    sys.stdout.flush()
    if speak:
        # FileNotFoundError means not macOS; the banner and the bell still fired.
        with contextlib.suppress(FileNotFoundError):
            subprocess.Popen(["say", text],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)


class Board:
    """demo.py, running, with its output on a queue and its stdin open.

    The queue matters: while the main thread is blocked on `input()` waiting for
    the operator to say the book is in place, nothing is draining demo.py's
    pipe. A full pipe stalls the board, and a stalled board trips demo.py's own
    idle timer, so the run would die of the operator thinking. One reader thread
    fixes that; the lines wait in memory until somebody asks for them.
    """

    def __init__(self, cmd: list[str]) -> None:
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     text=True, bufsize=1)
        self.lines: queue.Queue = queue.Queue()
        self.frame = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)        # EOF, so a waiting reader stops waiting

    def press(self, key: str) -> None:
        self.proc.stdin.write(f"!{key}\n")
        self.proc.stdin.flush()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        """Let demo.py finish on its own terms, and only then insist.

        SIGINT and not terminate(): demo.py owns the serial port and its
        KeyboardInterrupt path is what decides whether the board is left
        running. Killing it skips that, and a board dropped into BOOTSEL costs
        the next start a flash - which is what --leave-running was passed to
        avoid.

        The grace period first, because a terminal Ctrl-C has ALREADY signalled
        demo.py: it shares this process group, so by the time this runs it is
        usually mid-shutdown, and a second SIGINT arriving there interrupts the
        shutdown rather than causing it. Only a stop that did not come from the
        keyboard - stdin closing, this file exiting on an error - leaves it
        running, and that is the case the signal is for.
        """
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
        for sig in (None, signal.SIGINT, signal.SIGTERM):
            if sig is not None and self.proc.poll() is None:
                with contextlib.suppress(Exception):
                    self.proc.send_signal(sig)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=3 if sig is None else 20)
                return


def show(line: str) -> None:
    """Forward anything that is not a frame line, unchanged.

    demo.py and the firmware say a lot that is worth an operator's attention -
    the exposure never settling, a query set rejected, the watchdog - and none
    of it is worth paraphrasing. Frame lines are the only thing suppressed,
    because there are three and a half of them a second.
    """
    if line and not FRAME.match(line):
        print(line, flush=True)


def drain(b: Board, until: float, want=None):
    """Forward output until `want` matches a line or the clock runs out.

    Returns the match, or None. `want` is a callable so the two things this
    waits for - the warm-up ending, an enrolment receipt - can be expressed as
    predicates over lines and frame numbers rather than two near-copies of this.
    """
    while time.monotonic() < until:
        try:
            line = b.lines.get(timeout=0.2)
        except queue.Empty:
            if not b.alive():
                return None
            continue
        if line is None:
            return None
        m = FRAME.match(line)
        if m:
            b.frame = int(m.group(1))
        show(line)
        if want is not None:
            hit = want(line, b.frame)
            if hit:
                return hit
    return None


def enrol(b: Board, key: str, name: str, visit: int, visits: int,
          *, speak: bool, timeout: float) -> bool:
    """One press, one held scene, one receipt from the board.

    The receipt is not optional. A press that the board did not act on - the
    scene moved, the window was still open from the last one - is silent from
    this side, and an operator who assumes it landed goes on to enrol the second
    class against a first that does not exist. So this waits for the board to
    name the class back, and says so if it never does.
    """
    if key == "0":
        what = "Empty scene. Take everything out of shot"
    else:
        what = f"Show it: {name}"
    if visits > 1:
        what += f". Visit {visit} of {visits}"
    cue(what, speak=speak)
    try:
        input("    press Enter when the scene is right (Ctrl-C to stop) ")
    except EOFError:
        raise KeyboardInterrupt from None
    if not b.alive():
        return False
    b.press(key)
    print(f"    holding - the next 20 frames are '{name}'. "
          f"KEEP IT STILL.", flush=True)

    def receipt(line: str, _frame: int):
        m = ENROL_ONE.match(line)
        return m if m and m.group(1) == name else None

    got = drain(b, time.monotonic() + timeout, receipt)
    if got is None:
        print(f"    NO RECEIPT for '{name}' in {timeout:.0f} s. The board did "
              f"not confirm the press,\n    so this reference does not exist. "
              f"Nothing later in this run can fix that.", file=sys.stderr)
        return False
    return True


def live(b: Board, names: list[str], tally: dict[str, int], *,
         tty: bool) -> None:
    """Mirror the board's verdict until Ctrl-C.

    Nothing is smoothed, held or voted on. The board decides once a frame and
    this prints what it decided; a display that steadied the answer would be a
    second classifier nobody scored, sitting on top of the one that was.
    host/watch.py is where confirmation lives, and it says why there.

    The tally is passed in rather than returned because the only way out of
    this loop is Ctrl-C, and a count that is lost by the thing that ends the
    run is a count nobody ever sees.
    """
    last = None
    while True:
        try:
            line = b.lines.get(timeout=0.5)
        except queue.Empty:
            if not b.alive():
                return
            continue
        if line is None:
            return
        m = FRAME.match(line)
        if not m:
            show(line)
            continue
        b.frame = int(m.group(1))

        # THREE VERDICTS, NOT TWO, and the third is the easy one to lose - the
        # same trap host/watch.py fell into until 2026-08-22. A frame line that
        # ends in neither MATCH nor "nothing there" is not a missing answer: it
        # is the board saying the gate is open and nothing you asked about
        # fits, which on a demonstration is often the interesting frame.
        hit = MATCHED.search(line)
        if hit:
            verdict, detail = hit.group(1), f"by {hit.group(2)}"
        elif ABSENT.search(line):
            verdict, detail = EMPTY_NAME, ""
        else:
            verdict, detail = UNKNOWN_NAME, ""
        tally[verdict] = tally.get(verdict, 0) + 1

        if tty:
            print(f"\r  frame {b.frame:5d}   {verdict:<30.30s} {detail:<14s}",
                  end="", flush=True)
        elif verdict != last:
            print(f"  frame {b.frame:5d}   {verdict} {detail}".rstrip(),
                  flush=True)
        last = verdict


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Show the board two things and let it tell them apart.")
    ap.add_argument("phrases", nargs="*",
                    help="the phrases to tell apart, in plain English. Two is "
                         "what this is for; up to six work")
    ap.add_argument("--visits", type=int, default=2, metavar="N",
                    help="how many times each class is shown. The firmware "
                         "folds later visits into the same reference, and one "
                         "visit measures only how still your hand was - the "
                         "staging variance that actually decides runs needs a "
                         "second (default 2)")
    ap.add_argument("--no-empty", dest="empty", action="store_false",
                    help="skip enrolling the empty scene. The board then falls "
                         "back to a fitted band for 'is anything there', which "
                         "scores far worse - see bench/README.md #18")
    ap.add_argument("--quiet", dest="speak", action="store_false",
                    help="banners only, no spoken cues")
    ap.add_argument("--out", type=Path, default=Path("/tmp/spot.log"),
                    help="where demo.py's full output is kept (default "
                         "/tmp/spot.log)")
    ap.add_argument("--port", default=None)
    ap.add_argument("--enrol-timeout", type=float, default=90.0, metavar="S",
                    help="how long to wait for the board to confirm a press")
    args = ap.parse_args()

    if len(args.phrases) < 2:
        raise SystemExit(
            'two phrases, please:\n'
            '  uv run host/spot.py "an opened book" "a closed book"')

    # THE BOARD IS CHECKED BEFORE THE TEACHER IS. Loading SigLIP costs about a
    # minute, and finding out after it that the board is in BOOTSEL wastes the
    # whole minute for a fault that was visible up front. The two failures wear
    # the same symptom from here and want opposite fixes, which is why board.py
    # returns a token rather than a bool.
    what, detail = board.state()
    if what == "bootsel":
        print(f"the board is in BOOTSEL ({detail}) - it is running no "
              f"firmware.\n"
              f"  uv run host/bootsel.py --flash firmware/build/forgix_m9.uf2",
              file=sys.stderr)
        return 1
    if what == "absent":
        print(f"no board on the bus ({detail}).\n  {board.recover()}",
              file=sys.stderr)
        return 1

    names = [board_name(p) for p in args.phrases]
    clipped = [(p, n) for p, n in zip(args.phrases, names, strict=True)
               if p != n]
    if clipped:
        print("clipped to the board's 23-byte name slot - the display below "
              "uses the short form:", file=sys.stderr)
        for was, now in clipped:
            print(f"  {was!r} -> {now!r}", file=sys.stderr)
    if len(set(names)) != len(names):
        raise SystemExit("two phrases clip to the same board name; the "
                         "receipts could not be told apart")

    cmd = ["uv", "run", str(ROOT / "host/demo.py"),
           "--ask", "--leave-running", "--frames", "0",
           "--out", str(args.out), *args.phrases]
    if args.port:
        cmd += ["--port", args.port]

    print(f"\nspot      : {len(args.phrases)} phrases, {args.visits} visit"
          f"{'' if args.visits == 1 else 's'} each"
          f"{', plus the empty scene' if args.empty else ''}")
    for i, n in enumerate(names, 1):
        print(f"            '{i}' = {n}")
    print(f"log       : {args.out}  (not a bench record - nothing here is "
          f"archived)")
    print("\nLEAVE THE SCENE EMPTY. The board averages its first "
          f"{BG_TAU} frames into a\nbackground and then never revises it, so "
          "anything in shot now is\nsubtracted out of every score for the rest "
          "of the run.\n"
          "Loading the teacher takes about a minute.\n", flush=True)

    b = Board(cmd)
    tally: dict[str, int] = dict.fromkeys([*names, EMPTY_NAME, UNKNOWN_NAME], 0)
    try:
        # The teacher load, the port open and the handshake all happen before
        # the first frame line, and how long that takes depends on whether the
        # model is in the page cache. Waiting on the frame COUNT rather than on
        # a clock means the warm-up is over when the board says it is.
        print("waiting for the board to finish its background "
              f"({BG_TAU} frames)...", flush=True)
        done = drain(b, time.monotonic() + 300.0,
                     lambda _line, frame: frame >= BG_TAU)
        if done is None:
            print("the board never got through its warm-up. See the log.",
                  file=sys.stderr)
            return 1

        # Every visit to a class before every visit to the next would let the
        # light and the desk drift between the two references in a way that
        # tracks WHICH class it is - which is the confound #30 spent four runs
        # on. Interleaving the visits does not remove the drift, it just stops
        # it lining up with the thing being measured.
        for visit in range(1, args.visits + 1):
            for i, name in enumerate(names, 1):
                if not enrol(b, str(i), name, visit, args.visits,
                             speak=args.speak,
                             timeout=args.enrol_timeout):
                    return 1

        if args.empty and not enrol(b, "0", EMPTY_NAME, 1, 1,
                                    speak=args.speak,
                                    timeout=args.enrol_timeout):
            return 1

        # The board prints how far apart the references landed and how much
        # each wobbles, and that one line is the honest forecast for the run.
        # Forwarded, not interpreted: turning the ratio into a pass or a fail
        # here would be a constant fitted on this desk, which is the thing this
        # repo does not do.
        drain(b, time.monotonic() + 20.0,
              lambda line, _f: ENROL_PAIR.match(line))

        cue("Ready. Show me either one.", speak=args.speak)
        print("    Ctrl-C to stop.\n", flush=True)
        live(b, names, tally, tty=sys.stdout.isatty())
    except KeyboardInterrupt:
        pass
    finally:
        if sys.stdout.isatty():
            print(flush=True)
        b.stop()

    total = sum(tally.values())
    if total:
        print(f"\n{total} frames scored:")
        for name, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<30.30s} {n:5d}  {100.0 * n / total:5.1f}%")
        # NOT AN ACCURACY. Nobody recorded what was actually in front of the
        # camera, so these are shares of what the board said and not of what
        # was true - the two are only the same if the operator held each thing
        # for the same time and never got it wrong. tools/score_cue.py is what
        # turns held scenes into a number, and it needs host/cue.py's cues.
        print("  (what the board said, not what was there - this run held "
              "nothing out)")
    print(f"\nfull output: {args.out}")
    print("the board is still running - the next start skips the flash.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
