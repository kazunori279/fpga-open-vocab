# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""How often does the student's vector find its own image in the teacher's bank?

    uv run --script model/evaluate.py --split val2017 --run NAME \\
        --emit-embeddings /tmp/emb_NAME.npy
    uv run --script tools/probe_retrieval.py /tmp/emb_A.npy /tmp/emb_B.npy ...

WHY THIS AND NOT COSINE
-----------------------
The shipped M9 student scored 0.843 holdout cosine against its teacher and read
as healthy. It was not. Cosine is an average over a cone that CLIP's natural-
image embeddings already occupy: a model that emits something near the cone's
axis for every picture scores well on it while telling no two pictures apart.
These are the numbers that show that, and they are the ones a loss change aimed
at collapse has to move:

  top-1        query the 5000 teacher vectors with the student's vector for
               image i; how often does image i come back first
  median rank  where image i actually lands, over 5000
  overlap      of the teacher's 10 nearest neighbours for image i, how many are
               also in the student's 10 nearest - i.e. does the student agree
               about what the *neighbourhood* is, not just the identity
  cone         norm of the mean unit vector. The teacher's is 0.7381 (half-angle
               42.4 deg). Above that is collapse; far below it is the student
               spreading wider than the space it is supposed to be imitating,
               which is not obviously safe either - the board dot-products these
               against text vectors the *teacher* produced.

M9's student: top-1 11.4%, median rank 20 of 5000, overlap 3.09 of 10, cone
0.8657 - all while object-level AUC retention was 94%. Coarse category structure
survived and instance structure did not, and "is the book open" is an instance
question.

Those figures supersede a "top-1 18.6%, overlap 1.75" that an earlier throwaway
script produced and that this one does not reproduce at any normalization, on
either the crop or the camera embeddings. The direction of the finding is
unchanged and the throwaway script is gone, so this file is the definition now;
what matters is that every student is scored by the same code, which is the
reason it stopped being a throwaway script.

The teacher bank is model/cache/emb_val2017_ViT-B-16-openai.npy, in val2017's
canonical image order, so a student embedding file emitted by evaluate.py lines
up with it row for row and nothing here needs the images.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "model" / "cache" / "emb_val2017_ViT-B-16-openai.npy"
K = 10


def unit(v):
    v = v.astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def main():
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__.split("\n\n")[1])
        return 1
    t = unit(np.load(BANK))
    n = len(t)
    # The teacher's own K nearest, excluding self - the reference neighbourhood.
    tt = t @ t.T
    np.fill_diagonal(tt, -2.0)
    tnn = np.argpartition(-tt, K, axis=1)[:, :K]
    del tt

    print(f"bank : {n} val2017 images, teacher {BANK.name}")
    print(f"teacher cone {np.linalg.norm(t.mean(axis=0)):.4f}   "
          f"mean pairwise cos {float((t @ t.mean(axis=0)).mean()):.4f}\n")
    print(f"{'embeddings':22}{'top-1':>8}{'top-10':>8}{'med rank':>10}"
          f"{'nn overlap':>12}{'cone':>8}")
    for p in paths:
        s = unit(np.load(p))
        if len(s) != n:
            print(f"{p.name:22}  skipped: {len(s)} rows, bank has {n}")
            continue
        sim = s @ t.T
        own = sim[np.arange(n), np.arange(n)]
        # Rank of the correct image: how many bank entries beat it. Computed
        # this way rather than by argsort because a 5000x5000 sort is pointless
        # when the only thing wanted is one row's position.
        rank = (sim > own[:, None]).sum(axis=1)
        # Image i is excluded from the student's neighbour list as well as the
        # teacher's. It is a legitimate answer to "what is near student vector
        # i" and usually the top one, but the teacher's reference list cannot
        # contain it, so leaving it in would score one guaranteed miss per row
        # and make the overlap depend on top-1 rather than on the neighbourhood.
        sim_nn = sim.copy()
        np.fill_diagonal(sim_nn, -2.0)
        snn = np.argpartition(-sim_nn, K, axis=1)[:, :K]
        overlap = np.mean([len(set(a) & set(b)) for a, b in zip(snn, tnn)])
        print(f"{p.stem:22}{(rank == 0).mean():>8.3f}{(rank < 10).mean():>8.3f}"
              f"{np.median(rank) + 1:>10.0f}{overlap:>9.2f}/{K}"
              f"{np.linalg.norm(s.mean(axis=0)):>8.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
