"""Parse domestic futures broker settlement XLS (东亚期货客户交易结算日报)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

PathLike = Union[str, Path]


def _load_product_specs(path: Optional[PathLike] = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "product_specs.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def product_code_from_underlying(underlying: str) -> str:
    """Extract product code from underlying like AP610 / EG2610 / au2610 / SR611."""
    m = re.match(r"^([A-Za-z]+)", str(underlying).strip())
    return m.group(1) if m else str(underlying)


def lookup_multiplier(underlying_or_product: str, specs: Optional[dict] = None) -> float:
    specs = specs or _load_product_specs()
    code = product_code_from_underlying(underlying_or_product)
    products = specs.get("products", {})
    for key in (code, code.upper(), code.lower()):
        if key in products:
            return float(products[key]["multiplier"])
    return float(specs.get("default_multiplier", 10))


def parse_option_symbol(symbol: str) -> dict[str, Any]:
    """
    Parse broker option symbols:
      AP610C8200 / AP610P7500
      EG2610-C-5200 / JD2610-P-3650
      AU2610P880
    """
    s = str(symbol).strip().upper().replace(" ", "")
    # dashed form: EG2610-C-5200
    m = re.match(r"^([A-Z]+\d+)-([CP])(?:ALL)?-(\d+(?:\.\d+)?)$", s)
    if m:
        underlying, cp, strike = m.group(1), m.group(2), float(m.group(3))
        return {
            "symbol": symbol.strip(),
            "underlying": underlying,
            "option_type": "CALL" if cp == "C" else "PUT",
            "strike": strike,
            "product": product_code_from_underlying(underlying),
        }
    # compact: AP610C8200 / AU2610P880
    m = re.match(r"^([A-Z]+\d+)([CP])(\d+(?:\.\d+)?)$", s)
    if m:
        underlying, cp, strike = m.group(1), m.group(2), float(m.group(3))
        return {
            "symbol": symbol.strip(),
            "underlying": underlying,
            "option_type": "CALL" if cp == "C" else "PUT",
            "strike": strike,
            "product": product_code_from_underlying(underlying),
        }
    return {
        "symbol": symbol.strip(),
        "underlying": symbol.strip(),
        "option_type": "",
        "strike": 0.0,
        "product": product_code_from_underlying(symbol),
    }


def _norm_side(val: Any) -> str:
    s = str(val).strip()
    if "卖" in s or s.upper() in {"SELL", "S", "SHORT"}:
        return "SELL"
    if "买" in s or s.upper() in {"BUY", "B", "LONG"}:
        return "BUY"
    return s.upper()


def _norm_offset(val: Any) -> str:
    s = str(val).strip()
    if "平" in s or "CLOSE" in s.upper():
        return "CLOSE"
    if "开" in s or "OPEN" in s.upper():
        return "OPEN"
    return "OPEN" if not s else s.upper()


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("%", "")
    if not s or s == "--":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _to_int(val: Any, default: int = 0) -> int:
    return int(round(_to_float(val, float(default))))


def _sheet_as_grid(xls: pd.ExcelFile, name: str) -> pd.DataFrame:
    return pd.read_excel(xls, sheet_name=name, header=None, engine="xlrd")


def _find_row(df: pd.DataFrame, keyword: str, col: int = 0) -> Optional[int]:
    for i, val in df.iloc[:, col].items():
        if isinstance(val, str) and keyword in val:
            return int(i)
    # also search all columns
    for i in range(len(df)):
        for j in range(min(6, df.shape[1])):
            val = df.iat[i, j]
            if isinstance(val, str) and keyword in val:
                return i
    return None


def _kv_from_row(df: pd.DataFrame, row: int, key_cols: list[tuple[int, int]]) -> dict[str, Any]:
    """Extract key/value pairs where key at col k, value at col v."""
    out: dict[str, Any] = {}
    for kcol, vcol in key_cols:
        key = df.iat[row, kcol] if kcol < df.shape[1] else None
        val = df.iat[row, vcol] if vcol < df.shape[1] else None
        if isinstance(key, str) and key.strip():
            out[key.strip()] = val
    return out


@dataclass
class FundStatus:
    account_id: str
    client_name: str
    broker: str
    trade_date: str
    prev_balance: float = 0.0
    deposit_withdraw: float = 0.0
    realized_pnl: float = 0.0  # 当日盈亏
    premium_net: float = 0.0  # 当日总权利金
    commission: float = 0.0
    balance: float = 0.0  # 当日结存
    client_equity: float = 0.0
    currency_funds: float = 0.0
    margin_occupied: float = 0.0
    available: float = 0.0
    risk_degree: float = 0.0  # percent number, e.g. 18.16

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OptionPositionRow:
    """昨日/结算期权持仓（来自结算单「期权持仓汇总」）。"""

    symbol: str
    underlying: str
    option_type: str
    strike: float
    long_volume: int
    long_avg_price: float
    short_volume: int
    short_avg_price: float
    prev_settle: float  # 昨结算价
    settle_price: float  # 今结算价（导入后作为今日盯市起点）
    margin: float
    multiplier: float
    trade_code: str = ""
    source: str = "settlement"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OptionTradeRow:
    """期权成交（结算单明细或手动录入）。"""

    trade_id: str
    symbol: str
    underlying: str
    option_type: str
    strike: float
    side: str  # BUY / SELL
    price: float
    volume: int
    premium_cash: float
    fee: float
    trade_time: str = ""
    trade_date: str = ""
    offset: str = "OPEN"  # OPEN / CLOSE
    multiplier: float = 10.0
    source: str = "settlement"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SettlementParseResult:
    fund: FundStatus
    option_positions: list[OptionPositionRow] = field(default_factory=list)
    option_trades: list[OptionTradeRow] = field(default_factory=list)
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund": self.fund.to_dict(),
            "option_positions": [p.to_dict() for p in self.option_positions],
            "option_trades": [t.to_dict() for t in self.option_trades],
            "raw_meta": self.raw_meta,
        }


def _parse_fund(df: pd.DataFrame) -> FundStatus:
    account_id = ""
    client_name = ""
    broker = ""
    trade_date = ""
    for i in range(min(10, len(df))):
        row = {str(df.iat[i, j]).strip(): df.iat[i, j + 2] if j + 2 < df.shape[1] else None for j in range(0, 6, 3)}
        # simpler cell scan
        for j in range(df.shape[1]):
            cell = df.iat[i, j]
            if not isinstance(cell, str):
                continue
            key = cell.strip()
            val = df.iat[i, j + 2] if j + 2 < df.shape[1] else None
            if key == "客户期货期权内部资金账户":
                account_id = str(val).strip() if val is not None else ""
            elif key == "客户名称":
                client_name = str(val).strip() if val is not None else ""
            elif key == "期货公司名称":
                broker = str(val).strip() if val is not None else ""
            elif key == "交易日期":
                # may be datetime
                if hasattr(val, "strftime"):
                    trade_date = val.strftime("%Y-%m-%d")
                else:
                    trade_date = str(val).strip()[:10]

    def pick(label: str) -> float:
        for i in range(len(df)):
            for j in range(min(8, df.shape[1])):
                cell = df.iat[i, j]
                if isinstance(cell, str) and cell.strip() == label:
                    return _to_float(df.iat[i, j + 2] if j + 2 < df.shape[1] else 0)
        return 0.0

    risk_raw = None
    for i in range(len(df)):
        for j in range(min(8, df.shape[1])):
            cell = df.iat[i, j]
            if isinstance(cell, str) and cell.strip() == "风险度":
                risk_raw = df.iat[i, j + 2] if j + 2 < df.shape[1] else 0
    risk = _to_float(risk_raw)
    # if stored as 0.1816 accidentally treat as percent already from sheet "18.16%"

    return FundStatus(
        account_id=account_id or "UNKNOWN",
        client_name=client_name,
        broker=broker,
        trade_date=trade_date,
        prev_balance=pick("上日结存"),
        deposit_withdraw=pick("当日存取合计"),
        realized_pnl=pick("当日盈亏"),
        premium_net=pick("当日总权利金"),
        commission=pick("当日手续费"),
        balance=pick("当日结存"),
        client_equity=pick("客户权益"),
        currency_funds=pick("实有货币资金"),
        margin_occupied=pick("保证金占用"),
        available=pick("可用资金"),
        risk_degree=risk,
    )


def _parse_option_positions(df: pd.DataFrame, specs: dict) -> list[OptionPositionRow]:
    start = _find_row(df, "期权持仓汇总")
    if start is None:
        return []
    # header row usually start+1
    header_i = start + 1
    rows: list[OptionPositionRow] = []
    for i in range(header_i + 1, len(df)):
        sym = df.iat[i, 0]
        if sym is None or (isinstance(sym, float) and pd.isna(sym)):
            continue
        sym_s = str(sym).strip()
        if not sym_s or sym_s == "合计":
            if sym_s == "合计":
                break
            continue
        meta = parse_option_symbol(sym_s)
        underlying = str(df.iat[i, 1]).strip() if pd.notna(df.iat[i, 1]) else meta["underlying"]
        opt_cn = str(df.iat[i, 2]).strip() if pd.notna(df.iat[i, 2]) else ""
        opt_type = meta["option_type"] or ("CALL" if "涨" in opt_cn else ("PUT" if "跌" in opt_cn else ""))
        strike = _to_float(df.iat[i, 3], meta["strike"])
        long_vol = _to_int(df.iat[i, 4])
        long_avg = _to_float(df.iat[i, 5])
        short_vol = _to_int(df.iat[i, 6])
        short_avg = _to_float(df.iat[i, 7])
        prev_settle = _to_float(df.iat[i, 8])
        settle = _to_float(df.iat[i, 9])
        margin = _to_float(df.iat[i, 10])
        trade_code = str(df.iat[i, 11]).strip() if df.shape[1] > 11 and pd.notna(df.iat[i, 11]) else ""
        mult = lookup_multiplier(underlying, specs)
        rows.append(
            OptionPositionRow(
                symbol=sym_s,
                underlying=underlying,
                option_type=opt_type,
                strike=strike,
                long_volume=long_vol,
                long_avg_price=long_avg,
                short_volume=short_vol,
                short_avg_price=short_avg,
                prev_settle=prev_settle,
                settle_price=settle,
                margin=margin,
                multiplier=mult,
                trade_code=trade_code,
            )
        )
    return rows


def _parse_option_trades(df: pd.DataFrame, trade_date: str, specs: dict) -> list[OptionTradeRow]:
    start = _find_row(df, "期权成交明细")
    if start is None:
        # try summary sheet style — skip
        return []
    header_i = start + 1
    rows: list[OptionTradeRow] = []
    for i in range(header_i + 1, len(df)):
        sym = df.iat[i, 0]
        if sym is None or (isinstance(sym, float) and pd.isna(sym)):
            continue
        sym_s = str(sym).strip()
        if not sym_s or sym_s == "合计":
            break
        meta = parse_option_symbol(sym_s)
        trade_id = str(df.iat[i, 1]).strip() if pd.notna(df.iat[i, 1]) else f"ROW{i}"
        trade_time = str(df.iat[i, 2]).strip() if pd.notna(df.iat[i, 2]) else ""
        side = _norm_side(df.iat[i, 3])
        price = _to_float(df.iat[i, 4])
        volume = _to_int(df.iat[i, 5])
        premium = _to_float(df.iat[i, 6])
        fee = _to_float(df.iat[i, 8]) if df.shape[1] > 8 else 0.0
        tdate = str(df.iat[i, 9]).strip()[:10] if df.shape[1] > 9 and pd.notna(df.iat[i, 9]) else trade_date
        if hasattr(df.iat[i, 9], "strftime"):
            tdate = df.iat[i, 9].strftime("%Y-%m-%d")
        mult = lookup_multiplier(meta["underlying"], specs)
        rows.append(
            OptionTradeRow(
                trade_id=trade_id,
                symbol=sym_s,
                underlying=meta["underlying"],
                option_type=meta["option_type"],
                strike=meta["strike"],
                side=side,
                price=price,
                volume=volume,
                premium_cash=premium,
                fee=fee,
                trade_time=trade_time,
                trade_date=tdate,
                offset="OPEN",  # 结算明细通常不标开平；手动录入时再区分
                multiplier=mult,
                source="settlement",
            )
        )
    return rows


def parse_settlement_xls(path: PathLike) -> SettlementParseResult:
    """Parse a broker daily settlement .xls into fund + option positions (+ optional trades)."""
    specs = _load_product_specs()
    xls = pd.ExcelFile(path, engine="xlrd")
    # Prefer main daily sheet for fund + positions
    main_name = "客户交易结算日报" if "客户交易结算日报" in xls.sheet_names else xls.sheet_names[0]
    main = _sheet_as_grid(xls, main_name)
    fund = _parse_fund(main)
    positions = _parse_option_positions(main, specs)

    trades: list[OptionTradeRow] = []
    if "期权成交明细" in xls.sheet_names:
        trades = _parse_option_trades(_sheet_as_grid(xls, "期权成交明细"), fund.trade_date, specs)

    return SettlementParseResult(
        fund=fund,
        option_positions=positions,
        option_trades=trades,
        raw_meta={"sheets": list(xls.sheet_names), "main_sheet": main_name},
    )
