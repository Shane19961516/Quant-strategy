"""UPV Engine: Universal Price-Volume 配置与品种元数据。"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
METADATA_DIR = DATA_DIR / "metadata"
REPORTS_DIR = PACKAGE_DIR / "reports"

INTERVAL = "1h"
PERIOD = "730d"
BAR_MINUTES = 60

INITIAL_NAV = 1_000_000.0
# 单笔风险从 0.25% 提到 1.0%，让仓位真正吃到趋势，而不是靠改信号。
RISK_PER_TRADE = 0.01
MAX_WEIGHT = 2.5
STOP_ATR_MULT = 2.0
TRAIL_ATR_MULT = 3.0
# 1h 上 24 根把趋势砍得太短；72 根约 3 个交易日，交给 ATR 跟踪止盈。
TIME_STOP_BARS = 72
# 等风险组合：按日历年化 27% 反推杠杆，波动上限 12%，避免周末 BTC 把 252 日年化算歪。
TARGET_PORT_CAGR = 0.27
TARGET_PORT_VOL = 0.12
MAX_PORT_LEVERAGE = 8.0
SCORE_FULL = 60.0
SCORE_HALF = 30.0
RANDOM_SEED = 42

ER_N = 24
BREAKOUT_N = 24
VOL_N = 24
VOLUME_MA = 24
SLOPE_N = 24
MOM_N = 24

ER_TREND = 0.50
ER_RANGE = 0.30
MOM_TREND = 0.50
VOL_Z_SHOCK = 2.5
RET_Z_SHOCK = 3.0
VOLUME_Z_SHOCK = 2.5
VOLUME_RATIO_TH = 1.2
RANGE_LONG = 0.15
RANGE_SHORT = 0.85
SHOCK_SIZE = 0.35
TRANSITION_SIZE = 0.50

MVP_SYMBOLS = ("SPY", "QQQ", "BZ=F", "GC=F", "BTC-USD")
MODELS = ("A", "B", "C", "D", "E", "SCORE")

PAPER_DIR = DATA_DIR / "paper"
PAPER_STATE_PATH = PAPER_DIR / "account.json"
PAPER_MODEL = "B"
PAPER_PERIOD = "60d"
PAPER_LOOKBACK_BARS = 80
PAPER_GROSS_CAP = 3.0
DAILY_LOSS_LIMIT = 0.03
PORT_DD_LIMIT = 0.08
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8050
DATA_CACHE_SEC = 180

INSTRUMENTS = {
    "SPY": {
        "name": "SPY",
        "asset_class": "equity",
        "cluster": "Equity",
        "tick_size": 0.01,
        "multiplier": 1.0,
        "commission_bps": 1.0,
        "volume_type": "shares",
        "timezone": "America/New_York",
    },
    "QQQ": {
        "name": "QQQ",
        "asset_class": "equity",
        "cluster": "Equity",
        "tick_size": 0.01,
        "multiplier": 1.0,
        "commission_bps": 1.0,
        "volume_type": "shares",
        "timezone": "America/New_York",
    },
    "BZ=F": {
        "name": "Brent",
        "asset_class": "energy_fut",
        "cluster": "Energy",
        "tick_size": 0.01,
        "multiplier": 1000.0,
        "commission_bps": 1.5,
        "volume_type": "contracts",
        "timezone": "America/New_York",
    },
    "GC=F": {
        "name": "Gold",
        "asset_class": "metal_fut",
        "cluster": "Metals",
        "tick_size": 0.10,
        "multiplier": 100.0,
        "commission_bps": 1.5,
        "volume_type": "contracts",
        "timezone": "America/New_York",
    },
    "BTC-USD": {
        "name": "BTC",
        "asset_class": "crypto",
        "cluster": "Crypto",
        "tick_size": 0.01,
        "multiplier": 1.0,
        "commission_bps": 4.0,
        "volume_type": "coins",
        "timezone": "UTC",
    },
}


def spec_for(symbol: str) -> dict:
    if symbol in INSTRUMENTS:
        return dict(INSTRUMENTS[symbol])
    return {
        "name": symbol,
        "asset_class": "unknown",
        "cluster": "Other",
        "tick_size": 0.01,
        "multiplier": 1.0,
        "commission_bps": 2.0,
        "volume_type": "unknown",
        "timezone": "UTC",
    }
