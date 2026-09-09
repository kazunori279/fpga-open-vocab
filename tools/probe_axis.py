# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Did the state axis ever point the other way, with no ground truth to ask?

    uv run --script tools/probe_axis.py bench/probe/20260909-box/*.log

A registration-free run has no cue schedule and no sidecar, so nothing in the
file says what was in front of the camera. That rules out accuracy - but it does
not rule out the question that actually decides whether a phrase pair is worth
shooting a bench for, which is whether the axis MOVES AT ALL.

`led_two()` maps the signed margin between the two state queries onto hue: 1.00
is saturated on the first phrase, 0.00 saturated on the second, 0.50 is the two
of them tied. A pair that discriminates spends time at both ends as the object
changes. A pair that does not is pinned, and being pinned is visible without
knowing which state was in shot when.

So this reports, over the frames where the presence gate was open:

  span      the contrast axis from its lowest to its highest. A pair that never
            crosses zero has one answer, not two.
  red/green what fraction of gate-open frames each end of the hue took. 100/0
            is a stuck LED however confident it looks.
  gate      how much room the presence query had. A gate hovering at its own
            threshold makes the LED flicker for reasons that have nothing to do
            with the state, which is what happened to `a cube` on 2026-09-09.

WHAT THIS CANNOT SAY. Nothing here is accuracy and nothing here is a bench. An
axis that swings both ways may still be swinging at the wrong times; that needs
`host/cue.py` and a sidecar. This is the cheap screen that says whether spending
that morning is worth it, and on 2026-09-09 it separated four pairs in an hour.
"""

import argparse
import re
import sys
from pathlib import Path

FRAME = re.compile(r"^frame +(\d+) :(.*?)   led +\d+/ *\d+ h(\d\.\d+)"
                   r"(?: b(\d\.\d+))?")
SCORE = re.compile(r"([+-]\d+\.\d+)\*?")


def scores(body: str) -> dict[str, float]:
    out: dict[str, float] = {}
    pos = 0
    for m in SCORE.finditer(body):
        name = body[pos:m.start()].strip()
        if name:
            out[name] = float(m.group(1))
        pos = m.end()
    return out


def banner(log: Path, role: str) -> list[str]:
    """The queries demo.py gave `role`, off the banner it prints to its console.

    Not in the --out log: the `query :` lines are start-up chatter and go to
    stdout. The sibling `.console` is where a run's own record of what it was
    asked lives, so it is read when it is there and the caller falls back when
    it is not.
    """
    side = log.with_suffix(".console")
    if not side.exists():
        return []
    return [ln.split(":", 1)[1].split("  ")[0].strip()
            for ln in side.read_text(errors="replace").splitlines()
            if ln.startswith("query     :") and f" {role} " in ln]


def gate_name(log: Path) -> str | None:
    hits = banner(log, "presence")
    return hits[0] if hits else None


def state_names(log: Path, row: dict[str, float]) -> list[str]:
    """The two state queries.

    A '~' on the frame line means the vector was built by contrast, which used
    to be the same thing as being a state query and is not since --state: a
    bare phrase can be ranked too. So the banner is the answer when there is
    one, and the suffix is the fallback for the logs shot before the flag
    existed.
    """
    named = [q for q in banner(log, "state") if q in row]
    return named if len(named) == 2 else [q for q in row if q.endswith("~")]


def hue_ref(rows: list[tuple[dict[str, float], float]],
            state: list[str]) -> str:
    """Which of the two state queries the hue is signed against.

    The frame line prints queries in score order, so their position says
    nothing about which one the host sent first - and `led_two()` signs the
    margin against the first, not against the winner, for the reason its own
    comment gives. Rather than parse the banner for an ordering that the log
    may not carry, recover it from the hue: `h > 0.5` exactly when the
    reference is ahead, so the reference is whichever query agrees with the
    hue on more frames. With a real axis this is unanimous.
    """
    a, b = state
    agree = sum(1 for z, h in rows
                if a in z and b in z and (z[a] > z[b]) == (h > 0.5))
    both = sum(1 for z, _ in rows if a in z and b in z)
    return a if agree * 2 >= both else b


def main() -> int:
    ap = argparse.ArgumentParser(
        description="does a state axis swing both ways, without ground truth")
    ap.add_argument("log", type=Path, nargs="+")
    ap.add_argument("--lit", type=float, default=0.5, metavar="B",
                    help="count a frame only when the gate had the LED at least "
                         "this lit, default 0.5. The gate axis is the board's "
                         "own presence call, so this is not a fitted cut")
    args = ap.parse_args()

    print(f"  {'log':<14} {'axis':<26} {'lit':>5} {'span':>16} "
          f"{'red/green':>10} {'gate z':>14}")
    for p in sorted(args.log):
        rows = [(scores(m.group(2)), float(m.group(3)),
                 float(m.group(4)) if m.group(4) else 1.0)
                for ln in p.read_text(errors="replace").splitlines()
                if (m := FRAME.match(ln))]
        if not rows:
            continue
        state = state_names(p, rows[0][0])
        gate = gate_name(p)
        lit = [(z, h) for z, h, b in rows if b >= args.lit]
        if len(state) != 2 or not lit:
            print(f"  {p.stem:<14} {'no state pair or never lit':<26}")
            continue
        a = hue_ref(lit, state)
        b = state[1] if a == state[0] else state[0]
        marg = [z[a] - z[b] for z, _ in lit if a in z and b in z]
        red = sum(1 for _, h in lit if h > 0.5)
        gz = [z[gate] for z, _ in lit if gate and gate in z]
        print(f"  {p.stem:<14} {a.removesuffix('~'):<26} {len(lit):>5} "
              f"{f'{min(marg):+.1f} .. {max(marg):+.1f}':>16} "
              f"{f'{100 * red / len(lit):.0f}/{100 - 100 * red / len(lit):.0f}':>10} "
              f"{f'{min(gz):+.1f} .. {max(gz):+.1f}' if gz else '-':>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
