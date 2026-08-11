# /// script
# requires-python = ">=3.11"
# ///
"""Measure what moves when nothing moves.

Feed it a demo.py log of a scene that was held still for the whole run. The
background is frozen a few seconds in, so z == 0 by construction at that
moment; everything after it is drift. The question this answers is whether the
drift is common-mode across every query - which would make it the camera or the
room, not the scene - or confined to the queries that name the object.

    uv run --script tools/score_drift.py /tmp/m9_drift.log
"""
import re
import statistics as st
import sys
from pathlib import Path

FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led")
SCORE = re.compile(r"(\S.*?)\s([-+]\d+\.\d+)\*?(?=\s|$)")
FROZEN = re.compile(r"^background: after (\d+) frames \(frozen")


def main(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    frozen_at = None
    rows = []
    for line in lines:
        if frozen_at is None:
            m = FROZEN.match(line)
            if m:
                frozen_at = int(m.group(1))
        m = FRAME.match(line)
        if m:
            rows.append((int(m.group(1)), dict(
                (n, float(v)) for n, v in SCORE.findall(m.group(2)))))

    if frozen_at is None:
        print("no frozen background line - was --bg-tau reached?")
        return
    rows = [(i, d) for i, d in rows if i >= frozen_at]
    if len(rows) < 20:
        print(f"only {len(rows)} frames after the freeze; not enough")
        return
    names = sorted(rows[0][1])
    print(f"{path}: {len(rows)} frames after the freeze at frame {frozen_at}\n")

    # Quarters rather than a fitted slope: drift is not promised to be linear,
    # and four means say both how far it went and whether it went steadily.
    q = len(rows) // 4
    parts = [rows[i * q:(i + 1) * q] for i in range(4)]
    print(f"{'query':<20}" + "".join(f"{f'Q{i+1}':>9}" for i in range(4))
          + f"{'span':>9}{'sd':>8}")
    per = {}
    for n in names:
        means = [st.mean(d[n] for _, d in p if n in d) for p in parts]
        allv = [d[n] for _, d in rows if n in d]
        per[n] = means
        print(f"{n:<20}" + "".join(f"{m:+9.2f}" for m in means)
              + f"{means[-1] - means[0]:+9.2f}{st.pstdev(allv):8.2f}")

    # If every query moves by the same amount the shift is common to all four
    # and cannot be the object; what survives subtracting the frame mean is
    # what actually distinguishes them.
    print()
    common = [st.mean(per[n][i] for n in names) for i in range(4)]
    print(f"{'common mode':<20}" + "".join(f"{c:+9.2f}" for c in common)
          + f"{common[-1] - common[0]:+9.2f}")
    print(f"{'after removing it':<20}")
    for n in names:
        rel = [per[n][i] - common[i] for i in range(4)]
        print(f"  {n:<18}" + "".join(f"{r:+9.2f}" for r in rel)
              + f"{rel[-1] - rel[0]:+9.2f}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/m9_drift.log"))
