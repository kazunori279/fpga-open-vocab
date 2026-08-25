# /// script
# requires-python = ">=3.11"
# ///
"""Issue #30. How far the scores of a MOTIONLESS scene walk during one run.

The bench asks what the appliance scores; this asks the question underneath it -
whether a camera left to re-decide its own exposure and gain moves the numbers
when nothing in front of it moves at all. That needs no staging, no enrolment
and no cue schedule, which is the whole point: an operator who has an empty desk
and four minutes can run it, and the arms interleave in one sitting.

WHAT IT READS. `host/demo.py --no-smooth` frame lines, nothing else. No `.cues`
sidecar, no held-out set, no accuracy. These are soaks and they live in
bench/soak/, not bench/cue/ - a run in here has never been shown an object and
cannot be quoted as a recognition figure.

THE TWO COLUMNS, AND THEY ARE NOT THE SAME QUESTION.

  `common` is the walk of (z[A] + z[B]) / 2, which is every query moving
  together - the room getting brighter, the sensor re-deciding, the board
  warming up. With two queries this is exactly the direction the presence stage
  reads, because the empty reference is a point on the margin axis and a
  common-mode shift slides the whole axis underneath it.

  `margin` is the walk of z[A] - z[B], which is the direction the STATE stage
  reads. A camera that moves this one is changing which class an unchanged scene
  looks like.

#30 predicts the intervention lands on `common` and not on `margin`. If locking
moves both, or moves `margin` more, the mechanism is not the one in the issue.

WALK IS A RANGE OF SMOOTHED MEANS, NOT AN SD. Per-frame scatter on this board is
large and is not what breaks a bench - a reference enrolled at frame 90 and a
frame scored at frame 400 care about where the level sat, not how noisy it was
getting there. So each series is smoothed with a centred WINDOW-frame mean and
the column is max minus min of that. `sd` is printed beside it because a walk
that is small against the frame-to-frame scatter is not a walk anybody can see.

WHY THE FIRST FRAMES ARE DROPPED. `ft_acquire()`'s exposure ramp is a transient
every run has and no bench scores, and the locked arm cannot press its key until
the ramp is done. Both arms drop the same SKIP frames so the two windows are the
same window.
"""
import argparse
import re
import statistics as st
import sys
from pathlib import Path

WINDOW = 31      # centred, odd, about 9 s at 290 ms/frame
SKIP = 60        # past the ramp and past every arm's lock press

# `frame  123 :  a closed book +0.58  an opened book +0.39   led  49/ 63 ...`
# The trailing `*` marks a query over its threshold and is not part of the number.
FRAME = re.compile(r"^frame\s+(\d+)\s*:\s*(.*?)\s+led\s", re.MULTILINE)
SCORE = re.compile(r"([a-z][a-z' ]*?)\s+([+-]\d+\.\d+)\*?(?=\s\s|$)")
LOCK = re.compile(r"^camera\s*:\s*frozen now (.*?) at frame (\d+)", re.MULTILINE)


def series(path):
    """(frames, {query: [z]}, lock description or None) from one demo.py log."""
    text = path.read_text(errors="replace")
    frames, cols = [], {}
    for m in FRAME.finditer(text):
        pairs = SCORE.findall(m.group(2))
        if len(pairs) < 2:
            continue
        frames.append(int(m.group(1)))
        for name, z in pairs:
            cols.setdefault(name.strip(), []).append(float(z))
    # A query that appears on some frames and not others would silently
    # misalign against the others, so refuse rather than truncate.
    n = len(frames)
    for name, zs in cols.items():
        if len(zs) != n:
            sys.exit(f"{path}: '{name}' has {len(zs)} scores against {n} frames")
    lock = LOCK.search(text)
    return frames, cols, (lock.group(1).strip(), int(lock.group(2))) if lock else None


def smooth(xs, w=WINDOW):
    """Centred running mean, shortened at the ends rather than padded."""
    half = w // 2
    return [st.fmean(xs[max(0, i - half):i + half + 1]) for i in range(len(xs))]


def walk(xs):
    s = smooth(xs)
    return max(s) - min(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", type=Path,
                    help="demo.py logs, an empty scene throughout. PASS THE "
                         "LOGS, NOT THE GLOB, for anything quotable")
    ap.add_argument("--skip", type=int, default=SKIP, metavar="N",
                    help=f"frames to drop off the front of every log, the same "
                         f"number for both arms (default {SKIP})")
    args = ap.parse_args()

    print(f"{'run':<22} {'arm':<14} {'n':>4} "
          f"{'common':>7} {'margin':>7} {'cm sd':>7} {'mg sd':>7}")
    rows = []
    for path in args.logs:
        frames, cols, lock = series(path)
        if len(frames) <= args.skip + WINDOW:
            print(f"{path.stem:<22} too short: {len(frames)} frames")
            continue
        names = sorted(cols)
        if len(names) != 2:
            sys.exit(f"{path}: {len(names)} queries, and the margin is only "
                     f"defined for two. This probe does not cover k>2 (#31)")
        a, b = (cols[n][args.skip:] for n in names)
        common = [(x + y) / 2 for x, y in zip(a, b, strict=True)]
        margin = [x - y for x, y in zip(a, b, strict=True)]
        arm = "free" if lock is None else lock[0]
        rows.append((arm, walk(common), walk(margin)))
        print(f"{path.stem:<22} {arm:<14} {len(a):>4} "
              f"{walk(common):>7.2f} {walk(margin):>7.2f} "
              f"{st.stdev(common):>7.2f} {st.stdev(margin):>7.2f}")

    arms = sorted({r[0] for r in rows})
    if len(arms) == 2 and len(rows) > 2:
        print()
        for i, col in enumerate(("common", "margin"), start=1):
            got = {arm: [r[i] for r in rows if r[0] == arm] for arm in arms}
            # Two or three runs an arm is a median and a range, not a t-test.
            # Anything that needs a p-value here needs more runs first.
            desc = "   ".join(f"{arm} {st.median(v):.2f} "
                              f"({min(v):.2f}-{max(v):.2f}, n={len(v)})"
                              for arm, v in got.items())
            print(f"{col:>6} walk:  {desc}")
        print("\nRead `common` first. It is the direction the presence stage "
              "reads and the\none #30 predicts the lock moves; `margin` is the "
              "state stage and should not move.")


if __name__ == "__main__":
    main()
