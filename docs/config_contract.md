# 配置契约

## 配置目录约定

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

## 模型与化学体系

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

## 截止条件

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

## Benchmark 与质量门禁

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

## 发布基线 Benchmark

`configs/setups/benchmark_release_matrix.yaml` 是用于 P0 验收的专用可复现矩阵：
- `profiles` 固定为 `dfn_nmc, dfn_lfp, ecm_nmc, ecm_lfp`
- `rates_c` 固定为 `[0.2, 1.0]`
- `repeats` 固定为 `2`
- 质量门禁启用且强制执行

## 识别模板校验

```yaml
identification_inputs:
  enabled: true
  strict: true
  ocv_points_csv: inputs/templates/ocv_points_template.csv
  cc_cycle_csv: inputs/templates/cc_cycle_template.csv
  hppc_points_csv: inputs/templates/hppc_points_template.csv
```

## lumped 模式下的 timeseries 热边界

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

## timeseries 下的近似 SOC 切换循环

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

## HPPC 对比基线配置

用于 150Ah NMC 的 DFN/ECM 对比：
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn.yaml`
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm.yaml`

两者共同使用：
- `hppc.soc_start: 1.0`
- `hppc.soc_end: 0.0`
- `hppc.soc_step: 0.05`

## 热评估矩阵配置

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

## LFP 热参数辨识（round-1）

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

## ECM SOC×Temperature 拟合包规范（必需）

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

## 模板输入

模板文件位于 `inputs/templates/`：
- `ocv_points_template.csv`，列：`soc,ocv_v,temp_k`
- `cc_cycle_template.csv`，列：`time_s,current_a,voltage_v,temp_k`
- `hppc_points_template.csv`，列：`soc_target,r_dis_10s_ohm,r_chg_10s_ohm,temp_k`
