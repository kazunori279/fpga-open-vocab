"""Which embedding space a student emits into, resolved from one string.

`model/distill.py` writes the teacher's name into the checkpoint, so the pairing
travels with the weights. `tools/teacher_swap.py` writes two files beside the
cached targets under that same name - a `.json` naming the open_clip spec, and a
`.basis.npz` holding the frozen projection. All three are found from the one
string, and this module is the one place that does the finding.

**Why this is its own module rather than a helper in whichever script needed it
first.** Both spaces this project ships are 512-d: CLIP ViT-B/16's, and SigLIP 2
SO400M's squeezed 1152 -> 512 by a frozen PCA. A dot product between a vector
from one and a vector from the other therefore *succeeds*, and returns noise
that looks like a score. There is no shape to catch it and no exception to
raise. The only defence is that every consumer resolves the space the same way
from the same string, so this had to stop being three private copies.

No torch, no open_clip, no pillow - stdlib only, so `model/export.py` can call
it without acquiring the SigLIP tokenizer's dependencies (`transformers` and
`sentencepiece`, which the tools/probe_*.py scripts do need).
"""

import json
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "cache"

# The incumbent's teacher_mod.tag() predates the -quickgelu discovery recorded in
# tools/probe_teacher.py. Loading ViT-B-16 without that suffix silently uses the
# wrong activation - it produces vectors, they are just not the ones the cached
# targets were made from.
DEFAULT_SPEC = "ViT-B-16-quickgelu:openai"


def resolve(teacher: str, spec: str | None = None, basis: Path | None = None):
    """Turn a checkpoint's teacher string into (open_clip spec, basis or None).

    `basis` is None for a teacher that already emits 512-d, which is the
    incumbent and is not an error - it means the projection step is the
    identity, not that the lookup failed.
    """
    if spec is None:
        side = CACHE / f"{teacher}.json"
        spec = (json.loads(side.read_text())["teacher"] if side.exists()
                else DEFAULT_SPEC)
    if basis is None:
        side = CACHE / f"{teacher}.basis.npz"
        basis = side if side.exists() else None
    return spec, basis
