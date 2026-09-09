# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Does each class's reference point at its own phrase? Asked of old logs.

    uv run --script tools/probe_straddle.py --validate bench/probe/20260909-enroldrift/*.log
    uv run --script tools/probe_straddle.py --score bench/cue/*.log

Issue #35 found that on `an opened book` / `a closed book` the two enrolled
references land on the *same* side of the text axis, while on `a red cube` /
`a green cube` they straddle it - and that the first pair is the one that
classifies badly. That suggests a test an operator could run at enrolment time:
**does the reference for class k score highest on phrase k?**

If it does what it looks like it does, this would be the first enrolment-time
predictor in this project to survive contact with a bench. Eight have not, and
four of those pointed the wrong way, so the bar is a set of runs whose accuracy
is already known - not another pair of demonstrations.

**No bench time is needed to ask.** The frame lines already carry every query's
`z`; the enrolment window is delimited by `the next N frames are 'X'` and its
receipt; and the reference is the mean of `c[] = z[] - mean(z)` over that
window. `tools/score_cue.py` scores the same file. So straddle and accuracy come
out of one log under one set of conditions.

`--validate` checks the reconstruction rather than trusting it: the two
2026-09-09 probe logs carry both the frame lines and `'T'` dumps of the
references the board actually held, and the numbers computed here have to
reproduce them.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `frame  1114 :  a closed book -0.97  an opened book -2.48   led   0/255 ...`
# Names are matched by what follows them rather than by a character class: a
# query is an arbitrary English phrase and any class this parser guessed at
# would be a thing to get wrong later.
FRAME = re.compile(r"^frame +(\d+) :(.*?)   led ")
# The trailing `*` marks a score over its threshold and has to be consumed
# here, not left in the stream: without it the asterisk becomes the first
# character of the NEXT query's name, that frame fails the "every query
# present" check, and the frames silently dropped are exactly the ones where
# something matched. It cost a wrong reconstruction before it was caught.
SCORE = re.compile(r"([+-]\d+\.\d+)\*?")

CUE = re.compile(r"^enrol +: the next (\d+) frames are '(.+?)'")
# Two firmware generations, and the older one is 24 of the 54 cue logs - the
# whole of 2026-08-11 through 08-20 06:37, which is most of the accuracy the
# repo has. It prints `name, level L (N frames)` with no scatter and no visit
# number, and its N is per-visit where the newer one's is cumulative. Both are
# read here; the difference is one branch and it doubles the sample.
RECEIPT = re.compile(r"^enrol +: (.+?), level ([+-]?\d+\.\d+)"
                     r"(?:, scatter (\d+\.\d+))? \((\d+) frames"
                     r"(?:, visit (\d+))?")
DUMP_REF = re.compile(r"^enroldump : ref (\S+) vis (\d+) scat (\S+) (.*)")
DUMP_NQ = re.compile(r"^enroldump : nq (\d+),")
ACC = re.compile(r"^enrolled +: MATCH correct on (\d+)/(\d+)")


def scores(body: str) -> dict[str, float]:
    """Split a frame line's score list into {phrase: z}.

    The list is ordered by score, not by query index, so the phrase is the only
    key there is. Cutting on the numbers and taking whatever lies between them
    as the name means a phrase with a digit or a dash in it still parses.
    """
    out: dict[str, float] = {}
    pos = 0
    for m in SCORE.finditer(body):
        name = body[pos:m.start()].strip()
        if name:
            out[name] = float(m.group(1))
        pos = m.end()
    return out


class Run:
    def __init__(self, path: Path):
        self.path = path
        self.order: list[str] = []          # query index order, once known
        self.refs: dict[str, list[float]] = {}
        self.visits: dict[str, int] = {}
        self.level_err = 0.0
        self.note = ""

    def straddle(self) -> tuple[int, int]:
        """How many classes score highest on their own phrase.

        The general form of "the two references straddle zero". With two
        queries the class space is a line and `c[k] > 0` says the same thing;
        with more, the question is whether the reference's largest component is
        the phrase it was enrolled under, and that needs no constant either.
        """
        ok = 0
        for name, ref in self.refs.items():
            k = self.order.index(name)
            if ref and max(range(len(ref)), key=lambda i: ref[i]) == k:
                ok += 1
        return ok, len(self.refs)

    def sep(self) -> float:
        """The smallest distance between two class references.

        The run's own unit. `eref` is left out for the same reason it is left
        out of straddle: #18 keeps it out of the classifier, so a run is not
        made better or worse by where the empty scene landed.
        """
        refs = list(self.refs.values())
        best = float("inf")
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                d = sum((x - y) ** 2 for x, y in
                        zip(refs[i], refs[j], strict=True)) ** 0.5
                best = min(best, d)
        return best

    def own_over_sep(self) -> float:
        """The weakest reference's own component, in units of `sep`.

        The weakest and not the mean, because one reference pointing at the
        wrong phrase is enough to lose the run, and averaging would let a
        confident class cover for it.
        """
        s = self.sep()
        if not s or s == float("inf"):
            return 0.0
        return min(self.refs[n][self.order.index(n)] for n in self.refs) / s


def parse(path: Path, upto: str | None = None) -> Run:
    """Rebuild the references. `upto` stops at the first line containing it.

    A cue log holds one enrolment sequence and wants the whole file. The
    2026-09-09 probe logs hold six, each followed by a dump and a `'Y'`, so
    checking the rebuild against the first dump means reading only as far as
    that dump - otherwise the comparison is six rounds of mean against one
    round's reference, which is not a reconstruction error but it looks like
    a large one.
    """
    run = Run(path)
    text = path.read_text(errors="replace").splitlines()
    if upto is not None:
        for i, ln in enumerate(text):
            if upto in ln:
                text = text[:i + 1]
                break

    # Windows are anchored on the RECEIPT and straddle it: the last N-1 frame
    # lines ABOVE it, plus the one below. The board folds the frame in, prints
    # the receipt, and prints that frame's own score line afterwards, so the
    # obvious reading - the N lines above the receipt - is off by one and
    # silently drops the most informative frame of the window.
    #
    # This is not a guess. Both 2026-09-09 probe logs carry `'T'` dumps of the
    # references the board actually held, and `--validate` reproduces them to
    # 0.0007 with this window against 0.09 with the N-above reading. 0.0007 is
    # the rounding floor of a two-decimal score averaged over 20 frames, so
    # there is nothing left to explain.
    frames: list[dict[str, float]] = []
    receipts: list[tuple[int, str, int, float]] = []
    for ln in text:
        m = FRAME.match(ln)
        if m:
            frames.append(scores(m.group(2)))
            continue
        m = CUE.match(ln)
        if m:
            # The cue names the class before its window; nothing else does, in
            # the logs whose firmware predates the receipt line.
            if m.group(2) not in run.order:
                run.order.append(m.group(2))
            continue
        m = RECEIPT.match(ln)
        if m:
            name, level, n = m.group(1), float(m.group(2)), int(m.group(4))
            if name == "the empty scene":
                # eref is a reference but not a class, and #18 keeps it out of
                # the classifier. It has no phrase to score highest on.
                continue
            visit = int(m.group(5)) if m.group(5) else \
                sum(1 for r in receipts if r[1] == name) + 1
            # The newer firmware's count is cumulative across visits and the
            # older one's is per-visit. Either way what is wanted is the length
            # of the window that just closed.
            per = n // max(visit, 1) if m.group(5) else n
            receipts.append((len(frames), name, per, level))
            run.visits[name] = visit
            if name not in run.order:
                run.order.append(name)

    windows: dict[str, list[dict[str, float]]] = {}
    for at, name, per, level in receipts:
        rows = [d for d in frames[max(at - per + 1, 0):at + 1] if d]
        windows.setdefault(name, []).extend(rows)
        # The receipt's own `level` is mean(z) over the window, so the board
        # states the answer to half of this reconstruction on every line. It
        # costs nothing to check, it needs no `'T'` dump, and it is the only
        # thing standing behind the older logs - whose firmware predates the
        # dump key, so --validate can say nothing about them at all. A window
        # off by one frame shows up here as a residual well above 0.005.
        if rows:
            got = sum(sum(d.values()) / len(d) for d in rows) / len(rows)
            run.level_err = max(run.level_err, abs(got - level))

    if not windows:
        run.note = "no enrolment receipts"
        return run

    # The query index order is the order the phrases were enrolled in, which is
    # the order cue.py presses the digits in and therefore the order they were
    # given on the command line. Anything that disagrees is a log this cannot
    # read, and it says so rather than lining up the columns anyway.
    names = [n for n in run.order if n in windows]
    for name in names:
        rows = windows[name]
        acc = [0.0] * len(names)
        n = 0
        for d in rows:
            if not all(q in d for q in names):
                continue
            lvl = sum(d[q] for q in names) / len(names)
            for i, q in enumerate(names):
                acc[i] += d[q] - lvl
            n += 1
        if n:
            run.refs[name] = [a / n for a in acc]
    run.order = names
    return run


def dumped(path: Path) -> list[dict[str, list[float]]]:
    """The `'T'` dumps in a log, as {phrase: c[]}, for --validate."""
    out: list[dict[str, list[float]]] = []
    cur: dict[str, list[float]] = {}
    nq = 0
    for ln in path.read_text(errors="replace").splitlines():
        m = DUMP_NQ.match(ln)
        if m:
            nq, cur = int(m.group(1)), {}
            continue
        if ln.startswith("enroldump : end"):
            if cur:
                out.append(cur)
            cur = {}
            continue
        m = DUMP_REF.match(ln)
        if m and m.group(1) != "e":
            tok = m.group(4).split()
            cur[" ".join(tok[nq:])] = [float(t) for t in tok[:nq]]
    return out


def accuracy(path: Path) -> tuple[int, int] | None:
    if not path.with_suffix(path.suffix + ".cues").exists():
        return None
    r = subprocess.run(["uv", "run", "--script", "tools/score_cue.py", str(path)],
                       cwd=ROOT, capture_output=True, text=True, check=False)
    for ln in r.stdout.splitlines():
        m = ACC.match(ln)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="does each enrolled reference score highest on its own phrase")
    ap.add_argument("log", type=Path, nargs="+")
    ap.add_argument("--validate", action="store_true",
                    help="check the reconstruction against the log's own 'T' "
                         "dumps instead of reporting straddle. Only the "
                         "2026-09-09 probe logs have both")
    ap.add_argument("--score", action="store_true",
                    help="also run tools/score_cue.py for the accuracy column. "
                         "Slow - one subprocess per log - and needs a sidecar")
    ap.add_argument("--min-held", type=int, default=20, metavar="N",
                    help="with --score, skip runs with fewer than N held-out "
                         "frames; default 20. A 5-frame run is a 20%% "
                         "quantisation step and would be noise in the table")
    args = ap.parse_args()

    if args.validate:
        worst = 0.0
        for p in args.log:
            run, dumps = parse(p, upto="enroldump : end"), dumped(p)
            if not dumps:
                print(f"{p.name}: no 'T' dumps, nothing to check against")
                continue
            # Each round's dump follows that round's enrolment, and this parser
            # accumulates across the whole file, so only the FIRST dump can be
            # compared - after that the log has forgotten and re-enrolled.
            d = dumps[0]
            for name, ref in run.refs.items():
                if name not in d:
                    continue
                err = max(abs(x - y) for x, y in zip(ref, d[name], strict=True))
                worst = max(worst, err)
                print(f"{p.name}: {name:<20} rebuilt {ref[0]:+9.4f}  "
                      f"dumped {d[name][0]:+9.4f}  err {err:.2e}")
        print(f"\nworst component error {worst:.3e}")
        return 0

    print(f"  {'log':<34} {'nq':>3} {'straddle':>9} {'accuracy':>11} "
          f"{'lvlerr':>7}  own component")
    rows: list[tuple[str, int, int, float, Run]] = []
    for p in sorted(args.log):
        run = parse(p)
        if not run.refs:
            continue
        ok, n = run.straddle()
        # A residual above the 0.005 print quantum means the window is not the
        # one the board averaged, and the row below is then a reconstruction of
        # something else. Shown rather than filtered: which logs fail, and how,
        # is more useful than a shorter table.
        flag = "" if run.level_err < 0.01 else " !"
        acc = ""
        if args.score:
            a = accuracy(p)
            if a is None:
                acc = "no sidecar"
            elif a[1] < args.min_held:
                acc = f"{a[1]} held"
            else:
                acc = f"{100.0 * a[0] / a[1]:.1f}% ({a[1]})"
                rows.append((p.name, ok, n, 100.0 * a[0] / a[1], run))
        own = " ".join(f"{run.refs[q][i]:+.2f}" for i, q in enumerate(run.order))
        print(f"  {p.name:<34} {len(run.order):>3} {ok:>4}/{n:<4} {acc:>11} "
              f"{run.level_err:>7.4f}{flag}  {own}")

    if rows:
        summarise(rows)
    return 0


def summarise(rows: list[tuple[str, int, int, float, Run]]) -> None:
    """Group the accuracy column by straddle, and then by pair.

    The by-pair split is the part that decides anything. This repo already
    knows accuracy depends on which two phrases were chosen - 95.8 / 90.8 /
    50.0 / 34.2% across four pairs benched the same afternoon - so a predictor
    that only sorts good pairs from bad ones has told nobody anything they
    could not get by reading the phrase list. The question is whether straddle
    still separates runs *inside* one pair, where the phrases are held fixed
    and only the enrolment differs.
    """
    def stat(sel: list[float]) -> str:
        if not sel:
            return "     -"
        return (f"n={len(sel):<3} mean {sum(sel) / len(sel):5.1f}%  "
                f"min {min(sel):5.1f}%  max {max(sel):5.1f}%")

    print("\n  by straddle")
    for k in sorted({r[1] for r in rows}, reverse=True):
        sel = [r[3] for r in rows if r[1] == k]
        print(f"    {k}/2  {stat(sel)}")

    print("\n  by pair, then straddle")
    pairs = sorted({" / ".join(r[4].order) for r in rows})
    for pair in pairs:
        sel = [r for r in rows if " / ".join(r[4].order) == pair]
        print(f"    {pair}")
        for k in sorted({r[1] for r in sel}, reverse=True):
            print(f"      {k}/2  {stat([r[3] for r in sel if r[1] == k])}")

    # The book pair under both query orderings. Straddle asks whether each
    # reference's largest component is its own phrase, which does not depend on
    # which phrase was typed first, so the two orderings are one pair and 31 of
    # the 40 scored runs. It is the only group large enough to ask the question
    # with the phrases held fixed.
    book = [r for r in rows if set(r[4].order) ==
            {"an opened book", "a closed book"}]
    if len(book) >= 8:
        hi = sorted(r[3] for r in book if r[1] == 2)
        lo = sorted(r[3] for r in book if r[1] == 1)
        print(f"\n  book pair, both orderings, n={len(book)}")
        print(f"    2/2  {stat(hi)}")
        print(f"    1/2  {stat(lo)}")
        print(f"    ranksum p={ranksum(hi, lo):.3f} (two-sided)")

        # Straddle is a sign test, and a sign test throws away how far from
        # zero the thing was. The continuous form of the same question, and
        # scale-free without a constant: how much of its own phrase does the
        # weaker reference own, measured against the gap between the two
        # references - which is the run's own unit and the one the board
        # already prints as `nearest pair N apart`.
        # Straddle is a sign test, and a sign test throws away how far from
        # zero the thing was. The continuous form of the same question, and
        # scale-free without a constant: how much of its own phrase does the
        # weaker reference own, in units of the gap between the two references
        # - the run's own unit, the one the board prints as `nearest pair N
        # apart`.
        #
        # Everything below the first line is a control, and the controls are
        # the point. The headline rho looks like a result and is not one. It is
        # carried by a handful of runs whose two references almost coincide,
        # where `sep` in the denominator is doing all the work and the ratio is
        # measuring 1/sep rather than anything about the text axis. Three
        # checks catch that: the ratio against each of its own halves, the
        # ratio with the near-degenerate runs dropped, and the ratio computed
        # separately on each side of its own sign boundary. A predictor whose
        # correlation reverses sign across its own boundary is not one.
        def own(r: Run) -> float:
            return min(r.refs[n][r.order.index(n)] for n in r.refs)

        def rho(sel: list, f) -> str:
            if len(sel) < 6:
                return f"n={len(sel):<3} too few"
            return (f"rho={spearman([f(r[4]) for r in sel], [r[3] for r in sel]):+.3f}"
                    f"  n={len(sel)}")

        big = [r for r in book if r[4].sep() > 1.0]
        print(f"    min(own)/sep                {rho(book, Run.own_over_sep)}")
        print(f"      control, sep alone        {rho(book, Run.sep)}")
        print(f"      control, min(own) alone   {rho(book, own)}")
        print(f"      control, sep>1 only       {rho(big, Run.own_over_sep)}")
        print(f"      control, sep>1, sep alone {rho(big, Run.sep)}")
        for k in (1, 2):
            sel = [r for r in book if r[1] == k]
            print(f"      control, within {k}/2 only  "
                  f"{rho(sel, Run.own_over_sep)}")


def spearman(a: list[float], b: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return out

    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


def ranksum(a: list[float], b: list[float]) -> float:
    """Mann-Whitney U, normal approximation with a tie correction.

    Written out rather than imported because this file has no dependencies and
    a rank test is twenty lines. The normal approximation is the right one to
    doubt at these sizes; it is quoted to say whether the separation is worth a
    bench, not to certify anything.
    """
    import math
    pool = sorted(a + b)
    ranks: dict[float, float] = {}
    i = 0
    while i < len(pool):
        j = i
        while j + 1 < len(pool) and pool[j + 1] == pool[i]:
            j += 1
        ranks[pool[i]] = (i + j) / 2.0 + 1.0
        i = j + 1
    na, nb = len(a), len(b)
    u = sum(ranks[x] for x in a) - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    ties = sum(t ** 3 - t for t in
               (sum(1 for x in pool if x == v) for v in ranks))
    n = na + nb
    var = na * nb / 12.0 * (n + 1 - ties / (n * (n - 1.0)))
    if var <= 0:
        return 1.0
    z = (abs(u - mu) - 0.5) / math.sqrt(var)
    return math.erfc(z / math.sqrt(2.0))


if __name__ == "__main__":
    sys.exit(main())
