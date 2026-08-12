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


五.因子工程（Factor Engineering）
------

新增 `factor_engineering/`：面向 A 股月频价量数据的**因子研究与因子库**全流程。

### 1) 完整流程
```
因子生成 → 有效性/稳定性/分层/多空检验 → 入库标准裁决
       → FactorStore 录入 → 解释文档 → 固定时点更新 → 读取调用
```

| 步骤 | 模块 | 说明 |
|------|------|------|
| 生成 | `factors` / `process` | 滞后1期、缩尾、行业中性、z-score |
| 检验 | `battery` | 有效性、稳定性、分层、多空四套检验 |
| 入库标准 | `admission` | 全部门禁通过才可 `admitted` |
| 数据库 | `store` | SQLite 元数据 + `panels/*.csv.gz` + 文档 |
| 文档/调用 | `docs` + `FactorStore` API | `list` / `get_factor_on` / `get_doc` |
| 固定更新 | `update` | 默认 `month_end` 重算、复检、写审计 |

### 2) 入库标准（摘要）
详见 `factor_db/ADMISSION_STANDARD.md`（运行 `admit` 后生成），核心阈值：
- 有效性：\|IC\|≥0.02，\|ICIR\|≥0.30，\|t\|≥2，胜率≥55%
- 稳定性：年度同号≥75%，半样本同号，滚动 ICIR 为正≥55%
- 分层：单调性≥0.60，\|Top−Bottom\|≥0.3%/月
- 多空：夏普≥0.30，回撤≥−50%，换手≤1.8（含 20bp 成本）

### 3) 运行
```bash
pip install -r factor_engineering/requirements.txt

# 一键：生成 + 检验 + 入库 + 写文档
python3 run_factor_warehouse.py admit

# 查看入库标准 / 已入库因子 / 文档 / 截面
python3 run_factor_warehouse.py standard
python3 run_factor_warehouse.py list
python3 run_factor_warehouse.py doc rev_1
python3 run_factor_warehouse.py get rev_1 --date 2019-12-31

# 固定时点更新（月末）
python3 run_factor_warehouse.py update --schedule month_end
python3 run_factor_warehouse.py schedule

# 研究向报告（合成/IC 图等）
python3 run_factor_engineering.py
python3 -m pytest factor_engineering/tests -q
```

### 4) 调用示例
```python
from factor_engineering import FactorStore

store = FactorStore()  # ./factor_db
store.list_factors(status="admitted")
panel = store.load_panel("rev_1")           # 已按 direction 校正
s = store.get_factor_on("rev_1", "2019-12-31")
print(store.get_doc("rev_1")[:400])
```

### 5) 设计要点
- **无前视**：因子统一 `shift(1)`，回测 `weight[t]*return[t]`。
- **方向校正**：IC 为负则 `direction=-1`，读取 API 默认取反。
- **审计**：每次入库写入 `admission_runs`；每次更新写入 `update_runs`。
