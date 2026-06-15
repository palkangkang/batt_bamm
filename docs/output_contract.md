# 输出契约

## 顶层 `summary.json`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `contract_version` | 是 | 冻结输出契约版本（`3.0.0`） |
| `contract_fields` | 是 | 稳定字段与可扩展字段声明 |
| `generated_at_utc` | 是 | UTC 时间戳 |
| `mode` | 是 | `baseline|hppc|timeseries|benchmark` |
| `all_converged` | 是 | 整体通过/失败 |
| `config` | 是 | 运行时配置快照 |
| `termination_policy` | 是 | 生效的截止策略快照 |
| `termination_hits` | 是 | 命中截止的样本数量 |
| `artifacts` | 是 | 关键输出文件 |
| `cases` | 是 | 样本行与摘要 |
| `warnings` | 可选 | 去重后的警告 |
| `identification_inputs_validation` | 可选 | 模板校验结果 |
| `quality_gate` | benchmark | 质量门禁摘要 |
| `benchmark` | benchmark | Benchmark 聚合结果 |
| `sanity_gate` | baseline | 健全性门禁 结果 |
| `hppc` | hppc | HPPC 聚合结果 |
| `timeseries` | timeseries replay | Timeseries 回放结果 |
| `charge_compare` | timeseries compare | 充电对比结果 |

稳定/可扩展策略：
- 顶层稳定字段在 `summary.contract_fields.stable_top_level_fields` 中声明。
- 样本稳定字段在 `summary.contract_fields.stable_case_fields` 中声明。
- 新字段只能通过向后兼容追加方式引入，需走 `extensible_*` 声明。

## RunSummary（通用样本摘要）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `case_id` | 是 | 样本标识 |
| `converged` | 是 | 样本通过/失败 |
| `min_v`, `max_v` | 是（可空） | 电压范围 |
| `final_soc` | 是（可空） | 最终 SOC |
| `runtime_s` | 是 | 运行时长（秒） |
| `csv_path` | 是（可空） | 样本 CSV 路径 |
| `termination` | 是 | 截止明细 |
| `error` | 可选 | 失败原因 |

`termination` 字段：
- `hit, reason, time_s, index, metric, op, threshold, value`

## 统一 timeseries 输出列（CSV）

必需列：
- `time_s`
- `current_a`
- `voltage_v`
- `ocv_v`
- `soc`
- `cell_temperature_k`
- `boundary_temperature_k`

迁移说明：
- 不再支持 `termination.metric=temperature_k`；请改用 `cell_temperature_k` 或 `boundary_temperature_k`。

可选 `timeseries` 负载块（当 `soc_switch_approx.enabled=true`）：
- `soc_switch_approx.enabled`
- `soc_switch_approx.predicted_switch_time_s`
- `soc_switch_approx.predicted_end_time_s`
- `soc_switch_approx.soc_at_predicted_switch`
- `soc_switch_approx.final_soc`
- `soc_switch_approx.switch_soc_error`
- `soc_switch_approx.final_soc_error`

## Benchmark 块（`summary.benchmark`）

必需字段：
- `passed`
- `total_cases`
- `converged_cases`
- `convergence_rate`
- `repeatability`
- `trend_checks`
- `failures`
- `artifacts`

`benchmark.failures[]` 为结构化证据（非自由文本）：
- `category, reason, profile_id, rate_c, repeat, observed, threshold`

## 质量门禁块（`summary.quality_gate`）

必需字段：
- `enabled`
- `enforce`
- `passed`
- `thresholds`
- `metrics`

## 识别输入校验块（`summary.identification_inputs_validation`）

必需字段：
- `enabled`
- `strict`
- `passed`
- `datasets`
- `errors`

## HPPC 对比摘要（`hppc_compare_summary.json`）

必需字段：
- `cell_id`
- `chemistry`
- `nominal_capacity_ah`
- `soc_grid`
- `dfn_run`
- `ecm_run`
- `completed_points`
- `passed`
- `metrics`
- `artifacts`

可选字段：
- `stop_reason`
- `warnings`
