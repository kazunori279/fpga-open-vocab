#!/bin/sh
# The issue #9 soak harness.  $1 = tag, $2 = n runs, $3 = frames per run.
#
# NOT bench_loop.sh, which is checked in unmodified as the thing that produced
# the 2026-08-15 archive and hard-codes both `/dev/cu.usbmodem21101` and
# `uhubctl -l 2-1 -p 1`.  Both of those were wrong within a day.  Here:
#
#   - the CDC device is never named.  demo.py picks it by VID, which is the
#     only identification that survives a neighbour being unplugged.
#   - the hub port is looked up ONCE, HERE, WHILE THE BOARD IS STILL THERE,
#     and passed to bootsel.py as --hub.  That order is the whole point: the
#     moment the port number is needed - #9's outage - the board is gone from
#     uhubctl's tree and cannot be found by VID any more.  See host/board.py's
#     note_where() for the same reasoning in Python.
#
# Run usb_watch.py beside it, not instead of it.  Without the watcher a
# recurrence is unattributable exactly as it was in August: the only record of
# whether VBUS was still up is the hub's own `power`/`connect` bits, and they
# do not survive the power cycle that is the only known recovery.
#
#   uv run host/usb_watch.py --out /tmp/usb_watch_TAG.log &
#   sh bench/soak/usb_soak.sh TAG 20 200

cd /Users/kaz/Documents/GitHub/fpga-open-vocab || exit 1
UV=/Users/kaz/.local/bin/uv
TAG=$1
RUNS=${2:-20}
FRAMES=${3:-200}

# Where the board is, taken now rather than written down.  Format: "2-1:2".
WHERE=$(uhubctl 2>/dev/null | awk '
  /Current status for hub/ { hub = $5 }
  /2e8a:/                  { gsub(/:/, "", $2); print hub ":" $2; exit }')
if [ -z "$WHERE" ]; then
  echo "the board is not on the bus - nothing to soak" >&2
  exit 1
fi
echo "### soak $TAG: $RUNS x $FRAMES frames, board at $WHERE"

i=1
while [ $i -le "$RUNS" ]; do
  if ! uhubctl 2>/dev/null | grep -q "2e8a:0009"; then
    if uhubctl 2>/dev/null | grep -q "2e8a:000f"; then
      echo "    (in BOOTSEL, nudging)"
      picotool reboot >/dev/null 2>&1
    else
      echo "    (off the bus, cutting VBUS at $WHERE)"
      $UV run host/bootsel.py --power-cycle --run --hub "$WHERE" >/dev/null 2>&1
    fi
    sleep 10
  fi
  out=/tmp/soak_${TAG}_$i.log
  $UV run host/demo.py "an opened book" "a closed book" \
      --frames "$FRAMES" --out "$out" >/dev/null 2>&1
  echo "--- $TAG run $i/$RUNS  $(date +%H:%M:%S) ---"
  # The three flags at the end are the ones a run can be thrown out by without
  # re-reading its banner, which is exactly what runs 8-20 of 20260820-usb-p2
  # needed and did not have: they scored a black picture for twelve runs while
  # the only warning sat nine lines into a banner this loop never printed.
  # Anchored to the summary's twelve-space indent, because "scene: " unanchored
  # also matches ft_acquire()'s own "tuned camera on a neutral scene:" note.
  grep -hE "stopped   :|hang      :|usb: |^ {12}(scene|enrolment|lastwords): " \
      "$out" 2>/dev/null \
    || echo "  (no summary - the run did not finish)"
  grep -c "^frame" "$out" 2>/dev/null | sed 's/^/  frame lines: /'
  grep -h "^\[host\]" "$out" 2>/dev/null | sed 's/^/  /'
  i=$((i + 1))
done
echo "### soak $TAG done"
