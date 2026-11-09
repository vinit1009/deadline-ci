#!/bin/bash
# Boots the image in QEMU, lets the scenario run (healthy, then regressed), and saves both traces.
#   target/run-qemu.sh [image-dir] [traces-dir]
# QEMU runs with -icount shift=0,sleep=off: the guest's clock and its cycle counter come from one
# virtual clock, so timing is deterministic and free of host noise (emulated time, not wall time).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${1:-$REPO/../qnx-image}
OUT=${2:-$IMAGE/traces}
mkdir -p "$OUT"
LOG="$OUT/serial.log"
rm -f "$LOG"
qemu-system-x86_64 -pidfile "$OUT/qemu.pid" -display none -monitor none -serial "file:$LOG" \
  -m 1G -smp 1 -cpu max -icount shift=0,sleep=off \
  -drive "file=$IMAGE/output/disk-qemu.vmdk,if=ide,id=drv0" -kernel "$IMAGE/output/ifs.bin" &
for _ in $(seq 1 150); do
  grep -q "===DEADLINE-DONE===" "$LOG" 2>/dev/null && break
  sleep 2
done
kill "$(cat "$OUT/qemu.pid")" 2>/dev/null || true
grep -q "===DEADLINE-DONE===" "$LOG" || { echo "the scenario did not finish; see $LOG" >&2; exit 1; }
for mode in healthy regressed; do
  awk "/===DEADLINE-TRACE-BEGIN $mode===/{f=1;next}/===DEADLINE-TRACE-END $mode===/{f=0}f" "$LOG" | tr -d '\r' > "$OUT/$mode.txt"
  echo "$OUT/$mode.txt: $(wc -l < "$OUT/$mode.txt") lines"
done
echo "Check them: deadline check $OUT/healthy.txt --budgets scenario/deadlines.toml"
