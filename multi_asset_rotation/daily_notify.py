#!/usr/bin/env python3
"""云端可定时运行的每日策略微信/推送日报。

用法：
  python daily_notify.py                 # 生成并尝试推送
  python daily_notify.py --dry-run       # 只生成不推送
  python daily_notify.py --force-download

必要环境变量（至少一个推送渠道）：
  PUSHPLUS_TOKEN      # 推荐：pushplus.plus 微信推送 token
  SERVERCHAN_SENDKEY  # 可选：Server酱 SendKey
  WECOM_WEBHOOK       # 可选：企业微信群机器人 webhook

可选：
  WECHAT_PHONE        # 仅用于消息抬头展示（默认空；勿把隐私提交进仓库）
  PUSHPLUS_CHANNEL    # wechat|sms|mail|cp，默认 wechat
  NOTIFY_FORCE_DOWNLOAD=1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notify.push import push_message
from notify.report import build_daily_report

OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily strategy WeChat/notify job")
    ap.add_argument("--dry-run", action="store_true", help="generate only, do not push")
    ap.add_argument("--force-download", action="store_true", help="refresh market data")
    ap.add_argument("--text", action="store_true", help="push plain text instead of html")
    args = ap.parse_args()

    force = args.force_download or os.environ.get("NOTIFY_FORCE_DOWNLOAD", "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
    }
    phone = os.environ.get("WECHAT_PHONE", "").strip()

    print(f"[daily_notify] building report force_download={force}", flush=True)
    report = build_daily_report(force_download=force, phone_hint=phone)

    (OUT / "daily_notify_latest.json").write_text(
        json.dumps(report.payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (OUT / "daily_notify_latest.txt").write_text(report.text, encoding="utf-8")
    (OUT / "daily_notify_latest.html").write_text(report.html, encoding="utf-8")
    print(report.text, flush=True)
    print(f"[daily_notify] saved -> {OUT / 'daily_notify_latest.txt'}", flush=True)

    if args.dry_run:
        print("[daily_notify] dry-run: skip push", flush=True)
        return 0

    content = report.text if args.text else report.html
    ctype = "txt" if args.text else "html"
    result = push_message(report.title, content, content_type=ctype)
    (OUT / "daily_notify_push_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("[daily_notify] push result:", json.dumps(result, ensure_ascii=False), flush=True)

    # 若完全未配置渠道，非零退出，便于 CI 发现
    if "error" in result and len(result) == 1:
        return 2

    def _channel_ok(v: object) -> bool:
        if not isinstance(v, dict):
            return False
        body = v.get("body")
        # PushPlus: code=200 成功；Server酱: code=0 成功
        if isinstance(body, dict) and "code" in body:
            return body.get("code") in (0, 200, "0", "200")
        return bool(v.get("ok"))

    oks = [_channel_ok(v) for k, v in result.items() if k != "error"]
    if not oks or not any(oks):
        # 常见：PushPlus 905=未实名
        print("[daily_notify] push failed (check token / real-name / channel)", flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
