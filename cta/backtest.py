# -*- coding: utf-8 -*-
"""多品种 CTA 回测引擎。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .risk import portfolio_vol_scale, volatility_target_weights
from .signals import combine_signals, donchian_breakout_signal, dual_ma_signal, ts_momentum_signal


SignalFunc = Callable[[pd.DataFrame], pd.Series]


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    signals: pd.DataFrame
    asset_returns: pd.DataFrame
    summary: Dict[str, float]
    contrib: pd.Series = field(default_factory=pd.Series)

    def to_frames(self) -> Dict[str, pd.DataFrame]:
        return {
            "equity": self.equity.to_frame("equity"),
            "returns": self.returns.to_frame("ret"),
            "weights": self.weights,
            "signals": self.signals,
            "summary": pd.DataFrame([self.summary]),
            "contrib": self.contrib.to_frame("contrib"),
        }


def _default_signal(ohlc: pd.DataFrame, method: str = "combo") -> pd.Series:
    close, high, low = ohlc["close"], ohlc["high"], ohlc["low"]
    if method == "dual_ma":
        return dual_ma_signal(close, fast=20, slow=60)
    if method == "donchian":
        return donchian_breakout_signal(high, low, close, entry=20, exit_=10)
    if method == "tsmom":
        return ts_momentum_signal(close, lookback=60, skip=1)
    # combo: 三信号合成
    s1 = dual_ma_signal(close, fast=20, slow=60)
    s2 = donchian_breakout_signal(high, low, close, entry=20, exit_=10)
    s3 = ts_momentum_signal(close, lookback=90, skip=1)
    return combine_signals({"ma": s1, "don": s2, "mom": s3})


class CTABacktester:
    """多品种期货 CTA 回测。

    流程：
      1) 各品种独立生成方向信号
      2) 波动率目标得到单品种目标权重
      3) 截面归一（等风险），可选组合波动再缩放
      4) T+1 成交，扣除双边手续费与滑点
    """

    def __init__(
        self,
        panels: Dict[str, pd.DataFrame],
        method: str = "combo",
        target_vol: float = 0.10,
        portfolio_target_vol: float = 0.12,
        vol_window: int = 20,
        max_leverage: float = 2.0,
        cost_bps: float = 2.0,
        slip_bps: float = 1.0,
        equal_risk: bool = True,
        scale_portfolio: bool = True,
        initial_capital: float = 1.0,
        signal_fn: Optional[SignalFunc] = None,
    ):
        self.panels = panels
        self.method = method
        self.target_vol = target_vol
        self.portfolio_target_vol = portfolio_target_vol
        self.vol_window = vol_window
        self.max_leverage = max_leverage
        self.cost_bps = cost_bps
        self.slip_bps = slip_bps
        self.equal_risk = equal_risk
        self.scale_portfolio = scale_portfolio
        self.initial_capital = initial_capital
        self.signal_fn = signal_fn

    def _build_signal(self, ohlc: pd.DataFrame) -> pd.Series:
        if self.signal_fn is not None:
            return self.signal_fn(ohlc)
        return _default_signal(ohlc, self.method)

    def run(self) -> BacktestResult:
        symbols = sorted(self.panels.keys())
        closes = pd.DataFrame({s: self.panels[s]["close"] for s in symbols}).sort_index()
        closes = closes.ffill()
        asset_ret = closes.pct_change()

        signals = {}
        raw_weights = {}
        for s in symbols:
            ohlc = self.panels[s].reindex(closes.index).ffill()
            sig = self._build_signal(ohlc)
            signals[s] = sig
            w = volatility_target_weights(
                ohlc["close"],
                sig.fillna(0.0),
                target_vol=self.target_vol,
                vol_window=self.vol_window,
                max_leverage=self.max_leverage,
            )
            raw_weights[s] = w

        signal_df = pd.DataFrame(signals).reindex(closes.index)
        weight_df = pd.DataFrame(raw_weights).reindex(closes.index).fillna(0.0)

        if self.equal_risk:
            # 有信号的品种等风险：将绝对权重归一到总和 <= max_leverage
            abs_sum = weight_df.abs().sum(axis=1).replace(0, np.nan)
            scale = (self.max_leverage / abs_sum).clip(upper=1.0).fillna(0.0)
            weight_df = weight_df.mul(scale, axis=0)

        if self.scale_portfolio:
            weight_df = portfolio_vol_scale(
                asset_ret.fillna(0.0),
                weight_df,
                target_vol=self.portfolio_target_vol,
                vol_window=max(60, self.vol_window * 2),
                max_leverage=self.max_leverage * 1.5,
            )

        # T+1：用昨日权重乘今日收益
        traded = weight_df.shift(1).fillna(0.0)
        gross = (traded * asset_ret.fillna(0.0)).sum(axis=1)

        # 换手成本：|Δw| * (cost + slip)
        turnover = weight_df.diff().abs().sum(axis=1).fillna(0.0)
        cost = turnover * ((self.cost_bps + self.slip_bps) / 10000.0)
        net = gross - cost

        equity = (1.0 + net).cumprod() * self.initial_capital
        equity.iloc[0] = self.initial_capital
        summary = performance_summary(equity, net)

        # 品种贡献：累计毛收益
        contrib = (traded * asset_ret.fillna(0.0)).sum(axis=0).sort_values(ascending=False)

        return BacktestResult(
            equity=equity.rename("equity"),
            returns=net.rename("ret"),
            weights=weight_df,
            signals=signal_df,
            asset_returns=asset_ret,
            summary=summary,
            contrib=contrib.rename("contrib"),
        )


def run_strategy_comparison(
    panels: Dict[str, pd.DataFrame],
    methods: Optional[List[str]] = None,
    **kwargs,
) -> pd.DataFrame:
    """对比 dual_ma / donchian / tsmom / combo 四套信号的绩效。"""
    methods = methods or ["dual_ma", "donchian", "tsmom", "combo"]
    rows = []
    for m in methods:
        bt = CTABacktester(panels, method=m, **kwargs)
        res = bt.run()
        row = {"method": m, **res.summary}
        rows.append(row)
    return pd.DataFrame(rows).set_index("method")
