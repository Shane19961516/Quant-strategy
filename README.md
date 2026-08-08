# Brinson-Attribution

一.概述
------
brinson模型是基金组合业绩归因的重要工具，借助基金具体的持仓信息，来实现基金当期收益的分解。

具体而言，可以将基金所持有的股票组合相对于基准股票组合的超额收益分为资产配置收益和个股选择收益，考虑到股票组合通常按照行业维度进行分类，因此可将基金投资股票部分的超额收益分解为行业配置收益和行业内的个股选择收益。

另一方面，基金除了股票之外，还会将部分资金配置于债券、银行存款、货币基金等类固定收益资产。随着市场行情的变动，组合投资经理会调整股票与债券两部分投资之间的比例，从而通过择时对超额收益产生贡献，因此，最终可以将基金的超额收益分解为择时效应、行业配置效应和选股效应三部分。

代码用于以wind为数据源的基金单期brinson业绩归因。

二.模型细节
------

## 1.单层模型
### BHB模型 
Brinson、Hood和Beebower（1986）提出Brinson模型的经典版本，记为BHB模型，该模型将组合的超额收益分解为**资产配置收益**、**选择收益**和**交互收益**。

假定组合中的证券全部属于L个行业。以W<sub>i</sub>表示基准组合中行业i的权重，w<sub>i</sub>表示实际组合中行业i的权重；b<sub>i</sub>表示基准组合中行业i的收益，r<sub>i</sub>表示实际组合中行业i的收益。

![](https://github.com/ShiliangZhang-nku/Brinson-Attribution/blob/master/pics/brinson.png)

图中的4个组合分别为基准组合P<sub>1</sub>，主动配置组合P<sub>2</sub>，主动选择组合P<sub>3</sub>，实际投资组合P<sub>4</sub>。

超额收益表示为实际组合P<sub>4</sub>与基准组合P<sub>1</sub>之间的收益差额R<sub>e</sub>=P<sub>4</sub>-P<sub>1</sub>。基于4个组合，可以将R<sub>e</sub>分解为资产配置收益（AR）、选择收益（SR）和交互收益（IR）。

![](https://github.com/ShiliangZhang-nku/Brinson-Attribution/blob/master/pics/AR_SR_IR.png)





三.框架结构
------

四.核心代码说明
------
1.read_fund_holding函数：

输入基金代码与基准股票部分所占比例，返回：（1）股票持仓比例矩阵；（2）基准中股票与债券的持仓比例矩阵。

注意运行此函数前需运行 clean_index_quote 和 clean_fund_holding 对下载的基金持仓和基金/指数行情数据进行清洗。


2.brinson_attr_asset函数：

输入read_fund_holding函数的持仓比例矩阵，返回双层brinson归因模型的运行结果。

根据股票和债券部分比例，计算择时效应TR，通过调用brinson_attr_stock函数计算配置效应AR和选股效应SR；

通过verbose参数控制是否存储单层brinson归因结果（股票行业配置和选股效应）。


3.brinson_attr_stock函数：

计算所有单期截面的归因结果，通过调用brinson_attr_single_period函数进行计算，

通过version参数选择brinson模型版本，version=1 -- BHB模型， version=2 -- BF模型。

4.get_index_ret函数：

将日收益率转换为设定频率的收益率，默认为6个月（披露完整持仓数据的报告期仅为半年报和年报），

详见代码。


五.股票多因子策略
------

新增 `multifactor/` 模块：基于仓库内月度涨跌幅与中信一级行业，构建可回测的 A 股多因子选股框架。

### 1) 因子集（高分=更宜做多）
| 因子 | 逻辑 | 默认 |
|------|------|------|
| `rev_1` | 1 月反转 | ✓ |
| `vol_12` | 低波动（过去 12 月收益标准差取负） | ✓ |
| `max_ret` | MAX 因子（过去 12 月最大月收益取负） | ✓ |
| `skew_12` | 负偏度偏好 | ✓ |
| `mom_12_1` | 12-1 月动量 | 可选（本样本 IC 偏负） |
| `ind_resid_mom` | 行业中性残差动量 | 可选 |

处理流程：因子滞后 1 期 → 缩尾 → 行业中性 → 截面 z-score → 等权 / 滚动 ICIR 合成 → 五分位多空或 Top% 多头 → 月度再平衡（含交易成本）。

### 2) 运行
```bash
python3 stock_multifactor_strategy.py
python3 stock_multifactor_strategy.py --portfolio long_only --universe csi300 --combine equal
python3 stock_multifactor_strategy.py --factors mom_12_1,rev_1,vol_12,max_ret,skew_12,ind_resid_mom
python3 -m pytest multifactor/tests -q
```

结果输出至 `multifactor_result/`（净值、IC、分位收益、持仓快照与图）。

### 3) 样本回测摘要（2010-01 ~ 2019-12，成本 20bp 单边）
| 配置 | 累计 | 年化 | 夏普 | 最大回撤 |
|------|------|------|------|---------|
| 默认 ICIR 多空 | +124.7% | 8.4% | 0.72 | -32.8% |
| 等权多空 | +122.5% | 8.3% | 0.69 | -33.2% |
| 沪深300内 Top20% 多头 | +207.0% | 11.9% | 0.58 | -40.3%（超额年化 8.8%，IR 0.80） |


六.美股标普500多因子策略（yfinance, causal v2）
------

新增 `us_multifactor/`：yfinance 数据，标普500，**周频换仓、持有10只**。

### 审计结论（v1 → v2）
v1 中 Sharpe≈3.08 不可交付：存在**同周 SPY 收益过滤前视**，以及 Yahoo `info` 基本面截面回灌。v2 全部信号 `shift(1)`，生产因子仅用价量（动量/稳定性/规模）。

### 1) 生产因子与权重
| 大类 | 权重 | 因子（各类 Top5，类内等权） |
|------|------|---------------------------|
| 动量 | **60%** | `mom_1m`, `mom_accel`, `mom_12_1`, `mom_12m`, `mom_1w` |
| 稳定性 | **30%** | `inv_downside_vol`, `inv_vol_6m`, `inv_vol_1m`, `inv_vol_3m`, `inv_beta` |
| 规模 | **10%** | `neg_log_mcap`, `neg_mcap_rank`, `inv_price_rank`, `neg_log_price`, `neg_log_dollar_vol` |

### 2) 运行
```bash
pip install -r us_multifactor/requirements.txt
python3 us_spx_multifactor_strategy.py              # causal v2 主策略
python3 us_spx_multifactor_strategy.py --defensive  # 波动目标防守版
python3 us_spx_multifactor_strategy.py --search     # 重建交付产物
python3 -m pytest us_multifactor/tests -q
```

### 3) 交付指标（2016→2026，因果、无前视）
| 版本 | Sharpe | CAGR | MaxDD | OOS Sharpe |
|------|--------|------|-------|------------|
| Primary | **1.62** | **43.4%** | -15.9% | **1.59** |
| Defensive | 1.57 | 24.3% | -11.6% | ~1.6 |

说明：在无前视的多头 Top10 周频约束下，**无法同时**稳定达到 Sharpe≥3、CAGR≥30%、MDD≤10%。可交付版本优先正确性与样本外一致性（详见 `us_multifactor_result/EVALUATION.md`）。
