# -*- coding: utf-8 -*-
"""因子解释文档生成与标准调用说明。"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .admission import CRITERIA_DOCS, AdmissionCriteria, AdmissionDecision
from .battery import BatteryResult
from .factors import FACTOR_META, FACTOR_REGISTRY


FORMULAS: Dict[str, str] = {
    "mom_12_1": "exp(sum(log(1+r)_{t-12..t-1})) - 1，再滞后1期",
    "mom_6": "近6月累计收益，滞后1期",
    "mom_3": "近3月累计收益，滞后1期",
    "rev_1": "-r_t，滞后1期（做多上月弱势股）",
    "vol_12": "-std(r)_{12m}，滞后1期",
    "vol_6": "-std(r)_{6m}，滞后1期",
    "max_ret": "-max(r)_{12m}，滞后1期（MAX 因子取负）",
    "skew_12": "-skew(r)_{12m}，滞后1期",
    "downside_vol": "-std(min(r,0))_{12m}，滞后1期",
    "ind_resid_mom": "mom_12_1 相对行业均值残差，滞后1期",
    "liquidity_proxy": "-mean(|r|)_{6m}，滞后1期（跳跃/非流动性代理）",
}


def build_factor_doc(
    name: str,
    decision: AdmissionDecision,
    metrics: Mapping[str, Any],
    *,
    criteria: Optional[AdmissionCriteria] = None,
) -> Dict[str, str]:
    meta = FACTOR_META.get(name, {})
    family = meta.get("family", "other")
    desc = meta.get("desc", name)
    formula = FORMULAS.get(name, "见源码 factor_engineering.factors")
    crit = criteria or AdmissionCriteria()
    status = "已入库 (admitted)" if decision.admitted else "未通过入库 (rejected)"
    direction = decision.direction

    gates_lines = []
    for g in decision.gates:
        mark = "PASS" if g.passed else "FAIL"
        gates_lines.append(
            f"| {g.category} | `{g.name}` | {mark} | {g.value} | {g.threshold} | {g.detail} |"
        )

    api = f'''from factor_engineering import FactorStore

store = FactorStore()                      # 默认读取 ./factor_db
print(store.list_factors(status="admitted"))

# 读取整段面板（已按入库方向校正：direction={direction:+d}）
panel = store.load_panel("{name}")         # DataFrame: stocks x dates

# 读取固定时点截面（主调用方式）
s = store.get_factor_on("{name}", "2019-12-31")
print(s.head())

# 查看解释文档
print(store.get_doc("{name}")[:500])
'''

    body = f"""# 因子文档：`{name}`

## 概要
- **名称**: {name}
- **中文**: {desc}
- **类别**: {family}
- **状态**: {status}
- **使用方向 direction**: {direction:+d}（调用 `load_panel` / `get_factor_on` 默认已校正，高分为宜做多）
- **公式**: {formula}
- **处理**: 滞后1期 → 1%缩尾 → 行业中性 → 截面 z-score

## 入库裁决
- **结论**: {"通过" if decision.admitted else "拒绝"}
- **IC均值**: {metrics.get("ic_mean")}
- **ICIR**: {metrics.get("icir")}
- **分层价差 (Top-Bottom)**: {metrics.get("q_spread")}
- **多空夏普**: {metrics.get("ls_sharpe")}
- **多空年化**: {metrics.get("ls_cagr")}
- **最大回撤**: {metrics.get("ls_max_drawdown")}

### 门禁明细
| 类别 | 规则 | 结果 | 取值 | 阈值 | 说明 |
|------|------|------|------|------|------|
{chr(10).join(gates_lines)}

## 读取与调用

```python
{api}
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
"""
    for k, v in crit.to_dict().items():
        if k in ("rolling_icir_window", "cost_bps", "n_quantiles"):
            continue
        body += f"| `{k}` | {v} | {CRITERIA_DOCS.get(k, '')} |\n"

    body += f"""
## 源码入口
- 生成函数: `FACTOR_REGISTRY['{name}']` @ `factor_engineering.factors`
- 注册表键: `{name}`
"""
    return {"title": f"{name} — {desc}", "body_md": body, "api_example": api}


def render_admission_standard_md(criteria: Optional[AdmissionCriteria] = None) -> str:
    crit = criteria or AdmissionCriteria()
    lines = [
        "# 因子入库标准（Admission Standard）",
        "",
        "因子须通过以下 **全部** 门禁方可写入因子库（status=`admitted`）。",
        "",
        "## 1. 有效性 Validity",
        f"- |IC均值| ≥ **{crit.min_abs_ic}**",
        f"- |ICIR| ≥ **{crit.min_abs_icir}**",
        f"- |IC t| ≥ **{crit.min_ic_tstat}**",
        f"- 方向校正后 IC 胜率 ≥ **{crit.min_ic_hit_rate}**",
        "",
        "## 2. 稳定性 Stability",
        f"- 年度子样本 IC 与全样本同号占比 ≥ **{crit.min_subperiod_sign_ratio}**",
        f"- 前后半样本 IC 同号（强制）" if crit.min_half_sample_sign_match else "- 半样本同号：关闭",
        f"- 滚动 {crit.rolling_icir_window}m ICIR（方向校正）为正占比 ≥ **{crit.min_rolling_icir_pos_ratio}**",
        "",
        "## 3. 分层检验 Layered",
        f"- 分位收益单调性 ≥ **{crit.min_quantile_monotonicity}**",
        f"- |Top−Bottom 月均价差| ≥ **{crit.min_abs_q_spread}**",
        "",
        "## 4. 多空检验 Long-Short",
        f"- 净夏普 ≥ **{crit.min_ls_sharpe}**（成本 {crit.cost_bps}bp）",
        f"- 年化收益 ≥ **{crit.min_ls_cagr}**",
        f"- 最大回撤 ≥ **{crit.max_ls_drawdown}**（不低于该下限）",
        f"- 平均单边换手 ≤ **{crit.max_avg_turnover}**",
        "",
        "## 5. 样本",
        f"- 有效月份 ≥ **{crit.min_months}**",
        "",
        "## 方向",
        "若 IC 均值为负，入库时 `direction=-1`，读取 API 默认取反，使高分始终对应多头偏好。",
        "",
    ]
    return "\n".join(lines)
