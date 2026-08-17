#!/bin/sh
# $1 = tag, $2 = n runs
cd /Users/kaz/Documents/GitHub/fpga-open-vocab
i=1
while [ $i -le $2 ]; do
  if ! uhubctl 2>/dev/null | grep -q "2e8a:0009"; then
    if uhubctl 2>/dev/null | grep -q "2e8a:000f"; then
      picotool reboot >/dev/null 2>&1
    else
      uhubctl -l 2-1 -p 1 -a cycle >/dev/null 2>&1
    fi
    sleep 10
  fi
  out=/tmp/bench_${1}_$i.log
  /Users/kaz/.local/bin/uv run host/demo.py --port /dev/cu.usbmodem21101 --frames 200 --out $out >/dev/null 2>&1
  echo "--- $1 run $i ---"
  grep -h "stopped   :\|hang      :\|camera    : live" $out 2>/dev/null || echo "  (no summary)"
  grep -c "^frame" $out 2>/dev/null | sed 's/^/  frame lines: /'
  tail -1 $out | sed 's/^/  last: /'
  i=$((i+1))
done
