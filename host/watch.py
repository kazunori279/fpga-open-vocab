# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Watch one scene and say when it changes. The appliance, as one command.

    uv run --script host/watch.py "a glass with tea / an empty glass / a glass"
    uv run --script host/watch.py --enrol "a glass with tea" "an empty glass"

host/demo.py already does the whole measurement - bitstream, flash check, camera,
queries, the #18 decision rule with its reject option - and prints a line per
frame. What it does not do is any of the things that turn that into a monitor
somebody leaves running: it reports every frame whether or not anything happened,
it exits when the port goes quiet, and it has no notion of an event.

So this adds three things and re-derives nothing:

  1. **A confirmed state**, instead of a per-frame winner.
  2. **An event on transition**, to stdout, to `--jsonl`, and to `--on-change`.
  3. **A restart**, because a monitor that stops at the first USB hiccup is a
     demo with a longer timeout.

demo.py still does all of it. This reads its stdout, exactly as host/cue.py does,
and for the same reason: there is one implementation of the protocol and one
place that decides what a frame means.

WHY A CONFIRMED STATE, AND WHAT IT CANNOT FIX
---------------------------------------------
The board's per-frame verdict flickers. Over the segments of `bench/cue/` where
the operator was holding one scene still, the winner changes from one frame to
the next between 0 and 9 times per hundred frames depending on the contrast, and
at 322 ms/frame that is a spurious flip every seven seconds at the bad end. An
event stream built on the raw winner would be unreadable.

`--confirm N` fixes that: a state has to survive N frames in a row before it is
announced. It is a latency knob, not a quality one - N = 5 is about 1.6 s - and
it is a flag rather than a constant because the right value depends on how fast
the thing you are watching actually moves.

**It cannot fix a wrong side, and it makes a wrong side worse.** In the same
segments, `an empty glass` was the board's winner on 6.7% of the frames it was
the truth for. That is not flicker, it is an axis pointing backwards, and
confirmation turns "wrong most of the time" into "wrong, steadily, with an
event to prove it". If your contrast has an absence on one side, read
`docs/fit.md` Screen 0 before you read any output from this file.

TEXT MODE HAS NO REJECT, AND SAYS SO
------------------------------------
Without `--enrol` the board ranks your phrases and the top one wins. Something
always wins. Point the camera at a wall and it will still name a state, because
you never gave it the option of saying nothing is there.

`--enrol` is the option. It walks you through holding each state in front of the
camera while the board takes a reference off 20 frames, and from then on the
board answers with its own `MATCH ...` / `- (nothing there)` verdict, which this
file mirrors and never recomputes. That verdict is the one `docs/architecture.md`
describes and the only one with a reject in it.

The state names in the two modes are not interchangeable and the JSONL says
which mode produced a line, because a run whose `mode` is `text` has no
`unknown` in it - not because nothing was ever unrecognisable, but because
nothing could be.

WHAT COMES OUT
--------------
    12:04:31  a glass with tea      (held 4m12s)
    12:08:43  an empty glass        (held 11s, then back)
    12:08:54  a glass with tea

One line per confirmed transition and nothing in between, so a day of a stable
scene is a short file. `--jsonl` writes the same events as objects, with the
frame number, the board's margin, and the mode. `--on-change CMD` runs CMD with
FGX_STATE, FGX_PREV, FGX_FRAME and FGX_MARGIN in its environment; it is spawned
and not waited on, because a hook that blocks would stall the reader and a
stalled reader fills demo.py's pipe.

See `docs/monitor.md` for the five-minute version and `docs/fit.md` for whether
your contrast is one this board can do at all.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# demo.py's frame line. Kept in the same shape as host/cue.py's copy on purpose:
# both mirror one format that firmware/m9.c owns, and if that format moves, two
# files should break rather than one file silently reading the wrong column.
#   frame   125 :  an opened book~ +1.35*  a closed book~ -1.86   led 255/  0 ...
FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led\s")
SCORE = re.compile(r"([^+\-]+?)\s+([+-][\d.]+)(\*?)(?=\s\s|\s*$)")

# The tail of the same line, which is the board's own #18 verdict. Present only
# once something has been enrolled. MIRROR, DO NOT RECOMPUTE - host/cue.py
# learned that the hard way when its bars ran a softmax over the raw z and
# disagreed with the board's MATCH on the same frame.
#
# `, nearer by N` IS OPTIONAL, and getting that wrong is how this regex first
# shipped. firmware/m9.c:2371 prints it only `if (m21)`, so a real log carries
# both forms - 416 MATCH lines and 354 `nearer by` in
# bench/cue/m9_cue-20260820-142249.log alone. Requiring it does not fail
# loudly: the 15% without it fall through to the text-mode branch below and get
# read off the score leaderboard instead of off the board's verdict, in
# different units, with nothing in the output saying so.
MATCHED = re.compile(r"\sMATCH (.+?) \(cos [-+]?[\d.]+"
                     r"(?:, nearer by ([-+]?[\d.]+))?\)")
ABSENT = re.compile(r"\s-\s\(nothing there\)")

# THE BOARD IS NOT READY WHEN IT STARTS PRINTING FRAMES, AND SAYS WHEN IT IS.
# demo.py freezes the background over the first --bg-tau frames, so everything
# before this line is scored against a baseline that is still moving. Replaying
# the three cue runs of 2026-08-20, every single spurious event was before this
# line - four per run - and after it the two cube runs are 7 true changes out of
# 7 with nothing spurious at all. Waiting is not conservatism, it is the whole
# difference between a usable event stream and a noisy one.
BACKGROUND = re.compile(r"^background:")

# The other half of ready, when enrolling: the board prints this once per key,
# and the last one means every class has a reference. Until then a MATCH is
# against a partial set.
ENROLLED = re.compile(r"^enrol\s+:\s+(\d+) classes, nearest pair")

# The board takes a reference off this many frames after a key is pressed, and
# says so ("the next 20 frames are ..."). MUST MATCH firmware/m9.c. It is here
# to size the countdown, not to decide anything.
ENROL_FRAMES = 20

# What one frame costs, measured: "546 frames timed, 322 ms/frame mean" on
# 2026-08-20. Only used to turn seconds into frame numbers for the enrolment
# schedule and to print a human countdown, so being 10% out is harmless.
MS_PER_FRAME = 322

UNKNOWN = "unknown"


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_frame(line: str) -> tuple[int, str, float | None] | None:
    """(board frame, state name, margin) off one demo.py frame line, or None.

    Two sources, and which one is used is the difference between the two modes:
    the board's MATCH tail when something has been enrolled, and the top-scoring
    phrase when nothing has. The tail wins whenever it is there - it is the
    board's verdict, the other is this side reading a leaderboard.

    The margin is None when the board named a match but did not quote a gap.
    Not 0.0: zero is a real margin and means the opposite thing, and a caller
    comparing it against --floor would drop exactly the frames the board was
    most sure about.
    """
    m = FRAME.match(line)
    if not m:
        return None
    frame = int(m.group(1))

    if ABSENT.search(line):
        return frame, UNKNOWN, None
    hit = MATCHED.search(line)
    if hit:
        gap = float(hit.group(2)) if hit.group(2) else None
        return frame, hit.group(1).strip().rstrip("~"), gap

    # Text mode. The scores are printed best-first, so the head of the list is
    # the winner and the margin is its gap to the runner-up. A single query has
    # no runner-up and so no margin; it also has no contrast, which is a
    # different problem and one docs/fit.md Screen 0 is about.
    scores = [(n.strip().rstrip("~"), float(v))
              for n, v, _ in SCORE.findall(m.group(2))]
    if not scores:
        return None
    gap = scores[0][1] - scores[1][1] if len(scores) > 1 else 0.0
    return frame, scores[0][0], gap


class Debounce:
    """Turn a per-frame winner into a state that has to earn the announcement.

    A candidate becomes the state after `confirm` consecutive frames agreeing.
    Consecutive, not a majority of a window: a scene mid-change alternates, and
    a majority rule fires in the middle of that while a run rule waits for it to
    settle. The cost is that one dropped frame restarts the count, which at
    3 fps is a third of a second and not worth defending against.
    """

    def __init__(self, confirm: int, floor: float):
        self.confirm = confirm
        self.floor = floor
        self.state: str | None = None
        self.since = time.monotonic()
        self._cand: str | None = None
        self._run = 0

    def push(self, name: str,
             margin: float | None) -> tuple[str, str | None, float] | None:
        """Feed one frame. Returns (new, previous, held_seconds) on a change."""
        # A margin under the floor is not evidence for anything, so it is not
        # counted as evidence against the current state either: the run simply
        # does not advance. Without this a scene crossing between two states
        # announces the midpoint. A frame with no margin at all is not screened
        # - absence of the number is not a small number.
        if (self.floor and margin is not None and abs(margin) < self.floor
                and name != UNKNOWN):
            self._cand, self._run = None, 0
            return None

        if name == self._cand:
            self._run += 1
        else:
            self._cand, self._run = name, 1

        if self._run < self.confirm or name == self.state:
            return None

        prev, held = self.state, time.monotonic() - self.since
        self.state, self.since = name, time.monotonic()
        return name, prev, held


class Ready:
    """Has the board finished getting ready? Ask the board, do not count frames.

    Two gates, both read off lines the board prints for its own reasons:

      - the background freeze, which every run has, and
      - every enrolled class having a reference, which only an --enrol run has.

    Counting frames instead would need --bg-tau, the enrolment schedule and the
    frame rate to all be right at once, and would be wrong the first time any of
    them moved. The board already knows; this waits to be told.
    """

    def __init__(self, want_classes: int):
        self.want_classes = want_classes
        self.background = False
        self.classes = 0
        self.why = ""

    def observe(self, line: str) -> bool:
        """Feed a non-frame line. True if this line is the one that made it ready."""
        was = bool(self)
        if BACKGROUND.match(line):
            self.background = True
        got = ENROLLED.match(line)
        if got:
            self.classes = max(self.classes, int(got.group(1)))
        if bool(self) and not was:
            self.why = ("background frozen"
                        + (f", {self.classes} classes enrolled"
                           if self.want_classes else ""))
            return True
        return False

    def __bool__(self) -> bool:
        return self.background and self.classes >= self.want_classes


def human(secs: float) -> str:
    if secs < 60:
        return f"{secs:.0f}s"
    if secs < 3600:
        return f"{secs / 60:.0f}m{secs % 60:02.0f}s"
    return f"{secs / 3600:.0f}h{(secs % 3600) / 60:02.0f}m"


def enrol_schedule(states: list[str], lead: float, settle: int) -> list[str]:
    """`--enrol FRAME:KEY` arguments for demo.py, one per state.

    demo.py wants board frame numbers up front, because the caller computing
    them knows the schedule and an off-by-one enrols a hand still in shot. So
    the operator gets a countdown here and the frames are arithmetic: `lead`
    seconds to get the first state in place, then each window is ENROL_FRAMES
    plus `settle` frames of slack before the next cue.
    """
    fpsec = 1000.0 / MS_PER_FRAME
    at = int(lead * fpsec) + settle
    out = []
    for i in range(len(states)):
        out.append(f"{at}:{i + 1}")
        at += ENROL_FRAMES + settle
    return out


def announce_enrolment(states: list[str], sched: list[str], lead: float,
                       settle: int) -> None:
    """Tell the operator what is about to be asked of them, before it is asked.

    The board prints "HOLD THE SCENE STILL" when each window opens, which is
    already too late to walk across the room. This prints the whole running
    order first, in seconds, so nobody is surprised by the second cue.
    """
    print("\nenrolment, in order - each window is "
          f"{ENROL_FRAMES * MS_PER_FRAME / 1000:.0f}s of held scene:\n",
          file=sys.stderr)
    for state, spec in zip(states, sched, strict=True):
        secs = int(spec.split(":")[0]) * MS_PER_FRAME / 1000
        print(f"  t+{secs:5.0f}s   put the scene in '{state}' and hold it",
              file=sys.stderr)
    print(f"\n  ({lead:.0f}s of lead time, {settle} frames of slack between "
          f"windows; --enrol-lead and --enrol-settle move both)\n",
          file=sys.stderr)


def fire_hook(cmd: str, event: dict) -> None:
    """Run --on-change, and do not wait for it.

    Spawned with the event in the environment rather than on the command line so
    a state name containing a quote cannot become shell syntax. Not waited on
    because this thread is also demo.py's reader: a hook that takes a minute
    would fill the pipe and stall the board's own output, which is the failure
    host/cue.py documents in its snapshot thread.
    """
    env = dict(os.environ)
    env.update({f"FGX_{k.upper()}": str(v) for k, v in event.items()})
    try:
        subprocess.Popen(cmd, shell=True, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:                      # a bad --on-change, not a board
        print(f"(--on-change failed to start: {exc})", file=sys.stderr)


def demo_command(args, queries: list[str], sched: list[str]) -> list[str]:
    cmd = ["uv", "run", "--script", str(ROOT / "host/demo.py")]
    if args.bitstream:
        cmd += ["--bitstream", str(args.bitstream)]
    if args.export:
        cmd += ["--export", str(args.export)]
    if args.port:
        cmd += ["--port", args.port]
    # demo.py's --idle stops it when the port goes quiet. A monitor wants that
    # to mean "the board is gone, restart", not "nothing happened for a while",
    # and the board prints a line per frame even on a still scene - so quiet
    # really is a fault and the default 45 s is about right. Kept as a flag
    # because a slower --bg-tau or a wedged sensor changes what quiet means.
    cmd += ["--idle", str(args.idle), "--out", str(args.log)]
    for spec in sched:
        cmd += [f"--enrol={spec}"]
    return cmd + queries


def run_once(args, queries: list[str], sched: list[str], deb: Debounce,
             sink) -> int:
    """One demo.py lifetime. Returns its exit code."""
    proc = subprocess.Popen(demo_command(args, queries, sched),
                            stdout=subprocess.PIPE, stderr=None,
                            text=True, bufsize=1, cwd=ROOT)
    mode = "enrolled" if sched else "text"
    want_classes = len(sched)
    ready = Ready(want_classes)
    try:
        for line in proc.stdout:
            if args.verbose:
                sys.stderr.write(line)
            got = parse_frame(line)
            if got is None:
                # The board's own enrolment progress is worth showing even when
                # the frame lines are not: it is the only feedback the operator
                # holding a glass still is going to get.
                if line.startswith("enrol "):
                    sys.stderr.write(line)
                if ready.observe(line):
                    print(f"ready   : {ready.why}", file=sys.stderr)
                continue
            if not ready:
                continue
            frame, name, margin = got
            change = deb.push(name, margin)
            if change is None:
                continue
            new, prev, held = change
            sink(new, prev, held, frame, margin, mode)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return proc.returncode or 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Watch one scene and report confirmed state changes.")
    ap.add_argument("queries", nargs="+",
                    help="the states to tell apart, passed to host/demo.py "
                         "unchanged - so 'a / b / c' is demo.py's state-query "
                         "syntax and a bare phrase is a plain one")
    ap.add_argument("--confirm", type=int, default=5, metavar="N",
                    help="frames a state must hold before it is announced "
                         "(default 5, about 1.6 s at 322 ms/frame)")
    ap.add_argument("--floor", type=float, default=0.0, metavar="M",
                    help="ignore frames whose margin is under this, so a scene "
                         "mid-change does not announce its midpoint "
                         "(default 0, off - the units differ between modes)")
    ap.add_argument("--enrol", action="store_true",
                    help="walk through enrolling each state off the live "
                         "camera. THIS IS THE MODE WITH A REJECT OPTION; "
                         "without it something always wins")
    ap.add_argument("--enrol-lead", type=float, default=20.0, metavar="SECS",
                    help="seconds before the first enrolment window (default "
                         "20, which is time to walk to the scene)")
    ap.add_argument("--enrol-settle", type=int, default=10, metavar="N",
                    help="frames of slack between enrolment windows, dropped "
                         "because a hand is in shot (default 10)")
    ap.add_argument("--jsonl", type=Path, default=None, metavar="PATH",
                    help="append one JSON object per transition")
    ap.add_argument("--on-change", default=None, metavar="CMD",
                    help="shell command per transition, with FGX_STATE, "
                         "FGX_PREV, FGX_FRAME, FGX_MARGIN in the environment. "
                         "Spawned, not waited on")
    ap.add_argument("--restart", dest="restart", action="store_true",
                    default=True,
                    help="relaunch demo.py when it exits (the default)")
    ap.add_argument("--no-restart", dest="restart", action="store_false",
                    help="exit when demo.py does, for a scripted run")
    ap.add_argument("--restart-wait", type=float, default=15.0, metavar="SECS",
                    help="pause before relaunching (default 15). A board that "
                         "is power-cycling needs longer than a dropped pipe")
    ap.add_argument("--idle", type=float, default=45.0,
                    help="demo.py's --idle, forwarded")
    ap.add_argument("--bitstream", type=Path, default=None,
                    help="demo.py's --bitstream, forwarded")
    ap.add_argument("--export", type=Path, default=None,
                    help="demo.py's --export, forwarded")
    ap.add_argument("--port", default=None, help="demo.py's --port, forwarded")
    ap.add_argument("--log", type=Path, default=Path("/tmp/fgx-watch-demo.log"),
                    help="demo.py's --out, where the raw frame log goes")
    ap.add_argument("--verbose", action="store_true",
                    help="also echo demo.py's own output")
    args = ap.parse_args()

    if args.confirm < 1:
        raise SystemExit("--confirm must be at least 1")

    sched: list[str] = []
    if args.enrol:
        # The enrolment keys are '1'..'6' by query position, so the states have
        # to be one query each. A 'a / b / c' spec is one query with three
        # phrases in it and would enrol the whole spec under one key, which is
        # not what anybody passing --enrol means.
        if any("/" in q for q in args.queries):
            raise SystemExit(
                "--enrol takes one plain phrase per state, because the board's "
                "enrolment keys are per query and 'a / b / c' is one query.\n"
                "  wanted: --enrol \"a glass with tea\" \"an empty glass\"\n"
                "  not:    --enrol \"a glass with tea / an empty glass\"")
        sched = enrol_schedule(args.queries, args.enrol_lead,
                               args.enrol_settle)
        announce_enrolment(args.queries, sched, args.enrol_lead,
                           args.enrol_settle)

    # Line-buffered and appended, not written at the end: the point of a
    # monitor's log is that it is readable while the monitor is still running,
    # and the run that matters is the one that ended in a power cut.
    if args.jsonl:
        with args.jsonl.open("a", buffering=1) as fh:
            return supervise(args, sched, fh)
    return supervise(args, sched, None)


def supervise(args, sched: list[str], jsonl) -> int:
    """Run demo.py, forever if asked, feeding transitions to the sinks."""
    deb = Debounce(args.confirm, args.floor)

    def sink(new, prev, held, frame, margin, mode):
        stamp = now()
        tail = f"   (held {human(held)})" if prev else ""
        print(f"{stamp:%H:%M:%S}  {new:<28}{tail}", flush=True)
        event = {"time": stamp.isoformat(), "state": new, "prev": prev,
                 "frame": frame, "mode": mode,
                 "margin": None if margin is None else round(margin, 4),
                 "held": None if prev is None else round(held, 1)}
        if jsonl:
            jsonl.write(json.dumps(event) + "\n")
        if args.on_change:
            fire_hook(args.on_change, event)

    print(f"watching: {', '.join(args.queries)}", file=sys.stderr)
    print(f"mode    : {'enrolled - the board can say nothing is there' if sched else 'text - something always wins, see docs/fit.md'}",
          file=sys.stderr)
    print(f"confirm : {args.confirm} frames"
          f"{f', margin floor {args.floor}' if args.floor else ''}",
          file=sys.stderr)

    runs = 0
    try:
        while True:
            runs += 1
            code = run_once(args, args.queries, sched, deb, sink)
            if not args.restart:
                return code
            # The enrolment schedule is a one-shot: relaunching demo.py would
            # re-run it, and the operator is not standing there any more. A
            # restarted monitor comes back in text mode unless the board kept
            # its references, which it does not across a reboot - so say so
            # rather than silently changing what the output means.
            if sched:
                print(f"\n(demo.py exited [{code}] and the enrolment cannot be "
                      f"replayed unattended, so this run ends here. Restart it "
                      f"yourself when you can hold the scenes again.)",
                      file=sys.stderr)
                return code
            print(f"\n(demo.py exited [{code}] after run {runs}; relaunching in "
                  f"{args.restart_wait:.0f}s - Ctrl-C to stop)",
                  file=sys.stderr)
            # The confirmed state does not survive a restart, because the board
            # re-freezes its background and the first frames after that are the
            # settling ones. Clearing it means the first real state is announced
            # again, which is correct: nobody watching the output knows the
            # board went away.
            deb.state = None
            time.sleep(args.restart_wait)
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
