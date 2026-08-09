"""
周度轮动：信号层选方向 + 权重层非对称调解（最终版+）。

信号层：
1) 周五收盘计算动量/趋势
2) 美股三选一（相对动量最强）
3) 绝对动量跑赢债 + 站上均线 -> 可进攻
4) 金丝雀（短线普跌）作为防守信号

权重层（非对称调解 + YTD 油门）：
1) 战术仓：合格风险资产逆波动加权 + vol_budget 风险预算
2) 进攻期：sleeve = (1-tilt)*中枢 + tilt*战术，并裁剪偏离
3) 防守期：跳过中枢，提高债券地板，权重即时落地
4) YTD 油门：当年已实现收益偏高时降低战术倾斜/风险预算；偏低时略增进攻
5) 换手阈值（进攻期）
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from config import (
    CN,
    CODES,
    GOLD,
    PARAMS,
    SAFE,
    SLEEVES,
    US_CANDIDATES,
)


def week_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    week_id = index.to_series().dt.strftime("%Y-%W")
    return pd.DatetimeIndex(index.to_series().groupby(week_id.values).max().values)


def _clip_to_center(tactical: Dict[str, float], center: Dict[str, float], max_dev: float) -> Dict[str, float]:
    out = {}
    for k in SLEEVES:
        c = float(center.get(k, 0.0))
        x = float(tactical.get(k, 0.0))
        out[k] = float(np.clip(x, c - max_dev, c + max_dev))
    s = sum(out.values())
    if s <= 0:
        return dict(center)
    return {k: out[k] / s for k in SLEEVES}


def _blend(center: Dict[str, float], tactical: Dict[str, float], tilt: float) -> Dict[str, float]:
    tilt = float(np.clip(tilt, 0.0, 1.0))
    out = {k: (1 - tilt) * float(center.get(k, 0.0)) + tilt * float(tactical.get(k, 0.0)) for k in SLEEVES}
    s = sum(out.values())
    return {k: out[k] / s for k in SLEEVES} if s > 0 else dict(center)


def _inv_vol_weights(
    assets: list[str],
    fri: pd.Timestamp,
    vol: pd.DataFrame,
    daily_ret: pd.DataFrame,
    vol_lb: int,
    vol_budget: float,
    max_single: float,
) -> Dict[str, float]:
    if not assets:
        return {}
    vols = np.array(
        [
            max(float(vol.loc[fri, c]) if pd.notna(vol.loc[fri, c]) else 0.20, 0.05)
            for c in assets
        ]
    )
    inv = 1.0 / vols
    raw = inv / inv.sum()
    if len(assets) == 1:
        pvol = float(vols[0])
    else:
        hist = daily_ret[assets].loc[:fri].tail(vol_lb)
        cov = hist.cov().values * 252
        pvol = float(np.sqrt(max(raw @ cov @ raw, 0.0)))
    scale = min(1.0, vol_budget / max(pvol, 1e-6))
    out = {}
    for i, c in enumerate(assets):
        out[c] = min(float(raw[i] * scale), max_single)
    s = sum(out.values())
    if s > 1.0:
        out = {k: v / s for k, v in out.items()}
    return out


def _asset_from_sleeve(
    sleeve_w: Dict[str, float],
    us_pick: str | None,
) -> pd.Series:
    w = pd.Series(0.0, index=CODES)
    w[SAFE] = sleeve_w["bond"]
    if sleeve_w["gold"] > 0:
        w[GOLD] = sleeve_w["gold"]
    if sleeve_w["cn"] > 0:
        w[CN] = sleeve_w["cn"]
    if sleeve_w["us"] > 0:
        if us_pick:
            w[us_pick] = sleeve_w["us"]
        else:
            w[SAFE] += sleeve_w["us"]
    w = w.clip(lower=0.0)
    s = float(w.sum())
    if s <= 0:
        w[SAFE] = 1.0
    else:
        w = w / s
    return w


def _ytd_adjust(
    ytd: float,
    tilt: float,
    vol_budget: float,
    max_dev: float,
    p: dict,
) -> Tuple[float, float, float, float]:
    """根据当年已实现收益调节进攻强度。返回 tilt, vol_budget, max_dev, damp."""
    cap = float(p.get("ytd_soft_cap", 0.12))
    floor = float(p.get("ytd_soft_floor", 0.02))
    span = max(float(p.get("ytd_span", 0.10)), 1e-6)
    dampen = float(p.get("ytd_dampen", 0.40))
    boost = float(p.get("ytd_boost", 0.06))

    damp = 0.0
    if ytd > cap:
        damp = float(np.clip((ytd - cap) / span, 0.0, 1.0)) * dampen
        tilt_eff = tilt * (1.0 - damp)
        vol_eff = vol_budget * (1.0 - 0.55 * damp)
        max_dev_eff = max_dev * (1.0 - 0.35 * damp)
    elif ytd < floor:
        short = float(np.clip((floor - ytd) / max(floor, 1e-6), 0.0, 1.0)) * boost
        tilt_eff = min(1.0, tilt + short)
        vol_eff = vol_budget * (1.0 + 0.35 * short)
        max_dev_eff = min(1.0, max_dev + 0.10 * short)
        damp = -short
    else:
        tilt_eff, vol_eff, max_dev_eff = tilt, vol_budget, max_dev
    return float(tilt_eff), float(vol_eff), float(max_dev_eff), float(damp)


def generate_target_weights(
    close: pd.DataFrame,
    params: dict | None = None,
) -> Tuple[pd.DataFrame, Dict]:
    p = {**PARAMS, **(params or {})}
    mom_lb = int(p["mom_lb"])
    abs_lb = int(p["abs_lb"])
    vol_lb = int(p["vol_lb"])
    sma_lb = int(p["sma_lb"])
    canary_k = int(p["canary_k"])
    top_k = int(p.get("top_k", 2))
    vol_budget = float(p.get("vol_budget", p.get("vol_target", 0.08)))
    center = {k: float(v) for k, v in p["neutral_sleeve"].items()}
    tilt = float(p["active_tilt"])
    max_dev = float(p["max_sleeve_dev"])
    ema = float(p["weight_ema"])
    canary_boost = float(p["bond_canary_boost"])
    min_bond = float(p["min_bond"])
    max_single = float(p["max_single_asset"])
    thresh = float(p["rebalance_thresh"])
    defense_skip_center = bool(p.get("defense_skip_center", True))
    canary_bond_floor = float(p.get("canary_bond_floor", 0.85))
    use_ytd_throttle = bool(p.get("use_ytd_throttle", True))

    we = week_ends(close.index)
    w_close = close.loc[we]
    daily_ret = close.pct_change()
    sma = close.rolling(sma_lb).mean()
    above = (close > sma).loc[we]
    vol = (daily_ret.rolling(vol_lb).std() * np.sqrt(252)).loc[we]

    raw_rows = []
    meta_rows = []
    prev_w = None
    held_w = None
    shadow_nav = 1.0
    year_start_nav = 1.0
    prev_year = None

    for j, fri in enumerate(we):
        if j < max(mom_lb, abs_lb) + 1:
            continue

        # 用上一期持仓更新影子净值（无前视：本周收益在周五已知）
        if held_w is not None and j >= 1:
            r = (w_close.iloc[j] / w_close.iloc[j - 1] - 1).reindex(CODES)
            avail = r.notna()
            hw = held_w.reindex(CODES).fillna(0.0)
            hw = hw.where(avail, 0.0)
            if float(hw.sum()) > 0:
                hw = hw / hw.sum()
                shadow_nav *= 1.0 + float((hw * r.fillna(0.0)).sum())

        if prev_year is None or fri.year != prev_year:
            year_start_nav = shadow_nav
            prev_year = fri.year
        ytd = shadow_nav / max(year_start_nav, 1e-12) - 1.0

        rel = w_close.iloc[j] / w_close.iloc[j - mom_lb] - 1
        abs_m = w_close.iloc[j] / w_close.iloc[j - abs_lb] - 1
        short = w_close.iloc[j] / w_close.iloc[j - 1] - 1

        us_avail = [c for c in US_CANDIDATES if pd.notna(rel.get(c))]
        us_pick = max(us_avail, key=lambda c: rel[c]) if us_avail else None

        risk = [c for c in [GOLD, CN] if pd.notna(rel.get(c))]
        if us_pick:
            risk.append(us_pick)

        breadth_weak = sum(1 for c in risk if pd.notna(short.get(c)) and short[c] < 0)
        canary = breadth_weak >= canary_k

        thr = float(abs_m[SAFE]) if pd.notna(abs_m.get(SAFE)) else 0.0
        elig = [
            c
            for c in risk
            if pd.notna(abs_m.get(c)) and abs_m[c] > thr and bool(above.loc[fri, c])
        ]
        elig = sorted(elig, key=lambda c: rel[c], reverse=True)[:top_k]

        if use_ytd_throttle:
            tilt_eff, vol_eff, max_dev_eff, ytd_damp = _ytd_adjust(ytd, tilt, vol_budget, max_dev, p)
        else:
            tilt_eff, vol_eff, max_dev_eff, ytd_damp = tilt, vol_budget, max_dev, 0.0

        # ---- 战术资产权重 ----
        if not elig:
            tactical_asset = {SAFE: 1.0}
            regime = "bond_only"
            defensive = True
        else:
            rw = _inv_vol_weights(elig, fri, vol, daily_ret, vol_lb, vol_eff, max_single)
            tactical_asset = {SAFE: max(0.0, 1.0 - sum(rw.values())), **rw}
            regime = "allocated"
            defensive = False

        tactical = {
            "bond": float(tactical_asset.get(SAFE, 0.0)),
            "gold": float(tactical_asset.get(GOLD, 0.0)),
            "cn": float(tactical_asset.get(CN, 0.0)),
            "us": float(sum(tactical_asset.get(c, 0.0) for c in US_CANDIDATES)),
        }
        s = sum(tactical.values())
        tactical = {k: tactical[k] / s for k in SLEEVES} if s > 0 else {"bond": 1.0, "gold": 0.0, "cn": 0.0, "us": 0.0}

        # ---- 非对称权重调解 ----
        if canary:
            if defense_skip_center:
                sleeve_w = dict(tactical)
            else:
                sleeve_w = _blend(center, tactical, tilt=max(tilt_eff, 0.85))
            risk_sum = sleeve_w["gold"] + sleeve_w["cn"] + sleeve_w["us"]
            target_bond = max(canary_bond_floor, sleeve_w["bond"] + min(canary_boost, risk_sum))
            target_bond = min(1.0, target_bond)
            need = target_bond - sleeve_w["bond"]
            if need > 0 and risk_sum > 0:
                take = min(need, risk_sum)
                for k in ["gold", "cn", "us"]:
                    sleeve_w[k] *= (risk_sum - take) / risk_sum
                sleeve_w["bond"] += take
            regime = "canary_tilt"
            defensive = True
        elif defensive:
            sleeve_w = {"bond": 1.0, "gold": 0.0, "cn": 0.0, "us": 0.0}
        else:
            sleeve_w = _blend(center, tactical, tilt=tilt_eff)
            sleeve_w = _clip_to_center(sleeve_w, center, max_dev=max_dev_eff)
            # YTD 偏高时额外抬升债券地板
            bond_floor = min_bond + max(0.0, ytd_damp) * float(p.get("ytd_extra_bond", 0.08))
            if sleeve_w["bond"] < bond_floor:
                need = bond_floor - sleeve_w["bond"]
                risk_sum = sleeve_w["gold"] + sleeve_w["cn"] + sleeve_w["us"]
                if risk_sum > need and risk_sum > 0:
                    for k in ["gold", "cn", "us"]:
                        sleeve_w[k] *= (risk_sum - need) / risk_sum
                    sleeve_w["bond"] = bond_floor

        w = _asset_from_sleeve(sleeve_w, us_pick)

        for c in CODES:
            if c == SAFE:
                continue
            if w[c] > max_single:
                overflow = w[c] - max_single
                w[c] = max_single
                w[SAFE] += overflow
        w = w / w.sum()

        if prev_w is None:
            final = w
        elif defensive:
            final = w
        else:
            final = ema * w + (1.0 - ema) * prev_w
            final = final / final.sum()
            turn = float((final - prev_w).abs().sum()) / 2.0
            if turn < thresh:
                final = prev_w
        prev_w = final
        held_w = final

        raw_rows.append(final.rename(fri))
        meta_rows.append(
            {
                "signal_date": fri,
                "regime": regime,
                "us_pick": us_pick,
                "canary": int(canary),
                "breadth_weak": breadth_weak,
                "eligible": ",".join(elig),
                "ytd": float(ytd),
                "ytd_damp": float(ytd_damp),
                "tilt_eff": float(tilt_eff),
                "vol_eff": float(vol_eff),
                "sleeve_bond": sleeve_w["bond"],
                "sleeve_gold": sleeve_w["gold"],
                "sleeve_cn": sleeve_w["cn"],
                "sleeve_us": sleeve_w["us"],
                "safe_w": float(final[SAFE]),
                "gold_w": float(final[GOLD]),
                "cn_w": float(final[CN]),
                "us_w": float(final[[c for c in US_CANDIDATES]].sum()),
            }
        )

    weights = pd.DataFrame(raw_rows).reindex(columns=CODES).fillna(0.0)
    meta = pd.DataFrame(meta_rows).set_index("signal_date")
    return weights, {"meta": meta, "week_ends": we, "params": p}
