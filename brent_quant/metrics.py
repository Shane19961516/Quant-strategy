"""Performance, trade, Monte Carlo, and bootstrap statistics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from brent_quant.backtest import BacktestResult


def _to_float(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    if equity is None or equity.empty:
        return float("nan"), 0
    dd = equity / equity.cummax() - 1.0
    trough = int(dd.argmin()) if len(dd) else 0
    duration = 0
    peak = equity.iloc[0]
    cur = 0
    best = 0
    for v in equity:
        if v >= peak:
            peak = v
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
        duration = best
    return float(dd.min()), int(duration)


def trade_stats(trades: pd.DataFrame) -> dict[str, float]:
    empty = {
        "n_trades": 0.0,
        "win_rate": float("nan"),
        "avg_win": float("nan"),
        "avg_loss": float("nan"),
        "profit_factor": float("nan"),
        "payoff_ratio": float("nan"),
        "expectancy": float("nan"),
        "avg_holding_bars": float("nan"),
        "turnover": 0.0,
        "gross_pnl": 0.0,
    }
    if trades is None or trades.empty or "PnL" not in trades.columns:
        return empty
    pnl = trades["PnL"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(losses.abs().sum()) if len(losses) else 0.0
    avg_win = float(wins.mean()) if len(wins) else float("nan")
    avg_loss = float(losses.mean()) if len(losses) else float("nan")
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else float("nan"))
    payoff = abs(avg_win / avg_loss) if avg_loss and not np.isnan(avg_loss) and avg_loss != 0 else float("nan")
    return {
        "n_trades": float(len(pnl)),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else float("nan"),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": pf,
        "payoff_ratio": payoff,
        "expectancy": float(pnl.mean()) if len(pnl) else float("nan"),
        "avg_holding_bars": float(trades["bars_held"].mean()) if "bars_held" in trades else float("nan"),
        "turnover": float(len(pnl)),
        "gross_pnl": float(pnl.sum()),
    }


def var_cvar(returns: pd.Series, alpha: float = 0.05) -> tuple[float, float]:
    r = returns.dropna()
    if len(r) < 10:
        return float("nan"), float("nan")
    var = float(np.quantile(r, alpha))
    tail = r[r <= var]
    cvar = float(tail.mean()) if len(tail) else var
    return var, cvar


def performance_from_equity(equity: pd.Series, trades: pd.DataFrame | None = None) -> dict[str, Any]:
    if equity is None or len(equity) < 3:
        return {"error": "insufficient equity points"}
    eq = equity.dropna()
    daily = eq.resample("1D").last().dropna() if not isinstance(eq.index, pd.DatetimeIndex) or eq.index.inferred_type == "integer" else eq
    if isinstance(eq.index, pd.DatetimeIndex):
        daily = eq.resample("1D").last().dropna()
    else:
        daily = eq
    rets = daily.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    n = len(rets)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0) if eq.iloc[0] else float("nan")
    years = n / 252.0 if n else float("nan")
    if n >= 5 and years > 0:
        cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / max(years, 1e-9)) - 1.0)
        ann = float(rets.mean() * 252)
        vol = float(rets.std(ddof=1) * np.sqrt(252)) if n > 1 else float("nan")
    else:
        cagr = ann = vol = float("nan")
    mdd, dd_dur = max_drawdown(daily if len(daily) else eq)
    sharpe = (ann / vol) if vol and vol > 0 else float("nan")
    downside = rets[rets < 0]
    dvol = float(downside.std(ddof=1) * np.sqrt(252)) if len(downside) > 1 else float("nan")
    sortino = (ann / dvol) if dvol and dvol > 0 else float("nan")
    calmar = (cagr / abs(mdd)) if mdd and mdd < 0 else float("nan")
    var, cvar = var_cvar(rets)
    monthly = daily.resample("ME").last().pct_change().dropna() if isinstance(daily.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    yearly = daily.resample("YE").last().pct_change().dropna() if isinstance(daily.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    stats = {
        "start": str(eq.index[0]),
        "end": str(eq.index[-1]),
        "n_daily": int(n),
        "total_return": total,
        "cagr": cagr,
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": mdd,
        "drawdown_duration_days": int(dd_dur),
        "var_5": var,
        "cvar_5": cvar,
        "end_nav": float(eq.iloc[-1]),
        "monthly_returns": {str(k.date()): float(v) for k, v in monthly.items()} if len(monthly) else {},
        "yearly_returns": {str(k.date()): float(v) for k, v in yearly.items()} if len(yearly) else {},
    }
    stats.update(trade_stats(trades if trades is not None else pd.DataFrame()))
    return stats


def summarize_result(result: BacktestResult) -> dict[str, Any]:
    stats = performance_from_equity(result.daily_equity if len(result.daily_equity) else result.equity, result.trades)
    stats["model"] = result.model
    stats["params"] = result.params
    return stats


def monte_carlo_trade_shuffle(
    trades: pd.DataFrame,
    n_paths: int = 1000,
    seed: int = 42,
    initial_nav: float = 1_000_000.0,
) -> dict[str, float]:
    if trades is None or trades.empty or "PnL" not in trades.columns:
        return {}
    pnl = trades["PnL"].astype(float).to_numpy()
    rng = np.random.default_rng(seed)
    max_dds = np.empty(n_paths)
    finals = np.empty(n_paths)
    for i in range(n_paths):
        path = rng.permutation(pnl)
        eq = initial_nav + np.cumsum(path)
        eq = np.insert(eq, 0, initial_nav)
        peak = np.maximum.accumulate(eq)
        dd = eq / peak - 1.0
        max_dds[i] = float(dd.min())
        finals[i] = float(eq[-1] - initial_nav)
    ruin = float((finals <= -0.5 * initial_nav).mean())
    return {
        "mc_paths": float(n_paths),
        "mc_expected_max_dd": float(max_dds.mean()),
        "mc_dd_p95": float(np.quantile(max_dds, 0.05)),  # more negative is worse; 5th pct
        "mc_pnl_mean": float(finals.mean()),
        "mc_pnl_p5": float(np.quantile(finals, 0.05)),
        "mc_risk_of_ruin_50pct": ruin,
    }


def bootstrap_metrics(
    trades: pd.DataFrame,
    daily_equity: pd.Series,
    n: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    if trades is None or trades.empty or "PnL" not in trades.columns:
        return {}
    pnl = trades["PnL"].astype(float).to_numpy()
    rng = np.random.default_rng(seed)
    sharpes = []
    for _ in range(n):
        sample = rng.choice(pnl, size=len(pnl), replace=True)
        mu = sample.mean()
        sd = sample.std(ddof=1)
        sharpes.append(mu / sd if sd > 0 else np.nan)
    sharpes = np.array(sharpes, dtype=float)
    rets = daily_equity.pct_change().dropna().to_numpy() if daily_equity is not None and len(daily_equity) > 5 else None
    cagrs = []
    dds = []
    if rets is not None and len(rets) > 5:
        for _ in range(n):
            sample = rng.choice(rets, size=len(rets), replace=True)
            eq = np.cumprod(1.0 + sample)
            eq = np.insert(eq, 0, 1.0)
            years = len(sample) / 252.0
            cagrs.append(eq[-1] ** (1.0 / max(years, 1e-9)) - 1.0)
            peak = np.maximum.accumulate(eq)
            dds.append(float((eq / peak - 1.0).min()))
    out = {
        "sharpe_p5": float(np.nanpercentile(sharpes, 5)),
        "sharpe_p50": float(np.nanpercentile(sharpes, 50)),
        "sharpe_p95": float(np.nanpercentile(sharpes, 95)),
    }
    if cagrs:
        out["cagr_p5"] = float(np.nanpercentile(cagrs, 5))
        out["cagr_p50"] = float(np.nanpercentile(cagrs, 50))
        out["cagr_p95"] = float(np.nanpercentile(cagrs, 95))
        out["maxdd_p5"] = float(np.nanpercentile(dds, 5))
        out["maxdd_p50"] = float(np.nanpercentile(dds, 50))
        out["maxdd_p95"] = float(np.nanpercentile(dds, 95))
    return out


def hour_of_day_stats(result: BacktestResult) -> pd.DataFrame:
    eq = result.equity
    rets = eq.pct_change()
    hours = rets.index.tz_convert("UTC").hour
    pnl = rets.groupby(hours).mean()
    vol = result.bars["close"].pct_change().abs().groupby(result.bars.index.hour).mean() if "close" in result.bars else None
    volume = result.bars["volume"].groupby(result.bars.index.hour).mean() if "volume" in result.bars else None
    out = pd.DataFrame({"mean_bar_return": pnl})
    if vol is not None:
        out["mean_abs_return"] = vol
    if volume is not None:
        out["mean_volume"] = volume
    out.index.name = "hour_utc"
    return out
