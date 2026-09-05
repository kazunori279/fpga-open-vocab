#!/bin/sh
# The sixteen runs of 20260906-camlock-cold, in the order the pre-registration
# fixed: eight pairs, arms alternating, the LEADING ARM SWAPPING EVERY PAIR so
# the arm is not confounded with position inside a pair.
#
# Nothing in here decides anything. The design is in README.md and was committed
# before this script ran; this only executes it and writes down when each run
# started, because the tertiary test is a slope against position in the session
# and "position" has to mean wall clock and not filename order.
#
# NOT INTERRUPTIBLE BY pkill. Ctrl-C is the way out; a killed demo.py leaves the
# board mid-frame and the next run inherits it.

cd /Users/kaz/Documents/GitHub/fpga-open-vocab || exit 1
UV=/Users/kaz/.local/bin/uv
DIR=bench/soak/20260906-camlock-cold
SESSION=$DIR/session.log
FRAMES=600

: > "$SESSION"

run_one() {
  arm=$1        # free | lock
  n=$2
  log=$DIR/$arm-$n.log
  start=$(date +%H:%M:%S)
  printf '%s  start  %s-%s\n' "$start" "$arm" "$n" | tee -a "$SESSION"

  if [ "$arm" = lock ]; then
    $UV run host/demo.py "a closed book" "an opened book" \
        --no-smooth --frames "$FRAMES" --enrol=40:L --leave-running \
        --out "$log" >/dev/null 2>&1
  else
    $UV run host/demo.py "a closed book" "an opened book" \
        --no-smooth --frames "$FRAMES" --leave-running \
        --out "$log" >/dev/null 2>&1
  fi

  # Provenance and the mechanical-failure check in one place. The pre-registered
  # discard rule is failures only, so what is wanted here is a flag and never a
  # number: anything printed is a reason to re-run at the END of the session.
  rgb=$(grep -oE 'mean RGB +[0-9]+ +[0-9]+ +[0-9]+' "$log" | tail -1)
  lines=$(grep -cE '^ *[0-9]+ ' "$log" 2>/dev/null || echo 0)
  printf '%s  end    %s-%s  frames=%s  %s\n' \
      "$(date +%H:%M:%S)" "$arm" "$n" "$lines" "$rgb" | tee -a "$SESSION"
  for bad in 'EXPOSURE NEVER SETTLED' 'camera bus' 'link fault' 'USB'; do
    if grep -qi "$bad" "$log"; then
      printf '  !! %s-%s hit "%s" - mechanical, re-run at end\n' \
          "$arm" "$n" "$bad" | tee -a "$SESSION"
    fi
  done
}

p=1
while [ "$p" -le 8 ]; do
  if [ $(( p % 2 )) -eq 1 ]; then
    run_one free "$p"; run_one lock "$p"
  else
    run_one lock "$p"; run_one free "$p"
  fi
  p=$(( p + 1 ))
done

printf '%s  session complete\n' "$(date +%H:%M:%S)" | tee -a "$SESSION"
