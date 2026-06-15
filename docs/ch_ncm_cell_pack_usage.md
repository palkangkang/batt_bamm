# CH-NCM 电芯与 Pack 参数使用说明

## 资产范围

本仓库保存 CH-NCM locked 电芯参数、168 串 pack 参数、pack 工况输入和必要运行脚本。目录结构尽量保持相对路径稳定，便于直接复用脚本和配置。

当前 CH-NCM 相关资产包含：

- `configs/cells/ch_ncm_locked/`：CH-NCM locked ECM 电芯运行配置。
- `configs/cells/ch_ncm/`：CH-NCM 开发/对照配置。
- `configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/`：CH-NCM locked 2RC ECM 参数包和锁定指标。
- `configs/packs/ch_ncm/ecm_leakage_series_168/`：168 串 pack 配置、故障注入配置、工况索引和 9 条车辆工况 CSV/metadata。
- `configs/vehicles/`：旗舰 5 座纯电轿车代表规格。
- `scripts/`：CH-NCM 参数锁定、pack 功率负载、WLTC 全放电和工况功率生成相关脚本。
- `src/batt_bamm/` 与 `pyproject.toml`：运行上述脚本所需的最小源码和项目元数据。

仓库内通常不保留大型电芯级虚拟时序数据 `data/ch_ncm_ecm_virtual_charge_discharge/cells/*.csv`。如需 11 个虚拟单体的 0.1 s 时序数据，请按任务需要从外部产物位置恢复或重新生成。

## 电芯 locked 参数

电芯 locked 配置入口：

```powershell
configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml
```

核心参数包：

```powershell
configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/ecm_fitted_pack_temp_2d_2rc.json
```

参数含义：

- 化学体系：CH-NCM / NMC。
- 模型：Thevenin 2RC ECM。
- 基准 PyBaMM 参数集：`ECM_Example`。
- 标称容量：约 `150.031 Ah`。
- 电压窗口：`3.2 V` 到 `4.2 V`。
- 温度：等温模型，常用环境温度 `298.15 K`。
- 参数表：OCV、R0、R1/C1、R2/C2，按 SOC 和温度二维网格给出。
- 方向性：参数包包含充电/放电方向相关 RC 映射，仿真时按电流方向使用。

辅助文件：

- `locked_index.json`：locked 参数包机器可读索引。
- `locked_metrics_summary.json`：锁定验证指标和图表产物索引。
- `soc_shift_map.json`：仅用于 BMS 片段评估和诊断对齐，不是物理 ECM 参数。
- `README.md`：locked 参数包说明。

## 168 串 Pack 参数

pack 基准配置：

```powershell
configs/packs/ch_ncm/ecm_leakage_series_168/ch_ncm_ecm_leakage_168s.yaml
```

故障注入配置：

```powershell
configs/packs/ch_ncm/ecm_leakage_series_168/ch_ncm_ecm_leakage_168s_fault_injection.yaml
```

关键设定：

- pack 拓扑：168 串纯串联，当前不包含并联支路。
- 模型类型：`ecm_leakage`，即 Thevenin ECM 加自放电/泄露扩展。
- RC 阶数：`ecm_rc_elements: 2`。
- 参数来源：同一份 CH-NCM locked 2RC ECM 参数包。
- 全局泄露：`self_discharge_rate_ppd: 0.06666666666666667`，约等于 30 天自放电 2%。
- 随机分散：`series_pack` 中定义 OCV、R0、初始 SOC 和容量分散，使用固定随机种子保证可复现。
- 故障覆盖：`fault_injection.cells` 可按 `cell_id` 指定容量、初始 SOC、泄露电流/电阻、自放电倍率和 R0 倍率；故障注入优先级高于随机分散。

## Pack 工况文件

工况索引：

```powershell
configs/packs/ch_ncm/ecm_leakage_series_168/locked_cycle_power_index.json
```

工况目录：

```powershell
configs/packs/ch_ncm/ecm_leakage_series_168/drive_cycles/flagship_5seat_sedan/
```

包含 9 条工况：

- `cltc_p`
- `wltc_class3b`
- `udds`
- `ftp75`
- `cold_ftp75`
- `hwfet`
- `us06`
- `sc03`
- `nedc`

每条工况通常包含：

- `*_speed_profile.csv`：速度曲线。
- `*_power_profile.csv`：道路载荷和电池功率完整计算表。
- `*_power_demand.csv`：pack 级仿真用功率输入，列为 `time_s,battery_power_w,temp_k`。
- `*_cell_power_demand.csv`：按 168 串均分到单体的功率输入。
- `*_metadata.json`：工况来源、摘要和文件索引。

单体功率换算规则：

```text
cell_power_w = battery_power_w / 168
```

注意：这些 CSV 是车辆道路载荷功率输入。ECM 参数锁定会影响后续电压、SOC、泄露和一致性仿真结果，不会改变道路载荷功率曲线本身。

`EPA-5Cycle` 在索引中是组件集合，包含 `ftp75`、`hwfet`、`us06`、`sc03`、`cold_ftp75`，不是一条连续拼接的速度曲线。

## 常用命令

以下命令建议在仓库根目录 `C:\Users\pal\projects\batt_bamm` 执行，并使用仓库约定的 Python 环境：

```powershell
cd C:\Users\pal\projects\batt_bamm
```

检查 locked 电芯配置：

```powershell
pipenv run python -m batt_bamm.main --config configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml --mode timeseries
```

运行 168 串 pack 恒功率负载示例：

```powershell
pipenv run python scripts\simulate_ch_ncm_power_load_series_pack.py
```

运行 168 串 pack 1C 到 5% SOC 一致性示例：

```powershell
pipenv run python scripts\simulate_ch_ncm_leakage_series_pack.py
```

运行 WLTC Class 3b 全放电脚本：

```powershell
pipenv run python scripts\simulate_ch_ncm_wltc_full_discharge_168s.py
```

重新生成车辆工况功率输入时使用：

```powershell
pipenv run python scripts\generate_flagship_sedan_cycle_power.py
```

## 快速校验

可用以下命令确认 pack 配置和工况索引仍绑定 locked CH-NCM 2RC 参数：

```powershell
pipenv run python -c "from pathlib import Path; import json, yaml; root=Path.cwd(); cfg=yaml.safe_load((root/'configs/packs/ch_ncm/ecm_leakage_series_168/ch_ncm_ecm_leakage_168s.yaml').read_text(encoding='utf-8')); idx=json.loads((root/'configs/packs/ch_ncm/ecm_leakage_series_168/locked_cycle_power_index.json').read_text(encoding='utf-8')); assert cfg['model']['type']=='ecm_leakage'; assert cfg['model']['ecm_rc_elements']==2; assert 'locked_ch_ncm_ecm' in cfg['model']['ecm_fitted_pack_json']; assert idx['cell_power_split']['series_count']==168; print('CH-NCM export verification OK')"
```

## 使用边界

- 当前 locked 资产是 CH-NCM ECM 基线，不是 DFN locked 参数。
- `soc_shift_map.json` 只用于诊断，不参与 ECM 物理仿真。
- 168 串 pack 工况是外部功率负载输入，不等同于仿真结果。
- `ch_ncm_ecm_leakage_168s_fault_injection.yaml` 默认开启故障注入结构，但 `cells` 为空；需要显式填写故障电芯后才会产生指定异常。
- 若要复现实测级结论，应补充真实 OCV、恒流和 HPPC 数据，并重新做验证闭环。
