#!/bin/sh
# Runs both scenarios on boot and prints each decoded trace between markers on the console, so the
# host can capture them from QEMU's serial output without networking.
sleep 3
for mode in healthy regressed; do
  /system/deadline/run-scenario.sh $mode > /tmp/run-$mode.log 2>&1
  echo "===DEADLINE-TRACE-BEGIN $mode==="
  cat /tmp/trace-$mode.txt
  echo "===DEADLINE-TRACE-END $mode==="
done
echo "===DEADLINE-DONE==="
