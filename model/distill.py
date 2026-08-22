# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch"]
# ///
"""Distill the student against cached teacher embeddings.

    uv run model/distill.py --split val2017   --epochs 10 --no-augment   # smoke
    uv run model/distill.py --split train2017 --epochs 40                # real
    uv run model/distill.py --split train2017 --epochs 40 --infonce 1.0  # anti-collapse

Loss is `1 - cos(student, teacher)`: the student has to reach the same direction
in the teacher's 512-d space from a 128x128 image that it reached from 224x224.
That resolution gap is the design, not a bug.

**That loss alone turned out to be too easy to satisfy.** It contains nothing
that separates one sample from another, and CLIP's embeddings already sit in a
narrow cone, so the shipped student learned to hug the cone's axis: holdout
cosine 0.843, but only 11.4% top-1 when its vector is used to retrieve its own
image from the teacher's 5000-image bank. `--infonce` and `--rkd` add the
missing repulsion; both default to 0, so the plain command above is unchanged.
distill_loss() carries the evidence and the choice between them.

**And what survives distillation is not the same as what a phrase can reach.**
On generated cross-scene sets the student's state axis is worth AUC 0.75-0.84
when the direction is fitted on its own embeddings, but only 0.60-0.70 when a
sentence asks for it. `--text W --text-bank <npy>` spends the error budget where
sentences point, by matching the teacher's ranking of each image against a bank
of COCO captions; `tools/text_bank.py` builds the bank and distill_loss() has
the argument. It defaults to 0 as well.

**`--text` did not work, and neither did `--rkd`.** Over ten generated
contrasts, no weight of `--text` and no `--rkd` setting beats plain `1 - cos`:
the baseline is the top row at 0.596 mean cross-scene AUC and the best variant
is -0.007 +-0.017 below it. `--text` is biting the training - top1 falls
0.375 -> 0.239 monotonically in the weight - and it does not reach the eval, nor
shrink the `oracle_scene - sep` alignment gap it was built to shrink. The
earlier "RKD 10 is +0.10" came from two contrasts and is retracted; see
`docs/bring-up-log.md` for 2026-08-22 night. **Both flags stay, both default to
0, and the honest default for this student is the plain cosine loss.**

Held-out cosine is printed before training starts and after every epoch, so a
broken run is visible in the first two minutes rather than at hour three - and
alongside it now the two numbers that actually caught this, retrieval top-1 and
the cone norm.

**One honest wrinkle.** The cached teacher embedding is for the center-crop view,
so augmenting the student's input makes its target slightly wrong. Accepting that
is standard practice - flip and mild cropping act as a regularizer - and it is
what happens here. The clean fix is caching K views per image and pairing them,
which doubles the cache and is only worth it if the gate comes in marginal.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import data
import numpy as np
import student as student_mod
import teacher as teacher_mod
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

RUNS = Path(__file__).resolve().parent / "runs"

# [0,1] -> [-1,1]. Chosen so the MCU's int8 preprocessing in M5 is exactly
# "subtract 128 from the uint8 pixel" - no scale, no rounding, no lookup table.
PIXEL_MEAN = (0.5, 0.5, 0.5)
PIXEL_STD = (0.5, 0.5, 0.5)


def student_transform(train: bool, augment: bool, size: int = student_mod.INPUT_SIZE):
    norm = transforms.Normalize(PIXEL_MEAN, PIXEL_STD)
    if train and augment:
        return transforms.Compose([
            transforms.RandomResizedCrop(size, scale=(0.65, 1.0), ratio=(0.8, 1.25)),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            norm,
        ])
    # Match the teacher's framing as closely as the resolution allows: resize the
    # short side, then center crop.
    return transforms.Compose([
        transforms.Resize(size, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(size),
        transforms.ToTensor(),
        norm,
    ])


def _crop_to_4x3(img):
    """The widest 4:3 window the image allows, centered - the sensor's field."""
    w, h = img.size
    if w * 3 >= h * 4:
        nw, nh = round(h * 4 / 3), h
    else:
        nw, nh = w, round(w * 3 / 4)
    return transforms.functional.center_crop(img, [nh, nw])


def camera_transform(size: int = student_mod.INPUT_SIZE):
    """What the ArduCam Mega's 128x128 mode actually hands the student.

    Not the eval transform above, and the difference is not cosmetic. The Mega's
    128x128 mode does **not** center-crop the sensor's 4:3 field; it squashes the
    whole of it into a square. That was established by measurement, not by
    reading a datasheet - the native 128x128 frame was correlated against the
    QVGA frame of the same scene warped both ways, and the squash won 0.963 to
    0.866 on luma.

    So the student, at inference, sees every object 1.333x narrower than it was
    trained on. The training augmentation is `RandomResizedCrop(ratio=(0.8,
    1.25))`, which means 1.333 is *outside* the range the student ever saw - by
    a little, but on the wrong side of the edge. Whether that costs anything is
    an empirical question, and `evaluate.py --geometry camera` is how it gets
    answered rather than argued about.
    """
    return transforms.Compose([
        transforms.Lambda(_crop_to_4x3),
        # Resize to an explicit (h, w) pair, so this squashes rather than crops.
        transforms.Resize((size, size),
                          interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize(PIXEL_MEAN, PIXEL_STD),
    ])


class DistillSet(Dataset):
    def __init__(self, split: str, names: list[str], indices: np.ndarray,
                 targets: np.ndarray, transform):
        # The pre-shrunk copy when data.py resize has been run, the originals
        # otherwise. Same filenames either way, so the positional correspondence
        # with the teacher cache is unaffected.
        self.dir = data.student_image_dir(split)
        self.names = names
        self.indices = indices
        self.targets = targets
        self.transform = transform

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, i: int):
        idx = int(self.indices[i])
        img = Image.open(self.dir / self.names[idx]).convert("RGB")
        return self.transform(img), torch.from_numpy(self.targets[idx].astype(np.float32))


def split_indices(n: int, holdout: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic train/holdout split.

    Written to the run directory so evaluate.py scores on images the student
    never saw. Getting this wrong is the classic way to produce a passing gate
    that means nothing.
    """
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    return perm[holdout:], perm[:holdout]


def constant_baseline(targets: np.ndarray, train_idx: np.ndarray, hold_idx: np.ndarray) -> float:
    """Cosine achieved by always predicting the training-set mean embedding.

    This, not random initialization, is the floor that matters. CLIP embeddings
    of natural images occupy a narrow cone, so a model that ignores its input
    entirely and emits the mean still scores a high cosine. Reading 0.75 as
    "the student learned a lot" without this number next to it is how a
    distillation run flatters itself.
    """
    mean = targets[train_idx].astype(np.float32).mean(axis=0)
    mean /= np.linalg.norm(mean)
    held = targets[hold_idx].astype(np.float32)
    return float((held @ mean).mean())


def distill_loss(pred, target, w_infonce: float, w_rkd: float, tau: float,
                 bank=None, w_text: float = 0.0, tau_text: float = 0.05):
    """`1 - cos` plus, optionally, a term that pushes the batch apart.

    WHY THERE IS ANYTHING BESIDES `1 - cos`
    ---------------------------------------
    `1 - cos(pred, target)` has no term that separates one sample from another,
    and CLIP embeddings of natural images already sit in a narrow cone, so the
    objective is *partially satisfiable by ignoring the image*: emit something
    near the cone's axis and every target is already close. The trained student
    does exactly that. Measured on val2017: the teacher's mean-vector norm is
    0.7381 (cone half-angle 42.4 deg), the student's is 0.8657 (30.1 deg), and
    mean pairwise cosine rises 0.545 -> 0.755. Holdout cosine reads a reassuring
    0.843 while self-retrieval against the teacher bank is 11.4% top-1 of 5000,
    median rank 20 (tools/probe_retrieval.py). Close on average, poor at telling
    images apart - and "is the book open" is a telling-apart question.

    Both extra terms attack that, from different directions, and both default to
    weight 0 so an unflagged run is the M9 objective unchanged.

      infonce   Cross-entropy over the in-batch teacher bank: the student's
                vector must be nearer *its own* teacher target than to the other
                127. This is literally the retrieval metric that failed, made
                differentiable, so it is the one to try first. Symmetric because
                the reverse direction (each target claiming its own prediction)
                costs one extra transpose and stops the student from parking
                several images on one popular target.
      rkd       Match the within-batch similarity *matrix* rather than the
                identity of the pairs: smooth-L1 between pred@pred.T and
                target@target.T off the diagonal. Gentler - it asks the student
                to reproduce the teacher's spread, including how close two
                genuinely similar images should be, instead of driving every
                non-pair apart. Preferable if InfoNCE proves too aggressive on
                COCO's near-duplicates, which are false negatives to it.
      text      Match the teacher's ranking of each image against a BANK OF
                SENTENCES, not only against the other images. See below.

    tau is InfoNCE's temperature. In-batch teacher cosines span roughly 0.4-0.9,
    so 0.07 (CLIP's own value) spreads them over ~7 logits - enough to be a real
    ranking problem and not so sharp that one hard negative owns the gradient.

    WHY A TEXT TERM, WHEN THE STUDENT ALREADY REGRESSES THE TEACHER'S VECTOR
    -----------------------------------------------------------------------
    A student that matched its target exactly would match every text similarity
    for free, so the term adds nothing a *perfect* student would need. This
    student cannot be perfect - 1.4 M parameters against SO400M - so the
    question is not whether it has error but WHERE THE ERROR GOES. `1 - cos`
    treats all 512 directions as equally worth getting right, and most of them
    are directions no sentence ever points at.

    The measurement asking for it is in
    `bench/stills/20260822-synth-book-crop2/README.md`. Across generated scenes
    the student ranks the two states at AUC 0.60-0.70 when asked with a phrase,
    but at 0.75-0.84 when the direction is *fitted* on the student's own
    embeddings and held out by scene (`probe_bisect.py`'s scene-held-out
    oracle). Part of the axis is present and the sentence misses it. That
    difference is the alignment half of the gap and it is what this term aims
    at. The other half - that oracle reading 0.78 where the teacher's reads 0.96
    - is a representation gap, and no reweighting of the error budget fixes it.

    KL rather than smooth-L1 on the similarities, because what matters is the
    ORDER of the bank under an image and not the absolute cosines, which the
    modality gap keeps small and bunched. The bank rows are near-duplicates of
    each other by construction, so the softmax stays soft on purpose - this is a
    profile match, not a retrieval task, and retrieval is what `--infonce`
    already does against image targets.

    **tau_text is in units of the teacher's own spread, not in cosine.** A fixed
    cosine temperature means something different for every teacher: over the
    4096-caption bank, val2017 image-text cosines have sd 0.039 under
    ViT-B/16 and 0.064 under SO400M-pca512, and their means sit at +0.16 and
    -0.31. At a shared tau of 0.05 the same flag would leave one profile nearly
    uniform (7.95 nats of a possible 8.32) and the other with real structure
    (7.13). Dividing by the teacher's per-batch sd first makes 0.5 mean the same
    thing to both - about 5.3 nats, soft but far from flat - so a weight swept
    against one teacher transfers to the other. The student's similarities are
    divided by the TEACHER's sd, not their own: a student whose profile is
    flatter than the teacher's is wrong in a way the term should see, and
    self-normalising would hide exactly that.

    `tools/text_bank.py` builds a bank in the student's own target space out of
    COCO captions. **Never out of the eval's state phrases** - that would be
    fitting the objective to the test set, and the AUC afterwards would mean
    nothing.
    """
    loss = (1 - torch.nn.functional.cosine_similarity(pred, target, dim=-1)).mean()
    parts = {"cos": float(loss.detach())}
    if not (w_infonce or w_rkd or (w_text and bank is not None)):
        return loss, parts

    p = torch.nn.functional.normalize(pred, dim=-1)
    t = torch.nn.functional.normalize(target, dim=-1)
    if w_text and bank is not None:
        ts, ps = t @ bank.T, p @ bank.T
        scale = ts.detach().std().clamp_min(1e-6) * tau_text
        log_softmax = torch.nn.functional.log_softmax
        term = torch.nn.functional.kl_div(
            log_softmax(ps / scale, dim=-1),
            log_softmax(ts / scale, dim=-1),
            log_target=True, reduction="batchmean")
        loss = loss + w_text * term
        parts["txt"] = float(term.detach())
    if w_infonce:
        logits = p @ t.T / tau
        labels = torch.arange(len(p), device=p.device)
        ce = torch.nn.functional.cross_entropy
        term = 0.5 * (ce(logits, labels) + ce(logits.T, labels))
        loss = loss + w_infonce * term
        parts["nce"] = float(term.detach())
    if w_rkd:
        off = ~torch.eye(len(p), dtype=torch.bool, device=p.device)
        term = torch.nn.functional.smooth_l1_loss((p @ p.T)[off], (t @ t.T)[off])
        loss = loss + w_rkd * term
        parts["rkd"] = float(term.detach())
    return loss, parts


@torch.no_grad()
def holdout_metrics(model, loader, device, mean: torch.Tensor) -> tuple[float, float, float, float]:
    """Returns (raw cosine, centered cosine, retrieval top-1, cone norm).

    **Centered is the one to watch** among the two cosines. Subtracting the
    corpus mean embedding from both sides strips out the shared cone that every
    natural image sits in and leaves only the part that varies with the picture
    - which is exactly the part that decides whether "a photo of a cat" ranks
    cat images first. Raw cosine can sit at 0.75 while the model is doing
    nothing at all.

    **But centered cosine still missed the real defect**, which is why the last
    two numbers were added. `top1` queries the holdout's teacher bank with each
    student vector and asks how often the right image comes back first; `cone`
    is the norm of the mean student vector, the collapse measure directly. On
    the shipped M9 student those read 0.186 (against 5000 candidates) and 0.869
    (teacher 0.738). A loss change aimed at collapse has to move *these*, and
    watching them per-epoch is the difference between knowing that on epoch two
    and knowing it after a full retrain and a separate evaluation pass.

    top1 is over the holdout only, so it is not comparable to the 5000-way
    figure above - it is comparable across runs of this script, which is what
    it is for.
    """
    model.eval()
    preds, targs = [], []
    for pixels, target in loader:
        preds.append(model(pixels.to(device)))
        targs.append(target.to(device))
    model.train()
    pred, target = torch.cat(preds), torch.cat(targs)

    cos = torch.nn.functional.cosine_similarity
    raw = float(cos(pred, target, dim=-1).mean())
    cen = float(cos(pred - mean, target - mean, dim=-1).mean())
    p = torch.nn.functional.normalize(pred, dim=-1)
    t = torch.nn.functional.normalize(target, dim=-1)
    hit = (p @ t.T).argmax(dim=1) == torch.arange(len(p), device=p.device)
    return raw, cen, float(hit.float().mean()), float(p.mean(dim=0).norm())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val2017")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--holdout", type=int, default=1000)
    ap.add_argument("--no-augment", action="store_true",
                    help="smoke runs validate plumbing, so skip augmentation")
    # Both default to 0, so an unflagged run is the M9 objective byte for byte.
    # See distill_loss() for why either is worth turning on.
    ap.add_argument("--infonce", type=float, default=0.0, metavar="W",
                    help="weight on in-batch InfoNCE against the teacher bank "
                         "(0 = off; 1.0 is the sane first try)")
    ap.add_argument("--rkd", type=float, default=0.0, metavar="W",
                    help="weight on matching the within-batch similarity matrix "
                         "(0 = off; 10.0 puts it on the same scale as 1-cos)")
    ap.add_argument("--tau", type=float, default=0.07,
                    help="InfoNCE temperature")
    # The bank has to be built in the same space as --targets, by the same
    # teacher. tools/text_bank.py takes that teacher string and does it; the
    # dimension check below is a backstop, not the contract.
    ap.add_argument("--text", type=float, default=0.0, metavar="W",
                    help="weight on matching the teacher's image-vs-text "
                         "ranking over --text-bank (0 = off; needs the bank)")
    ap.add_argument("--text-bank", type=Path, default=None, metavar="NPY",
                    help="sentence bank in the target space, from "
                         "tools/text_bank.py")
    ap.add_argument("--tau-text", type=float, default=0.5,
                    help="text-profile temperature IN UNITS OF THE TEACHER'S "
                         "OWN SPREAD, so it means the same thing across "
                         "teachers (0.5 leaves ~5.3 of 8.3 nats)")
    # Sieving loss variants on the full split costs 54 minutes a candidate, and
    # the variants separate within the first few epochs. The holdout is carved
    # out *before* the subset, so every candidate is scored on the same 1000
    # images as a full run - only the training set shrinks.
    ap.add_argument("--subset", type=int, default=0, metavar="N",
                    help="train on N images instead of the whole split (0 = all)")
    ap.add_argument("--select", choices=("centered", "top1"), default="centered",
                    help="which holdout metric checkpoints the model")
    # Distilling from a different teacher is a change of *targets*, nothing
    # else: the student, the images and the loss are identical. So a path here
    # is the whole mechanism, and teacher_mod.load_cache() stays the default so
    # every existing command line means what it always meant. tools/
    # teacher_swap.py writes files in the expected shape.
    ap.add_argument("--targets", type=Path, default=None, metavar="NPY",
                    help="teacher embeddings to distil from, in the split's "
                         "image order (default: this split's ViT-B/16 cache)")
    ap.add_argument("--name", default=None, help="run directory name (default: the split)")
    args = ap.parse_args()

    names = data.image_list(args.split)
    if args.targets:
        targets = np.load(args.targets)
        if targets.shape[0] != len(names):
            raise SystemExit(f"{args.targets}: {targets.shape[0]} rows, but "
                             f"{args.split} has {len(names)} images. Targets are "
                             f"indexed by the split's image order.")
    else:
        targets = teacher_mod.load_cache(args.split)
    device = teacher_mod.pick_device()

    # A bank in the wrong space still multiplies, as long as the widths agree,
    # and returns a profile of noise the student would spend its capacity
    # matching. The width check catches the common mistake - a bank built
    # before the PCA - and the run's `bank` line puts the file in the log so
    # the rest is auditable after the fact.
    bank = None
    if args.text:
        if not args.text_bank:
            raise SystemExit("--text needs --text-bank; build one with "
                             "tools/text_bank.py --teacher <the run's teacher>")
        b = np.load(args.text_bank).astype(np.float32)
        if b.shape[1] != targets.shape[1]:
            raise SystemExit(f"{args.text_bank}: {b.shape[1]}-d bank against "
                             f"{targets.shape[1]}-d targets. The bank must be "
                             f"built by the same teacher, in the same space.")
        bank = torch.from_numpy(b).to(device)
        bank = torch.nn.functional.normalize(bank, dim=-1)

    train_idx, hold_idx = split_indices(len(names), args.holdout)
    if args.subset:
        train_idx = train_idx[:args.subset]
    augment = not args.no_augment

    # A --targets file built for a *subset* only has rows for the images that
    # subset touches; the rest are zero. That is fine as long as the subset used
    # to build it matches the one being trained, and catastrophic otherwise -
    # a zero target normalizes to NaN and the run reports a plausible-looking
    # loss for hours. Check it here rather than discovering it in the metrics.
    if args.targets:
        used = np.concatenate([train_idx, hold_idx])
        blank = int((np.abs(targets[used].astype(np.float32)).sum(axis=1) == 0).sum())
        if blank:
            raise SystemExit(
                f"{args.targets}: {blank} of {len(used)} rows this run needs are "
                f"all-zero. The file was built for a different --subset/--holdout; "
                f"rebuild it with the same values.")

    model = student_mod.Student().to(device)
    ok = student_mod.report(model)
    if not ok:
        print("\nRESULT : FAIL - student is over budget")
        return 1

    print()
    print(f"split    : {args.split}  ({len(train_idx)} train / {len(hold_idx)} holdout)")
    teacher_name = args.targets.stem if args.targets else teacher_mod.tag()
    print(f"teacher  : {teacher_name}   dim {targets.shape[1]}")
    print(f"device   : {device.type}")
    print(f"augment  : {'yes' if augment else 'no'}")
    print(f"epochs   : {args.epochs}   batch {args.batch}   lr {args.lr}")
    extra = ([f"infonce {args.infonce} (tau {args.tau})"] if args.infonce else []) \
        + ([f"rkd {args.rkd}"] if args.rkd else []) \
        + ([f"text {args.text} (tau {args.tau_text} sd)"] if bank is not None else [])
    print("loss     : 1-cos" + ("  +  " + "  +  ".join(extra) if extra
                                 else "   (M9 objective, no repulsion)"))
    if bank is not None:
        print(f"bank     : {args.text_bank.name}   {bank.shape[0]} sentences")
    print()

    train_loader = DataLoader(
        DistillSet(args.split, names, train_idx, targets, student_transform(True, augment)),
        batch_size=args.batch, shuffle=True, num_workers=6, drop_last=True, persistent_workers=True)
    hold_loader = DataLoader(
        DistillSet(args.split, names, hold_idx, targets, student_transform(False, False)),
        batch_size=args.batch, shuffle=False, num_workers=4, persistent_workers=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs * len(train_loader))

    # The cone centre, from training images only - centering the holdout by its
    # own mean would leak eval statistics into the metric.
    mean = torch.from_numpy(targets[train_idx].astype(np.float32).mean(axis=0)).to(device)

    constant = constant_baseline(targets, train_idx, hold_idx)
    raw0, cen0, top0, cone0 = holdout_metrics(model, hold_loader, device, mean)
    # The teacher's own cone norm, the number the student's should be moving
    # toward rather than past. Printed here so the per-epoch column has a target.
    tvec = targets[train_idx].astype(np.float32)
    tcone = float(np.linalg.norm(
        (tvec / np.linalg.norm(tvec, axis=1, keepdims=True)).mean(axis=0)))
    print(f"constant : raw {constant:+.4f}   centered +0.0000  (always predict the mean)")
    print(f"teacher  : cone {tcone:.4f}  (the student's cone should approach this, "
          f"not exceed it)")
    print(f"epoch  0 : raw {raw0:+.4f}   centered {cen0:+.4f}   "
          f"top1 {top0:.3f}   cone {cone0:.4f}  (untrained)")

    run = RUNS / (args.name or args.split)
    run.mkdir(parents=True, exist_ok=True)
    best, best_raw, best_top, best_cone = cen0, raw0, top0, cone0
    # The peak of each metric over the whole run, regardless of which one
    # selects the checkpoint. Without this a candidate whose top-1 peaks on a
    # different epoch than its centered cosine reports someone else's number,
    # and the sieve is comparing checkpoints rather than objectives.
    peak_cen, peak_top = cen0, top0

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        running, seen = 0.0, 0
        for step, (pixels, target) in enumerate(train_loader):
            pixels, target = pixels.to(device), target.to(device)
            pred = model(pixels)
            loss, parts = distill_loss(pred, target, args.infonce, args.rkd,
                                       args.tau, bank, args.text, args.tau_text)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            running += float(loss.detach()) * len(pixels)
            seen += len(pixels)
            # A heartbeat with a rate on it. Over 900 steps an epoch, the
            # difference between "slow" and "wedged" is otherwise invisible
            # until the epoch ends - and stdout to a file is block-buffered,
            # so without the flush nothing appears for minutes either way.
            if step % 20 == 0:
                # The per-term breakdown, not just the total: with two terms
                # summed, a total that stops falling could be either one, and
                # a weight that is far too small is otherwise invisible.
                bits = "  ".join(f"{k} {v:.4f}" for k, v in parts.items())
                print(f"\r  epoch {epoch:2d} : step {step}/{len(train_loader)}  "
                      f"loss {running / max(seen, 1):.4f}  [{bits}]  "
                      f"{seen / max(time.time() - t0, 1e-6):.0f} img/s",
                      end="", flush=True)
        print("\r" + " " * 90, end="\r")
        raw, cen, top, cone = holdout_metrics(model, hold_loader, device, mean)
        peak_cen, peak_top = max(peak_cen, cen), max(peak_top, top)
        flag = ""
        # Checkpoint on the centered number: it is the one that tracks whether
        # the student is separating images rather than finding the cone.
        now, prev = ((top, best_top) if args.select == "top1" else (cen, best))
        if now > prev:
            best, best_raw, best_top, best_cone = cen, raw, top, cone
            flag = "  *"
            torch.save({
                "state_dict": model.state_dict(),
                "stages": student_mod.STAGES,
                "embed_dim": student_mod.EMBED_DIM,
                "input_size": student_mod.INPUT_SIZE,
                "pixel_mean": PIXEL_MEAN,
                "pixel_std": PIXEL_STD,
                # Which teacher this student imitates decides which text vectors
                # its outputs may be compared against, so it travels with the
                # weights rather than only in the run directory.
                "teacher": teacher_name,
                "split": args.split,
                "holdout": hold_idx.tolist(),
                "epoch": epoch,
                "holdout_cosine": raw,
                "holdout_centered": cen,
                "holdout_top1": top,
                "cone_norm": cone,
                "target_mean": mean.cpu().numpy(),
            }, run / "student.pt")
        print(f"epoch {epoch:2d} : train loss {running / seen:.4f}   "
              f"raw {raw:+.4f}   centered {cen:+.4f}   top1 {top:.3f}   "
              f"cone {cone:.4f}   {time.time() - t0:.0f}s{flag}",
              flush=True)

    (run / "config.json").write_text(json.dumps(vars(args) | {
        "constant_cosine": constant,
        "baseline_cosine": raw0,
        "best_cosine": best_raw,
        "best_centered": best,
        "best_top1": best_top,
        "peak_centered": peak_cen,
        "peak_top1": peak_top,
        "cone_norm": best_cone,
        "teacher_cone_norm": tcone,
        "train_images": len(train_idx),
        "teacher": teacher_name,
        # vars(args) carries a PosixPath, which json refuses.
        "targets": str(args.targets) if args.targets else None,
        "text_bank": str(args.text_bank) if args.text_bank else None,
        "text_bank_n": int(bank.shape[0]) if bank is not None else 0,
    }, indent=2))

    print()
    print(f"raw cosine      : {raw0:+.4f} untrained -> {best_raw:+.4f} trained, "
          f"against {constant:+.4f} for the constant predictor")
    print(f"centered cosine : {cen0:+.4f} untrained -> {best:+.4f} trained, "
          f"against  0.0000 for the constant predictor")
    print(f"holdout top-1   : {top0:.3f} untrained -> {best_top:.3f} trained "
          f"(peak {peak_top:.3f}), against {1 / len(hold_idx):.3f} for chance "
          f"over {len(hold_idx)}")
    print(f"cone norm       : {cone0:.4f} untrained -> {best_cone:.4f} trained, "
          f"against {tcone:.4f} for the teacher "
          f"({'collapsed tighter than the teacher' if best_cone > tcone + 0.01 else 'no worse than the teacher'})")
    print(f"wrote           : {run / 'student.pt'}")

    # Beating a random init proves nothing, and beating the constant predictor
    # on raw cosine barely proves more. Centered cosine is zero by construction
    # for a model that ignores its input, so a clearly positive value is the
    # honest evidence that the student is looking at the picture. Whether it is
    # *good enough* is evaluate.py's call, on the query set rather than cosine.
    ok = best > 0.05
    print("\nRESULT : " + ("PASS - the student is using the image, not just the prior"
                           if ok else "FAIL - no better than predicting the mean embedding"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
