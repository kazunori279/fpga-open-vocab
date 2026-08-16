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
# Point the camera before spending ten minutes on a run:
#
#     ./ab.sh "an opened book" "a closed book" --frame-check
#
# That measures nothing. It runs the camera, keeps /tmp/fgx_preview.png showing
# the newest frame it captured, and stops on Ctrl-C - so a scene that is half
# out of frame, or too dark, or barely different from the empty desk, is found
# in twenty seconds instead of afterwards. --preview N does the same during a
# real run, every N frames, and with --enrol a picture of each enrolment window
# is kept beside the log whether or not you ask.
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
# any more: since #18 it is how FAR the nearest reference is, and "nothing there"
# means further than 2 sep from all of them. The neutral can be given or left out
# and the presence stage works either way. Run it both ways on one scene:
#
#     ./ab.sh "an opened book" "a closed book" "a book"            # M20
#     ./ab.sh "an opened book" "a closed book" --enrol             # M21
#
# The first visit to each scene teaches the board and every later visit is held
# out, which is what --repeat was already producing and nothing was using.
#
# The rotation also goes back to an EMPTY desk once per cycle, after the classes.
# Take everything out when it says so, and put the objects back where they were -
# see below, because that turned out to matter. That segment is what measures the
# presence stage, the half of the rule that answers "nothing there", and it could
# not be measured before: the only empty scene in the schedule was the one the
# rule learns "empty" from. --no-revisit-empty drops it.
#
# It was measured twice and the old level-based stage held 17.8% and 24.4% of
# those frames. #18 replaced it with a distance, and replaying that on the same
# two logs gives 90.0% and 87.8% - but that is offline, and THIS SCRIPT IS HOW
# THE BOARD GETS TO SAY SO. Watch the d column in the frame line: it is the
# distance to the nearest reference in sep, the quantity the gate cuts.
#
# Meanwhile the re-staging this rotation forces appears to have cost the STATE
# stage too: 58% held out on both runs against 120/120 on 2026-08-11, when the
# objects plausibly never moved between visits. That is #19, and
# --no-revisit-empty is the control that settles it - run both in one session.
#
# Extra flags go through to host/cue.py, so --hold 200 and --quiet work here.
# Flags that belong to host/demo.py rather than cue.py need the = form -
# --snap-every=15, not --snap-every 15 - because cue.py's queries are positional
# and argparse would hand the 15 to them. cue.py says so if you forget.
set -eu

cd "$(dirname "$0")"

if [ $# -lt 2 ]; then
    sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
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
            echo "ab.sh: --enrol given, so '$N' is not used - since #18 the gate"
            echo "       is the distance to the nearest enrolled reference, not"
            echo "       a query. Dropping it." >&2
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
