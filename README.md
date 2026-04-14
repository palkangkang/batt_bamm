# batt_bamm

Battery simulation project built on PyBaMM. This README is the single source of truth for current capabilities, output contracts, and roadmap.

## 1. Project Purpose and Scope

### 1.1 Goal
Build a battery data simulation platform that provides reproducible and comparable outputs for R&D and strategy iteration.

Current scope:
- Electrochemical route: DFN
- ECM route: Thevenin
- Chemistry coverage: NMC and LFP

### 1.2 Non-Goals
- No production-grade on-vehicle closed-loop control in this stage.
- No one-shot full coverage of all chemistry systems and all aging mechanisms.
- This stage targets relative trustworthiness, comparability, and reproducibility, not lab-grade absolute accuracy.

## 2. Modeling Assumptions

1. Initial condition path is SOC-only (`initial_soc`), no `initial_ocv -> soc` inversion yet.
2. Baseline parameter packs:
- NMC: `Chen2020`
- LFP: `Prada2013` (proxy for workflow closure)
- ECM: `ECM_Example`
   - `parameter_audit.json` exposes `parameter_pack.quality_level` (`proxy|identified`)
3. Capacity migration:
- Scale to target `nominal_capacity_ah`.
- For DFN, scale parallel-electrode count with the same ratio if present.
4. Thermal model supports `isothermal | lumped`.
5. `timeseries.temp_k` behavior:
- Default: IO passthrough.
- If `thermal=lumped` and `timeseries.use_temp_as_ambient_boundary=true`, used as time-varying ambient boundary.
6. Termination is sample-point based with `any_of` logic.

## 3. Quick Start

### 3.1 Environment
- Repository: `C:\Users\pal\projects\batt_bamm`
- Recommended Python env: `C:\Users\pal\pyenv\colab`

### 3.2 CLI

```powershell
pipenv run python -m batt_bamm.main --config <yaml_path> --mode <baseline|hppc|timeseries|benchmark>
```

### 3.3 Common Commands

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

# Benchmark matrix + quality gate
pipenv run python -m batt_bamm.main --config configs/cells/nmc622_150ah/baseline_150ah_nmc622.yaml --mode benchmark

# Release-candidate benchmark baseline (fixed matrix)
pipenv run python -m batt_bamm.main --config configs/setups/benchmark_release_matrix.yaml --mode benchmark

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
  --gate-profile target `
  --improve-threshold 0.2
```

## 4. Capability Matrix

| Capability | mode entry | Key config | Core behavior | Artifacts | Failure semantics | Test coverage | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Parameter migration and audit | baseline/hppc/timeseries/benchmark | `nominal_capacity_ah`, `parameter_set`, `model.type`, `chemistry` | Load parameter values and write migration audit | `parameter_audit.json` | Fail on invalid parameter setup | `TestBaselinePipeline`, `TestModelChemistryCoverage` | Done |
| Sanity gate | baseline | `sanity_gate.*` | Fast discharge+charge check for bidirectional current and infeasible/skip warnings | `sanity_gate.csv/json` | Fail-fast and block batch run | baseline tests | Done |
| Baseline batch flow | baseline | `discharge_rates_c`, `charge_cc_rate`, `cv_cutoff_c_rate` | Discharge -> Rest -> CC charge -> CV hold -> Rest | `case_*.csv`, `voltage_overlay.png`, `summary.json` | `all_converged=false` if any case fails | baseline tests | Done |
| HPPC SOC sweep | hppc | `hppc.*` | SOC-point pulse simulation and 10s resistance extraction | `hppc_point_soc_*.csv`, `hppc_summary.csv/json`, `hppc_voltage_overlay.png` | Point-level fail-fast | HPPC tests | Done |
| Timeseries replay core | timeseries | `timeseries.csv_path` | Replay `time_s/current_a/temp_k`, output unified timeseries | `timeseries_output.csv`, `timeseries_summary.json` | Fail-fast on input validation or solver failure | timeseries tests | Done |
| Multi-rate CC-CV compare | timeseries sub-flow | `timeseries.charge_compare.*` | Run CC-CV at multiple rates and summarize CC/CV durations | `charge_case_*.csv`, `charge_compare_summary.csv/json`, `charge_compare_overlay.png` | Per-case failure allowed, task-level `all_converged=false` | charge_compare tests | Done |
| Generic termination engine | all modes | `termination.*` | `any_of` hit truncates output and records hit detail | `termination` in case summary | If `must_hit=true` and no hit, mark failure | termination tests | Done |
| Thermal coupling (phase-1) | all modes | `model.thermal`, `timeseries.use_temp_as_ambient_boundary` | `isothermal/lumped` switch, output thermal trajectory | unified `temperature_k` | Invalid thermal option fail-fast | lumped tests | Phase-1 done |
| ECM route integration | baseline/timeseries/benchmark | `model.type=ecm` | Thevenin route with shared output contracts | mode-specific artifacts | mode semantics | ECM smoke test | Phase-1 done |
| LFP route integration | baseline/hppc/timeseries/benchmark | `chemistry=lfp` | LFP baseline workflow (proxy parameters) | mode-specific artifacts | mode semantics | LFP smoke test | Phase-1 done |
| Benchmark matrix and gate | benchmark | `benchmark.*`, `quality_gate.*` | Run fixed profile matrix with repeats and evaluate convergence/repeatability/trend | `benchmark_matrix.csv`, `benchmark_summary.json`, `benchmark_compare_report.md`, `summary.json` | Gate failure with `enforce=true` makes overall failure | `TestBenchmarkPipeline` | Done |
| DFN vs ECM HPPC compare | standalone runner | two HPPC config files + output dir | Run DFN/ECM HPPC separately, align SOC points, compute terminal-voltage deltas | `hppc_compare_by_soc.csv`, `hppc_compare_summary.json`, `hppc_compare_voltage_delta.png`, `hppc_compare_report.md` | Any side failure or misaligned SOC grid makes compare fail | compare tests | Done |
| DFN-driven ECM fitting (1RC/2RC) | standalone runner | `ecm_fit_compare` CLI options | Run baseline compare -> fit ECM from DFN HPPC -> replay compare and evaluate gain/gate | `ecm_fitted_pack*.json`, `ecm_fit_points*.csv`, `ecm_fit_compare_summary*.json`, `ecm_fit_compare_report*.md` | Any step failure or gate miss returns non-zero | `TestEcmFitCompare` | Done |
| Identification input templates | all modes | `identification_inputs.*` | Validate OCV/CC/HPPC template inputs without fitting | `summary.identification_inputs_validation` | strict mode marks run failed but no crash | `TestIdentificationInputValidation` | Done |

## 5. Output Contracts

### 5.1 Top-level `summary.json`

| Field | Required | Description |
| --- | --- | --- |
| `contract_version` | Yes | Frozen output contract version (`1.1.0`) |
| `contract_fields` | Yes | Stable vs extensible field declaration |
| `generated_at_utc` | Yes | UTC timestamp |
| `mode` | Yes | `baseline|hppc|timeseries|benchmark` |
| `all_converged` | Yes | Overall pass/fail |
| `config` | Yes | Runtime config snapshot |
| `termination_policy` | Yes | Active termination policy snapshot |
| `termination_hits` | Yes | Number of cases that hit termination |
| `artifacts` | Yes | Key output files |
| `cases` | Yes | Case rows/summaries |
| `warnings` | Optional | Deduplicated warnings |
| `identification_inputs_validation` | Optional | Template validation result |
| `quality_gate` | benchmark | Quality gate summary |
| `benchmark` | benchmark | Benchmark aggregate result |
| `sanity_gate` | baseline | Sanity gate result |
| `hppc` | hppc | HPPC aggregate result |
| `timeseries` | timeseries replay | Timeseries replay result |
| `charge_compare` | timeseries compare | Charge compare result |

Stable/Extensible policy:
- Stable top-level fields are declared in `summary.contract_fields.stable_top_level_fields`.
- Stable case fields are declared in `summary.contract_fields.stable_case_fields`.
- New fields are allowed only as backward-compatible append via `extensible_*` declarations.

### 5.2 RunSummary (common case summary)

| Field | Required | Description |
| --- | --- | --- |
| `case_id` | Yes | Case identifier |
| `converged` | Yes | Case pass/fail |
| `min_v`, `max_v` | Yes (nullable) | Voltage range |
| `final_soc` | Yes (nullable) | Final SOC |
| `runtime_s` | Yes | Runtime in seconds |
| `csv_path` | Yes (nullable) | Case CSV path |
| `termination` | Yes | Termination detail |
| `error` | Optional | Failure reason |

`termination` fields:
- `hit, reason, time_s, index, metric, op, threshold, value`

### 5.3 Benchmark block (`summary.benchmark`)

Required fields:
- `passed`
- `total_cases`
- `converged_cases`
- `convergence_rate`
- `repeatability`
- `trend_checks`
- `failures`
- `artifacts`

`benchmark.failures[]` is structured evidence (not free text):
- `category, reason, profile_id, rate_c, repeat, observed, threshold`

### 5.4 Quality gate block (`summary.quality_gate`)

Required fields:
- `enabled`
- `enforce`
- `passed`
- `thresholds`
- `metrics`

### 5.5 Identification validation block (`summary.identification_inputs_validation`)

Required fields:
- `enabled`
- `strict`
- `passed`
- `datasets`
- `errors`

### 5.6 HPPC compare summary (`hppc_compare_summary.json`)

Required fields:
- `cell_id`
- `chemistry`
- `nominal_capacity_ah`
- `soc_grid`
- `dfn_run`
- `ecm_run`
- `completed_points`
- `passed`
- `metrics`
- `artifacts`

Optional:
- `stop_reason`
- `warnings`

## 6. Configuration Contract

### 6.0 Config Directory Convention

`configs` is organized by cell spec and setup scope:

- Cell-bound simulation configs: `configs/cells/<cell_spec>/...`
- Setup/matrix configs (not bound to a specific cell parameter pack): `configs/setups/...`

Migration mapping (old -> new):

| Old path | New path |
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

### 6.1 Model and chemistry

```yaml
model:
  type: dfn          # dfn|ecm
  thermal: isothermal # isothermal|lumped
  ecm_rc_elements: 1  # 1|2 (only for model.type=ecm)
chemistry: nmc       # nmc|lfp
```

### 6.2 Termination

```yaml
termination:
  enabled: true
  logic: any_of
  must_hit: false
  apply_to_experiment_modes: true
  conditions:
    - metric: voltage_v   # time_s|voltage_v|soc|current_abs_a|temperature_k|ocv_v
      op: "<="           # <=|>=
      threshold: 2.8
      name: stop_at_low_v
```

### 6.3 Benchmark and quality gate

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

### 6.4 Release benchmark baseline

`configs/setups/benchmark_release_matrix.yaml` is the dedicated reproducible matrix for P0 acceptance:
- Profiles fixed to `dfn_nmc, dfn_lfp, ecm_nmc, ecm_lfp`
- Rates fixed to `[0.2, 1.0]`
- Repeats fixed to `2`
- Quality gate enabled and enforced

### 6.5 Identification template validation

```yaml
identification_inputs:
  enabled: true
  strict: true
  ocv_points_csv: inputs/templates/ocv_points_template.csv
  cc_cycle_csv: inputs/templates/cc_cycle_template.csv
  hppc_points_csv: inputs/templates/hppc_points_template.csv
```

### 6.6 Timeseries thermal boundary in lumped mode

```yaml
timeseries:
  enabled: true
  csv_path: path/to/input.csv
  use_temp_as_ambient_boundary: true
```

### 6.7 HPPC Compare Baseline Configs

For 150Ah NMC DFN/ECM compare:
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_dfn.yaml`
- `configs/cells/nmc622_150ah/hppc_compare_150ah_nmc_ecm.yaml`

Both use:
- `hppc.soc_start: 1.0`
- `hppc.soc_end: 0.0`
- `hppc.soc_step: 0.05`

## 7. Template Inputs

Template files are provided at `inputs/templates/`:
- `ocv_points_template.csv` with columns: `soc,ocv_v,temp_k`
- `cc_cycle_template.csv` with columns: `time_s,current_a,voltage_v,temp_k`
- `hppc_points_template.csv` with columns: `soc_target,r_dis_10s_ohm,r_chg_10s_ohm,temp_k`

## 8. Roadmap

### P0
- Stabilize ECM and DFN parity on standard workflows.
- Replace LFP proxy parameters with data-identified packs.
- Improve termination diagnostics.

### P1
- Extend thermal boundary strategies and thermal parameter calibration.
- Build closed-loop parameter identification from OCV / 0.5C / HPPC datasets.

### P2
- Add `initial_ocv -> soc` inversion.
- Add controller-style CCCV.
- Introduce aging and side-reaction models.

## 9. Artifact Governance and Maintenance

1. Keep only scripts, configs, and minimal reproducible inputs in repo.
2. Move large simulation artifacts to release/object storage/on-demand upload.
3. Any new mode/field/artifact must update this README in the same change.
4. New capability must include at least one automated test for smoke + contract + failure semantics.
