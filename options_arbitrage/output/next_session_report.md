# 下一交易日候选 · Short Strangle · 数据截止 2026-08-20T09:52:38

> **非即时成交声明**：本报告供下一交易日开盘前研究，不代表收盘价可立即成交。入场前须重新核验实时报价与保证金。

## 元数据

- `report_version`: report-v2.0.0
- `methods_version`: methods-v2.0.0
- `rules_version`: margin_rules-v2.0.0
- `data_source`: akshare_sina_chain+futures_zh_daily
- `quote_asof`: 2026-08-20T09:52:38
- `target_session`: 2026-08-21
- `model`: Black-76 (American risk flagged)
- `account_equity`: 500000.0
- 扫描品种: 14 · 推荐: 1 · 观察: 13 · 排除: 0

## 1. 分类摘要

共 **1** 个品种进入推荐。

### 观察池

- **豆粕** m2611 — 权/保比(无优惠) 7.9% < 8%
- **菜油** OI2611 — 见明细
- **白糖** SR2611 — 权/保比(无优惠) 6.4% < 8%
- **棉花** CF2611 — 权/保比(无优惠) 6.0% < 8%
- **黄金** au2610 — IVR/IVP 未达标 (IVR=8.618625127602122, IVP=27.31958762886598); 单腿持仓量不足 Call=428.0 Put=389.0; 数据闸门未全部通过: iv_history_252
- **橡胶** ru2610 — IVR/IVP 未达标 (IVR=15.422980940304607, IVP=15.976331360946746); 单腿持仓量不足 Call=29.0 Put=46.0; 权/保比(无优惠) 3.8% < 8%; 数据闸门未全部通过: iv_history_252
- **玉米** c2611 — 权/保比(无优惠) 4.1% < 8%
- **花生** PK2610 — IVR/IVP 未达标 (IVR=47.961586315990864, IVP=52.601156069364166); 权/保比(无优惠) 2.6% < 8%; 数据闸门未全部通过: iv_history_252
- **沪铜** cu2610 — IVR/IVP 未达标 (IVR=9.341169544935966, IVP=5.617977528089887); 权/保比(无优惠) 4.6% < 8%; 数据闸门未全部通过: iv_history_252
- **铁矿石** i2610 — IVR/IVP 未达标 (IVR=56.66260260858977, IVP=67.14285714285714); 权/保比(无优惠) 5.0% < 8%
- **PTA** TA2610 — IVR/IVP 未达标 (IVR=16.444139830383833, IVP=50.66666666666667); VRP≤0 (-0.066); 权/保比(无优惠) 7.8% < 8%
- **甲醇** MA2610 — IVR/IVP 未达标 (IVR=14.389239269137741, IVP=65.33333333333333); VRP≤0 (-0.087)
- **LPG** pg2610 — IVR/IVP 未达标 (IVR=34.745864431057974, IVP=55.35714285714286); VRP≤0 (-0.116); 单腿持仓量不足 Call=274.0 Put=154.0

## 2. 候选明细

| 分类 | 品种 | 标的期货 | 月份/DTE | 卖C/K/买一 | 卖P/K/买一 | σ* | IVR | IVP | VRP | 技分 | 权/保(无优惠) | RN-POP | 建议手数 |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 推荐 | 菜粕 | RM2611 | rm2611/68d | 2450.0/14.5 | 2175.0/14.0 | 22.4% | 55.1 | 96.0 | 1.6% | 63 | 8.2% | 48.5% | 41 |
| 观察 | 豆粕 | m2611 | m2611/68d | 3500.0/25.0 | 3100.0/14.0 | 22.0% | 91.9 | 93.6 | 7.9% | 42 | 7.9% | 68.0% | N/A(缺权益) |
| 观察 | 菜油 | OI2611 | oi2611/68d | 11600.0/68.5 | 10000.0/71.5 | 23.9% | 78.8 | 99.3 | 7.8% | 39 | 9.5% | 70.4% | N/A(缺权益) |
| 观察 | 白糖 | SR2611 | sr2611/68d | 5500.0/32.0 | 5000.0/15.0 | 15.9% | 63.7 | 99.3 | 7.4% | 52 | 6.4% | 79.4% | N/A(缺权益) |
| 观察 | 棉花 | CF2611 | cf2611/68d | 17600.0/77.0 | 16000.0/65.0 | 18.0% | 67.2 | 99.7 | 7.2% | 55 | 6.0% | 69.1% | N/A(缺权益) |
| 观察 | 黄金 | au2610 | au2610/36d | 1056.0/7.3 | 912.0/5.92 | 25.2% | 8.6 | 27.3 | 6.7% | 42 | 13.1% | 78.1% | N/A(缺权益) |
| 观察 | 橡胶 | ru2610 | ru2610/36d | 18500.0/84.0 | 16000.0/31.0 | 20.3% | 15.4 | 16.0 | 5.1% | 54 | 3.8% | 86.0% | N/A(缺权益) |
| 观察 | 玉米 | c2611 | c2611/68d | 2360.0/7.0 | 2180.0/8.5 | 13.4% | 100.0 | 99.6 | 4.5% | 49 | 4.1% | 69.4% | N/A(缺权益) |
| 观察 | 花生 | PK2610 | pk2610/36d | 8400.0/18.5 | 8000.0/20.5 | 8.4% | 48.0 | 52.6 | 2.0% | 50 | 2.6% | 76.5% | N/A(缺权益) |
| 观察 | 沪铜 | cu2610 | cu2610/36d | 112000.0/480.0 | 102000.0/308.0 | 13.7% | 9.3 | 5.6 | 1.4% | 66 | 4.6% | 77.5% | N/A(缺权益) |
| 观察 | 铁矿石 | i2610 | i2610/36d | 750.0/3.6 | 690.0/3.9 | 16.1% | 56.7 | 67.1 | 0.2% | 66 | 5.0% | 59.1% | N/A(缺权益) |
| 观察 | PTA | TA2610 | ta2610/36d | 6300.0/38.5 | 5500.0/26.5 | 22.9% | 16.4 | 50.7 | -6.6% | 70 | 7.8% | 53.3% | N/A(缺权益) |
| 观察 | 甲醇 | MA2610 | ma2610/36d | 3100.0/19.0 | 2650.0/21.5 | 26.4% | 14.4 | 65.3 | -8.7% | 48 | 10.6% | 51.7% | N/A(缺权益) |
| 观察 | LPG | pg2610 | pg2610/36d | 6200.0/41.2 | 5250.0/39.8 | 29.0% | 34.7 | 55.4 | -11.6% | 53 | 8.4% | 48.1% | N/A(缺权益) |

### 菜粕（推荐）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `rm2611C2450` K=2450.0 bid/ask=14.5/18.5 Δ=0.180 IV=0.178 slippage=2.00
- 腿: `rm2611P2175` K=2175.0 bid/ask=14.0/16.5 Δ=-0.203 IV=0.133 slippage=1.25
- 保证金: 单腿C=1,557 P=1,907 · 无优惠=3,464 · 理论组合=2,052 (unclear) · 客户预计=3,637
- 概率: RN到期盈利=48.5%（**非真实胜率**）· Δ近似=61.7% · 历史60日越界=15.0%
- 盈亏平衡区间: [2,146.5, 2,478.5]
- 压力: {'margin_iv_up50_no_combo': 3983.5999999999995, 'margin_iv_up50_combo': 3276.0000000000005, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 3464.0}
- 分类理由: 全部硬闸门通过且评分达标
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 豆粕（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'user_csv', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `m2611C3500` K=3500.0 bid/ask=25.0/25.5 Δ=0.195 IV=0.171 slippage=0.25
- 腿: `m2611P3100` K=3100.0 bid/ask=14.0/14.5 Δ=-0.149 IV=0.127 slippage=0.25
- 保证金: 单腿C=2,412 P=2,532 · 无优惠=4,944 · 理论组合=2,782 (unclear) · 客户预计=5,191
- 概率: RN到期盈利=68.0%（**非真实胜率**）· Δ近似=65.6% · 历史60日越界=60.0%
- 盈亏平衡区间: [3,061.0, 3,539.0]
- 压力: {'margin_iv_up50_no_combo': 5685.599999999999, 'margin_iv_up50_combo': 4537.500000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 4944.0}
- 分类理由: 权/保比(无优惠) 7.9% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 菜油（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `oi2611C11600` K=11600.0 bid/ask=68.5/70.5 Δ=0.158 IV=0.189 slippage=1.00
- 腿: `oi2611P10000` K=10000.0 bid/ask=71.5/73.5 Δ=-0.174 IV=0.163 slippage=1.00
- 保证金: 单腿C=6,615 P=8,095 · 无优惠=14,710 · 理论组合=8,780 (unclear) · 客户预计=15,446
- 概率: RN到期盈利=70.4%（**非真实胜率**）· Δ近似=66.8% · 历史60日越界=65.0%
- 盈亏平衡区间: [9,860.0, 11,740.0]
- 压力: {'margin_iv_up50_no_combo': 16916.5, 'margin_iv_up50_combo': 14527.500000000002, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 14710.0}
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 白糖（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `sr2611C5500` K=5500.0 bid/ask=32.0/33.0 Δ=0.190 IV=0.142 slippage=0.50
- 腿: `sr2611P5000` K=5000.0 bid/ask=15.0/16.0 Δ=-0.147 IV=0.089 slippage=0.50
- 保证金: 单腿C=3,512 P=3,822 · 无优惠=7,334 · 理论组合=4,142 (unclear) · 客户预计=7,700
- 概率: RN到期盈利=79.4%（**非真实胜率**）· Δ近似=66.3% · 历史60日越界=0.0%
- 盈亏平衡区间: [4,953.0, 5,547.0]
- 压力: {'margin_iv_up50_no_combo': 8433.64, 'margin_iv_up50_combo': 6623.700000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 7333.6}
- 分类理由: 权/保比(无优惠) 6.4% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 棉花（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `cf2611C17600` K=17600.0 bid/ask=77.0/80.0 Δ=0.171 IV=0.122 slippage=1.50
- 腿: `cf2611P16000` K=16000.0 bid/ask=65.0/66.0 Δ=-0.160 IV=0.105 slippage=0.50
- 保证金: 单腿C=5,709 P=6,049 · 无优惠=11,758 · 理论组合=6,434 (unclear) · 客户预计=12,346
- 概率: RN到期盈利=69.1%（**非真实胜率**）· Δ近似=66.9% · 历史60日越界=15.0%
- 盈亏平衡区间: [15,858.0, 17,742.0]
- 压力: {'margin_iv_up50_no_combo': 13521.699999999999, 'margin_iv_up50_combo': 10409.0, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 11758.0}
- 分类理由: 权/保比(无优惠) 6.0% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 黄金（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'hv_scaled_proxy_not_for_ivr_gate', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `au2610C1056` K=1056.0 bid/ask=7.3/7.84 Δ=0.174 IV=0.277 slippage=0.27
- 腿: `au2610P912` K=912.0 bid/ask=5.92/6.06 Δ=-0.169 IV=0.211 slippage=0.07
- 保证金: 单腿C=46,085 P=54,680 · 无优惠=100,764 · 理论组合=61,980 (unclear) · 客户预计=105,803
- 概率: RN到期盈利=78.1%（**非真实胜率**）· Δ近似=65.7% · 历史60日越界=53.3%
- 盈亏平衡区间: [898.8, 1,069.2]
- 压力: {'margin_iv_up50_no_combo': 115879.06, 'margin_iv_up50_combo': 104730.4, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 100764.40000000001}
- 未过闸门: iv_history_252(固定期限 ATM IV 历史仅 194 日，需要 252 日 source=hv_scaled_proxy_not_for_ivr_gate)
- 分类理由: IVR/IVP 未达标 (IVR=8.618625127602122, IVP=27.31958762886598); 单腿持仓量不足 Call=428.0 Put=389.0; 数据闸门未全部通过: iv_history_252
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 橡胶（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'hv_scaled_proxy_not_for_ivr_gate', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `ru2610C18500` K=18500.0 bid/ask=84.0/126.0 Δ=0.180 IV=0.202 slippage=21.00
- 腿: `ru2610P16000` K=16000.0 bid/ask=31.0/110.0 Δ=-0.112 IV=0.230 slippage=39.50
- 保证金: 单腿C=16,344 P=14,114 · 无优惠=30,458 · 理论组合=16,654 (unclear) · 客户预计=31,981
- 概率: RN到期盈利=86.0%（**非真实胜率**）· Δ近似=70.8% · 历史60日越界=0.0%
- 盈亏平衡区间: [15,885.0, 18,615.0]
- 压力: {'margin_iv_up50_no_combo': 35026.7, 'margin_iv_up50_combo': 27451.0, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 30458.0}
- 未过闸门: iv_history_252(固定期限 ATM IV 历史仅 169 日，需要 252 日 source=hv_scaled_proxy_not_for_ivr_gate)
- 分类理由: IVR/IVP 未达标 (IVR=15.422980940304607, IVP=15.976331360946746); 单腿持仓量不足 Call=29.0 Put=46.0; 权/保比(无优惠) 3.8% < 8%; 数据闸门未全部通过: iv_history_252
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 玉米（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'user_csv', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `c2611C2360` K=2360.0 bid/ask=7.0/9.5 Δ=0.164 IV=0.099 slippage=1.25
- 腿: `c2611P2180` K=2180.0 bid/ask=8.5/9.0 Δ=-0.176 IV=0.093 slippage=0.25
- 保证金: 单腿C=1,836 P=1,941 · 无优惠=3,777 · 理论组合=2,011 (unclear) · 客户预计=3,966
- 概率: RN到期盈利=69.4%（**非真实胜率**）· Δ近似=66.1% · 历史60日越界=0.0%
- 盈亏平衡区间: [2,164.5, 2,375.5]
- 压力: {'margin_iv_up50_no_combo': 4343.549999999999, 'margin_iv_up50_combo': 3188.0000000000005, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 3777.0}
- 分类理由: 权/保比(无优惠) 4.1% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 花生（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'hv_scaled_proxy_not_for_ivr_gate', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `pk2610C8400` K=8400.0 bid/ask=18.5/20.0 Δ=0.161 IV=0.090 slippage=0.75
- 腿: `pk2610P8000` K=8000.0 bid/ask=20.5/22.0 Δ=-0.193 IV=0.077 slippage=0.75
- 保证金: 单腿C=3,590 P=3,770 · 无优惠=7,361 · 理论组合=3,863 (unclear) · 客户预计=7,729
- 概率: RN到期盈利=76.5%（**非真实胜率**）· Δ近似=64.6% · 历史60日越界=15.0%
- 盈亏平衡区间: [7,961.0, 8,439.0]
- 压力: {'margin_iv_up50_no_combo': 8465.15, 'margin_iv_up50_combo': 5963.000000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 7361.0}
- 未过闸门: iv_history_252(固定期限 ATM IV 历史仅 173 日，需要 252 日 source=hv_scaled_proxy_not_for_ivr_gate)
- 分类理由: IVR/IVP 未达标 (IVR=47.961586315990864, IVP=52.601156069364166); 权/保比(无优惠) 2.6% < 8%; 数据闸门未全部通过: iv_history_252
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 沪铜（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'hv_scaled_proxy_not_for_ivr_gate', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `cu2610C112000` K=112000.0 bid/ask=480.0/540.0 Δ=0.180 IV=0.159 slippage=30.00
- 腿: `cu2610P102000` K=102000.0 bid/ask=308.0/314.0 Δ=-0.133 IV=0.136 slippage=3.00
- 保证金: 单腿C=42,980 P=42,820 · 无优惠=85,800 · 理论组合=45,380 (unclear) · 客户预计=90,090
- 概率: RN到期盈利=77.5%（**非真实胜率**）· Δ近似=68.7% · 历史60日越界=6.7%
- 盈亏平衡区间: [101,212.0, 112,788.0]
- 压力: {'margin_iv_up50_no_combo': 98669.99999999999, 'margin_iv_up50_combo': 73535.00000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 85800.0}
- 未过闸门: iv_history_252(固定期限 ATM IV 历史仅 178 日，需要 252 日 source=hv_scaled_proxy_not_for_ivr_gate)
- 分类理由: IVR/IVP 未达标 (IVR=9.341169544935966, IVP=5.617977528089887); 权/保比(无优惠) 4.6% < 8%; 数据闸门未全部通过: iv_history_252
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 铁矿石（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'user_csv', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `i2610C750` K=750.0 bid/ask=3.6/3.7 Δ=0.186 IV=0.163 slippage=0.05
- 腿: `i2610P690` K=690.0 bid/ask=3.9/4.1 Δ=-0.209 IV=0.147 slippage=0.10
- 保证金: 单腿C=7,221 P=7,701 · 无优惠=14,922 · 理论组合=8,061 (unclear) · 客户预计=15,668
- 概率: RN到期盈利=59.1%（**非真实胜率**）· Δ近似=60.5% · 历史60日越界=31.7%
- 盈亏平衡区间: [682.5, 757.5]
- 压力: {'margin_iv_up50_no_combo': 17160.3, 'margin_iv_up50_combo': 12579.0, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 14922.0}
- 分类理由: IVR/IVP 未达标 (IVR=56.66260260858977, IVP=67.14285714285714); 权/保比(无优惠) 5.0% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### PTA（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `ta2610C6300` K=6300.0 bid/ask=38.5/40.0 Δ=0.171 IV=0.241 slippage=0.75
- 腿: `ta2610P5500` K=5500.0 bid/ask=26.5/27.0 Δ=-0.145 IV=0.190 slippage=0.25
- 保证金: 单腿C=1,986 P=2,186 · 无优惠=4,173 · 理论组合=2,379 (unclear) · 客户预计=4,382
- 概率: RN到期盈利=53.3%（**非真实胜率**）· Δ近似=68.4% · 历史60日越界=16.7%
- 盈亏平衡区间: [5,435.0, 6,365.0]
- 压力: {'margin_iv_up50_no_combo': 4798.95, 'margin_iv_up50_combo': 3938.500000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 4173.0}
- 分类理由: IVR/IVP 未达标 (IVR=16.444139830383833, IVP=50.66666666666667); VRP≤0 (-0.066); 权/保比(无优惠) 7.8% < 8%
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### 甲醇（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'exchange_czce_atm', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `ma2610C3100` K=3100.0 bid/ask=19.0/20.5 Δ=0.158 IV=0.279 slippage=0.75
- 腿: `ma2610P2650` K=2650.0 bid/ask=21.5/22.0 Δ=-0.182 IV=0.239 slippage=0.25
- 保证金: 单腿C=1,664 P=2,149 · 无优惠=3,813 · 理论组合=2,339 (unclear) · 客户预计=4,004
- 概率: RN到期盈利=51.7%（**非真实胜率**）· Δ近似=66.0% · 历史60日越界=60.0%
- 盈亏平衡区间: [2,609.5, 3,140.5]
- 压力: {'margin_iv_up50_no_combo': 4384.95, 'margin_iv_up50_combo': 3875.000000000001, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 3813.0}
- 分类理由: IVR/IVP 未达标 (IVR=14.389239269137741, IVP=65.33333333333333); VRP≤0 (-0.087)
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

### LPG（观察）

- 数据追溯: `{'methods_version': 'methods-v2.0.0', 'iv_history_source': 'user_csv', 'data_source': 'akshare_sina_chain+futures_zh_daily', 'american_risk_flag': True, 'premium_uses_bid': True}`
- 行情日: 2026-08-20 · 目标会话: 2026-08-21
- 腿: `pg2610C6200` K=6200.0 bid/ask=41.2/59.4 Δ=0.179 IV=0.304 slippage=9.10
- 腿: `pg2610P5250` K=5250.0 bid/ask=39.8/52.2 Δ=-0.175 IV=0.265 slippage=6.20
- 保证金: 单腿C=8,934 P=10,326 · 无优惠=19,259 · 理论组合=11,150 (unclear) · 客户预计=20,222
- 概率: RN到期盈利=48.1%（**非真实胜率**）· Δ近似=64.6% · 历史60日越界=40.0%
- 盈亏平衡区间: [5,169.0, 6,281.0]
- 压力: {'margin_iv_up50_no_combo': 22148.079999999998, 'margin_iv_up50_combo': 18420.399999999998, 'underlying_up3pct_note': '需入场前重算', 'underlying_down3pct_note': '需入场前重算', 'combo_fail_use_no_combo': 19259.2}
- 分类理由: IVR/IVP 未达标 (IVR=34.745864431057974, IVP=55.35714285714286); VRP≤0 (-0.116); 单腿持仓量不足 Call=274.0 Put=154.0
- 事件: 2026-08-21 美国初请失业金 / 周度宏观 [MEDIUM]; 2026-08-22 美国制造业/服务业 PMI 初值窗口 [MEDIUM]

## 4. 风险提示

1. 卖出宽跨收益限于净权利金；尾部损失可能极大。
2. 负 Gamma + 负 Vega：趋势行情与 IV 上升将同时恶化损益与保证金。
3. 涨跌停/跳空、组合优惠失效、产业链共振需在实盘中单独压力测试。
4. IVR/IVP 若基于 HV 缩放代理序列，**不得**作为推荐依据（闸门已标注）。
5. 禁止自动下单；下一交易日开盘前重新核验。
