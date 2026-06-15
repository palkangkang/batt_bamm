# 配置与参数目录说明

本文说明 `configs/` 下当前各类配置、参数包和锁定输入的用途。除运行输出外，可复用或锁定的参数不应放在 `outputs/` 下。

## 目录边界原则

`configs/` 下的文件分为两类：一类是可直接作为运行入口的配置，另一类是被运行入口引用的参数资产、工况输入或索引。重构时优先保持这两类边界清楚，而不是把相关文件全部合并到同一个目录。

| 目录 | 语义 | 是否通常作为运行入口 |
| --- | --- | --- |
| `configs/cells/` | 单体级可运行配置入口 | 是 |
| `configs/packs/` | pack 场景、pack 级一致性、故障注入和锁定工况输入 | 是，限 YAML 配置 |
| `configs/setups/` | 外部拟合、热评估、benchmark 等专项任务入口 | 是 |
| `configs/parameter_packs/` | 可复用参数资产、锁定指标、诊断索引 | 否 |
| `configs/vehicles/` | 车辆规格和道路负载参数 | 否，作为生成或仿真输入 |

轻量机器可读索引见 `configs/manifest.json`。该索引只描述主要入口和资产关系，不替代各 YAML/JSON/CSV 文件本身。

## 总览


| 类别     | 路径                                             | 状态      | 主要用途                       | 下游引用                                                |
| ------ | ---------------------------------------------- | ------- | -------------------------- | --------------------------------------------------- |
| 电芯配置   | `configs/cells/ch_ncm/`                        | 当前主配置   | CH-NCM 电芯基线、ECM、时间序列配置     | `batt_bamm.main`、拟合与验证脚本                            |
| 锁定电芯配置 | `configs/cells/ch_ncm_locked/`                 | 已锁定     | CH-NCM locked ECM 运行配置     | `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/` |
| 参数包    | `configs/parameter_packs/ch_ncm/`              | 可复用/已锁定 | CH-NCM ECM 参数包与串联包参数来源 | 电芯配置、电池组配置、功率负载脚本                                   |
| 电池组配置  | `configs/packs/ch_ncm/ecm_leakage_series_168/` | 已锁定     | 168 串带泄露电流电池组配置与工况索引       | 串联包仿真脚本                                             |
| 车辆规格   | `configs/vehicles/`                            | 可复用     | 车辆道路负载和功率换算参数              | 工况功率生成脚本                                            |
| 运行输出   | `outputs/`                                     | 非参数     | 仿真结果、图表、临时分析、历史验证          | 不作为锁定参数来源                                           |


## 电芯配置


| 路径                             | 文件/目录                    | 状态    | 含义                   | 关键引用                                                                                        |
| ------------------------------ | ------------------------ | ----- | -------------------- | ------------------------------------------------------------------------------------------- |
| `configs/cells/ch_ncm/`        | `ecm_ch_ncm.yaml`        | 对照配置 | CH-NCM ECM 回放/对照配置    | 引用历史拟合输出 `outputs/ch_ncm_parameter_fit/tune/ecm_fit/ecm_fitted_pack_temp_2d_2rc.json`；不作为 locked 基线 |
| `configs/cells/ch_ncm/`        | `baseline_ch_ncm.yaml`   | 当前主配置 | CH-NCM 基线仿真配置        | 用于 baseline 工作流                                                                             |
| `configs/cells/ch_ncm/`        | `timeseries_ch_ncm.yaml` | 当前主配置 | CH-NCM 时间序列仿真配置      | 用于 timeseries 工作流                                                                           |
| `configs/cells/ch_ncm/`        | `fit_summary_index.json` | 当前索引  | CH-NCM 参数拟合摘要索引      | 记录拟合产物和配置路径                                                                                 |
| `configs/cells/ch_ncm_locked/` | `ecm_ch_ncm_locked.yaml` | 已锁定   | CH-NCM locked ECM 配置 | 引用 `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/ecm_fitted_pack_temp_2d_2rc.json`      |


## 参数包


| 路径                                                       | 文件/目录                              | 状态    | 含义                                                           | 注意事项                    |
| -------------------------------------------------------- | ---------------------------------- | ----- | ------------------------------------------------------------ | ----------------------- |
| `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`      | `ecm_fitted_pack_temp_2d_2rc.json` | 已锁定   | CH-NCM Thevenin 2RC ECM 参数包，包含 OCV、R0/R1/R2、C1/C2 的 SOC/温度映射 | 当前 locked CH-NCM ECM 基线 |
| `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`      | `locked_index.json`                | 已锁定   | locked ECM 参数包机器可读索引                                         | 记录锁定包、配置、指标文件           |
| `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`      | `locked_metrics_summary.json`      | 已锁定   | 锁定前验证指标与图表索引                                                 | 用于追溯锁定质量                |
| `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`      | `soc_shift_map.json`               | 诊断用   | 评估和诊断可视化的 SOC 对齐量                                            | 不属于物理 ECM 参数            |
| `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`      | `README.md`                        | 说明文件  | locked ECM 参数包说明                                             | 本地 Markdown 使用中文维护      |
| `configs/parameter_packs/ch_ncm/segment_layered_r0_ecm/` | `ecm_fitted_pack_temp_2d_2rc.json` | 当前识别包 | 当前 CH-NCM 主配置使用的分段 R0 Thevenin 2RC ECM 参数包                   | 未标记为 locked，但为当前主配置引用   |
| `configs/parameter_packs/ch_ncm/segment_layered_r0_ecm/` | 其它 JSON/CSV                        | 过程产物  | 参数包生成过程的摘要、指标或中间表                                            | 用于追溯拟合来源                |

注：原 `configs/parameter_packs/ch_ncm/ecm_leakage_series_168/base_ecm_example_pack_1rc.json` 已移入回收站，不再作为当前 `configs` 参数资产维护；168 串 locked pack 当前引用 locked CH-NCM 2RC 参数包。


## 串联电池组配置


| 路径                                                                               | 文件/目录                           | 状态  | 含义                              | 包含内容                             |
| -------------------------------------------------------------------------------- | ------------------------------- | --- | ------------------------------- | -------------------------------- |
| `configs/packs/ch_ncm/ecm_leakage_series_168/`                                   | `ch_ncm_ecm_leakage_168s.yaml`  | 已锁定 | 168 串 NCM/ECM 带泄露电流电池组配置        | 串数、连接方式、泄露电流、OCV/R0/初始 SOC/容量分散性 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/`                                   | `locked_cycle_power_index.json` | 已锁定 | 该电池组锁定的车辆规格和功率需求曲线索引 | 指向车辆规格、CLTC/WLTC、EPA 5-cycle、UDDS、NEDC、US06 等工况曲线 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | 目录                              | 已锁定 | 锁定到该电池组下的功率工况曲线副本               | CLTC-P、WLTC Class 3b、UDDS、FTP-75、Cold FTP-75、HWFET、US06、SC03、NEDC |


## 车辆规格


| 路径                  | 文件                                      | 状态   | 含义                   | 包含内容                                |
| ------------------- | --------------------------------------- | ---- | -------------------- | ----------------------------------- |
| `configs/vehicles/` | `flagship_5seat_sedan.yaml`             | 可复用  | 旗舰 5 座纯电轿车代表规格       | 质量、风阻、迎风面积、滚阻、驱动效率、回收效率、附件功率        |
| `configs/vehicles/` | `flagship_5seat_sedan.locked_copy.yaml` | 迁移审计 | 从早期电池组锁定目录迁移出的车辆规格副本 | 后续应以 `flagship_5seat_sedan.yaml` 为主 |


## 锁定功率工况


| 路径                                                                               | 文件                               | 状态  | 含义                      | 输入列/内容                                            |
| -------------------------------------------------------------------------------- | -------------------------------- | --- | ----------------------- | ------------------------------------------------- |
| `configs/packs/ch_ncm/ecm_leakage_series_168/` | `locked_cycle_power_index.json` | 已锁定 | 功率工况锁定索引 | `cycles` 记录单条工况；`cell_power_split.series_count=168` 记录单体功率均分规则；`cycle_groups.epa_5cycle` 记录 EPA 5-cycle 组合关系 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `cltc_p_power_demand.csv`        | 已锁定 | CLTC-P 功率负载输入           | `time_s,battery_power_w,temp_k`；当前速度曲线为公开汇总指标近似生成 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `wltc_class3b_power_demand.csv`  | 已锁定 | WLTC Class 3b 功率负载输入    | `time_s,battery_power_w,temp_k`；来源为 JRCSTU/wltp 公开速度曲线 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `udds_power_demand.csv` | 已锁定 | EPA UDDS 城市工况功率负载输入 | `time_s,battery_power_w,temp_k`；来源为 EPA 官方速度曲线 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `ftp75_power_demand.csv` | 已锁定 | EPA FTP-75 功率负载输入 | `time_s,battery_power_w,temp_k`；EPA 5-cycle 子工况 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `cold_ftp75_power_demand.csv` | 已锁定 | EPA Cold FTP-75 功率负载输入 | `time_s,battery_power_w,temp_k`；速度曲线同 FTP-75，温度标记为 `266.48 K` |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `hwfet_power_demand.csv` | 已锁定 | EPA HWFET 高速工况功率负载输入 | `time_s,battery_power_w,temp_k`；EPA 5-cycle 子工况 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `us06_power_demand.csv` | 已锁定 | EPA US06 激烈驾驶工况功率负载输入 | `time_s,battery_power_w,temp_k`；EPA 5-cycle 子工况 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `sc03_power_demand.csv` | 已锁定 | EPA SC03 空调补充工况功率负载输入 | `time_s,battery_power_w,temp_k`；EPA 5-cycle 子工况 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `nedc_power_demand.csv` | 已锁定 | NEDC 功率负载输入 | `time_s,battery_power_w,temp_k`；由公开 NEDC 分段速度数据展开 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `*_cell_power_demand.csv` | 已锁定 | 按 168 串均分到单体电池的功率需求 | `time_s,power_w,cell_power_w,pack_battery_power_w,temp_k,series_count`；`cell_power_w=pack_battery_power_w/168`，`power_w` 为单体功率通用别名 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `*_power_profile.csv` | 已锁定 | 完整道路负载计算表 | 速度、加速度、阻力、轮端功率、电池功率等 |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `*_speed_profile.csv` | 已锁定 | 速度时序曲线 | `cycle_id,time_s,speed_kph,speed_mps,acceleration_mps2` |
| `configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/` | `*_metadata.json` | 已锁定 | 工况元数据 | 数据来源、生成方式、汇总指标、对应车辆规格和输出路径 |


## 运行输出约定


| 路径         | 定位      | 用途                       | 约束            |
| ---------- | ------- | ------------------------ | ------------- |
| `outputs/` | 运行输出    | 仿真结果、图表、临时分析、历史验证输出      | 不作为锁定参数来源     |
| `logs/`    | 会话与任务日志 | 记录 prompt/response 和任务过程 | 按天追加，使用 UTF-8 |
