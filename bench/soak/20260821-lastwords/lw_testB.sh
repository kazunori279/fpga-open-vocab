#!/bin/sh
cd /Users/kaz/Documents/GitHub/fpga-open-vocab || exit 1
UV=/Users/kaz/.local/bin/uv
$UV run host/demo.py "an opened book" "a closed book" \
    --frames 60 --usb-drop-hard 20 --out /tmp/lw_testB2.log > /tmp/lw_testB2.host 2>&1 &
DEMO=$!
i=0
while [ $i -lt 300 ]; do
  ls /dev/cu.usbmodem21201 >/dev/null 2>&1 || break
  sleep 1; i=$((i+1))
done
echo "### off the bus at $(date +%H:%M:%S) after ${i}s"
sleep 8
echo "### cutting VBUS at $(date +%H:%M:%S) - uhubctl only, exactly one boot after it"
uhubctl -l 2-1 -p 2 -a cycle -d 3 2>&1 | sed 's/^/    /'
wait $DEMO
echo "### demo.py exited $?  at $(date +%H:%M:%S)"
