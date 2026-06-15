# batt_bamm

`batt_bamm` 是一个基于 PyBaMM 与本地等效电路扩展的锂电池仿真工程。当前目标是把电芯级 DFN/ECM 仿真、CH-NCM locked 参数包、168 串 pack 工况、故障注入和数据驱动模型评估组织成可复现、可比较、可追溯的一套本地研发平台。

本 README 只作为项目总入口维护。详细契约、运行步骤和专项分析请进入下方报告或对应资产目录。

## 当前工程主线

| 主线 | 当前内容 | 代表入口 |
| --- | --- | --- |
| 单体仿真 | NMC622、LFP、CH-NCM 的 baseline、HPPC、timeseries、benchmark 流程 | `src/batt_bamm/main.py`, `configs/cells/` |
| ECM 参数化 | Thevenin ECM、SOC x temperature 二维 R/C 插值、DFN 驱动 ECM 拟合与对比 | `src/batt_bamm/ecm_fit_compare.py`, `configs/parameter_packs/` |
| 热与外部数据闭环 | 热评估矩阵、LFP 热参数识别、外部测试数据参数调优 | `src/batt_bamm/thermal_eval.py`, `src/batt_bamm/lfp_thermal_identify.py`, `src/batt_bamm/external_parameter_tune.py` |
| CH-NCM locked 资产 | locked CH-NCM 2RC ECM 参数包、locked seq2seq slow-fast 模型包 | `configs/cells/ch_ncm_locked/`, `configs/model_packs/ch_ncm/` |
| 168 串 pack | CH-NCM ECM leakage 168 串 pack、车辆工况功率输入、故障注入配置 | `configs/packs/ch_ncm/ecm_leakage_series_168/`, `src/batt_bamm/ch_ncm_pack_fault/` |
| 虚拟数据与学习材料 | CH-NCM ECM 虚拟充放电数据、PyBaMM 8 周学习路径 | `data/ch_ncm_ecm_virtual_charge_discharge/`, `learning/pybamm_8week/` |

## 代表性专项报告

| 专项报告 | 内容定位 |
| --- | --- |
| [能力矩阵](docs/capability_matrix.md) | 汇总当前 mode、配置入口、产物、失败语义、测试覆盖与状态。 |
| [CH-NCM 电芯与 Pack 参数使用说明](docs/ch_ncm_cell_pack_usage.md) | 说明 locked CH-NCM ECM 参数包、168 串 pack、车辆工况和常用复现命令。 |
| [配置与参数目录说明](configs/configs.md) | 解释 `configs/` 下电芯配置、pack 配置、参数包、车辆规格和输出目录的边界。 |
| [配置契约](docs/config_contract.md) | 定义配置目录、模型字段、截止条件、热边界、benchmark 和 ECM 拟合包 schema。 |
| [输出契约](docs/output_contract.md) | 定义 `summary.json`、RunSummary、timeseries CSV、benchmark、质量门禁和 HPPC 对比摘要字段。 |
| [最小实测闭环执行清单](docs/minimal_field_closed_loop_checklist.md) | 给出 OCV、0.5C 循环、HPPC、拟合门禁和留出验证的最低成本闭环。 |
| [CH-NCM ECM 锁定参数包](configs/parameter_packs/ch_ncm/locked_ch_ncm_ecm/README.md) | 说明当前推荐 CH-NCM 2RC ECM 参数包、锁定前验证摘要和使用边界。 |
| [CH-NCM Seq2Seq locked 模型包](configs/model_packs/ch_ncm/locked_ch_ncm_seq2seq_slowfast_v1/README.md) | 说明 slow-fast 三目标预测模型的锁定内容、训练契约、指标和边界。 |
| [CH-NCM/ECM 虚拟充放电测试数据](data/ch_ncm_ecm_virtual_charge_discharge/README.md) | 说明 11 个虚拟单体、AC/DC 充电、车辆放电连接工况和输出字段。 |
| [常用命令](docs/common_commands.md) | 收纳 baseline、HPPC、timeseries、benchmark、热评估、拟合和外部数据调优命令。 |

## 目录地图

| 路径 | 用途 |
| --- | --- |
| `src/batt_bamm/` | 核心 Python 包，包含主 CLI、热评估、HPPC 对比、ECM 拟合、外部数据调优、CH-NCM pack fault 和 seq2seq 模块。 |
| `configs/cells/` | 可直接传给 `batt_bamm.main --config` 的单体级运行配置。 |
| `configs/setups/` | benchmark、热评估、热参数识别、外部数据调优和 CH-NCM pack 专项任务配置。 |
| `configs/parameter_packs/` | 可复用参数资产和锁定指标，不作为运行输出目录。 |
| `configs/model_packs/` | 数据驱动模型包与模型锁定索引。 |
| `configs/packs/` | pack 场景、故障注入、车辆工况索引和锁定工况 CSV。 |
| `configs/vehicles/` | 车辆道路负载参数和锁定副本。 |
| `data/` | 本地样例数据、虚拟数据和工况功率输入。 |
| `inputs/` | 外部调优样例和 OCV/CC/HPPC 数据模板。 |
| `scripts/` | 数据生成、参数锁定、pack 仿真、seq2seq 训练/评估和辅助汇总脚本。 |
| `tests/` | 自动化测试，覆盖主流程、热评估、ECM 拟合、外部调优、CH-NCM pack 和 seq2seq。 |
| `docs/` | 项目契约、能力矩阵、专项说明和执行清单。 |
| `outputs/` | 运行产物与历史分析输出，不作为 canonical 参数来源。 |
| `logs/` | 按天追加的会话日志和任务日志。 |

## 快速开始

推荐在仓库根目录 `C:\Users\pal\projects\batt_bamm` 使用项目约定的 Python 环境运行：

```powershell
pipenv run python -m batt_bamm.main --config <yaml_path> --mode <baseline|hppc|timeseries|benchmark>
```

典型入口：

```powershell
# NMC622 DFN baseline
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode baseline

# LFP ECM baseline
pipenv run python -m batt_bamm.main --config configs/cells/lfp_130ah/baseline_130ah_lfp_ecm.yaml --mode baseline

# CH-NCM locked ECM timeseries
pipenv run python -m batt_bamm.main --config configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml --mode timeseries
```

更多命令见 [常用命令](docs/common_commands.md)。

## 能力摘要

当前本地工程已覆盖：

- 标准仿真模式：`baseline`, `hppc`, `timeseries`, `benchmark`。
- 化学体系：NMC/NCM 与 LFP。
- 模型路线：DFN 与 Thevenin ECM。
- 热相关能力：`isothermal` 与 `lumped`，支持热边界、热评估矩阵和 LFP 热参数识别。
- ECM 参数包：要求使用 `ecm_temp_2d_v1` schema，R/C 采用 SOC x temperature 二维插值，OCV 仍为 SOC 依赖。
- CH-NCM 资产：locked 2RC ECM 参数包、168 串 ECM leakage pack、车辆工况功率输入、故障注入模板和 seq2seq slow-fast 模型包。
- 数据闭环：支持外部 `time_s/current_a/voltage_v` 数据校验、ECM 优先拟合和 DFN 小范围微调。

## 资产边界

- `configs/parameter_packs/` 与 `configs/model_packs/` 是可复用或锁定资产的主位置。
- `outputs/` 只保存运行输出、图表、临时分析和历史验证结果，不作为参数主源。
- CH-NCM locked ECM 是当前推荐的 CH-NCM ECM 基线；seq2seq 模型包是数据驱动预测模型，不是 PyBaMM 参数包。
- `data/ch_ncm_ecm_virtual_charge_discharge/` 是基于 locked ECM 生成的虚拟测试数据，不代表真实车队统计分布。
- 真实实测级结论仍需要 OCV、恒流循环、HPPC 和留出验证数据闭环支撑，最低执行口径见 [最小实测闭环执行清单](docs/minimal_field_closed_loop_checklist.md)。

## 维护约定

1. 新增 mode、配置字段、输出字段或关键产物时，同步更新 [能力矩阵](docs/capability_matrix.md)、[配置契约](docs/config_contract.md) 或 [输出契约](docs/output_contract.md)。
2. 新增 locked 参数包或模型包时，在资产目录内保留 `README.md` 与机器可读索引。
3. 新增可运行配置时，优先放入 `configs/cells/`、`configs/setups/` 或 `configs/packs/`，并保持参数资产与运行输出分离。
4. 新增能力应配套最小自动化测试，优先覆盖冒烟、契约和失败语义。
5. 会话与任务日志按天追加到 `logs/session_YYYY-MM-DD.md`，不覆盖历史记录。
