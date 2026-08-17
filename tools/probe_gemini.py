# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "google-genai"]
# ///
"""Does gemini-embedding-2 bind the adjective that CLIP cannot?

    uv run --script tools/probe_gemini.py [--snaps DIR] [--dim 512]

This is tools/probe_open.py's question asked of a different teacher, and it is
deliberately the *same* question: same four board PNGs, same eight prompts, same
three difference axes. Anything else and the two runs would not be comparable,
which is the only reason to run this one.

WHY IT EXISTS
-------------
M12 established that the failure is not the board. Fed the identical 128x128
frames, CLIP ViT-B/16 scores "a closed book" *higher* on the OPEN image (0.2898)
than on the CLOSED one (0.2743), and the OPEN image wins seven of the eight
prompts - including "the front cover of a closed book". That is the documented
bag-of-words failure (ARO, Winoground, SugarCrepe): CLIP knows the nouns are
present and does not bind the adjective to them.

Replacing the teacher only helps if the replacement does not share the flaw, and
no datasheet answers that. This does, for about 1100 tokens.

WHAT WOULD COUNT AS A PASS
--------------------------
Not "the numbers look different". Three things, in order:

  1. CONTROL. The empty-desk frame must rank the empty-desk prompt above the
     book prompts. If it does not, the image and text vectors are not usefully
     in one space and every other number here is noise - the run says nothing
     about the model, only about how it was called.
  2. RANKING. "a closed book" scores CLOSED above OPEN, and "an opened book"
     scores OPEN above CLOSED. This is the exact comparison CLIP gets backwards.
  3. AXIS. Projected onto normalize(e_open - e_closed), OPEN lands above CLOSED
     by more than the two book-free frames span. CLIP's student was
     anti-correlated at -4.67 sd here.

A pass on 2 and 3 does not yet mean the distillation works - the student's own
failure (self-retrieval top-1 11.4%, neighbour overlap 3.09/10) is a separate
defect that a better teacher does not touch. It means the ceiling moved.

CALLING CONVENTION, AND WHY BOTH VARIANTS ARE TRIED
---------------------------------------------------
gemini-embedding-2 has no task_type field; the docs say to put the task in the
prompt ("task: search result | query: ..."). Those examples are all text-to-text
retrieval and none of them covers image-to-text, so which form to use for a
camera frame against a caption is genuinely undocumented. Guessing wrong would
look exactly like the model failing, so both forms are measured and printed.

Output is requested at --dim 512 rather than the default 3072: that is the
student's existing head width, and at non-default dimensions the API returns
vectors already L2-normalized.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

MODEL = "gemini-embedding-2"

# Same four frames as tools/probe_open.py, same labels, same order.
IMAGES = [
    ("OPEN  ", "m9-54-f0550-hi.png"),
    ("CLOSED", "m9-56-f0570-hi.png"),
    ("close ", "m9-57-f0580-hi.png"),
    ("empty ", "m9-58-f0590-hi.png"),
]
PROMPTS = [
    "a book",
    "an opened book",
    "a closed book",
    "an open book",
    "a page of printed text",
    "a book cover",
    "two pages of an open book",
    "the front cover of a closed book",
]
# The control (question 1 above). Not in probe_open.py's list because CLIP was
# never in doubt about nouns - it is here to catch a broken call, not the model.
CONTROL = "an empty wooden desk"

AXES = [
    ("opened-closed", "an opened book", "a closed book"),
    ("pages-cover", "a page of printed text", "a book cover"),
    ("spread-front", "two pages of an open book", "the front cover of a closed book"),
]


def embed(client, parts, dim):
    """One embed_content call per part. Batched calls would share a context
    window and the docs describe interleaved parts as a single fused input, so
    sending four frames together would return one vector for the collage rather
    than four for the frames."""
    out = []
    for p in parts:
        r = client.models.embed_content(
            model=MODEL, contents=[types.Content(parts=[p])],
            config=types.EmbedContentConfig(output_dimensionality=dim))
        out.append(np.array(r.embeddings[0].values, dtype=np.float32))
    v = np.stack(out)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def table(title, cos, labels, prompts):
    print(f"\n=== {title} ===")
    print(f"{'':10}" + "".join(f"{p[:17]:>19}" for p in prompts))
    for i, lab in enumerate(labels):
        print(f"{lab:10}" + "".join(f"{cos[i, j]:>19.4f}" for j in range(len(prompts))))


def axes(title, img, qv, prompts):
    print(f"\n--- {title}: projection onto normalize(pos - neg) ---")
    idx = {p: i for i, p in enumerate(prompts)}
    for name, pos, neg in AXES:
        d = qv[idx[pos]] - qv[idx[neg]]
        d = d / np.linalg.norm(d)
        vals = img @ d
        cells = "  ".join(f"{lab.strip()} {v:+.4f}" for (lab, _), v in zip(IMAGES, vals, strict=False))
        # The two book-free frames are the only noise estimate available with
        # four images, so the gap is reported against their span rather than
        # against a standard deviation that four points cannot support.
        span = abs(vals[2] - vals[3])
        gap = vals[0] - vals[1]
        verdict = "OPEN > CLOSED" if gap > 0 else "INVERTED"
        print(f"  {name:16}{cells}   gap {gap:+.4f} vs non-book span {span:.4f}  {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"))
    ap.add_argument("--dim", type=int, default=512)
    ap.add_argument("--project", default=None)
    ap.add_argument("--location", default="us")
    args = ap.parse_args()

    client = genai.Client(vertexai=True, project=args.project, location=args.location)

    missing = [n for _, n in IMAGES if not (args.snaps / n).exists()]
    if missing:
        print(f"missing frames in {args.snaps}: {', '.join(missing)}")
        return 1

    print(f"model    : {MODEL}, output_dimensionality={args.dim}")
    print(f"frames   : {args.snaps}")

    img = embed(client, [types.Part.from_bytes(
        data=(args.snaps / n).read_bytes(), mime_type="image/png")
        for _, n in IMAGES], args.dim)
    print(f"images   : {img.shape}, norms {np.linalg.norm(img, axis=1).round(4)}")

    labels = [lab for lab, _ in IMAGES]
    allp = PROMPTS + [CONTROL]

    for variant, fmt in [("bare text", "{}"),
                         ("task-instruction", "task: search result | query: {}")]:
        qv = embed(client, [types.Part.from_text(text=fmt.format(p))
                            for p in allp], args.dim)
        cos = img @ qv.T
        table(f"{variant}: image-to-text cosine", cos, labels, allp)

        # Question 1. Printed before the rest because a failure here voids it.
        ci = len(allp) - 1
        empty_best = int(np.argmax(cos[3]))
        print(f"\n  CONTROL: for the empty frame the top prompt is "
              f"'{allp[empty_best]}' ({cos[3, empty_best]:+.4f}); "
              f"'{CONTROL}' scores {cos[3, ci]:+.4f}"
              + ("  -> shared space looks usable"
                 if empty_best == ci else "  -> SUSPECT, read nothing else here"))

        # Question 2, the comparison CLIP gets backwards.
        for p in ("an opened book", "a closed book"):
            j = allp.index(p)
            want = "OPEN" if "opened" in p else "CLOSED"
            got = "OPEN" if cos[0, j] > cos[1, j] else "CLOSED"
            print(f"  RANKING: '{p}' picks {got} "
                  f"(OPEN {cos[0, j]:+.4f} vs CLOSED {cos[1, j]:+.4f}), want {want}"
                  + ("  ok" if got == want else "  WRONG"))

        wins = int((cos[0, :len(PROMPTS)] > cos[1, :len(PROMPTS)]).sum())
        print(f"  OPEN beats CLOSED on {wins} of {len(PROMPTS)} prompts "
              f"(CLIP: 7 of 8 - a bag-of-words model wins nearly all of them)")

        axes(variant, img, qv[:len(PROMPTS)], PROMPTS)

    return 0


if __name__ == "__main__":
    sys.exit(main())
