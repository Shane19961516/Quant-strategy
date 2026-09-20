"""Train-only MAE/MFE / ATR diagnostic for the frozen 10x Donchian.

Not a delivery book and not a grid. The 5×-margin take-profit is ignored so
the path is the breakout, not the current exit. Paths stop at isolated
liquidation or the last train bar — never the OOS / last-month window.

Literature lock (Original Turtle Rules, not fit here):

- No fixed take-profit. Winning exit is the opposite shorter Donchian
  (System 1: 20-day in / 10-day out → here 144 in / 72 out).
- Always a predefined stop. 1N ≈ 1% equity; stop 2N ≈ 2% equity.
- On this 1/10 isolated 10x clip, notional ≈ equity, so 2% equity ≈ 2%
  price ≈ 0.2× invested margin. Isolated liq is ≈ 9.6% price.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    FUNDING_BLACKOUT_MIN,
    GAP_FREEZE_MINUTES,
    HALF_SPREAD_BPS,
    MMR,
    DonchianConfig,
    donchian_config,
)
from .donchian import _prefix, add_donchian
from .simulator import _exec_price, _impact_bps

ATR14 = 14
ATR144 = 144
DAILY_ATR_N = 20
TP_MARGIN_MULTS = (0.2, 0.5, 1.0, 2.0, 3.0, 5.0)
FIXED_STOP_PRICE_PCTS = {
    "2pct_equity": 0.02,
    "4pct": 0.04,
}


def add_atr(panel: pd.DataFrame, n: int = ATR14) -> pd.DataFrame:
    """Wilder ATR on mark OHLC, shifted so bar t is ATR through t-1."""
    df = panel.copy()
    n = int(n)
    for prefix in ("btc", "eth"):
        high = df[f"{prefix}_mark_high"].astype(float)
        low = df[f"{prefix}_mark_low"].astype(float)
        close = df[f"{prefix}_mark_close"].astype(float)
        prev = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev).abs(), (low - prev).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
        df[f"{prefix}_atr_{n}"] = atr.shift(1)
    return df


def add_daily_atr(panel: pd.DataFrame, n: int = DAILY_ATR_N) -> pd.DataFrame:
    """Previous-day Wilder ATR(n) of daily mark bars (Turtle N analog)."""
    df = panel.copy()
    n = int(n)
    if "bar_open" not in df.columns:
        df["bar_open"] = pd.to_datetime(df["bar_open_ts"], unit="ms", utc=True)
    day = df["bar_open"].dt.floor("D")
    for prefix in ("btc", "eth"):
        g = df.groupby(day, sort=True)
        daily = pd.DataFrame(
            {
                "h": g[f"{prefix}_mark_high"].max(),
                "l": g[f"{prefix}_mark_low"].min(),
                "c": g[f"{prefix}_mark_close"].last(),
            }
        )
        prev = daily["c"].shift(1)
        tr = pd.concat(
            [
                daily["h"] - daily["l"],
                (daily["h"] - prev).abs(),
                (daily["l"] - prev).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
        # Known at the open of day D: yesterday's ATR.
        mapped = day.map(atr.shift(1))
        df[f"{prefix}_atr_daily"] = np.asarray(mapped, dtype=float)
    return df


def add_half_channel(panel: pd.DataFrame, window: int) -> pd.DataFrame:
    """Lagged opposite-channel of window//2 (Turtle System-1 analog)."""
    df = panel.copy()
    n = max(int(window) // 2, 1)
    complete = df["pair_incomplete"] == 0
    for prefix in ("btc", "eth"):
        high = df[f"{prefix}_mark_high"].where(complete)
        low = df[f"{prefix}_mark_low"].where(complete)
        df[f"{prefix}_donch_exit_upper"] = high.shift(1).rolling(n, min_periods=n).max()
        df[f"{prefix}_donch_exit_lower"] = low.shift(1).rolling(n, min_periods=n).min()
    return df


def _pctiles(x: np.ndarray, qs: tuple[int, ...] = (10, 25, 50, 75, 90)) -> dict:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {f"p{q}": None for q in qs} | {"n": 0, "mean": None}
    return {f"p{q}": float(np.percentile(x, q)) for q in qs} | {
        "n": int(x.size),
        "mean": float(x.mean()),
    }


def _liq_price_pct(leverage: float, mmr: float = MMR) -> float:
    return float(1.0 / leverage - mmr)


@dataclass(frozen=True)
class ExitStudySpec:
    train_start_ts: int
    train_end_ts: int
    mode: str = "independent"  # independent rising-edge vs sequential flat-only


def breakout_excursions(
    df: pd.DataFrame,
    *,
    symbol: str,
    cfg: DonchianConfig | None = None,
    spec: ExitStudySpec,
) -> pd.DataFrame:
    """One row per train breakout clip. Paths never read bars after train_end."""
    cfg = cfg or donchian_config()
    if spec.mode not in {"independent", "sequential"}:
        raise ValueError(f"unknown mode={spec.mode}")
    prefix = _prefix(symbol)
    n = len(df)
    last_open = df[f"{prefix}_open"].to_numpy(dtype=float)
    mark = df[f"{prefix}_mark_close"].to_numpy(dtype=float)
    mark_h = df[f"{prefix}_mark_high"].to_numpy(dtype=float)
    mark_l = df[f"{prefix}_mark_low"].to_numpy(dtype=float)
    qvol = df[f"{prefix}_quote_volume"].to_numpy(dtype=float)
    side_sig = df[f"{prefix}_donch_side"].to_numpy(dtype=np.int8)
    incomplete = df["pair_incomplete"].to_numpy(dtype=np.int8)
    gap_run = df["gap_run"].to_numpy(dtype=np.int32)
    next_fund = df[f"{prefix}_next_funding_ts"].to_numpy(dtype=np.int64)
    ts = df["bar_open_ts"].to_numpy(dtype=np.int64)
    upper = df[f"{prefix}_donch_upper"].to_numpy(dtype=float)
    lower = df[f"{prefix}_donch_lower"].to_numpy(dtype=float)
    atr14 = df[f"{prefix}_atr_{ATR14}"].to_numpy(dtype=float)
    atr144 = df[f"{prefix}_atr_{ATR144}"].to_numpy(dtype=float)
    atr_d = df[f"{prefix}_atr_daily"].to_numpy(dtype=float)
    exit_u = df[f"{prefix}_donch_exit_upper"].to_numpy(dtype=float)
    exit_l = df[f"{prefix}_donch_exit_lower"].to_numpy(dtype=float)
    half = HALF_SPREAD_BPS[symbol]
    lev = float(cfg.leverage)
    mmr = float(cfg.mmr)
    liq_pct = _liq_price_pct(lev, mmr)
    last_i = int(np.searchsorted(ts, int(spec.train_end_ts), side="right") - 1)
    last_i = min(max(last_i, 0), n - 1)

    def in_funding_blackout(i: int) -> bool:
        if next_fund[i] < 0:
            return False
        dist_min = abs(ts[i] - next_fund[i]) / 60_000
        return dist_min <= FUNDING_BLACKOUT_MIN

    def can_enter(i: int, sig: int, edge_ok: bool) -> bool:
        if not edge_ok or sig == 0:
            return False
        tradable = incomplete[i] == 0 and np.isfinite(last_open[i]) and np.isfinite(mark[i])
        in_window = int(ts[i]) >= int(spec.train_start_ts) and int(ts[i]) <= int(spec.train_end_ts)
        return bool(
            tradable
            and in_window
            and not in_funding_blackout(i)
            and gap_run[i] == 0
            and (i == 0 or gap_run[i - 1] <= GAP_FREEZE_MINUTES)
        )

    rows: list[dict] = []
    busy_until = -1
    for i in range(1, last_i + 1):
        sig = int(side_sig[i - 1])
        prev = int(side_sig[i - 2]) if i >= 2 else 0
        if spec.mode == "independent":
            edge_ok = sig != 0 and prev == 0
        else:
            edge_ok = i > busy_until
        if not can_enter(i, sig, edge_ok):
            continue
        notion = 1.0
        cap = cfg.adv_participation * max(qvol[i], 0.0)
        if cap > 0:
            notion = min(notion, cap)
        buy = sig > 0
        imp = _impact_bps(notion, qvol[i])
        fill_px = _exec_price(last_open[i], buy, half, imp)
        if not np.isfinite(fill_px) or fill_px <= 0:
            continue
        a14 = float(atr14[i]) if np.isfinite(atr14[i]) else float("nan")
        a144 = float(atr144[i]) if np.isfinite(atr144[i]) else float("nan")
        ad = float(atr_d[i]) if np.isfinite(atr_d[i]) else float("nan")
        ch_w = (
            float(upper[i] - lower[i])
            if np.isfinite(upper[i]) and np.isfinite(lower[i])
            else float("nan")
        )
        mfe = 0.0
        mae = 0.0
        end_j = last_i
        why = "censored_train_end"
        first_tp: dict[float, int | None] = {m: None for m in TP_MARGIN_MULTS}
        first_stop: dict[str, int | None] = {
            "2pct_equity": None,
            "4pct": None,
            "2N_atr14": None,
            "2N_atr144": None,
            "2N_daily": None,
            "liq": None,
        }
        first_72: int | None = None
        mfe_at_72 = float("nan")
        mae_at_72 = float("nan")
        move_at_72 = float("nan")
        stop_px = {
            "2pct_equity": FIXED_STOP_PRICE_PCTS["2pct_equity"],
            "4pct": FIXED_STOP_PRICE_PCTS["4pct"],
            "2N_atr14": (2.0 * a14 / fill_px) if np.isfinite(a14) and fill_px else float("nan"),
            "2N_atr144": (2.0 * a144 / fill_px) if np.isfinite(a144) and fill_px else float("nan"),
            "2N_daily": (2.0 * ad / fill_px) if np.isfinite(ad) and fill_px else float("nan"),
            "liq": liq_pct,
        }
        tp_px = {m: float(m) / lev for m in TP_MARGIN_MULTS}

        for j in range(i, last_i + 1):
            if incomplete[j] != 0 or not np.isfinite(mark[j]):
                continue
            if first_72 is None and j > i:
                prev_c = mark[j - 1]
                if sig > 0 and np.isfinite(exit_l[j]) and np.isfinite(prev_c) and prev_c < exit_l[j]:
                    first_72 = j
                elif sig < 0 and np.isfinite(exit_u[j]) and np.isfinite(prev_c) and prev_c > exit_u[j]:
                    first_72 = j
                if first_72 == j:
                    mfe_at_72 = mfe
                    mae_at_72 = mae
                    move_at_72 = (
                        (prev_c - fill_px) / fill_px
                        if sig > 0
                        else (fill_px - prev_c) / fill_px
                    )
            hi = mark_h[j] if np.isfinite(mark_h[j]) else mark[j]
            lo = mark_l[j] if np.isfinite(mark_l[j]) else mark[j]
            if sig > 0:
                fav = (hi - fill_px) / fill_px
                adv = (fill_px - lo) / fill_px
                close_move = (mark[j] - fill_px) / fill_px
            else:
                fav = (fill_px - lo) / fill_px
                adv = (hi - fill_px) / fill_px
                close_move = (fill_px - mark[j]) / fill_px
            mfe = max(mfe, float(fav))
            mae = max(mae, float(adv))
            for m, thr in tp_px.items():
                if first_tp[m] is None and fav >= thr:
                    first_tp[m] = j
            for name, thr in stop_px.items():
                if first_stop[name] is None and np.isfinite(thr) and adv >= thr:
                    first_stop[name] = j
            if close_move <= -liq_pct:
                end_j = j
                why = "liq"
                break
        else:
            end_j = last_i
            why = "censored_train_end"

        if spec.mode == "sequential":
            busy_until = end_j

        row = {
            "symbol": symbol,
            "mode": spec.mode,
            "side": int(sig),
            "entry_ts": int(ts[i]),
            "end_ts": int(ts[end_j]),
            "bars_held": int(end_j - i + 1),
            "exit_reason": why,
            "fill_px": float(fill_px),
            "mfe_price_pct": float(mfe),
            "mae_price_pct": float(mae),
            "mfe_margin_mult": float(mfe) * lev,
            "mae_margin_mult": float(mae) * lev,
            "atr14_pct": float(a14 / fill_px) if np.isfinite(a14) else float("nan"),
            "atr144_pct": float(a144 / fill_px) if np.isfinite(a144) else float("nan"),
            "atr_daily_pct": float(ad / fill_px) if np.isfinite(ad) else float("nan"),
            "two_n_atr14_pct": float(2.0 * a14 / fill_px) if np.isfinite(a14) else float("nan"),
            "two_n_atr144_pct": float(2.0 * a144 / fill_px) if np.isfinite(a144) else float("nan"),
            "two_n_daily_pct": float(2.0 * ad / fill_px) if np.isfinite(ad) else float("nan"),
            "channel_width_pct": float(ch_w / fill_px) if np.isfinite(ch_w) else float("nan"),
            "liq_price_pct": liq_pct,
            "leverage": lev,
            "half_exit_bars": int(cfg.window) // 2,
            "mfe_at_72_price_pct": float(mfe_at_72),
            "mae_at_72_price_pct": float(mae_at_72),
            "move_at_72_price_pct": float(move_at_72),
            "move_at_72_margin_mult": (
                float(move_at_72) * lev if np.isfinite(move_at_72) else float("nan")
            ),
            "hit_72_exit": first_72 is not None,
        }
        for m in TP_MARGIN_MULTS:
            row[f"first_tp_{m:g}x"] = first_tp[m]
        for name in first_stop:
            row[f"first_stop_{name}"] = first_stop[name]
        rows.append(row)
    return pd.DataFrame(rows)


def _hit_table(trades: pd.DataFrame) -> dict:
    """Share of clips where TP is touched before the stop (same bar = stop first)."""
    out: dict[str, dict] = {}
    if trades.empty:
        return out
    for stop_name in ("2pct_equity", "2N_daily", "2N_atr14", "liq"):
        stop_col = f"first_stop_{stop_name}"
        if stop_col not in trades.columns:
            continue
        bucket = {}
        for m in TP_MARGIN_MULTS:
            tp_col = f"first_tp_{m:g}x"
            tp_i = trades[tp_col]
            st_i = trades[stop_col]
            n = int(len(trades))
            tp_before = 0
            stop_before = 0
            neither = 0
            for t_hit, s_hit in zip(tp_i.to_numpy(), st_i.to_numpy()):
                t_ok = t_hit == t_hit and t_hit is not None
                s_ok = s_hit == s_hit and s_hit is not None
                if t_ok and (not s_ok or t_hit < s_hit):
                    tp_before += 1
                elif s_ok:
                    stop_before += 1
                else:
                    neither += 1
            bucket[f"{m:g}x"] = {
                "tp_before_stop": tp_before / n if n else None,
                "stop_before_tp": stop_before / n if n else None,
                "neither": neither / n if n else None,
                "n": n,
            }
        out[stop_name] = bucket
    return out


def summarize_excursions(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"n": 0}
    two_n_daily = trades["two_n_daily_pct"].to_numpy(dtype=float)
    liq = float(trades["liq_price_pct"].iloc[0])
    inside_liq = two_n_daily[np.isfinite(two_n_daily)]
    return {
        "n": int(len(trades)),
        "liq_exits": int((trades["exit_reason"] == "liq").sum()),
        "censored": int((trades["exit_reason"] == "censored_train_end").sum()),
        "mfe_price_pct": _pctiles(trades["mfe_price_pct"].to_numpy(dtype=float)),
        "mae_price_pct": _pctiles(trades["mae_price_pct"].to_numpy(dtype=float)),
        "mfe_margin_mult": _pctiles(trades["mfe_margin_mult"].to_numpy(dtype=float)),
        "mae_margin_mult": _pctiles(trades["mae_margin_mult"].to_numpy(dtype=float)),
        "atr14_pct": _pctiles(trades["atr14_pct"].to_numpy(dtype=float)),
        "atr144_pct": _pctiles(trades["atr144_pct"].to_numpy(dtype=float)),
        "atr_daily_pct": _pctiles(trades["atr_daily_pct"].to_numpy(dtype=float)),
        "two_n_atr14_pct": _pctiles(trades["two_n_atr14_pct"].to_numpy(dtype=float)),
        "two_n_daily_pct": _pctiles(trades["two_n_daily_pct"].to_numpy(dtype=float)),
        "channel_width_pct": _pctiles(trades["channel_width_pct"].to_numpy(dtype=float)),
        "move_at_72_margin_mult": _pctiles(
            trades["move_at_72_margin_mult"].to_numpy(dtype=float)
        ),
        "hit_72_exit_share": float(trades["hit_72_exit"].mean()),
        "two_n_daily_inside_liq_share": (
            float((inside_liq < liq).mean()) if inside_liq.size else None
        ),
        "mfe_reaches_5x_share": float((trades["mfe_margin_mult"] >= 5.0).mean()),
        "mfe_reaches_1x_share": float((trades["mfe_margin_mult"] >= 1.0).mean()),
        "mfe_reaches_0_2x_share": float((trades["mfe_margin_mult"] >= 0.2).mean()),
        "mae_hits_2pct_share": float((trades["mae_price_pct"] >= 0.02).mean()),
        "mae_hits_liq_share": float((trades["mae_price_pct"] >= liq).mean()),
        "hit_rates": _hit_table(trades),
    }


def prepare_exit_panel(panel: pd.DataFrame, cfg: DonchianConfig | None = None) -> pd.DataFrame:
    cfg = cfg or donchian_config()
    out = add_donchian(panel, cfg)
    out = add_atr(out, ATR14)
    out = add_atr(out, ATR144)
    out = add_daily_atr(out, DAILY_ATR_N)
    out = add_half_channel(out, cfg.window)
    return out
