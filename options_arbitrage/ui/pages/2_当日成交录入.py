"""Page: 当日成交手动录入（期权 + 期货对冲）。"""

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
st.caption("期权与期货分表存储；期货对冲计入风控台「对冲盈亏」。")

sess = session_date()
acct = account_id()
if not sess:
    st.warning("请先在侧边栏填写监控交易日，或先导入结算单。")
    st.stop()

st.markdown(f"**账户** `{acct}` · **交易日** `{sess}`")

tab_opt, tab_fut = st.tabs(["期权成交", "期货对冲成交"])

with tab_opt:
    with st.form("trade_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        symbol = c1.text_input("合约代码", placeholder="EG2610-C-5200")
        side = c2.selectbox("买/卖", ["卖", "买"], key="opt_side")
        offset = c3.selectbox("开/平", ["开", "平"])
        volume = c4.number_input("手数", min_value=1, value=1, step=1, key="opt_vol")
        c5, c6, c7, c8 = st.columns(4)
        price = c5.number_input("权利金单价", min_value=0.0, value=0.0, step=0.5)
        fee = c6.number_input("手续费", min_value=0.0, value=0.0, step=0.01, key="opt_fee")
        trade_time = c7.text_input("成交时间", placeholder="09:35:45")
        note = c8.text_input("备注", placeholder="", key="opt_note")
        submitted = st.form_submit_button("录入期权成交", type="primary")

    if submitted:
        if not symbol or price <= 0:
            st.error("请填写合约与有效价格")
        else:
            try:
                resp = post_json(
                    "/api/v1/settlement/today-trades",
                    {
                        "account_id": acct,
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
                    f"x{resp['volume']} @ {resp['price']}"
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"录入失败: {exc}")

    st.subheader("今日期权成交")
    try:
        data = get_json(
            "/api/v1/settlement/today-trades",
            {"account_id": acct, "session_date": sess},
        )
        trades = data.get("trades", [])
        if trades:
            st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
            st.metric("今日权利金净现金流", f"{sum(t['premium_cash'] for t in trades):,.2f}")
            del_id = st.number_input("删除期权记录 ID", min_value=0, value=0, step=1, key="del_opt")
            if st.button("删除选中期权成交") and del_id > 0:
                r = requests.delete(
                    f"{api_base()}/api/v1/settlement/today-trades/{int(del_id)}",
                    params={"account_id": acct},
                    timeout=15,
                )
                if r.ok:
                    st.success(f"已删除 {del_id}")
                    st.rerun()
                else:
                    st.error(r.text)
        else:
            st.info("尚无期权成交")
    except Exception as exc:  # noqa: BLE001
        st.error(f"无法读取期权成交: {exc}")

with tab_fut:
    st.caption("期货示例：JD2610 / V2610。需填写成交价与当前最新价，用于对冲盈亏。")
    with st.form("fut_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        f_symbol = c1.text_input("期货合约", placeholder="JD2610")
        f_side = c2.selectbox("买/卖", ["卖", "买"], key="fut_side")
        f_vol = c3.number_input("手数", min_value=1, value=1, step=1, key="fut_vol")
        c4, c5, c6 = st.columns(3)
        f_price = c4.number_input("成交价", min_value=0.0, value=0.0, step=1.0)
        f_last = c5.number_input("当前最新价", min_value=0.0, value=0.0, step=1.0)
        f_fee = c6.number_input("手续费", min_value=0.0, value=0.0, step=0.01, key="fut_fee")
        f_note = st.text_input("备注", key="fut_note")
        f_sub = st.form_submit_button("录入期货成交", type="primary")

    if f_sub:
        if not f_symbol or f_price <= 0 or f_last <= 0:
            st.error("请填写合约、成交价、最新价")
        else:
            try:
                resp = post_json(
                    "/api/v1/settlement/futures-trades",
                    {
                        "account_id": acct,
                        "session_date": sess,
                        "symbol": f_symbol.strip(),
                        "side": f_side,
                        "volume": int(f_vol),
                        "price": float(f_price),
                        "last": float(f_last),
                        "fee": float(f_fee),
                        "note": f_note,
                    },
                )
                st.success(
                    f"已录入期货 {resp['side']} {resp['symbol']} x{resp['volume']} "
                    f"@{resp['price']} 最新{resp['last']}"
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"录入失败: {exc}")

    st.subheader("今日期货对冲成交")
    try:
        fdata = get_json(
            "/api/v1/settlement/futures-trades",
            {"account_id": acct, "session_date": sess},
        )
        ftrades = fdata.get("trades", [])
        if ftrades:
            fdf = pd.DataFrame(ftrades)
            # compute pnl preview
            pnls = []
            for t in ftrades:
                mult = float(t.get("multiplier") or 10)
                if t["side"] == "BUY":
                    pnl = (t["last"] - t["price"]) * t["volume"] * mult - float(t.get("fee") or 0)
                else:
                    pnl = (t["price"] - t["last"]) * t["volume"] * mult - float(t.get("fee") or 0)
                pnls.append(pnl)
            fdf["浮动盈亏"] = [round(x, 2) for x in pnls]
            st.dataframe(fdf, use_container_width=True, hide_index=True)
            st.metric("期货对冲盈亏合计", f"{sum(pnls):,.2f}")
        else:
            st.warning("尚无期货成交 — 若你表里有 JD/V 期货，请在此补录")
            if st.button("一键补录表内 4 笔期货（JD/V）"):
                batch = [
                    ("JD2610", "卖", 1, 3908, 3905),
                    ("JD2610", "买", 1, 3939, 3905),
                    ("V2610", "卖", 1, 4515, 4551),
                    ("V2610", "买", 1, 4554, 4551),
                ]
                ok = 0
                for sym, side, vol, px, last in batch:
                    try:
                        post_json(
                            "/api/v1/settlement/futures-trades",
                            {
                                "account_id": acct,
                                "session_date": sess,
                                "symbol": sym,
                                "side": side,
                                "volume": vol,
                                "price": px,
                                "last": last,
                            },
                        )
                        ok += 1
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
                st.success(f"已补录 {ok} 笔期货")
                st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"无法读取期货成交: {exc}")

st.divider()
st.subheader("对照：昨日持仓（只读）")
try:
    y = get_json("/api/v1/settlement/yesterday-positions", {"account_id": acct})
    st.caption(f"结算日 {y.get('settlement_date')} · {y.get('count')} 条")
    if y.get("positions"):
        st.dataframe(pd.DataFrame(y["positions"]), use_container_width=True, hide_index=True)
except Exception:
    st.caption("无昨日持仓")
