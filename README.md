# batt_bamm

基于 PyBaMM 的电池仿真项目。本 README 是当前能力、输出契约与路线图的唯一事实来源。

## 1. 项目目标与范围

### 1.1 目标
构建一个电池数据仿真平台，为研发与策略迭代提供可复现、可比较的输出结果。

当前范围：
- 电化学路线：DFN
- ECM 路线：Thevenin
- 化学体系覆盖：NMC 与 LFP

### 1.2 非目标
- 当前阶段不面向生产级车载闭环控制。
- 不追求一次性覆盖全部化学体系与全部老化机理。
- 当前阶段关注相对可信、可比较、可复现，而非实验室级绝对精度。

## 2. 建模假设

1. 初始条件路径仅支持 SOC（`initial_soc`），暂不支持 `initial_ocv -> soc` 反演。
2. 基线参数包：
- NMC：`Chen2020`
- LFP：`Prada2013`（用于流程闭环的代理）
- ECM：`ECM_Example`
   - `parameter_audit.json` 会暴露 `parameter_pack.quality_level`（`proxy|identified`）
3. 容量迁移：
- 按目标 `nominal_capacity_ah` 缩放。
- 对 DFN 而言，若存在并联极片数量参数，则按同一比例缩放。
4. 热模型支持 `isothermal | lumped`。
   - 对 `Prada2013 + lumped`，运行时会从 `Chen2020` 自动补齐缺失的热材料/集流体字段作为代理值，并写入 `parameter_audit.json -> thermal_proxy_overrides`。
5. DFN 温度依赖：
- 默认保留参数包自带的温度相关函数。
- 可通过 `model.temperature_dependence.dfn.*` 配置可选 Arrhenius 覆盖，覆盖项包括：
  - 负/正极颗粒扩散系数
  - 负/正极交换电流密度
6. ECM 温度依赖：
- ECM 拟合包格式现严格要求为 `ecm_temp_2d_v1`。
- `R/C` 采用 `SOC x temperature`（2D）插值。
- `OCV` 在本阶段仍保持仅 SOC 依赖。
7. 统一温度输出：
- `cell_temperature_k`：电芯本体温度轨迹。
- `boundary_temperature_k`：热边界温度轨迹。
8. `timeseries.temp_k` 行为：
- 默认：输入透传。
- 当 `thermal=lumped` 且 `model.thermal_coupling.enabled=true` 且 `boundary_mode=timeseries` 时，作为随时间变化的环境边界温度。
- 兼容旧配置：`timeseries.use_temp_as_ambient_boundary=true` 仍可使用，并映射到同一行为。
9. 截止条件基于采样点，使用 `any_of` 逻辑。

## 3. 快速开始

### 3.1 环境
- 仓库：`C:\Users\pal\projects\batt_bamm`
- 推荐 Python 环境：`C:\Users\pal\pyenv\colab`

### 3.2 CLI

```powershell
pipenv run python -m batt_bamm.main --config <yaml_path> --mode <baseline|hppc|timeseries|benchmark>
```

### 3.3 常用命令

```powershell
# NMC DFN baseline
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode baseline

# LFP DFN baseline
pipenv run python -m batt_bamm.main --config configs/cells/lfp_130ah/baseline_130ah_lfp.yaml --mode baseline

# NMC ECM baseline
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc_ecm.yaml --mode baseline

# LFP ECM baseline (proxy)
pipenv run python -m batt_bamm.main --config configs/cells/lfp_130ah/baseline_130ah_lfp_ecm.yaml --mode baseline

# HPPC
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode hppc

# Timeseries replay / charge_compare sub-flow
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode timeseries

# Approx SOC-switch cycle (99% -> 30% -> 90%, 1C/1C, lumped thermal)
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/timeseries_soc_switch_approx_99to30to90_150ah_nmc622.yaml --mode timeseries

# Benchmark matrix + quality gate
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode benchmark

# Release-candidate benchmark baseline (fixed matrix)
pipenv run python -m batt_bamm.main --config configs/setups/benchmark_release_matrix.yaml --mode benchmark

# Thermal-eval matrix (150Ah NMC, 7 cases)
pipenv run python -m batt_bamm.thermal_eval --config configs/setups/thermal_eval_150ah_nmc.yaml

# Thermal-eval matrix (130Ah LFP, 7 cases)
pipenv run python -m batt_bamm.thermal_eval --config configs/setups/thermal_eval_130ah_lfp.yaml

# LFP thermal eval (short regression window)
pipenv run python -m batt_bamm.thermal_eval --config configs/setups/thermal_eval_130ah_lfp_short_regression.yaml

# LFP thermal eval (long representative cases)
pipenv run python -m batt_bamm.thermal_eval --config configs/setups/thermal_eval_130ah_lfp_long_representative.yaml

# LFP thermal identification round-1 (h / heat-capacity scale / conductivity scale)
pipenv run python -m batt_bamm.lfp_thermal_identify --config configs/setups/thermal_identify_130ah_lfp_round1.yaml

# DFN vs ECM HPPC compare (100% -> 0% SOC, 5% step)
pipenv run python -m batt_bamm.hppc_compare `
  --dfn-config configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn.yaml `
  --ecm-config configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm.yaml `
  --output-dir outputs/hppc_compare_150ah_nmc/compare

# DFN-driven ECM fit + compare (2RC target gate)
pipenv run python -m batt_bamm.ecm_fit_compare `
  --dfn-config configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn_99to1.yaml `
  --ecm-config configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm_99to1_2rc.yaml `
  --output-dir outputs/ecm_fit_compare_150ah_nmc_99to1_2rc `
  --ecm-order 2 `
  --loss-dynamic-weight 0.7 `
  --fit-temperature-grid-c -10 25 45 `
  --gate-profile target `
  --improve-threshold 0.2
```

## 4. 能力矩阵

| 能力 | 模式入口 | 关键配置 | 核心行为 | 产物 | 失败语义 | 测试覆盖 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 参数迁移与审计 | baseline/hppc/timeseries/benchmark | `nominal_capacity_ah`, `parameter_set`, `model.type`, `chemistry` | 加载参数值并写出迁移审计 | `parameter_audit.json` | 参数配置非法时失败 | `TestBaselinePipeline`, `TestModelChemistryCoverage` | 完成 |
| 健全性门禁 | baseline | `sanity_gate.*` | 快速充放电检查双向电流与 `infeasible/skip` 警告 | `sanity_gate.csv/json` | 快速失败并阻断批量运行 | baseline 测试 | 完成 |
| 基线批量流程 | baseline | `discharge_rates_c`, `charge_cc_rate`, `cv_cutoff_c_rate` | 放电 -> 静置 -> CC 充电 -> CV 保压 -> 静置 | `case_*.csv`, `voltage_overlay.png`, `summary.json` | 任一 case 失败则 `all_converged=false` | baseline 测试 | 完成 |
| HPPC SOC 扫描 | hppc | `hppc.*` | SOC 点脉冲仿真并提取 10s 电阻 | `hppc_point_soc_*.csv`, `hppc_summary.csv/json`, `hppc_voltage_overlay.png` | 点级快速失败 | HPPC 测试 | 完成 |
| Timeseries 回放核心 | timeseries | `timeseries.csv_path` | 回放 `time_s/current_a/temp_k`，输出统一时序结果 | `timeseries_output.csv`, `timeseries_summary.json` | 输入校验或求解失败时快速失败 | timeseries 测试 | 完成 |
| 近似 SOC 切换循环 | timeseries 子流程 | `timeseries.soc_switch_approx.*` | 从 SOC 目标生成两阶段电流并使用同一内核回放 | `soc_switch_approx_input.csv`, `timeseries_output.csv`, `timeseries_summary.json` | SOC 边界或配置冲突时快速失败 | timeseries 测试 | 完成 |
| 多倍率 CC-CV 对比 | timeseries 子流程 | `timeseries.charge_compare.*` | 多倍率运行 CC-CV 并汇总 CC/CV 时长 | `charge_case_*.csv`, `charge_compare_summary.csv/json`, `charge_compare_overlay.png` | 允许 case 级失败，任务级 `all_converged=false` | charge_对比测试 | 完成 |
| 通用截止引擎 | 全模式 | `termination.*` | `any_of` 命中后截断输出并记录命中明细 | case summary 内的 `termination` | 若 `must_hit=true` 且未命中则标记失败 | `termination` 测试 | 完成 |
| 热耦合（阶段 1） | 全模式 | `model.thermal`, `model.thermal_coupling.*`, `model.thermal_params.*` | `isothermal/lumped` 切换、统一热边界策略、可选热参数覆盖、输出温度轨迹 | `cell_temperature_k`, `boundary_temperature_k` | 热选项非法时快速失败 | lumped tests | 阶段 1 完成 |
| ECM 路线集成 | baseline/timeseries/benchmark | `model.type=ecm` | Thevenin 路线并复用统一输出契约 | 各模式产物 | 各模式语义 | ECM 冒烟测试 | 阶段 1 完成 |
| LFP 路线集成 | baseline/hppc/timeseries/benchmark | `chemistry=lfp` | LFP 基线流程（代理参数） | 各模式产物 | 各模式语义 | LFP 冒烟测试 | 阶段 1 完成 |
| Benchmark 矩阵与门禁 | benchmark | `benchmark.*`, `quality_gate.*` | 运行固定工况矩阵并评估收敛、重复性、趋势 | `benchmark_matrix.csv`, `benchmark_summary.json`, `benchmark_compare_report.md`, `summary.json` | `enforce=true` 时门禁失败会导致整体失败 | `TestBenchmarkPipeline` | 完成 |
| 热评估矩阵 | 独立运行器 | `thermal_eval` 配置 + `initial_cell_temp_k` | 批量 timeseries 回放做热评估，默认 OR 截止 | `thermal_case_*.csv` (K), `thermal_eval_summary.csv/json` (°C), `thermal_eval_manifest.json`, `thermal_eval_temperature_overlay.png` (°C) | 逐样本记录状态；仅当全部收敛时 `passed=true` | `TestThermalEval` | 完成 |
| LFP 热参数辨识（round-1） | 独立运行器 | `thermal_identify_130ah_lfp_round1.yaml` | 用短窗口残差拟合 `h / heat_capacity_scale / thermal_conductivity_scale`，并在长窗口验证 | `lfp_thermal_ident_summary.json`, `lfp_thermal_ident_trials.csv`, `configs/cells/lfp_130ah/thermal_identified_round1.yaml` | 目标数据缺失时快速失败（除非启用 bootstrap） | 热参数辨识冒烟测试 | 完成 |
| DFN vs ECM HPPC 对比 | 独立运行器 | 两个 HPPC 配置 + 输出目录 | 分别运行 DFN/ECM HPPC，按 SOC 对齐并计算端电压差 | `hppc_compare_by_soc.csv`, `hppc_compare_summary.json`, `hppc_compare_voltage_delta.png`, `hppc_compare_report.md` | 任一侧失败或 SOC 网格不对齐则对比失败 | 对比测试 | 完成 |
| DFN 驱动 ECM 拟合（1RC/2RC，多温区） | 独立运行器 | `ecm_fit_compare` CLI 选项 | 在 `-10/25/45°C` 运行 DFN HPPC，拟合 ECM，回放对比并评估增益/门禁 | `ecm_fitted_pack_temp_2d*.json`, `ecm_fit_points_temp_2d*.csv`, `ecm_fit_compare_summary*.json`, `ecm_fit_compare_report*.md` | 任一步失败或门禁未通过返回非零退出码 | `TestEcmFitCompare` | 完成 |
| 温度相关参数增强 | baseline/hppc/timeseries/benchmark + 拟合运行器 | `model.temperature_dependence.*`, `model.ecm_fitted_pack_json` | DFN Arrhenius 覆盖（可选）+ ECM SOC×temperature R/C 插值 | `parameter_audit.json` + 温度 2D 拟合包产物 | 旧版 1D ECM 包快速失败 | `TestEcmFitCompare`, baseline 测试 | 完成 |
| 识别输入模板校验 | 全模式 | `identification_inputs.*` | 在不拟合的情况下校验 OCV/CC/HPPC 模板输入 | `summary.identification_inputs_validation` | strict 模式将运行标记失败但不崩溃 | `TestIdentificationInputValidation` | 完成 |

## 5. 输出契约

### 5.1 顶层 `summary.json`

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

### 5.2 RunSummary（通用样本摘要）

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

### 5.2a 统一 timeseries 输出列（CSV）

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

### 5.3 Benchmark 块（`summary.benchmark`）

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

### 5.4 质量门禁块（`summary.quality_gate`）

必需字段：
- `enabled`
- `enforce`
- `passed`
- `thresholds`
- `metrics`

### 5.5 识别输入校验块（`summary.identification_inputs_validation`）

必需字段：
- `enabled`
- `strict`
- `passed`
- `datasets`
- `errors`

### 5.6 HPPC 对比摘要（`hppc_compare_summary.json`）

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

## 6. 配置契约

### 6.0 配置目录约定

`configs` 按电芯规格与配置用途组织：

- 绑定电芯参数包的仿真配置：`configs/cells/<cell_spec>/...`
- 不绑定特定电芯参数包的 setup/matrix 配置：`configs/setups/...`

迁移映射（旧 -> 新）：

| 旧路径 | 新路径 |
| --- | --- |
| `configs/baseline_150ah_nmc622.yaml` | `configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml` |
| `configs/baseline_150ah_nmc_ecm.yaml` | `configs/cells/nmc622_150ah/baseline_150ah_nmc_ecm.yaml` |
| `configs/hppc_compare_150ah_nmc_dfn.yaml` | `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn.yaml` |
| `configs/hppc_compare_150ah_nmc_dfn_99to1.yaml` | `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn_99to1.yaml` |
| `configs/hppc_compare_150ah_nmc_ecm.yaml` | `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm.yaml` |
| `configs/hppc_compare_150ah_nmc_ecm_99to1.yaml` | `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm_99to1.yaml` |
| `configs/hppc_compare_150ah_nmc_ecm_99to1_2rc.yaml` | `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm_99to1_2rc.yaml` |
| `configs/ecm_2rc_params_vs_soc_150ah_nmc_99to1.csv` | `configs/cells/nmc622_150ah/ecm_2rc_params_vs_soc_150ah_nmc_99to1.csv` |
| `configs/baseline_130ah_lfp.yaml` | `configs/cells/lfp_130ah/baseline_130ah_lfp.yaml` |
| `configs/baseline_130ah_lfp_ecm.yaml` | `configs/cells/lfp_130ah/baseline_130ah_lfp_ecm.yaml` |
| `configs/benchmark_release_matrix.yaml` | `configs/setups/benchmark_release_matrix.yaml` |

### 6.1 模型与化学体系

```yaml
model:
  type: dfn          # dfn|ecm
  thermal: isothermal # isothermal|lumped
  thermal_coupling:
    enabled: false
    boundary_mode: constant # constant|timeseries
  thermal_params:
    total_heat_transfer_coefficient_w_m2_k: 120.0 # optional, >0
    cell_volume_m3: 0.000726                      # optional, >0
    cell_cooling_surface_area_m2: 0.0512674863   # optional, >0
  temperature_dependence:
    dfn:
      enabled: false
      reference_temp_k: 298.15
      arrhenius_overrides:
        negative_particle_diffusivity_ea_j_mol: 35000.0
        positive_particle_diffusivity_ea_j_mol: 30000.0
        negative_exchange_current_ea_j_mol: 25000.0
        positive_exchange_current_ea_j_mol: 28000.0
  ecm_rc_elements: 1  # 1|2 (only for model.type=ecm)
  ecm_fitted_pack_json: "" # optional, requires schema_version=ecm_temp_2d_v1
chemistry: nmc       # nmc|lfp
initial_cell_temp_k: 298.15 # optional, defaults to ambient_temp_k
```

说明：
- `model.thermal_params.*` 仅在 `model.type=dfn` 且 `model.thermal=lumped` 时生效。
- 超出该范围时，运行时保留参数包内热参数并给出警告。
- `model.temperature_dependence.dfn.enabled=false` 时，保留参数包原始行为。
- 启用 Arrhenius 覆盖后，会按温度缩放目标 DFN 参数，同时保留原始基函数。
- `model.ecm_fitted_pack_json` 不再接受旧版 1D pack。

### 6.2 截止条件

```yaml
termination:
  enabled: true
  logic: any_of
  must_hit: false
  apply_to_experiment_modes: true
  conditions:
    - metric: voltage_v   # time_s|voltage_v|soc|current_abs_a|cell_temperature_k|boundary_temperature_k|ocv_v
      op: "<="           # <=|>=
      threshold: 2.8
      name: stop_at_low_v
    - metric: cell_temperature_k
      op: ">="
      threshold: 333.15
      name: cell_temp_limit_60c
    - metric: boundary_temperature_k
      op: ">="
      threshold: 323.15
      name: boundary_temp_limit_50c
```

### 6.3 Benchmark 与质量门禁

```yaml
benchmark:
  enabled: true
  rates_c: [0.2, 1.0]
  repeats: 2
  rest_min: 5
  charge_cc_rate: 0.5
  cv_cutoff_c_rate: 0.05
  period_s: 30
  profiles: [dfn_nmc, dfn_lfp, ecm_nmc, ecm_lfp]

quality_gate:
  enabled: true
  min_convergence_rate: 0.95
  max_repeat_delta_final_soc: 5.0e-4
  max_repeat_delta_min_v: 5.0e-3
  require_polarization_trend: true
  enforce: true
```

### 6.4 发布基线 Benchmark

`configs/setups/benchmark_release_matrix.yaml` 是用于 P0 验收的专用可复现矩阵：
- `profiles` 固定为 `dfn_nmc, dfn_lfp, ecm_nmc, ecm_lfp`
- `rates_c` 固定为 `[0.2, 1.0]`
- `repeats` 固定为 `2`
- 质量门禁启用且强制执行

### 6.5 识别模板校验

```yaml
identification_inputs:
  enabled: true
  strict: true
  ocv_points_csv: inputs/templates/ocv_points_template.csv
  cc_cycle_csv: inputs/templates/cc_cycle_template.csv
  hppc_points_csv: inputs/templates/hppc_points_template.csv
```

### 6.6 lumped 模式下的 timeseries 热边界

```yaml
model:
  thermal: lumped
  thermal_coupling:
    enabled: true
    boundary_mode: timeseries

timeseries:
  enabled: true
  csv_path: path/to/input.csv
```

说明：
- 在 `baseline/hppc/benchmark/charge_compare` 下使用 `boundary_mode=timeseries` 时，没有显式温度序列输入；运行时将回退到 `ambient_temp_k` 并给出警告。
- 旧配置 `timeseries.use_temp_as_ambient_boundary=true` 保持向后兼容。

### 6.7 timeseries 下的近似 SOC 切换循环

```yaml
timeseries:
  enabled: true
  csv_path: ""
  soc_switch_approx:
    enabled: true
    soc_start: 0.99
    discharge_rate_c: 1.0
    discharge_to_soc: 0.30
    charge_rate_c: 1.0
    charge_to_soc: 0.90
    period_s: 0.1
    temp_k: 298.15
  charge_compare:
    enabled: false
```

说明：
- `soc_switch_approx` 与 `charge_compare` 不能同时启用。
- 该流程为按时间近似的快速路径，并非严格 SOC 事件控制。

### 6.8 HPPC 对比基线配置

用于 150Ah NMC 的 DFN/ECM 对比：
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn.yaml`
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm.yaml`

两者共同使用：
- `hppc.soc_start: 1.0`
- `hppc.soc_end: 0.0`
- `hppc.soc_step: 0.05`

### 6.9 热评估矩阵配置

`configs/setups/thermal_eval_150ah_nmc.yaml` 以及 LFP 分层配置：
- `configs/setups/thermal_eval_130ah_lfp.yaml`（完整 7 样本矩阵）
- `configs/setups/thermal_eval_130ah_lfp_short_regression.yaml`（短稳态回归窗口）
- `configs/setups/thermal_eval_130ah_lfp_long_representative.yaml`（少量长代表工况）

公共字段：
- `base_config_path`
- `sampling_period_s`
- `output_dir`
- `cases[]`，包含 `case_id, ambient_temp_c|ambient_temp_k, initial_cell_temp_c|initial_cell_temp_k, soc_start, soc_end, rate_c`

热评估产物：
- `thermal_case_<case_id>.csv`（温度列保持开尔文：`cell_temperature_k`, `boundary_temperature_k`）
- `thermal_eval_summary.csv`（温度字段为摄氏度并带 `_c` 后缀）
- `thermal_eval_summary.json`（包含 `temperature_unit: "C"`）
- `thermal_eval_manifest.json`
- `thermal_eval_temperature_overlay.png`（Y 轴单位 `°C`）

热评估摘要温度字段：
- `ambient_temp_c`
- `initial_cell_temp_c`
- `max_cell_temperature_c`
- `max_boundary_temperature_c`

### 6.10 LFP 热参数辨识（round-1）

`configs/setups/thermal_identify_130ah_lfp_round1.yaml`：
- `base_config_path`
- `short_eval_config_path`, `long_eval_config_path`
- `target.short_summary_json`, `target.long_summary_json`, `target.bootstrap_if_missing`
- `fit.max_nfev`
- `fit.initial_guess/lower_bounds/upper_bounds`，对应
  - `total_heat_transfer_coefficient_w_m2_k`
  - `heat_capacity_scale`
  - `thermal_conductivity_scale`
- `fit.weights.cell_temperature`, `fit.weights.boundary_temperature`

round-1 产物：
- `outputs/thermal_identify_130ah_lfp_round1/lfp_thermal_ident_summary.json`
- `outputs/thermal_identify_130ah_lfp_round1/lfp_thermal_ident_trials.csv`
- `configs/cells/lfp_130ah/thermal_identified_round1.yaml`

### 6.11 ECM SOC×Temperature 拟合包规范（必需）

不再支持旧版 1D ECM 拟合包。

JSON 必需字段：
- `schema_version: "ecm_temp_2d_v1"`
- `ecm_order: 1|2`
- `soc_axis`：升序 1D 数组
- `temp_c_axis`：升序 1D 数组
- `ocv_v`：1D `[soc]` 数组
- `r0_ohm_map`, `r1_ohm_map`, `c1_f_map`：2D `[temp, soc]` 数组
- `r2_ohm_map`, `c2_f_map`：当 `ecm_order=2` 时必需，形状为 `[temp, soc]`

运行时行为：
- OCV 插值仅使用 SOC。
- R/C 插值使用二维 `temperature(°C) x SOC`。
- `schema`、形状或阶数非法时，快速失败并返回显式错误。

示例配置：
- `configs/cells/nmc622_150ah/baseline_150ah_nmc622_temp_dep_example.yaml`
- `configs/cells/lfp_130ah/baseline_130ah_lfp_ecm_temp_dep_example.yaml`

## 7. 模板输入

模板文件位于 `inputs/templates/`：
- `ocv_points_template.csv`，列：`soc,ocv_v,temp_k`
- `cc_cycle_template.csv`，列：`time_s,current_a,voltage_v,temp_k`
- `hppc_points_template.csv`，列：`soc_target,r_dis_10s_ohm,r_chg_10s_ohm,temp_k`

## 8. 路线图

### P0
- 稳定 ECM 与 DFN 在标准流程上的一致性。
- 将 LFP 代理参数替换为数据识别参数包。
- 提升截止条件诊断能力。

### P1
- 扩展热边界策略与热参数标定能力。
- 基于 OCV / 0.5C / HPPC 数据集构建闭环参数识别。

### P2
- 增加 `initial_ocv -> soc` 反演。
- 增加控制器风格 CCCV。
- 引入老化与副反应模型。

## 9. 产物治理与维护

1. 仓库内仅保留脚本、配置与最小可复现输入。
2. 大体量仿真产物迁移到发布包、对象存储或按需上传位置。
3. 任何新增 mode/field/artifact 必须在同一变更中更新本 README。
4. 新增能力必须至少包含一条自动化测试，覆盖冒烟、契约与失败语义。

