#!/usr/bin/env bash
# 本地/云主机 cron 示例（中国时区）
# 建议：交易日 19:00 发送（收盘后跑策略并推送盈亏/周五调仓目标）
# crontab -e 添加：
#   0 19 * * 1-5 cd /path/to/multi_asset_rotation && /usr/bin/bash scripts/run_daily_notify.sh >> output/daily_notify_cron.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/.."

# 可选：从 .env 加载（勿把含密钥的 .env 提交到 git）
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export TZ=Asia/Shanghai
export NOTIFY_FORCE_DOWNLOAD="${NOTIFY_FORCE_DOWNLOAD:-1}"

python3 daily_notify.py
