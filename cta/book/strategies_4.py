# -*- coding: utf-8 -*-
"""策略四：主力 vs 次主力跨期价差套利。

数据：新浪分合约日线，按当日成交量选持仓量/成交量前两名作为主力与次主力。
价差 = 主力收盘 − 次主力收盘；滚动 20 日 z-score：
- |z|>=2 做价差回归
- 回到 0 平仓
- |z|>=4 止损
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary
from .strategies_12 import BookConfig
from .strategies_3 import simulate_pairs_book

# 品种 -> 合约月份（国内商品常见月份；股指按季）
CONTRACT_MONTHS: Dict[str, Tuple[int, ...]] = {
    "RB": tuple(range(1, 13)),
    "HC": tuple(range(1, 13)),
    "CU": tuple(range(1, 13)),
    "AU": (2, 4, 6, 8, 10, 12),
    "RU": (1, 3, 4, 5, 6, 7, 8, 9, 10, 11),
    "I": (1, 5, 9),
    "M": (1, 3, 5, 7, 8, 9, 11, 12),
    "Y": (1, 5, 9),
    "C": (1, 3, 5, 7, 9, 11),
    "TA": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
    "MA": (1, 5, 9),
    "SC": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
    "IF": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
}


def _contract_codes(symbol: str, start_year: int = 2017, end_year: int = 2027) -> List[str]:
    months = CONTRACT_MONTHS.get(symbol.upper(), tuple(range(1, 13)))
    out = []
    for y in range(start_year, end_year + 1):
        for m in months:
            out.append(f"{symbol.upper()}{y % 100:02d}{m:02d}")
    return out


def _normalize_sina(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "hold" in df.columns:
        df["oi"] = pd.to_numeric(df["hold"], errors="coerce")
    elif "oi" in df.columns:
        df["oi"] = pd.to_numeric(df["oi"], errors="coerce")
    else:
        df["oi"] = np.nan
    return df.dropna(subset=["close"])


def fetch_contract_sina(code: str, retries: int = 3, sleep: float = 0.8) -> Optional[pd.DataFrame]:
    import akshare as ak

    last = None
    for i in range(retries):
        try:
            raw = ak.futures_zh_daily_sina(symbol=code)
            if raw is None or len(raw) == 0:
                return None
            return _normalize_sina(raw)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(sleep * (i + 1))
    return None


def load_cached_contracts_only(
    symbols: Sequence[str],
    cache_dir: str = "cta_data_contracts",
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """只读取本地缓存，不发起网络请求。"""
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        sym_u = sym.upper()
        sym_dir = os.path.join(cache_dir, sym_u)
        result[sym_u] = {}
        if not os.path.isdir(sym_dir):
            continue
        for fname in os.listdir(sym_dir):
            if not fname.endswith(".csv"):
                continue
            code = fname[:-4]
            path = os.path.join(sym_dir, fname)
            try:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                if len(df) > 0:
                    result[sym_u][code] = df
            except Exception:  # noqa: BLE001
                continue
    return result


def load_or_fetch_contracts(
    symbols: Sequence[str],
    cache_dir: str = "cta_data_contracts",
    max_workers: int = 6,
    force: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """返回 {品种: {合约代码: OHLCV}}。"""
    os.makedirs(cache_dir, exist_ok=True)
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    jobs = []
    for sym in symbols:
        sym_dir = os.path.join(cache_dir, sym.upper())
        os.makedirs(sym_dir, exist_ok=True)
        result[sym.upper()] = {}
        for code in _contract_codes(sym):
            path = os.path.join(sym_dir, f"{code}.csv")
            if os.path.exists(path) and not force:
                try:
                    df = pd.read_csv(path, index_col=0, parse_dates=True)
                    if len(df) > 0:
                        result[sym.upper()][code] = df
                        continue
                except Exception:  # noqa: BLE001
                    pass
            jobs.append((sym.upper(), code, path))

    def _job(item):
        sym, code, path = item
        df = fetch_contract_sina(code)
        if df is not None and len(df) > 0:
            df.to_csv(path)
            return sym, code, df
        return sym, code, None

    if jobs:
        print(f"拉取分合约日线: {len(jobs)} 个任务...")
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_job, j) for j in jobs]
            for fut in as_completed(futs):
                sym, code, df = fut.result()
                done += 1
                if df is not None:
                    result[sym][code] = df
                if done % 50 == 0:
                    print(f"  进度 {done}/{len(jobs)}")
        print(f"分合约拉取完成: 成功缓存品种合约数="
              f"{ {s: len(v) for s, v in result.items()} }")
    return result


def build_main_secondary_spread(
    contracts: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """由分合约构建主力/次主力价差面板。

    返回列: main_close, second_close, spread, main_code, second_code
    选取规则：当日有行情的合约中，按 oi（缺则用 volume）降序，第1=主力，第2=次主力。
    """
    if not contracts:
        return pd.DataFrame()
    # 对齐所有合约
    closes = pd.DataFrame({c: df["close"] for c, df in contracts.items()})
    vols = pd.DataFrame({c: df["volume"] if "volume" in df.columns else np.nan for c, df in contracts.items()})
    ois = pd.DataFrame({c: df["oi"] if "oi" in df.columns else np.nan for c, df in contracts.items()})
    score = ois.fillna(0.0)
    # oi 全空时退回 volume
    if float(score.abs().sum().sum()) <= 0:
        score = vols.fillna(0.0)
    else:
        # 对 oi 缺失的用 volume 补
        score = score.where(score > 0, vols.fillna(0.0))

    idx = closes.index.sort_values()
    closes = closes.reindex(idx)
    score = score.reindex(idx).fillna(0.0)

    rows = []
    for dt in idx:
        sc = score.loc[dt]
        cl = closes.loc[dt]
        valid = sc[sc > 0].sort_values(ascending=False)
        if len(valid) < 2:
            continue
        m_code, s_code = valid.index[0], valid.index[1]
        mc, sc_ = cl[m_code], cl[s_code]
        if np.isnan(mc) or np.isnan(sc_):
            continue
        rows.append(
            {
                "date": dt,
                "main_close": float(mc),
                "second_close": float(sc_),
                "spread": float(mc - sc_),
                "main_code": m_code,
                "second_code": s_code,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).set_index("date").sort_index()
    return out


def calendar_spread_signal(
    spread: pd.Series,
    window: int = 20,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 4.0,
) -> pd.Series:
    """价差 z 回归：+1=做多价差(多主力空次主力)，-1=做空价差。"""
    mu = spread.rolling(window, min_periods=window).mean()
    sd = spread.rolling(window, min_periods=window).std()
    z = ((spread - mu) / sd.replace(0.0, np.nan)).to_numpy(dtype=float)
    n = len(spread)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(n):
        zi = z[i]
        if np.isnan(zi):
            out[i] = 0.0
            pos = 0.0
            continue
        if pos == 0.0:
            if zi >= entry_z:
                pos = -1.0
            elif zi <= -entry_z:
                pos = 1.0
        else:
            if abs(zi) >= stop_z:
                pos = 0.0
            elif pos > 0 and zi >= exit_z:
                pos = 0.0
            elif pos < 0 and zi <= -exit_z:
                pos = 0.0
        out[i] = pos
    return pd.Series(out, index=spread.index, name="signal")


def build_s4_leg_weights(
    contract_store: Dict[str, Dict[str, pd.DataFrame]],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """把各品种跨期仓位映射到「虚拟腿」MAIN/SECOND 收益上。

    为便于统一回测，构建两列合成收益：
    - 对每个品种独立做价差，再等权合成到总书；
    这里返回 per-symbol spread position 与 per-symbol spread returns。
    """
    spread_pos = {}
    spread_ret = {}
    for sym, contracts in contract_store.items():
        panel = build_main_secondary_spread(contracts)
        if panel.empty or len(panel) < 40:
            continue
        sig = calendar_spread_signal(panel["spread"])
        # 价差收益 ≈ 主连收益 - 次连收益（按收盘）
        mret = panel["main_close"].pct_change()
        sret = panel["second_close"].pct_change()
        # 换约日主次合约切换会造成跳价，屏蔽极端
        spr = (mret - sret).fillna(0.0)
        spr = spr.mask(spr.abs() > 0.08, 0.0)
        spread_pos[sym] = sig
        spread_ret[sym] = spr
    if not spread_pos:
        return pd.DataFrame(), pd.DataFrame()
    return (
        pd.DataFrame(spread_pos).sort_index().fillna(0.0),
        pd.DataFrame(spread_ret).sort_index().fillna(0.0),
    )


def simulate_spread_book(
    positions: pd.DataFrame,
    spread_rets: pd.DataFrame,
    margin_budget: float,
    capital: float,
    leverage: float = 10.0,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame, Dict[str, float]]:
    """跨期：每个活跃品种占用一对腿名义（计 2 倍单边），预算内等权。"""
    pos = positions.fillna(0.0).sort_index()
    rets = spread_rets.reindex(pos.index).fillna(0.0)
    symbols = list(pos.columns)
    n = len(pos)
    # 每品种开仓时两腿总名义 = 2 * leg；预算按活跃品种数均分到「对」
    max_pair_gross = margin_budget * leverage  # 全部对的总名义之和上限

    weight_rows = np.zeros((n, len(symbols)))  # 这里权重表示价差名义（单边）
    book_pnl = np.zeros(n)
    margin_used = np.zeros(n)
    cash = 0.0
    prev_w = pd.Series(0.0, index=symbols)

    for i in range(n):
        d = pos.iloc[i]
        active = d[d != 0.0]
        w = pd.Series(0.0, index=symbols)
        if len(active) > 0 and max_pair_gross > 0:
            # 每对两腿名义合计 = max_pair_gross / n_active；单边名义取其半
            pair_notional = max_pair_gross / float(len(active))
            leg = pair_notional / 2.0
            w = (d * leg).reindex(symbols).fillna(0.0)
        # 价差仓盈亏：w_spread * (r_main - r_second)
        day_ret = rets.iloc[i]
        gross = float((prev_w * day_ret).sum())
        # 换仓成本按两腿计：turnover_spread * 2
        turnover = float((w - prev_w).abs().sum()) * 2.0
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost
        cash += net
        book_pnl[i] = net
        # 保证金：两腿名义 / leverage
        margin_used[i] = float(w.abs().sum() * 2.0 / leverage)
        weight_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(weight_rows, index=pos.index, columns=symbols)
    pnl = pd.Series(book_pnl, index=pos.index, name="pnl")
    ret_on_capital = (pnl / capital).rename("ret")
    nav = (1.0 + ret_on_capital).cumprod().rename("nav")
    summary = performance_summary(nav, ret_on_capital)
    summary["max_margin"] = float(margin_used.max()) if n else 0.0
    summary["avg_margin"] = float(margin_used.mean()) if n else 0.0
    summary["margin_budget"] = float(margin_budget)
    summary["margin_ok"] = float(summary["max_margin"] <= margin_budget + 1.0)
    return nav, ret_on_capital, weights, summary


def run_s4(
    panels: Dict[str, pd.DataFrame],
    cfg: Optional[BookConfig] = None,
    contract_store: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None,
    cache_dir: str = "cta_data_contracts",
    allow_fetch: bool = True,
):
    cfg = cfg or BookConfig()
    if contract_store is None:
        if allow_fetch:
            contract_store = load_or_fetch_contracts(list(panels.keys()), cache_dir=cache_dir)
        else:
            contract_store = load_cached_contracts_only(list(panels.keys()), cache_dir=cache_dir)
    pos, spr = build_s4_leg_weights(contract_store)
    if pos.empty:
        idx = pd.DataFrame({s: panels[s]["close"] for s in panels}).index
        empty = pd.Series(0.0, index=idx)
        nav = pd.Series(1.0, index=idx, name="nav")
        summary = performance_summary(nav, empty.rename("ret"))
        summary.update(
            {
                "max_margin": 0.0,
                "avg_margin": 0.0,
                "margin_budget": cfg.margin_s4,
                "margin_ok": 1.0,
            }
        )
        return nav, empty.rename("ret"), pd.DataFrame(0.0, index=idx, columns=list(panels)), summary, pos
    return simulate_spread_book(
        pos, spr, cfg.margin_s4, cfg.capital, cfg.leverage, cfg.cost_bps, cfg.slip_bps
    ) + (pos,)
