"""
双动量 + 波动目标 + 金丝雀风控 的周度权重矩阵。

逻辑（清晰可实盘）：
1) 每周最后一个交易日（通常周五）收盘后计算信号；
2) 资产分 sleeve：安全垫(地方债) / 黄金 / A股红利低波 / 美股(标普/纳指/道指择强)；
3) 绝对动量：风险资产过去 abs_lb 周收益需跑赢债券；
4) 趋势过滤：收盘价 > sma_lb 日均线；
5) 相对动量排序，取 Top-K；
6) 逆波动加权，并缩放至 vol_target；
7) 金丝雀：若风险 sleeve 中短周期（1周）走弱个数 >= canary_k，则 100% 债券；
8) 换手迟滞：目标权重相对上期换手不足阈值则不调，降低无效交易。
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from config import CN, CODES, GOLD, PARAMS, SAFE, US_CANDIDATES


def week_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    week_id = index.to_series().dt.strftime("%Y-%W")
    return pd.DatetimeIndex(index.to_series().groupby(week_id.values).max().values)


def generate_target_weights(
    close: pd.DataFrame,
    params: dict | None = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    返回：
      weights: index=信号日(周末), columns=codes 的目标权重
      meta: 过程信息（美股选择、是否触发金丝雀等）
    """
    p = {**PARAMS, **(params or {})}
    mom_lb = int(p["mom_lb"])
    abs_lb = int(p["abs_lb"])
    vol_lb = int(p["vol_lb"])
    sma_lb = int(p["sma_lb"])
    vt = float(p["vol_target"])
    top_k = int(p["top_k"])
    max_single = float(p["max_single"])
    canary_k = int(p["canary_k"])
    thresh = float(p["rebalance_thresh"])

    we = week_ends(close.index)
    w_close = close.loc[we]
    daily_ret = close.pct_change()
    sma = close.rolling(sma_lb).mean()
    above = (close > sma).loc[we]
    vol = (daily_ret.rolling(vol_lb).std() * np.sqrt(252)).loc[we]

    raw_rows = []
    meta_rows = []
    for j, fri in enumerate(we):
        if j < max(mom_lb, abs_lb) + 1:
            continue
        rel = w_close.iloc[j] / w_close.iloc[j - mom_lb] - 1
        abs_m = w_close.iloc[j] / w_close.iloc[j - abs_lb] - 1
        short = w_close.iloc[j] / w_close.iloc[j - 1] - 1

        us_avail = [c for c in US_CANDIDATES if pd.notna(rel.get(c))]
        us_pick = max(us_avail, key=lambda c: rel[c]) if us_avail else None
        risk = [c for c in [GOLD, CN] if pd.notna(rel.get(c))]
        if us_pick:
            risk.append(us_pick)

        breadth_weak = sum(1 for c in risk if pd.notna(short.get(c)) and short[c] < 0)
        w = pd.Series(0.0, index=CODES)
        regime = "risk_on"
        elig = []

        if breadth_weak >= canary_k:
            w[SAFE] = 1.0
            regime = "canary_safe"
        else:
            thr = float(abs_m[SAFE]) if pd.notna(abs_m.get(SAFE)) else 0.0
            elig = [
                c
                for c in risk
                if pd.notna(abs_m.get(c))
                and abs_m[c] > thr
                and bool(above.loc[fri, c])
            ]
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
                scale = min(1.0, vt / max(pvol, 1e-6))
                for a, c in enumerate(elig):
                    w[c] = min(float(raw[a] * scale), max_single)
                if w.sum() > 1:
                    w /= w.sum()
                w[SAFE] = max(0.0, 1.0 - float(w.sum()))
                regime = "allocated"

        raw_rows.append(w.rename(fri))
        meta_rows.append(
            {
                "signal_date": fri,
                "regime": regime,
                "us_pick": us_pick,
                "eligible": ",".join(elig),
                "breadth_weak": breadth_weak,
                "safe_w": float(w[SAFE]),
                "gold_w": float(w[GOLD]),
                "cn_w": float(w[CN]),
                "us_w": float(w[[c for c in US_CANDIDATES]].sum()),
            }
        )

    raw_w = pd.DataFrame(raw_rows).reindex(columns=CODES).fillna(0.0)
    # 换手迟滞
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
