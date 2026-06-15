# CH-NCM ECM 锁定参数包

## 内容

- `ecm_fitted_pack_temp_2d_2rc.json`: 已锁定的 CH-NCM Thevenin 2RC ECM 参数包。
- `soc_shift_map.json`: 仅用于评估和诊断可视化的逐样本 SOC 对齐量。
- `locked_metrics_summary.json`: 验证指标与输出图表索引。
- `locked_index.json`: 机器可读索引。
- `configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml`: 指向本锁定参数包的可运行配置。

## 使用方式

```powershell
python -m batt_bamm.main --config configs/cells/ch_ncm_locked/ecm_ch_ncm_locked.yaml --mode timeseries
```

## 说明

本锁定 ECM 参数包是当前推荐的 CH-NCM ECM 基线，来源于 SOC/温度辨识候选包
`soc_temp_r0_rc_high_soc_r0_lifted_v2_ecm`。R0 满足低 SOC 内阻最高、40%-90% SOC
平台较低、高 SOC 轻微上升的形态。`soc_shift_map.json` 不属于物理参数包，仅作为 BMS 数据质量诊断和
可视化对齐层使用。

## 锁定前验证摘要

- 当前验证集整体 MAE：22.52 mV -> 15.05 mV。
- 扩大留出集整体 MAE：19.55 mV -> 17.27 mV。
- 扩大留出集 P95 均值：49.04 mV -> 42.95 mV。
- 扩大留出集最大误差：182.7 mV -> 176.4 mV。

当前 BMS 片段暂不适合直接锁定 DFN 参数辨识结果；DFN 校准需要后续补充更适合的 OCV/恒流/HPPC 数据。