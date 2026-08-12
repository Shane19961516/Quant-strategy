"""
升级版周度轮动：
1) 双动量 + 趋势过滤 + Top-K 逆波动
2) 条件杠杆：强趋势时允许 gross>1
3) 组合回撤断路器：策略净值回撤超阈值强制债券
4) 金丝雀 + 换手迟滞
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from config import CN, CODES, CORE_RISK, GOLD, HK, PARAMS, SAFE, US_CANDIDATES, VIG


def week_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    week_id = index.to_series().dt.strftime("%Y-%W")
    return pd.DatetimeIndex(index.to_series().groupby(week_id.values).max().values)


def generate_target_weights(
    close: pd.DataFrame,
    params: dict | None = None,
) -> Tuple[pd.DataFrame, Dict]:
    p = {**PARAMS, **(params or {})}
    mom_lb = int(p["mom_lb"])
    abs_lb = int(p["abs_lb"])
    vol_lb = int(p["vol_lb"])
    sma_lb = int(p["sma_lb"])
    abs_margin = float(p.get("abs_margin", 0.0))
    require_abs_pos = bool(p.get("require_abs_pos", False))
    sma_buffer = float(p.get("sma_buffer", 0.0))
    sma_slope_lb = int(p.get("sma_slope_lb", 0))
    vt = float(p["vol_target"])
    top_k = int(p["top_k"])
    max_single = float(p["max_single"])
    canary_k = int(p["canary_k"])
    thresh = float(p["rebalance_thresh"])

    max_gross = float(p.get("max_gross", 1.0))
    boost_mom = float(p.get("boost_mom", 0.08))
    boost_min_n = int(p.get("boost_min_n", 2))  # 至少 N 个合格才加杠杆
    dd_stop = float(p.get("dd_stop", 0.06))
    dd_resume = float(p.get("dd_resume", 0.02))
    mom_strength = float(p.get("mom_strength", 0.0))
    exposure_floor = float(p.get("exposure_floor", 0.0))  # 风险仓下限缩放（弱信号时）
    weak_scale = float(p.get("weak_scale", 0.5))  # 弱信号时风险仓缩放
    borrow_rate = float(p.get("borrow_rate", 0.03))

    we = week_ends(close.index)
    w_close = close.loc[we]
    daily_ret = close.pct_change().fillna(0.0)
    sma = close.rolling(sma_lb).mean()
    above = (close > sma * (1.0 + sma_buffer)).loc[we]
    sma_up = (sma > sma.shift(sma_slope_lb)).loc[we] if sma_slope_lb > 0 else None
    vol = (daily_ret.rolling(vol_lb).std() * np.sqrt(252)).loc[we]

    raw_rows = []
    meta_rows = []
    sim_nav = 1.0
    sim_peak = 1.0
    breaker_on = False
    prev_w = None
    prev_gross = 1.0

    for j, fri in enumerate(we):
        if j < max(mom_lb, abs_lb) + 1:
            continue

        if prev_w is not None and j >= 1:
            prev_fri = we[j - 1]
            seg = daily_ret.loc[prev_fri:fri].iloc[1:]
            if len(seg) > 0:
                w_use = prev_w.reindex(CODES).fillna(0.0)
                for _, row in seg.iterrows():
                    rr = row.reindex(CODES).fillna(0.0)
                    if prev_gross <= 1.0 + 1e-9:
                        s = float(w_use.sum())
                        ww = (w_use / s) if s > 0 else w_use
                        r = float((ww * rr).sum())
                    else:
                        s = float(w_use.sum())
                        ww = (w_use / s * prev_gross) if s > 0 else w_use
                        r = float((ww * rr).sum()) - (prev_gross - 1.0) * borrow_rate / 252.0
                    sim_nav *= 1.0 + r
            sim_peak = max(sim_peak, sim_nav)
            dd = sim_nav / sim_peak - 1.0
            if (not breaker_on) and dd <= -abs(dd_stop):
                breaker_on = True
            if breaker_on and dd >= -abs(dd_resume):
                breaker_on = False

        rel = w_close.iloc[j] / w_close.iloc[j - mom_lb] - 1
        abs_m = w_close.iloc[j] / w_close.iloc[j - abs_lb] - 1
        short = w_close.iloc[j] / w_close.iloc[j - 1] - 1

        us_avail = [c for c in US_CANDIDATES if c in close.columns and pd.notna(rel.get(c))]
        us_pick = max(us_avail, key=lambda c: rel[c]) if us_avail else None
        risk = [c for c in CORE_RISK if c in close.columns and pd.notna(rel.get(c))]
        if us_pick:
            risk.append(us_pick)

        breadth_weak = sum(1 for c in risk if pd.notna(short.get(c)) and short[c] < 0)
        w = pd.Series(0.0, index=CODES)
        regime = "risk_on"
        elig = []

        if breaker_on:
            w[SAFE] = 1.0
            regime = "dd_breaker"
        elif breadth_weak >= canary_k:
            w[SAFE] = 1.0
            regime = "canary_safe"
        else:
            thr = float(abs_m[SAFE]) if pd.notna(abs_m.get(SAFE)) else 0.0
            for c in risk:
                if not pd.notna(abs_m.get(c)):
                    continue
                if abs_m[c] <= thr + abs_margin:
                    continue
                if require_abs_pos and abs_m[c] <= 0.0:
                    continue
                if not bool(above.loc[fri, c]):
                    continue
                if sma_up is not None and not bool(sma_up.loc[fri, c]):
                    continue
                if mom_strength > 0 and float(rel[c]) < mom_strength:
                    continue
                elig.append(c)
            elig = sorted(elig, key=lambda c: rel[c], reverse=True)[:top_k]
            if not elig:
                w[SAFE] = 1.0
                regime = "no_eligible"
            else:
                vols = np.array(
                    [
                        max(
                            float(vol.loc[fri, c]) if pd.notna(vol.loc[fri, c]) else 0.2,
                            0.05,
                        )
                        for c in elig
                    ]
                )
                inv = 1.0 / vols
                raw = inv / inv.sum()
                if len(elig) == 1:
                    pvol = float(vols[0])
                else:
                    hist = daily_ret[elig].loc[:fri].tail(vol_lb)
                    cov = hist.cov().values * 252
                    pvol = float(np.sqrt(max(raw @ cov @ raw, 0.0)))

                best_mom = float(rel[elig[0]])
                upside_vol_boost = float(p.get("upside_vol_boost", 1.0))
                raw_scale = vt / max(pvol, 1e-6)
                base_scale = min(1.0, raw_scale)
                # 弱信号降仓（仅 weak_scale<1 时生效）
                if weak_scale < 0.999 and best_mom < mom_strength + 0.02 and len(elig) == 1:
                    base_scale *= weak_scale
                    regime = "weak"
                base_scale = max(exposure_floor, base_scale)

                target_gross = base_scale
                lev_dd_cap = float(p.get("lev_dd_cap", 0.99))  # sim回撤超阈值则禁止加杠杆
                sim_dd = sim_nav / sim_peak - 1.0
                allow_leverage = sim_dd > -abs(lev_dd_cap)
                if (
                    allow_leverage
                    and max_gross > 1.0
                    and best_mom >= boost_mom
                    and len(elig) >= boost_min_n
                    and breadth_weak <= max(1, canary_k - 2)
                ):
                    # 低波动时提高杠杆；upside_vol_boost>1 时放大上行杠杆
                    lev = max(1.0, raw_scale)
                    if upside_vol_boost > 1.0 and lev > 1.0:
                        lev = 1.0 + (lev - 1.0) * upside_vol_boost
                    lev = min(max_gross, lev)
                    if lev > 1.0:
                        target_gross = lev
                        regime = "risk_on_boost"

                for a, c in enumerate(elig):
                    cap = max_single if target_gross <= 1.0 else max_single * min(target_gross, max_gross)
                    w[c] = min(float(raw[a] * target_gross), cap)
                gross = float(w.sum())
                if target_gross <= 1.0 + 1e-12:
                    # 无杠杆：与旧版一致，超配则归一
                    if gross > 1.0:
                        w /= gross
                        gross = 1.0
                    w[SAFE] = max(0.0, 1.0 - gross)
                else:
                    if gross > max_gross:
                        w *= max_gross / gross
                        gross = float(w.sum())
                    w[SAFE] = 0.0
                if regime == "risk_on" and float(w[SAFE]) < 0.01:
                    regime = "allocated"

        raw_rows.append(w.rename(fri))
        meta_rows.append(
            {
                "signal_date": fri,
                "regime": regime,
                "us_pick": us_pick,
                "eligible": ",".join(elig),
                "breadth_weak": breadth_weak,
                "gross": float(w.sum()),
                "breaker": int(breaker_on),
                "sim_nav": float(sim_nav),
                "safe_w": float(w[SAFE]),
                "gold_w": float(w[GOLD]),
                "cn_w": float(w[CN]),
                "hk_w": float(w.get(HK, 0.0)),
                "vig_w": float(w.get(VIG, 0.0)),
                "us_w": float(w[[c for c in US_CANDIDATES if c in w.index]].sum()),
            }
        )
        prev_w = w
        prev_gross = float(w.sum()) if float(w.sum()) > 0 else 1.0

    raw_w = pd.DataFrame(raw_rows).reindex(columns=CODES).fillna(0.0)
    final_rows = []
    prev = None
    for dt, row in raw_w.iterrows():
        if prev is None:
            final_rows.append(row)
            prev = row
            continue
        turn = float((row - prev).abs().sum()) / 2.0
        if turn >= thresh:
            final_rows.append(row)
            prev = row
        else:
            final_rows.append(prev)
    weights = pd.DataFrame(final_rows, index=raw_w.index).reindex(columns=CODES)
    meta = pd.DataFrame(meta_rows).set_index("signal_date")
    return weights, {"meta": meta, "raw_weights": raw_w, "week_ends": we}
