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

五.SOTP valuation Agent
------

新增 `sotp_valuation_agent.py`，用于执行分部估值（Sum-of-the-Parts, SOTP）并输出：

- 分部估值明细（每个业务板块的 implied value）
- 企业价值（Enterprise Value）
- 股权价值（Equity Value）
- 每股价值（Per Share Value）

运行示例：

```bash
python sotp_valuation_agent.py '{
  "segments": [
    {"name":"Core Business","metric_value":120.0,"valuation_multiple":8.5,"ownership":1.0},
    {"name":"Fintech","metric_value":35.0,"valuation_multiple":12.0,"ownership":0.75}
  ],
  "net_debt": 180.0,
  "non_operating_assets": 60.0,
  "minority_interest": 25.0,
  "shares_outstanding": 300.0
}'
```

使用 yfinance 自动拉取财务数据并生成 bear/base/bull 三情景：

```bash
python3 sotp_valuation_agent.py --ticker AAPL
```

说明：yfinance 不提供标准化分部收入拆分，因此该模式默认使用单一收入分部（Revenue Base）并围绕 `enterpriseToRevenue` 生成情景倍数。

Hybrid 模式（推荐）：yfinance 提供资本结构，用户提供分部拆分（更接近真实 SOTP）

```bash
python3 sotp_valuation_agent.py --hybrid-payload '{
  "ticker": "AAPL",
  "segment_splits": [
    {"name":"Products","revenue_share":0.75,"valuation_multiple":6.5},
    {"name":"Services","revenue_share":0.25,"valuation_multiple":12.0}
  ]
}'
```

说明：
- `revenue_share` 会自动归一化（总和不必严格等于 1）。
- 未提供 `valuation_multiple` 时，默认使用 yfinance 的 `enterpriseToRevenue`。
- 可在每个分部补充论据字段：`peer_multiples`、`gross_margin`、`market_share`、`growth_rate`、`lifecycle_stage`（`introduction/growth/mature/decline`），程序会输出 `evidence_report` 解释为何给该倍数。

带论据字段的示例：

```bash
python3 sotp_valuation_agent.py --hybrid-payload '{
  "ticker":"AAPL",
  "segment_splits":[
    {"name":"Products","revenue_share":0.75,"valuation_multiple":6.5,"peer_multiples":[5.8,6.1,6.4,6.0],"gross_margin":0.38,"market_share":0.24,"growth_rate":0.06,"lifecycle_stage":"mature"},
    {"name":"Services","revenue_share":0.25,"valuation_multiple":12.0,"peer_multiples":[8.5,9.2,10.1,9.8],"gross_margin":0.72,"market_share":0.18,"growth_rate":0.19,"lifecycle_stage":"growth"}
  ]
}'
```

Hybrid 情景模式（自动 bear/base/bull）：

```bash
python3 sotp_valuation_agent.py --hybrid-scenarios-payload '{
  "ticker": "AAPL",
  "segment_splits": [
    {"name":"Products","revenue_share":0.75,"valuation_multiple":6.5},
    {"name":"Services","revenue_share":0.25,"valuation_multiple":12.0}
  ],
  "bear_factor": 0.85,
  "bull_factor": 1.15,
  "scenario_probabilities": {"bear": 0.25, "base": 0.50, "bull": 0.25}
}'
```

说明：
- bear/base/bull 分别按 `bear_factor` / `1.0` / `bull_factor` 缩放每个分部倍数。
- 分部收入占比与 yfinance 抓取的资本结构参数在三情景下保持一致。
- 支持 `scenario_probabilities` 做概率加权估值（自动归一化），输出 `probability_weighted` 结果。

可选紧凑输出格式（便于快速对比）：

```bash
# 表格输出
python3 sotp_valuation_agent.py --ticker AAPL --output-format table

# CSV 输出
python3 sotp_valuation_agent.py --hybrid-scenarios-payload '{...}' --output-format csv
```

说明：`--output-format table/csv` 仅适用于情景模式（`--ticker` 与 `--hybrid-scenarios-payload`）。

可视化报告输出（HTML，含结论 + 论据 + 风险提示）：

```bash
python3 sotp_valuation_agent.py --hybrid-scenarios-payload '{
  "ticker":"AAPL",
  "segment_splits":[
    {"name":"Products","revenue_share":0.75,"valuation_multiple":6.5,"peer_multiples":[5.8,6.1,6.4,6.0],"gross_margin":0.38,"market_share":0.24,"growth_rate":0.06,"lifecycle_stage":"mature"},
    {"name":"Services","revenue_share":0.25,"valuation_multiple":12.0,"peer_multiples":[8.5,9.2,10.1,9.8],"gross_margin":0.72,"market_share":0.18,"growth_rate":0.19,"lifecycle_stage":"growth"}
  ],
  "bear_factor":0.85,
  "bull_factor":1.15
}' --report-out ./aapl_sotp_report.html
```

报告现已包含“投资建议（模板）”区块：
- 当前立场（偏积极/中性/偏谨慎）
- 基于实时价格的上行/下行空间
- 建议跟踪触发条件（倍数、竞争、资本结构）

报告结构升级为“审委会版三页”：
- 第1页：Executive Summary（结论、核心KPI、情景估值）
- 第2页：估值假设矩阵（倍数、可比中位数、毛利、市占率、增速、阶段）
- 第3页：风险与反证（红旗提示 + what could go wrong 清单）

支持导出美化 PDF：

```bash
python3 sotp_valuation_agent.py --hybrid-scenarios-payload '{...}' --pdf-out ./aapl_sotp_report.pdf
```

也可以同时导出 HTML + PDF：

```bash
python3 sotp_valuation_agent.py --hybrid-scenarios-payload '{...}' --report-out ./aapl_sotp_report.html --pdf-out ./aapl_sotp_report.pdf
```
