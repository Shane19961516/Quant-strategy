# -*- coding: utf-8 -*-
"""因子数据库（FactorStore）：元数据 + 面板值 + 入库记录 + 文档。

布局（默认 ``<repo>/factor_db/``）::

    factor_db/
      factor_store.sqlite
      panels/<factor_id>.csv.gz          # stocks x dates
      docs/<factor_id>.md
      admission/<factor_id>_<asof>.json
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd

from .admission import AdmissionDecision
from .data import REPO_ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS factors (
    factor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    family TEXT,
    description TEXT,
    formula TEXT,
    direction INTEGER NOT NULL DEFAULT 1,
    version TEXT NOT NULL DEFAULT '1.0',
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    admitted_at TEXT,
    last_asof TEXT,
    panel_path TEXT,
    doc_path TEXT,
    source_module TEXT,
    process_spec TEXT
);

CREATE TABLE IF NOT EXISTS admission_runs (
    run_id TEXT PRIMARY KEY,
    factor_id TEXT NOT NULL,
    asof TEXT,
    decided_at TEXT NOT NULL,
    admitted INTEGER NOT NULL,
    reason TEXT,
    metrics_json TEXT,
    gates_json TEXT,
    FOREIGN KEY(factor_id) REFERENCES factors(factor_id)
);

CREATE TABLE IF NOT EXISTS update_runs (
    run_id TEXT PRIMARY KEY,
    schedule TEXT NOT NULL,
    asof TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    n_updated INTEGER DEFAULT 0,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS factor_docs (
    factor_id TEXT PRIMARY KEY,
    title TEXT,
    body_md TEXT,
    api_example TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(factor_id) REFERENCES factors(factor_id)
);

CREATE INDEX IF NOT EXISTS idx_factors_status ON factors(status);
CREATE INDEX IF NOT EXISTS idx_admission_factor ON admission_runs(factor_id);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FactorRecord:
    factor_id: str
    name: str
    family: str
    description: str
    formula: str
    direction: int
    version: str
    status: str
    created_at: str
    updated_at: str
    admitted_at: Optional[str]
    last_asof: Optional[str]
    panel_path: Optional[str]
    doc_path: Optional[str]
    source_module: Optional[str]
    process_spec: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row | Mapping[str, Any]) -> "FactorRecord":
        d = dict(row)
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


class FactorStore:
    """SQLite-backed factor warehouse with on-disk panels."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else (REPO_ROOT / "factor_db")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "panels").mkdir(exist_ok=True)
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "admission").mkdir(exist_ok=True)
        self.db_path = self.root / "factor_store.sqlite"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # ----- write -----
    def upsert_factor_meta(
        self,
        name: str,
        *,
        family: str = "",
        description: str = "",
        formula: str = "",
        direction: int = 1,
        version: str = "1.0",
        status: str = "candidate",
        source_module: str = "factor_engineering.factors",
        process_spec: Optional[Mapping[str, Any]] = None,
        factor_id: Optional[str] = None,
    ) -> str:
        fid = factor_id or name
        now = _utcnow()
        spec = json.dumps(process_spec or {}, ensure_ascii=False)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT factor_id, created_at FROM factors WHERE name=?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE factors SET family=?, description=?, formula=?, direction=?,
                      version=?, status=?, updated_at=?, source_module=?, process_spec=?
                    WHERE name=?
                    """,
                    (
                        family,
                        description,
                        formula,
                        int(direction),
                        version,
                        status,
                        now,
                        source_module,
                        spec,
                        name,
                    ),
                )
                return existing["factor_id"]
            conn.execute(
                """
                INSERT INTO factors (
                  factor_id, name, family, description, formula, direction, version,
                  status, created_at, updated_at, source_module, process_spec
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fid,
                    name,
                    family,
                    description,
                    formula,
                    int(direction),
                    version,
                    status,
                    now,
                    now,
                    source_module,
                    spec,
                ),
            )
        return fid

    def save_panel(self, name: str, panel: pd.DataFrame, asof: Optional[str] = None) -> Path:
        path = self.root / "panels" / f"{name}.csv.gz"
        panel.to_csv(path, encoding="utf-8", compression="gzip")
        last = asof or (str(pd.Timestamp(panel.columns.max()).date()) if len(panel.columns) else None)
        rel = str(path.relative_to(self.root))
        with self._connect() as conn:
            conn.execute(
                "UPDATE factors SET panel_path=?, last_asof=?, updated_at=? WHERE name=?",
                (rel, last, _utcnow(), name),
            )
        return path

    def record_admission(
        self,
        name: str,
        decision: AdmissionDecision,
        *,
        asof: Optional[str] = None,
        auto_status: bool = True,
    ) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        payload = decision.to_dict()
        path = self.root / "admission" / f"{name}_{asof or 'na'}_{run_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT factor_id FROM factors WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Factor {name} not in store; call upsert_factor_meta first")
            fid = row["factor_id"]
            conn.execute(
                """
                INSERT INTO admission_runs
                  (run_id, factor_id, asof, decided_at, admitted, reason, metrics_json, gates_json)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    fid,
                    asof,
                    now,
                    int(decision.admitted),
                    "; ".join(decision.reject_reasons) if not decision.admitted else "PASS",
                    json.dumps(payload.get("metrics", {}), ensure_ascii=False),
                    json.dumps(payload.get("gates", []), ensure_ascii=False),
                ),
            )
            if auto_status:
                if decision.admitted:
                    conn.execute(
                        """
                        UPDATE factors SET status='admitted', direction=?, admitted_at=?,
                          updated_at=? WHERE factor_id=?
                        """,
                        (int(decision.direction), now, now, fid),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE factors SET status='rejected', direction=?, updated_at=?
                        WHERE factor_id=? AND status!='admitted'
                        """,
                        (int(decision.direction), now, fid),
                    )
        return run_id

    def save_doc(
        self,
        name: str,
        body_md: str,
        *,
        title: Optional[str] = None,
        api_example: str = "",
    ) -> Path:
        path = self.root / "docs" / f"{name}.md"
        path.write_text(body_md, encoding="utf-8")
        rel = str(path.relative_to(self.root))
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT factor_id FROM factors WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                raise KeyError(name)
            fid = row["factor_id"]
            conn.execute(
                "UPDATE factors SET doc_path=?, updated_at=? WHERE factor_id=?",
                (rel, now, fid),
            )
            conn.execute(
                """
                INSERT INTO factor_docs (factor_id, title, body_md, api_example, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(factor_id) DO UPDATE SET
                  title=excluded.title, body_md=excluded.body_md,
                  api_example=excluded.api_example, updated_at=excluded.updated_at
                """,
                (fid, title or name, body_md, api_example, now),
            )
        return path

    def begin_update_run(self, schedule: str, asof: Optional[str] = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO update_runs (run_id, schedule, asof, started_at, status)
                VALUES (?,?,?,?,?)
                """,
                (run_id, schedule, asof, _utcnow(), "running"),
            )
        return run_id

    def finish_update_run(
        self,
        run_id: str,
        *,
        status: str = "ok",
        n_updated: int = 0,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE update_runs SET finished_at=?, status=?, n_updated=?, detail_json=?
                WHERE run_id=?
                """,
                (
                    _utcnow(),
                    status,
                    n_updated,
                    json.dumps(detail or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    # ----- read / call API -----
    def list_factors(
        self, status: Optional[str] = "admitted"
    ) -> pd.DataFrame:
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM factors ORDER BY status, name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM factors WHERE status=? ORDER BY name", (status,)
                ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])

    def get_meta(self, name: str) -> FactorRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM factors WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown factor: {name}")
        return FactorRecord.from_row(row)

    def load_panel(
        self,
        name: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        apply_direction: bool = True,
    ) -> pd.DataFrame:
        meta = self.get_meta(name)
        if not meta.panel_path:
            raise FileNotFoundError(f"No panel stored for {name}")
        path = self.root / meta.panel_path
        df = pd.read_csv(path, index_col=0, compression="gzip")
        df.columns = pd.to_datetime(df.columns)
        df.index = df.index.astype(str)
        if start is not None:
            df = df.loc[:, df.columns >= pd.Timestamp(start)]
        if end is not None:
            df = df.loc[:, df.columns <= pd.Timestamp(end)]
        if apply_direction and meta.direction == -1:
            df = -df
        return df

    def get_factor_on(
        self,
        name: str,
        date: str,
        *,
        apply_direction: bool = True,
    ) -> pd.Series:
        """读取某一时点截面因子值（调用主入口）。"""
        panel = self.load_panel(name, apply_direction=apply_direction)
        ts = pd.Timestamp(date)
        # exact or month match
        if ts in panel.columns:
            s = panel[ts]
        else:
            key = ts.strftime("%Y-%m")
            cols = [c for c in panel.columns if pd.Timestamp(c).strftime("%Y-%m") == key]
            if not cols:
                raise KeyError(f"No factor column for {name} @ {date}")
            s = panel[cols[-1]]
        s.name = name
        return s.dropna()

    def get_doc(self, name: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.body_md FROM factor_docs d
                JOIN factors f ON f.factor_id=d.factor_id
                WHERE f.name=?
                """,
                (name,),
            ).fetchone()
        if row and row["body_md"]:
            return row["body_md"]
        meta = self.get_meta(name)
        if meta.doc_path and (self.root / meta.doc_path).exists():
            return (self.root / meta.doc_path).read_text(encoding="utf-8")
        raise KeyError(f"No documentation for {name}")

    def latest_admission(self, name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.* FROM admission_runs a
                JOIN factors f ON f.factor_id=a.factor_id
                WHERE f.name=?
                ORDER BY a.decided_at DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def update_history(self, limit: int = 20) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM update_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
