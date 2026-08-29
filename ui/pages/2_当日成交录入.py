"""Page: 当日成交手动录入。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.common import account_id, api_base, get_json, inject_sidebar, post_json, session_date

st.set_page_config(page_title="当日成交录入", layout="wide")
inject_sidebar()

st.title("② 当日成交录入")
st.caption("与昨日持仓分表存储。开仓/平仓请正确选择；标的合约支持 AP610C8200 或 EG2610-C-5200 格式。")

sess = session_date()
if not sess:
    st.warning("请先在侧边栏填写监控交易日，或先导入结算单。")
    st.stop()

st.markdown(f"**账户** `{account_id()}` · **交易日** `{sess}`")

with st.form("trade_form", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns(4)
    symbol = c1.text_input("合约代码", placeholder="EG2610-C-5200")
    side = c2.selectbox("买/卖", ["卖", "买"])
    offset = c3.selectbox("开/平", ["开", "平"])
    volume = c4.number_input("手数", min_value=1, value=1, step=1)
    c5, c6, c7, c8 = st.columns(4)
    price = c5.number_input("权利金单价", min_value=0.0, value=0.0, step=0.5)
    fee = c6.number_input("手续费", min_value=0.0, value=0.0, step=0.01)
    trade_time = c7.text_input("成交时间", placeholder="09:35:45")
    note = c8.text_input("备注", placeholder="")
    submitted = st.form_submit_button("录入成交", type="primary")

if submitted:
    if not symbol or price <= 0:
        st.error("请填写合约与有效价格")
    else:
        try:
            resp = post_json(
                "/api/v1/settlement/today-trades",
                {
                    "account_id": account_id(),
                    "session_date": sess,
                    "symbol": symbol.strip(),
                    "side": side,
                    "offset": offset,
                    "price": float(price),
                    "volume": int(volume),
                    "fee": float(fee),
                    "trade_time": trade_time,
                    "note": note,
                },
            )
            st.success(
                f"已录入 {resp['side']}/{resp['offset']} {resp['symbol']} "
                f"x{resp['volume']} @ {resp['price']} · 权利金现金流 {resp['premium_cash']:.2f}"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"录入失败: {exc}")

st.subheader("今日已录入成交")
try:
    data = get_json(
        "/api/v1/settlement/today-trades",
        {"account_id": account_id(), "session_date": sess},
    )
    trades = data.get("trades", [])
    if trades:
        df = pd.DataFrame(trades)
        st.dataframe(df, use_container_width=True, hide_index=True)
        del_id = st.number_input("删除记录 ID", min_value=0, value=0, step=1)
        if st.button("删除选中成交") and del_id > 0:
            r = requests.delete(
                f"{api_base()}/api/v1/settlement/today-trades/{int(del_id)}",
                params={"account_id": account_id()},
                timeout=15,
            )
            if r.ok:
                st.success(f"已删除 {del_id}")
                st.rerun()
            else:
                st.error(r.text)
        st.metric("今日权利金净现金流", f"{sum(t['premium_cash'] for t in trades):,.2f}")
    else:
        st.info("尚无当日成交")
except Exception as exc:  # noqa: BLE001
    st.error(f"无法读取当日成交（API: {api_base()}）: {exc}")

st.divider()
st.subheader("对照：昨日持仓（只读）")
try:
    y = get_json("/api/v1/settlement/yesterday-positions", {"account_id": account_id()})
    st.caption(f"结算日 {y.get('settlement_date')} · {y.get('count')} 条")
    if y.get("positions"):
        st.dataframe(pd.DataFrame(y["positions"]), use_container_width=True, hide_index=True)
except Exception:
    st.caption("无昨日持仓")
