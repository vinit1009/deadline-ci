#!/bin/sh
# Runs the demo on the QNX target and captures 2 seconds of kernel events.
#   run-scenario.sh healthy    ai-worker below the control loop (priority 10, 30% duty)
#   run-scenario.sh regressed  ai-worker above it (priority 40, 90% duty): the "bad config" change
set -e
case "$1" in
  healthy)   WORKER="-p 10 -d 30" ;;
  regressed) WORKER="-p 40 -d 90" ;;
  *) echo "usage: $0 healthy|regressed" >&2; exit 2 ;;
esac
cd "$(dirname "$0")"
export PATH=/system/bin:$PATH
./deadline-demo &
sleep 1
./deadline-client &
./ai-worker $WORKER &
sleep 1
tracelogger -n 0 -s 2 -f /tmp/trace-$1.kev
slay -f deadline-demo deadline-client ai-worker 2>/dev/null || true
traceprinter -n -p "c:0x%016c CPU:%02C %-16Z:%-18z" -f /tmp/trace-$1.kev > /tmp/trace-$1.txt
echo "wrote /tmp/trace-$1.txt"
