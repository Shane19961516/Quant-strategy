# -*- coding: utf-8 -*-
"""期货行情数据：加载与合成样本。"""

from __future__ import annotations

import os
from typing import Dict, Optional

import numpy as np
import pandas as pd


# 国内常见 CTA 品种（示例合约乘数 / 点值，仅用于演示）
DEFAULT_UNIVERSE = {
    "IF": {"name": "沪深300股指", "sector": "金融", "multiplier": 300.0, "tick": 0.2},
    "RB": {"name": "螺纹钢", "sector": "黑色", "multiplier": 10.0, "tick": 1.0},
    "HC": {"name": "热轧卷板", "sector": "黑色", "multiplier": 10.0, "tick": 1.0},
    "I": {"name": "铁矿石", "sector": "黑色", "multiplier": 100.0, "tick": 0.5},
    "AU": {"name": "黄金", "sector": "贵金属", "multiplier": 1000.0, "tick": 0.02},
    "CU": {"name": "沪铜", "sector": "有色", "multiplier": 5.0, "tick": 10.0},
    "C": {"name": "玉米", "sector": "农产品", "multiplier": 10.0, "tick": 1.0},
    "M": {"name": "豆粕", "sector": "农产品", "multiplier": 10.0, "tick": 1.0},
    "Y": {"name": "豆油", "sector": "农产品", "multiplier": 10.0, "tick": 2.0},
    "SC": {"name": "原油", "sector": "能源", "multiplier": 1000.0, "tick": 0.1},
    "TA": {"name": "PTA", "sector": "化工", "multiplier": 5.0, "tick": 2.0},
    "MA": {"name": "甲醇", "sector": "化工", "multiplier": 10.0, "tick": 1.0},
}


def load_futures_csv(
    path: str,
    date_col: str = "date",
    price_col: str = "close",
) -> pd.DataFrame:
    """从 CSV 加载单品种日线，返回含 open/high/low/close 的 DataFrame。"""
    df = pd.read_csv(path)
    if date_col not in df.columns:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        out = df.copy()
        if "close" not in out.columns and price_col in out.columns:
            out = out.rename(columns={price_col: "close"})
        for col in ("open", "high", "low"):
            if col not in out.columns:
                out[col] = out["close"]
        return out.sort_index()

    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    if price_col != "close" and "close" not in df.columns:
        df = df.rename(columns={price_col: "close"})
    for col in ("open", "high", "low"):
        if col not in df.columns:
            df[col] = df["close"]
    return df


def generate_synthetic_futures(
    symbols: Optional[Dict[str, dict]] = None,
    start: str = "2018-01-01",
    end: str = "2024-12-31",
    seed: int = 42,
) -> Dict[str, pd.DataFrame]:
    """生成带趋势/震荡 regime 的合成期货日线，便于本地演示回测。

    价格过程：几何布朗运动 + 分段趋势漂移 + 随机波动率。
    """
    rng = np.random.default_rng(seed)
    symbols = symbols or DEFAULT_UNIVERSE
    dates = pd.bdate_range(start=start, end=end)
    n = len(dates)
    panels: Dict[str, pd.DataFrame] = {}

    # 各品种基础参数（年化）
    base_params = {
        "IF": (4000.0, 0.04, 0.18),
        "RB": (3500.0, 0.02, 0.25),
        "HC": (3400.0, 0.02, 0.24),
        "I": (700.0, 0.01, 0.28),
        "AU": (380.0, 0.03, 0.15),
        "CU": (65000.0, 0.02, 0.22),
        "C": (2400.0, 0.01, 0.16),
        "M": (3200.0, 0.015, 0.20),
        "Y": (7000.0, 0.015, 0.22),
        "SC": (500.0, 0.01, 0.30),
        "TA": (5500.0, 0.02, 0.24),
        "MA": (2500.0, 0.02, 0.26),
    }

    for i, sym in enumerate(symbols):
        p0, mu0, vol0 = base_params.get(sym, (1000.0, 0.02, 0.20))
        # 分段趋势：约每 80~150 个交易日切换一次方向
        regime = np.zeros(n)
        t = 0
        while t < n:
            length = int(rng.integers(80, 151))
            direction = float(rng.choice([-1.0, 0.0, 1.0], p=[0.35, 0.30, 0.35]))
            strength = float(rng.uniform(0.08, 0.25))
            end_t = min(n, t + length)
            regime[t:end_t] = direction * strength
            t = end_t

        daily_mu = (mu0 + regime) / 252.0
        daily_vol = vol0 * (1.0 + 0.3 * np.sin(np.linspace(0, 8 * np.pi, n) + i)) / np.sqrt(252.0)
        shocks = rng.standard_normal(n)
        # 轻度自相关，贴近真实期货残差
        eps = np.zeros(n)
        for t in range(1, n):
            eps[t] = 0.15 * eps[t - 1] + shocks[t]
        rets = daily_mu + daily_vol * eps
        close = p0 * np.cumprod(1.0 + rets)
        # OHLC：用日内噪声构造高低开
        noise = np.abs(rng.normal(0, daily_vol, n))
        open_ = np.r_[p0, close[:-1]] * (1.0 + rng.normal(0, daily_vol * 0.3, n))
        high = np.maximum(open_, close) * (1.0 + noise)
        low = np.minimum(open_, close) * (1.0 - noise)
        panels[sym] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close},
            index=dates,
        )

    # 经济配对：让价差围绕缓慢漂移均值回归，便于套利测试
    def _link_pair(a: str, b: str, scale: float = 1.0) -> None:
        if a not in panels or b not in panels:
            return
        ca = panels[a]["close"].to_numpy(dtype=float)
        # b ≈ scale * a * exp(mean-reverting residual)
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = 0.92 * x[t - 1] + rng.normal(0, 0.01)
        cb = scale * ca * np.exp(x)
        noise = np.abs(rng.normal(0, 0.005, n))
        open_ = np.r_[cb[0], cb[:-1]] * (1.0 + rng.normal(0, 0.002, n))
        high = np.maximum(open_, cb) * (1.0 + noise)
        low = np.minimum(open_, cb) * (1.0 - noise)
        panels[b] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": cb},
            index=dates,
        )

    _link_pair("RB", "HC", scale=0.97)
    _link_pair("RB", "I", scale=0.22)
    _link_pair("M", "Y", scale=2.2)
    _link_pair("TA", "MA", scale=0.45)
    return panels


def save_panels(panels: Dict[str, pd.DataFrame], out_dir: str) -> None:
    """将多品种面板保存为 CSV。"""
    os.makedirs(out_dir, exist_ok=True)
    for sym, df in panels.items():
        path = os.path.join(out_dir, f"{sym}.csv")
        out = df.copy()
        out.index.name = "date"
        out.to_csv(path)


def load_panels(data_dir: str) -> Dict[str, pd.DataFrame]:
    """从目录加载全部品种 CSV。"""
    panels = {}
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        sym = fname.replace(".csv", "")
        panels[sym] = load_futures_csv(os.path.join(data_dir, fname))
    return panels
