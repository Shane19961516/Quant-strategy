"""策略引擎：为 Web 端提供回测、监控、预计收益数据。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest import run_backtest
from config import CN, CODES, CORE_RISK, GOLD, HK, PARAMS, STRATEGY_START, UNIVERSE
from data import build_panels, load_universe
from metrics import latest_rebalance_instruction, perf_stats, yearly_returns
from strategy import generate_target_weights, week_ends

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


@dataclass
class EngineSnapshot:
    close: pd.DataFrame
    signal_w: pd.DataFrame
    nav: pd.Series
    weights_daily: pd.DataFrame
    trades: pd.DataFrame
    meta: pd.DataFrame
    stats: dict
    order: dict


def _pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{digits}%}"


class StrategyEngine:
    def __init__(self):
        self.snap: EngineSnapshot | None = None

    def refresh(self, force_download: bool = False) -> EngineSnapshot:
        raw = load_universe(force=force_download)
        close, _ = build_panels(raw)
        close = close.loc[STRATEGY_START:]
        signal_w, info = generate_target_weights(close, PARAMS)
        nav, weights_daily, trades = run_backtest(
            close,
            signal_w,
            cost_bps=PARAMS["cost_bps"],
            borrow_rate=PARAMS.get("borrow_rate", 0.03),
            daily_dd_stop=PARAMS.get("daily_dd_stop", 0.99),
            daily_dd_resume=PARAMS.get("daily_dd_resume", 0.02),
            stop_only_levered=PARAMS.get("stop_only_levered", True),
            stop_vol_mult=PARAMS.get("stop_vol_mult", 1.0),
            dd_action=PARAMS.get("dd_action", "delever"),
            resume_on_rebalance=PARAMS.get("resume_on_rebalance", True),
        )
        stats = perf_stats(nav)
        order = latest_rebalance_instruction(signal_w, weights_daily, close.index)
        meta = info["meta"]
        self.snap = EngineSnapshot(
            close=close,
            signal_w=signal_w,
            nav=nav,
            weights_daily=weights_daily,
            trades=trades,
            meta=meta,
            stats=stats,
            order=order,
        )
        return self.snap

    def ensure(self) -> EngineSnapshot:
        if self.snap is None:
            return self.refresh()
        return self.snap

    # -------- research --------
    def research_context(self) -> dict[str, Any]:
        s = self.ensure()
        yearly = yearly_returns(s.nav)
        return {
            "params": PARAMS,
            "universe": UNIVERSE,
            "stats": s.stats,
            "yearly": {str(k): float(v) for k, v in yearly.items()},
            "targets": {
                "sharpe_rf0>=2": bool(s.stats.get("sharpe_rf0", 0) >= 2),
                "ann_return>=15%": bool(s.stats.get("ann_return", 0) >= 0.15),
                "max_drawdown<=7%": bool(s.stats.get("max_drawdown", -1) >= -0.07),
            },
        }

    # -------- monitor --------
    def monitor_context(self) -> dict[str, Any]:
        s = self.ensure()
        yearly = yearly_returns(s.nav)
        dd = s.nav / s.nav.cummax() - 1
        last_meta = s.meta.iloc[-1].to_dict() if len(s.meta) else {}
        # 当前持仓：最近已执行权重；目标：最新信号
        cur = s.weights_daily.iloc[-1].reindex(CODES).fillna(0.0)
        tgt = s.signal_w.iloc[-1].reindex(CODES).fillna(0.0)

        # 指标面板
        rolling = s.nav.pct_change()
        vol_20 = float(rolling.tail(20).std() * np.sqrt(252)) if len(rolling) > 20 else np.nan
        ret_5 = float((1 + rolling.tail(5)).prod() - 1) if len(rolling) >= 5 else np.nan
        ret_20 = float((1 + rolling.tail(20)).prod() - 1) if len(rolling) >= 20 else np.nan

        # 风险资产短线状态（金丝雀监控）
        we = week_ends(s.close.index)
        w_close = s.close.loc[we]
        short = w_close.iloc[-1] / w_close.iloc[-2] - 1
        us_pick = last_meta.get("us_pick")
        risk_codes = [c for c in CORE_RISK if c in short.index]
        if us_pick in short.index:
            risk_codes.append(us_pick)
        canary_rows = []
        for c in risk_codes:
            canary_rows.append(
                {
                    "code": c,
                    "name": UNIVERSE[c]["name"],
                    "week_return": float(short[c]) if pd.notna(short[c]) else None,
                    "weak": bool(pd.notna(short[c]) and short[c] < 0),
                }
            )

        holdings = []
        for c in CODES:
            holdings.append(
                {
                    "code": c,
                    "name": UNIVERSE[c]["name"],
                    "current": float(cur[c]),
                    "target": float(tgt[c]),
                    "delta": float(tgt[c] - cur[c]),
                }
            )

        # NAV 序列给前端画图
        nav_tail = s.nav.tail(252)
        dd_tail = dd.tail(252)

        return {
            "stats": s.stats,
            "yearly": {str(k): float(v) for k, v in yearly.items()},
            "order": s.order,
            "last_meta": {
                "signal_date": str(s.meta.index[-1].date()) if len(s.meta) else None,
                "regime": last_meta.get("regime"),
                "us_pick": us_pick,
                "us_pick_name": UNIVERSE.get(us_pick, {}).get("name") if us_pick else None,
                "breadth_weak": int(last_meta.get("breadth_weak", 0) or 0),
                "eligible": last_meta.get("eligible", ""),
                "safe_w": float(last_meta.get("safe_w", 0) or 0),
                "gold_w": float(last_meta.get("gold_w", 0) or 0),
                "cn_w": float(last_meta.get("cn_w", 0) or 0),
                "hk_w": float(last_meta.get("hk_w", 0) or 0),
                "us_w": float(last_meta.get("us_w", 0) or 0),
            },
            "panel": {
                "nav": float(s.nav.iloc[-1]),
                "drawdown": float(dd.iloc[-1]),
                "ret_5d": ret_5,
                "ret_20d": ret_20,
                "vol_20d_ann": vol_20,
                "n_trades": int(len(s.trades)),
            },
            "canary_rows": canary_rows,
            "holdings": holdings,
            "nav_chart": {
                "dates": [d.strftime("%Y-%m-%d") for d in nav_tail.index],
                "nav": [float(x) for x in nav_tail.values],
                "dd": [float(x) for x in dd_tail.reindex(nav_tail.index).values],
            },
            "params": PARAMS,
        }

    # -------- forecast --------
    def forecast_context(self) -> dict[str, Any]:
        """本周期（约1周）持仓收益率预计。"""
        s = self.ensure()
        # 使用最新目标权重作为“本周期拟持仓”；若无下一执行日则用当前持仓
        w = s.signal_w.iloc[-1].reindex(CODES).fillna(0.0)
        if float(w.sum()) <= 0:
            w = s.weights_daily.iloc[-1].reindex(CODES).fillna(0.0)
        w = w / w.sum()

        close = s.close
        # 周频收益
        we = week_ends(close.index)
        w_close = close.loc[we]
        week_ret = w_close.pct_change()

        # 方法A：动量外推（最近1周实现收益，作为下周朴素预计）
        last_1w = week_ret.iloc[-1].reindex(CODES)
        # 方法B：近4周平均周收益
        mean_4w = week_ret.tail(4).mean().reindex(CODES)
        # 方法C：近8周平均周收益
        mean_8w = week_ret.tail(8).mean().reindex(CODES)
        # 方法D：近52周（若不足则全部）历史周收益均值/分位
        hist = week_ret.tail(52).reindex(columns=CODES)

        rows = []
        exp_a = exp_b = exp_c = 0.0
        for c in CODES:
            wi = float(w[c])
            if wi < 1e-8:
                continue
            r1 = float(last_1w[c]) if pd.notna(last_1w.get(c)) else 0.0
            r4 = float(mean_4w[c]) if pd.notna(mean_4w.get(c)) else 0.0
            r8 = float(mean_8w[c]) if pd.notna(mean_8w.get(c)) else 0.0
            series = hist[c].dropna()
            p25 = float(series.quantile(0.25)) if len(series) else 0.0
            p50 = float(series.quantile(0.50)) if len(series) else 0.0
            p75 = float(series.quantile(0.75)) if len(series) else 0.0
            rows.append(
                {
                    "code": c,
                    "name": UNIVERSE[c]["name"],
                    "weight": wi,
                    "last_1w": r1,
                    "mean_4w": r4,
                    "mean_8w": r8,
                    "p25": p25,
                    "p50": p50,
                    "p75": p75,
                    "contrib_mom1": wi * r1,
                    "contrib_mean4": wi * r4,
                    "contrib_mean8": wi * r8,
                }
            )
            exp_a += wi * r1
            exp_b += wi * r4
            exp_c += wi * r8

        # 组合历史周收益分布（用当前权重在历史周上回放）
        port_hist = []
        for dt, row in hist.iterrows():
            rr = 0.0
            ok = False
            for c in CODES:
                if w[c] > 0 and pd.notna(row.get(c)):
                    rr += float(w[c]) * float(row[c])
                    ok = True
            if ok:
                port_hist.append(rr)
        port_hist = np.array(port_hist) if port_hist else np.array([0.0])

        # 综合预计：0.2*1w + 0.5*4w + 0.3*8w
        blended = 0.2 * exp_a + 0.5 * exp_b + 0.3 * exp_c
        # 波动与区间
        port_vol = float(port_hist.std()) if len(port_hist) > 1 else 0.0
        low = blended - port_vol
        high = blended + port_vol

        sig_dt = s.signal_w.index.max()
        return {
            "asof": str(s.close.index.max().date()),
            "signal_date": str(sig_dt.date()),
            "horizon": "本周（约5个交易日 / 1个周度持有期）",
            "weights": rows,
            "expected": {
                "momentum_1w": exp_a,
                "mean_4w": exp_b,
                "mean_8w": exp_c,
                "blended": blended,
                "hist_p25": float(np.quantile(port_hist, 0.25)),
                "hist_p50": float(np.quantile(port_hist, 0.50)),
                "hist_p75": float(np.quantile(port_hist, 0.75)),
                "range_low": low,
                "range_high": high,
                "port_week_vol": port_vol,
            },
            "method_note": (
                "预计收益为持仓加权统计外推，非承诺收益。"
                "综合预计 = 0.2×近1周动量 + 0.5×近4周均周收益 + 0.3×近8周均周收益；"
                "区间为综合预计 ± 当前权重在近一年周收益回放波动。"
            ),
            "stats": s.stats,
        }


ENGINE = StrategyEngine()
