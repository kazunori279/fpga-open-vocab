# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch", "transformers", "sentencepiece"]
# ///
"""Sweep wordings against vectors the board already produced.

    uv run --script tools/probe_prompts.py --log /tmp/m9_cue.log \
        --a "an open hand" --b "a closed hand" --phrases tools/phrases_hand.txt

    uv run --script tools/probe_prompts.py \
        --log-a /tmp/m9_open.log --log-b /tmp/m9_closed.log --phrases -

WHY THIS EXISTS. Every wording experiment so far has cost a board session: 3.5
minutes of frames, a hand held still, and a power cycle when the loop hangs.
None of that is needed. The board's contribution to "does this sentence rank
this scene above that one" is the 512-d vector, and it dumps that on 'V'. Once
two states have been captured, fifty phrasings cost one laptop minute and no
board time at all - and they are measured against *the same frames*, so a
difference between two wordings is a difference between the wordings rather
than between two visits to a scene, which on this bench has been the larger
term (spread 1.90 on a mean of 2.78, 2026-08-10).

TWO QUESTIONS, NOT ONE, and keeping them apart is the point. `d` asks whether a
wording tells scene A from scene B. `zA`/`zB` ask what the board's threshold
sees: each scene against the run's baseline segment, mean and spread both, as
m9.c's zscore() computes it with FGX_BG_ROOM_SD. These come apart badly. The
shipped `a closed hand / an open hand / a hand` scores d -3.72 at AUC 0.00 -
every closed frame above every open frame, a perfect discriminator - and z -0.1
on its own scene, so it fired 0/90 on the board. An empty desk is as much "not
an open hand" as a fist is. Read both columns or the table will mislead you in
whichever direction you were already leaning.

When the log has a baseline segment the run's own `background:` line is parsed
and printed against this bank's, because the two are the same quantity computed
two ways and a disagreement would mean the sweep is measuring some other space -
which would not raise anything anywhere else. They agree to 0.001 on the bench.

The syntax for a candidate is demo.py's own, so a line that wins here can be
pasted into a run unchanged - NEG_SEP separates a positive from its negatives
and the tool scores both the bare positive and the contrast vector for every
line, because which of the two forms is better has not been the same twice.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "model"))

import json                                        # noqa: E402
from caption import vectors_from_log               # noqa: E402
import teacher                                     # noqa: E402
import spaces                                      # noqa: E402

NEG_SEP = "/"          # demo.py:173, and it must stay the same character


AT = re.compile(r"^frame (\d+)$")
# The board's own background line, which is the only authority on what it
# subtracted. "  an open hand~ -0.058 +-0.0044 (COCO ...)" - the trailing ~
# marks a contrast query, and the name before it is the positive part.
BG = re.compile(r"\s\s(.+?)(~?) (-?[\d.]+) \+-([\d.]+) \(COCO")


def load_bank(log: Path, lo: int | None, hi: int | None) -> np.ndarray:
    """Unit vectors from a log's 'V' dumps, optionally windowed by frame.

    vectors_from_log labels each block from the "embedding : frame N" line the
    board prints right before it, paired positionally - one such line per block,
    so the pairing holds however many frames went by in between. A block whose
    label is not a frame number is one with no such line, which means the
    pairing has slipped; drop it rather than guess, because a vector filed under
    the wrong scene would not look like an error, it would look like a result.
    A window of None keeps everything, which is the two-log case.
    """
    vs = []
    for label, v in vectors_from_log(log):
        m = AT.match(label)
        if m is None:
            print(f"WARN  : {log.name}: block labelled {label!r} has no frame "
                  f"line - skipped", file=sys.stderr)
            continue
        f = int(m[1])
        if (lo is None or lo <= f) and (hi is None or f <= hi):
            vs.append(v.astype(np.float32))
    if not vs:
        raise SystemExit(f"{log}: no 'V' dumps in frames {lo}..{hi}. "
                         f"Re-run with --emb --snap-every=N.")
    m = np.stack(vs)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def segments(cues: Path) -> list[tuple[str, int, int]]:
    out = []
    for line in cues.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        lo, hi, label = line.split("\t")
        out.append((label, int(lo), int(hi)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path,
                    help="one cue.py log holding both states; needs --a/--b "
                         "and a <log>.cues sidecar to say which frames are which")
    ap.add_argument("--cues", type=Path,
                    help="the sidecar, if it is not <log>.cues")
    ap.add_argument("--log-a", type=Path, help="or one log per state")
    ap.add_argument("--log-b", type=Path)
    ap.add_argument("--a", default="A", help="the scene label in the sidecar")
    ap.add_argument("--b", default="B")
    ap.add_argument("--settle", type=int, default=10,
                    help="frames to drop after each cue, matching cue.py's; "
                         "default 10")
    ap.add_argument("--phrases", type=Path, required=True,
                    help="one candidate per line, in demo.py's query syntax "
                         "('a / b / c' is a contrast). '-' reads stdin. Blank "
                         "lines and # comments are skipped")
    ap.add_argument("--export", type=Path,
                    default=ROOT / "model/runs/so400m-full-a05/export/export.json",
                    help="which space to encode text into; it must be the one "
                         "the board was running or the cosines are noise")
    ap.add_argument("--top", type=int, default=0,
                    help="print only the best N by separation, 0 for all")
    args = ap.parse_args()

    if args.log:
        cues = args.cues or Path(str(args.log) + ".cues")
        if not cues.exists():
            raise SystemExit(f"{cues}: no sidecar, so there is nothing saying "
                             f"which frames were {args.a!r} and which were "
                             f"{args.b!r}. Use --log-a/--log-b instead.")
        banks = {}
        for label in (args.a, args.b):
            wins = [(lo + args.settle, hi) for l, lo, hi in segments(cues)
                    if l == label]
            if not wins:
                raise SystemExit(f"{cues}: no segment labelled {label!r}. "
                                 f"It has: "
                                 f"{sorted({l for l, _, _ in segments(cues)})}")
            # Every visit to the scene, pooled. Vectors from three visits are a
            # better bank than three times as many from one, for the same
            # reason --repeat exists: the between-visit term is the big one.
            banks[label] = np.concatenate(
                [load_bank(args.log, lo, hi) for lo, hi in wins])
        # The baseline, if the run has one, and it is not a third scene - it is
        # what the board subtracts. cue.py does not settle it ("baseline has no
        # hand in it - nothing to settle out"), so neither does this.
        bl = [(lo, hi) for l, lo, hi in segments(cues) if l == "baseline"]
        if bl:
            bg = np.concatenate([load_bank(args.log, lo, hi) for lo, hi in bl])
        else:
            bg = None
    elif args.log_a and args.log_b:
        banks = {args.a: load_bank(args.log_a, None, None),
                 args.b: load_bank(args.log_b, None, None)}
        bg = None
    else:
        raise SystemExit("need either --log with a .cues sidecar, or "
                         "--log-a and --log-b")

    text = (sys.stdin.read() if str(args.phrases) == "-"
            else args.phrases.read_text())
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    if not lines:
        raise SystemExit("no candidates")

    export = json.loads(args.export.read_text())
    basis = (dict(np.load(spaces.CACHE / export["basis"]))
             if export.get("basis") else None)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model, tok = teacher.load_spec(export["spec"], dev)

    # One encode for every distinct phrase across every candidate, because the
    # prompt ensemble is seven forward passes and the same phrase appears as a
    # negative in half the lines.
    parts = [[p.strip() for p in l.split(NEG_SEP)] for l in lines]
    flat = sorted({p for ps in parts for p in ps})
    T = teacher.encode_queries_spec(model, tok, flat, dev,
                                    basis=basis).astype(np.float32)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    idx = {p: i for i, p in enumerate(flat)}

    A, B = banks[args.a], banks[args.b]
    print(f"\nspace     : {export['spec']} -> {A.shape[1]}-d")
    print(f"bank      : {len(A)} vectors for {args.a!r}, "
          f"{len(B)} for {args.b!r}")
    print(f"candidates: {len(lines)}\n")

    # Before any of it is believed: the board printed its own background for
    # the queries it was running, so where a candidate here is one of those,
    # the two must agree. They are the same quantity computed two ways - the
    # board from its int8-packed query against the frames as they went by, this
    # from a fresh full-precision encode against the dumps of the same frames -
    # and a disagreement means the sweep is measuring some other space, which
    # would not look like an error anywhere else.
    board_bg = {}
    if args.log:
        for ln in args.log.read_text(errors="replace").splitlines():
            if ln.startswith("background:") and "frozen" in ln:
                for nm, tilde, mu, sd in BG.findall(ln):
                    board_bg[nm.strip()] = (float(mu), float(sd))
    if board_bg and bg is not None:
        print("board check: its frozen background against this baseline bank")
        for nm, (mu, sd) in board_bg.items():
            if nm not in idx:
                continue
            negs = next((p[1:] for p in parts if p[0] == nm and len(p) > 1), None)
            if negs is None:
                continue
            v = T[idx[nm]] - T[[idx[n] for n in negs]].mean(axis=0)
            g = bg @ (v / np.linalg.norm(v))
            print(f"  {nm+'~':<24} board {mu:+.3f} +-{sd:.4f}   "
                  f"here {g.mean():+.3f} +-{g.std(ddof=1):.4f}")
        print()

    rows = []
    for line, ps in zip(lines, parts):
        pos, negs = ps[0], ps[1:]
        forms = [("bare", T[idx[pos]])]
        if negs:
            v = T[idx[pos]] - T[[idx[n] for n in negs]].mean(axis=0)
            forms.append(("contrast", v / np.linalg.norm(v)))
        for form, q in forms:
            a, b = A @ q, B @ q
            # Cohen's d on the pooled spread. The cosines themselves are tiny
            # and their scale means nothing across wordings - each sentence has
            # its own offset, which is the whole reason the board standardises -
            # so what ranks the candidates has to be a separation, not a margin.
            sp = (np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
                  if len(a) > 1 and len(b) > 1 else 0.0)
            diff = float(a.mean() - b.mean())
            d = diff / float(sp) if sp > 0 else float("nan")
            auc = float(np.mean(a[:, None] > b[None, :]))
            # The board's own arithmetic, and it is a different question from
            # the one above. m9.c's zscore() is (cos - qbg[i]) / bg_spread(i)
            # with FGX_BG_ROOM_SD: the mean and the spread both come from the
            # warm-up frames, which is this baseline segment. So these two
            # numbers are what the threshold actually sees, and a wording can
            # separate A from B perfectly - AUC 0.00 - while both of its z sit
            # under the threshold, because the background scores as high as the
            # scene does. FGX_BG_SD_FLOOR is 0.001f; keep the floor here or a
            # near-constant query divides by nothing and reports a huge z the
            # board would never print.
            if bg is not None:
                g = bg @ q
                sd = max(float(g.std(ddof=1)), 0.001)
                za = (float(a.mean()) - float(g.mean())) / sd
                zb = (float(b.mean()) - float(g.mean())) / sd
            else:
                za = zb = float("nan")
            rows.append((d, diff, auc, form, line,
                         float(a.mean()), float(b.mean()), za, zb))

    # One vector a side - the two-log case - leaves no spread to divide by, so
    # rank on the raw margin and say which was used. Mixing the two orderings in
    # one table would put numbers on different scales next to each other.
    by_d = all(r[0] == r[0] for r in rows)
    rows.sort(key=lambda r: -abs(r[0] if by_d else r[1]))
    if args.top:
        rows = rows[:args.top]
    w = max(len(r[4]) for r in rows)
    zh = f"{'zA':>8}{'zB':>8}" if bg is not None else ""
    print(f"{'candidate':<{w}}  {'form':<9}{'cos A':>9}{'cos B':>9}"
          f"{'diff':>9}{'d':>8}{'AUC':>7}{zh}")
    for d, diff, auc, form, line, ca, cb, za, zb in rows:
        ds = f"{d:>8.2f}" if d == d else f"{'-':>8}"
        zs = f"{za:>8.1f}{zb:>8.1f}" if bg is not None else ""
        print(f"{line:<{w}}  {form:<9}{ca:>9.4f}{cb:>9.4f}"
              f"{diff:>9.4f}{ds}{auc:>7.2f}{zs}")
    print(f"\nRanked by {'Cohen d' if by_d else 'raw margin'}. diff and d are "
          f"{args.a!r} minus {args.b!r}, and AUC is\nthe chance a frame of the "
          f"first outscores one of the second. Positive means\nthe wording "
          f"prefers {args.a!r}.")
    if bg is not None:
        print(f"zA and zB are what the board thresholds: each scene against "
              f"the {len(bg)} baseline\nframes, mean and spread both, as "
              f"m9.c's zscore() computes it. Read them and d as\ntwo different "
              f"questions - a wording can hit AUC 0.00 and still never fire, "
              f"if\nthe empty room scores as high as the scene does.")
    else:
        print("This ranks; it does not threshold. Take the shortlist to the "
              "board.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
