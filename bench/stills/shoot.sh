#!/bin/sh
# Stills of one scene, through the appliance's own camera.
#
#   mkdir -p bench/stills/20260821-laptop
#   printf 'an opened laptop\na closed laptop\n' \
#       > bench/stills/20260821-laptop/queries.txt
#
#   sh bench/stills/shoot.sh 20260821-laptop open   1   # then close it by hand
#   sh bench/stills/shoot.sh 20260821-laptop closed 1
#   sh bench/stills/shoot.sh 20260821-laptop open   2   # ... and again
#
# $1 = set (a directory under bench/stills/), $2 = class, $3 = round,
# $4 = stills wanted (default 10).
#
# The set directory must hold a `queries.txt` of exactly two lines: the positive
# phrase and the negative one, as `tools/probe_bisect.py` will be given them.
# They change nothing about the pixels - they are there so the log carries the
# board's own reading of the same frames, and so the pair a directory of PNGs is
# about is written down beside the PNGs rather than in someone's memory.
#
# NOT A BENCH.  No cue protocol, no enrolment, no LED - just the camera, which
# is why it costs minutes instead of a morning and can be repeated as often as
# the question needs.  What comes out is PNGs for tools/probe_*.py to encode;
# what the board scores while it captures them is a free side effect and is
# kept only as provenance.
#
# ROUNDS, RATHER THAN ONE LONG RUN OF EACH SCENE, and this is the whole reason
# the script takes a round number.  Thirty consecutive tea frames followed by
# thirty consecutive empty ones confounds the class with everything that drifts
# between the two halves - the AEC, the daylight, the operator - which is
# exactly the confound that made four glass benches unreadable and that 08-20's
# interleaved book/glass run was built to break.  Alternate, and a margin that
# is really drift shows up as a sign that flips from round to round.  Two rounds
# is the floor: probe_bisect.py cannot measure its drift null below that and
# says so rather than printing a zero.
#
# The board is left in BOOTSEL by demo.py (it sends 'B' on the way out), so
# every round begins by putting it back.  That is not a workaround, it is the
# documented exit.

cd /Users/kaz/Documents/GitHub/fpga-open-vocab || exit 1
UV=/Users/kaz/.local/bin/uv

SET=$1
CLASS=$2
ROUND=$3
WANT=${4:-10}
if [ -z "$SET" ] || [ -z "$CLASS" ] || [ -z "$ROUND" ]; then
  echo "usage: sh bench/stills/shoot.sh SET CLASS ROUND [STILLS]" >&2
  exit 1
fi

DIR=bench/stills/$SET
QF=$DIR/queries.txt
if [ ! -f "$QF" ]; then
  echo "no $QF - write the two phrases there first:" >&2
  printf "  mkdir -p %s && printf 'POSITIVE\\\\nNEGATIVE\\\\n' > %s\n" "$DIR" "$QF" >&2
  exit 1
fi
POS=$(sed -n 1p "$QF")
NEG=$(sed -n 2p "$QF")
if [ -z "$POS" ] || [ -z "$NEG" ]; then
  echo "$QF must have two non-empty lines" >&2
  exit 1
fi

mkdir -p "$DIR/$CLASS" "$DIR/logs" || exit 1
LOG=$DIR/logs/r${ROUND}-${CLASS}.log

# One dump every other frame.  Every frame would halve the wall clock spent
# holding a scene still and is not worth it: consecutive frames of a stationary
# scene are near-duplicates, and thirty of those are one sample, not thirty.
EVERY=2
# The board dumps the frame AFTER the one the request went out on and the run
# has to outlive the last request, so ask for two frames of slack rather than
# discovering at render time that the last still is missing.  That slack buys
# one extra still more often than not, and an extra is kept: WANT is a floor.
FRAMES=$(( WANT * EVERY + 2 ))

if ! uhubctl 2>/dev/null | grep -q "2e8a:0009"; then
  echo "  (not running - taking it out of BOOTSEL)"
  $UV run host/bootsel.py --run >/dev/null 2>&1 || exit 1
  sleep 3
fi

echo "### $SET round $ROUND, '$CLASS': $WANT stills, $FRAMES frames"
$UV run host/demo.py "$POS" "$NEG" \
    --frames "$FRAMES" --snap-every "$EVERY" --out "$LOG" >/dev/null 2>&1

# Anything the acquire or the enrolment doubted, before the pictures are
# trusted.  A still taken mid-ramp is still a still, but it is not the same
# scene as the ones around it and #26 is the only thing that will say so.
grep -E "^camera    : live|^ {12}(scene|enrolment): " "$LOG" | sed 's/^/  /'

# --rot 0 because FT_MOUNT_ROT is CAM_ROT_0: the -hi PNG is then byte for byte
# what the board handed the encoder, which is the only version worth measuring
# a teacher against.  cam.py writes a -lo twin as a byte-order check; the order
# has been settled since 2026-08-07, so only -hi is kept.
TMP=/tmp/shoot_${SET}_${ROUND}_${CLASS}
rm -rf "$TMP" && mkdir -p "$TMP"
$UV run host/cam.py "$LOG" --out "$TMP" --rot 0 >/dev/null 2>&1
n=0
for f in "$TMP"/*-hi.png; do
  [ -e "$f" ] || break
  n=$((n + 1))
  cp "$f" "$DIR/$CLASS/r${ROUND}-$(basename "$f")"
done
echo "  kept $n stills in $DIR/$CLASS  (floor was $WANT)"
[ "$n" -lt "$WANT" ] && echo "  !! short - re-shoot this round, do not pad it from another"
exit 0
