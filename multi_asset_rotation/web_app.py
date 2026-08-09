#!/usr/bin/env python3
"""启动策略 Web 控制台（可选公网隧道）。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import app
from web.services import ENGINE


def _start_cloudflared(port: int) -> subprocess.Popen | None:
    bin_path = shutil.which("cloudflared")
    candidates = [bin_path, "/tmp/cloudflared"]
    cf = next((c for c in candidates if c and Path(c).exists()), None)
    if not cf:
        print("[warn] cloudflared 不存在，跳过公网隧道。可先下载到 /tmp/cloudflared")
        return None
    log_path = ROOT / "output" / "cloudflared.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=logf,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _watch():
        url = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                txt = log_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line in txt.splitlines():
                if "trycloudflare.com" in line and "https://" in line:
                    # extract url
                    for token in line.replace("|", " ").split():
                        if token.startswith("https://") and "trycloudflare.com" in token:
                            url = token.strip()
                            break
                if url:
                    break
            if url:
                print("\n" + "=" * 64)
                print("公网访问地址（Cloudflare Tunnel）:")
                print(f"  {url}")
                print("  研究报告:  {}/research".format(url))
                print("  指标监控:  {}/monitor".format(url))
                print("  收益预计:  {}/forecast".format(url))
                print("=" * 64 + "\n")
                (ROOT / "output" / "public_url.txt").write_text(url + "\n", encoding="utf-8")
                return
        print("[warn] 未能解析公网 URL，请查看 output/cloudflared.log")

    threading.Thread(target=_watch, daemon=True).start()
    return proc


def main(host: str = "0.0.0.0", port: int = 8080, public: bool = True):
    print("Loading strategy engine...")
    snap = ENGINE.refresh(force_download=False)
    print(
        f"Ready. ann={snap.stats.get('ann_return', 0):.2%} "
        f"sharpe={snap.stats.get('sharpe_rf0', 0):.3f} "
        f"mdd={snap.stats.get('max_drawdown', 0):.2%}"
    )
    print(f"Local:  http://127.0.0.1:{port}")
    print(f"        http://127.0.0.1:{port}/research")
    print(f"        http://127.0.0.1:{port}/monitor")
    print(f"        http://127.0.0.1:{port}/forecast")

    tunnel_proc = None
    if public:
        tunnel_proc = _start_cloudflared(port)

    try:
        app.run(host=host, port=port, debug=False)
    finally:
        if tunnel_proc and tunnel_proc.poll() is None:
            tunnel_proc.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--public", action="store_true", default=True, help="启用 Cloudflare 临时公网隧道（默认开）")
    ap.add_argument("--no-public", action="store_true", help="仅本地监听，不创建公网隧道")
    args = ap.parse_args()
    main(host=args.host, port=args.port, public=(not args.no_public))
