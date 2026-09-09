# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Enrol the same classes over and over inside ONE boot, and dump each time.

    uv run --script tools/enrol_repeat.py --rounds 6 \\
        --out /tmp/enrol-repeat.log \\
        "an opened book" "a closed book"

Issue #34 asks whether a reference saved in one boot means anything in the next
one. That question has no scale until something says how much a reference moves
when *nothing* has changed - same boot, same room, same frozen background, same
object put back in the same place. This produces that number, and step 3 is the
same loop with a power cycle in it.

**Why this is not `host/cue.py`.** cue.py is an accuracy harness: it rotates
scenes, splits enrolment visits from held-out visits, writes a sidecar and
scores against ground truth. None of that applies here. There is no held-out
set, no ground truth and no accuracy - the output is a set of reference vectors
and the distances between them. Sharing the code would mean carrying the
sidecar contract into a measurement that has nothing to put in it.

**Why `'Y'` and not just pressing the digit twice.** Pressing '1' a second time
folds a second visit into a running mean, which is the enrolment guard working
as designed and the wrong thing entirely here: it produces one reference that
saw 40 frames, not two references that saw 20 each. `'Y'` drops the references
and keeps the background, so every round starts from nothing and lands in the
same coordinate system as the last one. That is the whole reason the key exists.

The operator is looking at the desk, not the screen, so every instruction is
spoken. `--quiet` falls back to a banner and a bell.
"""

import argparse
import contextlib
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The board freezes its background over the first `--bg-tau` frames, so the desk
# has to be empty until then and the first enrolment must land after it. A
# handful of frames of margin costs nine seconds and removes the one failure
# that would invalidate every round at once.
BG_MARGIN = 6

# 20 frames of enrolment at ~0.9 s/frame is 18 s; the wait is on the receipt
# line, not the clock, so this only fires when something is actually wrong.
RECEIPT_TIMEOUT_S = 90.0

# Long enough for a hand to leave the frame. The enrolment window opens the
# moment the key lands, so this is spent before the press and not after.
SETTLE_S = 4.0


def cue(text: str, *, speak: bool) -> None:
    """A banner, a bell, and a voice. cue.py's, and for cue.py's reason."""
    bar = "=" * 60
    print(f"\n{bar}\n>>> {text}\n{bar}\n", flush=True)
    sys.stdout.write("\a")
    sys.stdout.flush()
    if speak:
        # FileNotFoundError means not macOS; the banner and the bell still fired.
        with contextlib.suppress(FileNotFoundError):
            subprocess.run(["say", text], check=False)


class Board:
    """demo.py as a subprocess: keys down its stdin, lines off its stdout."""

    def __init__(self, proc: subprocess.Popen, raw: bool):
        self.proc = proc
        self.raw = raw
        self.q: queue.Queue[str] = queue.Queue()
        self.frame = -1
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            m = re.match(r"frame +(\d+) ", line)
            if m:
                self.frame = int(m.group(1))
                if not self.raw:
                    # 120 score lines a minute would bury the receipts, and the
                    # receipts are the measurement.
                    self.q.put(line)
                    continue
            sys.stdout.write(line)
            sys.stdout.flush()
            self.q.put(line)

    def press(self, key: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"!{key}\n")
        self.proc.stdin.flush()

    def wait_for(self, pattern: str, timeout: float = RECEIPT_TIMEOUT_S) -> str:
        """Block until a line matches, or raise. Never sleeps a fixed length.

        A fixed sleep would be wrong in both directions: too short and the next
        cue lands inside the enrolment window it is meant to follow, too long
        and a 12-minute run becomes 20. The board says when it is done.
        """
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no line matching {pattern!r} in {timeout}s")
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if rx.search(line):
                return line

    def wait_frame(self, n: int, timeout: float = 180.0) -> None:
        end = time.monotonic() + timeout
        while self.frame < n:
            if time.monotonic() > end:
                raise TimeoutError(f"board never reached frame {n}")
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="repeat one enrolment inside one boot and dump each time")
    ap.add_argument("queries", nargs="+",
                    help="the phrases, passed to demo.py unchanged. One scene "
                         "cue is generated per phrase, in order")
    ap.add_argument("--rounds", type=int, default=6, metavar="K",
                    help="times to enrol every class from scratch, default 6. "
                         "K rounds give K*(K-1)/2 displacements per class, so "
                         "6 buys 15 - enough for a spread, and about 12 min")
    ap.add_argument("--out", type=Path, default=Path("/tmp/enrol-repeat.log"),
                    help="demo.py's --out; this is the file that gets archived")
    ap.add_argument("--bg-tau", type=int, default=30, metavar="N",
                    help="demo.py's --bg-tau, forwarded. The desk must be "
                         "empty until this frame or the background absorbs "
                         "whatever is in shot. Default 30, demo.py's own")
    ap.add_argument("--still", action="store_true",
                    help="cue once, then never again: every round enrols the "
                         "same untouched scene. This is the other half of the "
                         "measurement - the staged run mixes the board's noise "
                         "with how differently the operator placed the object, "
                         "and only a run where nothing moves separates them. "
                         "The classes come out on top of each other, so the "
                         "run has no meaningful sep of its own and the staged "
                         "run's is what its numbers get divided by")
    ap.add_argument("--no-empty", action="store_true",
                    help="skip the empty-scene reference each round. eref is a "
                         "reference like any other and #18's presence rule "
                         "rides on it, so it is measured by default")
    ap.add_argument("--quiet", action="store_true",
                    help="banner and bell only, no spoken cue")
    ap.add_argument("--raw", action="store_true",
                    help="print demo.py's frame lines too")
    args = ap.parse_args()

    speak = not args.quiet
    cmd = ["uv", "run", "--script", "host/demo.py",
           "--ask", "--leave-running", "--frames", "0",
           "--bg-tau", str(args.bg_tau), "--out", str(args.out),
           *args.queries]

    print(f"enrol-rep : {args.rounds} rounds, {len(args.queries)} classes"
          f"{'' if args.no_empty else ' + the empty scene'}, log {args.out}")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    b = Board(proc, args.raw)
    rc = 0
    try:
        cue("Take everything out of shot. Learning the background now.",
            speak=speak)
        b.wait_frame(args.bg_tau + BG_MARGIN)
        cue("Background frozen. Do not move the camera for the rest of this "
            "run.", speak=speak)

        for r in range(1, args.rounds + 1):
            print(f"\nenrol-rep : round {r} of {args.rounds}\n")
            for i, name in enumerate(args.queries):
                cue(f"Round {r} of {args.rounds}. Show {name}. Hold still.",
                    speak=speak)
                time.sleep(SETTLE_S)
                b.press(str(i + 1))
                b.wait_for(r"^enrol +: " + re.escape(name) + r", level")
            if not args.no_empty:
                cue(f"Round {r} of {args.rounds}. Empty. Take it all out.",
                    speak=speak)
                time.sleep(SETTLE_S)
                b.press("0")
                b.wait_for(r"^enrol +: the empty scene, level")

            # Dump BEFORE forgetting, obviously, but also before any cue: the
            # references are what the round produced and a spoken sentence is
            # four seconds in which the operator can nudge the desk.
            b.press("T")
            b.wait_for(r"^enroldump : end")
            b.press("Y")
            b.wait_for(r"^enrolment : forgotten")

        cue("Done. All rounds recorded.", speak=speak)
    except KeyboardInterrupt:
        print("\n(stopped by Ctrl-C)", file=sys.stderr)
        rc = 1
    except TimeoutError as e:
        # Not a crash to swallow: every round after this one would be measured
        # against a board in an unknown enrolment state.
        print(f"\nenrol-rep : GAVE UP - {e}", file=sys.stderr)
        rc = 2
    finally:
        # demo.py inherits SIG_IGN for SIGINT when this script is itself a
        # background job, so terminate() rather than a signal it may ignore.
        # --leave-running means nothing is owed to the board on the way out.
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)

    dumps = args.out.read_text().count("enroldump : end") if args.out.exists() \
        else 0
    print(f"\nenrol-rep : {dumps} dumps in {args.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
