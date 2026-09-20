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

# Slow cost-aware book. Locked from microstructure economics + the research
# workflow (hold must match a slow factor; 1-min taker cannot support 100+
# trades/month). Not grid-searched on the last-month window.
DELIVERY_Z_WINDOW = 7 * 1440  # 7-day z of log(ETH/BTC)
DELIVERY_BETA_WINDOW = 1440
DELIVERY_CORR_WINDOW = 1440
DELIVERY_CORR_MIN = 0.70
DELIVERY_MAX_HOLD = 5 * 1440
DELIVERY_COOLDOWN = 1440  # 1 day after flatten
DELIVERY_COST_HURDLE_BPS = 30.0  # ~1.25× round-trip taker+spread on two legs
DELIVERY_STRIDE = 1  # entries gated by UTC hour, not a return-fitted stride
DELIVERY_ENTRY_HOUR_UTC = 1  # 01:00 UTC, after 00:00 funding, 7-day factor needs no hourly churn
DELIVERY_LEVERAGE = 2.0
DELIVERY_LEVERAGE_1X = 1.0  # one-knob: same 7d z / 01:00 UTC shell, no extra notional
DELIVERY_ADV = 1.0  # once-a-day clip; 1m ADV% was binding. Impact cap still applies.
# Pre-declared train-only grid on frozen A: z-window days × entry |z|.
# Beta/corr/leverage/hour/exit stay locked. Simulator enters [entry_z, stop_z),
# so |z|≥4 cannot keep stop 4 — stop 5 is declared a priori, not searched.
DELIVERY_Z_WINDOW_DAYS = (3, 7, 14, 30)
DELIVERY_ENTRY_ZS_GRID = (2.0, 2.5, 3.0, 4.0)
DELIVERY_ENTRY_Z_3 = 3.0
DELIVERY_ENTRY_Z_4 = 4.0
DELIVERY_STOP_Z_E4 = 5.0  # E4 / any |z|≥4 band is [4, 5); exit still 0.5
# Train-chosen cell from the window×sigma grid. Leverage sweep only; do not
# reopen windows or |z|. Cross-margin liq is eq < MMR × gross.
CHOSEN_Z_DAYS = 14
CHOSEN_ENTRY_Z = 2.0
DELIVERY_SWEEP_LEVERAGES = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


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
    cooldown_bars: int = 0
    cost_hurdle_bps: float = 0.0
    decision_stride: int = 1
    entry_hour_utc: int | None = None  # if set, new entries only at that UTC hour :00
    require_reversion: bool = False  # extra confirm: |z| shrinking vs 1d ago, same sign
    signal_mode: str = "residual"  # residual | funding_carry | raw_spread | ratio_btc_eth | hedge_residual
    raw_spread_min_periods: int = 30 * 1440  # expanding mean of log(ETH/BTC)
    eth_ticket_usd: float = 0.0  # if >0, ETH notional is ticket (× leverage if flagged)
    notional_times_leverage: bool = True
    min_leg_notional: float = 50.0  # skip entry if either leg notional is below this


# 8h ETH−BTC funding carry (Fork 2). Scale: 1bp of last-settled (ETH−BTC)
# funding → |z|=1, so the frozen |z|∈[2,4) band is a 2–4bp carry gap.
# Cost hurdle is off: the edge is carry, not residual bp.
FUNDING_BP_SCALE = 1e4

# Raw log-spread vs expanding long-run mean (not a 7d window).
# min_periods 30d so z is “extreme vs history,” not a local residual.
RAW_SPREAD_MIN_PERIODS = 30 * 1440


def delivery_config(**overrides) -> BacktestConfig:
    """A-priori slow RV book.

    Locked defaults are 7d z, 01:00 UTC, 2x. Research overrides change one
    knob at a time (entry_z / require_reversion / exit_z); do not grid OOS.
    """
    kwargs = dict(
        leverage=DELIVERY_LEVERAGE,
        z_window=DELIVERY_Z_WINDOW,
        beta_window=DELIVERY_BETA_WINDOW,
        corr_window=DELIVERY_CORR_WINDOW,
        corr_min=DELIVERY_CORR_MIN,
        entry_z=ENTRY_Z,
        exit_z=EXIT_Z,
        stop_z=STOP_Z,
        max_hold_bars=DELIVERY_MAX_HOLD,
        cooldown_bars=DELIVERY_COOLDOWN,
        cost_hurdle_bps=DELIVERY_COST_HURDLE_BPS,
        decision_stride=DELIVERY_STRIDE,
        entry_hour_utc=DELIVERY_ENTRY_HOUR_UTC,
        adv_participation=DELIVERY_ADV,
        taker_fee_bps=TAKER_FEE_BPS,
        starting_equity=STARTING_EQUITY,
        raw_spread_min_periods=RAW_SPREAD_MIN_PERIODS,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def delivery_1x_config(**overrides) -> BacktestConfig:
    """Frozen book A shell with leverage 1 instead of 2. No other knobs."""
    return delivery_config(leverage=DELIVERY_LEVERAGE_1X, **overrides)


def delivery_grid_config(
    *, z_days: int, entry_z: float, **overrides
) -> BacktestConfig:
    """Frozen A shell with one z-window (days) and one entry |z|.

    Only those two knobs change. |z|≥4 uses stop 5 so the band is tradable.
    Do not grid OOS / last month.
    """
    kwargs = dict(
        z_window=int(z_days) * 1440,
        entry_z=float(entry_z),
        stop_z=DELIVERY_STOP_Z_E4 if float(entry_z) >= 4.0 else STOP_Z,
    )
    kwargs.update(overrides)
    return delivery_config(**kwargs)


def delivery_e3_config(**overrides) -> BacktestConfig:
    """Frozen A 7d shell, enter |z|≥3. Stop still 4, exit 0.5. Band is [3, 4)."""
    return delivery_grid_config(z_days=7, entry_z=DELIVERY_ENTRY_Z_3, **overrides)


def delivery_e4_config(**overrides) -> BacktestConfig:
    """Frozen A 7d shell, enter |z|≥4, stop 5, exit 0.5."""
    return delivery_grid_config(z_days=7, entry_z=DELIVERY_ENTRY_Z_4, **overrides)


def delivery_chosen_config(**overrides) -> BacktestConfig:
    """Train-chosen 14d |z|≥2 shell. Pass leverage= for the sweep; nothing else."""
    return delivery_grid_config(
        z_days=CHOSEN_Z_DAYS, entry_z=CHOSEN_ENTRY_Z, **overrides
    )


def funding_carry_config(**overrides) -> BacktestConfig:
    """Slow 2x shell with 8h ETH−BTC funding-carry as the signal.

    Execution matches book A (01:00 UTC, 2x, 5d hold, 1d cooldown). Signal is
    last settled funding differential in bp, not 7d residual z. Residual cost
    hurdle is off so this is not a merged factor.
    """
    return delivery_config(signal_mode="funding_carry", cost_hurdle_bps=0.0, **overrides)


def raw_spread_config(**overrides) -> BacktestConfig:
    """Slow 2x shell on the raw log(ETH/BTC) vs an expanding long-run mean.

    Not the 7d residual z. Entry |z|∈[2,4) is 2–4 expanding σ of the raw
    spread; 30bp hurdle is |spread − expanding μ|. Same 01:00 UTC / 2x book.
    """
    return delivery_config(signal_mode="raw_spread", **overrides)


# User-declared 1m z-grid (not a delivery book; not searched on 2026-07–09).
# 2 series × 2 windows × 3 |z| × 2 leverages. Ticket is $100 margin; ETH
# notional = ticket × leverage. No stop_z / time stop / corr gate. Exit |z|≤0.5.
ZGRID_EQUITY = 1_000.0
ZGRID_TICKET_USD = 100.0
ZGRID_STOP_Z = 1e9
ZGRID_MAX_HOLD = 10**9
ZGRID_CORR_MIN = -1.0
ZGRID_WINDOWS = (120, 240)
ZGRID_ENTRY_ZS = (2.0, 2.5, 3.0)
ZGRID_LEVERAGES = (5.0, 10.0)


def zgrid_config(
    *,
    scheme: str,
    z_window: int,
    entry_z: float,
    leverage: float,
    **overrides,
) -> BacktestConfig:
    """1-minute rolling-z book: log-spread or BTC/ETH ratio.

    scheme ``spread``: z of log(ETH_mark/BTC_mark). High z → short ETH / long β BTC.
    scheme ``ratio``: z of BTC_mark/ETH_mark, invert_signal so high ratio shorts BTC.
    β window matches z window. Hedge is still ETH vs BTC rolling β.
    """
    if scheme not in {"spread", "ratio"}:
        raise ValueError(f"unknown zgrid scheme={scheme}")
    kwargs = dict(
        leverage=float(leverage),
        starting_equity=ZGRID_EQUITY,
        eth_ticket_usd=ZGRID_TICKET_USD,
        notional_times_leverage=True,
        min_leg_notional=0.0,
        z_window=int(z_window),
        beta_window=int(z_window),
        corr_window=int(z_window),
        corr_min=ZGRID_CORR_MIN,
        entry_z=float(entry_z),
        exit_z=EXIT_Z,
        stop_z=ZGRID_STOP_Z,
        max_hold_bars=ZGRID_MAX_HOLD,
        cooldown_bars=0,
        cost_hurdle_bps=0.0,
        decision_stride=1,
        entry_hour_utc=None,
        require_reversion=False,
        signal_mode="ratio_btc_eth" if scheme == "ratio" else "residual",
        invert_signal=scheme == "ratio",
        taker_fee_bps=TAKER_FEE_BPS,
        adv_participation=ADV_PARTICIPATION,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


# One-knob repairs of the user 5m 5x log-spread shell. Frozen before looking
# at this step's results. Do not grid OOS / last month.
REPAIR_BAR_MINUTES = 5
REPAIR_SCHEME = "spread"
REPAIR_Z_WINDOW = 120
REPAIR_ENTRY_Z = 2.0
REPAIR_LEVERAGE = 5.0
REPAIR_COST_HURDLE_BPS = 30.0  # F1: ~1.25× two-leg taker+spread round-trip
REPAIR_SIGNAL_F2 = "hedge_residual"  # F2: z of cum(r_ETH − β r_BTC)


def repair_baseline_config(**overrides) -> BacktestConfig:
    """Frozen 5m 5x 120-bar log-spread shell (B0: no cost hurdle).

    Pass cost_hurdle_bps=REPAIR_COST_HURDLE_BPS for F1. F2 adds
    signal_mode=REPAIR_SIGNAL_F2 on top of F1. Other knobs stay locked:
    |z|≥2, exit 0.5, no stop, $100×leverage ETH, rolling β = z window.
    """
    return zgrid_config(
        scheme=REPAIR_SCHEME,
        z_window=REPAIR_Z_WINDOW,
        entry_z=REPAIR_ENTRY_Z,
        leverage=REPAIR_LEVERAGE,
        **overrides,
    )


def repair_f1_config(**overrides) -> BacktestConfig:
    """F1 kept shell: 30bp hurdle on the frozen 5m 5x log-spread z."""
    return repair_baseline_config(cost_hurdle_bps=REPAIR_COST_HURDLE_BPS, **overrides)


def repair_f2_config(**overrides) -> BacktestConfig:
    """F2: F1 shell with z of cumulative return residual vs lagged β."""
    return repair_f1_config(signal_mode=REPAIR_SIGNAL_F2, **overrides)


# Locked Donchian breakout (not an RV repair; not searched on 2026-07–09).
# 144 × 5m = 12h channel. 1/10 of equity as isolated margin, no stop,
# take-profit at 5× that margin. BTC and ETH are separate books.
# D10 = 10x (frozen). D50 = one-knob 50x; train keep vs D10.
DONCHIAN_BAR_MINUTES = 5
DONCHIAN_WINDOW = 144
DONCHIAN_FRACTION = 0.10
DONCHIAN_LEVERAGE = 10.0
DONCHIAN_LEVERAGE_50 = 50.0
DONCHIAN_TP_MULTIPLE = 5.0
DONCHIAN_EQUITY = 1_000.0
DONCHIAN_SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class DonchianConfig:
    window: int = DONCHIAN_WINDOW
    bar_minutes: int = DONCHIAN_BAR_MINUTES
    fraction: float = DONCHIAN_FRACTION
    leverage: float = DONCHIAN_LEVERAGE
    tp_multiple: float = DONCHIAN_TP_MULTIPLE
    starting_equity: float = DONCHIAN_EQUITY
    taker_fee_bps: float = TAKER_FEE_BPS
    mmr: float = MMR
    adv_participation: float = ADV_PARTICIPATION
    exec_mode: str = "open"
    min_notional: float = 0.0


def donchian_config(**overrides) -> DonchianConfig:
    """A-priori 5m Donchian(144) breakout. Do not grid OOS / last month."""
    kwargs = dict(
        window=DONCHIAN_WINDOW,
        bar_minutes=DONCHIAN_BAR_MINUTES,
        fraction=DONCHIAN_FRACTION,
        leverage=DONCHIAN_LEVERAGE,
        tp_multiple=DONCHIAN_TP_MULTIPLE,
        starting_equity=DONCHIAN_EQUITY,
        taker_fee_bps=TAKER_FEE_BPS,
        mmr=MMR,
        adv_participation=ADV_PARTICIPATION,
        exec_mode="open",
        min_notional=0.0,
    )
    kwargs.update(overrides)
    return DonchianConfig(**kwargs)


def donchian_50_config(**overrides) -> DonchianConfig:
    """Same 144×5m / 1/10 / TP×5 shell, leverage 50 instead of 10."""
    return donchian_config(leverage=DONCHIAN_LEVERAGE_50, **overrides)


# Locked four-family composite (MA / slope / volume / range-position).
# Not a grid: equal weights, one 144×5m window, |z|≥1 in / |z|<1 out.
# Size matches the user clip: 1/10 isolated margin × 10x. Stop is the
# a-priori 2% price / 0.2× margin from the Donchian MAE study, not a
# 5× take-profit. BTC and ETH are separate books. Do not search OOS.
COMPOSITE_BAR_MINUTES = 5
COMPOSITE_WINDOW = 144
COMPOSITE_SLOPE_LAG = 24  # 2h on 5m; 1/6 of the MA window
COMPOSITE_ENTRY_Z = 1.0
COMPOSITE_EXIT_Z = 1.0
COMPOSITE_FRACTION = 0.10
COMPOSITE_LEVERAGE = 10.0
COMPOSITE_LEVERAGE_1X = 1.0  # one-knob on frozen C1 1h; fraction stays 1/10
COMPOSITE_STOP_PRICE_PCT = 0.02
COMPOSITE_Z_CLIP = 5.0
COMPOSITE_EQUITY = 1_000.0
COMPOSITE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
COMPOSITE_WEIGHTS = (0.25, 0.25, 0.25, 0.25)  # ma, slope, volume, position


@dataclass(frozen=True)
class CompositeConfig:
    window: int = COMPOSITE_WINDOW
    slope_lag: int = COMPOSITE_SLOPE_LAG
    bar_minutes: int = COMPOSITE_BAR_MINUTES
    entry_z: float = COMPOSITE_ENTRY_Z
    exit_z: float = COMPOSITE_EXIT_Z
    fraction: float = COMPOSITE_FRACTION
    leverage: float = COMPOSITE_LEVERAGE
    stop_price_pct: float = COMPOSITE_STOP_PRICE_PCT
    z_clip: float = COMPOSITE_Z_CLIP
    starting_equity: float = COMPOSITE_EQUITY
    taker_fee_bps: float = TAKER_FEE_BPS
    mmr: float = MMR
    adv_participation: float = ADV_PARTICIPATION
    exec_mode: str = "open"
    min_notional: float = 0.0
    weights: tuple[float, float, float, float] = COMPOSITE_WEIGHTS
    flatten_in_band: bool = True  # False = hold until opposite or stop


def composite_config(**overrides) -> CompositeConfig:
    """A-priori 5m MA+slope+volume+position composite. Do not grid OOS."""
    kwargs = dict(
        window=COMPOSITE_WINDOW,
        slope_lag=COMPOSITE_SLOPE_LAG,
        bar_minutes=COMPOSITE_BAR_MINUTES,
        entry_z=COMPOSITE_ENTRY_Z,
        exit_z=COMPOSITE_EXIT_Z,
        fraction=COMPOSITE_FRACTION,
        leverage=COMPOSITE_LEVERAGE,
        stop_price_pct=COMPOSITE_STOP_PRICE_PCT,
        z_clip=COMPOSITE_Z_CLIP,
        starting_equity=COMPOSITE_EQUITY,
        taker_fee_bps=TAKER_FEE_BPS,
        mmr=MMR,
        adv_participation=ADV_PARTICIPATION,
        exec_mode="open",
        min_notional=0.0,
        weights=COMPOSITE_WEIGHTS,
        flatten_in_band=True,
    )
    kwargs.update(overrides)
    return CompositeConfig(**kwargs)


# C1: one-knob slower clock. MA/slope/volume/position are swing features;
# 5m |z|≥1 was a churn machine. Only bar_minutes changes (5 → 60).
# Window 144, slope 24, |z|≥1, 1/10×10x, 2% stop stay frozen.
COMPOSITE_BAR_MINUTES_1H = 60


def composite_hourly_config(**overrides) -> CompositeConfig:
    """Same four-family composite, 1h bars instead of 5m."""
    kwargs = dict(bar_minutes=COMPOSITE_BAR_MINUTES_1H)
    kwargs.update(overrides)
    return composite_config(**kwargs)


def composite_hourly_1x_config(**overrides) -> CompositeConfig:
    """Frozen C1 1h four-family composite, leverage 1 instead of 10."""
    kwargs = dict(leverage=COMPOSITE_LEVERAGE_1X)
    kwargs.update(overrides)
    return composite_hourly_config(**kwargs)


# Pre-declared C2–C4 waterfall on the 1h (or daily) shell. First with
# train equity > 0 is the candidate; do not pick the best of three.
# C2: hold through the |z|<1 band until opposite (scratch exits were the 1h leak).
# C3: |z|≥2, band exit stays (fewer entries).
# C4: daily bars, |z|≥1, band exit (calendar-day TA).
COMPOSITE_ENTRY_Z_2 = 2.0
COMPOSITE_BAR_MINUTES_1D = 1440


def composite_hold_config(**overrides) -> CompositeConfig:
    """C2: 1h composite, flatten only on opposite / stop / liq."""
    kwargs = dict(flatten_in_band=False)
    kwargs.update(overrides)
    return composite_hourly_config(**kwargs)


def composite_z2_config(**overrides) -> CompositeConfig:
    """C3: 1h composite, |z|≥2 in / |z|<2 out."""
    kwargs = dict(entry_z=COMPOSITE_ENTRY_Z_2, exit_z=COMPOSITE_ENTRY_Z_2)
    kwargs.update(overrides)
    return composite_hourly_config(**kwargs)


def composite_daily_config(**overrides) -> CompositeConfig:
    """C4: same four-family composite on daily bars."""
    kwargs = dict(bar_minutes=COMPOSITE_BAR_MINUTES_1D)
    kwargs.update(overrides)
    return composite_config(**kwargs)


