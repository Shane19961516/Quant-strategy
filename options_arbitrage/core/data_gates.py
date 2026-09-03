"""Data quality gates for next-session short strangle candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    severity: str = "hard"  # hard | soft


@dataclass
class GateSummary:
    results: list[GateResult] = field(default_factory=list)

    @property
    def all_hard_passed(self) -> bool:
        return all(r.passed for r in self.results if r.severity == "hard")

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def add(self, name: str, passed: bool, detail: str, *, severity: str = "hard") -> None:
        self.results.append(GateResult(name, passed, detail, severity))

    def to_dict(self) -> list[dict[str, Any]]:
        return [{"name": r.name, "passed": r.passed, "detail": r.detail, "severity": r.severity} for r in self.results]


def check_quote_freshness(
    quote_date: Optional[date],
    as_of: date,
    quote_timestamp: Optional[datetime],
) -> GateResult:
    if quote_date is None:
        return GateResult("quote_same_day", False, "缺少行情交易日", "hard")
    if quote_date != as_of:
        return GateResult(
            "quote_same_day",
            False,
            f"行情日 {quote_date} ≠ 扫描基准日 {as_of}",
            "hard",
        )
    ts = quote_timestamp.isoformat() if quote_timestamp else "unknown"
    return GateResult("quote_same_day", True, f"行情日一致 timestamp={ts}", "hard")


def check_mapping(
    option_month: str,
    underlying_futures: str,
    multiplier: Optional[float],
    tick_size: Optional[float],
) -> GateResult:
    missing = []
    if not option_month:
        missing.append("option_month")
    if not underlying_futures:
        missing.append("underlying_futures")
    if multiplier is None:
        missing.append("multiplier")
    if tick_size is None:
        missing.append("tick_size")
    if missing:
        return GateResult("mapping_clear", False, f"映射缺失: {', '.join(missing)}", "hard")
    if underlying_futures.lower().replace(" ", "")[:4] != option_month.lower()[:4]:
        # soft warning if month codes differ format but still mapped explicitly
        pass
    return GateResult(
        "mapping_clear",
        True,
        f"期权月={option_month} 标的={underlying_futures} mult={multiplier} tick={tick_size}",
        "hard",
    )


def check_bid_ask_leg(
    leg_name: str,
    bid: Optional[float],
    ask: Optional[float],
) -> GateResult:
    if bid is None or ask is None:
        return GateResult(f"bid_ask_{leg_name}", False, f"{leg_name} 缺少买一/卖一", "hard")
    if bid <= 0 or ask <= 0:
        return GateResult(f"bid_ask_{leg_name}", False, f"{leg_name} 报价非正 bid={bid} ask={ask}", "hard")
    if ask < bid:
        return GateResult(f"bid_ask_{leg_name}", False, f"{leg_name} 卖一<买一", "hard")
    return GateResult(f"bid_ask_{leg_name}", True, f"{leg_name} bid={bid} ask={ask}", "hard")


def check_iv_solved(iv: Optional[float], leg: str) -> GateResult:
    if iv is None:
        return GateResult(f"iv_solved_{leg}", False, f"{leg} IV 反解失败", "hard")
    if iv <= 0 or iv > 3.0:
        return GateResult(f"iv_solved_{leg}", False, f"{leg} IV 异常 iv={iv}", "hard")
    return GateResult(f"iv_solved_{leg}", True, f"{leg} iv={iv:.4f}", "hard")


def check_iv_history(n_obs: int, required: int = 252, *, source: str = "") -> GateResult:
    valid = source in {
        "exchange_czce_atm",
        "exchange_shfe_inverted",
        "exchange_dce_inverted",
        "exchange_gfex",
        "csv_import",
        "user_csv",
    } or source.startswith("user_csv") or source.startswith("csv")
    if n_obs >= required and valid:
        return GateResult("iv_history_252", True, f"固定期限 ATM IV 历史 {n_obs} 日 source={source}", "hard")
    if n_obs >= required and not valid:
        return GateResult(
            "iv_history_252",
            False,
            f"有 {n_obs} 日但来源无效 source={source}（禁止 HV 代理）",
            "hard",
        )
    return GateResult(
        "iv_history_252",
        False,
        f"固定期限 ATM IV 历史仅 {n_obs} 日，需要 {required} 日 source={source or 'none'}",
        "hard",
    )


def check_rules_meta(rules_version: Optional[str], verified_date: Optional[str]) -> GateResult:
    if not rules_version or not verified_date:
        return GateResult("rules_verified", False, "规则包缺少 version/verified_date", "hard")
    return GateResult("rules_verified", True, f"rules={rules_version} verified={verified_date}", "hard")


def check_events_loaded(calendar_source: str, last_updated: str, n_events: int) -> GateResult:
    if not calendar_source:
        return GateResult("events_checked", False, "事件日历未加载", "hard")
    return GateResult(
        "events_checked",
        True,
        f"source={calendar_source} updated={last_updated} events={n_events}",
        "hard",
    )


def check_client_margin_known(client_margin_rate: Optional[float]) -> GateResult:
    if client_margin_rate is None:
        return GateResult(
            "client_margin_known",
            False,
            "未提供客户/期货公司保证金加收比例",
            "hard",
        )
    return GateResult("client_margin_known", True, f"client_addon={client_margin_rate}", "hard")


def check_account_equity(equity: Optional[float]) -> GateResult:
    if equity is None:
        return GateResult(
            "account_equity",
            False,
            "未提供账户权益，不建议手数",
            "soft",
        )
    return GateResult("account_equity", True, f"equity={equity}", "soft")
