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

There is a fourth now, and it needed a change at the other end to be askable at
all: what the presence stage BUYS. Its cost was always measurable - it is the
class frames it wrongly shuts on - but its benefit is frames of empty scene it
keeps a class name off, and until host/cue.py started returning to the empty
scene mid-run there was no such frame that the empty reference had not been
taught from. There is one segment per repeat now, labelled "empty", and the
section that scores it is the one below the rules.

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

# Everything after `led`, which the score column deliberately stops before
# (firmware/m9.c says why). Three numbers live out here:
#   b   the LED's brightness, which is also the presence stage's confidence.
#       Under #18 it is 1 - d/(TRIP*sep); before that it was the frame's
#       fraction of the enrolled level span. Printed only under a two-stage rule.
#   lvl the frame's mean z. Nothing decides on it since #18 - it is the axis
#       that turned out to be the drift - but it is still what the two runs that
#       killed the level-based stage were diagnosed from.
#   d   THE DECISION VARIABLE under #18: distance to the nearest enrolled
#       reference in the centred space, in units of sep. Absent above TRIP.
#       Missing from any log written before #18, which is what tells the two
#       generations of log apart.
#   de  the distance to the EMPTY reference, in the same units, present only
#       when '0' was pressed. It marks the third generation of log: with it the
#       verdict is `de > d` and no threshold is involved, and `d` alone is half
#       of a comparison. Its absence means the band, which is what the two
#       constants below still describe.
#     ... led 255/  0 h1.00 b0.87 lvl+0.34 d0.26 de1.40   MATCH an opened book ...
TAIL = re.compile(r"\sled\s+\d+/\s*\d+\s+h[-+]?[\d.]+"
                  r"(?:\s+b([-+]?[\d.]+))?\s+lvl([-+][\d.]+)"
                  r"(?:\s+d([\d.]+))?(?:\s+de([\d.]+))?")

# MUST MATCH FGX_PRESENT_ON / FGX_PRESENT_OFF in firmware/m9.c before #18, and
# FGX_ABSENT_TRIP / FGX_ABSENT_STAY after it. Enter one way, leave the other:
# the board latches, so a frame between the edges is present if the one before
# it was and absent if it was not, and no single number scores it.
PRESENT_ON, PRESENT_OFF = 0.50, 0.15        # the level rule, pre-#18 logs
ABSENT_TRIP, ABSENT_STAY = 2.0, 1.5         # the distance rule, in sep

# MUST MATCH host/cue.py's EMPTY. The label of a return visit to the empty
# scene, as opposed to "baseline", which is the leading empty segment the
# reference was taught from. The difference between those two words is the
# whole of #15.
EMPTY = "empty"
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
            # host/cue.py stamps this when demo.py reported a watchdog reboot.
            # Refused rather than warned about: a warning above a table of
            # numbers loses to the table every time, and the numbers from a run
            # whose background froze twice are not weak evidence, they are
            # evidence of a different experiment.
            if line.startswith("# VOID"):
                sys.exit(f"{sidecar} is marked VOID: {line[7:].strip()}\n"
                         f"Re-run the bench. To look at it anyway, delete that "
                         f"line - deliberately, and knowing the above.")
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
        t = TAIL.search(line)
        frames[int(m.group(1))] = (
            scores, hit.group(1).strip() if hit else None,
            float(t.group(1)) if t and t.group(1) else None,
            float(t.group(2)) if t else None,
            float(t.group(3)) if t and t.group(3) else None,
            float(t.group(4)) if t and t.group(4) else None)
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
    # Neither empty segment is a class, and they are dropped here for two
    # different reasons. "baseline" taught the references, so scoring it is
    # scoring training frames. "empty" is held out and IS scored - just not
    # against a class it was never supposed to win, which is what leaving it in
    # `scenes` would do. It gets its own section, which is #15.
    scenes = [c for c in cues if c[2] not in ("baseline", EMPTY)]
    blank = [c for c in cues if c[2] == EMPTY]
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
    #
    # THE LAST WINDOW AND NOT THE SECOND, since FGX_ENROL_V. A class enrolled
    # from more than one visit has a reference that MOVES when the later visit
    # lands, so the frames in between were scored against a reference the run
    # did not finish with - live, but not the rule whose score is being
    # reported. On a one-visit-per-class log the two are the same frame and
    # this changes nothing, which is why it is safe to apply to old logs.
    #
    # AND THE '0' WINDOW COUNTS TOO, for exactly the same reason (2026-08-25).
    # The empty reference does not move a class reference, but until it lands
    # the presence stage is #18's band and not the nearest-of-three rule - a
    # different rule, whose frames would otherwise be counted under this one's
    # name. The 13:09 bench is the case that found it: '0' went in at frame 272
    # so the rule was live from 294, while `engage` read 254 and swept in the
    # whole 260-299 empty span. That span reported 23/30 called present, all of
    # it the band's, and it dragged the third reference's own figure from
    # 82.1% down to 62.2%. The '0' key still cannot make a rule EXIST - two
    # classes do that - so it does not join the `len(cls) >= 2` test.
    cls = sorted(f for f, k in enrol if k != "0")
    emp = sorted(f for f, k in enrol if k == "0")
    engage = (max(cls[-1], *emp) + 2 + window if len(cls) >= 2 and emp else
              cls[-1] + 2 + window if len(cls) >= 2 else None)
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

    # The empty revisits are in the table - a presence stage is judged on where
    # the empty scene sits relative to the classes, and that is a comparison of
    # rows - but they are not in `scenes` and so not in anything scored as a
    # class. Sorted, so the table reads in the order the operator was cued.
    # Which generation of firmware wrote this log. `d` is #18's decision
    # variable and only appears after it; without it the presence column is the
    # old level fraction and has to be described as one, because the two numbers
    # do not mean the same thing and a run of each will be compared side by side
    # for a while yet.
    dist_rule = any(f[4] is not None for f in frames.values())
    # Three generations of presence stage now print into this file and the
    # counts below are the board's own verdicts either way - what changes is
    # what they are a count OF, so the description has to follow the log rather
    # than the constants at the top of this script.
    three_rule = any(f[5] is not None for f in frames.values())

    print("per segment")
    head = ("  cue  scene           n  " + "".join(f"{n:>16}" for n in names)
            + f"{'presence':>10}" + (f"{'d/sep':>8}" if dist_rule else ""))
    print(head)
    for a, b, lab in sorted(scenes + blank):
        fs = scored(a, b)
        if not fs:
            continue
        row = f"  {a:>3}  {lab:<14} {len(fs):>2}  "
        row += "".join(f"{st.mean([frames[f][0].get(n, 0.0) for f in fs]):>+16.2f}"
                       for n in names)
        lit = [frames[f][2] for f in fs if frames[f][2] is not None]
        row += f"{st.mean(lit):>+10.2f}" if lit else f"{'-':>10}"
        if dist_rule:
            ds = [frames[f][4] for f in fs if frames[f][4] is not None]
            row += f"{st.mean(ds):>8.2f}" if ds else f"{'-':>8}"
        print(row)
    if dist_rule:
        print(f"  d/sep is the distance to the NEAREST enrolled reference, in "
              f"units of the\n  closest pair of references: the quantity #18's "
              f"stage cuts. Absent above\n  {ABSENT_TRIP:.1f}, present again "
              f"below {ABSENT_STAY:.1f}. presence is the LED's brightness on the "
              f"same\n  distance, 1 on a reference and 0 a trip radius from all "
              f"of them.")
    else:
        print("  presence is the frame's fraction of the enrolled span: 0 is "
              "where the empty\n  scene read, 1 is where the objects did. Blank "
              "under any rule that has no\n  presence stage to print it. This "
              "log predates #18.")
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
            print(f"enrolled  : rule settled at frame {engage} (the last "
                  f"reference lands there); {len(before)} earlier scored frames "
                  f"were the old rule's or an earlier reference's and are not "
                  f"counted")
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

    # 5. WHAT THE PRESENCE STAGE BUYS, which is #15 and which nothing measured
    #    before. Everything above measures its COST - the class frames it shuts
    #    on - because until cue.py put the empty scene back in the rotation
    #    there was no scored frame with nothing in it. There is now, and it is
    #    held out the way a later visit to a class is: the empty reference came
    #    off the baseline at the head of the run, these visits come after every
    #    class has been enrolled.
    #
    #    Be blunt about how cheap the benefit number is. Ranking has no way to
    #    answer "nothing" - argmax over the enrolled classes returns one of them
    #    on every frame there is - so on an empty desk rank-only is wrong N out
    #    of N by construction, and every frame the stage holds is one of those
    #    removed. The two counts below are therefore one measurement read from
    #    either end, and the one carrying information is the false positives:
    #    how much of an empty scene still comes back with a class name on it.
    if blank:
        efs = [f for a, b, _ in blank for f in scored(a, b)]
        late = [f for f in efs if engage is None or f >= engage]
        print(f"\nempty scene, {len(blank)} revisit"
              f"{'' if len(blank) == 1 else 's'}, held out")
        # Which rule's "nothing there" is being scored. All three print the
        # same absent frame, and only two of them have a presence stage.
        if enrol and three_rule:
            print("  stage: M21 with a third reference, 2026-08-25: absent when "
                  "the empty scene is\n         the nearest of the three. No "
                  "threshold, so neither constant above applies")
        elif enrol and dist_rule:
            print(f"  stage: M21 after #18, distance to the nearest reference: "
                  f"absent above {ABSENT_TRIP:.1f} sep, back below "
                  f"{ABSENT_STAY:.1f}")
        elif enrol:
            print("  stage: M21 before #18, the frame's fraction of the "
                  "enrolled level span")
        elif gates:
            print(f"  stage: M20, the gate query {gates}")
        else:
            print("  stage: none - no gate and no enrolment, so a frame with no "
                  "MATCH is\n         only one where nothing cleared its own "
                  "threshold. Not a presence rule.")
        if len(late) != len(efs):
            print(f"  {len(efs) - len(late)} of {len(efs)} frames came before "
                  f"the rule was live at {engage} and are not counted")
        if not late:
            print("  no scored frames - nothing to say")
        else:
            named = [f for f in late if frames[f][1] is not None]
            held = len(late) - len(named)
            print(f"  called present, wrongly    {len(named):>4}/{len(late)}  "
                  f"({100 * len(named) / len(late):5.1f}%)")
            print(f"  held                       {held:>4}/{len(late)}  "
                  f"({100 * held / len(late):5.1f}%)   <- what the stage buys, "
                  f"against rank-only's {len(late)}/{len(late)} wrong")
            if named:
                by = {}
                for f in named:
                    by[frames[f][1]] = by.get(frames[f][1], 0) + 1
                print("  what it called them:       "
                      + ", ".join(f"{k} {v}" for k, v in
                                  sorted(by.items(), key=lambda kv: -kv[1])))
            for a, b, _ in blank:
                fs = [f for f in scored(a, b) if f in late]
                if not fs:
                    continue
                bad = sum(frames[f][1] is not None for f in fs)
                # From the cue, not from the first scored frame: the operator
                # taking the object away is the event, and how long the latch
                # takes to let go of it is a property of the rule worth seeing
                # next to --settle rather than hidden inside it.
                rel = next((f - a for f in range(a, b + 1)
                            if f in frames and frames[f][1] is None), None)
                print(f"    cue {a:>3}   {bad}/{len(fs)} present   "
                      + (f"released {rel} frames after the cue" if rel is not None
                         else "never released"))

        # Where the empty scene sat on the axis the edges cut, and where the
        # classes sat, because a benefit of "it held" means nothing without the
        # margin it held by. The old stage picked 0.15 out of a gap it measured
        # this way, and the 08-16 bench then found the gap was not there.
        #
        # UNDER #18 THE AXIS POINTS THE OTHER WAY: the empty scene should be
        # FAR from every reference and the classes near one, so the worst cases
        # swap ends. Two blocks rather than one signed number, because a table
        # whose "worst" column silently changes meaning between two logs is how
        # a comparison goes wrong without anybody noticing.
        if dist_rule:
            ed = [frames[f][4] for f in late if frames[f][4] is not None]
            cd = [frames[f][4] for a, b, _ in scenes for f in scored(a, b)
                  if frames[f][4] is not None]
            if ed and cd:
                print("\n  distance to the nearest reference, " + (
                    "against the empty reference's own below"
                    if three_rule else
                    f"absent above {ABSENT_TRIP:.1f} / back below "
                    f"{ABSENT_STAY:.1f} sep"))
                print(f"    empty      mean {st.mean(ed):6.2f}   "
                      f"worst (lowest)  {min(ed):6.2f}")
                print(f"    classes    mean {st.mean(cd):6.2f}   "
                      f"worst (highest) {max(cd):6.2f}")
                gap = min(ed) - max(cd)
                print(f"    gap between the two worst cases {gap:+.2f} sep"
                      + ("" if gap > 0 else
                         "   <- they overlap: no single pair of edges separates "
                         "these two"))
            # AND THE OTHER HALF OF THE COMPARISON, which only exists in a
            # third-generation log. `d` overlapping does not condemn this rule
            # the way it condemns the band: the cut is `de > d` per frame, so
            # what matters is the SIGN of the difference and not whether either
            # column separates on its own.
            if three_rule:
                de_e = [frames[f][5] for f in late if frames[f][5] is not None]
                de_c = [frames[f][5] for a, b, _ in scenes for f in scored(a, b)
                        if frames[f][5] is not None]
                dd_e = [frames[f][5] - frames[f][4] for f in late
                        if frames[f][5] is not None and frames[f][4] is not None]
                dd_c = [frames[f][5] - frames[f][4]
                        for a, b, _ in scenes for f in scored(a, b)
                        if frames[f][5] is not None and frames[f][4] is not None]
                if de_e and de_c:
                    print("\n  distance to the EMPTY reference, and the margin "
                          "the rule actually cuts on")
                    print(f"    empty      mean {st.mean(de_e):6.2f}   "
                          f"de - d  mean {st.mean(dd_e):+6.2f}   "
                          f"absent on {sum(x < 0 for x in dd_e):>4}/{len(dd_e)}")
                    print(f"    classes    mean {st.mean(de_c):6.2f}   "
                          f"de - d  mean {st.mean(dd_c):+6.2f}   "
                          f"absent on {sum(x < 0 for x in dd_c):>4}/{len(dd_c)}")
                    print("    negative is absent. These two counts ARE the "
                          "rule, so they must agree with\n    the verdicts "
                          "above - and the line below checks that rather than "
                          "asserting it")
                    # SAYING "these must agree" and not looking was the first
                    # version, and on the very first board run they did not:
                    # 40/120 derived against 41 verdicts, 54/66 against 55.
                    # Both are ties. `d` and `de` are printed to two decimals,
                    # so a frame the board decided in full float on a
                    # difference below 0.005 reaches this script as an exact
                    # 0.00 and falls to the present side of `x < 0`. That is a
                    # print-width artefact and NOT a drift, but the only way to
                    # know which of the two it is, is to count the ties.
                    dis = [f for f in
                           list(late) + [f for a, b, _ in scenes
                                         for f in scored(a, b)]
                           if frames[f][5] is not None
                           and frames[f][4] is not None
                           and ((frames[f][5] - frames[f][4]) < 0)
                           != (frames[f][1] is None)]
                    ties = [f for f in dis
                            if frames[f][5] == frames[f][4]]
                    if not dis:
                        print("    checked: every frame's sign matches the "
                              "board's own verdict")
                    elif len(ties) == len(dis):
                        print(f"    checked: {len(dis)} frame"
                              f"{'' if len(dis) == 1 else 's'} differ "
                              f"({', '.join(str(f) for f in dis)}) and all of "
                              f"them print d == de\n    to two decimals - the "
                              f"board cut them in full float. Rounding, not "
                              f"drift")
                    else:
                        print(f"    !! {len(dis) - len(ties)} frame(s) differ "
                              f"and are NOT ties: "
                              f"{', '.join(str(f) for f in dis if f not in ties)}"
                              f"\n    This script and the board disagree about "
                              f"the rule. Do not quote the counts above")
        else:
            elit = [frames[f][2] for f in late if frames[f][2] is not None]
            clit = [frames[f][2] for a, b, _ in scenes for f in scored(a, b)
                    if frames[f][2] is not None]
            if elit and clit:
                print(f"\n  presence fraction, enter {PRESENT_ON:.2f} / "
                      f"leave {PRESENT_OFF:.2f}")
                print(f"    empty      mean {st.mean(elit):+.3f}   "
                      f"worst (highest) {max(elit):+.3f}")
                print(f"    classes    mean {st.mean(clit):+.3f}   "
                      f"worst (lowest)  {min(clit):+.3f}")
                gap = min(clit) - max(elit)
                print(f"    gap between the two worst cases {gap:+.3f}"
                      + ("" if gap > 0 else
                         "   <- they overlap: no single pair of edges separates "
                         "these two"))

    # 6. How long the operator and the EMA took. A settle that is too short is
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
