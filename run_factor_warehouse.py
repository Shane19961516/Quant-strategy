# -*- coding: utf-8 -*-
"""因子库 CLI：入库流水线 / 更新 / 查询。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_engineering.admission import AdmissionCriteria
from factor_engineering.data import REPO_ROOT
from factor_engineering.docs import render_admission_standard_md
from factor_engineering.factors import DEFAULT_FACTOR_NAMES, FACTOR_REGISTRY
from factor_engineering.store import FactorStore
from factor_engineering.update import UpdateConfig, describe_schedule, run_scheduled_update
from factor_engineering.warehouse import run_warehouse_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="因子工程完整流程：生成→检验→入库→更新→调用"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    admit = sub.add_parser("admit", help="生成+完整检验+按标准入库")
    admit.add_argument("--start", default="2010-01-01")
    admit.add_argument("--end", default="2019-12-31")
    admit.add_argument("--universe", default="intersect", choices=["intersect", "csi300"])
    admit.add_argument("--factors", default=",".join(DEFAULT_FACTOR_NAMES))
    admit.add_argument("--db", default=str(REPO_ROOT / "factor_db"))
    admit.add_argument("--min-abs-ic", type=float, default=None)
    admit.add_argument("--min-abs-icir", type=float, default=None)
    admit.add_argument("--min-ls-sharpe", type=float, default=None)

    upd = sub.add_parser("update", help="固定时点更新已入库因子")
    upd.add_argument("--schedule", default="month_end", choices=["month_end", "manual"])
    upd.add_argument("--db", default=str(REPO_ROOT / "factor_db"))
    upd.add_argument("--end", default=None, help="数据截止日，默认用面板最新")
    upd.add_argument("--no-retest", action="store_true")
    upd.add_argument("--all-status", action="store_true", help="更新全部状态因子")
    upd.add_argument("--factors", default=None, help="逗号分隔，覆盖库内列表")

    ls = sub.add_parser("list", help="列出因子库")
    ls.add_argument("--db", default=str(REPO_ROOT / "factor_db"))
    ls.add_argument(
        "--status",
        default="admitted",
        choices=["admitted", "rejected", "candidate", "all"],
    )

    get = sub.add_parser("get", help="读取某时点因子截面")
    get.add_argument("name")
    get.add_argument("--date", required=True)
    get.add_argument("--db", default=str(REPO_ROOT / "factor_db"))
    get.add_argument("--top", type=int, default=10)

    doc = sub.add_parser("doc", help="打印因子解释文档")
    doc.add_argument("name")
    doc.add_argument("--db", default=str(REPO_ROOT / "factor_db"))

    sub.add_parser("standard", help="打印入库标准")
    sub.add_parser("schedule", help="打印固定时点更新说明")
    sub.add_parser("list-registry", help="打印可生成的因子注册表")
    return p


def _criteria_from_args(args) -> AdmissionCriteria:
    crit = AdmissionCriteria()
    overrides = {}
    if getattr(args, "min_abs_ic", None) is not None:
        overrides["min_abs_ic"] = args.min_abs_ic
    if getattr(args, "min_abs_icir", None) is not None:
        overrides["min_abs_icir"] = args.min_abs_icir
    if getattr(args, "min_ls_sharpe", None) is not None:
        overrides["min_ls_sharpe"] = args.min_ls_sharpe
    if overrides:
        crit = AdmissionCriteria(**{**crit.to_dict(), **overrides})
    return crit


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "list-registry":
        for n in sorted(FACTOR_REGISTRY):
            print(n)
        return 0

    if args.cmd == "standard":
        print(render_admission_standard_md())
        return 0

    if args.cmd == "schedule":
        print(describe_schedule())
        return 0

    if args.cmd == "admit":
        names = [x.strip() for x in args.factors.split(",") if x.strip()]
        crit = _criteria_from_args(args)
        print(f"[admit] factors={names} range={args.start}~{args.end}")
        result = run_warehouse_pipeline(
            db_root=args.db,
            start=args.start,
            end=args.end,
            universe=args.universe,
            factor_names=names,
            criteria=crit,
        )
        print(result.summary.to_string(float_format=lambda x: f"{x:.4f}"))
        print(f"\nASOF={result.asof}")
        print(f"ADMITTED ({len(result.admitted)}): {result.admitted}")
        print(f"REJECTED ({len(result.rejected)}): {result.rejected}")
        print(f"DB: {result.store.root}")
        return 0

    if args.cmd == "update":
        names = (
            [x.strip() for x in args.factors.split(",") if x.strip()]
            if args.factors
            else None
        )
        cfg = UpdateConfig(
            schedule=args.schedule,
            retest=not args.no_retest,
            only_admitted=not args.all_status,
        )
        out = run_scheduled_update(
            store=FactorStore(args.db),
            config=cfg,
            factor_names=names,
            end=args.end,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") in {"ok", "skipped", "partial"} else 1

    if args.cmd == "list":
        store = FactorStore(args.db)
        status = None if args.status == "all" else args.status
        df = store.list_factors(status=status)
        if df.empty:
            print("(empty)")
        else:
            cols = [
                c
                for c in [
                    "name",
                    "family",
                    "status",
                    "direction",
                    "last_asof",
                    "description",
                ]
                if c in df.columns
            ]
            print(df[cols].to_string(index=False))
        return 0

    if args.cmd == "get":
        store = FactorStore(args.db)
        s = store.get_factor_on(args.name, args.date)
        print(s.sort_values(ascending=False).head(args.top).to_string())
        print(f"... n={len(s)}")
        return 0

    if args.cmd == "doc":
        store = FactorStore(args.db)
        print(store.get_doc(args.name))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
