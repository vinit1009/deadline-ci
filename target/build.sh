#!/bin/bash
# Builds the demo programs and a QNX OS 8.0 QEMU image (x86_64) that runs the scenario on boot.
# Run on a Linux x86_64 host with QNX SDP 8.0 installed (needs qcc and mkqnximage):
#   source ~/qnx800/qnxsdp-env.sh
#   target/build.sh [image-dir]           # default image dir: ../qnx-image
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${1:-$REPO/../qnx-image}
: "${QNX_TARGET:?source qnxsdp-env.sh first}"

mkdir -p "$REPO/scenario/build/x86_64" "$IMAGE"
for p in deadline-demo deadline-client ai-worker; do
  qcc -Vgcc_ntox86_64 -O2 -Wall -Wextra -o "$REPO/scenario/build/x86_64/$p" "$REPO/scenario/$p.c"
done

# mkqnximage reads local/snippets from the image directory; point the file list at this checkout.
rm -rf "$IMAGE/local"
mkdir -p "$IMAGE/local/snippets"
sed "s|/work/deadline-ci|$REPO|g" "$REPO/scenario/image/local/snippets/system_files.custom" > "$IMAGE/local/snippets/system_files.custom"
cp "$REPO/scenario/image/local/snippets/post_start.custom" "$IMAGE/local/snippets/"
cd "$IMAGE"
mkqnximage --type=qemu --arch=x86_64 --cpu=1 --ram=1G --hostname=deadline-demo --noprompt --force --build
echo "Image ready in $IMAGE/output (ifs.bin, disk-qemu.vmdk). Next: target/run-qemu.sh $IMAGE"
