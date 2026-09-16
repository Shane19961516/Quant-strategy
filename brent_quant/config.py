"""Phase-1 defaults for the Brent volume-price system."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PACKAGE_DIR / "reports"

TICKER = "BZ=F"
CONTRACT = "BZ=F"
INTERVAL = "5m"
PERIOD = "60d"
BAR_MINUTES = 5

# Internal store is always UTC; display conversion happens in reports.
TIMEZONE = "UTC"
SOURCE_TIMEZONE = "America/New_York"

# ICE Brent: $0.01 tick, 1,000 barrels.
TICK_SIZE = 0.01
MULTIPLIER = 1000.0
COMMISSION_PER_SIDE = 2.50
SLIPPAGE_TICKS = 1
INITIAL_NAV = 1_000_000.0
RISK_PER_TRADE = 0.0035
MAX_LEVERAGE = 3.0
ATR_PERIOD = 14
STOP_ATR_MULT = 2.0
TRAIL_ATR_MULT = 3.0
TIME_STOP_BARS = 48
EXTREME_RETURN_Z = 5.0
ABNORMAL_RETURN = 0.10

BREAKOUT_N = 24
ER_N = 24
ER_THRESHOLD = 0.50
VOLUME_MA = 24
VOLUME_RATIO_THRESHOLD = 1.2
VOLUME_Z_N = 60
VOLUME_MOM_N = 20
SMA_N = 48

SCORE_FULL = 60.0
SCORE_HALF = 30.0

BREAKOUT_GRID = (12, 24, 36, 48, 72)
ER_GRID = (12, 24, 48, 72)
ER_THRESH_GRID = (0.30, 0.40, 0.50, 0.60)
VOLUME_MA_GRID = (12, 24, 48, 72)
VOLUME_RATIO_GRID = (1.0, 1.2, 1.5, 2.0)
SLIPPAGE_GRID = (0, 1, 2, 3)
COST_MULT_GRID = (1.0, 2.0, 3.0)

MODELS = ("A", "B", "C", "D", "E", "SCORE")

# ~23h session * 12 bars/hour * 252 sessions, used only as a fallback.
BARS_PER_YEAR = 252 * 23 * 12
RANDOM_SEED = 42
MONTE_CARLO_PATHS = 1000
