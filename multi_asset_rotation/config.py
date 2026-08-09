"""策略配置：股债金/美股ETF周度轮动。"""

from __future__ import annotations

# 标的池与角色
UNIVERSE = {
    "159816": {"name": "地方债0-4Y", "role": "safe", "market": "sz"},
    "513400": {"name": "道琼斯", "role": "us", "market": "sh"},
    "513110": {"name": "纳斯达克100", "role": "us", "market": "sh"},
    "513500": {"name": "标普500", "role": "us", "market": "sh"},
    "159934": {"name": "黄金", "role": "gold", "market": "sz"},
    "515450": {"name": "红利低波", "role": "cn", "market": "sh"},
}

CODES = list(UNIVERSE.keys())
SAFE = "159816"
GOLD = "159934"
CN = "515450"
US_CANDIDATES = ["513500", "513110", "513400"]

# 回测区间
START_DATE = "20200101"
END_DATE = "20260808"
# 地方债ETF上市后开始正式轮动（此前无安全垫）
STRATEGY_START = "2020-09-04"

# 默认参数（已校准：Sharpe>=2, 年化>=15%, MDD<=7%）
PARAMS = {
    "mom_lb": 8,          # 相对动量周数
    "abs_lb": 4,          # 绝对动量周数
    "vol_lb": 20,         # 波动率估计交易日
    "sma_lb": 40,         # 趋势过滤均线
    "vol_target": 0.075,  # 组合波动目标
    "top_k": 2,           # 风险资产最多持有数
    "max_single": 0.55,   # 单一风险资产上限
    "canary_k": 3,        # 短周期走弱资产数>=该值则全进债
    "rebalance_thresh": 0.25,  # 换手阈值（迟滞），降低无效调仓
    "cost_bps": 2.0,      # 单边交易成本（bp）
}

# 基准：等权可用资产 / 纯债券
RF_ANNUAL = 0.02
