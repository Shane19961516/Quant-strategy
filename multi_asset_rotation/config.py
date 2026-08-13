"""策略配置：股债金/港股/美股ETF周度轮动。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# 标的池与角色
UNIVERSE = {
    "159816": {"name": "地方债0-4Y", "role": "safe", "market": "sz"},
    "513400": {"name": "道琼斯", "role": "us", "market": "sh"},
    "513110": {"name": "纳斯达克100", "role": "us", "market": "sh"},
    "513500": {"name": "标普500", "role": "us", "market": "sh"},
    "VIG": {"name": "美股红利增长", "role": "us_core", "market": "us", "ak_symbol": "VIG"},
    "159934": {"name": "黄金", "role": "gold", "market": "sz"},
    "515450": {"name": "红利低波", "role": "cn", "market": "sh"},
    # 港股通标的：汇丰控股（行情代码 00005，本地键名 HK0005）
    "HK0005": {"name": "汇丰控股", "role": "hk", "market": "hk", "ak_symbol": "00005"},
}

CODES = list(UNIVERSE.keys())
SAFE = "159816"
GOLD = "159934"
CN = "515450"
HK = "HK0005"
US_CANDIDATES = ["513500", "513110", "513400", "VIG"]
VIG = "VIG"
# 非美股风险资产（直接进入风险池）
CORE_RISK = [GOLD, CN, HK]

# 回测/拉数区间（结束日默认滚动到今天，避免写死后无法获取新行情）
START_DATE = "20200101"
END_DATE = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
# 地方债ETF上市后开始正式轮动（此前无安全垫）
STRATEGY_START = "2020-09-04"

# 冻结参数：条件杠杆 + 周度断路器 + 日度债券止损
# 验证指标（2020-09-04 → 2026-08-07）：
#   年化 ~25.05%，Sharpe(rf0) ~2.417，MDD ~-6.93%
PARAMS = {
    "mom_lb": 8,
    "abs_lb": 4,
    "vol_lb": 20,
    "sma_lb": 35,
    "abs_margin": 0.0,
    "require_abs_pos": True,
    "sma_buffer": 0.01,
    "sma_slope_lb": 0,
    "vol_target": 0.14,
    "top_k": 3,
    "max_single": 0.50,
    "canary_k": 4,
    "rebalance_thresh": 0.10,
    "cost_bps": 2.0,
    # 条件杠杆
    "max_gross": 1.5,
    "boost_mom": 0.03,
    "boost_min_n": 1,
    "upside_vol_boost": 1.0,
    "lev_dd_cap": 0.03,
    # 周度组合断路器（策略内 sim NAV）
    "dd_stop": 0.06,
    "dd_resume": 0.02,
    "mom_strength": 0.0,
    "exposure_floor": 0.0,
    "weak_scale": 1.0,
    "borrow_rate": 0.02,
    # 日度回撤保护：触发后切债券，下次再平衡恢复；高波动时收紧阈值
    "daily_dd_stop": 0.05,
    "daily_dd_resume": 0.02,
    "stop_only_levered": True,
    "stop_vol_mult": 1.5,
    "dd_action": "bonds",
    "resume_on_rebalance": True,
}

# 基准：等权可用资产 / 纯债券
RF_ANNUAL = 0.02
