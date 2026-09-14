#!/bin/sh

LOG="/mnt/us/kpm-local-install.log"

exec > "$LOG" 2>&1

echo "===== KPM local install start ====="
date

echo "Checking package:"
ls -l /mnt/us/koreader.kpkg

export LD_LIBRARY_PATH="/var/local/kmc/kindlehf/lib:$LD_LIBRARY_PATH"

echo "Running KPM..."
/var/local/kmc/kindlehf/bin/kpm -d -y install "file:///mnt/us/koreader.kpkg"

RET=$?

echo "KPM exit code: $RET"
echo "===== finished ====="

sync