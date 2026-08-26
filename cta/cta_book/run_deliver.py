# -*- coding: utf-8 -*-
"""python -m cta.cta_book.run_deliver --data-dir cta_data_akshare --plot"""

from __future__ import annotations

import argparse
import sys

from .deliverable import run_deliverable


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="全品种 CTA 可交付组合")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--out-dir", default="cta_result_deliver")
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args(argv)
    out = run_deliverable(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        plot=args.plot and not args.no_plot,
    )
    print(
        f"\nDeploy OOS: CAGR={out['oos_deploy']['cagr']:.1%} "
        f"Sharpe={out['oos_deploy']['sharpe']:.2f} "
        f"MaxDD={out['oos_deploy']['max_drawdown']:.1%} hit={out['deploy_hit']}"
    )
    print(
        f"Stretch(OOS-cal) OOS: CAGR={out['oos_stretch']['cagr']:.1%} "
        f"Sharpe={out['oos_stretch']['sharpe']:.2f} "
        f"MaxDD={out['oos_stretch']['max_drawdown']:.1%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
