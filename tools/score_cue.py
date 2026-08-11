# /// script
# requires-python = ">=3.11"
# ///
"""Score a host/cue.py run against the boundaries it recorded.

    uv run --script tools/score_cue.py /tmp/m9_cue.log

The log and its .cues sidecar are the whole input. cue.py writes the sidecar as
it goes, so the segment boundaries are RECORDED rather than reconstructed
afterwards from where the numbers happen to move - which matters here, because
the thing being measured is exactly whether the numbers move where they should.

WHAT THIS ANSWERS, AND WHY IT IS THREE QUESTIONS AND NOT ONE.

The 2026-08-10 hand run is the reason this exists. Read one number off it and
you get one of three different stories:

  the gate fired                     180/180   - M20 works
  the two-stage MATCH was right      104/180   - M20 does not work
  the gate query alone told the
  two poses apart                    180/180   - M20 is aimed at the wrong query

All three are true of the same 180 frames. So this prints all three, plus the
per-segment table they are computed from, because the per-segment table is what
shows a between-segment drift big enough to swallow the effect.

AUC, NOT ACCURACY, FOR THE DISCRIMINATION. Accuracy needs a threshold and the
threshold is the thing in question; AUC is the fraction of (scene A frame,
scene B frame) pairs that are ordered correctly, so it asks whether the
separation exists at all before asking where to cut. The best-threshold
accuracy is printed next to it, and the gap between the two is how much of the
score is real and how much is a cut fitted to this run.

RAW COSINE IS RECOVERED, NOT MEASURED. m9 prints z, not cos, but z is
(cos - mu) / sd with mu and sd frozen after the baseline and printed on the
board's "background:" line, so cos comes back exactly. It is worth having:
comparing two queries' z compares two different standardisations, and if the
ranking only works in one of the two spaces that is a fact about the rule
rather than about the scene.
"""

import argparse
import math
import re
import statistics as st
import sys
from pathlib import Path

# frame   266 :  a hand +19.02*  an open hand~ -0.70  a closed hand~ -4.92   led ...
FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led")
SCORE = re.compile(r"(\S.*?)\s([-+]\d+\.\d+)\*?(?=\s|$)")
MATCH = re.compile(r"MATCH (.+?) \(cos")
# background: after 30 frames (frozen, room spread), this room reads  an open
# hand~ -0.057 +-0.0046 (COCO ...)  a closed hand~ 0.062 +-0.0051 (COCO ...)
#
# Matched per known query name rather than by a general "name then numbers"
# pattern. The line opens with prose that ends in a number - "after 30 frames" -
# and a general pattern happily reads that as a query called "background: after".
def bg_of(line, name):
    m = re.search(re.escape(name) + r"\s([-+]?\d+\.\d+)\s\+-(\d+\.\d+)", line)
    return (float(m.group(1)), float(m.group(2))) if m else None

# The roles arrive as indented continuations of the "queries :" block, not as
# lines of their own:
#             a hand               presence, gates above z 1.23   (COCO ...
# The trailing clause is required so that the prose two lines above - "the
# presence queries gate, and the state queries are then RANKED" - is not read
# as a query named "TWO-STAGE: the".
ROLE = re.compile(r"^\s+(\S.*?)\s{2,}(plain|presence|state)"
                  r"(?:,\s+(?:ranked|gates)|\s+z>)")


def auc(pos, neg):
    """P(a random pos scores above a random neg), ties counted as half."""
    if not pos or not neg:
        return float("nan")
    above = sum(1 for p in pos for n in neg if p > n)
    tied = sum(1 for p in pos for n in neg if p == n)
    return (above + 0.5 * tied) / (len(pos) * len(neg))


def best_cut(pos, neg):
    """Accuracy of the best single threshold, the threshold, and which way it
    points. Fitted to this run by construction - that is the point of printing
    it beside the AUC.

    BOTH DIRECTIONS ARE TRIED, and the first version of this tried only "pos
    above the cut". That is not a conservative choice, it is a wrong one: every
    query that separates the scenes the other way came back at exactly 50.0%,
    which reads as "this query is useless" when the truth was the opposite. The
    book run printed 50.0% for `a book` while that query separated opened from
    closed at AUC 0.999 the other way round."""
    best = (0.0, float("nan"), ">")
    for t in sorted(set(pos) | set(neg)):
        up = (sum(p > t for p in pos) + sum(n <= t for n in neg))
        dn = (sum(p <= t for p in pos) + sum(n > t for n in neg))
        for acc, arrow in ((up / (len(pos) + len(neg)), ">"),
                           (dn / (len(pos) + len(neg)), "<")):
            if acc > best[0]:
                best = (acc, t, arrow)
    return best


def cohen_d(a, b):
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    s = math.sqrt((st.pstdev(a) ** 2 + st.pstdev(b) ** 2) / 2) or float("nan")
    return (st.mean(a) - st.mean(b)) / s


def load(log):
    cues, frames, roles, bg, enrol = [], {}, {}, {}, []
    window = 1          # pre-2026-08-11 sidecars: one captured frame, no line
    sidecar = Path(str(log) + ".cues")
    if not sidecar.exists():
        sys.exit(f"no {sidecar} - that run was not cued, so there is nothing to "
                 f"score it against. Guessing the boundaries afterwards is how "
                 f"you get to confirm whatever you already believed.")
    for line in sidecar.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            # M21 runs teach the board from their own frames, and a segment the
            # board learned from is a training segment. Scoring it beside the
            # rest and calling the total an accuracy is the oldest mistake there
            # is; the frames are recorded so this does not have to make it.
            m = re.match(r"#\s*enrol\s+(\d+)\s+(\d+)", line)
            if m:
                enrol.append((int(m.group(1)), m.group(2)))
            m = re.match(r"#\s*enrol-window\s+(\d+)", line)
            if m:
                window = int(m.group(1))
            continue
        a, b, name = line.split("\t")
        cues.append((int(a), int(b), name.strip()))

    for line in Path(log).read_text().splitlines():
        r = ROLE.match(line)
        if r:
            roles[r.group(1)] = r.group(2)
        if line.startswith("background:"):
            # The last one wins: mu and sd are frozen after the baseline, and a
            # re-learn ('N') restarts the run as far as this is concerned. The
            # warming-up line printed before the freeze is a different pair of
            # numbers and scoring against it would be scoring against a baseline
            # the board itself no longer uses.
            bg = {n: v for n in roles
                  if (v := bg_of(line, n)) is not None}
        m = FRAME.match(line)
        if not m:
            continue
        scores = {n.strip(): float(v) for n, v in SCORE.findall(m.group(2))}
        hit = MATCH.search(line)
        frames[int(m.group(1))] = (scores, hit.group(1).strip() if hit else None)
    return cues, frames, roles, bg, enrol, window


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", type=Path)
    p.add_argument("--settle", type=int, default=10,
                   help="frames to drop after each cue, for the operator's hand "
                        "to arrive and the EMA to follow it (default 10)")
    args = p.parse_args()

    cues, frames, roles, bg, enrol, window = load(args.log)
    scenes = [c for c in cues if c[2] != "baseline"]
    # A visit the board learned from, by frame number rather than by position in
    # the schedule: cue.py enrols on the first visit to each scene today, and a
    # rule about "the first two segments" would go quietly wrong the day that
    # changes.
    taught = {(a, b) for a, b, _ in scenes if any(a <= f <= b for f, _ in enrol)}

    # WHEN THE RULE STARTS EXISTING. M21 needs two enrolled classes before it
    # runs at all, so every frame before the second reference lands is the OLD
    # rule's output - and charging those to M21 is not conservatism, it is a
    # different rule's score under M21's name. The 2026-08-11 bench reported
    # "enrolled from 26/60 (43.3%)" and 30 of those 60 frames were scored before
    # the board had a second reference to be nearest to.
    #
    # A key sent for frame F is read during F+1 and its window covers
    # F+2 .. F+1+window, so the rule is live from F+2+window.
    cls = sorted(f for f, k in enrol if k != "0")
    engage = cls[1] + 2 + window if len(cls) >= 2 else None
    if not scenes:
        sys.exit("no scene segments in the sidecar")
    names = list(next(iter(frames.values()))[0]) if frames else []
    gates = [n for n in names if roles.get(n) == "presence"]
    labels = sorted({n for _, _, n in scenes})

    # THE RANKING SET IS THE QUERIES THAT NAME THE SCENES, whatever demo.py
    # calls their role. Reading it off role == "state" was the first version and
    # it silently skipped every plain-query run: ranking two bare phrases is the
    # experiment that finally beat the two-stage rule, and the tool printed no
    # ranking line for it at all. A query is in the ranking set if some scene is
    # named after it, with the '~' demo.py adds to a contrast form removed.
    states = [n for n in names if n.rstrip("~").strip() in labels]

    # Which state query each scene is supposed to win. cue.py names a scene with
    # the bare phrase and demo.py suffixes the contrast form with '~', so the
    # match is on the prefix rather than on equality.
    want = {}
    for lab in labels:
        hits = [s for s in states if s.rstrip("~").strip() == lab]
        want[lab] = hits[0] if len(hits) == 1 else None

    def scored(a, b):
        return [f for f in range(a + args.settle, b + 1) if f in frames]

    print(f"{args.log}  -  {len(scenes)} scenes, settle {args.settle}, "
          f"gate {gates or '(none)'}, states {states or '(none)'}\n")

    print("per segment")
    head = "  cue  scene           n  " + "".join(f"{n:>16}" for n in names)
    print(head)
    for a, b, lab in scenes:
        fs = scored(a, b)
        row = f"  {a:>3}  {lab:<14} {len(fs):>2}  "
        row += "".join(f"{st.mean([frames[f][0].get(n, 0.0) for f in fs]):>+16.2f}"
                       for n in names)
        print(row)
    print()

    # 1. Did the gate let anything through at all? This is M20's own claim.
    if gates:
        fs = [f for a, b, _ in scenes for f in scored(a, b)]
        fired = sum(frames[f][1] is not None for f in fs)
        print(f"gate      : fired on {fired}/{len(fs)} scored frames "
              f"({100 * fired / len(fs):.0f}%)")

    # 2. Was the two-stage answer the right one? What the board actually says.
    fs = [(lab, f) for a, b, lab in scenes for f in scored(a, b)]
    # A set of frames rather than a third element on every tuple: `fs` is read
    # by four more blocks below and widening it broke all of them at once.
    taught_f = {f for a, b, _ in scenes if (a, b) in taught for f in scored(a, b)}
    known = [(lab, f) for lab, f in fs if want.get(lab)]
    if known and engage is not None:
        before = [f for _, f in known if f < engage]
        known = [(lab, f) for lab, f in known if f >= engage]
        if before:
            print(f"enrolled  : rule live from frame {engage} (second reference "
                  f"lands there); {len(before)} earlier scored frames are the "
                  f"old rule's and are not counted")
    if known:
        ok = sum(frames[f][1] == want[lab] for lab, f in known)
        rule = "enrolled " if enrol else "two-stage"
        print(f"{rule} : MATCH correct on {ok}/{len(known)} "
              f"({100 * ok / len(known):.1f}%)")
        for lab in labels:
            sub = [(l, f) for l, f in known if l == lab]
            if sub:
                h = sum(frames[f][1] == want[lab] for _, f in sub)
                print(f"            {lab:<20} {h}/{len(sub)}")
        # The number worth quoting is the held-out one, and it goes last so it
        # is the one still on the screen. The total above is printed anyway,
        # because a big gap between the two is itself the finding.
        if taught:
            for tag, want_t in (("enrolled from", True), ("HELD OUT", False)):
                sub = [(l, f) for l, f in known if (f in taught_f) is want_t]
                if not sub:
                    continue
                h = sum(frames[f][1] == want[lab] for lab, f in sub)
                print(f"            {tag:<20} {h}/{len(sub)} "
                      f"({100 * h / len(sub):.1f}%)")

    # 3. Is the separation there at all, in each query on its own? A query that
    #    separates the scenes but never fires is a threshold problem; one that
    #    does not separate them is not fixable by any rule downstream.
    if len(labels) == 2:
        one, two = labels
        print(f"\nseparation  ({two} above {one})")
        print("  query                       AUC   best cut    acc      d")
        for n in names:
            pos = [frames[f][0][n] for lab, f in fs if lab == two and n in frames[f][0]]
            neg = [frames[f][0][n] for lab, f in fs if lab == one and n in frames[f][0]]
            acc, cut, arrow = best_cut(pos, neg)
            print(f"  {n:<24} {auc(pos, neg):.3f}   z{arrow}{cut:>+6.2f}  "
                  f"{100 * acc:>5.1f}%  {cohen_d(pos, neg):+5.2f}")

        if len(states) == 2:
            lo, hi = states
            def margin(f):
                return frames[f][0][lo] - frames[f][0][hi]
            pos = [margin(f) for lab, f in fs if lab == two]
            neg = [margin(f) for lab, f in fs if lab == one]
            acc, cut, arrow = best_cut(pos, neg)
            lab = f"margin {lo} - {hi}"
            print(f"  {lab[:24]:<24} {auc(pos, neg):.3f}   "
                  f" {arrow}{cut:>+6.2f}  {100 * acc:>5.1f}%  {cohen_d(pos, neg):+5.2f}")

        # The same question in the teacher's own units, which is the one place
        # a difference between the two rules can show up.
        if bg:
            print("\n  the same, on raw cosine rather than z:")
            for n in names:
                if n not in bg:
                    continue
                mu, sd = bg[n]
                pos = [mu + frames[f][0][n] * sd for lab, f in fs if lab == two]
                neg = [mu + frames[f][0][n] * sd for lab, f in fs if lab == one]
                print(f"  {n:<24} {auc(pos, neg):.3f}   "
                      f"{one} {st.mean(neg):+.4f}  {two} {st.mean(pos):+.4f}  "
                      f"(moves {st.mean(pos) - st.mean(neg):+.4f}, "
                      f"sd {st.pstdev(neg + pos):.4f})")

    # 4. What the other rules would have said. The comparison that matters is
    #    rank-only against two-stage: the gate can only ever remove answers, so
    #    the gap between them is the price of the gate, and on the book run that
    #    price was 50 points - "a book" reads NEGATIVE with an opened book in
    #    shot, so the gate shut on exactly one of the two classes it was meant
    #    to be admitting.
    if len(states) == 2 and want and all(want.get(l) for l in labels):
        pts = [(lab, f) for a, b, lab in scenes for f in scored(a, b)]

        def lead(f):
            return max(states, key=lambda s: frames[f][0][s])

        rank = sum(lead(f) == want[lab] for lab, f in pts)
        print(f"\nrules       rank the states, no gate    "
              f"{rank}/{len(pts)} ({100 * rank / len(pts):.1f}%)")

        # Two-point calibration, scored on the visits it did NOT see. Ranking
        # gets the ORDER right and the ZERO wrong - AUC 1.000 on the bare book
        # pair against 79.4% at a margin of zero - so the question is whether
        # one visit per state is enough to place the boundary. Fitted on the
        # first visit to each and scored on the rest, because fitting and
        # scoring on all six would just report the best cut again.
        if len(labels) == 2 and all(want.get(l) for l in labels):
            hi, lo = want[labels[0]], want[labels[1]]

            def margin(f):
                return frames[f][0][hi] - frames[f][0][lo]

            first, order = {}, []
            for a, b, lab in scenes:
                fs = scored(a, b)
                if lab not in first and fs:
                    first[lab] = st.mean(margin(f) for f in fs)
                    order.append((a, b))
            if len(first) == 2:
                cut = sum(first.values()) / 2
                held = [(lab, f) for a, b, lab in scenes if (a, b) not in order
                        for f in scored(a, b)]
                if held:
                    ok = sum((margin(f) > cut) == (lab == labels[0])
                             for lab, f in held)
                    print(f"            one visit per state, then held out  "
                          f"{ok}/{len(held)} ({100 * ok / len(held):.1f}%)  "
                          f"at margin {cut:+.2f}")
        if gates:
            thr = {}
            for line in Path(args.log).read_text().splitlines():
                t = re.search(r"gates above z ([\d.]+)", line)
                if t and (r := ROLE.match(line)):
                    thr[r.group(1)] = float(t.group(1))
            cut = min(thr.values()) if thr else 1.23
            two = sum(all(frames[f][0][g] >= cut for g in gates) and lead(f) == want[lab]
                      for lab, f in pts)
            print(f"            two-stage, gate at z {cut:<6.2f}  "
                  f"{two}/{len(pts)} ({100 * two / len(pts):.1f}%)")

    # 5. How long the operator and the EMA took. A settle that is too short is
    #    counted as wrong answers and looks exactly like a worse model.
    print("\nlag to the right leader")
    for a, b, lab in scenes:
        w = want.get(lab)
        if not w:
            continue
        flip = next((f - a for f in range(a, b + 1)
                     if f in frames and frames[f][1] == w), None)
        print(f"  cue {a:>3} {lab:<16} " +
              (f"{flip} frames" if flip is not None else "never"))


if __name__ == "__main__":
    main()
