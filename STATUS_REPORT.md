# STATUS_REPORT

## 1. 当前项目状态（截至 2026-04-14）

### 1.1 目标对齐
- 项目目标：锂电池仿真平台，覆盖 `DFN/ECM` 与 `NMC/LFP`。
- 当前主线：`baseline | hppc | timeseries | benchmark` 均可运行，并有统一摘要契约。

### 1.2 已完成能力
- 参数迁移与审计：支持 150Ah NMC、130Ah LFP 基线配置。
- 热模型：支持 `isothermal/lumped`，输出电芯本体温度与边界温度。
- 通用截止条件：支持 `time_s/voltage_v/soc/current_abs_a/cell_temperature_k/boundary_temperature_k/ocv_v`。
- 对比与拟合：支持 `hppc_compare` 与 `ecm_fit_compare(1RC/2RC)`。

### 1.3 契约版本
- `summary.contract_version = 2.0.0`。
- 该版本为破坏性升级：移除旧输出字段 `temperature_k`。

## 2. 本轮关键变更

### 2.1 温度字段统一
- 时序输出仅保留：
  - `cell_temperature_k`
  - `boundary_temperature_k`
- 不再输出 `temperature_k` 别名字段。

### 2.2 截止条件迁移
- `termination.metric=temperature_k` 已废弃。
- 必须改为：
  - `cell_temperature_k` 或
  - `boundary_temperature_k`

### 2.3 文档与测试同步
- README 已同步到双温度字段与 `2.0.0` 契约版本。
- 回归测试覆盖：旧指标报错、新指标可用、输出列契约稳定。

## 3. 已知风险与建议
- 风险：下游脚本若仍读取 `temperature_k` 会直接失败。
- 建议：统一迁移读取逻辑到 `cell_temperature_k`，并按业务需要引入 `boundary_temperature_k` 判据。

## 4. 后续工作计划（热耦合 + 串联模型）

### 4.1 热耦合后续
- 增强边界温度输入策略（常温/时序边界统一）。
- 增加温升统计指标与门禁阈值（峰值温升、温升速率、重复性）。

### 4.2 串联模型后续
- 先做等参 `N` 串闭环（同电流、公共边界）。
- 输出整包与单体双视图：`pack_voltage_v/pack_soc/pack_temperature_k` + `cell_i_*`。
- 再扩展离散参数（容量/内阻差异）与均衡策略。

## 5. 运行与治理约定
- 文档、配置、代码、日志统一 UTF-8。
- 仓库只保留可复现输入与脚本，大体量产物放 release/对象存储。
