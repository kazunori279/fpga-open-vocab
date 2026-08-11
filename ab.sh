#!/bin/sh
# One A/B scene experiment, from two phrases.
#
#     ./ab.sh "a glass with water" "a glass without water" "a glass"
#     ./ab.sh "an opened book" "a closed book" "a book"
#     ./ab.sh "a full cup of coffee" "an empty cup" "a cup"
#
# The third phrase is the shared neutral and is optional; give it when both
# sides are the same object in two states, which is the case this is for.
#
# M20 gives it a second job, and it turns out to have been the same job all
# along: the neutral also becomes the presence gate. So "a book" both cancels
# out of the two contrasts and decides whether there is a book there at all,
# and the two phrases are then ranked against each other instead of each
# against an empty desk - which they lose to, an empty desk being as much "not
# an opened book" as a closed one is. Without a neutral there is no gate and
# the board scores exactly as it did before.
#
# Everything else is derived, because in an A/B run there is only one sensible
# arrangement and typing it out by hand is four chances to get it subtly wrong:
# each phrase is asked as a contrast against the other one plus the neutral, and
# the two scenes are cued in the order the phrases were given. See host/cue.py
# for why the cue matters - it is not the timing, it is that the segment
# boundary ends up recorded rather than guessed at afterwards.
#
# M21 adds --enrol, and it changes what the neutral is for again - this time by
# making it unnecessary. With --enrol the board is SHOWN each scene once and
# decides the rest of the run by nearest reference, so the gate is not a query
# any more, it is the frame's mean z; the neutral can be given or left out and
# the presence stage works either way. Run it both ways on one scene to compare:
#
#     ./ab.sh "an opened book" "a closed book" "a book"            # M20
#     ./ab.sh "an opened book" "a closed book" --enrol             # M21
#
# The first visit to each scene teaches the board and every later visit is held
# out, which is what --repeat was already producing and nothing was using.
#
# Extra flags go through to host/cue.py, so --hold 200 and --quiet work here.
# Flags that belong to host/demo.py rather than cue.py need the = form -
# --snap-every=15, not --snap-every 15 - because cue.py's queries are positional
# and argparse would hand the 15 to them. cue.py says so if you forget.
set -eu

cd "$(dirname "$0")"

if [ $# -lt 2 ]; then
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi

A=$1
B=$2
shift 2

# A bare "$3" that starts with a dash is a flag, not the neutral.
N=""
case "${1-}" in
    -*|"") ;;
    *) N=$1; shift ;;
esac

# --enrol sends the phrases BARE, and the 2026-08-11 06:41 bench is why.
#
# The contrast form "A / B" is A minus B in the text encoder, and M21 subtracts
# the mean of the queries at the score level. For two phrases those are the same
# operation done twice, and the second one is then an identity: "an opened
# book~" and "a closed book~" come out exact negatives of each other, so their
# mean is 0 on EVERY frame. lvl+0.00 was the only value in 300 frames. The
# presence axis had nothing to measure and the centring subtracted nothing, so
# the run tested one of M21's two claims and silently skipped the other.
#
# Bare is not a downgrade here, it is what M21 replaces the contrast form with -
# and across N queries rather than two, without spending a slot on the neutral.
# tools/probe_rule.py measures 84.2% for it against 44.2% uncentred on the same
# frames, which is the difference this switch exists to keep.
GATE=""
case " $* " in
    *" --enrol "*)
        QA="$A"
        QB="$B"
        if [ -n "$N" ]; then
            echo "ab.sh: --enrol given, so '$N' is not used - M21's gate is the"
            echo "       frame's mean z, not a query. Dropping it." >&2
        fi
        ;;
    *)
        if [ -n "$N" ]; then
            QA="$A / $B / $N"
            QB="$B / $A / $N"
            GATE="--gate=$N"          # the = form, for the reason in the header
        else
            QA="$A / $B"
            QB="$B / $A"
        fi
        ;;
esac

# The board-present check and the uhubctl recovery used to be here, matching
# /dev/cu.usbmodem* - which the Tiliqua on this hub satisfies whether or not the
# board is plugged in, so it never fired. host/cue.py does it now against USB
# VID 2E8A, still before the minute of teacher loading, and recovers rather than
# advising. One copy, and one that can fail.

exec uv run host/cue.py --scene "$A" --scene "$B" ${GATE:+"$GATE"} "$@" \
     "$QA" "$QB"
