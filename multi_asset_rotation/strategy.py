"""
升级版周度轮动：
1) 双动量 + 趋势过滤 + Top-K 逆波动
2) 条件杠杆：强趋势时允许 gross>1
3) 组合回撤断路器：基于“迟滞后实盘权重”的 sim NAV
4) 金丝雀 + 换手迟滞（迟滞在周循环内生效，与 sim/breaker 对齐）
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
    boost_min_n = int(p.get("boost_min_n", 2))
    dd_stop = float(p.get("dd_stop", 0.06))
    dd_resume = float(p.get("dd_resume", 0.02))
    mom_strength = float(p.get("mom_strength", 0.0))
    exposure_floor = float(p.get("exposure_floor", 0.0))
    weak_scale = float(p.get("weak_scale", 0.5))
    borrow_rate = float(p.get("borrow_rate", 0.03))
    upside_vol_boost = float(p.get("upside_vol_boost", 1.0))
    lev_dd_cap = float(p.get("lev_dd_cap", 0.99))

    we = week_ends(close.index)
    w_close = close.loc[we]
    daily_ret = close.pct_change().fillna(0.0)
    sma = close.rolling(sma_lb).mean()
    above = (close > sma * (1.0 + sma_buffer)).loc[we]
    sma_up = (sma > sma.shift(sma_slope_lb)).loc[we] if sma_slope_lb > 0 else None
    vol = (daily_ret.rolling(vol_lb).std() * np.sqrt(252)).loc[we]

    raw_rows = []
    exec_rows = []
    meta_rows = []
    sim_nav = 1.0
    sim_peak = 1.0
    breaker_on = False
    prev_exec = None
    prev_gross = 1.0

    for j, fri in enumerate(we):
        if j < max(mom_lb, abs_lb) + 1:
            continue

        # 用“上一期迟滞后实盘权重”推进 sim NAV，与真实再平衡路径对齐
        if prev_exec is not None and j >= 1:
            prev_fri = we[j - 1]
            seg = daily_ret.loc[prev_fri:fri].iloc[1:]
            if len(seg) > 0:
                w_use = prev_exec.reindex(CODES).fillna(0.0)
                for _, row in seg.iterrows():
                    rr = row.reindex(CODES).fillna(0.0)
                    s = float(w_use.sum())
                    if s <= 0:
                        r = 0.0
                    elif prev_gross <= 1.0 + 1e-9:
                        ww = w_use / s
                        r = float((ww * rr).sum())
                    else:
                        ww = w_use / s * prev_gross
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
        proposed = pd.Series(0.0, index=CODES)
        regime = "risk_on"
        elig = []
        target_gross = 1.0

        if breaker_on:
            proposed[SAFE] = 1.0
            regime = "dd_breaker"
            target_gross = 1.0
        elif breadth_weak >= canary_k:
            proposed[SAFE] = 1.0
            regime = "canary_safe"
            target_gross = 1.0
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
                proposed[SAFE] = 1.0
                regime = "no_eligible"
                target_gross = 1.0
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
                raw_scale = vt / max(pvol, 1e-6)
                base_scale = min(1.0, raw_scale)
                if weak_scale < 0.999 and best_mom < mom_strength + 0.02 and len(elig) == 1:
                    base_scale *= weak_scale
                    regime = "weak"
                base_scale = max(exposure_floor, base_scale)

                target_gross = base_scale
                sim_dd = sim_nav / sim_peak - 1.0
                allow_leverage = sim_dd > -abs(lev_dd_cap)
                if (
                    allow_leverage
                    and max_gross > 1.0
                    and best_mom >= boost_mom
                    and len(elig) >= boost_min_n
                    and breadth_weak <= max(1, canary_k - 2)
                ):
                    lev = max(1.0, raw_scale)
                    if upside_vol_boost > 1.0 and lev > 1.0:
                        lev = 1.0 + (lev - 1.0) * upside_vol_boost
                    lev = min(max_gross, lev)
                    if lev > 1.0:
                        target_gross = lev
                        regime = "risk_on_boost"

                for a, c in enumerate(elig):
                    cap = (
                        max_single
                        if target_gross <= 1.0
                        else max_single * min(target_gross, max_gross)
                    )
                    proposed[c] = min(float(raw[a] * target_gross), cap)
                gross = float(proposed.sum())
                if target_gross <= 1.0 + 1e-12:
                    if gross > 1.0:
                        proposed /= gross
                        gross = 1.0
                    proposed[SAFE] = max(0.0, 1.0 - gross)
                else:
                    if gross > max_gross:
                        proposed *= max_gross / gross
                        gross = float(proposed.sum())
                    proposed[SAFE] = 0.0

                # 按实际毛敞口修正标签，避免 boost 名不副实
                if float(proposed.sum()) > 1.01:
                    regime = "risk_on_boost"
                elif regime == "risk_on_boost":
                    regime = "allocated"
                elif regime == "risk_on" and float(proposed[SAFE]) < 0.01:
                    regime = "allocated"

        # 换手迟滞：在周循环内生效，后续 sim/breaker 跟踪的是执行权重
        hysteresis_hold = False
        proposed_regime = regime
        if prev_exec is None:
            executed = proposed.copy()
        else:
            turn = float((proposed - prev_exec).abs().sum()) / 2.0
            if turn >= thresh:
                executed = proposed.copy()
            else:
                executed = prev_exec.copy()
                hysteresis_hold = True

        # 对外 regime 以执行后真实敞口为准
        exec_gross = float(executed.sum())
        if hysteresis_hold:
            if exec_gross > 1.01:
                regime = "risk_on_boost|hold"
            elif float(executed[SAFE]) >= 0.999:
                regime = "safe|hold"
            else:
                regime = "allocated|hold"
        elif exec_gross > 1.01:
            regime = "risk_on_boost"
        elif proposed_regime in ("dd_breaker", "canary_safe", "no_eligible", "weak"):
            regime = proposed_regime
        elif float(executed[SAFE]) >= 0.999:
            regime = proposed_regime if proposed_regime.endswith("safe") or "eligible" in proposed_regime or "breaker" in proposed_regime else "safe"
        elif float(executed[SAFE]) < 0.01:
            regime = "allocated"
        else:
            regime = "allocated"

        raw_rows.append(proposed.rename(fri))
        exec_rows.append(executed.rename(fri))
        meta_rows.append(
            {
                "signal_date": fri,
                "regime": regime,
                "proposed_regime": proposed_regime,
                "us_pick": us_pick,
                "eligible": ",".join(elig),
                "breadth_weak": breadth_weak,
                "proposed_gross": float(proposed.sum()),
                "gross": exec_gross,
                "target_gross": float(target_gross),
                "hysteresis_hold": int(hysteresis_hold),
                "breaker": int(breaker_on),
                "sim_nav": float(sim_nav),
                "safe_w": float(executed[SAFE]),
                "gold_w": float(executed[GOLD]),
                "cn_w": float(executed[CN]),
                "hk_w": float(executed.get(HK, 0.0)),
                "vig_w": float(executed.get(VIG, 0.0)),
                "us_w": float(executed[[c for c in US_CANDIDATES if c in executed.index]].sum()),
            }
        )
        prev_exec = executed
        prev_gross = float(executed.sum()) if float(executed.sum()) > 0 else 1.0

    raw_w = pd.DataFrame(raw_rows).reindex(columns=CODES).fillna(0.0)
    weights = pd.DataFrame(exec_rows).reindex(columns=CODES).fillna(0.0)
    meta = pd.DataFrame(meta_rows).set_index("signal_date")
    return weights, {"meta": meta, "raw_weights": raw_w, "week_ends": we}
