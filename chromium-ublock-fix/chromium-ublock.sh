#!/bin/sh
# Chromium 升级后修复完整版 uBlock Origin；依赖文件随本目录分发。
# 如提示浏览器正在运行，请先完全退出 Chromium 后重试；无需 sudo。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
exec python3 "$SCRIPT_DIR/repair.py" "$@"
