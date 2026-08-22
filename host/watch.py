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

THREE VERDICTS, NOT TWO, AND ALL THREE COME FROM THE BOARD
----------------------------------------------------------
Every frame line ends in one of `MATCH <name>`, `- (nothing there)`, or a bare
`-`, and this file mirrors all three and computes none of them:

    MATCH <name>       a query or an enrolled class cleared its threshold
    - (nothing there)  the presence gate is closed; the scene is empty
    -                  the gate is open and nothing cleared: `unrecognised`

The third one is the one that is easy to lose, and losing it is what this file
did until 2026-08-22 - see `parse_frame`. It is not an absence of a verdict. It
is the board saying something is in front of the camera that none of your
phrases fit, which for a monitor is often the event you most wanted.

`--enrol` changes what a MATCH means, not whether there is a reject. It walks
you through holding each state in front of the camera while the board takes a
reference off 20 frames, and from then on `MATCH` is against those references
under the two-stage rule `docs/architecture.md` describes rather than against a
text threshold. Without it the board still declines - a text query has a
calibrated threshold and sits below it most of the time.

The JSONL still records which mode produced a line, because the state names are
not interchangeable between them even though the verdicts are.

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

# There is deliberately no regex for the score column. This file used to have
# one and read the leaderboard off it, which is the bug parse_frame's docstring
# is about: the scores are the board's working, the tail is its answer, and
# MIRROR, DO NOT RECOMPUTE means reading the answer. host/cue.py learned the
# same lesson when its bars ran a softmax over the raw z and disagreed with the
# board's MATCH on the same frame.
#
# The tail is the board's own #18 verdict, and every frame line has one.
#
# `, nearer by N` IS OPTIONAL, and getting that wrong is how this regex first
# shipped. firmware/m9.c:2371 prints it only `if (m21)`, so a real log carries
# both forms - 416 MATCH lines and 354 `nearer by` in
# bench/cue/m9_cue-20260820-142249.log alone.
# The margin clause has three forms, all of them from firmware/m9.c:2366-2373:
# nothing at all, `, nearer by N` under the two-stage rule, and `, by N` on the
# single-stage path. The third has never appeared in bench/cue/, which is
# exactly why it is here - a form that is unreachable today and reachable in the
# source is a parser bug waiting for a firmware flag to change.
MATCHED = re.compile(r"\sMATCH (.+?) \(cos [-+]?[\d.]+"
                     r"(?:,(?: nearer)? by ([-+]?[\d.]+))?\)")

# THE TWO WAYS THE BOARD DECLINES ARE NOT THE SAME VERDICT, and reading them as
# one was this file's worst bug. firmware/m9.c:2373-2377:
#
#     } else if (!open_gate) { printf("   - (nothing there)\n"); }
#     else                   { printf("   -\n"); }
#
# The gate is the presence stage. Closed, the board is saying the scene is
# empty. Open with no winner, it is saying something IS in front of the camera
# and nothing it was given fits - which for a monitor is a different event, and
# usually the more interesting one.
ABSENT = re.compile(r"\s-\s\(nothing there\)\s*$")
UNNAMED = re.compile(r"\slvl[-+][\d.]+(?: d[\d.]+)?(?: !led)?\s+-\s*$")

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

# The board's two declines, in its own words rather than in a word of ours.
NOTHING = "nothing there"     # the presence gate is closed: the scene is empty
UNNAMED_STATE = "unrecognised"   # the gate is open and no query crossed


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_frame(line: str) -> tuple[int, str, float | None] | None:
    """(board frame, state name, margin) off one demo.py frame line, or None.

    ONE SOURCE: the verdict the board printed. It has exactly three values -
    `MATCH <name>`, `- (nothing there)`, and a bare `-` - and every frame line
    carries one of them, in both modes. This function chooses between them and
    invents nothing.

    It used to have a second source. When neither MATCH nor `(nothing there)`
    matched, it read the score leaderboard and returned the top phrase, which it
    called "text mode". That branch was wrong in a way worth spelling out,
    because it looked like the sensible fallback: the leaderboard is printed on
    every frame, so the branch appeared to be handling the no-enrolment case.
    It was not. A query that clears its threshold produces a MATCH line
    (m9.c:2332), so the *only* frames that ever reached the leaderboard branch
    were the ones ending in a bare `-` - frames on which the board had already
    looked at the same numbers and declined to name a state. This side then
    overrode it with the argmax. 3,722 of the 14,874 recorded frame lines in
    bench/cue/ take that path, and on a single-query text run it is every frame:
    a smoke run on 2026-08-22 pointed at a desk with `a desk` scoring -0.10
    against a 1.23 threshold, and this file announced `a desk`.

    The margin is None when the board named no gap, and on both declines. Not
    0.0: zero is a real margin and means the opposite thing, and a caller
    comparing it against --floor would drop exactly the frames the board was
    most sure about. The old code returned 0.0 for a single query anyway,
    contradicting this paragraph two lines below where it was written.
    """
    m = FRAME.match(line)
    if not m:
        return None
    frame = int(m.group(1))

    hit = MATCHED.search(line)
    if hit:
        gap = float(hit.group(2)) if hit.group(2) else None
        return frame, hit.group(1).strip().rstrip("~"), gap
    if ABSENT.search(line):
        return frame, NOTHING, None
    if UNNAMED.search(line):
        return frame, UNNAMED_STATE, None
    return None


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
                and name not in (NOTHING, UNNAMED_STATE)):
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
    # Always, not only under --restart. demo.py's default is to drop the board
    # into BOOTSEL as it leaves, which is right for a bench script - m9 never
    # stops by itself, so "finished" and "still looping" look the same and the
    # next thing anybody does is flash it. A monitor is the other case. The
    # board it leaves behind is the board somebody walks back to, or the board
    # the restart loop below is counting on, and BOOTSEL is neither.
    cmd += ["--leave-running"]
    for spec in sched:
        cmd += [f"--enrol={spec}"]
    return cmd + queries


def bootsel_note(port: str | None) -> str | None:
    """Why relaunching is pointless, or None if it is worth a try.

    There are two ways demo.py can fail to find a board and only one of them is
    worth waiting out. An outage - issue #9 - clears on its own or on a power
    cycle, and riding through it is what --restart is for. BOOTSEL does not
    clear: a board with no firmware running will not grow a CDC node however
    long anybody waits, so the loop turns into one full SigLIP load a minute
    against a port that is never coming back. demo.py asks for the teacher
    before it asks for the port, so each turn costs the whole minute.

    Shelled out rather than imported because this file has no dependencies and
    host/board.py needs pyserial. --state answers in one token so this is not
    matching on prose. If it cannot run at all, say nothing and let the restart
    happen: a broken diagnostic must not be what stops a monitor.
    """
    if port:            # the caller pinned one; believe them and let it try
        return None
    try:
        out = subprocess.run(["uv", "run", "--script", str(ROOT / "host/board.py"),
                              "--state"], capture_output=True, text=True,
                             timeout=120, cwd=ROOT, check=False).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if out and out[0] == "bootsel":
        where = " ".join(out[1:])
        return (f"the board is on the bus at {where} with no serial port, so it "
                f"is in BOOTSEL and running no firmware. Waiting will not fix "
                f"that.\n Flash it and start again:\n"
                f"   uv run host/bootsel.py --flash firmware/build/forgix_m9.uf2")
    return None


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
        # SIGINT first, because demo.py closes the port and prints its tallies
        # on it. Then escalate, and the middle rung is not decoration: demo.py
        # inherits this process's signal dispositions, so when watch.py itself
        # was started with SIGINT ignored - `&` from a script, nohup, launchd -
        # the polite signal lands on a child that cannot act on it either.
        for sig in (signal.SIGINT, signal.SIGTERM):
            if proc.poll() is not None:
                break
            proc.send_signal(sig)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    return proc.returncode or 0


def install_stop_signals() -> None:
    """Make SIGINT and SIGTERM both stop this process, from any parent.

    THE DEFAULT IS NOT ENOUGH FOR THE JOB THIS FILE IS FOR. A shell without job
    control - `watch.py ... &` in a script, a nohup, a launchd plist, anything
    that is not a person at a terminal - sets SIGINT to SIG_IGN in the child,
    and CPython leaves an inherited SIG_IGN alone rather than installing its
    own handler. The result is a monitor that ignores Ctrl-C, ignores `kill
    -INT`, and can only be stopped with a signal that skips every `finally` in
    the file: the demo.py subprocess is orphaned and keeps the serial port.

    That was reproduced on 2026-08-22 and it is exactly the "leaving it running"
    case docs/monitor.md is about, so it is fixed here rather than documented as
    a quirk. SIGTERM is handled for the same reason - a supervisor stopping the
    service should get the same clean shutdown a person does.
    """
    def stop(signum, _frame):
        raise KeyboardInterrupt(signum)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)


def main() -> int:
    install_stop_signals()
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
                         "camera, which is what every accuracy number in this "
                         "repository is measured on. Both modes can decline")
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
    mode = ("enrolled - MATCH is against references you hold up" if sched else
            "text - MATCH is against a calibrated threshold, see docs/fit.md")
    print(f"mode    : {mode}", file=sys.stderr)
    print(f"states  : your queries, plus '{NOTHING}' and '{UNNAMED_STATE}'",
          file=sys.stderr)
    print(f"confirm : {args.confirm} frames"
          f"{f', margin floor {args.floor}' if args.floor else ''}",
          file=sys.stderr)

    # Asked before the first run and not only before a relaunch. demo.py loads
    # the teacher before it asks for a port, so a board in BOOTSEL costs a full
    # minute to be told about; this costs two seconds and says the same thing.
    stuck = bootsel_note(args.port)
    if stuck:
        print(f"\ncannot start: {stuck}", file=sys.stderr)
        return 1

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
            stuck = bootsel_note(args.port)
            if stuck:
                print(f"\n(demo.py exited [{code}] after run {runs}, and "
                      f"relaunching cannot work: {stuck})", file=sys.stderr)
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
