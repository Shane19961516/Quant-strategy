"""Page: 实时盈亏监控。"""

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
st.caption("昨日持仓盯市盈亏 + 当日成交盈亏 − 手续费。可手动刷新标记价格。")

sess = session_date()
acct = account_id()
if not sess:
    st.warning("请设置监控交易日")
    st.stop()

if st.button("刷新盈亏", type="primary"):
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

r1, r2, r3 = st.columns(3)
r1.metric("结算保证金占用", f"{report['margin_occupied_settlement']:,.2f}")
r2.metric("结算可用", f"{report['available_settlement']:,.2f}")
r3.metric("结算风险度", f"{report['risk_degree_settlement']:.2f}%")

if report.get("alerts"):
    for a in report["alerts"]:
        st.warning(a)

st.subheader("按合约明细")
legs = report.get("by_leg") or []
if legs:
    df = pd.DataFrame(legs)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("更新标记价格")
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

st.subheader("按标的汇总")
by_u = report.get("by_underlying") or []
if by_u:
    st.dataframe(pd.DataFrame(by_u), use_container_width=True, hide_index=True)

st.caption(
    f"结算日 {report['settlement_date']} · 监控日 {report['session_date']} · "
    f"昨持仓 {report.get('yesterday_position_count')} · 今成交 {report.get('today_trade_count')}"
)
