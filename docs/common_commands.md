# 常用命令

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

# External test-data parameter tune (ECM first, then DFN micro-tune)
pipenv run python -m batt_bamm.external_parameter_tune `
  --config configs/setups/external_parameter_tune_example.yaml
```
