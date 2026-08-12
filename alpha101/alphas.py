# -*- coding: utf-8 -*-
"""WorldQuant Alpha101 formulaic alphas (OHLCV-implementable subset + core set).

Frames are dates × tickers. Higher raw value is as-defined by the paper
(direction may be negative IC; admission layer flips via direction).
"""

from __future__ import annotations

from typing import Callable, Dict

import numpy as np
import pandas as pd

from .data import PricePanel
from .operators import (
    abs_df,
    correlation,
    covariance,
    cs_demean,
    decay_linear,
    delay,
    delta,
    log,
    product,
    rank,
    scale,
    sign,
    signedpower,
    ts_argmax,
    ts_argmin,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
    ts_sum,
)


AlphaFn = Callable[[PricePanel], pd.DataFrame]


def _returns(p: PricePanel) -> pd.DataFrame:
    return p.returns


def alpha001(p: PricePanel) -> pd.DataFrame:
    # rank(Ts_ArgMax(SignedPower(((returns < 0) ? stddev(returns, 20) : close), 2.), 5)) - 0.5
    inner = p.close.copy()
    std20 = ts_std(_returns(p), 20)
    inner = inner.where(~(_returns(p) < 0), std20)
    return rank(ts_argmax(signedpower(inner, 2.0), 5)) - 0.5


def alpha002(p: PricePanel) -> pd.DataFrame:
    # -1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6)
    return -1 * correlation(
        rank(delta(log(p.volume.replace(0, np.nan)), 2)),
        rank((p.close - p.open) / p.open.replace(0, np.nan)),
        6,
    )


def alpha003(p: PricePanel) -> pd.DataFrame:
    return -1 * correlation(rank(p.open), rank(p.volume), 10)


def alpha004(p: PricePanel) -> pd.DataFrame:
    return -1 * ts_rank(rank(p.low), 9)


def alpha005(p: PricePanel) -> pd.DataFrame:
    # rank(open - mean(vwap,10)) * (-1 * abs(rank(close - vwap)))
    return rank(p.open - ts_mean(p.vwap, 10)) * (-1 * abs_df(rank(p.close - p.vwap)))


def alpha006(p: PricePanel) -> pd.DataFrame:
    return -1 * correlation(p.open, p.volume, 10)


def alpha007(p: PricePanel) -> pd.DataFrame:
    # (adv20 < volume) ? ((-1 * ts_rank(abs(delta(close, 7)), 60)) * sign(delta(close, 7))) : (-1)
    adv20 = ts_mean(p.volume, 20)
    part = (-1 * ts_rank(abs_df(delta(p.close, 7)), 60)) * sign(delta(p.close, 7))
    return part.where(adv20 < p.volume, -1.0)


def alpha008(p: PricePanel) -> pd.DataFrame:
    # -1 * rank(((sum(open,5)*sum(returns,5)) - delay(sum(open,5)*sum(returns,5),10)))
    s = ts_sum(p.open, 5) * ts_sum(_returns(p), 5)
    return -1 * rank(s - delay(s, 10))


def alpha009(p: PricePanel) -> pd.DataFrame:
    # (0 < ts_min(delta(close,1),5)) ? delta(close,1)
    # : ((ts_max(delta(close,1),5) < 0) ? delta(close,1) : (-1*delta(close,1)))
    d1 = delta(p.close, 1)
    cond1 = ts_min(d1, 5) > 0
    cond2 = ts_max(d1, 5) < 0
    out = -1 * d1
    out = out.where(~cond2, d1)
    out = out.where(~cond1, d1)
    return out


def alpha010(p: PricePanel) -> pd.DataFrame:
    return rank(alpha009(p))


def alpha011(p: PricePanel) -> pd.DataFrame:
    # (rank(ts_max(vwap-close,3)) + rank(ts_min(vwap-close,3))) * rank(delta(volume,3))
    return (rank(ts_max(p.vwap - p.close, 3)) + rank(ts_min(p.vwap - p.close, 3))) * rank(
        delta(p.volume, 3)
    )


def alpha012(p: PricePanel) -> pd.DataFrame:
    return sign(delta(p.volume, 1)) * (-1 * delta(p.close, 1))


def alpha013(p: PricePanel) -> pd.DataFrame:
    return -1 * rank(covariance(rank(p.close), rank(p.volume), 5))


def alpha014(p: PricePanel) -> pd.DataFrame:
    return (-1 * rank(delta(_returns(p), 3))) * correlation(p.open, p.volume, 10)


def alpha015(p: PricePanel) -> pd.DataFrame:
    return -1 * ts_sum(rank(correlation(rank(p.high), rank(p.volume), 3)), 3)


def alpha016(p: PricePanel) -> pd.DataFrame:
    return -1 * rank(covariance(rank(p.high), rank(p.volume), 5))


def alpha017(p: PricePanel) -> pd.DataFrame:
    # ((-1 * rank(ts_rank(close, 10))) * rank(delta(delta(close, 1), 1))) * rank(ts_rank(volume / adv20, 5))
    adv20 = ts_mean(p.volume, 20)
    return (
        (-1 * rank(ts_rank(p.close, 10)))
        * rank(delta(delta(p.close, 1), 1))
        * rank(ts_rank(p.volume / adv20.replace(0, np.nan), 5))
    )


def alpha018(p: PricePanel) -> pd.DataFrame:
    # -1 * rank(stddev(abs(close-open),5) + (close-open) + correlation(close, open, 10))
    return -1 * rank(
        ts_std(abs_df(p.close - p.open), 5)
        + (p.close - p.open)
        + correlation(p.close, p.open, 10)
    )


def alpha019(p: PricePanel) -> pd.DataFrame:
    # (-1 * sign((close - delay(close, 7)) + delta(close, 7))) * (1 + rank(1 + sum(returns, 250)))
    return (-1 * sign((p.close - delay(p.close, 7)) + delta(p.close, 7))) * (
        1 + rank(1 + ts_sum(_returns(p), 250))
    )


def alpha020(p: PricePanel) -> pd.DataFrame:
    return (
        (-1 * rank(p.open - delay(p.high, 1)))
        * rank(p.open - delay(p.close, 1))
        * rank(p.open - delay(p.low, 1))
    )


def alpha021(p: PricePanel) -> pd.DataFrame:
    # multipartite conditional on mean/volume — simplified robust form
    # rank(mean(close,8)+stddev) vs mean(close,2); volume vs adv20
    a = ts_mean(p.close, 8) + ts_std(p.close, 8) < ts_mean(p.close, 2)
    b = ts_mean(p.volume, 20) / p.volume.replace(0, np.nan) < 1
    out = pd.DataFrame(np.nan, index=p.close.index, columns=p.close.columns)
    out = out.where(~((~a) & b), 1.0)
    out = out.where(~a, -1.0)
    return out.fillna(0.0)  # else 0 per paper branch


def alpha022(p: PricePanel) -> pd.DataFrame:
    return -1 * (delta(correlation(p.high, p.volume, 5), 5) * rank(ts_std(p.close, 20)))


def alpha024(p: PricePanel) -> pd.DataFrame:
    # conditional on delta(mean(close,100)) — simplified
    m = ts_mean(p.close, 100)
    cond = (delta(m, 100) / delay(p.close, 100).replace(0, np.nan) <= 0.05) | (
        delta(m, 100) == 0
    )
    part = -1 * delta(p.close, 3)
    alt = -1 * (p.close - ts_min(p.close, 100))
    return part.where(cond, alt)


def alpha026(p: PricePanel) -> pd.DataFrame:
    return -1 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)


def alpha028(p: PricePanel) -> pd.DataFrame:
    adv20 = ts_mean(p.volume, 20)
    return scale(correlation(adv20, p.low, 5) + (p.high + p.low) / 2 - p.close)


def alpha033(p: PricePanel) -> pd.DataFrame:
    return rank(-1 * (1 - p.open / p.close.replace(0, np.nan)))


def alpha034(p: PricePanel) -> pd.DataFrame:
    return rank(
        (1 - rank(ts_std(_returns(p), 2) / ts_std(_returns(p), 5).replace(0, np.nan)))
        + (1 - rank(delta(p.close, 1)))
    )


def alpha038(p: PricePanel) -> pd.DataFrame:
    return (-1 * rank(ts_rank(p.close, 10))) * rank(p.close / p.open.replace(0, np.nan))


def alpha040(p: PricePanel) -> pd.DataFrame:
    return (-1 * rank(ts_std(p.high, 10))) * correlation(p.high, p.volume, 10)


def alpha041(p: PricePanel) -> pd.DataFrame:
    return ((p.high * p.low) ** 0.5) - p.vwap


def alpha042(p: PricePanel) -> pd.DataFrame:
    return rank(p.vwap - p.close) / rank(p.vwap + p.close).replace(0, np.nan)


def alpha044(p: PricePanel) -> pd.DataFrame:
    return -1 * correlation(p.high, rank(p.volume), 5)


def alpha046(p: PricePanel) -> pd.DataFrame:
    # (0.25 < (((delay(close, 20) - delay(close, 10)) / 10) - ((delay(close, 10) - close) / 10))) ? -1 : ...
    core = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - (
        (delay(p.close, 10) - p.close) / 10
    )
    out = -1 * delta(p.close, 1)  # else branch approx: -1*(close-delay(close,1)) when mid
    out = out.where(~(core < 0), 1.0)
    out = out.where(~(core > 0.25), -1.0)
    return out


def alpha049(p: PricePanel) -> pd.DataFrame:
    core = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - (
        (delay(p.close, 10) - p.close) / 10
    )
    return (-1 * delta(p.close, 1)).where(core < -0.1, 1.0)


def alpha053(p: PricePanel) -> pd.DataFrame:
    # -1 * delta(((close-low)-(high-close))/(close-low), 9)
    denom = (p.close - p.low).replace(0, np.nan)
    return -1 * delta(((p.close - p.low) - (p.high - p.close)) / denom, 9)


def alpha054(p: PricePanel) -> pd.DataFrame:
    # -1 * ((low - close) * (open^5)) / ((low - high) * (close^5))
    return (
        -1
        * ((p.low - p.close) * (p.open ** 5))
        / ((p.low - p.high).replace(0, np.nan) * (p.close.replace(0, np.nan) ** 5))
    )


def alpha055(p: PricePanel) -> pd.DataFrame:
    # -1 * correlation(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))), rank(volume), 6)
    numer = p.close - ts_min(p.low, 12)
    denom = (ts_max(p.high, 12) - ts_min(p.low, 12)).replace(0, np.nan)
    return -1 * correlation(rank(numer / denom), rank(p.volume), 6)


def alpha060(p: PricePanel) -> pd.DataFrame:
    # -(2*scale(rank((((close-low)-(high-close))/(high-low))*volume)) - scale(rank(ts_argmax(close,10))))
    hl = (p.high - p.low).replace(0, np.nan)
    inner = (((p.close - p.low) - (p.high - p.close)) / hl) * p.volume
    return -(2 * scale(rank(inner)) - scale(rank(ts_argmax(p.close, 10))))


def alpha101(p: PricePanel) -> pd.DataFrame:
    return (p.close - p.open) / ((p.high - p.low).replace(0, np.nan) + 0.001)


# Extra useful short-horizon / liquidity alphas often grouped with 101 research
def alpha_rev1(p: PricePanel) -> pd.DataFrame:
    """1-day reversal (not in 101 numbering; research control)."""
    return -1 * _returns(p)


def alpha_mom5(p: PricePanel) -> pd.DataFrame:
    return p.close / delay(p.close, 5) - 1


def alpha_vol20(p: PricePanel) -> pd.DataFrame:
    return -1 * ts_std(_returns(p), 20)


ALPHA_REGISTRY: Dict[str, AlphaFn] = {
    "alpha001": alpha001,
    "alpha002": alpha002,
    "alpha003": alpha003,
    "alpha004": alpha004,
    "alpha005": alpha005,
    "alpha006": alpha006,
    "alpha007": alpha007,
    "alpha008": alpha008,
    "alpha009": alpha009,
    "alpha010": alpha010,
    "alpha011": alpha011,
    "alpha012": alpha012,
    "alpha013": alpha013,
    "alpha014": alpha014,
    "alpha015": alpha015,
    "alpha016": alpha016,
    "alpha017": alpha017,
    "alpha018": alpha018,
    "alpha019": alpha019,
    "alpha020": alpha020,
    "alpha021": alpha021,
    "alpha022": alpha022,
    "alpha024": alpha024,
    "alpha026": alpha026,
    "alpha028": alpha028,
    "alpha033": alpha033,
    "alpha034": alpha034,
    "alpha038": alpha038,
    "alpha040": alpha040,
    "alpha041": alpha041,
    "alpha042": alpha042,
    "alpha044": alpha044,
    "alpha046": alpha046,
    "alpha049": alpha049,
    "alpha053": alpha053,
    "alpha054": alpha054,
    "alpha055": alpha055,
    "alpha060": alpha060,
    "alpha101": alpha101,
    # benchmarks / short-horizon controls
    "alpha_rev1": alpha_rev1,
    "alpha_mom5": alpha_mom5,
    "alpha_vol20": alpha_vol20,
}

ALPHA_DOCS: Dict[str, str] = {
    k: f"WorldQuant Alpha101 `{k}` (OHLCV implementation)" for k in ALPHA_REGISTRY
}
ALPHA_DOCS.update(
    {
        "alpha_rev1": "1日反转对照因子 -r_t（非101编号）",
        "alpha_mom5": "5日动量对照 close/delay(close,5)-1",
        "alpha_vol20": "20日低波对照 -std(returns,20)",
    }
)


def compute_alphas(
    panel: PricePanel,
    names: list[str] | None = None,
    *,
    lag: int = 1,
    winsor_q: float = 0.01,
    zscore: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Compute processed alpha panels (dates × tickers), lagged for no look-ahead."""
    names = names or list(ALPHA_REGISTRY.keys())
    out: Dict[str, pd.DataFrame] = {}
    for name in names:
        if name not in ALPHA_REGISTRY:
            raise KeyError(name)
        try:
            raw = ALPHA_REGISTRY[name](panel)
        except Exception as exc:  # noqa: BLE001
            print(f"[alpha] {name} failed: {exc}")
            continue
        # replace inf
        raw = raw.replace([np.inf, -np.inf], np.nan)
        sig = raw.shift(lag) if lag else raw
        if winsor_q and winsor_q > 0:
            lo = sig.quantile(winsor_q, axis=1)
            hi = sig.quantile(1 - winsor_q, axis=1)
            sig = sig.clip(lower=lo, upper=hi, axis=0)
        if zscore:
            mu = sig.mean(axis=1)
            sd = sig.std(axis=1, ddof=0).replace(0, np.nan)
            sig = sig.sub(mu, axis=0).div(sd, axis=0)
        out[name] = sig
    return out
