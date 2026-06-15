# 能力矩阵

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
| 外部测试数据参数调优 | 独立运行器 | `external_parameter_tune` 配置 + manifest | 校验外部 `time_s/current_a/voltage_v` 数据，默认先拟合 ECM，再做 DFN 初始 SOC/容量小范围微调，并输出失败诊断 | `external_fit_summary.json`, `case_diagnostics.csv`, `fit_acceptance_report.md`, `ecm_fitted_pack_temp_2d*.json`, `dfn_fitted_config.yaml` | 数据校验、ECM 或 DFN 失败时保留可行动提示；ECM 失败默认不继续 DFN | `TestExternalParameterTune` | 完成 |
| 温度相关参数增强 | baseline/hppc/timeseries/benchmark + 拟合运行器 | `model.temperature_dependence.*`, `model.ecm_fitted_pack_json` | DFN Arrhenius 覆盖（可选）+ ECM SOC×temperature R/C 插值 | `parameter_audit.json` + 温度 2D 拟合包产物 | 旧版 1D ECM 包快速失败 | `TestEcmFitCompare`, baseline 测试 | 完成 |
| 识别输入模板校验 | 全模式 | `identification_inputs.*` | 在不拟合的情况下校验 OCV/CC/HPPC 模板输入 | `summary.identification_inputs_validation` | strict 模式将运行标记失败但不崩溃 | `TestIdentificationInputValidation` | 完成 |
