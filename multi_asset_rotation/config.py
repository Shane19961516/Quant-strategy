"""策略配置：股债金/美股ETF周度轮动（最终版：非对称调解 + YTD油门）。"""

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

# 最终版固化参数：
# - 进攻期中枢混合 + 偏离裁剪
# - 防守期跳过中枢、债券地板
# - YTD 油门压平牛市年份、弱年略增进攻
PARAMS = {
    # ---- 信号层 ----
    "mom_lb": 8,
    "abs_lb": 4,
    "vol_lb": 20,
    "sma_lb": 40,
    "canary_k": 3,
    "top_k": 2,
    # ---- 权重层 ----
    "vol_budget": 0.078,
    "neutral_sleeve": {
        "bond": 0.30,
        "gold": 0.23,
        "cn": 0.14,
        "us": 0.33,
    },
    "active_tilt": 0.90,
    "max_sleeve_dev": 0.40,
    "weight_ema": 1.00,
    "bond_canary_boost": 0.60,
    "canary_bond_floor": 0.95,
    "defense_skip_center": True,
    "min_bond": 0.07,
    "max_single_asset": 0.55,
    "rebalance_thresh": 0.25,
    "cost_bps": 2.0,
    # ---- YTD 油门 ----
    "use_ytd_throttle": True,
    "ytd_soft_cap": 0.08,
    "ytd_soft_floor": 0.02,
    "ytd_span": 0.10,
    "ytd_dampen": 0.60,
    "ytd_boost": 0.06,
    "ytd_extra_bond": 0.08,
}

RF_ANNUAL = 0.02
