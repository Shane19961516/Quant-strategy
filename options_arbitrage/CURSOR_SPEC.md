# `CURSOR_SPEC.md`: 宽跨式期权 (Short Strangle) 波动率套利决策与监控系统

## 1. 系统目标与核心架构

本系统是一套专为商品与股指期货期权（BS76模型）设计的**波动率套利决策监控与持仓风控系统**。
系统通过自动化筛选“高 IV 溢价 + 无明显单边趋势”的合约组合，计算动态保证金与组合希腊值，并提供可视化交互大盘与持仓 API 管理接口。

### 技术栈选型 (Tech Stack)

* **后端 Engine / API:** Python 3.10+ / FastAPI / QuantLib (或 `scipy.stats` 实现 BS76) / Pandas / Numpy
* **数据库 / 持久化:** SQLite + SQLModel / DuckDB（用于高频历史 IV 序列快速计算）
* **前端大盘 (Interactive UI):** Streamlit **或** FastAPI + Vue3/React + Lightweight Charts (支持双轴图表)
* **任务调度:** APScheduler（定时拉取行情与更新计算）

---

## 2. 项目目录结构 (Project Structure)

```text
strangle_vol_arbitrage/
├── CURSOR_SPEC.md                  # 本设计规范文件
├── config/
│   ├── settings.yaml               # 资金偏好、风控阈值配置
│   └── margin_rules.json           # 各交易所保证金计算系数规则
├── core/
│   ├── bs76_engine.py              # BS76 期权定价与希腊值计算引擎
│   ├── metrics.py                  # IVR, IVP, IV-HV, POP 算子
│   ├── screener.py                 # 合约筛选与单腿/对腿组合引擎
│   └── capital_allocator.py        # 保证金计算与最大开仓手數算子
├── database/
│   ├── models.py                   # SQLModel 数据库表结构
│   └── db.py                       # 数据库连接与 CRUD
├── api/
│   ├── main.py                     # FastAPI 入口
│   ├── routes_screener.py          # 筛选结果 API
│   ├── routes_portfolio.py         # 盘后持仓/成交导入与希腊值汇总 API
│   └── routes_charts.py            # K线与 IV/HV 历史数据接口
├── ui/
│   └── app.py                      # Streamlit / Web 前端监控与交互窗口
└── data_fetcher/
    └── market_data.py              # 行情数据对接接口 (支持 CSV / 行情 API)
```

---

## 3. 核心计算模型与逻辑规约

### 3.1 BS76 期权定价与希腊值 (BS76 Greeks Engine)

针对期货期权，必须采用 **Black-76** 模型计算：

设 $F$ 为期货标的前日结算价/实时价，$K$ 为行权价，$T$ 为以年为单位的到期时间（$\text{DTE}/365$），$r$ 为无风险利率，$\sigma$ 为隐含波动率。

$$d_1 = \frac{\ln(F/K) + \frac{\sigma^2}{2}T}{\sigma\sqrt{T}}, \quad d_2 = d_1 - \sigma\sqrt{T}$$

* **Call Delta ($\Delta_c$):** $e^{-rT} N(d_1)$
* **Put Delta ($\Delta_p$):** $-e^{-rT} N(-d_1)$
* **Gamma ($\Gamma$):** $e^{-rT} \frac{n(d_1)}{F \sigma \sqrt{T}}$ （Call 与 Put 相同）
* **Vega ($\mathcal{V}$):** $F e^{-rT} n(d_1) \sqrt{T} \times 0.01$ （IV 变动 1% 的价值变化）
* **Theta ($\Theta$):** 转换为**单日时间衰减额**：

$$\Theta_c = \left[ -\frac{F e^{-rT} n(d_1) \sigma}{2\sqrt{T}} + r F e^{-rT} N(d_1) - r K e^{-rT} N(d_2) \right] / 365$$

### 3.2 统计波动率与溢价指标

1. **历史波动率 (HV30):** 采用标的前 30 个交易日 Log Return 的年化标准差：

$$\text{HV}_{30} = \sqrt{\frac{252}{29} \sum_{i=1}^{30} (r_i - \bar{r})^2}$$

2. **IV Rank (IVR):** 过去 252 个交易日范围内的相对位置：

$$\text{IVR} = \frac{\text{IV}_{current} - \text{IV}_{252d\_min}}{\text{IV}_{252d\_max} - \text{IV}_{252d\_min}} \times 100\%$$

3. **IV Percentile (IVP):** 过去 252 个交易日中，IV 低于当前 IV 的天数占比：

$$\text{IVP} = \frac{\sum \mathbb{I}(\text{IV}_{hist} < \text{IV}_{current})}{252} \times 100\%$$

4. **IV-HV 差值:** $\text{Spread} = \text{IV}_{current} - \text{HV}_{30}$

### 3.3 胜率 (POP)、保证金与组合指标

1. **不被行权概率 (POP - Probability of Profit):**
近似公式：$\text{POP} \approx 1 - \vert{}\Delta_{Call}\vert{} - \vert{}\Delta_{Put}\vert{}$（精确计算时采用对数正态分布在 $[K_{Put}, K_{Call}]$ 区间的累积概率）。
2. **交易所保证金测算 (Domestic / CME Standard):**
单腿卖出卖出 Call 保证金公式（以国内商品期权为例）：

$$\text{Margin}_{Call} = \text{权利金结算价} + \max\left( \text{标的合约保证金} - \frac{1}{2} \text{虚值额}, 0.5 \times \text{标的合约保证金} \right)$$

*组合保证金采用“取大值 + 虚值端权利金”的优惠估计规则。*
3. **最大开仓对数算子 (Position Sizing):**
设总可用资金为 $W$（例如 $100,000$），单个品种最大保证金授权占比为 $R_{max}$（例：$30\%$）。
单个宽跨式组合（1手Call + 1手Put）所需保证金为 $M_{unit}$。

$$\text{Max Pairs} = \lfloor \frac{W \times R_{max}}{M_{unit}} \rfloor$$

4. **组合期望收益率 (Un-exercised ROI):**

$$\text{ROI}_{max} = \frac{\text{总收取权利金 (Premium\_{Total})} \times \text{Max Pairs}}{\text{实际占用保证金 (Margin\_{Total})}} \times 100\%$$

---

## 4. 核心功能模块详细设计

### 模块一：全市场筛选引擎 (`screener.py`)

* **过滤条件 (Hard Filters):**
1. $\text{DTE} \in [30, 45]$ 天；
2. $\text{IVR} > 50\%$ 且 $\text{IVP} > 70\%$；
3. $\text{IV}_{30d} - \text{HV}_{30d} > 5\%$。

* **匹配对腿算法 (Contract Pairing):**
* 遍历符合 DTE 的合约列，锁定目标标的；
* 分别寻找 Call 列表中 $\vert{}\Delta_c - 0.20\vert{}$ 最小的 Call 合约；
* 寻找 Put 列表中 $\vert{}\Delta_p - (-0.20)\vert{}$ 最小的 Put 合约；
* 组合为 `ShortStrangleCandidate` 结构体。

### 模块二：交互式诊断与图表观察窗口 (`ui/app.py` & `routes_charts.py`)

* **布局与交互设计：**
* **主选择器：** 下拉菜单选择筛选出来的“符合条件品种列表”；
* **顶栏度量板 (Metric Tiles):** 显示当前标的价、当前 IVR/IVP、匹配到的 Call/Put 行权价、预估胜率 (POP)、最大可开手數、组合 ROI。
* **主图表 (Dual-Axis Interactive Chart):**
* **上图（价格与区间通道）：** 标的历史 K 线 + Donchian Channel / 布林带，在图表右侧标出 **卖出 Call 行权价（红虚线）** 与 **卖出 Put 行权价（绿虚线）**，直观观察安全边际。
* **下图（波动率走势）：** 绘制历史 **IV 曲线 (主线)** 与 **HV30 曲线 (辅线)**，高亮标注 IVR/IVP 拐头区间。

### 模块三：盘后持仓与成交 Web API 接口 (`routes_portfolio.py`)

构建 RESTful Web API，提供外部更新持仓与交互的接口：

#### 1. POST `/api/v1/positions/sync`
#### 2. GET `/api/v1/portfolio/greeks-summary`

### 模块四：风控与监控补强

1. **Delta 倾斜预警 (Delta Tilt Alert):** 净 Delta 绝对值超过 `0.30` 时触发对冲/移仓提醒。
2. **Gamma 暴击预警 (Gamma Squeeze Alert):** DTE < 10 且标的价格贴近行权价 3% 范围内，强制高亮警告。
3. **保证金占用上限防护:** 总体账户保证金占用率超过 60% 时，Screener 拒绝输出新的开仓对数建议。

---

## 5. 数据库模型定义 (`database/models.py`)

采用 `SQLModel` 定义持久化表：`OptionContractCache`, `ScreenerResult`, `DailyPosition`。

---

## 6. 分步执行指令

### 阶段一：建立核心算法库 (Core Analytics)
### 阶段二：筛选与资金分配引擎 (Screener & Allocator)
### 阶段三：FastAPI 后端与持仓同步接口 (API Engine)
### 阶段四：前端 UI 交互与图表可视化 (Interactive UI)
