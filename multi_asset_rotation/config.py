"""策略配置：股债金/美股ETF周度轮动（最终版：非对称权重调解）。"""

from __future__ import annotations

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

SLEEVES = ["bond", "gold", "cn", "us"]
SLEEVE_ASSETS = {
    "bond": [SAFE],
    "gold": [GOLD],
    "cn": [CN],
    "us": US_CANDIDATES,
}

START_DATE = "20200101"
END_DATE = "20260808"
STRATEGY_START = "2020-09-04"

# 最终版参数（网格搜索固化）：
# 进攻期中枢混合抑制过度集中；防守期跳过中枢、债券地板保护
# 目标：Sharpe>=2 / 年化>=15% / MDD<=7%，并降低分年收益离散度
PARAMS = {
    # ---- 信号层 ----
    "mom_lb": 8,
    "abs_lb": 4,
    "vol_lb": 20,
    "sma_lb": 40,
    "canary_k": 3,
    "top_k": 2,
    # ---- 权重层（非对称调解）----
    "vol_budget": 0.0715,       # 战术风险预算（逆波动缩放）
    "neutral_sleeve": {         # 战略中枢
        "bond": 0.30,
        "gold": 0.23,
        "cn": 0.14,
        "us": 0.33,
    },
    "active_tilt": 0.91,        # 进攻期战术占比
    "max_sleeve_dev": 0.53,     # 进攻期相对中枢最大偏离
    "weight_ema": 1.00,         # 进攻期权重平滑（1=不拖尾）；防守期强制即时落地
    "bond_canary_boost": 0.60,  # 金丝雀额外拨债
    "canary_bond_floor": 0.90,  # 金丝雀债券下限
    "defense_skip_center": True,
    "min_bond": 0.07,           # 仅进攻期生效
    "max_single_asset": 0.55,
    "rebalance_thresh": 0.20,
    "cost_bps": 2.0,
}

RF_ANNUAL = 0.02
