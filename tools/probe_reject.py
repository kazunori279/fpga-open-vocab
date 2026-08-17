# /// script
# requires-python = ">=3.11"
# ///
"""Replay open-set rejection against a bench the board already ran. Issue #18.

    uv run --script tools/probe_reject.py /tmp/m9_cue.log [more logs...]

M21 answers "is anything there" on the LEVEL - the frame's mean z placed as a
fraction of the span between an enrolled empty scene and the enrolled classes.
The 2026-08-16 bench measured what that buys for the first time on frames it was
not fit to, and the answer was 16/90 and 22/90 (#15). Two things are wrong with
it and only one is a bug: `absent_lvl` records the background freeze rather than
an empty desk, AND - the part no re-enrolment fixes - the level IS the common
mode, which is exactly the term `c[] = z[] - lvl` subtracts to make the state
stage immune to the 1.5 z of sensor drift in four minutes. Empty and class
fractions overlap by 0.87 of a span at their worst cases, so no pair of edges
separates them.

THE PROPOSAL is to stop measuring presence on its own axis and reject on
distance in the space the state stage already decides in:

    absent  <=>  min_k || c[] - qref[k] ||  >  radius

and this script says whether that separates, without reflashing anything. Every
input is already in the log: the score column prints each query's z, the frame
line prints `lvl`, so `c[] = z[] - lvl` is recoverable per frame, and `qref[k]`
is by definition the mean of `c[]` over that class's enrolment window, whose
offsets are in the `.cues` sidecar. If empty and class distances do not separate
here, the redesign is wrong and the firmware was never the problem.

`radius` is reported as a fraction of `sep`, the closest two references sit to
each other, so it carries the room's calibration the way z already does rather
than being a constant to be wrong about.

WHAT COUNTS AS HELD OUT IS DIFFERENT UNDER THIS RULE, and in the honest
direction. Nothing is fit to an empty scene at all - key '0' does not exist -
so the baseline is test data here where under M21 it was training data. Runs
from before the empty revisit existed can therefore be scored too, which is the
only reason 2026-08-11 is comparable.
"""
import re
import statistics as st
import sys
from pathlib import Path

FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led")
SCORE = re.compile(r"(\S.*?)\s([-+]\d+\.\d+)\*?(?=\s|$)")
LVL = re.compile(r"\slvl([-+][\d.]+)")
EMPTY = "empty"                     # MUST MATCH host/cue.py
BASELINE = "baseline"


def load(log: Path):
    """(spans, frames, enrol, window) - spans are (a, b, label) after settle."""
    cues = Path(str(log) + ".cues")
    if not cues.exists():
        raise SystemExit(f"{log}: no .cues sidecar, so the boundaries are opinion")
    text = cues.read_text()
    if "VOID" in text:
        raise SystemExit(f"{log}: the sidecar is marked VOID")

    settle, window, enrol, spans = 0, 20, [], []
    for line in text.splitlines():
        if line.startswith("#"):
            if m := re.search(r"settle (\d+)", line):
                settle = int(m.group(1))
            if m := re.match(r"#\s*enrol-window\s+(\d+)", line):
                window = int(m.group(1))
            if m := re.match(r"#\s*enrol\s+(\d+)\s+(\d+)", line):
                enrol.append((int(m.group(1)), m.group(2)))
            continue
        if not line.strip():
            continue
        a, b, label = line.split("\t")
        # cue.py records the span it cued; the operator's hand is in shot for
        # --settle frames of it. score_cue.py drops them, so this does too, or
        # the two tools report different denominators for the same run.
        spans.append((int(a) + settle, int(b), label))

    frames = {}
    for line in log.read_text(errors="replace").splitlines():
        if m := FRAME.match(line):
            z = {n: float(v) for n, v in SCORE.findall(m.group(2))}
            lv = LVL.search(line)
            frames[int(m.group(1))] = (z, float(lv.group(1)) if lv else None)
    if not enrol:
        raise SystemExit(f"{log}: no enrolment in the sidecar - this rule needs "
                         f"references. Run ./ab.sh ... --enrol")
    return spans, frames, enrol, window


def centred(z, names):
    """c[i] = z[i] - mean(z), the state axis. The rule lives entirely in here."""
    m = st.mean(z[k] for k in names)
    return [z[k] - m for k in names]


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def auc(pos, neg):
    """P(a random pos ranks above a random neg), ties at a half."""
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def quart(xs):
    s = sorted(xs)
    q = lambda f: s[min(len(s) - 1, int(f * len(s)))]  # noqa: E731
    return q(0.0), q(0.25), q(0.5), q(0.75), s[-1]


def score(log: Path) -> None:
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    print(f"\n=== {log}")
    print(f"    queries {names}, {len(frames)} frames, enrol window {window}")

    # A key sent for frame F is read during F+1 and its window covers
    # F+2 .. F+1+window. Same arithmetic as tools/score_cue.py, and it is the
    # difference between a reference and twenty frames of somebody's hand.
    refs, ref_lab, scat = {}, {}, {}
    for f, k in enrol:
        if k == "0":
            continue                    # this rule does not enrol the empty scene
        lo, hi = f + 2, f + 1 + window
        vecs = [centred(frames[i][0], names) for i in range(lo, hi + 1) if i in frames]
        if len(vecs) < window:
            print(f"    key {k}: only {len(vecs)}/{window} frames in the window - skipped")
            continue
        refs[k] = [st.mean(v[j] for v in vecs) for j in range(len(names))]
        # How far ONE frame of that window fell from the window's own mean, RMS.
        # This is the scale that says whether the gap between two references is
        # a gap: sep cannot, being the unit everything else is quoted in. Same
        # formula as m9.c's FGX_ENROL_SNR guard, and it agrees with the board to
        # the printed digits - keep the two together if either changes.
        scat[k] = sum(st.mean((v[j] - refs[k][j]) ** 2 for v in vecs)
                      for j in range(len(names))) ** 0.5
        # Which class this key is, by which cued segment the window fell in,
        # rather than by its position in the schedule.
        ref_lab[k] = next((lab for a, b, lab in spans if a - 1 <= lo <= b), f"key {k}")
    if len(refs) < 2:
        print("    fewer than two references - nothing to be nearest to")
        return

    keys = sorted(refs)
    sep = min(dist(refs[i], refs[j])
              for x, i in enumerate(keys) for j in keys[x + 1:])
    engage = sorted(f for f, k in enrol if k != "0")[1] + 2 + window
    worst = max(scat[k] for k in keys)
    print(f"    references, in the centred space, and their windows' scatter:")
    for k in keys:
        print(f"      {ref_lab[k]:<18} " +
              "  ".join(f"{v:+.2f}" for v in refs[k]) +
              f"   +-{scat[k]:.2f}")
    print(f"    nearest pair {sep:.2f} apart against {worst:.2f} of scatter "
          f"({sep / worst:.2f}x); rule live from frame {engage}")
    if sep < 2.0 * worst:
        print("      ^ THE CLASSES OVERLAP. Below 2x a frame lands nearer the "
              "wrong reference about\n"
              "        as often as the right one, so this run measured noise. "
              "m9.c refuses to stay\n"
              "        quiet about this since FGX_ENROL_SNR; runs older than "
              "that guard did not know.")
    # WHERE THE ORIGIN IS, because it is not an arbitrary point. c[] = 0 means
    # every query moved together, which is what "nothing has changed since the
    # background was frozen" reads as. A reference that sits close to the origin
    # is a reference this rule cannot fence off from a still scene, and that is
    # the failure mode to watch rather than the radius.
    print("    distance from the origin (= the frozen background) to each "
          "reference, in sep")
    for k in keys:
        print(f"      {ref_lab[k]:<18} {dist(refs[k], [0.0]*len(names))/sep:5.2f}")

    # SANITY, and it is not a formality: the whole replay rests on c[] being
    # recoverable from the log, so check the piece of it the board also prints.
    diffs = [abs(st.mean(z[k] for k in names) - lv)
             for z, lv in frames.values() if lv is not None]
    if diffs:
        print(f"    reconstruction: mean z vs the board's printed lvl, "
              f"worst {max(diffs):.3f} over {len(diffs)} frames")

    # Distance to the NEAREST reference, per frame, and the label the segment
    # says it should have. Only from `engage`: before it the board had fewer
    # than two references and the rule did not exist.
    taught = {(a, b) for a, b, _ in spans
              if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
    rows = []          # (frame, truth, d, nearest_key, held_out)
    for a, b, lab in spans:
        for i in range(a, b + 1):
            if i not in frames or i < engage:
                continue
            c = centred(frames[i][0], names)
            k = min(keys, key=lambda k: dist(c, refs[k]))
            rows.append((i, lab, dist(c, refs[k]), k, (a, b) not in taught))
    if not rows:
        print("    no frames after the rule engages")
        return

    # The empty scene is whatever the schedule called empty; failing that the
    # baseline, which under THIS rule is held out - nothing is fit to it.
    blank_lab = EMPTY if any(r[1] == EMPTY for r in rows) else BASELINE
    blank = [r for r in rows if r[1] == blank_lab]
    if not blank:
        # The baseline runs before the rule engages, so a pre-#15 log has no
        # empty frames left after the cut. Score it from the raw baseline span
        # anyway: the references exist by then even if the board's rule did not.
        blank = []
        for a, b, lab in spans:
            if lab != BASELINE:
                continue
            for i in range(a, b + 1):
                if i not in frames:
                    continue
                c = centred(frames[i][0], names)
                k = min(keys, key=lambda k: dist(c, refs[k]))
                blank.append((i, lab, dist(c, refs[k]), k, True))
        blank_lab = BASELINE + " (replayed before the rule engaged)"
    cls = [r for r in rows if r[1] != EMPTY and r[1] != BASELINE and r[4]]
    if not blank or not cls:
        print("    nothing to separate - need both empty and class frames")
        return

    db = [r[2] for r in blank]
    dc = [r[2] for r in cls]
    if blank_lab.startswith(BASELINE):
        print(f"\n    NOTE: this run has no empty scene after the classes are "
              f"enrolled, so the\n"
              f"    only 'nothing there' frames are the baseline - which sits "
              f"where the background\n"
              f"    was just frozen, i.e. AT THE ORIGIN by construction. That "
              f"is the degenerate\n"
              f"    case for a distance rule and not a test of an empty desk. "
              f"Read the figures\n"
              f"    below as the origin question only; #15's rotation is what "
              f"produces the real one.")
    print(f"\n    distance to the nearest reference, in units of sep={sep:.2f}")
    print(f"      {'':<10} {'n':>4}  {'min':>6} {'q1':>6} {'med':>6} "
          f"{'q3':>6} {'max':>6}")
    for tag, xs in ((blank_lab.split()[0], db), ("classes", dc)):
        lo, q1, md, q3, hi = quart([x / sep for x in xs])
        print(f"      {tag:<10} {len(xs):>4}  {lo:6.2f} {q1:6.2f} {md:6.2f} "
              f"{q3:6.2f} {hi:6.2f}")
    a = auc(db, dc)
    print(f"      AUC {a:.3f}  <- 1.000 is the empty desk always further from "
          f"every reference than any class frame is;\n"
          f"                 0.500 is no signal. THIS IS THE NUMBER #18 TURNS ON.")
    print(f"      worst empty {min(db)/sep:.2f} sep against worst class "
          f"{max(dc)/sep:.2f} sep   -> "
          + ("they separate: a single radius between them is exact"
             if min(db) > max(dc) else
             f"they overlap by {(max(dc)-min(db))/sep:.2f} sep"))

    # DOES IT DRIFT. This is the question M21 failed: its three empty revisits
    # read 0.21 / 0.32 / 0.44 of the span, monotonically, because the level is
    # the common mode and the common mode is where the sensor's warm-up lives.
    # A distance in the centred space should not move at all, and if it does
    # this redesign has inherited the same disease under a new name.
    visits = {}
    for i, lab, d, _, _ in blank:
        visits.setdefault(next(a for a, b, _ in spans if a <= i <= b), []).append(d)
    if len(visits) > 1:
        print(f"\n    per visit to the empty scene, mean distance in sep")
        for a in sorted(visits):
            print(f"      from frame {a:>4}   {st.mean(visits[a])/sep:5.2f} sep"
                  f"   ({len(visits[a])} frames)")
        v = [st.mean(visits[a]) / sep for a in sorted(visits)]
        print(f"      spread {max(v)-min(v):.2f} sep across the run   <- M21's "
              f"equivalent walked 0.23 of a span in one direction")

    # One radius, swept. A fraction of sep rather than an absolute, so the
    # number survives a different room; 0.50 is the obvious first guess (halfway
    # to another class is as far as a frame can be and still be nearer to one of
    # them than to the boundary) and is here to be beaten rather than assumed.
    #
    # `balanced` is the mean of the two rates and not overall accuracy, because
    # the two populations are different sizes and the interesting failure is
    # asymmetric: M21 scores 62% balanced by keeping every class frame and
    # holding almost no empty one.
    print(f"\n    single radius, no hysteresis")
    print(f"      {'r/sep':>6}  {'empty held':>16}  {'class kept':>16}   balanced")
    best = None
    for frac in (0.25, 0.375, 0.50, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 3.6):
        r = frac * sep
        held = sum(1 for x in db if x > r)
        kept = sum(1 for x in dc if x <= r)
        bal = (held / len(db) + kept / len(dc)) / 2
        mark = " *" if best is None or bal > best[0] else ""
        if best is None or bal > best[0]:
            best = (bal, frac, held, kept)
        print(f"      {frac:6.3f}  {held:>6}/{len(db)} {100*held/len(db):5.1f}%"
              f"  {kept:>6}/{len(cls)} {100*kept/len(cls):5.1f}%   "
              f"{100*bal:5.1f}%{mark}")
    bal, frac, held, kept = best
    print(f"      best r = {frac:.3f} sep = {frac*sep:.2f}: holds {held}/{len(db)} "
          f"({100*held/len(db):.1f}%) of the empty desk, keeps {kept}/{len(cls)} "
          f"({100*kept/len(cls):.1f}%) of the classes")

    # Two edges, the way the firmware already does it, on the same distance.
    # Hysteresis costs nothing here and the shipped stage has it, so comparing
    # one-edge-new against two-edge-old would be comparing two changes at once.
    # STAY is the lower edge: once the board has said absent it keeps saying it
    # until the frame comes back inside STAY. TRIP is the higher one.
    print(f"\n    two edges, in units of sep - TRIP to go absent, back inside "
          f"STAY to return")
    order = sorted(blank + cls)
    print(f"      {'STAY':>5} {'TRIP':>5}  {'empty held':>16}  {'class kept':>16}"
          f"   balanced")
    for stay in (0.5, 1.0, 1.5, 2.0):
        for trip in (1.5, 2.0, 2.5, 3.0):
            if trip <= stay:
                continue
            absent, held, kept = False, 0, 0
            for _, lab, d, _, _ in order:
                absent = (d > stay * sep) if absent else (d > trip * sep)
                if lab in (EMPTY, BASELINE):
                    held += absent
                else:
                    kept += not absent
            bal = (held / len(db) + kept / len(dc)) / 2
            print(f"      {stay:5.2f} {trip:5.2f}  {held:>6}/{len(db)} "
                  f"{100*held/len(db):5.1f}%  {kept:>6}/{len(cls)} "
                  f"{100*kept/len(cls):5.1f}%   {100*bal:5.1f}%")

    # WHAT IT IS AGAINST. The shipped stage, replayed on the same frames from
    # the same log, so the comparison is not against a number remembered from a
    # different tool. absent_lvl and the span are rebuilt the way the firmware
    # builds them: key '0's window, and the mean of the class levels.
    lvl_of = {i: st.mean(z[k] for k in names) for i, (z, _) in frames.items()}
    z0 = next((f for f, k in enrol if k == "0"), None)
    if z0 is not None:
        w = [lvl_of[i] for i in range(z0 + 2, z0 + 2 + window) if i in lvl_of]
        absent_lvl = st.mean(w)
        obj = st.mean(st.mean(lvl_of[i] for i in range(f + 2, f + 2 + window)
                              if i in lvl_of)
                      for f, k in enrol if k != "0")
        span = obj - absent_lvl
        present, m_held, m_kept = False, 0, 0
        for i, lab, _, _, _ in order:
            frac = (lvl_of[i] - absent_lvl) / span
            present = (frac >= 0.15) if present else (frac >= 0.50)
            if lab in (EMPTY, BASELINE):
                m_held += not present
            else:
                m_kept += present
        bal = (m_held / len(db) + m_kept / len(dc)) / 2
        print(f"\n    M21 as shipped, replayed on these same frames")
        print(f"      absent_lvl {absent_lvl:+.2f}, objects {obj:+.2f}, "
              f"span {span:+.2f}, edges 0.50 / 0.15")
        print(f"      {'':<11} {m_held:>6}/{len(db)} {100*m_held/len(db):5.1f}%"
              f"  {m_kept:>6}/{len(cls)} {100*m_kept/len(cls):5.1f}%   "
              f"{100*bal:5.1f}%   <- what #18 has to beat")

    # And what the classes cost, because a rejection rule that also rejects the
    # thing it is watching has moved the error rather than removed it.
    st_ok = sum(1 for _, lab, _, k, ho in rows
                if ho and lab not in (EMPTY, BASELINE) and ref_lab[k] == lab)
    st_n = sum(1 for _, lab, _, _, ho in rows if ho and lab not in (EMPTY, BASELINE))
    print(f"\n    state stage, unchanged by any of this: {st_ok}/{st_n} "
          f"({100*st_ok/st_n:.1f}%) held out")


if __name__ == "__main__":
    for p in sys.argv[1:] or ["/tmp/m9_cue.log"]:
        score(Path(p))
