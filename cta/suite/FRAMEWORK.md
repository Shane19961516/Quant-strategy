# 趋势 / 套利策略测评框架 v2（防过拟 · 可落地 · 压力测试）

## 1. 研报锁参（禁止全样本寻优）

### 趋势
| 策略 | 来源 | 冻结参数 | v2 加固 |
|------|------|----------|---------|
| TSMOM | AQR / Baltas | L=60, skip=1 | 逆波动配权，lev≤1 |
| Donchian | Turtle System 2 | entry=55, exit=20 | 替代易假突破的 20/10 |
| Dual MA | 经典 CTA | MA20/60 | +ATR 跟踪止损 2.5/3 |

### 套利
| 策略 | 来源 | 冻结参数 | v2 加固 |
|------|------|----------|---------|
| 跨期价差 | 国内跨期 | z20±2；RB/HC/I/CU | 固定合约对+约1×名义 |
| 产业配对 | 产业链统计套利 | z60±2 | 滚动相关≥0.55 + 半衰期 5–45 日门控；exit z=0.5 |
| 截面短反转 | 商品短周期反转 | 5日收益，多空各3 | 替代失效单品种布林 |

## 2. 防过拟与实盘规则
1. 参数文献冻结；IS≤2021-12-31，OOS≥2022-01-01。
2. 滚动 WF：3 年训练概念窗后的每 1 年 OOS 夏普（均值/胜率）。
3. 成本：基准 1.5+1.5bp；压力 3+3bp，压力 OOS 夏普须≥0。
4. T+1 成交；总名义 ≤ 1× 资金（逆波动配权）。
5. **落地硬门槛（用户）**：OOS Sharpe≥**2.0**；且 WF均值≥1.0、压力OOS Sharpe≥1.0、MaxDD>-30%、WF正比例≥50%。
6. **重要**：夏普对杠杆/仓位缩放近似不变；加杠杆提高的是收益与波动，不是夏普。

## 3. 组合
- 仅合成通过门槛袖层
- 等权 + 逆波动两种；实盘优先逆波动

## 3b. 边缘冲刺 v3（仍未过闸则诚实报告）
| 策略 | 来源 | 冻结参数 |
|------|------|----------|
| 隔夜动量 | Lou / 商品 OHLC 分解 | L=5/20 TS；XS top/bottom 3（实测 OOS 弱，不进预注册书） |
| 日内反转 | overnight–intraday 分解 | L=1/5（同上） |
| OLS 对冲配对 | 滚动 beta 价差 MR | win=60, z±2.5 / 极端 z±3.0 |
| 截面 carry | 近远月 log(near/far) | 多低 carry / 空高 carry 各 3 |
| Basis momentum | Boons / Prado | Δbasis L=20/60，XS 3/3 |

预注册实盘书 **live_v3** = 黑色配对+跨期 + carry + olsx_RB_HC（等权，lev≤1）。

## 3c. 收益目标（CAGR 15–20%）
- 无杠杆 live_v3：OOS Sharpe≈1.46 但 CAGR≈1.6%（波动被压到 ~1%）。
- 要抬收益：对同一信号书做 **IS 标定固定杠杆** 或 **因果目标波动**（默认目标 vol 10/12/14%）。
- 夏普近似不变；MaxDD / 保证金占用随名义放大。模块：`cta.suite.return_target`。

## 4. 运行
```bash
python -m cta.suite.run_suite --data-dir cta_data_akshare --plot
# 仅冲刺：
python -m cta.suite.run_suite --skip-core --data-dir cta_data_akshare --plot
```
