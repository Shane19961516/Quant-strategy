"""A-priori parameters for the BTC–ETH perp residual z-score book.

These are locked from the research-workflow defaults, not fit on the
last-month window. Leverage is a margin constraint, not part of the signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PACKAGE_DIR / "cache"
RAW_DIR = CACHE_DIR / "raw"

VENUE = "binance_usdtm"
VENUE_NAME = "Binance USDⓈ-M futures"
VISION_BASE = "https://data.binance.vision/data/futures/um"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL = "1m"

# Binance UM VIP0 taker; research doc example was 4bp — we use the live VIP0 schedule.
TAKER_FEE_BPS = 5.0
# Historical BBO is not on data.binance.vision; conservative half-spreads.
HALF_SPREAD_BPS = {"BTCUSDT": 0.5, "ETHUSDT": 1.0}
IMPACT_K = 1.0
IMPACT_CAP_BPS = 10.0
ADV_PARTICIPATION = 0.005  # 0.5% of bar quote volume, entries only

LEVERAGE = 10.0
MMR = 0.004  # Binance UM BTC/ETH tier-1 maintenance, small notional
LIQUIDATION_EXTRA_BPS = 5.0
QTY_STEP = {"BTCUSDT": 0.001, "ETHUSDT": 0.001}
TICK_SIZE = {"BTCUSDT": 0.1, "ETHUSDT": 0.01}

BETA_WINDOW = 240
Z_WINDOW = 1440
CORR_WINDOW = 60
CORR_MIN = 0.50
ENTRY_Z = 2.0
EXIT_Z = 0.50
STOP_Z = 4.0
MAX_HOLD_BARS = 240
FUNDING_BLACKOUT_MIN = 2
GAP_FREEZE_MINUTES = 2

STARTING_EQUITY = 100_000.0
INTEREST_PER_8H = 0.0001  # Binance default interest used in funding formula


@dataclass(frozen=True)
class BacktestConfig:
    leverage: float = LEVERAGE
    taker_fee_bps: float = TAKER_FEE_BPS
    starting_equity: float = STARTING_EQUITY
    entry_z: float = ENTRY_Z
    exit_z: float = EXIT_Z
    stop_z: float = STOP_Z
    beta_window: int = BETA_WINDOW
    z_window: int = Z_WINDOW
    corr_window: int = CORR_WINDOW
    corr_min: float = CORR_MIN
    max_hold_bars: int = MAX_HOLD_BARS
    mmr: float = MMR
    impact_k: float = IMPACT_K
    adv_participation: float = ADV_PARTICIPATION
    exec_mode: str = "open"  # open | pessimistic
    invert_signal: bool = False  # flip spread side; |z| thresholds unchanged
