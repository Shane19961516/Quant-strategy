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
RISK_PER_TRADE = 0.0025
MAX_WEIGHT = 1.5
STOP_ATR_MULT = 2.0
TRAIL_ATR_MULT = 3.0
TIME_STOP_BARS = 24
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
