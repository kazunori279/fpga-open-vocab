# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""How far a reference moves when nothing has changed.

    uv run --script tools/probe_enroldrift.py /tmp/enrol-repeat.log

Reads the `enroldump` blocks that `tools/enrol_repeat.py` collected and prints,
for every class, how far apart the same class's references landed across rounds.

**Everything is reported as a ratio of the run's own measurements.** The unit is
`sep`, the distance between the two nearest class references in that round -
the same quantity the board prints as `nearest pair N apart` and the same one
the enrolment guard already scales by. A displacement of 1.0 sep means the
reference moved by as much as the whole gap between the two classes it is meant
to separate, which is a failure whatever the absolute numbers are; 0.1 sep means
it moved by a tenth of it. No constant is fitted here and none is needed, which
is the point: the question "is this drift large" has an answer inside the run.

What it does NOT do is decide anything. It produces the within-boot floor. The
across-boot number from step 3 is what gets compared against it, and the
comparison is two distributions, not a number against a threshold.
"""

import argparse
import math
import re
import sys
from pathlib import Path


class Dump:
    """One `enroldump` block: the references and the space they live in."""

    def __init__(self, nq: int):
        self.nq = nq
        self.refs: dict[str, list[float]] = {}
        self.scat: dict[str, float] = {}
        self.vis: dict[str, int] = {}
        self.names: dict[str, str] = {}
        self.qbg: list[float] = []
        self.sd: list[float] = []

    def sep(self) -> float:
        """Nearest pair among the CLASS references, ignoring the empty one.

        eref is deliberately excluded. #18 keeps it out of the classifier, so a
        run where the empty reference happens to sit close to a class is not a
        run with a small class gap, and dividing by it would say otherwise.
        """
        keys = [k for k in self.refs if k != "e"]
        best = math.inf
        for i, a in enumerate(keys):
            for bkey in keys[i + 1:]:
                best = min(best, dist(self.refs[a], self.refs[bkey]))
        return best


def dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def parse(text: str) -> list[Dump]:
    dumps: list[Dump] = []
    cur: Dump | None = None
    for ln in text.splitlines():
        if not ln.startswith("enroldump : "):
            continue
        body = ln[len("enroldump : "):]
        m = re.match(r"nq (\d+), classes (\d+), empty (\d+)", body)
        if m:
            cur = Dump(int(m.group(1)))
            continue
        if cur is None:
            continue
        if body.startswith("end"):
            # A block with no class references is the un-enrolled dump that a
            # smoke test leaves behind. Keeping it would put a zero-length
            # vector in the middle of the displacement table.
            if len([k for k in cur.refs if k != "e"]) >= 2:
                dumps.append(cur)
            cur = None
            continue
        m = re.match(r"bg (\d+) qbg (\S+) sd (\S+) coco (\S+) cocosd (\S+) (.*)",
                     body)
        if m:
            cur.qbg.append(float(m.group(2)))
            cur.sd.append(float(m.group(3)))
            continue
        m = re.match(r"ref (\S+) vis (\d+) scat (\S+) (.*)", body)
        if m:
            key = m.group(1)
            tok = m.group(4).split()
            cur.refs[key] = [float(t) for t in tok[:cur.nq]]
            cur.names[key] = " ".join(tok[cur.nq:])
            cur.vis[key] = int(m.group(2))
            cur.scat[key] = float(m.group(3))
    return dumps


def main() -> int:
    ap = argparse.ArgumentParser(
        description="reference displacement across repeated enrolments, in sep")
    ap.add_argument("log", type=Path, nargs="+", help="logs with enroldump blocks")
    ap.add_argument("--drop", type=int, default=0, metavar="K",
                    help="ignore the first K rounds. For a run whose opening "
                         "round has a hand in shot - say so in the write-up "
                         "rather than dropping it quietly")
    args = ap.parse_args()

    dumps: list[Dump] = []
    for p in args.log:
        dumps.extend(parse(p.read_text()))
    dumps = dumps[args.drop:]
    if len(dumps) < 2:
        print("need at least two enrolled dumps", file=sys.stderr)
        return 1

    nq = dumps[0].nq
    if any(d.nq != nq for d in dumps):
        # Different nq is a different space, and the vectors are not comparable
        # component by component. This is the same failure recv_queries() warns
        # about when it throws the enrolment away on a new query set.
        print("dumps disagree on nq - not one space, refusing", file=sys.stderr)
        return 1

    # The coordinate system has to be the same or none of the rest means
    # anything. Within one boot this is exact; across boots it will not be, and
    # that is step 3's finding rather than an error here.
    bg0 = dumps[0].qbg
    bgmax = max(abs(x - y) for d in dumps for x, y in zip(d.qbg, bg0, strict=True))
    print(f"dumps     : {len(dumps)}, nq {nq}")
    print(f"background: largest qbg difference across dumps {bgmax:.3e}"
          f"  ({'same space' if bgmax == 0 else 'MOVED - see step 3'})")

    print("\nper round")
    print(f"  {'#':>3}  {'sep':>7}  " +
          "  ".join(f"{'scat ' + k:>10}" for k in sorted(dumps[0].refs)))
    for i, d in enumerate(dumps):
        row = "  ".join(f"{d.scat.get(k, float('nan')):>10.3f}"
                        for k in sorted(dumps[0].refs))
        print(f"  {i:>3}  {d.sep():>7.3f}  {row}")

    # The divisor is the mean sep and not each pair's own, so that one round
    # with an unusually tight pair cannot inflate every displacement it appears
    # in. Reported below so the reader can undo it.
    msep = sum(d.sep() for d in dumps) / len(dumps)
    print(f"\ndisplacement between rounds, in units of mean sep {msep:.3f}")
    print(f"  {'class':<20} {'n':>4} {'median':>8} {'max':>8} {'worst pair':>12}")
    for key in sorted(dumps[0].refs):
        vals: list[tuple[float, str]] = []
        for i in range(len(dumps)):
            for j in range(i + 1, len(dumps)):
                if key in dumps[i].refs and key in dumps[j].refs:
                    vals.append((dist(dumps[i].refs[key],
                                      dumps[j].refs[key]) / msep, f"{i}-{j}"))
        if not vals:
            continue
        vals.sort()
        med = vals[len(vals) // 2][0]
        print(f"  {dumps[0].names[key]:<20} {len(vals):>4} {med:>8.3f} "
              f"{vals[-1][0]:>8.3f} {vals[-1][1]:>12}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
