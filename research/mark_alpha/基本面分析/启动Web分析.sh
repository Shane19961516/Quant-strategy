#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "========================================"
echo "  基本面分析 Web"
echo "  http://127.0.0.1:8765"
echo "========================================"
python3 -m pip install -r requirements.txt -q
( sleep 1; command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:8765" || open "http://127.0.0.1:8765" 2>/dev/null || true ) &
python3 app.py
