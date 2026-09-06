#!/bin/sh
# The sixteen runs of 20260907-camlock-cold, in the order the pre-registration
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
DIR=bench/soak/20260907-camlock-cold
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
  # THE FRAME COUNT WAS NEVER THE FRAME COUNT. This read
  #   grep -cE '^ *[0-9]+ ' "$log"
  # which matches no frame line at all - every one of them begins with the word
  # `frame` - and instead matched the three indented timing lines under
  # `stopped :`. 20260906 duly recorded frames=3 for a run of 602, and nobody
  # looked because the run was being aborted for other reasons. Take the board's
  # own count, which also says how many of them were good.
  lines=$(grep -oE '[0-9]+ frames, [0-9]+ good' "$log" | tail -1)
  # No `stopped :` line means the run did not reach its own summary. Say that in
  # the field rather than printing a plausible number for a truncated run.
  [ -n "$lines" ] || lines="$(grep -c '^frame ' "$log") frames, NO stopped LINE"
  # For #32, and an OBSERVATION WITH NO TEST BEHIND IT - see the README. The
  # firmware prints this only when #33's ramp rescue actually fired, so an
  # absent field means zero and not "not measured". Sixteen cold boots is the
  # first sample of what the rescue is worth on a board that has not been
  # running all day.
  relit=$(grep -oE 'the auto loops were switched back on [0-9]+ time' "$log" \
          | grep -oE '[0-9]+' | tail -1)
  printf '%s  end    %s-%s  frames=%s  %s  relit=%s\n' \
      "$(date +%H:%M:%S)" "$arm" "$n" "$lines" "$rgb" "${relit:-0}" | tee -a "$SESSION"
  # MATCH THE FAILING FORM AND NOT THE TOPIC. The first version of this grepped
  # for 'camera bus' and 'USB', which the banner prints unconditionally as
  # "camera bus: worst gap 14 us against the 2000 us deadline" and "usb: 0
  # outages" - so every run flagged three mechanical failures and the flag meant
  # nothing. What is wanted is the non-zero case of each.
  # BOTH OF ft_acquire()'s DOUBTS AND NOT JUST THE LOUD ONE. This grepped only
  # for EXPOSURE NEVER SETTLED, which is the case where the ramp also failed to
  # reach the floor. The quieter case - the ramp climbed enough to satisfy the
  # settle test but never MOVED from its first reading - prints a different
  # sentence, and it is the one two of the three flagged benches in bench/cue/
  # carry. Missing it here is how 20260816-172256 got scored.
  grep -qE 'EXPOSURE NEVER SETTLED|the exposure never moved from its first reading' "$log" &&
    printf '  !! %s-%s the board distrusted its own camera (#33)\n' "$arm" "$n" | tee -a "$SESSION"
  grep -qE 'usb: [1-9][0-9]* outages' "$log" &&
    printf '  !! %s-%s USB outage (#9)\n' "$arm" "$n" | tee -a "$SESSION"
  grep -qiE 'camera bus: .*(stall|fault|missed|deadline exceeded)' "$log" &&
    printf '  !! %s-%s camera bus fault (#8, #12)\n' "$arm" "$n" | tee -a "$SESSION"
  true
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
