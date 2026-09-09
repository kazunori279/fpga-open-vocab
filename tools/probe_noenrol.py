# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""What does the enrolment buy, scored against no enrolment on the same frames?

    uv run --script tools/probe_noenrol.py bench/cue/m9_cue-2026*.log

The board can classify without being shown anything: pick the query with the
highest `z` and call the frame that. It needs no operator, no reference, no
threshold and no constant, and it is what the `*` on every frame line already
marks. With two queries it is exactly the sign of the margin - `c[] = z[] -
mean(z)` subtracts the same number from every query, so the argmax over `c[]`
and the argmax over `z[]` pick the same phrase.

So "why is there a registration stage" has a number, and it is a subtraction:
run both rules over the same frames and take the difference.

  enrolled   the board's own verdict, read off the frame line's `MATCH` field.
             Not recomputed and not re-derived - this is what the LED did.
  argmax     the same frames, decided by which query scored highest. No
             enrolment, no constant, no operator.
  oracle     the best fixed cut on the margin, chosen per run *by looking at
             the answers*. Not shippable and not meant to be. It is the ceiling
             argmax would reach if someone could hand it the right threshold
             for this run, so oracle - argmax is the size of the prize and
             enrolled - argmax is how much of it the registration collects.

WHY BOTH RULES MUST SEE THE SAME FRAMES
---------------------------------------
`tools/score_cue.py` drops every frame before the last reference lands, because
until then the board was deciding by a rule that no longer exists. A rule that
never enrolled has no such moment and could be scored over the whole run -
which would be an easier set, and the comparison would be worth nothing. So the
enrolment windows and everything before them are dropped here too, and the two
rules are scored row for row on what is left.

The counts will not match score_cue.py's exactly: it scores each cue segment
against that segment's own role, and it has a longer list of frames it declines
to count. Both rules here see whatever this file keeps, which is what the
comparison needs; `--verbose` prints the per-run frame count so the two can be
put side by side.

An enrolled run can also answer "absent" on a frame where the object is
present, and that is counted as a miss - declining is a decision the argmax
rule is not allowed to make, and pretending otherwise would score the two rules
under different rules. The count is printed.
"""

import argparse
import re
import sys
from pathlib import Path

FRAME = re.compile(r"^frame +(\d+) :(.*?)   led .*?   (-|MATCH (.+?) \(cos)")
SCORE = re.compile(r"([+-]\d+\.\d+)\*?")
EMPTY = "empty"


def scores(body: str) -> dict[str, float]:
    out: dict[str, float] = {}
    pos = 0
    for m in SCORE.finditer(body):
        name = body[pos:m.start()].strip()
        if name:
            out[name] = float(m.group(1))
        pos = m.end()
    return out


class Row:
    __slots__ = ("match", "truth", "z")

    def __init__(self, truth: str, z: dict[str, float], match: str | None):
        self.truth = truth
        self.z = z
        self.match = match


def load(log: Path, settle: int) -> list[Row]:
    """Class frames after the last reference lands, with both rules' inputs."""
    side = Path(str(log) + ".cues")
    if not side.exists():
        return []
    segs: list[tuple[int, int, str]] = []
    window, enrols = 20, []
    for ln in side.read_text().splitlines():
        if ln.startswith("# enrol-window"):
            window = int(ln.split()[-1])
        elif ln.startswith("# enrol "):
            enrols.append(int(ln.split()[2]))
        elif ln.strip() and not ln.startswith("#"):
            a, b, name = ln.split("\t")
            segs.append((int(a), int(b), name.strip()))
    if not enrols:
        return []
    engage = max(enrols) + window
    taught = {f for e in enrols for f in range(e, e + window)}

    parsed: dict[int, tuple[dict[str, float], str | None]] = {}
    for ln in log.read_text(errors="replace").splitlines():
        m = FRAME.match(ln)
        if m:
            parsed[int(m.group(1))] = (scores(m.group(2)), m.group(4))

    rows: list[Row] = []
    for a, b, name in segs:
        if name in ("baseline", EMPTY):
            continue
        for f in range(a + settle, b + 1):
            if f < engage or f in taught or f not in parsed:
                continue
            z, match = parsed[f]
            if name in z and len(z) >= 2:
                rows.append(Row(name, z, match))
    return rows


def oracle_acc(rows: list[Row], qs: list[str]) -> float:
    """Best accuracy over every cut on the margin, chosen with the answers.

    Only defined for two queries: with more, "the margin" is not one number and
    the honest ceiling is a different calculation. Returns 0 for those and the
    caller prints a dash.
    """
    if len(qs) != 2 or not rows:
        return 0.0
    a, b = qs
    best = 0.0
    for k in sorted({r.z[a] - r.z[b] for r in rows} | {float("-inf")}):
        ok = sum(1 for r in rows if (r.truth == a) == (r.z[a] - r.z[b] > k))
        best = max(best, 100.0 * ok / len(rows))
    return best


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial on wins vs losses, ties discarded.

    Exact rather than normal-approximated because the interesting comparisons
    here run to a couple of dozen runs, where the approximation is loose in
    exactly the range that decides whether to believe the mean.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    c, tot, tail = 1, 0, 0
    obs = min(wins, losses)
    for k in range(n + 1):
        tot += c
        if min(k, n - k) <= obs:
            tail += c
        c = c * (n - k) // (k + 1)
    return min(1.0, tail / tot)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="the enrolled rule against no enrolment, same frames")
    ap.add_argument("log", type=Path, nargs="+")
    ap.add_argument("--settle", type=int, default=10,
                    help="frames dropped after each cue, score_cue.py's default")
    ap.add_argument("--min-frames", type=int, default=20, metavar="N",
                    help="skip runs with fewer scored frames than this")
    ap.add_argument("--queries", metavar="A,B",
                    help="only runs whose query set is exactly these, so one "
                         "pair can be read without the others confounding it")
    ap.add_argument("--verbose", action="store_true",
                    help="per-run rows, not just the summary")
    args = ap.parse_args()

    want = set(args.queries.split(",")) if args.queries else None

    print(f"  {'log':<34} {'n':>4} {'enrolled':>9} {'argmax':>8} {'oracle':>8}"
          f" {'absent':>7}   enrolled - argmax")
    keep: list[tuple[float, float, float]] = []
    absent_tot = frames_tot = 0
    for p in sorted(args.log):
        rows = load(p, args.settle)
        if len(rows) < args.min_frames:
            continue
        qs = sorted({q for r in rows for q in r.z})
        if want and set(qs) != want:
            continue
        e = 100.0 * sum(1 for r in rows if r.match == r.truth) / len(rows)
        a = 100.0 * sum(1 for r in rows
                        if max(r.z, key=lambda q: r.z[q]) == r.truth) / len(rows)
        o = oracle_acc(rows, qs)
        absent = sum(1 for r in rows if r.match is None)
        keep.append((e, a, o))
        absent_tot += absent
        frames_tot += len(rows)
        if args.verbose:
            print(f"  {p.name:<34} {len(rows):>4} {e:8.1f}% {a:7.1f}% "
                  f"{f'{o:7.1f}%' if o else '      -'} {absent:>7}"
                  f"   {e - a:+8.1f}")

    if not keep:
        print("\n  nothing to score")
        return 1
    n = len(keep)
    print(f"\n  n={n} runs, {frames_tot} frames\n")
    print(f"  {'rule':<10} {'mean':>7} {'min':>7} {'median':>7} {'max':>7}")
    for i, label in enumerate(("enrolled", "argmax", "oracle")):
        col = sorted(x[i] for x in keep)
        med = col[n // 2] if n % 2 else (col[n // 2 - 1] + col[n // 2]) / 2
        print(f"  {label:<10} {sum(col) / n:6.1f}% {col[0]:6.1f}% "
              f"{med:6.1f}% {col[-1]:6.1f}%")

    me, ma, mo = (sum(x[i] for x in keep) / n for i in range(3))
    wins = sum(1 for x in keep if x[0] > x[1])
    ties = sum(1 for x in keep if x[0] == x[1])
    print(f"\n  enrolment beats argmax on {wins}/{n} runs, loses on "
          f"{n - wins - ties}, ties on {ties} (sign test "
          f"p = {sign_test(wins, n - wins - ties):.3f})")
    print(f"  a per-run threshold would be worth {mo - ma:+.1f} points over "
          f"argmax; the enrolment collects {me - ma:+.1f} of that")
    print(f"  the enrolled rule answers ABSENT on "
          f"{100.0 * absent_tot / frames_tot:.1f}% of frames where the object "
          f"was in shot; argmax cannot decline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
