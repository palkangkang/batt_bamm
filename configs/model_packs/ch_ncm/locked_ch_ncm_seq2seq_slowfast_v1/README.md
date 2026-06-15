# CH-NCM Seq2Seq Slow-Fast Locked Model Pack v1

## 用途

本目录锁定当前 Colab 正式训练产物 `colab_formal_slowfast` 中的最佳 `ch_ncm_seq2seq` 模型，用于本地复现实验、结果追溯和后续对比。它对应的是慢变量/快动态双头结构：SOC 与 OCV 走慢变量头，端电压走快动态头。

关联 cell 配置为 `configs/cells/ch_ncm_locked/seq2seq_slowfast_ch_ncm_locked.yaml`。该配置把模型 checkpoint、normalizer、训练 split、窗口长度和源数据元信息绑定到同一个可引用入口。

## 锁定内容

| 文件 | 用途 |
| --- | --- |
| `best.pt` | 锁定的最佳模型 checkpoint |
| `normalizer.json` | 与模型一致的数据归一化参数 |
| `metrics.json` | validation/test 指标与训练配置摘要 |
| `runtime_status.json` | 运行状态、签名和早停状态 |
| `epoch_log.csv` | 每个 epoch 的训练/验证日志 |
| `test_metrics_by_horizon.csv` | 全 test horizon 指标 |
| `train_history.json` | 训练历史明细 |
| `best_test_predictions_preview.csv` | 轻量预测预览，用于快速人工检查 |
| `drive_sync_manifest.json` | Colab 输出同步清单，记录未纳入的大文件 |
| `locked_index.json` | 本模型包的机器可读索引 |

未纳入 `test_predictions_full.csv`，因为 Colab 同步清单显示该文件约 5.31 GB。需要全量点级分析时，应从原 Colab 输出目录重新引用或再生成。

## 训练契约

| 项目 | 值 |
| --- | --- |
| 架构 | `slow_fast_heads` |
| 特征集 | `v2` |
| 训练 cell | `[1, 2, 3, 4, 5, 6, 7, 8]` |
| 验证 cell | `[9]` |
| 测试 cell | `[10, 11]` |
| 上下文窗口 | `300 s` |
| 预测窗口 | `300 s` |
| stride | `300 s` |
| best epoch | `91` |
| 选择指标 | `normalized_rmse_mean` |
| validation best score | `1.160093` |

## Full Test 指标

| 目标 | MAE | RMSE |
| --- | ---: | ---: |
| SOC | 0.006493 | 0.010736 |
| OCV V | 0.004989 | 0.008518 |
| Terminal Voltage V | 0.001246 | 0.002161 |

三目标 test normalized RMSE mean 为 `0.964786`。其中 SOC 和 OCV 相对上一版基线改善，端电压略高于 `2.05 mV` 基线，需要在下一轮模型结构或工况覆盖上继续优化。

## 使用边界

- 本包锁定的是时序神经网络预测模型，不是 PyBaMM ECM/DFN 参数包。
- test split 仍固定为 cell 10-11，不能用于后续模型选择。
- `dc_chg` 测试窗口只有 47 个，误差最高；正式报告中应单独声明该工况证据较薄。
- 若训练脚本、特征集、窗口长度或 normalizer 发生改变，不应复用此 checkpoint。
