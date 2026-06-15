# CH-NCM/ECM 虚拟充放电测试数据

## 数据目的

本目录保存基于已锁定 CH-NCM/ECM 单体参数生成的虚拟充放电时序数据。数据用于仿真流程冒烟测试、字段契约验证和后续批量样例生成，不代表真实车队原始统计比例。

## 仿真条件

- 电芯参数：`configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml` 与 `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/ecm_fitted_pack_temp_2d_2rc.json`。
- 电芯数量：11 个虚拟单体，参数分散性来自现有 `series_pack` OCV/R0/容量分散性逻辑。
- 温度：固定 298.15 K，不考虑温度影响。
- 时间步长：0.1 s。
- 直流充电：50 组，2C，CC+CV，起始 SOC 10%-30%，目标 SOC 90%-98%。
- 交流充电：50 组，0.07C，CC+CV，起始 SOC 8%-20%，目标 SOC 95%-100%。
- 放电连接工况：`cltc_p, wltc_class3b, udds, ftp75, hwfet, nedc`，每个充电样例前随机选择一条工况并循环至目标起始 SOC。
- 高 SOC 预处理：若放电前 SOC >95%，先以 0.5C 放电到 95%。
- 随机种子：20260513。
- cell11 扩展：10 组交流充电，起始 SOC 随机生成，截止 SOC 从既有交流充电样例分布中抽样，交流充电之间用电芯级整车放电工况随机循环连接。

## 文件说明

- `metadata/charge_behavior_sources.json`：中国大陆充电行为公开资料、采用假设和与 50/50 测试口径的偏离说明。
- `metadata/charge_samples.csv`：充电样例，包含电芯编号、样例顺序、起始 SOC、目标 SOC、充电类型和倍率。
- `metadata/cell_parameter_manifest.json`：虚拟电芯的 OCV/R0/容量分散性和基础曲线。
- `metadata/cycle_selection_log.csv`：已仿真电芯中每个放电连接段选用的工况、循环次数和截断点。
- `metadata/run_summary.json`：本轮已完成电芯、行数、SOC/电压范围与执行方式摘要。
- `smoke/cell_001_timeseries.csv`：1 号电芯冒烟测试时序。
- `cells/cell_XXX_timeseries.csv`：批量执行后的独立电芯时序。
- 本数据目录固定为 `data/ch_ncm_ecm_virtual_charge_discharge`，目录名不带日期；不生成 `cells/all_cells_wide_timeseries.csv` 宽表。

## 输出字段

- `cell_id`：电芯编号，1-11。
- `time_s`：电芯独立时间，单位秒，0.1 秒间隔。
- `ocv_v`：电芯 OCV，按 0.0005 V 精度记录。
- `soc`：电芯 SOC，按 0.001 精度记录。
- `current_a`：电芯电流，按 0.01 A 精度记录；正值表示放电，负值表示充电。
- `terminal_voltage_v`：电芯端电压，按 0.0005 V 精度记录。
- `condition_type`：`ac_chg`、`dc_chg`、`drive`。

## 当前执行状态

已仿真电芯：1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11。
