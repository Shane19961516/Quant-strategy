"""宽跨式波动率套利 · 结算与实时盈亏监控 Web 首页。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.common import inject_sidebar

st.set_page_config(
    page_title="波动率套利结算监控",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Serif:wght@600&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans', 'Source Han Sans SC', sans-serif; }
    h1, h2 { font-family: 'IBM Plex Serif', serif !important; }
    .hero {
      background: linear-gradient(135deg, #1a3a4a 0%, #2c5f6e 45%, #c4a35a 140%);
      color: #f7f3ea; padding: 1.6rem 1.8rem; border-radius: 6px; margin-bottom: 1rem;
    }
    .hero h1 { color: #f7f3ea !important; margin: 0 0 0.4rem 0; font-size: 1.8rem; }
    .hero p { margin: 0; opacity: 0.9; }
    .card {
      background: linear-gradient(160deg, #f4efe6, #e8e0d2);
      border-left: 3px solid #1a3a4a; padding: 0.9rem 1rem; border-radius: 4px; margin-bottom: 0.6rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

inject_sidebar()

st.markdown(
    """
    <div class="hero">
      <h1>宽跨式 · 结算与波动率套利监控台</h1>
      <p>昨日结算单导入 ｜ 当日成交手录 ｜ 实时盈亏盯市 ｜ BS76 筛选与组合风控</p>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown('<div class="card"><b>① 结算单导入</b><br/>上传东亚期货客户交易结算日报，解析期权持仓汇总为「昨日持仓」。</div>', unsafe_allow_html=True)
with c2:
    st.markdown('<div class="card"><b>② 当日成交</b><br/>手动录入今日开平仓；与昨日持仓分表存储，互不覆盖。</div>', unsafe_allow_html=True)
with c3:
    st.markdown('<div class="card"><b>③ 实时盈亏</b><br/>持仓盯市 + 今日成交盈亏 − 手续费；按品种汇总与风险提示。</div>', unsafe_allow_html=True)
with c4:
    st.markdown('<div class="card"><b>④ 筛选大盘</b><br/>高 IV 溢价宽跨式筛选、POP/ROI、双轴 IV-HV 图表。</div>', unsafe_allow_html=True)

st.info("请从左侧页面切换：**结算单导入 / 当日成交录入 / 实时盈亏监控 / 筛选大盘**。先启动 API：`uvicorn api.main:app --port 8000`")

st.markdown("### 工作流")
st.code(
    "昨日结算单.xls  →  昨日持仓基线\n"
    "当日成交（手录） →  与持仓分离存储\n"
    "标记价格/结算价  →  实时盈亏 & 权益估算\n"
    "Screener         →  新开仓候选（保证金占用>60% 拒绝）",
    language="text",
)
