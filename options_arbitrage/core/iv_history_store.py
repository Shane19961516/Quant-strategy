"""Persistent fixed-tenor ATM IV history store (methods-v2.0.0)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "iv_history"

VALID_SOURCES = frozenset(
    {
        "exchange_czce_atm",
        "exchange_shfe_inverted",
        "exchange_dce_inverted",
        "exchange_gfex",
        "csv_import",
        "user_csv",
    }
)


@dataclass
class IVHistorySeries:
    product: str
    tenor_days: int
    dates: list[str]
    values: list[float]
    source: str
    updated_at: str

    @property
    def n(self) -> int:
        return len(self.values)

    def as_floats(self) -> list[float]:
        return list(self.values)


class IVHistoryStore:
    def __init__(self, root: Optional[Path] = None, tenor_days: int = 30):
        self.root = Path(root) if root else DEFAULT_DIR
        self.tenor_days = tenor_days
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, product: str) -> Path:
        return self.root / f"{product.upper()}_atm{self.tenor_days}.csv"

    def _meta_path(self, product: str) -> Path:
        return self.root / f"{product.upper()}_atm{self.tenor_days}.meta.json"

    def load(self, product: str) -> Optional[IVHistorySeries]:
        path = self._path(product)
        if not path.exists():
            return None
        df = pd.read_csv(path)
        if "date" not in df.columns or "atm_iv" not in df.columns:
            return None
        df = df.dropna(subset=["atm_iv"]).sort_values("date")
        meta = {}
        mp = self._meta_path(product)
        if mp.exists():
            meta = json.loads(mp.read_text(encoding="utf-8"))
        return IVHistorySeries(
            product=product.upper(),
            tenor_days=self.tenor_days,
            dates=df["date"].astype(str).tolist(),
            values=df["atm_iv"].astype(float).tolist(),
            source=str(meta.get("source", "csv_import")),
            updated_at=str(meta.get("updated_at", "")),
        )

    def save(
        self,
        product: str,
        dates: list[str],
        values: list[float],
        *,
        source: str,
        merge: bool = True,
    ) -> IVHistorySeries:
        if source not in VALID_SOURCES and not source.startswith("csv"):
            # still allow save but mark for gate
            pass
        existing = self.load(product) if merge else None
        rows: dict[str, float] = {}
        if existing:
            for d, v in zip(existing.dates, existing.values):
                rows[d[:10]] = float(v)
        for d, v in zip(dates, values):
            if v is None or (isinstance(v, float) and (v != v or v <= 0)):
                continue
            rows[str(d)[:10]] = float(v)
        ordered = sorted(rows.items())
        df = pd.DataFrame(ordered, columns=["date", "atm_iv"])
        self._path(product).write_text(df.to_csv(index=False), encoding="utf-8")
        # rewrite via pandas for cleanliness
        df.to_csv(self._path(product), index=False)
        meta = {
            "product": product.upper(),
            "tenor_days": self.tenor_days,
            "source": source,
            "n": len(df),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "valid_for_recommend": source in VALID_SOURCES or source.startswith("user_csv") or source.startswith("csv"),
        }
        self._meta_path(product).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return IVHistorySeries(
            product=product.upper(),
            tenor_days=self.tenor_days,
            dates=df["date"].astype(str).tolist(),
            values=df["atm_iv"].astype(float).tolist(),
            source=source,
            updated_at=meta["updated_at"],
        )

    def import_csv(self, product: str, path: Path, *, source: str = "user_csv") -> IVHistorySeries:
        df = pd.read_csv(path)
        cols = {c.lower(): c for c in df.columns}
        date_col = cols.get("date") or cols.get("交易日") or cols.get("日期")
        iv_col = cols.get("atm_iv") or cols.get("iv") or cols.get("隐含波动率")
        if not date_col or not iv_col:
            raise ValueError(f"CSV must have date and atm_iv columns: {path}")
        dates = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d").tolist()
        vals = pd.to_numeric(df[iv_col], errors="coerce").tolist()
        # if IV given in percent (>3), convert
        vals2 = []
        for v in vals:
            if v is None or (isinstance(v, float) and v != v):
                continue
            vals2.append(float(v) / 100.0 if float(v) > 3 else float(v))
        dates2 = [d for d, v in zip(dates, vals) if v == v and v is not None]
        return self.save(product, dates2, vals2, source=source, merge=True)

    def list_products(self) -> list[str]:
        return sorted({p.name.split("_")[0] for p in self.root.glob("*_atm*.csv")})
