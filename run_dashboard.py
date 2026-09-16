"""Launch the UPV paper dashboard from any working directory.

Windows example:

    py -3 run_dashboard.py

or double-click run_dashboard.bat in the repo root.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    print(f"仓库目录: {ROOT}")
    print(f"Python:   {sys.executable}")
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import yfinance  # noqa: F401
    except ModuleNotFoundError as exc:
        req = ROOT / "universal_quant" / "requirements.txt"
        _die(
            f"缺少依赖: {exc.name}\n"
            f"请先安装:\n  {sys.executable} -m pip install -r \"{req}\""
        )
    try:
        from universal_quant.dashboard import serve
        from universal_quant import config as cfg
    except ModuleNotFoundError as exc:
        _die(
            f"找不到模块 {exc.name}。请在 Quant-strategy 仓库根目录运行本脚本，"
            f"或把该目录加入 PYTHONPATH。\n当前仓库目录: {ROOT}"
        )

    url = f"http://127.0.0.1:{cfg.DASHBOARD_PORT}"
    print(f"浏览器打开 {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    serve(host="127.0.0.1", port=cfg.DASHBOARD_PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
