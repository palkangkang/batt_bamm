# 外部测试数据调优示例

本目录用于跑通“先 ECM 拟合、后 DFN 微调”的外部数据参数调优流程。示例数据只用于验证流程，不代表真实电芯标定质量。

## 文件清单

- `manifest_example.csv`：多工况入口清单。
- `manifest_arbitrary_waveform.csv`：单个任意波形工况入口清单。
- `case_train_0p5c.csv`：训练用 0.5C 近似充放电片段。
- `case_train_hppc_like.csv`：训练用 HPPC 风格脉冲片段。
- `case_validation_1c.csv`：验证用 1C 片段。
- `case_arbitrary_waveform.csv`：非 HPPC 任意波形示例，只包含 `time_s/current_a/voltage_v` 三列。

## manifest 模板要求

必需列：

```csv
case_id,csv_path,split,initial_soc,nominal_capacity_ah,ambient_temp_k,weight
```

字段说明：

- `case_id`：工况标识，必须唯一。
- `csv_path`：测试 CSV 路径；相对路径按 manifest 所在目录解析。
- `split`：`train` 或 `validation`；训练集参与拟合，验证集用于结果对比与诊断。
- `initial_soc`：该工况起始 SOC，范围为 `[0, 1]`；当前主要用于 DFN 回放初始条件，也为后续 SOC 对齐预留。
- `nominal_capacity_ah`：标称容量，单位 Ah；用于 DFN 容量缩放与库仑量尺度。
- `ambient_temp_k`：默认环境温度，单位 K；当测试 CSV 没有 `temp_k` 时用于补齐温度列。
- `weight`：该工况在目标函数中的权重，必须为正数；数值越大，该工况残差对拟合目标影响越大。

## 测试 CSV 模板要求

必需列：

```csv
time_s,current_a,voltage_v
```

可选列：

- `temp_k`：温度，单位 K；缺省时使用 manifest 中的 `ambient_temp_k`。
- `soc`：外部估算 SOC，仅用于后续诊断扩展。
- `case_note`：备注字段，仅透传。

约束：

- `time_s` 可以从任意时刻开始，脚本会归一到 0；但文件内必须严格递增，不能重复。
- `current_a` 单位为 A，必须使用同一符号约定，不能同一文件内混用不同约定。
- `voltage_v` 是端电压观测值，用作拟合目标；模型只把 `time_s/current_a` 作为电流激励输入。
- `voltage_v` 必须在合理物理范围内。

## 电流符号约定

默认约定为 `target.current_sign: discharge_positive`：

- 放电电流为正值，例如 `current_a=75` 表示以 75 A 放电。
- 充电电流为负值，例如 `current_a=-40` 表示以 40 A 充电。
- 静置或近似无负载为 `current_a=0`。

如果外部设备导出的数据采用“充电为正、放电为负”，不要手动改 CSV，可以在配置中设置：

```yaml
target:
  current_sign: charge_positive
```

此时脚本会在内部把电流符号翻转成平台统一约定，再进入 ECM/DFN 回放。无论选择哪种设置，manifest 中所有 case 都必须采用同一种原始符号约定。

## 变量补充清单

当前脚本强制要求的最小数据是 `time_s/current_a/voltage_v`，但为了提高诊断与后续热耦合扩展能力，建议按优先级补充以下变量。

测试 CSV 中建议补充：

- `temp_k`：实测或环境温度，单位 K；后续热耦合调优会优先使用它。
- `soc`：外部估算 SOC，范围建议为 `[0, 1]`；当前不作为强制拟合输入，后续可用于 SOC 对齐与异常诊断。
- `case_note`：工况备注，如倍率、温度箱设定、测试阶段；仅用于人工追溯。

manifest 中必须认真填写：

- `initial_soc`：若不准确，DFN 微调的初始条件会偏移，电压残差可能被错误归因到参数。
- `nominal_capacity_ah`：容量尺度错误会直接影响 SOC 变化速率和 DFN 容量缩放。
- `ambient_temp_k`：在 CSV 缺少 `temp_k` 时会被用作默认温度。
- `weight`：建议先全部设为 `1.0`；只有确认某些工况更可信或更重要时再调高。

配置中需要关注：

- `target.current_sign`：必须匹配外部设备导出的电流方向。
- `fit.min_train_cases`：有效训练 case 数量下限；单文件演示可设为 `1`，正式拟合建议至少 `2`。
- `fit.current_dynamic_threshold_a`：区分动态段和静置/低电流段的电流阈值。
- `fit.loss_dynamic_weight`：动态段残差权重；默认 `0.7` 表示更重视有电流激励的片段。
- `fit.ecm.ecm_order`：ECM 阶数，`2` 表示 2RC；失败时可通过 `fallback_to_1rc` 降级。
- `fit.ecm.soc_grid`：输出 ECM 参数包的 SOC 轴；当前任意波形示例主要用于流程验证，不代表这些 SOC 点都被充分辨识。
- `fit.ecm.temp_axis_c`：输出 ECM 参数包的温度轴；当前无热耦合时使用近似常温双点占位。
- `fit.dfn.bounds.initial_soc` 与 `fit.dfn.bounds.capacity_scale`：DFN 微调边界，建议保持窄范围，避免用少量电压数据过拟合。
- `fit.thermal.enabled`：当前保持 `false`；热参数联合调优需要额外温度目标后再开启。

## 任意波形单文件试跑

`case_arbitrary_waveform.csv` 用混合脉冲和缓变电流构造，不遵循 HPPC 固定协议。它用于证明接口层不限制 HPPC，只要提供 `time_s/current_a/voltage_v` 即可进入残差拟合。

输入 CSV 示例：

```csv
time_s,current_a,voltage_v
5,0,4.010
7,35,3.968
9,70,3.902
11,110,3.814
```

对应 manifest 示例：

```csv
case_id,csv_path,split,initial_soc,nominal_capacity_ah,ambient_temp_k,weight
case_arbitrary_waveform,case_arbitrary_waveform.csv,train,0.88,150,298.15,1.0
```

专用配置：

- `configs/setups/external_parameter_tune_arbitrary_waveform.yaml`
- `fit.min_train_cases: 1`
- 默认顺序仍为先 ECM 拟合，再 DFN 微调。
- `fit.thermal.enabled: false`，当前只预留热耦合接口。

试跑命令：

```powershell
cd C:\Users\pal\pyenv\colab
$env:PYTHONPATH='C:\Users\pal\projects\batt_bamm\src'
pipenv run python -m batt_bamm.external_parameter_tune `
  --config C:\Users\pal\projects\batt_bamm\configs\setups\external_parameter_tune_arbitrary_waveform.yaml `
  --output-dir C:\Users\pal\projects\batt_bamm\outputs\external_parameter_tune\arbitrary_waveform_verify
```

本轮试跑已生成：

- `outputs/external_parameter_tune/arbitrary_waveform_verify/external_fit_summary.json`
- `outputs/external_parameter_tune/arbitrary_waveform_verify/case_diagnostics.csv`
- `outputs/external_parameter_tune/arbitrary_waveform_verify/fit_acceptance_report.md`
- `outputs/external_parameter_tune/arbitrary_waveform_verify/ecm_fit/ecm_fitted_pack_temp_2d_2rc.json`
- `outputs/external_parameter_tune/arbitrary_waveform_verify/dfn_micro_tune/dfn_fitted_config.yaml`

本轮试跑结果：

- `passed=true`
- ECM：已尝试并通过，输出 `2RC` 拟合包，未发生 1RC 降级。
- DFN：已尝试并通过，输出 `dfn_fitted_config.yaml`。
- `case_diagnostics.csv` 只有表头，表示没有输入校验或拟合阶段诊断项。

如果流程失败，优先查看：

- `case_diagnostics.csv`：输入缺列、非数值、时间重复、电压异常、拟合降级等可行动提示。
- `external_fit_summary.json`：整体 `passed/stop_reason`、ECM/DFN 分阶段状态和关键产物路径。
- `fit_acceptance_report.md`：面向人工排查的汇总报告。

注意：任意波形可以跑，但不代表任意波形都能可靠识别参数。若电流变化太少、SOC 覆盖太窄、缺少低电流或静置段，ECM 的 RC 参数会弱可辨识，报告可能通过但结果只能作为接口验证或低可信初值。高质量 ECM/DFN 参数仍建议使用包含阶跃、静置和足够 SOC 覆盖的测试数据。

## 运行命令

```powershell
cd C:\Users\pal\pyenv\colab
$env:PYTHONPATH='C:\Users\pal\projects\batt_bamm\src'
pipenv run python -m batt_bamm.external_parameter_tune `
  --config C:\Users\pal\projects\batt_bamm\configs\setups\external_parameter_tune_example.yaml
```

关键输出：

- `external_fit_summary.json`
- `case_diagnostics.csv`
- `fit_acceptance_report.md`
- `ecm_fit/ecm_fitted_pack_temp_2d_2rc.json`
- `dfn_micro_tune/dfn_fitted_config.yaml`
