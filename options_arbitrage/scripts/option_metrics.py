#!/usr/bin/env python3
"""
Standard option metrics CLI — see docs/方法与口径.md

Usage:
  python scripts/option_metrics.py iv --F 5200 --K 5700 --T 0.18 --price 17.5 --type call
  python scripts/option_metrics.py greeks --F 5200 --K 5700 --T 0.18 --iv 0.21 --type call
  python scripts/option_metrics.py pop --F 5200 --K-put 5100 --K-call 5700 --T 0.18 --sigma 0.115
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.bs76_engine import black76_greeks, black76_price, implied_volatility
from core.metrics import pop_lognormal, pop_approx


METHODS_VERSION = "methods-v2.0.0"


def cmd_iv(args: argparse.Namespace) -> dict:
    opt = args.type.upper()
    iv = implied_volatility(args.price, args.F, args.K, args.T, args.r, opt)  # type: ignore[arg-type]
    return {
        "methods_version": METHODS_VERSION,
        "model": "Black-76",
        "F": args.F,
        "K": args.K,
        "T": args.T,
        "market_price": args.price,
        "option_type": opt,
        "implied_vol": iv,
        "american_risk_flag": True,
    }


def cmd_greeks(args: argparse.Namespace) -> dict:
    opt = args.type.upper()
    g = black76_greeks(args.F, args.K, args.T, args.r, args.iv, opt)  # type: ignore[arg-type]
    return {
        "methods_version": METHODS_VERSION,
        "model": "Black-76",
        "F": args.F,
        "K": args.K,
        "T": args.T,
        "iv": args.iv,
        "option_type": opt,
        "price": g.price,
        "delta": g.delta,
        "gamma": g.gamma,
        "vega": g.vega,
        "theta": g.theta,
        "american_risk_flag": True,
    }


def cmd_pop(args: argparse.Namespace) -> dict:
    rn = pop_lognormal(args.F, args.K_put, args.K_call, args.T, args.sigma, args.r)
    return {
        "methods_version": METHODS_VERSION,
        "expiry_profit_prob_risk_neutral": rn,
        "label": "风险中性到期盈利概率，非真实胜率",
        "F": args.F,
        "K_put": args.K_put,
        "K_call": args.K_call,
        "T": args.T,
        "sigma_used": args.sigma,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Option metrics (methods-v2.0.0)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ivp = sub.add_parser("iv", help="Invert implied volatility")
    ivp.add_argument("--F", type=float, required=True)
    ivp.add_argument("--K", type=float, required=True)
    ivp.add_argument("--T", type=float, required=True, help="Years to expiry")
    ivp.add_argument("--price", type=float, required=True)
    ivp.add_argument("--type", choices=["call", "put"], required=True)
    ivp.add_argument("--r", type=float, default=0.02)

    gp = sub.add_parser("greeks", help="Compute Greeks")
    gp.add_argument("--F", type=float, required=True)
    gp.add_argument("--K", type=float, required=True)
    gp.add_argument("--T", type=float, required=True)
    gp.add_argument("--iv", type=float, required=True)
    gp.add_argument("--type", choices=["call", "put"], required=True)
    gp.add_argument("--r", type=float, default=0.02)

    pp = sub.add_parser("pop", help="Risk-neutral expiry profit probability")
    pp.add_argument("--F", type=float, required=True)
    pp.add_argument("--K-put", type=float, required=True)
    pp.add_argument("--K-call", type=float, required=True)
    pp.add_argument("--T", type=float, required=True)
    pp.add_argument("--sigma", type=float, required=True, help="HV or RN sigma")
    pp.add_argument("--r", type=float, default=0.02)

    args = p.parse_args()
    if args.cmd == "iv":
        out = cmd_iv(args)
    elif args.cmd == "greeks":
        out = cmd_greeks(args)
    else:
        out = cmd_pop(args)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
