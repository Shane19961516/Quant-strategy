"""Live P&L engine — gross direction PnL (no inventory netting/offset).

套利策略损益 = 昨日持仓损益 + 今日成交损益

昨日持仓损益（逐腿、按方向）:
  多头: +qty * (最新价 - 昨收) * 乘数
  空头: -qty * (最新价 - 昨收) * 乘数

今日成交损益（逐笔、按方向，不与昨仓冲抵）:
  买入: +qty * (最新价 - 成交价) * 乘数
  卖出: -qty * (最新价 - 成交价) * 乘数

希腊值持仓簿（净持仓）仍由 build_book 提供，仅用于风险/Greeks，不用于本盈亏公式。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class MarkPrice:
    symbol: str
    price: float


@dataclass
class PositionLegState:
    """Net book for risk/Greeks only (not used for gross PnL)."""

    symbol: str
    underlying: str
    option_type: str
    strike: float
    multiplier: float
    long_volume: int = 0
    short_volume: int = 0
    ref_settle: float = 0.0  # 昨收/基准价
    long_cost: float = 0.0
    short_cost: float = 0.0
    margin: float = 0.0
    y_long: int = 0
    y_short: int = 0
    t_long: int = 0
    t_short: int = 0
    t_long_cost: float = 0.0
    t_short_cost: float = 0.0

    @property
    def net_volume(self) -> int:
        return self.long_volume - self.short_volume


@dataclass
class LegPnL:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    long_volume: int  # 昨仓买持仓（不因今平仓冲减）
    short_volume: int  # 昨仓卖持仓（不因今平仓冲减）
    mark: float  # 最新价
    ref_settle: float  # 昨收（优先 close）
    carry_pnl: float  # 昨日持仓损益
    today_trade_pnl: float  # 该合约今日成交损益合计
    fee: float
    total_pnl: float
    margin: float
    multiplier: float
    today_buy_volume: int = 0
    today_sell_volume: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LivePnLReport:
    account_id: str
    settlement_date: str
    session_date: str
    opening_equity: float
    margin_occupied_settlement: float
    available_settlement: float
    risk_degree_settlement: float
    total_carry_pnl: float  # 昨日持仓损益合计
    total_today_trade_pnl: float  # 今日成交损益合计
    total_fees: float
    total_pnl: float  # 套利策略损益 = 昨仓 + 今成交 - 费
    estimated_equity: float
    by_leg: list[LegPnL] = field(default_factory=list)
    by_underlying: list[dict[str, Any]] = field(default_factory=list)
    by_trade: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    methodology: str = (
        "套利=昨仓损益+今成交损益；"
        "昨仓: 方向×数量×(最新-昨收)×乘数；"
        "今成交: 方向×数量×(最新-成交价)×乘数；不冲抵"
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ref_close(p: dict[str, Any]) -> float:
    """
    昨仓浮动盈亏基准价（昨收）— Libra / 夜盘口径:
      1) 行情日盘收盘价 prev_close / __PREV_CLOSE__（夜盘21:00后 = 当天下午15:00收盘）
      2) 显式 ref_close / close_price（仅当来自行情收盘，勿用脏盘中价）
      3) 回退结算单今结算价 settle_price（无行情时的交易所盯市近似）
    """
    return float(
        p.get("prev_close")
        or p.get("ref_close")
        or p.get("day_close")
        or p.get("close_price")
        or p.get("settle_price")
        or p.get("ref_settle")
        or p.get("ref_price")
        or 0
    )


def _norm_side(side: Any) -> str:
    s = str(side or "").strip().upper()
    if s in {"买", "BUY", "B", "LONG"} or "买" in str(side):
        return "BUY"
    return "SELL"


def build_book(
    yesterday_positions: list[dict[str, Any]],
    today_trades: list[dict[str, Any]],
) -> dict[str, PositionLegState]:
    """
    Net inventory book for Greeks/risk only.
    PnL must NOT use this for gross attribution — see compute_live_pnl.
    """
    book: dict[str, PositionLegState] = {}

    for p in yesterday_positions:
        sym = p["symbol"]
        ref = _ref_close(p)
        leg = PositionLegState(
            symbol=sym,
            underlying=p.get("underlying") or sym,
            option_type=p.get("option_type") or "",
            strike=float(p.get("strike") or 0),
            multiplier=float(p.get("multiplier") or 10),
            long_volume=int(p.get("long_volume") or 0),
            short_volume=int(p.get("short_volume") or 0),
            ref_settle=ref,
            long_cost=float(p.get("long_avg_price") or 0),
            short_cost=float(p.get("short_avg_price") or 0),
            margin=float(p.get("margin") or 0),
            y_long=int(p.get("long_volume") or 0),
            y_short=int(p.get("short_volume") or 0),
        )
        book[sym] = leg

    ordered = sorted(
        today_trades,
        key=lambda t: (str(t.get("trade_date") or ""), str(t.get("trade_time") or ""), str(t.get("trade_id") or "")),
    )

    for t in ordered:
        sym = t["symbol"]
        side = _norm_side(t.get("side"))
        offset = str(t.get("offset") or "OPEN").upper()
        vol = int(t.get("volume") or 0)
        px = float(t.get("price") or 0)
        if vol <= 0:
            continue
        if sym not in book:
            book[sym] = PositionLegState(
                symbol=sym,
                underlying=t.get("underlying") or sym,
                option_type=t.get("option_type") or "",
                strike=float(t.get("strike") or 0),
                multiplier=float(t.get("multiplier") or 10),
                ref_settle=px,
            )
        leg = book[sym]
        if t.get("multiplier"):
            leg.multiplier = float(t["multiplier"])

        if offset == "CLOSE":
            if side == "BUY":
                close_y = min(leg.y_short, vol)
                leg.y_short -= close_y
                rem = vol - close_y
                close_t = min(leg.t_short, rem)
                leg.t_short -= close_t
                leg.short_volume = leg.y_short + leg.t_short
            else:
                close_y = min(leg.y_long, vol)
                leg.y_long -= close_y
                rem = vol - close_y
                close_t = min(leg.t_long, rem)
                leg.t_long -= close_t
                leg.long_volume = leg.y_long + leg.t_long
        else:
            if side == "SELL":
                new_v = leg.t_short + vol
                if new_v > 0:
                    leg.t_short_cost = (leg.t_short_cost * leg.t_short + px * vol) / new_v
                leg.t_short = new_v
                leg.short_volume = leg.y_short + leg.t_short
            else:
                new_v = leg.t_long + vol
                if new_v > 0:
                    leg.t_long_cost = (leg.t_long_cost * leg.t_long + px * vol) / new_v
                leg.t_long = new_v
                leg.long_volume = leg.y_long + leg.t_long

    return book


def _direction_pnl(signed_qty: float, last: float, entry: float, mult: float) -> float:
    """signed_qty: +多 / -空；损益 = signed_qty * (last - entry) * mult。"""
    return float(signed_qty) * (last - entry) * mult


def compute_live_pnl(
    *,
    account_id: str,
    settlement_date: str,
    session_date: str,
    yesterday_positions: list[dict[str, Any]],
    today_trades: list[dict[str, Any]],
    marks: dict[str, float],
    opening_equity: float = 0.0,
    margin_occupied_settlement: float = 0.0,
    available_settlement: float = 0.0,
    risk_degree_settlement: float = 0.0,
) -> LivePnLReport:
    """
    Gross PnL — 昨仓与今成交独立计价，不做开平冲抵。

    昨仓:  多 +q*(last-close)*mult；空 -q*(last-close)*mult
    今成交: 买 +q*(last-px)*mult；卖 -q*(last-px)*mult
    """
    # --- 昨日持仓损益（按昨仓数量，不冲减）---
    y_pnl_by_sym: dict[str, float] = {}
    meta_by_sym: dict[str, dict[str, Any]] = {}
    total_carry = 0.0

    for p in yesterday_positions:
        sym = p["symbol"]
        mult = float(p.get("multiplier") or 10)
        close = _ref_close(p)
        y_long = int(p.get("long_volume") or 0)
        y_short = int(p.get("short_volume") or 0)
        raw_mark = marks.get(sym)
        # 无有效最新价（缺失或≤0）→ 浮动盈亏记 0，对齐 Libra 无夜盘/无行情品种
        if raw_mark is None or float(raw_mark) <= 0:
            last = close
            pnl = 0.0
        else:
            last = float(raw_mark)
            # 多头 +，空头 −
            pnl = _direction_pnl(+y_long, last, close, mult) + _direction_pnl(-y_short, last, close, mult)
        y_pnl_by_sym[sym] = y_pnl_by_sym.get(sym, 0.0) + pnl
        total_carry += pnl
        meta_by_sym[sym] = {
            "underlying": p.get("underlying") or sym,
            "option_type": p.get("option_type") or "",
            "strike": float(p.get("strike") or 0),
            "multiplier": mult,
            "long_volume": y_long,
            "short_volume": y_short,
            "ref_close": close,
            "mark": last,
            "margin": float(p.get("margin") or 0),
            "live": raw_mark is not None and float(raw_mark) > 0,
        }

    # --- 今日成交损益（逐笔，不与昨仓冲抵）---
    t_pnl_by_sym: dict[str, float] = {}
    buy_vol: dict[str, int] = {}
    sell_vol: dict[str, int] = {}
    fee_by_sym: dict[str, float] = {}
    by_trade: list[dict[str, Any]] = []
    total_today = 0.0
    total_fees = 0.0

    for t in today_trades:
        sym = t["symbol"]
        side = _norm_side(t.get("side"))
        vol = int(t.get("volume") or 0)
        px = float(t.get("price") or 0)
        fee = float(t.get("fee") or 0)
        mult = float(t.get("multiplier") or meta_by_sym.get(sym, {}).get("multiplier") or 10)
        if vol <= 0:
            continue
        # 最新价：无有效 mark 时今成交浮动也记 0（保留成交信息）
        sign = +1 if side == "BUY" else -1
        raw_mark = marks.get(sym)
        if raw_mark is None or float(raw_mark) <= 0:
            last = px
            pnl = 0.0
        else:
            last = float(raw_mark)
            pnl = _direction_pnl(sign * vol, last, px, mult)
        t_pnl_by_sym[sym] = t_pnl_by_sym.get(sym, 0.0) + pnl
        total_today += pnl
        fee_by_sym[sym] = fee_by_sym.get(sym, 0.0) + fee
        total_fees += fee
        if side == "BUY":
            buy_vol[sym] = buy_vol.get(sym, 0) + vol
        else:
            sell_vol[sym] = sell_vol.get(sym, 0) + vol

        if sym not in meta_by_sym:
            meta_by_sym[sym] = {
                "underlying": t.get("underlying") or sym,
                "option_type": t.get("option_type") or "",
                "strike": float(t.get("strike") or 0),
                "multiplier": mult,
                "long_volume": 0,
                "short_volume": 0,
                "ref_close": 0.0,
                "mark": last,
                "margin": 0.0,
            }
        else:
            meta_by_sym[sym]["mark"] = last

        by_trade.append(
            {
                "trade_id": t.get("trade_id") or "",
                "symbol": sym,
                "underlying": meta_by_sym[sym]["underlying"],
                "side": side,
                "offset": str(t.get("offset") or ""),
                "volume": vol,
                "price": px,
                "mark": last,
                "multiplier": mult,
                "pnl": round(pnl, 2),
                "fee": fee,
                "formula": f"{'+' if sign > 0 else '-'}{vol}*(最新{last}-成交{px})*{mult}",
            }
        )

    # --- 按合约汇总展示 ---
    all_syms = sorted(set(y_pnl_by_sym) | set(t_pnl_by_sym) | set(meta_by_sym))
    legs: list[LegPnL] = []
    for sym in all_syms:
        meta = meta_by_sym.get(sym, {})
        carry = y_pnl_by_sym.get(sym, 0.0)
        today_pnl = t_pnl_by_sym.get(sym, 0.0)
        fee = fee_by_sym.get(sym, 0.0)
        legs.append(
            LegPnL(
                symbol=sym,
                underlying=str(meta.get("underlying") or sym),
                option_type=str(meta.get("option_type") or ""),
                strike=float(meta.get("strike") or 0),
                long_volume=int(meta.get("long_volume") or 0),
                short_volume=int(meta.get("short_volume") or 0),
                mark=float(meta.get("mark") or marks.get(sym) or 0),
                ref_settle=float(meta.get("ref_close") or 0),
                carry_pnl=round(carry, 2),
                today_trade_pnl=round(today_pnl, 2),
                fee=round(fee, 2),
                total_pnl=round(carry + today_pnl - fee, 2),
                margin=float(meta.get("margin") or 0),
                multiplier=float(meta.get("multiplier") or 10),
                today_buy_volume=buy_vol.get(sym, 0),
                today_sell_volume=sell_vol.get(sym, 0),
            )
        )

    by_u: dict[str, dict[str, Any]] = {}
    for leg in legs:
        u = by_u.setdefault(
            leg.underlying,
            {
                "underlying": leg.underlying,
                "total_pnl": 0.0,
                "carry_pnl": 0.0,
                "today_trade_pnl": 0.0,
                "fee": 0.0,
                "margin": 0.0,
                "short_volume": 0,
                "long_volume": 0,
                "legs": 0,
            },
        )
        u["total_pnl"] += leg.total_pnl
        u["carry_pnl"] += leg.carry_pnl
        u["today_trade_pnl"] += leg.today_trade_pnl
        u["fee"] += leg.fee
        u["margin"] += leg.margin
        u["short_volume"] += leg.short_volume
        u["long_volume"] += leg.long_volume
        u["legs"] += 1

    by_underlying = sorted(by_u.values(), key=lambda x: x["total_pnl"])
    for u in by_underlying:
        for k in ("total_pnl", "carry_pnl", "today_trade_pnl", "fee", "margin"):
            u[k] = round(u[k], 2)

    total_pnl = total_carry + total_today - total_fees
    alerts: list[str] = []
    if opening_equity > 0 and margin_occupied_settlement / opening_equity > 0.60:
        alerts.append("结算保证金占用率已超过 60%，Screener 将拒绝新开仓建议")

    return LivePnLReport(
        account_id=account_id,
        settlement_date=settlement_date,
        session_date=session_date,
        opening_equity=opening_equity,
        margin_occupied_settlement=margin_occupied_settlement,
        available_settlement=available_settlement,
        risk_degree_settlement=risk_degree_settlement,
        total_carry_pnl=round(total_carry, 2),
        total_today_trade_pnl=round(total_today, 2),
        total_fees=round(total_fees, 2),
        total_pnl=round(total_pnl, 2),
        estimated_equity=round(opening_equity + total_pnl, 2) if opening_equity else round(total_pnl, 2),
        by_leg=legs,
        by_underlying=by_underlying,
        by_trade=by_trade,
        alerts=alerts,
    )
