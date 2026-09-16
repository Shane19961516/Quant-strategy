"""JSON-backed paper account."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from universal_quant import config as cfg

EMPTY_BOOK = {
    "weight": 0.0,
    "side": 0.0,
    "entry": None,
    "stop": None,
    "extreme": None,
    "held": 0,
    "pending": 0.0,
    "trade_pnl": 0.0,
    "entry_time": None,
    "last_close": None,
    "last_bar": None,
    "regime": "",
    "score": 0.0,
    "signal": 0.0,
    "price": None,
    "natr": None,
    "atr": None,
    "forming": False,
    "name": "",
    "cluster": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_account(model: str | None = None, symbols: tuple[str, ...] | None = None) -> dict[str, Any]:
    symbols = symbols or cfg.MVP_SYMBOLS
    books = {}
    for sym in symbols:
        spec = cfg.spec_for(sym)
        book = deepcopy(EMPTY_BOOK)
        book["name"] = spec["name"]
        book["cluster"] = spec["cluster"]
        books[sym] = book
    nav = float(cfg.INITIAL_NAV)
    return {
        "nav": nav,
        "peak_nav": nav,
        "day_start_nav": nav,
        "day_utc": datetime.now(timezone.utc).date().isoformat(),
        "kill": False,
        "kill_reason": "",
        "model": (model or cfg.PAPER_MODEL).upper(),
        "updated_at": _now_iso(),
        "equity": [{"t": _now_iso(), "nav": nav}],
        "trades": [],
        "books": books,
        "last_error": "",
    }


def load_account(path: Path | None = None) -> dict[str, Any]:
    path = path or cfg.PAPER_STATE_PATH
    if not path.exists():
        return empty_account()
    data = json.loads(path.read_text(encoding="utf-8"))
    base = empty_account(data.get("model"))
    base.update({k: data.get(k, base[k]) for k in base if k != "books"})
    for sym, book in base["books"].items():
        if sym in data.get("books", {}):
            book.update(data["books"][sym])
    return base


def save_account(account: dict[str, Any], path: Path | None = None) -> Path:
    path = path or cfg.PAPER_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    account["updated_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(account, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


class PaperAccount:
    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.path = path or cfg.PAPER_STATE_PATH
        self.data = data if data is not None else load_account(self.path)

    def persist(self) -> Path:
        return save_account(self.data, self.path)

    def rollover_day(self, now_date: str) -> None:
        if self.data.get("day_utc") != now_date:
            self.data["day_utc"] = now_date
            self.data["day_start_nav"] = float(self.data["nav"])
            if self.data.get("kill_reason") == "daily_loss":
                self.data["kill"] = False
                self.data["kill_reason"] = ""

    def mark_peak(self) -> None:
        nav = float(self.data["nav"])
        self.data["peak_nav"] = max(float(self.data.get("peak_nav", nav)), nav)

    def drawdown(self) -> float:
        peak = float(self.data.get("peak_nav") or self.data["nav"])
        if peak <= 0:
            return 0.0
        return float(self.data["nav"]) / peak - 1.0

    def day_pnl_pct(self) -> float:
        start = float(self.data.get("day_start_nav") or self.data["nav"])
        if start <= 0:
            return 0.0
        return float(self.data["nav"]) / start - 1.0

    def gross(self) -> float:
        return float(sum(abs(float(b.get("weight") or 0.0)) for b in self.data["books"].values()))

    def net(self) -> float:
        return float(sum(float(b.get("weight") or 0.0) * float(b.get("side") or 0.0) for b in self.data["books"].values()))

    def check_auto_kill(self) -> bool:
        if self.data.get("kill"):
            return True
        if self.day_pnl_pct() <= -float(cfg.DAILY_LOSS_LIMIT):
            self.data["kill"] = True
            self.data["kill_reason"] = "daily_loss"
            return True
        if self.drawdown() <= -float(cfg.PORT_DD_LIMIT):
            self.data["kill"] = True
            self.data["kill_reason"] = "drawdown"
            return True
        return False
