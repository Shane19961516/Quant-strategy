"""Page: 实时盈亏监控 — 含分品种净持仓与希腊值汇总。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.common import account_id, get_json, inject_sidebar, post_json, session_date

st.set_page_config(page_title="实时盈亏监控", layout="wide")
inject_sidebar()

st.title("③ 实时盈亏监控")
st.caption("昨日持仓盯市 + 当日成交 − 手续费｜分品种净持仓（昨仓+今成交）｜BS76 希腊值汇总")

sess = session_date()
acct = account_id()
if not sess:
    st.warning("请设置监控交易日")
    st.stop()

if st.button("刷新盈亏 / 希腊值", type="primary"):
    st.session_state["_pnl_refresh"] = True

try:
    report = get_json(
        "/api/v1/settlement/live-pnl",
        {"account_id": acct, "session_date": sess},
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"读取失败: {exc}")
    st.stop()

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("期初权益", f"{report['opening_equity']:,.2f}")
k2.metric("今日总盈亏", f"{report['total_pnl']:,.2f}")
k3.metric("持仓盯市", f"{report['total_carry_pnl']:,.2f}")
k4.metric("今日成交盈亏", f"{report['total_today_trade_pnl']:,.2f}")
k5.metric("手续费", f"{report['total_fees']:,.2f}")
k6.metric("估算权益", f"{report['estimated_equity']:,.2f}")

gsum = report.get("greeks_summary") or {}
g1, g2, g3, g4 = st.columns(4)
g1.metric("组合净 Δ", f"{gsum.get('total_net_delta', 0):+.3f}")
g2.metric("组合净 Γ", f"{gsum.get('total_net_gamma', 0):+.5f}")
g3.metric("组合净 Vega", f"{gsum.get('total_net_vega', 0):+,.1f}")
g4.metric("组合净 Theta/日", f"{gsum.get('total_net_theta', 0):+,.1f}")

r1, r2, r3 = st.columns(3)
r1.metric("结算保证金占用", f"{report['margin_occupied_settlement']:,.2f}")
r2.metric("结算可用", f"{report['available_settlement']:,.2f}")
r3.metric("结算风险度", f"{report['risk_degree_settlement']:.2f}%")

if report.get("alerts"):
    for a in report["alerts"]:
        st.warning(a)

# ---- 分品种净持仓 ----
st.subheader("分品种净持仓汇总（昨仓 + 今成交）")
np_ = report.get("net_positions") or {}
by_prod = np_.get("by_product") or gsum.get("by_product") or []
by_u = np_.get("by_underlying") or gsum.get("by_underlying") or []

c_a, c_b = st.columns(2)
with c_a:
    st.markdown("**按品种**")
    if by_prod:
        pdf = pd.DataFrame(by_prod)
        cols = [
            "product",
            "short_volume",
            "long_volume",
            "net_volume",
            "net_delta",
            "net_gamma",
            "net_vega",
            "net_theta",
            "margin",
            "risk_status",
            "underlyings",
        ]
        st.dataframe(pdf[[c for c in cols if c in pdf.columns]], use_container_width=True, hide_index=True)
    else:
        st.info("无品种汇总")
with c_b:
    st.markdown(
        f"**合计** 卖持仓 `{np_.get('total_short_volume', 0)}` 手 · "
        f"买持仓 `{np_.get('total_long_volume', 0)}` 手"
    )

st.markdown("**按标的合约**")
if by_u:
    udf = pd.DataFrame(by_u)
    show = [
        "underlying",
        "product",
        "F_est",
        "dte",
        "y_short",
        "y_long",
        "t_short",
        "t_long",
        "call_short",
        "put_short",
        "call_long",
        "put_long",
        "short_volume",
        "long_volume",
        "net_volume",
        "net_delta",
        "net_vega",
        "net_theta",
        "margin",
        "risk_status",
    ]
    st.dataframe(udf[[c for c in show if c in udf.columns]], use_container_width=True, hide_index=True)
else:
    st.info("无标的汇总")

# ---- 希腊值明细 ----
st.subheader("希腊值汇总（BS76）")
st.caption("Δ/Γ 按手数合计；Vega/Theta 已乘合约乘数（权利金点值）。F 默认由宽跨式行权价中点估计，可手动覆盖。")

legs_g = gsum.get("by_leg") or []
if legs_g:
    gdf = pd.DataFrame(legs_g)
    gcols = [
        "symbol",
        "underlying",
        "option_type",
        "strike",
        "net_volume",
        "F",
        "iv",
        "dte",
        "unit_delta",
        "delta",
        "gamma",
        "vega",
        "theta",
        "mark",
    ]
    st.dataframe(gdf[[c for c in gcols if c in gdf.columns]], use_container_width=True, hide_index=True)

# optional F override
F_map = report.get("underlying_F") or {}
if by_u:
    st.markdown("**覆盖标的期货价 F**")
    f_edit = st.data_editor(
        pd.DataFrame(
            {
                "underlying": [u["underlying"] for u in by_u],
                "F": [float(F_map.get(u["underlying"], u.get("F_est") or 0)) for u in by_u],
            }
        ),
        hide_index=True,
        use_container_width=True,
        disabled=["underlying"],
        key="f_editor",
    )
    if st.button("保存 F 并重算希腊值"):
        payload = {str(r["underlying"]): float(r["F"]) for _, r in f_edit.iterrows()}
        try:
            post_json(
                "/api/v1/settlement/underlying-F",
                {"account_id": acct, "session_date": sess, "underlying_F": payload},
            )
            st.success("已更新标的 F")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

# ---- 合约盈亏明细 ----
st.subheader("按合约盈亏明细")
legs = report.get("by_leg") or []
if legs:
    df = pd.DataFrame(legs)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("更新期权标记价格")
    edit = st.data_editor(
        pd.DataFrame(
            {
                "symbol": [x["symbol"] for x in legs],
                "mark": [x["mark"] for x in legs],
                "ref_settle": [x["ref_settle"] for x in legs],
            }
        ),
        hide_index=True,
        use_container_width=True,
        disabled=["symbol", "ref_settle"],
        key="mark_editor",
    )
    if st.button("保存标记价格并重算"):
        marks = {str(r["symbol"]): float(r["mark"]) for _, r in edit.iterrows()}
        try:
            post_json(
                "/api/v1/settlement/marks/batch",
                {"account_id": acct, "session_date": sess, "marks": marks},
            )
            st.success("已更新标记价格")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
else:
    st.info("无持仓腿")

st.subheader("按标的盈亏")
by_u_pnl = report.get("by_underlying") or []
if by_u_pnl:
    st.dataframe(pd.DataFrame(by_u_pnl), use_container_width=True, hide_index=True)

st.caption(
    f"结算日 {report['settlement_date']} · 监控日 {report['session_date']} · "
    f"昨持仓 {report.get('yesterday_position_count')} · 今成交 {report.get('today_trade_count')}"
)
