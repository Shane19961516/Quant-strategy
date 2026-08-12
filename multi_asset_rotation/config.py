"""策略配置：股债金/港股/美股ETF周度轮动。"""

from __future__ import annotations

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

# 回测区间
START_DATE = "20200101"
END_DATE = "20260808"
# 地方债ETF上市后开始正式轮动（此前无安全垫）
STRATEGY_START = "2020-09-04"

# 默认参数（加入 HK0005 后重校准；并微调绝对动量/趋势门槛）
# 说明：港股波动更高，MDD 可能略高于原 7% 阈值；优先保证年化/夏普与全年非负
# 搜索结论：仅调 abs_lb/sma 窗口很难同时“提收益+降回撤”；
# 抬高趋势过滤缓冲（sma_buffer）可在 MDD 基本不变下提升年化与 Sharpe。
PARAMS = {
    "mom_lb": 8,          # 相对动量周数
    "abs_lb": 4,          # 绝对动量周数
    "vol_lb": 20,         # 波动率估计交易日
    "sma_lb": 35,         # 趋势过滤均线
    "abs_margin": 0.0,    # 绝对动量相对债券的超额门槛
    "require_abs_pos": False,  # 是否额外要求绝对动量 > 0
    "sma_buffer": 0.005,  # 价格需高于均线的比例缓冲（0.5%）
    "sma_slope_lb": 0,    # >0 时要求均线近 N 日上行（当前关闭）
    "vol_target": 0.09,   # 组合波动目标
    "top_k": 3,           # 风险资产最多持有数
    "max_single": 0.35,   # 单一风险资产上限
    "canary_k": 4,        # 风险池短线走弱>=4 才全进债
    "rebalance_thresh": 0.25,  # 换手阈值（迟滞），降低无效调仓
    "cost_bps": 2.0,      # 单边交易成本（bp）
}

# 基准：等权可用资产 / 纯债券
RF_ANNUAL = 0.02
