# /// script
# requires-python = ">=3.11"
# ///
"""Would enrolling a class from more than one visit fix the state stage? Issue #19.

    uv run --script tools/probe_multivisit.py /tmp/m9_cue.log [more logs...]

WHAT THIS IS ASKING. `m9` takes a reference from ONE visit: the operator presses
'1', the board averages the next 20 frames, and that point is the class for the
rest of the run. On 2026-08-17 two benches with enrolments the new guard was
happy with (2.85x and 2.75x) scored 91.7% and 59.2%, and the difference was not
the enrolment - it was where the same object landed when it was staged AGAIN.
09:33's opened book visited at +2.28, +3.71 and +2.75 on the state axis with its
reference at +1.72 and the other class's at +4.00, so its second visit was
nearer the wrong reference than its own and thirty frames went the other way.

A reference taken from one visit sits wherever that visit happened to sit, which
is somewhere at the EDGE of its class's real spread as often as in the middle.
Averaging two visits should put it nearer the middle. That is the whole
hypothesis, it costs one more key press at the bench, and it is answerable
without touching the board because every visit is already in the logs.

HOW IT IS SCORED, and the fairness matters more than the number. Leave-one-visit
-out: for each visit v of each class, build references from visits that are NOT
v, then assign every frame of v to its nearest reference and count. Three arms
on the same folds and the same test frames:

    A  one visit, 20 frames      what the board does today
    B  one visit, every frame    controls for "B and C just see more frames"
    C  two visits, 20 frames each   the proposal

B is the arm that keeps this honest. If C beats A but B also beats A by as much,
the win was frames and not visits, and the answer at the bench is a longer
enrolment window rather than a second key press.

Arm A does not reproduce the board's own figure exactly and is not meant to: its
window starts at the top of the counted segment rather than two frames after the
key, and it is scored on held-out visits only. Compare the arms with each other,
not with tools/score_cue.py.
"""
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_reject import EMPTY, BASELINE, centred, dist, load  # noqa: E402

WINDOW = 20                 # MUST MATCH FGX_ENROL_N in firmware/m9.c


def visits(spans, frames, names):
    """{label: [[c[] per frame], ...]} - one list of vectors per visit.

    Keyed on the cued label rather than on the enrolment schedule, so a visit
    the board never enrolled from is as usable here as one it did.
    """
    out: dict[str, list[list[list[float]]]] = {}
    for a, b, lab in spans:
        if lab in (EMPTY, BASELINE):
            continue
        v = [centred(frames[i][0], names) for i in range(a, b + 1) if i in frames]
        if v:
            out.setdefault(lab, []).append(v)
    return out


def mean_of(chunks):
    """The reference: mean over every frame handed in, of any number of visits."""
    flat = [f for c in chunks for f in c]
    return [st.mean(f[j] for f in flat) for j in range(len(flat[0]))]


def score(log: Path) -> None:
    spans, frames, _enrol, _window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    vis = visits(spans, frames, names)
    labs = sorted(vis)
    print(f"\n=== {log.name}")
    if len(labs) < 2:
        print("    fewer than two classes cued - nothing to be nearest to")
        return
    counts = {lab: len(vis[lab]) for lab in labs}
    print(f"    {', '.join(f'{lab} x{n}' for lab, n in counts.items())}")
    if min(counts.values()) < 3:
        print("    a class with fewer than three visits cannot hold one out AND "
              "enrol from two - skipped")
        return

    # Where each visit sits, which is the whole diagnosis and worth printing
    # before any arm: a class whose visits are tight has nothing for this to fix.
    for lab in labs:
        cs = [mean_of([v]) for v in vis[lab]]
        print(f"    {lab:<18} visit centres " +
              "  ".join(f"{c[0]:+6.2f}" for c in cs))

    arms = {"A one visit, 20 frames": None,
            "B one visit, every frame": None,
            "C two visits, 20 each": None}
    tally = {k: [0, 0] for k in arms}
    for lab in labs:
        for out_i in range(counts[lab]):
            test = vis[lab][out_i]
            # The other classes always enrol the same way in every arm - only
            # the class being held out changes - or the arms would differ by two
            # things at once and the comparison would say nothing.
            base = {o: mean_of([vis[o][0][:WINDOW]]) for o in labs if o != lab}
            keep = [i for i in range(counts[lab]) if i != out_i]
            refs = {
                "A one visit, 20 frames":   mean_of([vis[lab][keep[0]][:WINDOW]]),
                "B one visit, every frame": mean_of([vis[lab][keep[0]]]),
                "C two visits, 20 each":    mean_of([vis[lab][k][:WINDOW]
                                                     for k in keep[:2]]),
            }
            for arm, own in refs.items():
                pool = {lab: own, **base}
                for f in test:
                    hit = min(pool, key=lambda k: dist(f, pool[k]))
                    tally[arm][hit == lab] += 1

    print(f"    leave-one-visit-out, {sum(tally['A one visit, 20 frames'])} "
          f"held-out frames per arm")
    best = max(tally, key=lambda k: tally[k][1])
    for arm in arms:
        bad, good = tally[arm]
        n = bad + good
        print(f"      {arm:<26} {good:>4}/{n}  {100.0 * good / n:5.1f} %"
              f"{'  <' if arm == best else ''}")


def main() -> int:
    logs = [Path(a) for a in sys.argv[1:]]
    if not logs:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    for log in logs:
        score(log)
    print("\nC over A is the second key press; C over B is the second VISIT. "
          "Only the\nsecond one is an argument for changing the bench, and only "
          "a change that\nholds on more than one run is a change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
