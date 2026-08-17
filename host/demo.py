# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "pillow", "open_clip_torch", "pyserial",
#                 "transformers", "sentencepiece"]
# ///
"""fpga-open-vocab: describe it, the board spots it.

    uv run host/demo.py --bitstream rtl/bitstreams/m16/gemm_top_wide.hex \\
                        "cup" "person" "book" "laptop"
    uv run host/demo.py "an opened book / a closed book / a book"

host/m8.py plus a second framed message. The bitstream goes over first, exactly
as before; then, once the board says it is ready, a set of up to six text
queries - each one a 512-d teacher vector and a match threshold - and the board
scores every frame against all of them.

**Why this script needs torch.** The whole demo rests on the two vectors living
in the same space: the student was distilled into a teacher's image embedding,
so that teacher's *text* embedding is directly comparable to what the tile
emits. There is no way to produce that text vector without the teacher, so
unlike the other host scripts this one loads a large model. It is also why the
device holds vectors rather than words - the RP2350 has no text encoder and is
not going to grow one.

**WHICH teacher is not a choice this file makes, and that is the point** (M18).
Two spaces ship. The incumbent is CLIP ViT-B/16. The current student is
distilled from SigLIP 2 SO400M squeezed 1152 -> 512 by a frozen PCA, because
that teacher is the one that passes gate 2 - it can tell an opened book from a
closed one, and ViT-B/16 cannot (tools/probe_teacher.py, tools/probe_inherit.py).

**Both spaces are 512-d.** A ViT-B/16 text vector dotted against an SO400M-space
image vector computes fine and returns noise: no exception, no NaN, no shape
error, just plausible scores that mean nothing. So this script does not pick a
teacher. `model/export.py` writes `export.json` beside the weights naming the
space that blob emits into, and `--export` points here. The encoder is derived
from it.

That covers the file on disk. It does not cover the flash, so the board is asked
too: m9 prints a crc32 over its own `fgx_weights[]` and this script refuses to
send a query set unless it matches `export.json`. A board still running last
week's student is the failure this catches, and it is the one the sidecar
cannot.

**The prompt ensemble is not a detail.** `teacher.encode_queries_spec()`
averages seven templates ("a photo of a {}.", "a close-up photo of a {}.", ...)
and `model/teacher.py:45` says demo.py must use that same list. It is imported
rather than copied, because the device stores the averaged vector: a different
ensemble here is a different query, silently, and it would show up only as
slightly worse answers. Under a projected teacher the averaging happens in the
teacher's own 1152-d space and the PCA is applied to the result - the order
tools/probe_retention.py measured, and not interchangeable with the other one.

**Thresholds come from data, and the file admits where it doesn't have any.**
`model/evaluate.py --emit-thresholds` places one per COCO class at a chosen
false-positive rate on the negatives, and records the AUC beside it. Free text
is still the point of an open-vocabulary demo, so a query with no entry is not
refused - it is measured instead, against the int8 student's eval embeddings
from `--emit-embeddings`, and this script says which of the two it used. Only
with neither does it fall back to the median of every other query, which is a
guess wearing three decimal places.

**A query can say what it is NOT** (M12): `"an opened book / a closed book /
a book"` is sent as `normalize(e_pos - mean(e_neg))`, one vector like any other,
so the board is untouched. Give two or three negatives - naming only the
opposite state measured *worse* than naming none. See NEG_SEP below.

**The background baseline can be frozen** (M12): `--bg-tau` and
`--bg-hold`/`--no-bg-hold` ride on the query header. The default warms up for 30
frames and then stops moving, so a book left in shot keeps scoring instead of
fading into the baseline it is supposed to stand out from.

**The device ranks a standardised score, not the cosine.** Each record carries
the mean and spread of that query's negatives, and the board compares
`(cos - mean) / std`. A CLIP cosine's per-query offset is a property of the
wording - it spans 0.06 across the scored COCO classes - and on a live bench
that is several times the difference the object itself makes.

**Both terms of that now come from the room** (M19): `--room-sd`, on by default,
divides by the spread measured over the same warm-up rather than COCO's. M12
learned the mean here and left the spread fixed on the argument that only the
centre was wrong. Measured, the room was 9x tighter, so every z was 9x too small
and a threshold meant for the 90th percentile of the background sat far out in
its tail - `an open hand` separated its scene at AUC 0.861 and fired on 6 frames
of 90. `--coco-sd` restores M12 and is the right choice for a camera that moves
between scenes. `--smooth`, also on by default, EMAs the z on the board before
thresholding; use `--no-smooth` when measuring.

    "FGXB" | len u32 LE | crc32 u32 LE | len bytes                  (bitstream)
    "FGXQ" | len u32 LE | crc32 u32 LE | header | records           (queries)
    header = u32 nq | u32 dim | u32 bg_tau | u32 bg_flags
    bg_flags = 0x1 hold | 0x2 room spread | 0x4 smooth z
    record = char name[24] | f32 z_threshold | f32 mean | f32 std | f32 vec[dim]

Same framing, same CRC, and on the device the same ft_recv_exact() - one shape
to get right rather than two.

`--ask` re-sends a set to a *running* board, which is what makes this
demonstrable rather than a screenshot: type a new comma-separated list at any
time and the winner changes without a reflash.

**A wedge is followed rather than reported as silence** (exit 3). m9's watchdog
reboots a stuck board and the next boot names the stage it stuck in; that reboot
re-enumerates the CDC device, so this end now goes and finds the board again
instead of sitting out its `--idle` against a port that no longer exists. The
run is void either way - the reboot forgets the frozen background - but it ends
with the one sentence that says why. Exit 2 is the other half of that fork and
is now a positive result too: the port was still there, so the board is stuck
somewhere that is feeding the watchdog. See REOPEN_S.
"""

import argparse
import json
import queue
import re
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contextlib

from board import RP2350_VID, find_port, pick_port, recover
from board import ports as bus_ports

MAGIC_B = b"FGXB"
MAGIC_Q = b"FGXQ"

# How much to hand the serial port before draining the other direction, and it
# is small for a reason that cost a 300-frame endurance run.
#
# THE DEADLOCK. At 4096 this wedged the board solid, but only on --ask, and the
# exception is the whole explanation. A bitstream or a first query set arrives
# while the board is sitting in a blocking read printing nothing, so a big write
# streams straight in. A *re-query* arrives mid-loop, and then both ends are
# talking at once: pyserial blocks until the device accepts all 4096, the RP2350
# CDC FIFO is 64 bytes and the firmware only drains it between frames, so the
# host stops reading for the ~900 ms of a frame - and in that window the board
# fills its own TX buffer with the frame line and blocks in printf. Neither side
# can move, and neither side times out. Frame 22 of /tmp/m9_endur.log is where
# it stopped, with no error on either end.
#
# 512 bytes is under one frame's worth of console output in the other direction,
# so the drain below always gets there before the board's TX buffer fills. The
# host can still block for one frame's time on a single write; that is fine, and
# it is not a deadlock, because the board is not blocked while it happens.
CHUNK = 512

# THE HANG REPORT WAS ALWAYS PRINTED TO A PORT THIS END NO LONGER HELD.
#
# m9's watchdog (firmware/m9.c:661) reboots a wedged board after 8 s, and the
# next boot prints one line naming the stage and the frame it stopped in. That
# is the whole of M20b, and it worked; none of the wedges since has produced
# the line here, and the reason is this end rather than that one. The reboot
# re-enumerates the CDC device, macOS tears the old /dev/cu.usbmodem* down and
# builds the next one out of a fresh location id, and the read then fails on a
# descriptor that names nothing. pump() used to return "" for that - the same
# answer it gives for a board with nothing to say - so the run sat out its whole
# --idle against a port that no longer existed, called it a stall, and exited.
# The board printed its one useful sentence to nobody.
#
# Nothing on the device needs changing for this: m9.c:1466 waits on
# stdio_usb_connected() *before* wd_report_last(), with the watchdog disarmed,
# so a rebooted board holds the line until a host raises DTR. There is no race
# here to lose - only a reader that has to still be attached.
#
# So both waits are generous. 8 s of watchdog plus enumeration is the thing
# being waited for, and the cost of overshooting is seconds on a run that is
# already void.
REOPEN_S = 45.0
HANG_REPORT_S = 30.0


class BoardGone(Exception):
    """The board left the bus and did not come back inside REOPEN_S.

    Raised by follow_reboot() once it has printed why. Nothing below it can do
    anything over a port that no longer exists, so this ends the run there
    rather than letting the next write turn a diagnosed failure into a
    traceback."""

# How long the board may say nothing before this end stops waiting to be told
# the port died and goes and looks.
#
# The reboot is *usually* delivered as an exception - pyserial raises on a read
# from a descriptor whose device has gone - but "usually" is not a word to hang
# eight unexplained wedges on. macOS can keep the /dev node alive across the
# re-enumeration for long enough that the read simply times out instead, and
# then a fix that waits for an exception waits for one that never comes.
#
# 10 s against m9's 8 s watchdog and a ~304 ms frame: a working board never goes
# a tenth of this quiet, and a wedged one has already rebooted by the time this
# fires. The check itself is a USB enumeration walk, not a read, so it costs
# nothing and cannot be lied to by a stale descriptor.
QUIET_PROBE_S = 10.0

# Both mirror firmware/m9.c. A mismatch is caught on the device - it checks the
# length against nq and dim before accepting anything - but it is caught much
# more legibly here.
MAX_Q = 6
NAME_LEN = 24

# The background policy, mirroring FGX_BG_TAU_DEFAULT / FGX_BG_HOLD_DEFAULT.
# Under hold, bg_tau is a warm-up length and not an averaging window, which is
# why 30 (~27 s at 0.9 s/frame) replaced M9's 200: three minutes of standing
# still is long enough that a book set down mid-warm-up gets absorbed into the
# baseline it was supposed to stand out from. `--bg-tau 200 --no-bg-hold` is
# M9's exact behaviour, kept reachable by one flag.
BG_TAU = 30
BG_MAX_TAU = 100000

# M19. The fourth header word was 0/1 for bg_hold and is now a flag set, chosen
# so that the old encoding is a valid new one: hold=1 is BG_HOLD alone. See
# m9.c's FGX_BG_* block for what the two new bits buy and what they cost.
BG_HOLD = 0x1
BG_ROOM_SD = 0x2
BG_SMOOTH = 0x4

# Below this the threshold is a number with nothing behind it: the eval says the
# int8 student cannot separate this class from the rest of COCO at all, so
# whatever the board prints for it is noise with a decimal point.
WEAK_AUC = 0.75

# M12's contrast queries. "an opened book / a closed book / a book" asks for the
# first thing AS AGAINST the others, and is sent as normalize(e_pos - mean(e_neg))
# - one 512-d vector, so the board is unchanged and the wire is unchanged.
#
# WHY THIS EXISTS. The M11 bench could not tell an opened book from a closed one;
# it ranked them backwards. tools/probe_open.py showed the teacher separates the
# two images and the student does not, so the plain prompt has nothing left to
# work with by the time it reaches the board. Subtracting a negative cancels the
# "book" component that dominates both and leaves the part the question is about.
#
# WHY THE HELP TEXT SHOWS THREE TERMS AND NOT TWO, which is the counter-intuitive
# part. tools/probe_negatives.py measured five strategies on COCO. Naming just
# the opposite state - the obvious thing to type - was the WORST of them, worse
# than supplying no negative at all (student mean 0.609 against 0.610), and on
# "a glass of water" it inverted the ranking outright. Averaging several
# negatives was the only choice that helped everywhere: 0.646 against 0.610.
#
# AND WHY "nothing" IS NOT APPENDED AUTOMATICALLY, tempting as it is. An empty-
# scene negative helps some queries a lot and hurts others as much - on the
# student, book +0.111 and glass +0.061 against pouring -0.117 and posture
# -0.124. There is no safe default, so the user gets the knob and the docs get
# the numbers.
# m9.c's FGX_Q_*. What a query is for, which the M20 sweep found is two
# different things wearing the same shape - see that file's FGX_Q_PLAIN block.
Q_PLAIN, Q_GATE, Q_CLASS = 0, 1, 2
ROLE_NAME = {Q_PLAIN: "plain", Q_GATE: "presence", Q_CLASS: "state"}

NEG_SEP = "/"

# What the device shows for a contrast query, appended to the positive so a
# frame line says which mode produced it. One character, because qname is 24
# bytes including the terminator and the positive needs the room.
CONTRAST_MARK = "~"


# RP2350_VID and pick_port() moved to host/board.py, so that host/cue.py and
# ab.sh can ask the same question without importing torch to do it. Their old
# glob for /dev/cu.usbmodem* could not fail on this desk; see that file.


def load_image(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    if path.suffix.lower() != ".hex":
        return raw, "raw binary"
    compact = re.sub(r"\s+", "", raw.decode("ascii"))
    if len(compact) % 2:
        raise SystemExit(f"{path}: odd number of hex digits")
    return bytes.fromhex(compact), "Efinity ASCII hex"


def parse_spec(spec: str) -> tuple[str, list[str]]:
    """"an opened book / a closed book" -> ("an opened book", ["a closed book"]).

    A spec with no separator is a plain query and comes back with no negatives,
    which is the path every pre-M12 invocation takes.
    """
    parts = [p.strip() for p in spec.split(NEG_SEP)]
    parts = [p for p in parts if p]
    if not parts:
        raise SystemExit(f"{spec!r}: nothing but separators")
    return parts[0], parts[1:]


def load_export(path: Path) -> dict:
    """export.json, and the basis file it names, resolved to a usable pair.

    The sidecar is the only thing that says which of the two 512-d spaces a blob
    emits into, so a missing one is fatal rather than a default: the fallback
    would be a guess, and a wrong guess here produces scores instead of errors.
    Re-export the run to get one.
    """
    side = path / "export.json"
    if not side.exists():
        raise SystemExit(
            f"{side}: not found. The query space cannot be guessed - both "
            f"shipped teachers are 512-d, so the wrong one would score instead "
            f"of failing. Re-export:\n  uv run model/export.py --run <RUN> "
            f"--wbits 4 --wsearch --ends8")
    blob = json.loads(side.read_text())
    if blob.get("basis"):
        import spaces
        b = spaces.CACHE / blob["basis"]
        if not b.exists():
            raise SystemExit(
                f"{b}: not found, and {blob['run']} emits into the projected "
                f"space it defines. Rebuild it with tools/teacher_swap.py")
        blob["basis_path"] = b
    else:
        blob["basis_path"] = None
    return blob


def check_run(what: str, path: Path, meta: dict, export: dict) -> None:
    """Refuse a calibration file that was measured in a different space.

    Cosines, means and spreads are all space-specific, and none of them look
    wrong when they come from the other one - a threshold from the ViT-B/16
    student applied to SO400M-space scores is just a cutoff in the wrong place.
    The filenames carry the run for the same reason, but a filename is a
    convention and this is a check.
    """
    got = meta.get("run")
    if got is not None and got != export["run"]:
        raise SystemExit(
            f"{what} {path} was made for run {got!r}, and the export is "
            f"{export['run']!r}. Those are different embedding spaces, so its "
            f"numbers would be silently wrong. Regenerate:\n"
            f"  uv run model/evaluate.py --run {export['run']} "
            f"--geometry camera --emit-thresholds ... --emit-embeddings ...")


class Encoder:
    """The teacher, loaded once and kept for --ask.

    Which teacher comes from export.json - see the module docstring. Loading is
    deliberately lazy: --bootsel should not spend forty seconds pulling a
    400M-parameter model off disk just to reboot a board.
    """

    def __init__(self, export: dict, thresholds: Path, override: float | None,
                 embeddings: Path | None = None):
        self.model = None
        self.tokenizer = None
        self.device = None
        self.export = export
        self.spec = export["spec"]
        self.basis = None
        if export["basis_path"] is not None:
            import numpy as np
            self.basis = dict(np.load(export["basis_path"]))
        self.override = override
        self.table, self.median, self.meta = self._load(thresholds)
        check_run("thresholds", thresholds, self.meta, export)
        self.emb_path = embeddings
        self.emb = None            # loaded on first use, and only if needed
        self.emb_meta: dict = {}

    @staticmethod
    def _load(path: Path) -> tuple[dict, dict, dict]:
        if not path.exists():
            print(f"thresholds: {path} not found - every query will use "
                  f"--threshold or the built-in 0.26. Generate it with:\n"
                  f"            uv run model/evaluate.py --split val2017 --run "
                  f"<RUN> --geometry camera \\\n"
                  f"                --emit-thresholds {path}", file=sys.stderr)
            return {}, {"z_threshold": 1.28, "mean_neg": 0.26,
                        "std_neg": 0.02, "threshold": 0.26}, {}
        blob = json.loads(path.read_text())
        table = blob.get("queries", {})
        # The fallback for a query the eval never scored. Medians of the three
        # numbers separately rather than a median row, because there is no
        # reason the same query should be typical in all three and picking one
        # to be representative would be a choice with nothing behind it.
        def mid(key: str, default: float) -> float:
            vals = sorted(q[key] for q in table.values() if key in q)
            return vals[len(vals) // 2] if vals else default
        median = {"z_threshold": mid("z_threshold", 1.28),
                  "mean_neg": mid("mean_neg", 0.26),
                  "std_neg": mid("std_neg", 0.02),
                  "threshold": mid("threshold", 0.26)}
        return table, median, blob

    def _clip(self):
        if self.model is None:
            import teacher
            self.device = teacher.pick_device()
            print(f"teacher   : {self.spec}, {len(teacher.TEMPLATES)} templates,"
                  f" on {self.device}", file=sys.stderr)
            print("space     : "
                  + (f"projected by {self.export['basis']}"
                     if self.basis is not None
                     else "the teacher's own, no projection")
                  + f" -> {self.export['embed_dim']}-d", file=sys.stderr)
            self.model, self.tokenizer = teacher.load_spec(self.spec,
                                                           self.device)
        return self.model

    def _bank(self):
        """The int8 student's eval embeddings, or None if they were never made.

        model/evaluate.py --emit-embeddings writes these. They are what lets a
        query with no table row be calibrated properly instead of borrowing the
        median of everyone else's numbers - see NEG_SEP's note and evaluate.py's.
        """
        if self.emb is None and self.emb_path is not None:
            import numpy as np
            if not self.emb_path.exists():
                self.emb = False
            else:
                meta = self.emb_path.with_suffix(".json")
                if meta.exists():
                    self.emb_meta = json.loads(meta.read_text())
                check_run("embeddings", self.emb_path, self.emb_meta,
                          self.export)
                self.emb = np.load(self.emb_path).astype("float32")
                print(f"embeddings: {self.emb_path}, {self.emb.shape[0]} x "
                      f"{self.emb.shape[1]} on "
                      f"{self.emb_meta.get('geometry', '?')} geometry - free-text "
                      f"queries get measured calibration, not the median",
                      file=sys.stderr)
        return None if self.emb is False else self.emb

    def _measure(self, vec):
        """(mean, std) of this vector's cosine over the eval images, or None.

        The population is every eval image rather than a query's negatives,
        because a contrast axis has no membership to define negatives with. For
        a COCO class the two nearly coincide; the table is still preferred
        wherever it has a row.
        """
        bank = self._bank()
        if bank is None:
            return None
        s = bank @ vec
        return float(s.mean()), float(s.std())

    def encode(self, specs: list[str], gate_specs: list[str] | None = None):
        """(display names, vectors, calibration, roles), provenance each.

        A spec is either a plain query, which behaves exactly as it did before
        M12, or a contrast query - positive, then negatives, separated by
        NEG_SEP. The contrast case sends normalize(e_pos - mean(e_neg)); the
        board is unchanged either way, since both are one unit 512-d vector.

        The calibration is (z_threshold, mean_neg, std_neg) per query. The
        device ranks `(cos - mean_neg) / std_neg`, not the cosine: see
        evaluate.py's docstring for the bench run that made that necessary.

        M20's roles say what each query is FOR, and the assignment is not a
        preference - it is what the sweep measured. `gate_specs` are presence
        prompts, which carry all of the z against an empty room and none of the
        discrimination; contrast queries are the reverse, so once a gate exists
        they become the state queries that get ranked against each other. A
        bare query that is not a gate stays PLAIN and keeps its own threshold,
        which is the pre-M20 rule and the whole of the COCO object demos.
        """
        import numpy as np
        import teacher

        model = self._clip()
        gate_specs = list(gate_specs or [])
        specs = list(specs) + list(gate_specs)
        n_plain = len(specs) - len(gate_specs)

        # One encode_queries() call for every prompt in every spec, rather than
        # one per spec. The prompt ensemble is seven forward passes per prompt
        # and this runs while the board is waiting, so the batching is not
        # premature - a three-term contrast query would otherwise triple it.
        parsed = [parse_spec(s) for s in specs]
        flat = [p for pos, negs in parsed for p in (pos, *negs)]
        # basis=None is the identity map, so the incumbent ViT-B/16 export takes
        # exactly the path it always did - one formula, not two branches.
        enc = teacher.encode_queries_spec(model, self.tokenizer, flat,
                                          self.device,
                                          basis=self.basis).astype(np.float32)

        names, rows, cal, weak, synth, roles = [], [], [], [], [], []
        at = 0
        for k, (pos, negs) in enumerate(parsed):
            roles.append(Q_GATE if k >= n_plain else
                         Q_CLASS if (negs and gate_specs) else Q_PLAIN)
            e_pos = enc[at]
            e_neg = enc[at + 1:at + 1 + len(negs)]
            at += 1 + len(negs)

            if negs:
                v = e_pos - e_neg.mean(axis=0)
                v = v / np.linalg.norm(v)
                name = pos[:NAME_LEN - 2] + CONTRAST_MARK
            else:
                v, name = e_pos, pos
            rows.append(v)
            names.append(name)

            # The table is keyed on the plain prompt and a difference axis is
            # never in it, so a contrast query always takes the else branch.
            entry = None if negs else self.table.get(pos)
            if entry is not None:
                note = (f"AUC {entry['auc']:.3f} on {entry['n_pos']} positives"
                        + ("  <- weak: the student barely separates this class,"
                           " so this threshold means little"
                           if entry["auc"] < WEAK_AUC else ""))
                if entry["auc"] < WEAK_AUC:
                    weak.append(name)
            else:
                got = self._measure(v)
                if got is None:
                    entry = self.median
                    note = ("no eval data and no embedding cache - median "
                            "calibration, so treat the '*' as a guess and the "
                            "ranking as the answer")
                else:
                    mu_m, sd_m = got
                    entry = {"z_threshold": self.median["z_threshold"],
                             "mean_neg": mu_m, "std_neg": sd_m}
                    note = (f"measured on {self.emb.shape[0]} eval images, "
                            f"z from the median row")
                    synth.append(name)
            mu, sd = entry["mean_neg"], entry["std_neg"]
            # --threshold is still in cosine, because that is the number a
            # person reads off the log when they want to move a cutoff by hand.
            # It is converted here rather than special-cased on the device.
            if self.override is not None:
                zthr = (self.override - mu) / sd if sd > 0 else 0.0
                note = f"--threshold {self.override:.3f}, overriding everything"
            else:
                zthr = entry["z_threshold"]
            cal.append((zthr, mu, sd))
            print(f"query     : {name:<20} {ROLE_NAME[roles[k]]:<8} z>{zthr:>5.2f} "
                  f"(background {mu:.3f} +-{sd:.3f}, cos {mu + zthr * sd:.3f})"
                  f"   {note}", file=sys.stderr)
            if negs:
                print(f"            {CONTRAST_MARK} = {pos!r} against "
                      f"{', '.join(repr(n) for n in negs)}", file=sys.stderr)

        if weak:
            print(f"            {len(weak)} of {len(specs)} queries are below "
                  f"AUC {WEAK_AUC:.2f} ({', '.join(weak)}) - the demo will still "
                  f"rank them, and the ranking is the part that holds up",
                  file=sys.stderr)
        if synth:
            # Said plainly because it is a different estimator, not a worse
            # version of the same one: the table's mu/sd are over each query's
            # negatives, these are over every eval image. No AUC comes with
            # them, so there is no weak-query warning to be had either.
            print(f"            {len(synth)} calibrated from the embedding "
                  f"cache ({', '.join(synth)}) - measured, but over all images "
                  f"rather than that query's negatives, and with no AUC behind "
                  f"the threshold", file=sys.stderr)
        # A PLAIN query inside a two-stage set is legal and takes no part in the
        # decision - the board gates and then ranks the states, and this one is
        # neither. It still appears on every frame line, so it looks like it is
        # doing something. Say that it is not.
        idle = [n for n, r in zip(names, roles, strict=False) if r == Q_PLAIN] if gate_specs else []
        if idle:
            print(f"            {len(idle)} query in a two-stage set is neither "
                  f"presence nor state ({', '.join(idle)}) - it will be scored "
                  f"and printed but cannot produce a MATCH. Give it a '/' to "
                  f"make it a state query.", file=sys.stderr)
        return names, np.stack(rows), cal, roles


def pack_queries(names: list[str], vecs, cal, roles=None,
                 bg_tau: int = BG_TAU, bg_flags: int = BG_HOLD) -> bytes:
    """The payload m9.c's recv_queries() parses, byte for byte.

    M12 widened the header from 8 bytes to 16 to carry the background policy.
    The records after it are unchanged, so a query packed here is the same
    bytes it always was - only its offset moved. An old host against new
    firmware fails the length check and is told so; see m9.c's recv_queries().

    M19 turned that policy's last word into a flag set without widening it
    again. The length is the same, so the failure mode that migration has to
    worry about is not a short header but a bit the board does not know: it
    rejects those by name rather than ignoring them, because a run scored under
    a policy nobody asked for is the exact mistake this milestone is about.

    M20 widened the *record* instead, by one u32 of role. The header could have
    carried a bitmask in bg_flags' spare bits and stayed 16 bytes, but a role is
    a property of one query and belongs beside that query's threshold; a
    per-query fact folded into a global word is the kind of thing that reads
    fine when it is written and wrong a year later. The cost is the length
    check, which an M12-era host now fails - loudly, with the expected size, the
    same migration story as before.
    """
    body = struct.pack("<IIII", len(names), vecs.shape[1], bg_tau, bg_flags)
    if roles is None:
        roles = [Q_PLAIN] * len(names)
    for name, vec, (zthr, mu, sd), role in zip(names, vecs, cal, roles, strict=False):
        raw = name.encode("utf-8")[:NAME_LEN - 1]
        body += raw + b"\0" * (NAME_LEN - len(raw))
        body += struct.pack("<fffI", zthr, mu, sd, role)
        body += vec.astype("<f4").tobytes()
    return body


def bootsel(port: str) -> int:
    """host/m8.py's routine, unchanged - m9 checks stdin once a frame too."""
    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        try:
            with serial.Serial(port, 115200, timeout=0.5) as s:
                s.dtr = True
                time.sleep(0.2)
                s.write(b"B")
                s.flush()
                time.sleep(0.5)
            with serial.Serial(port, 1200) as s:
                s.dtr = False
                time.sleep(0.3)
        except OSError:
            pass                      # the port vanishing is the success case
        for _ in range(20):
            if Path("/Volumes/RP2350").is_dir():
                print("BOOTSEL - /Volumes/RP2350 is up", file=sys.stderr)
                return 0
            time.sleep(0.25)
    print("the board never reached BOOTSEL - unplug and replug USB",
          file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*",
                    help=f"what to look for, up to {MAX_Q} of them. A query may "
                         f"name what it is NOT, after a {NEG_SEP!r}: "
                         f"\"an opened book {NEG_SEP} a closed book {NEG_SEP} a "
                         f"book\". Give two or three negatives rather than one - "
                         f"naming only the opposite state measured worse than "
                         f"naming none at all")
    ap.add_argument("--bitstream", type=Path,
                    # See the note in host/m6.py. rtl/build/gemm_top_wide.hex is
                    # the last *untagged* wide build and froze at M14; the
                    # shipped netlist is the M16 one. m6.py and m7.py were
                    # repointed when that was found and this file was missed,
                    # which is its own small lesson about fixing a default in
                    # the two places you happen to be looking at - and then all
                    # four were moved off rtl/build/ entirely, since that
                    # directory is gitignored and overwritten in place.
                    default=Path("rtl/bitstreams/m16/gemm_top_wide.hex"),
                    help="either image works: m9 probes the wire and reports "
                         "which configuration it found")
    ap.add_argument("--export", type=Path,
                    default=Path("model/runs/so400m-full-a05/export"),
                    help="the export directory that was flashed. Its "
                         "export.json names the teacher, so this flag is what "
                         "chooses the query space - and must match "
                         "firmware/CMakeLists.txt's FGX_EXPORT. The board's "
                         "crc32 is checked against it before any query is sent")
    ap.add_argument("--thresholds", type=Path, default=None,
                    help="from model/evaluate.py --emit-thresholds. Defaults to "
                         "model/cache/thresholds_<run>.json for the export's "
                         "run - a table is only valid in the space it was "
                         "measured in, so it follows the export rather than "
                         "sitting at a fixed path")
    ap.add_argument("--threshold", type=float, default=None,
                    help="one threshold for every query, overriding the file - "
                         "for re-tuning from the bench")
    ap.add_argument("--embeddings", type=Path, default=None,
                    help="from model/evaluate.py --emit-embeddings, defaulting "
                         "to model/cache/eval_int8_<run>_camera.npy for the "
                         "export's run. Lets a query "
                         "the threshold table has no row for - every contrast "
                         "query, and any free text - be calibrated by "
                         "measurement instead of by the median of everyone else")
    ap.add_argument("--gate", action="append", default=[], metavar="PHRASE",
                    help="a presence prompt, which switches the board to the "
                         "two-stage rule: this has to clear its threshold "
                         "before anything is called, and the contrast queries "
                         "are then ranked against each other rather than "
                         "against a threshold of their own. Repeatable, and "
                         "all of them have to clear. Write it bare - 'a hand', "
                         "not 'a hand / a fist' - because what makes a good "
                         "gate is exactly what makes a bad discriminator")
    ap.add_argument("--bg-tau", type=int, default=BG_TAU, metavar="N",
                    help=f"frames of warm-up before the background baseline "
                         f"freezes, or the averaging window under --no-bg-hold "
                         f"(default {BG_TAU}). 0 keeps COCO's calibration for "
                         f"the whole run, which is M8's behaviour")
    ap.add_argument("--bg-hold", dest="bg_hold", action="store_true",
                    default=True,
                    help="freeze the background baseline after the warm-up "
                         "(default) - what a fixed installation watching for a "
                         "state to appear and STAY needs")
    ap.add_argument("--no-bg-hold", dest="bg_hold", action="store_false",
                    help="keep tracking the background forever, so anything "
                         "left in shot fades out. --bg-tau 200 --no-bg-hold is "
                         "M9's exact behaviour")
    ap.add_argument("--room-sd", dest="room_sd", action="store_true",
                    default=True,
                    help="divide by the spread THIS ROOM shows over the warm-up "
                         "rather than the one evaluate.py measured on COCO "
                         "(default). On the 2026-08-10 hand run the room was 9x "
                         "tighter, which is 9x of sensitivity the thresholds "
                         "were throwing away")
    ap.add_argument("--coco-sd", dest="room_sd", action="store_false",
                    help="keep COCO's fixed spread - M12's behaviour, and the "
                         "right one if the camera moves between scenes, since "
                         "then the negatives really are arbitrary photographs")
    ap.add_argument("--smooth", dest="smooth", action="store_true",
                    default=True,
                    help="EMA the z on the board before ranking and "
                         "thresholding it (default). Costs about two seconds of "
                         "latency and bought AUC 0.861 -> 0.914 on the hand run")
    ap.add_argument("--no-smooth", dest="smooth", action="store_false",
                    help="threshold each frame on its own z, which is what to "
                         "use when measuring rather than demonstrating")
    ap.add_argument("--port", default=None)
    ap.add_argument("--out", type=Path, default=Path("/tmp/m9.log"))
    ap.add_argument("--idle", type=float, default=45.0,
                    help="stop, and report a stall, after this many quiet "
                         "seconds")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="give up if the board never says anything")
    ap.add_argument("--frames", type=int, default=0,
                    help="stop after this many frame lines (0 = until Ctrl-C)")
    ap.add_argument("--snap", type=int, default=0, metavar="N",
                    help="after N frames, ask the board to dump the frame it "
                         "is scoring; render it with host/cam.py on the log")
    ap.add_argument("--snap-every", type=int, default=0, metavar="N",
                    help="dump a frame every N frame lines, not just once. For "
                         "correlating what the board saw against how it scored "
                         "it - host/cam.py numbers the PNGs in log order")
    ap.add_argument("--emb", action="store_true",
                    help="with --snap/--snap-every, also dump the 512 floats "
                         "the board scored that frame with, for "
                         "host/caption.py. Costs ~2.8 KB of base64 per dump "
                         "against the frame's 44 KB")
    ap.add_argument("--emb-every", type=int, default=0, metavar="N",
                    help="dump the 512 floats every N frames and NOT the "
                         "picture - 2.8 KB against 46.8 KB, so this can run "
                         "for a whole session where --snap-every --emb cannot. "
                         "For tools/probe_prompts.py, which needs many vectors "
                         "per scene and no images at all")
    ap.add_argument("--fault", type=int, default=0, metavar="N",
                    help="after N frames, provoke a link fault so D1 shows its "
                         "fault display for six seconds, then clears")
    ap.add_argument("--cam-fault", type=int, default=0, metavar="N",
                    help="after N frames, stall the camera bus on purpose, so "
                         "issue #8's deadline fires and costs one frame instead "
                         "of the board. The real trigger appears twice in five "
                         "runs and never on demand, so this is how the recovery "
                         "gets watched")
    ap.add_argument("--usb-drop", type=int, default=0, metavar="N",
                    help="issue #9. After N frames, press 'U': the board drops "
                         "its USB pull-up on purpose, so the outage this script "
                         "keeps meeting by accident can be watched on demand. "
                         "The board should re-attach itself after ~2 s and say "
                         "how long it was gone")
    ap.add_argument("--usb-drop-hard", type=int, default=0, metavar="N",
                    help="issue #9, the half that does not recover: press 'I' "
                         "instead, which also suppresses the board's own "
                         "re-attach, so it has to escalate to the deliberate "
                         "reboot at 30 s. The next banner should name the "
                         "reason as `usb :` rather than as a hang")
    ap.add_argument("--overlap", action="append", type=int, default=[],
                    metavar="N",
                    help="issue #10. At the board's frame N, press 'O': close "
                         "the timing window, print it, and flip the capture "
                         "between overlapped with the compute and serial. "
                         "Repeatable, and the board boots overlapped, so "
                         "`--overlap 40` gives 40 frames overlapped then the "
                         "rest serial - which is what the appliance did until "
                         "2026-08-15, and measures 429 ms by the board's clock "
                         "against 372 overlapped")
    ap.add_argument("--eager", action="append", type=int, default=[],
                    metavar="N",
                    help="issue #14. At the board's frame N, press 'D': close "
                         "the timing window, print it, and flip the trigger "
                         "between late on the schedule and back at the collect, "
                         "which is where it sat before #14. Repeatable, and the "
                         "board boots on the schedule, so `--eager 60` gives 60 "
                         "fresh-frame frames then the rest stale. Only the "
                         "shutter-to-LED line should move")
    ap.add_argument("--enrol", action="append", default=[], metavar="FRAME:KEY",
                    help="M21. At the board's frame FRAME, press KEY - '0' for "
                         "the empty scene, '1'..'6' for the Nth class query. "
                         "Repeatable. The frame is the BOARD's number off the "
                         "frame line, not a count of lines here, because the "
                         "caller computing these knows the schedule in board "
                         "frames and an off-by-one would enrol the settling "
                         "frame with a hand still in shot")
    ap.add_argument("--snap-at", action="append", default=[], type=int,
                    metavar="FRAME",
                    help="dump a frame at the BOARD's frame FRAME, the same "
                         "numbering --enrol uses and for the same reason: the "
                         "caller asking for a picture of an enrolment window "
                         "knows where that window is in board frames. "
                         "Repeatable, and independent of --snap-every")
    ap.add_argument("--ask", action="store_true",
                    help="read new comma-separated query sets from stdin and "
                         "send them to the running board")
    ap.add_argument("--bootsel", action="store_true",
                    help="reboot the board into BOOTSEL and wait for the drive")
    args = ap.parse_args()

    if args.bootsel:
        return bootsel(args.port or pick_port())

    if not args.queries:
        raise SystemExit('nothing to look for - try: "cup" "person" "book"')
    # Parsed here rather than at the frame it fires on. A typo in --enrol would
    # otherwise surface two minutes into a run, after the teacher has loaded and
    # while somebody is holding a scene still - and the run would be the one
    # thing it cost. Same reason --bg-tau is range-checked above.
    enrol: list[tuple[int, str]] = []
    for spec in args.enrol:
        frame, _, key = spec.partition(":")
        if not frame.isdigit() or key not in [str(k) for k in range(MAX_Q + 1)]:
            raise SystemExit(f"--enrol {spec}: want FRAME:KEY with KEY in "
                             f"0..{MAX_Q}, e.g. --enrol=125:1")
        enrol.append((int(frame), key))
    enrol.sort()
    if any(k < 1 for k in args.snap_at):
        raise SystemExit("--snap-at wants a board frame number of 1 or more")
    snap_at = sorted(set(args.snap_at))
    # A set, so a repeated frame is one keystroke and not two - two would flip
    # the overlap back and leave a window nobody asked for. Checked here for the
    # reason --enrol is: the run is the expensive thing.
    if any(k < 1 for k in args.overlap):
        raise SystemExit("--overlap wants a frame number of 1 or more")
    if any(k < 1 for k in args.eager):
        raise SystemExit("--eager wants a frame number of 1 or more")
    overlap_at = set(args.overlap)
    eager_at = set(args.eager)
    # The gates occupy slots like anything else, and counting only --queries
    # here would push the overflow onto the board, which rejects the whole set
    # after a minute of teacher loading rather than before it.
    if len(args.queries) + len(args.gate) > MAX_Q:
        raise SystemExit(f"{len(args.queries)} queries and {len(args.gate)} "
                         f"gates, and the board holds {MAX_Q} in total")
    if not args.bitstream.exists():
        raise SystemExit(f"{args.bitstream}: not found - run ./rtl/build.sh")
    # Checked here as well as on the device. The device's rejection costs a
    # round trip and arrives after the bitstream has already gone over.
    if not 0 <= args.bg_tau <= BG_MAX_TAU:
        raise SystemExit(f"--bg-tau {args.bg_tau}: the board takes 0 to "
                         f"{BG_MAX_TAU}")

    image, kind = load_image(args.bitstream)
    print(f"bitstream : {args.bitstream} ({kind}), {len(image)} bytes, "
          f"crc32=0x{zlib.crc32(image):08X}", file=sys.stderr)

    export = load_export(args.export)
    print(f"export    : {args.export}/weights.bin, run {export['run']}, "
          f"{export['bytes']} bytes, crc32={export['crc32']}", file=sys.stderr)
    # Derived rather than fixed, so pointing --export at the other run moves the
    # calibration with it instead of quietly reusing the wrong space's numbers.
    thresholds = args.thresholds or Path(
        f"model/cache/thresholds_{export['run']}.json")
    embeddings = args.embeddings or Path(
        f"model/cache/eval_int8_{export['run']}_camera.npy")

    # Encode before opening the port. Loading the teacher takes long enough that
    # doing it with the board already waiting turns a slow import into what
    # looks like a dead link.
    enc = Encoder(export, thresholds, args.threshold, embeddings)
    if enc.meta:
        print(f"thresholds: {thresholds}, {len(enc.table)} queries at "
              f"{enc.meta.get('fpr', 0):.0%} FPR on "
              f"{enc.meta.get('geometry', '?')} geometry, median "
              f"z {enc.median['z_threshold']:.2f}", file=sys.stderr)
    names, vecs, cal, roles = enc.encode(args.queries, args.gate)
    bg_flags = ((BG_HOLD if args.bg_hold else 0)
                | (BG_ROOM_SD if args.room_sd else 0)
                | (BG_SMOOTH if args.smooth else 0))
    payload = pack_queries(names, vecs, cal, roles, args.bg_tau, bg_flags)
    print(f"queries   : {len(names)} x {vecs.shape[1]}-d, "
          f"{len(payload)} bytes, background "
          + (f"frozen after {args.bg_tau} frames" if args.bg_hold
             else f"tracking, tau {args.bg_tau}")
          + f", {'room' if args.room_sd else 'COCO'} spread"
          + (", z smoothed" if args.smooth else ""), file=sys.stderr)

    port = args.port or pick_port()
    print(f"port      : {port}", file=sys.stderr)

    # --ask reads stdin on a thread but never touches the serial port: the main
    # loop picks the line up between pumps and does the encode and the send
    # itself. Encoding costs about a second with the model resident, which is
    # one frame of paused output and no shared state at all.
    asked: queue.Queue = queue.Queue()
    if args.ask:
        def reader() -> None:
            for line in sys.stdin:
                asked.put(line.strip())
        threading.Thread(target=reader, daemon=True).start()

    frames, stalled, interrupted, sets = 0, False, False, 1
    tally: dict[str, int] = {}

    # write_timeout is the backstop under CHUNK's comment. With the chunking
    # right this should never fire; if it ever does, the run ends with a
    # sentence naming the direction that stalled instead of hanging until
    # somebody notices, which is how the first one was found - late.
    with serial.Serial(port, 115200, timeout=0.5, write_timeout=10) as s, \
            args.out.open("w") as log:
        s.dtr = True

        # Everything the board has said, kept because the crc32 check below
        # reads a line that scrolls past during the start-up wait.
        transcript: list[str] = []

        # Set when follow_reboot() gave up: the port is closed and every step
        # below that talks to the board has to be skipped, but the frames that
        # did arrive are still worth summarising.
        gone = False

        # Set by follow_reboot(), read by the frame loop and by the summary.
        # The run is over the moment this is true - the board has forgotten its
        # frozen background - so it ends the loop rather than resuming it.
        rebooted = False

        def emit(text: str) -> str:
            sys.stdout.write(text)
            sys.stdout.flush()
            log.write(text)
            log.flush()
            transcript.append(text)
            return text

        # A vanished port is followed, not absorbed. See REOPEN_S's comment for
        # why this is the difference between eight wedges with no cause and a
        # wedge with an address.
        #
        # The board is identified by VID on the way back, never by the name it
        # had before: the whole event is macOS renaming the node, so the old
        # path is precisely the wrong thing to reopen. board.find_port() is the
        # one answer, and it declines to guess when two RP2350s are on the bus.
        def follow_reboot() -> str:
            nonlocal s, port, rebooted
            rebooted = True
            emit(f"\n[host] {port} vanished mid-run. That is what m9's watchdog "
                 f"reboot looks like from this end; the board should come back "
                 f"in a few seconds and say why. Following it.\n")
            with contextlib.suppress(OSError, serial.SerialException):
                s.close()
            deadline = time.monotonic() + REOPEN_S
            while time.monotonic() < deadline:
                time.sleep(0.5)
                found = find_port()
                if found is None:
                    continue
                try:
                    fresh = serial.Serial(found, 115200, timeout=0.5,
                                          write_timeout=10)
                except (OSError, serial.SerialException):
                    continue          # enumerated, not yet openable
                # DTR is what the board is waiting on, so raising it is not
                # housekeeping - it is the thing that releases the report.
                fresh.dtr = True
                s, port = fresh, found
                emit(f"[host] reattached on {port}, DTR raised.\n")
                return ""
            emit(f"[host] nothing with VID {RP2350_VID} came back within "
                 f"{REOPEN_S:.0f}s, so the board is not enumerating at all and "
                 f"the watchdog did not get it either.\n"
                 f"[host] {recover()}\n")
            # AND STOP, because `s` is closed and everything after this point
            # writes to it. Returning "" used to let the caller carry on into
            # the next send(), which then died on PortNotOpenError - a traceback
            # in place of the two lines above, which are the ones that say what
            # actually happened. The message is already printed; this only ends
            # the run at the point where the board did.
            raise BoardGone


        def pump() -> str:
            try:
                chunk = s.read(4096)
            except (OSError, serial.SerialException):
                return follow_reboot()
            if not chunk:
                return ""
            return emit(chunk.decode("utf-8", "replace"))

        def drain() -> str:
            # pump() waits out the whole 0.5 s read timeout when the board has
            # nothing to say, which is right in await_line() - it is a wait -
            # and wrong in send(), where it is 0.5 s of nothing per chunk. This
            # takes only what has already arrived.
            return pump() if s.in_waiting else ""

        def send(magic: bytes, body: bytes) -> None:
            s.write(magic + len(body).to_bytes(4, "little")
                    + zlib.crc32(body).to_bytes(4, "little"))
            for i in range(0, len(body), CHUNK):
                s.write(body[i:i + CHUNK])
                drain()
            s.flush()

        def await_line(needle: str, why: str, secs: float | None = None) -> bool:
            started = time.monotonic()
            limit = args.wait if secs is None else secs
            seen = ""
            while needle not in seen:
                seen += pump()
                if "RESULT : FAIL" in seen:
                    return False
                if time.monotonic() - started > limit:
                    if why:
                        print(f"\n({why})", file=sys.stderr)
                    return False
            return True

        # Wait for each prompt rather than sending immediately. For the
        # bitstream that is m8.py's reason - the header must not land before
        # stdio is up. For the queries it is a harder requirement: the board
        # spends ~15 s on the reference, the width probe and the exposure ramp
        # without reading stdin, and 12 KB pushed into a CDC buffer nobody is
        # draining just blocks this end.
        # A missed banner is not a dead board. stdio_usb discards everything
        # printed before a host attaches, so a board that finished booting in
        # the second between the flash and this open has already said its line
        # to nobody, and it will now sit in ft_recv_bitstream(0) - which waits
        # forever - without ever repeating itself. Bailing here sent one bench
        # session chasing a phantom.
        #
        # So the timeout downgrades to a note and we push the bitstream anyway.
        # The wait was only ever evidence that stdio is up; having missed the
        # line is the same evidence, arrived differently. If the board really is
        # absent this costs 173 KB into a void and the *next* await_line says so
        # with a message that fits what actually happened.
        if not await_line("waiting for a bitstream", "", secs=10.0):
            # UNLESS THE BOARD IS ALREADY RUNNING, which is a different thing
            # and used to be catastrophic. m9 never stops on its own, so a run
            # that ended without the 'B' below - Ctrl-C, a crash, a killed
            # terminal - leaves the board in its frame loop, and the next
            # demo.py would then push 173 KB of bitstream into poll_host(). The
            # first 0x42 in it reads as 'B' and the board goes to BOOTSEL
            # mid-download, which from here looks exactly like issue #9: a port
            # that vanished and did not come back. It cost a bench session. The
            # firmware side is fixed (m9.c's quiet-time guard), and this is the
            # other half: a running board gets 'R' and starts a clean run.
            if re.search(r"^frame\s+\d+", "".join(transcript), re.MULTILINE):
                print("\n(the board is still in the frame loop from an earlier "
                      "run - pressing 'R' to restart it rather than sending a "
                      "bitstream into a running loop.)", file=sys.stderr)
                s.write(b"R")
                s.flush()
                if not await_line("waiting for a bitstream",
                                  "the board was mid-run, took 'R', and never "
                                  "came back to the bitstream prompt",
                                  secs=30.0):
                    return 1
                # 'R' IS watchdog_reboot(), so the port vanishes and comes back
                # and follow_reboot() sets `rebooted` on the way through - the
                # detector cannot tell this end's own keypress from a wedge.
                # Left set, it voids a run that has not started yet: two
                # consecutive benches came back ">>> VOID: the board wedged
                # mid-run" with a clean 103-frame log underneath. The reboot we
                # asked for is not evidence of anything, so it is cleared here,
                # before the run, and any later one still counts.
                rebooted = False
                # The old loop's frames are in the log above, numbered from
                # wherever that session had reached - 1114 on the run that
                # caught this - and the run about to start numbers from 1. Two
                # sessions in one file is not a log, and every tool downstream
                # reads it by frame number. `emit` is the only writer and it
                # mirrors into `transcript`, so the file can be rebuilt from the
                # reattach onward. Keeping the reattach line itself: the banner
                # the board prints after the reboot comes later, so the crc32
                # and query-set checks below still have theirs.
                cut = next((n for n, t in enumerate(transcript)
                            if "reattached on" in t), None)
                if cut is not None:
                    del transcript[:cut]
                    log.seek(0)
                    log.truncate()
                    log.writelines(transcript)
                    log.flush()
            else:
                print("\n(no banner in 10s - it was probably printed "
                      "before this end opened the port, which stdio_usb drops. "
                      "Sending the bitstream anyway.)", file=sys.stderr)
        send(MAGIC_B, image)

        if not await_line("waiting for a query set",
                          "the board never got as far as asking for queries - "
                          "one of its three start-up checks failed above"):
            return 1

        # THE FLASH, AS OPPOSED TO THE FILE. export.json says which space the
        # blob on disk emits into; only the board can say which blob it is
        # running. Both shipped students are 512-d and both answer every query
        # with a plausible number, so a stale flash has no symptom other than
        # being wrong - and the vectors are already encoded and about to go over
        # the wire. This is the last moment it can be caught.
        # Anchored on the "weights" label, not on a bare crc32=, so that adding
        # a second hash to the banner some day cannot silently redirect this
        # check at the wrong one.
        found = re.search(r"weights\s+:.*crc32=0x([0-9A-Fa-f]{8})",
                          "".join(transcript))
        if not found:
            print(f"\n(the board never printed its weights crc32, so which "
                  f"model it is running cannot be established. That line came "
                  f"in with M18 - a build old enough to lack it is old enough "
                  f"to be the wrong student. Rebuild and reflash:\n"
                  f"   cmake -S firmware -B firmware/build "
                  f"-DFGX_EXPORT=$PWD/{args.export}\n"
                  f"   ninja -C firmware/build forgix_m9)", file=sys.stderr)
            return 1
        # Compared as integers. The first version of this compared the two
        # strings with .upper() on both, which also uppercases the "0x" prefix
        # on one side only - so "0xF368CC6E" never equalled "0XF368CC6E" and the
        # guard refused a board that matched. It failed closed, which is the
        # right direction, but a check that cannot pass is not a check: it would
        # have been switched off by the first person it inconvenienced.
        board_n = int(found.group(1), 16)
        board = f"0x{found.group(1).upper()}"
        if board_n != int(export["crc32"], 16):
            print(f"\n(the flashed weights are not the ones {args.export} "
                  f"describes:\n"
                  f"   board      {board}\n"
                  f"   export.json {export['crc32']}  (run {export['run']}, "
                  f"{export['spec']})\n"
                  f" Queries were encoded for the export's space, and the "
                  f"board's is 512-d too, so sending them would score instead "
                  f"of failing. Refusing. Either reflash from {args.export} or "
                  f"pass the --export the board is actually running.)",
                  file=sys.stderr)
            return 1
        print(f"flash     : crc32 {board} matches {args.export}/export.json",
              file=sys.stderr)
        send(MAGIC_Q, payload)

        last = time.monotonic()
        carry = ""
        try:
            while time.monotonic() - last < args.idle:
                if not asked.empty():
                    # Commas separate queries, NEG_SEP separates the terms
                    # within one, so both syntaxes fit on the same line:
                    #   an opened book / a closed book / a book, a cup
                    want = [q.strip() for q in
                            re.split(r"[,\n]", asked.get()) if q.strip()]
                    if not want:
                        continue
                    if len(want) > MAX_Q:
                        print(f"(the board holds {MAX_Q} queries, not "
                              f"{len(want)})", file=sys.stderr)
                        continue
                    # The gates ride along unchanged. Asking a new question at
                    # runtime should not silently drop the presence stage and
                    # switch the board back to the old rule mid-run - and
                    # MAX_Q is checked above against `want` alone, so the
                    # board's own count is what catches an overlong set.
                    n, v, c, rl = enc.encode(want, args.gate)
                    # A re-send also restarts the warm-up, because
                    # recv_queries() resets the baseline on every accepted set.
                    # That is the documented escape from a background that froze
                    # around the wrong scene; 'N' on the board is the other one.
                    send(MAGIC_Q, pack_queries(n, v, c, rl, args.bg_tau,
                                               bg_flags))
                    sets += 1
                    last = time.monotonic()
                    continue

                text = pump()
                if rebooted:
                    break
                if not text:
                    # Quiet is two states wearing one face, and telling them
                    # apart IS the diagnosis: the port gone means the watchdog
                    # fired and the report is already on its way, the port still
                    # there means whatever the board is stuck in is feeding the
                    # watchdog - which is a far shorter list of places than "the
                    # frame loop". Asked of the bus about this exact node, not
                    # via find_port(), which declines to answer at all when a
                    # second RP2350 is on the desk - and that desk exists.
                    if (time.monotonic() - last > QUIET_PROBE_S
                            and port not in [p.device for p in bus_ports()]):
                        follow_reboot()
                        break
                    continue
                last = time.monotonic()
                carry += text
                lines = carry.split("\n")
                carry = lines[-1]
                for ln in lines[:-1]:
                    if not ln.startswith("frame "):
                        continue
                    frames += 1
                    # M21's enrolment keys. `>=`, not `==`: a frame line that
                    # never arrives - a dropped USB packet, a dump in the middle
                    # of the run - would otherwise skip the enrolment silently
                    # and the board would spend the rest of the run on the old
                    # rule while the log looked like a clean M21 run.
                    if enrol or snap_at:
                        tok = ln.split()
                        bf = int(tok[1]) if len(tok) > 1 and tok[1].isdigit() else -1
                        while enrol and bf >= enrol[0][0]:
                            key = enrol.pop(0)[1]
                            print(f"enrol     : pressing '{key}' at board frame "
                                  f"{bf}")
                            sys.stdout.flush()
                            s.write(key.encode())
                            s.flush()
                        # `>=` again, and every frame we ran past is dropped
                        # rather than dumped: two 44 KB transfers back to back
                        # to catch up on a missed one would cost more frames
                        # than they document.
                        if snap_at and bf >= snap_at[0]:
                            while snap_at and bf >= snap_at[0]:
                                snap_at.pop(0)
                            print(f"snap      : requesting a dump at board "
                                  f"frame {bf}")
                            sys.stdout.flush()
                            s.write(b"PV" if args.emb else b"P")
                            s.flush()
                    # Once, on the way past. The dump is ~44 KB of base64 into
                    # the same log, which host/cam.py picks out by its BEGIN/END
                    # markers - that is what those markers are for.
                    if args.snap and frames == args.snap:
                        s.write(b"PV" if args.emb else b"P")
                        s.flush()
                    # Repeatedly, for the case --snap cannot serve: correlating
                    # a score against the image that produced it, when nobody
                    # knows in advance which frame will be the interesting one.
                    # The marker goes in the log first so the dump can be tied
                    # to a frame number - cam.py numbers blocks in log order,
                    # and order alone stops being enough to trust the moment a
                    # dump is dropped or the loop stalls mid-transfer. Read the
                    # number as off by one in the safe direction: 'P' dumps the
                    # *next* frame (m9.c:770), so the image belongs to N+1 while
                    # the marker says N. A second either way does not matter for
                    # "what was in shot", which is what this is for.
                    elif args.snap_every and frames % args.snap_every == 0:
                        print(f"snap      : requesting a dump at frame {frames}")
                        sys.stdout.flush()
                        # 'V' after 'P' rather than instead of it: m9.c defers
                        # both to the same next frame, so the image and the 512
                        # floats describe one capture. host/caption.py reads the
                        # vector back in words next to the picture cam.py
                        # renders, which is the pairing the two are for.
                        s.write(b"PV" if args.emb else b"P")
                        s.flush()
                    # Its own `if`, not another arm of that chain: asking for
                    # vectors throughout a run and a picture at one chosen
                    # moment are different requests and there is no reason one
                    # should silence the other. The board defers 'V' by a frame
                    # exactly as it defers 'P' (m9.c:1039), and prints
                    # "embedding : frame N" itself right before the block, so
                    # host/caption.py ties block to frame off the board's line
                    # rather than off anything counted here.
                    if args.emb_every and frames % args.emb_every == 0:
                        s.write(b"V")
                        s.flush()
                    # M11 gate 7. The board holds the fault for six seconds and
                    # then clears it, so this needs no second keystroke - which
                    # matters, because the thing being checked is on the board
                    # and the person checking it is looking at the board.
                    if args.fault and frames == args.fault:
                        s.write(b"E")
                        s.flush()
                    # And the camera's. One keystroke, one lost frame: the board
                    # arms the stall for the next transfer, prints where the
                    # state machine stopped, resyncs and carries on - so unlike
                    # 'E' there is nothing to clear and nothing to wait out.
                    if args.cam_fault and frames == args.cam_fault:
                        s.write(b"C")
                        s.flush()
                    # And issue #9's, which is the one that takes this end with
                    # it: after the keystroke the port goes away, so the branch
                    # below that follows a vanished board by VID is part of the
                    # test rather than an error path. Flush before the port dies
                    # or the byte can still be sitting in the driver.
                    if args.usb_drop and frames == args.usb_drop:
                        s.write(b"U")
                        s.flush()
                    if args.usb_drop_hard and frames == args.usb_drop_hard:
                        s.write(b"I")
                        s.flush()
                    # Issue #10's A/B. Repeatable, and the board prints the
                    # window it just closed, so `--overlap 30 --overlap 60`
                    # reads out as three windows on one scene under one lamp -
                    # which is the only way the comparison is worth anything,
                    # since the encode is the same either way and what moves is
                    # a wait on a sensor that does not care what we are doing.
                    if frames in overlap_at:
                        s.write(b"O")
                        s.flush()
                    # Issue #14's, one level down and read the same way. Both on
                    # the same frame is allowed and means "flip both", which is
                    # a window nobody can attribute - so don't, and the schedule
                    # that generates these should keep them apart.
                    if frames in eager_at:
                        s.write(b"D")
                        s.flush()
                    # Up to the "(cos", not the first space: m9.c prints
                    # `MATCH <name> (cos 0.037)` and a name has spaces in it
                    # more often than not. \S+ tallied "wine glass" as "wine"
                    # and M12's "an opened book~" as "an", which looked like a
                    # board fault in the summary and was this line all along.
                    hit = re.search(r"MATCH (.+?) \(cos", ln)
                    tally[hit.group(1) if hit else "-"] = \
                        tally.get(hit.group(1) if hit else "-", 0) + 1
                if args.frames and frames >= args.frames:
                    break
            else:
                stalled = True
        except KeyboardInterrupt:
            interrupted = True
        except BoardGone:
            gone = True
        except serial.SerialTimeoutException:
            interrupted = True
            print("\n(the board stopped accepting bytes for 10 s mid-send. "
                  "That is the deadlock CHUNK's comment describes, and finding "
                  "it here rather than by waiting is the point of the timeout. "
                  "Replug USB.)", file=sys.stderr)

        # THE ONLY REASON THIS RUN IS STILL RUNNING. The measurement is gone -
        # the reboot forgot the frozen background, and resuming would restart
        # the baseline around whatever is in shot now and produce a number that
        # looks like a run. So this collects one line and stops.
        hang = ""
        if rebooted and not gone:
            # Waited out against the report's LAST line and not its first. The
            # `hang :` line is 120 characters and the CDC delivers 64 at a time,
            # so a chunk boundary lands inside it about half the time, and a
            # search that stops the moment the first line matches quotes half a
            # stage name and calls it the answer. The closing sentence is the
            # only thing that says the block arrived whole. It is quoted from
            # firmware/m9.c:709 - if that wording changes, this waits out
            # HANG_REPORT_S and then prints whatever did arrive, which is the
            # failure this should have.
            until = time.monotonic() + HANG_REPORT_S
            while (time.monotonic() < until
                   and "what survives is this line." not in "".join(transcript)):
                pump()
            found = re.search(r"^hang\s+:[^\r\n]*(?:\r?\n[ \t]{2,}[^\r\n]*)*",
                              "".join(transcript), re.MULTILINE)
            hang = found.group(0).replace("\r", "") if found else ""

        # Leave the board in BOOTSEL, for m8.py's reason: m9 never stops on its
        # own, so "the script finished" and "the board is still looping" are the
        # same state, and the next thing anybody does is flash it.
        #
        # Not after a reboot, though. A board that has just rebooted is already
        # stopped, at the bitstream prompt, one demo.py away from another run -
        # and BOOTSEL would throw that away and demand a reflash to get back to
        # exactly where it is standing. The next thing anybody does here is
        # re-run, not flash.
        #
        # The 'B' is answered before it is obeyed: m9.c:1991 prints the
        # `stopped :` block - the frames, the good count, and the only
        # ms/frame figure a run ever produces - flushes, waits 50 ms and then
        # detaches into BOOTSEL. Sleeping 0.6 s and pumping once afterwards
        # read a port that had already gone, so that line was lost on every
        # run that ended normally; and pump()'s OSError path is
        # follow_reboot(), which then spent REOPEN_S looking for a board this
        # end had just asked to leave, and voided the measurement it had
        # finished taking. Read what arrives, and let the detach end the loop
        # rather than chase it - this departure is the one we asked for.
        if not rebooted:
            try:
                s.write(b"B")
                s.flush()
                until = time.monotonic() + 1.0
                while time.monotonic() < until:
                    if s.in_waiting:
                        emit(s.read(s.in_waiting).decode("utf-8", "replace"))
                    else:
                        time.sleep(0.02)
            except (OSError, serial.SerialException):
                pass

    print(f"\nsaved     : {args.out}  ({frames} frame lines, {sets} query "
          f"set{'' if sets == 1 else 's'})", file=sys.stderr)
    for name, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        label = "no match" if name == "-" else f"MATCH {name}"
        print(f"            {label:<28} {n:5d} frames  "
              f"({n * 100 // max(frames, 1)}%)", file=sys.stderr)

    text = args.out.read_text()
    if "RESULT : FAIL" in text:
        print("(the board failed one of its four start-up checks)",
              file=sys.stderr)
        return 1
    if "queries   : rejected" in text and sets == 1:
        print("(the board rejected the query set - see the reason above)",
              file=sys.stderr)
        return 1
    if rebooted:
        if hang:
            print(f"\nThe board wedged and its watchdog rebooted it. It says "
                  f"where:\n\n{hang}\n\nThe run is void - the frozen background "
                  f"went with the reboot - but the board is back at the "
                  f"bitstream prompt, so re-running this is all it takes.",
                  file=sys.stderr)
            return 3
        if gone:
            print(f"\n(the board left the bus and never came back - see the "
                  f"[host] lines above. {(args.frames and 'The run is void. ') or ''}"
                  f"That is issue #9's shape, but a board that is still gone "
                  f"cannot be asked which of its two exits it took, so recover "
                  f"it first and read the next banner: `usb :` means it saw the "
                  f"outage and rebooted itself, no banner at all means the "
                  f"reboot never got it back on the bus.)", file=sys.stderr)
            return 3
        # The other half of #9, and the other end of the same event: the board
        # was gone long enough that it rebooted itself on purpose, and the
        # banner after the reboot names that as the reason. Matched on the
        # board's own line rather than inferred here, for wd_report_last()'s
        # reason - a deliberate reboot must not read as a hang.
        why = re.search(r"^usb\s+: the last run rebooted[^\r\n]*"
                        r"(?:\r?\n[ \t]{2,}[^\r\n]*)*", text, re.MULTILINE)
        if why:
            asked = bool(args.usb_drop_hard)
            print(f"\n({'as asked: ' if asked else ''}the board rebooted itself "
                  f"because the bus stopped answering, and said so:\n\n"
                  f"{why.group(0).replace(chr(13), '')}\n\n"
                  f"The run is void either way - the frozen background went "
                  f"with the reboot.)", file=sys.stderr)
            return 0 if asked else 3
        # Issue #9's outage, and the board now says so itself: it stayed in the
        # loop the whole time and named the frames the log is missing. That is a
        # different event from a reboot and it used to read as the paragraph
        # below, which guesses. Requested with --usb-drop it is a pass; arriving
        # on its own it is still the open fault, so it still ends non-zero.
        back = [ln for ln in text.splitlines() if "usb       : back after" in ln]
        if back:
            asked = bool(args.usb_drop or args.usb_drop_hard)
            print(f"\n({'as asked: ' if asked else ''}the board went off the "
                  f"bus and brought itself back, without rebooting and without "
                  f"leaving the loop.\n {back[-1].strip()}\n"
                  f"{'' if asked else 'That is issue #9 happening on its own. '}"
                  f"The frames inside that window were computed and their "
                  f"lines are gone.)", file=sys.stderr)
            return 0 if asked else 3
        print("\n(the board vanished and came back, which is a watchdog "
              "reboot, but it never printed the `hang :` line that names the "
              "stage. Either the reboot was not the watchdog's - a brown-out "
              "or a USB re-enumeration on its own would look identical from "
              "here - or this end reattached and the board is still waiting "
              "on something. The log has whatever it did say.)",
              file=sys.stderr)
        return 3
    if stalled:
        print(f"(nothing printed for {args.idle:.0f} s - the loop stalled. "
              f"NOTE the port never went away, so the board is still "
              f"enumerated and its watchdog did NOT reboot it: whatever it is "
              f"stuck in is feeding the watchdog, which is a much smaller list "
              f"than 'somewhere in the frame loop'.)",
              file=sys.stderr)
        return 2
    if not frames:
        print("(the loop never printed a frame)", file=sys.stderr)
        return 2
    if interrupted:
        print("(stopped by Ctrl-C)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BoardGone:
        # Raised during start-up, before the frame loop's own handler exists.
        # follow_reboot() has already printed the why and the uhubctl line.
        sys.exit(3)
