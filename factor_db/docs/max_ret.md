# 因子文档：`max_ret`

## 概要
- **名称**: max_ret
- **中文**: MAX因子(取负)
- **类别**: lottery
- **状态**: 已入库 (admitted)
- **使用方向 direction**: +1（调用 `load_panel` / `get_factor_on` 默认已校正，高分为宜做多）
- **公式**: -max(r)_{12m}，滞后1期（MAX 因子取负）
- **处理**: 滞后1期 → 1%缩尾 → 行业中性 → 截面 z-score

## 入库裁决
- **结论**: 通过
- **IC均值**: 0.0447452609518543
- **ICIR**: 0.5028248204339338
- **分层价差 (Top-Bottom)**: 0.007546169353719185
- **多空夏普**: 0.8258081997609092
- **多空年化**: 0.07367524572201578
- **最大回撤**: -0.1473931677690281

### 门禁明细
| 类别 | 规则 | 结果 | 取值 | 阈值 | 说明 |
|------|------|------|------|------|------|
| sample | `min_months` | PASS | 108.0 | 36 | 样本：最少有效月份数 |
| validity | `min_abs_ic` | PASS | 0.0447452609518543 | 0.02 | 有效性：|IC均值| 下限 |
| validity | `min_abs_icir` | PASS | 0.5028248204339338 | 0.3 | 有效性：|ICIR| 下限 |
| validity | `min_ic_tstat` | PASS | 5.225508817789625 | 2.0 | 有效性：|IC t统计量| 下限 |
| validity | `min_ic_hit_rate` | PASS | 0.6759259259259259 | 0.55 | 有效性：方向校正后 IC 胜率下限 |
| stability | `min_subperiod_sign_ratio` | PASS | 1.0 | 0.75 | 稳定性：年度子样本 IC 同号占比下限 |
| stability | `half_sample_sign_match` | PASS | True | True | 稳定性：前后半样本 IC 必须同号 |
| stability | `min_rolling_icir_pos_ratio` | PASS | 1.0 | 0.55 | 稳定性：滚动 ICIR>0 占比下限 |
| layered | `min_quantile_monotonicity` | PASS | 1.0 | 0.6 | 分层：分位收益单调性下限 |
| layered | `min_abs_q_spread` | PASS | 0.007546169353719185 | 0.003 | 分层：|Top-Bottom 月均价差| 下限 |
| long_short | `min_ls_sharpe` | PASS | 0.8258081997609092 | 0.3 | 多空：净夏普下限（含成本） |
| long_short | `min_ls_cagr` | PASS | 0.07367524572201578 | 0.0 | 多空：年化收益下限 |
| long_short | `max_ls_drawdown` | PASS | -0.1473931677690281 | -0.5 | 多空：最大回撤下限（更差则拒） |
| long_short | `max_avg_turnover` | PASS | 0.28477301856024884 | 1.8 | 多空：平均单边换手上限 |

## 读取与调用

```python
from factor_engineering import FactorStore

store = FactorStore()                      # 默认读取 ./factor_db
print(store.list_factors(status="admitted"))

# 读取整段面板（已按入库方向校正：direction=+1）
panel = store.load_panel("max_ret")         # DataFrame: stocks x dates

# 读取固定时点截面（主调用方式）
s = store.get_factor_on("max_ret", "2019-12-31")
print(s.head())

# 查看解释文档
print(store.get_doc("max_ret")[:500])

```

## 更新机制
本因子随 **月度固定时点更新**（默认每个自然月最后一个交易日对应月度截面）。
执行：

```bash
python3 run_factor_warehouse.py update --schedule month_end
```

## 标准入库阈值（当前）
| 参数 | 值 | 说明 |
|------|----|------|
| `min_abs_ic` | 0.02 | 有效性：|IC均值| 下限 |
| `min_abs_icir` | 0.3 | 有效性：|ICIR| 下限 |
| `min_ic_tstat` | 2.0 | 有效性：|IC t统计量| 下限 |
| `min_ic_hit_rate` | 0.55 | 有效性：方向校正后 IC 胜率下限 |
| `min_subperiod_sign_ratio` | 0.75 | 稳定性：年度子样本 IC 同号占比下限 |
| `min_half_sample_sign_match` | True | 稳定性：前后半样本 IC 必须同号 |
| `min_rolling_icir_pos_ratio` | 0.55 | 稳定性：滚动 ICIR>0 占比下限 |
| `min_quantile_monotonicity` | 0.6 | 分层：分位收益单调性下限 |
| `min_abs_q_spread` | 0.003 | 分层：|Top-Bottom 月均价差| 下限 |
| `min_ls_sharpe` | 0.3 | 多空：净夏普下限（含成本） |
| `min_ls_cagr` | 0.0 | 多空：年化收益下限 |
| `max_ls_drawdown` | -0.5 | 多空：最大回撤下限（更差则拒） |
| `max_avg_turnover` | 1.8 | 多空：平均单边换手上限 |
| `min_months` | 36 | 样本：最少有效月份数 |

## 源码入口
- 生成函数: `FACTOR_REGISTRY['max_ret']` @ `factor_engineering.factors`
- 注册表键: `max_ret`
