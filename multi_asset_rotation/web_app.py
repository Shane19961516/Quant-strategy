#!/usr/bin/env python3
"""启动策略 Web 控制台。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import app
from web.services import ENGINE


def main(host: str = "0.0.0.0", port: int = 8080):
    print("Loading strategy engine...")
    snap = ENGINE.refresh(force_download=False)
    print(
        f"Ready. ann={snap.stats.get('ann_return', 0):.2%} "
        f"sharpe={snap.stats.get('sharpe_rf0', 0):.3f} "
        f"mdd={snap.stats.get('max_drawdown', 0):.2%}"
    )
    print(f"Open http://127.0.0.1:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    main(host=args.host, port=args.port)
