# -*- coding: utf-8 -*-
"""策略四（实盘口径）：跨期价差套利。

相对旧版 / 对照主流做法：
1. 固定近月+次近月（不按持仓量日更），交割前移仓
2. 只做流动性好的黑色+铜（RB/HC/I/CU），不做全市场乱扫
3. 价差滚动 z 均值回归 + 成本门槛 + 半衰期过滤
4. 按合约乘数/保证金率计手数；远月更高滑点
5. 纯利率持有成本带在商品上实测失效（缺仓储/便利收益），故不用
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary
from .strategies_12 import BookConfig

# 交易月份 + 合约乘数 + 单边保证金率（近似）
CONTRACT_SPEC: Dict[str, dict] = {
    "RB": {"months": tuple(range(1, 13)), "multiplier": 10.0, "margin_rate": 0.10},
    "HC": {"months": tuple(range(1, 13)), "multiplier": 10.0, "margin_rate": 0.10},
    "I": {"months": (1, 5, 9), "multiplier": 100.0, "margin_rate": 0.11},
    "CU": {"months": tuple(range(1, 13)), "multiplier": 5.0, "margin_rate": 0.10},
    "AU": {"months": (2, 4, 6, 8, 10, 12), "multiplier": 1000.0, "margin_rate": 0.08},
    "RU": {"months": (1, 3, 4, 5, 6, 7, 8, 9, 10, 11), "multiplier": 10.0, "margin_rate": 0.10},
    "M": {"months": (1, 3, 5, 7, 8, 9, 11, 12), "multiplier": 10.0, "margin_rate": 0.09},
    "Y": {"months": (1, 5, 9), "multiplier": 10.0, "margin_rate": 0.09},
    "C": {"months": (1, 3, 5, 7, 9, 11), "multiplier": 10.0, "margin_rate": 0.09},
    "TA": {"months": tuple(range(1, 13)), "multiplier": 5.0, "margin_rate": 0.09},
    "MA": {"months": (1, 5, 9), "multiplier": 10.0, "margin_rate": 0.09},
    "SC": {"months": tuple(range(1, 13)), "multiplier": 1000.0, "margin_rate": 0.10},
    # IF 跨期机制不同，默认不纳入实盘跨期池
}

# 向后兼容旧常量名
CONTRACT_MONTHS = {k: v["months"] for k, v in CONTRACT_SPEC.items()}


@dataclass
class CalendarConfig:
    """跨期实盘参数（对齐主流商品跨期做法）。

    信号：固定近/远月价差的滚动 z 均值回归（牛/熊市套利）。
    落地关键不在复杂信号，而在：
    1) 只做流动性好、价差相对平稳的品种（黑色+铜）
    2) 固定合约对 + 交割前移仓
    3) 成本/滑点覆盖后再开仓
    4) 半衰期过滤（价差若近似随机游走则不做）
    """

    z_window: int = 20
    entry_z: float = 2.0
    exit_z: float = 0.0
    stop_z: float = 4.0
    # 近月距名义到期少于此日历天数则移仓
    roll_days: int = 10
    min_volume: float = 5000.0
    min_oi: float = 10000.0
    # 跨期套利保证金 ≈ 两腿保证金之和 * 折扣
    spread_margin_coef: float = 0.70
    # 交易所组合单通常优于两腿拆单；仍对远月保守加点
    near_cost_bps: float = 0.8
    far_cost_bps: float = 2.0
    # 开仓需覆盖约 cost_mult 倍双边成本（价格点）；0 关闭
    cost_mult: float = 1.5
    # 价差半衰期过滤默认关闭：滚动 HL 在短合约段上不稳定，易过度拒单
    # 品种池本身已承担“可回归性”筛选；需要时可设 min/max_half_life
    min_half_life: float = 0.0
    max_half_life: float = 0.0
    hl_lookback: int = 60
    # 白名单：主流可交易跨期池（黑色+铜）；其余默认不做
    allow: Tuple[str, ...] = ("RB", "HC", "I", "CU")
    # 额外排除（与 allow 同时生效）
    exclude: Tuple[str, ...] = ("IF", "RU", "AU", "SC", "MA", "Y", "C", "TA", "M")


def _contract_codes(symbol: str, start_year: int = 2017, end_year: int = 2027) -> List[str]:
    months = CONTRACT_SPEC.get(symbol.upper(), {}).get("months", tuple(range(1, 13)))
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
    os.makedirs(cache_dir, exist_ok=True)
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    jobs = []
    for sym in symbols:
        if sym.upper() not in CONTRACT_SPEC:
            continue
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
    return result


_CODE_RE = re.compile(r"^([A-Z]+)(\d{2})(\d{2})$")


def parse_contract_ym(code: str) -> Optional[pd.Timestamp]:
    """合约年月 -> 近似最后交易日（当月 15 日）。"""
    m = _CODE_RE.match(code.upper())
    if not m:
        return None
    yy, mm = int(m.group(2)), int(m.group(3))
    year = 2000 + yy if yy <= 79 else 1900 + yy
    try:
        return pd.Timestamp(year=year, month=mm, day=15)
    except ValueError:
        return None


def _liquid_ok(df: pd.DataFrame, dt: pd.Timestamp, cfg: CalendarConfig) -> bool:
    if dt not in df.index:
        return False
    row = df.loc[dt]
    vol = float(row["volume"]) if "volume" in df.columns and pd.notna(row["volume"]) else 0.0
    oi = float(row["oi"]) if "oi" in df.columns and pd.notna(row["oi"]) else 0.0
    return vol >= cfg.min_volume and oi >= cfg.min_oi


def select_near_deferred(
    contracts: Dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    cfg: CalendarConfig,
) -> Optional[Tuple[str, str]]:
    """选近月+次近月：名义到期晚于 dt+roll_days，当日有行情且流动性达标，按到期排序取前两名。"""
    cands = []
    for code, df in contracts.items():
        exp = parse_contract_ym(code)
        if exp is None:
            continue
        if exp <= dt + pd.Timedelta(days=cfg.roll_days):
            continue
        # 合约数据已结束则跳过
        if dt > df.index.max():
            continue
        if not _liquid_ok(df, dt, cfg):
            continue
        if dt not in df.index or pd.isna(df.loc[dt, "close"]):
            continue
        cands.append((exp, code))
    if len(cands) < 2:
        return None
    cands.sort(key=lambda x: (x[0], x[1]))
    return cands[0][1], cands[1][1]


def _half_life_ar1(x: np.ndarray) -> float:
    """OU/AR(1) 半衰期（样本不足或非均值回归时返回 nan）。"""
    if x is None or len(x) < 20:
        return float("nan")
    s = pd.Series(x, dtype=float).dropna()
    if len(s) < 20:
        return float("nan")
    lag = s.shift(1)
    d = s.diff()
    df = pd.concat([d, lag], axis=1).dropna()
    df.columns = ["d", "lag"]
    if float(df["lag"].var()) < 1e-12:
        return float("nan")
    b = float(np.polyfit(df["lag"].to_numpy(), df["d"].to_numpy(), 1)[0])
    if b >= 0:
        return float("nan")
    return float(-np.log(2.0) / b)


def calendar_spread_signal_on_series(
    spread: np.ndarray,
    window: int = 20,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 4.0,
    near_px: Optional[np.ndarray] = None,
    near_cost_bps: float = 0.0,
    far_cost_bps: float = 0.0,
    cost_mult: float = 0.0,
    min_half_life: float = 0.0,
    max_half_life: float = 0.0,
    hl_lookback: int = 60,
) -> np.ndarray:
    """固定合约对上的价差 z 回归信号（可加成本门槛与半衰期过滤）。"""
    n = len(spread)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(n):
        if i + 1 < window:
            out[i] = 0.0
            pos = 0.0
            continue
        window_vals = spread[i + 1 - window : i + 1]
        if np.isnan(window_vals).any():
            out[i] = 0.0
            pos = 0.0
            continue
        mu = float(np.mean(window_vals))
        sd = float(np.std(window_vals, ddof=1))
        if sd < 1e-12:
            out[i] = 0.0
            pos = 0.0
            continue
        z = (spread[i] - mu) / sd

        # 半衰期：仅约束新开仓
        allow_entry = True
        if min_half_life > 0 or max_half_life > 0:
            lb = max(hl_lookback, window)
            if i + 1 >= lb:
                hl = _half_life_ar1(spread[i + 1 - lb : i + 1])
                if np.isnan(hl):
                    allow_entry = False
                else:
                    if min_half_life > 0 and hl < min_half_life:
                        allow_entry = False
                    if max_half_life > 0 and hl > max_half_life:
                        allow_entry = False
            else:
                allow_entry = False

        # 成本门槛：偏离均值需覆盖双边成本
        if allow_entry and cost_mult > 0 and near_px is not None and i < len(near_px):
            px = float(near_px[i])
            if px > 0:
                rt_cost = px * ((near_cost_bps + far_cost_bps) / 10000.0) * 2.0
                if abs(spread[i] - mu) < cost_mult * rt_cost:
                    allow_entry = False

        if pos == 0.0:
            if allow_entry and z >= entry_z:
                pos = -1.0
            elif allow_entry and z <= -entry_z:
                pos = 1.0
        else:
            if abs(z) >= stop_z:
                pos = 0.0
            elif pos > 0 and z >= exit_z:
                pos = 0.0
            elif pos < 0 and z <= -exit_z:
                pos = 0.0
        out[i] = pos
    return out


# 兼容旧测试名
def calendar_spread_signal(
    spread: pd.Series,
    window: int = 20,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 4.0,
) -> pd.Series:
    arr = calendar_spread_signal_on_series(
        spread.to_numpy(dtype=float), window, entry_z, exit_z, stop_z
    )
    return pd.Series(arr, index=spread.index, name="signal")


def _backtest_symbol_calendar(
    sym: str,
    contracts: Dict[str, pd.DataFrame],
    cfg: CalendarConfig,
) -> pd.DataFrame:
    """单品种跨期回测明细：date, pos, pnl, margin, near, deferred, spread。"""
    if not contracts:
        return pd.DataFrame()
    # 交易日历 = 所有合约日期并集
    idx = sorted(set().union(*[set(df.index) for df in contracts.values()]))
    idx = pd.DatetimeIndex(idx)

    spec = CONTRACT_SPEC[sym]
    mult = float(spec["multiplier"])
    rate = float(spec["margin_rate"])

    rows = []
    near = deferred = None
    spread_buf: List[float] = []
    near_px_buf: List[float] = []
    pos = 0.0
    prev_near_px = prev_def_px = np.nan

    for dt in idx:
        need_roll = False
        hold_flat = False  # 临时缺行情/流动性：平仓但保留合约对

        if near is None or deferred is None:
            need_roll = True
        else:
            exp = parse_contract_ym(near)
            near_last = contracts[near].index.max()
            # 仅按名义到期 / 合约摘牌移仓，避免数据缺口导致每日重选
            if exp is not None and exp <= dt + pd.Timedelta(days=cfg.roll_days):
                need_roll = True
            elif dt > near_last:
                need_roll = True
            elif dt not in contracts[near].index or dt not in contracts[deferred].index:
                hold_flat = True
            elif not (
                _liquid_ok(contracts[near], dt, cfg) and _liquid_ok(contracts[deferred], dt, cfg)
            ):
                hold_flat = True

        # 昨仓盈亏（收盘换仓）
        pnl = 0.0
        if (
            pos != 0.0
            and near is not None
            and deferred is not None
            and dt in contracts[near].index
            and dt in contracts[deferred].index
            and not np.isnan(prev_near_px)
            and not np.isnan(prev_def_px)
        ):
            pn = float(contracts[near].loc[dt, "close"])
            pd_ = float(contracts[deferred].loc[dt, "close"])
            pnl = mult * ((pn - prev_near_px) - (pd_ - prev_def_px)) * pos
            prev_near_px, prev_def_px = pn, pd_

        rolled = False
        if need_roll:
            pos = 0.0
            pair = select_near_deferred(contracts, dt, cfg)
            if pair is None:
                near = deferred = None
                spread_buf = []
                near_px_buf = []
                prev_near_px = prev_def_px = np.nan
                rows.append(
                    {
                        "date": dt,
                        "pos": 0.0,
                        "pnl_per_lot": pnl,
                        "margin_per_lot": 0.0,
                        "near": "",
                        "deferred": "",
                        "spread": np.nan,
                        "roll": 0.0,  # 无可用合约对，不是移仓
                    }
                )
                continue
            new_near, new_def = pair
            # 同一对重复“移仓”不重置 z（防止边界日抖动）
            if (new_near, new_def) != (near, deferred):
                near, deferred = new_near, new_def
                spread_buf = []
                near_px_buf = []
                rolled = True
            else:
                near, deferred = new_near, new_def
            pn = float(contracts[near].loc[dt, "close"])
            pd_ = float(contracts[deferred].loc[dt, "close"])
            prev_near_px, prev_def_px = pn, pd_

        if near is None or deferred is None:
            rows.append(
                {
                    "date": dt,
                    "pos": 0.0,
                    "pnl_per_lot": pnl,
                    "margin_per_lot": 0.0,
                    "near": "",
                    "deferred": "",
                    "spread": np.nan,
                    "roll": float(rolled),
                }
            )
            continue

        if dt not in contracts[near].index or dt not in contracts[deferred].index:
            # 保留合约对，当日空仓
            pos = 0.0
            rows.append(
                {
                    "date": dt,
                    "pos": 0.0,
                    "pnl_per_lot": pnl,
                    "margin_per_lot": 0.0,
                    "near": near,
                    "deferred": deferred,
                    "spread": np.nan,
                    "roll": float(rolled),
                }
            )
            continue

        pn = float(contracts[near].loc[dt, "close"])
        pd_ = float(contracts[deferred].loc[dt, "close"])
        spr = pn - pd_

        if rolled:
            spread_buf = [spr]
            near_px_buf = [pn]
            pos = 0.0
        elif hold_flat:
            pos = 0.0
            spread_buf.append(spr)
            near_px_buf.append(pn)
            prev_near_px, prev_def_px = pn, pd_
        else:
            spread_buf.append(spr)
            near_px_buf.append(pn)
            if len(spread_buf) >= cfg.z_window:
                sig_arr = calendar_spread_signal_on_series(
                    np.asarray(spread_buf, dtype=float),
                    cfg.z_window,
                    cfg.entry_z,
                    cfg.exit_z,
                    cfg.stop_z,
                    near_px=np.asarray(near_px_buf, dtype=float),
                    near_cost_bps=cfg.near_cost_bps,
                    far_cost_bps=cfg.far_cost_bps,
                    cost_mult=cfg.cost_mult,
                    min_half_life=cfg.min_half_life,
                    max_half_life=cfg.max_half_life,
                    hl_lookback=cfg.hl_lookback,
                )
                pos = float(sig_arr[-1])
            else:
                pos = 0.0
            prev_near_px, prev_def_px = pn, pd_

        margin_lot = (pn * mult * rate + pd_ * mult * rate) * cfg.spread_margin_coef
        rows.append(
            {
                "date": dt,
                "pos": pos,
                "pnl_per_lot": pnl,
                "margin_per_lot": margin_lot if pos != 0 else 0.0,
                "near": near,
                "deferred": deferred,
                "spread": spr,
                "roll": float(rolled),
            }
        )

    return pd.DataFrame(rows).set_index("date").sort_index()


def build_calendar_book(
    contract_store: Dict[str, Dict[str, pd.DataFrame]],
    cal_cfg: Optional[CalendarConfig] = None,
) -> Dict[str, pd.DataFrame]:
    cal_cfg = cal_cfg or CalendarConfig()
    allow = {s.upper() for s in cal_cfg.allow} if cal_cfg.allow else None
    out = {}
    for sym, contracts in contract_store.items():
        sym_u = sym.upper()
        if sym_u in cal_cfg.exclude or sym_u not in CONTRACT_SPEC:
            continue
        if allow is not None and sym_u not in allow:
            continue
        if len(contracts) < 2:
            continue
        df = _backtest_symbol_calendar(sym_u, contracts, cal_cfg)
        if not df.empty:
            out[sym_u] = df
    return out


def simulate_spread_book_realistic(
    book: Dict[str, pd.DataFrame],
    margin_budget: float,
    capital: float,
    cal_cfg: Optional[CalendarConfig] = None,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame, Dict[str, float], pd.DataFrame]:
    """按保证金预算分配手数（各品种等手数上限由保证金约束）。"""
    cal_cfg = cal_cfg or CalendarConfig()
    if not book:
        raise ValueError("empty calendar book")

    idx = sorted(set().union(*[set(df.index) for df in book.values()]))
    idx = pd.DatetimeIndex(idx)
    symbols = sorted(book.keys())

    # 对齐
    pos = pd.DataFrame({s: book[s]["pos"].reindex(idx).fillna(0.0) for s in symbols})
    pnl_lot = pd.DataFrame({s: book[s]["pnl_per_lot"].reindex(idx).fillna(0.0) for s in symbols})
    mgn_lot = pd.DataFrame({s: book[s]["margin_per_lot"].reindex(idx).fillna(0.0) for s in symbols})
    roll = pd.DataFrame({s: book[s]["roll"].reindex(idx).fillna(0.0) for s in symbols})

    n = len(idx)
    lots_rows = np.zeros((n, len(symbols)))
    book_pnl = np.zeros(n)
    margin_used = np.zeros(n)
    # 有符号手数：+多近空远 / -空近多远
    prev_signed = pd.Series(0.0, index=symbols)
    # 昨保证金/手（平仓日今日 margin_per_lot=0，用昨日反推名义）
    prev_mgn = pd.Series(0.0, index=symbols)

    for i, _dt in enumerate(idx):
        dpos = pos.iloc[i]
        dmgn = mgn_lot.iloc[i]
        active = [s for s in symbols if float(dpos[s]) != 0.0 and float(dmgn[s]) > 1e-6]

        # 目标绝对手数：保证金预算内活跃品种等权
        target_abs = pd.Series(0.0, index=symbols)
        if active and margin_budget > 0:
            per_budget = margin_budget / float(len(active))
            for s in active:
                lots = np.floor(per_budget / float(dmgn[s]))
                target_abs[s] = max(lots, 0.0)
        target_signed = target_abs * np.sign(dpos)

        # 盈亏：昨绝对手数 × 今日每手盈亏（已含昨方向）
        day_pnl = float((prev_signed.abs() * pnl_lot.iloc[i]).sum())

        # 交易成本：有符号手数变化（含反向开仓）× 两腿名义 × bps
        cost = 0.0
        for s in symbols:
            d_lots = abs(float(target_signed[s] - prev_signed[s]))
            if d_lots <= 0:
                continue
            m = float(dmgn[s]) if float(dmgn[s]) > 0 else float(prev_mgn[s])
            rate = float(CONTRACT_SPEC[s]["margin_rate"])
            notional_leg = 0.0
            if m > 0 and rate > 0:
                notional_leg = m / cal_cfg.spread_margin_coef / 2.0 / rate
            cost += d_lots * notional_leg * (
                (cal_cfg.near_cost_bps + cal_cfg.far_cost_bps) / 10000.0
            )

        net = day_pnl - cost
        book_pnl[i] = net
        margin_used[i] = float((target_abs * dmgn).sum())
        lots_rows[i, :] = target_signed.to_numpy()
        prev_signed = target_signed
        prev_mgn = dmgn.where(dmgn > 0, prev_mgn)

    lots_df = pd.DataFrame(lots_rows, index=idx, columns=symbols)
    pnl = pd.Series(book_pnl, index=idx, name="pnl")
    ret_on_capital = (pnl / capital).rename("ret")
    nav = (1.0 + ret_on_capital).cumprod().rename("nav")
    summary = performance_summary(nav, ret_on_capital)
    summary["max_margin"] = float(np.max(margin_used)) if n else 0.0
    summary["avg_margin"] = float(np.mean(margin_used)) if n else 0.0
    summary["margin_budget"] = float(margin_budget)
    summary["margin_ok"] = float(summary["max_margin"] <= margin_budget + 1.0)
    summary["n_symbols_traded"] = float(len(symbols))
    summary["avg_active_symbols"] = float((pos != 0).sum(axis=1).mean())
    summary["n_roll_days"] = float((roll.sum(axis=1) > 0).sum())
    detail = pd.DataFrame(
        {
            "margin": margin_used,
            "n_active": (pos != 0).sum(axis=1),
            "n_roll": roll.sum(axis=1),
        },
        index=idx,
    )
    return nav, ret_on_capital, lots_df, summary, detail


def build_s4_leg_weights(
    contract_store: Dict[str, Dict[str, pd.DataFrame]],
    cal_cfg: Optional[CalendarConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """兼容旧接口：返回 (pos_by_symbol, spread_ret_proxy)。"""
    book = build_calendar_book(contract_store, cal_cfg)
    if not book:
        return pd.DataFrame(), pd.DataFrame()
    pos = pd.DataFrame({s: df["pos"] for s, df in book.items()}).sort_index().fillna(0.0)
    # proxy ret = pnl_per_lot / (价格尺度) —— 仅兼容，正式回测走 run_s4
    rets = {}
    for s, df in book.items():
        # 用 pos 变化不大时的每手盈亏 / 常数名义近似
        mult = CONTRACT_SPEC[s]["multiplier"]
        px = df["spread"].abs().rolling(20, min_periods=1).median().replace(0, np.nan)
        # 不稳定；给 0
        rets[s] = df["pnl_per_lot"] / (mult * 1000.0)
    spr = pd.DataFrame(rets).sort_index().fillna(0.0)
    return pos, spr


def simulate_spread_book(
    positions: pd.DataFrame,
    spread_rets: pd.DataFrame,
    margin_budget: float,
    capital: float,
    leverage: float = 10.0,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame, Dict[str, float]]:
    """旧接口保留（测试兼容）；正式路径请用 simulate_spread_book_realistic。"""
    pos = positions.fillna(0.0).sort_index()
    rets = spread_rets.reindex(pos.index).fillna(0.0)
    symbols = list(pos.columns)
    n = len(pos)
    max_pair_gross = margin_budget * leverage
    weight_rows = np.zeros((n, len(symbols)))
    book_pnl = np.zeros(n)
    margin_used = np.zeros(n)
    cash = 0.0
    prev_w = pd.Series(0.0, index=symbols)
    for i in range(n):
        d = pos.iloc[i]
        active = d[d != 0.0]
        w = pd.Series(0.0, index=symbols)
        if len(active) > 0 and max_pair_gross > 0:
            pair_notional = max_pair_gross / float(len(active))
            leg = pair_notional / 2.0
            w = (d * leg).reindex(symbols).fillna(0.0)
        gross = float((prev_w * rets.iloc[i]).sum())
        turnover = float((w - prev_w).abs().sum()) * 2.0
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost
        cash += net
        book_pnl[i] = net
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
    cal_cfg: Optional[CalendarConfig] = None,
):
    cfg = cfg or BookConfig()
    cal_cfg = cal_cfg or CalendarConfig()
    if contract_store is None:
        if allow_fetch:
            contract_store = load_or_fetch_contracts(list(panels.keys()), cache_dir=cache_dir)
        else:
            contract_store = load_cached_contracts_only(list(panels.keys()), cache_dir=cache_dir)

    book = build_calendar_book(contract_store, cal_cfg)
    if not book:
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
        return (
            nav,
            empty.rename("ret"),
            pd.DataFrame(0.0, index=idx, columns=list(panels)),
            summary,
            pd.DataFrame(0.0, index=idx, columns=[]),
        )

    nav, ret, lots, summary, _detail = simulate_spread_book_realistic(
        book, cfg.margin_s4, cfg.capital, cal_cfg
    )
    pos = pd.DataFrame({s: df["pos"] for s, df in book.items()}).sort_index().fillna(0.0)
    return nav, ret, lots, summary, pos
