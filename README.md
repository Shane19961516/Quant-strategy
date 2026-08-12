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

新增 `factor_engineering/`：面向 A 股月频价量数据的**因子研究全流程**工具包（构建 → 处理 → 评估 → 筛选 → 正交/合成 → 多空验证）。

### 1) 能力清单
| 模块 | 内容 |
|------|------|
| 因子库 | 动量 / 反转 / 低波 / MAX / 偏度 / 下行波动 / 流动性代理等 |
| 处理 | 滞后1期、缩尾、行业中性、截面 z-score / rank |
| 评估 | Rank/Pearson IC、ICIR、t 统计、IC 衰减、自相关、分位收益与单调性、换手 |
| 筛选 | 质量分榜单 + IC/ICIR/胜率/单调性/换手/冗余相关软过滤 |
| 合成 | 等权、滚动 ICIR；可选 Gram-Schmidt 正交化 |
| 验证 | 五分位多空回测（含交易成本）+ 自动研究报告 |

### 2) 运行
```bash
pip install -r factor_engineering/requirements.txt
python3 run_factor_engineering.py
python3 run_factor_engineering.py --universe csi300 --combine equal
python3 run_factor_engineering.py --factors rev_1,vol_12,max_ret,skew_12,mom_12_1
python3 -m pytest factor_engineering/tests -q
```

结果输出至 `factor_engineering_result/`（`FACTOR_ENGINEERING_REPORT.md`、质量榜、IC 序列/衰减、相关矩阵、合成净值与图）。

### 3) 设计要点
- **无前视**：因子先算 raw，再统一 `shift(1)`，回测用 `weight[t] * return[t]`。
- **方向校正**：IC 为负的因子在合成前按 `direction` 取反。
- **冗余控制**：高相关因子对仅保留质量分更高者。
