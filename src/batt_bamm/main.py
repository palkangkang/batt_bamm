from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pybamm
import yaml

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y7lvtQAAAAASUVORK5CYII="
)
_WARNING_TOKENS = ("infeasible at initial conditions", "skipping step")
_ALLOWED_MODEL_TYPES = {"dfn", "ecm"}
_ALLOWED_CHEMISTRY = {"nmc", "lfp"}
_ALLOWED_THERMAL = {"isothermal", "lumped"}
_ALLOWED_THERMAL_BOUNDARY_MODES = {"constant", "timeseries"}
_ALLOWED_ECM_RC_ELEMENTS = {1, 2}
_ALLOWED_TERMINATION_METRICS = {
    "time_s",
    "voltage_v",
    "soc",
    "current_abs_a",
    "cell_temperature_k",
    "boundary_temperature_k",
    "ocv_v",
}
_ALLOWED_TERMINATION_OPS = {">=", "<="}
_THERMAL_PARAM_TO_PYBAMM_KEY = {
    "total_heat_transfer_coefficient_w_m2_k": "Total heat transfer coefficient [W.m-2.K-1]",
    "cell_volume_m3": "Cell volume [m3]",
    "cell_cooling_surface_area_m2": "Cell cooling surface area [m2]",
}
_THERMAL_HEAT_CAPACITY_PARAMETER_KEYS = (
    "Negative current collector specific heat capacity [J.kg-1.K-1]",
    "Positive current collector specific heat capacity [J.kg-1.K-1]",
    "Negative electrode specific heat capacity [J.kg-1.K-1]",
    "Positive electrode specific heat capacity [J.kg-1.K-1]",
    "Separator specific heat capacity [J.kg-1.K-1]",
)
_THERMAL_CONDUCTIVITY_PARAMETER_KEYS = (
    "Negative current collector thermal conductivity [W.m-1.K-1]",
    "Positive current collector thermal conductivity [W.m-1.K-1]",
    "Negative electrode thermal conductivity [W.m-1.K-1]",
    "Positive electrode thermal conductivity [W.m-1.K-1]",
    "Separator thermal conductivity [W.m-1.K-1]",
)
_LFP_LUMPED_PROXY_PARAMETER_KEYS = (
    "Negative current collector thickness [m]",
    "Negative current collector conductivity [S.m-1]",
    "Positive current collector thickness [m]",
    "Positive current collector conductivity [S.m-1]",
    "Negative current collector density [kg.m-3]",
    "Negative current collector specific heat capacity [J.kg-1.K-1]",
    "Negative electrode density [kg.m-3]",
    "Negative electrode specific heat capacity [J.kg-1.K-1]",
    "Separator density [kg.m-3]",
    "Separator specific heat capacity [J.kg-1.K-1]",
    "Positive electrode density [kg.m-3]",
    "Positive electrode specific heat capacity [J.kg-1.K-1]",
    "Positive current collector density [kg.m-3]",
    "Positive current collector specific heat capacity [J.kg-1.K-1]",
)
_LFP_LUMPED_PROXY_SOURCE_SET = "Chen2020"
_DFN_ARRHENIUS_PARAMETER_MAP = {
    "negative_particle_diffusivity_ea_j_mol": "Negative particle diffusivity [m2.s-1]",
    "positive_particle_diffusivity_ea_j_mol": "Positive particle diffusivity [m2.s-1]",
    "negative_exchange_current_ea_j_mol": "Negative electrode exchange-current density [A.m-2]",
    "positive_exchange_current_ea_j_mol": "Positive electrode exchange-current density [A.m-2]",
}
_ECM_TEMP_PACK_SCHEMA_VERSION = "ecm_temp_2d_v1"
_CONTRACT_VERSION = "3.0.0"
_STABLE_SUMMARY_FIELDS = {
    "contract_version",
    "contract_fields",
    "generated_at_utc",
    "mode",
    "all_converged",
    "config",
    "termination_policy",
    "termination_hits",
    "artifacts",
    "cases",
}
_STABLE_CASE_FIELDS = {"case_id", "converged", "runtime_s", "csv_path", "error"}
_STABLE_QUALITY_GATE_FIELDS = {"enabled", "enforce", "passed", "thresholds", "metrics"}
_STABLE_BENCHMARK_FIELDS = {
    "passed",
    "total_cases",
    "converged_cases",
    "convergence_rate",
    "repeatability",
    "trend_checks",
    "failures",
    "artifacts",
}
_STABLE_IDENTIFICATION_FIELDS = {"enabled", "strict", "passed", "datasets", "errors"}


@dataclass(frozen=True)
class SanityGateConfig:
    enabled: bool = True
    rate_c: float = 0.5
    discharge_to_v: float = 3.6
    charge_to_v: float = 4.1
    period_s: int = 30


@dataclass(frozen=True)
class HppcConfig:
    enabled: bool = True
    soc_start: float = 1.0
    soc_end: float = 0.05
    soc_step: float = 0.05
    pulse_c_rate: float = 1.0
    discharge_s: float = 10.0
    charge_s: float = 10.0
    rest_minutes: float = 30.0
    period_s: float = 0.1


@dataclass(frozen=True)
class ChargeCompareConfig:
    enabled: bool = False
    soc_start: float = 0.0
    rates_c: list[float] = field(default_factory=lambda: [0.1, 1.0 / 3.0, 1.0])
    period_by_rate_s: dict[float, float] = field(
        default_factory=lambda: {0.1: 1.0, 1.0 / 3.0: 0.1, 1.0: 0.1}
    )
    cv_cutoff_c_rate: float = 0.05
    voltage_high_v: float = 4.2


@dataclass(frozen=True)
class SocSwitchApproxConfig:
    enabled: bool = False
    soc_start: float | None = None
    discharge_rate_c: float = 1.0
    discharge_to_soc: float = 0.30
    charge_rate_c: float = 1.0
    charge_to_soc: float = 0.90
    period_s: float = 0.1
    temp_k: float | None = None


@dataclass(frozen=True)
class TimeseriesSuiteConfig:
    enabled: bool = False
    csv_path: Path | None = None
    period_s: float | None = None
    use_temp_as_ambient_boundary: bool = False
    allow_early_stop: bool = False
    charge_compare: ChargeCompareConfig = field(default_factory=ChargeCompareConfig)
    soc_switch_approx: SocSwitchApproxConfig = field(default_factory=SocSwitchApproxConfig)


@dataclass(frozen=True)
class ThermalCouplingConfig:
    enabled: bool = False
    boundary_mode: str = "constant"


@dataclass(frozen=True)
class ThermalParamsConfig:
    total_heat_transfer_coefficient_w_m2_k: float | None = None
    cell_volume_m3: float | None = None
    cell_cooling_surface_area_m2: float | None = None


@dataclass(frozen=True)
class ThermalPropertyScaleConfig:
    heat_capacity_scale: float = 1.0
    thermal_conductivity_scale: float = 1.0


@dataclass(frozen=True)
class DfnArrheniusOverridesConfig:
    negative_particle_diffusivity_ea_j_mol: float | None = None
    positive_particle_diffusivity_ea_j_mol: float | None = None
    negative_exchange_current_ea_j_mol: float | None = None
    positive_exchange_current_ea_j_mol: float | None = None


@dataclass(frozen=True)
class DfnTemperatureDependenceConfig:
    enabled: bool = False
    reference_temp_k: float = 298.15
    arrhenius_overrides: DfnArrheniusOverridesConfig = field(default_factory=DfnArrheniusOverridesConfig)


@dataclass(frozen=True)
class TemperatureDependenceConfig:
    dfn: DfnTemperatureDependenceConfig = field(default_factory=DfnTemperatureDependenceConfig)


@dataclass(frozen=True)
class QualityGateConfig:
    enabled: bool = True
    min_convergence_rate: float = 0.95
    max_repeat_delta_final_soc: float = 5e-4
    max_repeat_delta_min_v: float = 5e-3
    require_polarization_trend: bool = True
    enforce: bool = True


@dataclass(frozen=True)
class IdentificationInputsConfig:
    enabled: bool = False
    strict: bool = True
    ocv_points_csv: Path | None = None
    cc_cycle_csv: Path | None = None
    hppc_points_csv: Path | None = None


@dataclass(frozen=True)
class BenchmarkConfig:
    enabled: bool = True
    rates_c: list[float] = field(default_factory=lambda: [0.2, 1.0])
    repeats: int = 2
    rest_min: float = 5.0
    charge_cc_rate: float = 0.5
    cv_cutoff_c_rate: float = 0.05
    period_s: int = 30
    profiles: list[str] = field(default_factory=lambda: ["dfn_nmc", "dfn_lfp", "ecm_nmc", "ecm_lfp"])


@dataclass(frozen=True)
class TerminationCondition:
    metric: str
    op: str
    threshold: float
    name: str | None = None


@dataclass(frozen=True)
class TerminationConfig:
    enabled: bool = True
    logic: str = "any_of"
    must_hit: bool = False
    apply_to_experiment_modes: bool = True
    conditions: list[TerminationCondition] = field(default_factory=list)


@dataclass(frozen=True)
class TerminationResult:
    hit: bool
    reason: str | None
    time_s: float | None
    index: int | None
    metric: str | None
    op: str | None
    threshold: float | None
    value: float | None


def _default_termination_result() -> TerminationResult:
    return TerminationResult(
        hit=False,
        reason=None,
        time_s=None,
        index=None,
        metric=None,
        op=None,
        threshold=None,
        value=None,
    )


@dataclass(frozen=True)
class Config:
    model_type: str
    chemistry: str
    nominal_capacity_ah: float
    initial_soc: float
    ambient_temp_k: float
    initial_cell_temp_k: float
    voltage_low_v: float
    voltage_high_v: float
    discharge_rates_c: list[float]
    charge_cc_rate: float
    cv_cutoff_c_rate: float
    rest_min: float
    output_dir: Path
    parameter_set: str = "Chen2020"
    thermal: str = "isothermal"
    thermal_coupling: ThermalCouplingConfig = field(default_factory=ThermalCouplingConfig)
    thermal_params: ThermalParamsConfig = field(default_factory=ThermalParamsConfig)
    thermal_property_scales: ThermalPropertyScaleConfig = field(default_factory=ThermalPropertyScaleConfig)
    temperature_dependence: TemperatureDependenceConfig = field(default_factory=TemperatureDependenceConfig)
    ecm_rc_elements: int = 1
    ecm_fitted_pack_json: Path | None = None
    period_s: int = 30
    solver_rtol: float = 1e-6
    solver_atol: float = 1e-8
    sanity_gate: SanityGateConfig = field(default_factory=SanityGateConfig)
    hppc: HppcConfig = field(default_factory=HppcConfig)
    timeseries: TimeseriesSuiteConfig = field(default_factory=TimeseriesSuiteConfig)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    identification_inputs: IdentificationInputsConfig = field(default_factory=IdentificationInputsConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    termination: TerminationConfig = field(default_factory=TerminationConfig)


@dataclass(frozen=True)
class RunSummary:
    case_id: str
    converged: bool
    min_v: float | None
    max_v: float | None
    final_soc: float | None
    runtime_s: float
    csv_path: str | None
    termination: TerminationResult = field(default_factory=_default_termination_result)
    error: str | None = None


@dataclass(frozen=True)
class SanityGateResult:
    enabled: bool
    passed: bool
    converged: bool
    has_positive_current: bool
    has_negative_current: bool
    warning_messages: list[str]
    runtime_s: float
    artifact_csv: str | None
    artifact_json: str
    error: str | None = None


@dataclass(frozen=True)
class HppcPointResult:
    soc_target: float
    passed: bool
    runtime_s: float
    v_dis_end: float | None
    v_dis_rest_start: float | None
    r_dis_10s_ohm: float | None
    v_chg_end: float | None
    v_chg_rest_start: float | None
    r_chg_10s_ohm: float | None
    has_positive_current: bool
    has_negative_current: bool
    csv_path: str | None
    warning_messages: list[str]
    termination: TerminationResult = field(default_factory=_default_termination_result)
    error: str | None = None


@dataclass(frozen=True)
class ChargeCompareCaseResult:
    case_id: str
    rate_c: float
    period_s: float
    converged: bool
    runtime_s: float
    final_soc: float | None
    final_voltage_v: float | None
    charge_time_s: float | None
    cc_end_time_s: float | None
    cv_time_s: float | None
    csv_path: str | None
    termination: TerminationResult = field(default_factory=_default_termination_result)
    error: str | None = None


class _WarningCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _dedupe_messages(messages: list[str]) -> list[str]:
    deduped: list[str] = []
    for message in messages:
        if message not in deduped:
            deduped.append(message)
    return deduped


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def _config_to_summary_dict(config: Config) -> dict[str, Any]:
    payload = asdict(config)
    payload["output_dir"] = str(config.output_dir)
    return _to_jsonable(payload)


def _infer_parameter_quality_level(parameter_set: str) -> str:
    token = str(parameter_set).strip().lower()
    identified_tokens = ("identified", "fitted", "calibrated", "measured")
    return "identified" if any(item in token for item in identified_tokens) else "proxy"


def _resolve_parameter_quality_level(config: Config) -> str:
    if config.model_type == "ecm" and config.ecm_fitted_pack_json is not None:
        return "identified"
    return _infer_parameter_quality_level(config.parameter_set)


def _proxy_parameter_warning(config: Config) -> str | None:
    quality_level = _resolve_parameter_quality_level(config)
    if quality_level != "proxy":
        return None
    if config.model_type == "ecm" and config.chemistry == "lfp":
        return (
            "Proxy parameter pack in use for ECM+LFP profile; "
            "results are workflow-grade and not lab-calibrated."
        )
    if config.chemistry == "lfp":
        return "Proxy parameter pack in use for LFP chemistry; results are workflow-grade and not lab-calibrated."
    return "Proxy parameter pack in use; results are for relative comparison and reproducibility only."


def _contract_fields_descriptor(mode: str) -> dict[str, Any]:
    mode_specific = {
        "baseline": ["sanity_gate"],
        "hppc": ["hppc"],
        "timeseries": ["timeseries|charge_compare"],
        "benchmark": ["quality_gate", "benchmark"],
    }
    return {
        "stable_top_level_fields": sorted(_STABLE_SUMMARY_FIELDS),
        "mode_required_blocks": mode_specific.get(mode, []),
        "extensible_top_level_fields": ["warnings", "identification_inputs_validation"],
        "stable_case_fields": sorted(_STABLE_CASE_FIELDS),
        "extensible_case_fields": ["min_v", "max_v", "final_soc", "termination"],
        "stable_block_fields": {
            "quality_gate": sorted(_STABLE_QUALITY_GATE_FIELDS),
            "benchmark": sorted(_STABLE_BENCHMARK_FIELDS),
            "identification_inputs_validation": sorted(_STABLE_IDENTIFICATION_FIELDS),
        },
    }


def _attach_contract_metadata(summary: dict[str, Any]) -> None:
    mode = str(summary.get("mode", ""))
    summary["contract_version"] = _CONTRACT_VERSION
    summary["contract_fields"] = _contract_fields_descriptor(mode)


def _validate_summary_contract(summary: dict[str, Any]) -> None:
    missing_top = sorted(_STABLE_SUMMARY_FIELDS - set(summary.keys()))
    if missing_top:
        raise ValueError(f"summary contract missing top-level fields: {missing_top}")
    if not isinstance(summary.get("cases"), list):
        raise ValueError("summary.cases must be a list.")

    mode = str(summary.get("mode", ""))
    if mode == "baseline" and "sanity_gate" not in summary:
        raise ValueError("baseline summary must include sanity_gate.")
    if mode == "hppc" and "hppc" not in summary:
        raise ValueError("hppc summary must include hppc block.")
    if mode == "benchmark" and ("quality_gate" not in summary or "benchmark" not in summary):
        raise ValueError("benchmark summary must include quality_gate and benchmark blocks.")
    if mode == "timeseries" and ("timeseries" not in summary and "charge_compare" not in summary):
        raise ValueError("timeseries summary must include timeseries or charge_compare block.")

    for idx, case in enumerate(summary["cases"]):
        if not isinstance(case, dict):
            raise ValueError(f"summary.cases[{idx}] must be an object.")
        missing_case = sorted(_STABLE_CASE_FIELDS - set(case.keys()))
        if missing_case:
            raise ValueError(f"summary.cases[{idx}] missing fields: {missing_case}")
        if "termination" in case:
            term = case["termination"]
            if not isinstance(term, dict):
                raise ValueError(f"summary.cases[{idx}].termination must be an object.")
            expected_term = {"hit", "reason", "time_s", "index", "metric", "op", "threshold", "value"}
            missing_term = sorted(expected_term - set(term.keys()))
            if missing_term:
                raise ValueError(f"summary.cases[{idx}].termination missing fields: {missing_term}")

    if "quality_gate" in summary:
        payload = summary["quality_gate"]
        if not isinstance(payload, dict):
            raise ValueError("summary.quality_gate must be an object.")
        missing = sorted(_STABLE_QUALITY_GATE_FIELDS - set(payload.keys()))
        if missing:
            raise ValueError(f"summary.quality_gate missing fields: {missing}")

    if "benchmark" in summary:
        payload = summary["benchmark"]
        if not isinstance(payload, dict):
            raise ValueError("summary.benchmark must be an object.")
        missing = sorted(_STABLE_BENCHMARK_FIELDS - set(payload.keys()))
        if missing:
            raise ValueError(f"summary.benchmark missing fields: {missing}")
        failures = payload.get("failures", [])
        if not isinstance(failures, list):
            raise ValueError("summary.benchmark.failures must be a list.")
        for idx, failure in enumerate(failures):
            if not isinstance(failure, dict):
                raise ValueError(f"summary.benchmark.failures[{idx}] must be an object.")
            required = {"category", "reason", "profile_id", "rate_c", "repeat", "observed", "threshold"}
            missing_failure = sorted(required - set(failure.keys()))
            if missing_failure:
                raise ValueError(f"summary.benchmark.failures[{idx}] missing fields: {missing_failure}")

    if "identification_inputs_validation" in summary:
        payload = summary["identification_inputs_validation"]
        if not isinstance(payload, dict):
            raise ValueError("summary.identification_inputs_validation must be an object.")
        missing = sorted(_STABLE_IDENTIFICATION_FIELDS - set(payload.keys()))
        if missing:
            raise ValueError(f"summary.identification_inputs_validation missing fields: {missing}")


def _write_summary_json(output_dir: Path, summary: dict[str, Any]) -> None:
    _attach_contract_metadata(summary)
    _validate_summary_contract(summary)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _warning_is_infeasible(message: str) -> bool:
    lower = message.lower()
    return any(token in lower for token in _WARNING_TOKENS)


def _should_retry_with_casadi(config: Config, error: str | None) -> bool:
    if config.model_type not in {"dfn", "ecm"} or not error:
        return False
    token = str(error).lower()
    return (
        "ida_conv_fail" in token
        or "ida_err_fail" in token
        or "error test failures occurred too many times" in token
        or "minimum step size was reached" in token
        or "corrector convergence failed repeatedly" in token
    )


def _prefer_casadi_primary_solver(config: Config) -> bool:
    return bool(config.model_type == "dfn" and config.chemistry == "lfp" and config.thermal == "lumped")


def _case_id(rate_c: float) -> str:
    return f"case_{str(rate_c).replace('.', 'p')}c"


def _rate_from_case_id(case_id: str) -> float | None:
    if not case_id.startswith("case_") or not case_id.endswith("c"):
        return None
    token = case_id[5:-1].replace("p", ".")
    try:
        return float(token)
    except ValueError:
        return None


def _soc_label(soc: float) -> str:
    return f"{int(round(soc * 100)):03d}"


def _uses_initial_soc_argument(config: Config) -> bool:
    return config.model_type == "dfn"


def _build_core_model(config: Config) -> pybamm.BaseModel:
    if config.model_type == "dfn":
        return pybamm.lithium_ion.DFN(options={"thermal": config.thermal})
    if config.model_type == "ecm":
        return pybamm.equivalent_circuit.Thevenin(
            options={"number of rc elements": config.ecm_rc_elements}
        )
    raise ValueError(f"Unsupported model_type: {config.model_type}")


def _effective_initial_soc(config: Config, initial_soc: float) -> float:
    soc = float(initial_soc)
    if config.model_type == "ecm":
        # Thevenin event checks can fail at exactly 0/1 SOC. Keep a tiny safety margin.
        return float(np.clip(soc, 1e-8, 1.0 - 1e-8))
    return soc


def _set_initial_soc_if_supported(
    parameter_values: pybamm.ParameterValues, initial_soc: float
) -> pybamm.ParameterValues:
    updated = parameter_values.copy()
    if "Initial SoC" in updated.keys():
        updated.update({"Initial SoC": float(initial_soc)})
    return updated


def _resolve_optional_path(raw_value: Any, base_dir: Path) -> Path | None:
    if not isinstance(raw_value, str):
        return None
    token = raw_value.strip()
    if not token:
        return None
    candidate = Path(token)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def _parse_optional_positive_float(raw: dict[str, Any], key: str, *, field_label: str) -> float | None:
    if key not in raw or raw.get(key) is None:
        return None
    try:
        value = float(raw[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} must be a positive number when provided.") from exc
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{field_label} must be a positive finite number when provided.")
    return value


def load_config(config_path: str | Path) -> Config:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    if not raw:
        raise ValueError("Config file is empty")

    solver = raw.get("solver", {})
    model_raw = raw.get("model", {})
    sanity_raw = raw.get("sanity_gate", {})
    hppc_raw = raw.get("hppc", {})
    timeseries_raw = raw.get("timeseries", {})
    termination_raw = raw.get("termination", {})
    quality_gate_raw = raw.get("quality_gate", {})
    benchmark_raw = raw.get("benchmark", {})
    identification_raw = raw.get("identification_inputs", {})

    model_type = str(model_raw.get("type", "dfn")).strip().lower()
    if model_type not in _ALLOWED_MODEL_TYPES:
        raise ValueError(f"model.type must be one of {sorted(_ALLOWED_MODEL_TYPES)}")

    chemistry = str(raw.get("chemistry", "nmc")).strip().lower()
    if chemistry not in _ALLOWED_CHEMISTRY:
        raise ValueError(f"chemistry must be one of {sorted(_ALLOWED_CHEMISTRY)}")
    ambient_temp_k = float(raw["ambient_temp_k"])
    initial_cell_temp_k = float(raw.get("initial_cell_temp_k", ambient_temp_k))
    if not np.isfinite(initial_cell_temp_k) or initial_cell_temp_k <= 0:
        raise ValueError("initial_cell_temp_k must be a positive finite number when provided.")

    thermal = str(model_raw.get("thermal", "isothermal")).strip().lower()
    if thermal not in _ALLOWED_THERMAL:
        raise ValueError(f"model.thermal must be one of {sorted(_ALLOWED_THERMAL)}")
    thermal_coupling_raw = model_raw.get("thermal_coupling", {})
    if thermal_coupling_raw is None:
        thermal_coupling_raw = {}
    if not isinstance(thermal_coupling_raw, dict):
        raise ValueError("model.thermal_coupling must be a mapping when provided.")

    legacy_timeseries_boundary = bool(timeseries_raw.get("use_temp_as_ambient_boundary", False))
    if not thermal_coupling_raw and legacy_timeseries_boundary:
        thermal_coupling_enabled = True
        thermal_boundary_mode = "timeseries"
    else:
        thermal_coupling_enabled = bool(thermal_coupling_raw.get("enabled", False))
        thermal_boundary_mode = str(thermal_coupling_raw.get("boundary_mode", "constant")).strip().lower()
    if thermal_boundary_mode not in _ALLOWED_THERMAL_BOUNDARY_MODES:
        raise ValueError(
            f"model.thermal_coupling.boundary_mode must be one of {sorted(_ALLOWED_THERMAL_BOUNDARY_MODES)}"
        )
    thermal_params_raw = model_raw.get("thermal_params", {})
    if thermal_params_raw is None:
        thermal_params_raw = {}
    if not isinstance(thermal_params_raw, dict):
        raise ValueError("model.thermal_params must be a mapping when provided.")
    thermal_params = ThermalParamsConfig(
        total_heat_transfer_coefficient_w_m2_k=_parse_optional_positive_float(
            thermal_params_raw,
            "total_heat_transfer_coefficient_w_m2_k",
            field_label="model.thermal_params.total_heat_transfer_coefficient_w_m2_k",
        ),
        cell_volume_m3=_parse_optional_positive_float(
            thermal_params_raw,
            "cell_volume_m3",
            field_label="model.thermal_params.cell_volume_m3",
        ),
        cell_cooling_surface_area_m2=_parse_optional_positive_float(
            thermal_params_raw,
            "cell_cooling_surface_area_m2",
            field_label="model.thermal_params.cell_cooling_surface_area_m2",
        ),
    )
    thermal_property_scales_raw = model_raw.get("thermal_property_scales", {})
    if thermal_property_scales_raw is None:
        thermal_property_scales_raw = {}
    if not isinstance(thermal_property_scales_raw, dict):
        raise ValueError("model.thermal_property_scales must be a mapping when provided.")
    heat_capacity_scale = float(thermal_property_scales_raw.get("heat_capacity_scale", 1.0))
    thermal_conductivity_scale = float(thermal_property_scales_raw.get("thermal_conductivity_scale", 1.0))
    if not np.isfinite(heat_capacity_scale) or heat_capacity_scale <= 0:
        raise ValueError("model.thermal_property_scales.heat_capacity_scale must be a positive finite number.")
    if not np.isfinite(thermal_conductivity_scale) or thermal_conductivity_scale <= 0:
        raise ValueError(
            "model.thermal_property_scales.thermal_conductivity_scale must be a positive finite number."
        )
    thermal_property_scales = ThermalPropertyScaleConfig(
        heat_capacity_scale=heat_capacity_scale,
        thermal_conductivity_scale=thermal_conductivity_scale,
    )
    temperature_dependence_raw = model_raw.get("temperature_dependence", {})
    if temperature_dependence_raw is None:
        temperature_dependence_raw = {}
    if not isinstance(temperature_dependence_raw, dict):
        raise ValueError("model.temperature_dependence must be a mapping when provided.")
    dfn_temp_raw = temperature_dependence_raw.get("dfn", {})
    if dfn_temp_raw is None:
        dfn_temp_raw = {}
    if not isinstance(dfn_temp_raw, dict):
        raise ValueError("model.temperature_dependence.dfn must be a mapping when provided.")
    arrhenius_overrides_raw = dfn_temp_raw.get("arrhenius_overrides", {})
    if arrhenius_overrides_raw is None:
        arrhenius_overrides_raw = {}
    if not isinstance(arrhenius_overrides_raw, dict):
        raise ValueError("model.temperature_dependence.dfn.arrhenius_overrides must be a mapping.")
    dfn_reference_temp_k = float(dfn_temp_raw.get("reference_temp_k", 298.15))
    if not np.isfinite(dfn_reference_temp_k) or dfn_reference_temp_k <= 0:
        raise ValueError("model.temperature_dependence.dfn.reference_temp_k must be a positive finite number.")
    dfn_arrhenius_overrides = DfnArrheniusOverridesConfig(
        negative_particle_diffusivity_ea_j_mol=_parse_optional_positive_float(
            arrhenius_overrides_raw,
            "negative_particle_diffusivity_ea_j_mol",
            field_label=(
                "model.temperature_dependence.dfn.arrhenius_overrides."
                "negative_particle_diffusivity_ea_j_mol"
            ),
        ),
        positive_particle_diffusivity_ea_j_mol=_parse_optional_positive_float(
            arrhenius_overrides_raw,
            "positive_particle_diffusivity_ea_j_mol",
            field_label=(
                "model.temperature_dependence.dfn.arrhenius_overrides."
                "positive_particle_diffusivity_ea_j_mol"
            ),
        ),
        negative_exchange_current_ea_j_mol=_parse_optional_positive_float(
            arrhenius_overrides_raw,
            "negative_exchange_current_ea_j_mol",
            field_label=(
                "model.temperature_dependence.dfn.arrhenius_overrides."
                "negative_exchange_current_ea_j_mol"
            ),
        ),
        positive_exchange_current_ea_j_mol=_parse_optional_positive_float(
            arrhenius_overrides_raw,
            "positive_exchange_current_ea_j_mol",
            field_label=(
                "model.temperature_dependence.dfn.arrhenius_overrides."
                "positive_exchange_current_ea_j_mol"
            ),
        ),
    )
    temperature_dependence_config = TemperatureDependenceConfig(
        dfn=DfnTemperatureDependenceConfig(
            enabled=bool(dfn_temp_raw.get("enabled", False)),
            reference_temp_k=dfn_reference_temp_k,
            arrhenius_overrides=dfn_arrhenius_overrides,
        )
    )

    try:
        ecm_rc_elements = int(model_raw.get("ecm_rc_elements", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("model.ecm_rc_elements must be an integer.") from exc
    if ecm_rc_elements not in _ALLOWED_ECM_RC_ELEMENTS:
        raise ValueError(f"model.ecm_rc_elements must be one of {sorted(_ALLOWED_ECM_RC_ELEMENTS)}")

    timeseries_csv_path = _resolve_optional_path(timeseries_raw.get("csv_path"), config_path.parent)
    ecm_fitted_pack_path = _resolve_optional_path(model_raw.get("ecm_fitted_pack_json"), config_path.parent)
    ocv_points_path = _resolve_optional_path(identification_raw.get("ocv_points_csv"), config_path.parent)
    cc_cycle_path = _resolve_optional_path(identification_raw.get("cc_cycle_csv"), config_path.parent)
    hppc_points_path = _resolve_optional_path(identification_raw.get("hppc_points_csv"), config_path.parent)

    charge_compare_raw = timeseries_raw.get("charge_compare", {})
    soc_switch_raw = timeseries_raw.get("soc_switch_approx", {})
    rates_raw = charge_compare_raw.get("rates_c", [0.1, 1.0 / 3.0, 1.0])
    rates_c = [float(rate) for rate in rates_raw]
    period_by_rate_raw = charge_compare_raw.get(
        "period_by_rate_s",
        {0.1: 1.0, 1.0 / 3.0: 0.1, 1.0: 0.1},
    )
    period_by_rate_s = {float(rate_key): float(period_value) for rate_key, period_value in period_by_rate_raw.items()}
    soc_start_raw = soc_switch_raw.get("soc_start")
    soc_start = None if soc_start_raw is None else float(soc_start_raw)
    temp_k_raw = soc_switch_raw.get("temp_k")
    temp_k = None if temp_k_raw is None else float(temp_k_raw)
    benchmark_rates = [float(rate) for rate in benchmark_raw.get("rates_c", [0.2, 1.0])]
    benchmark_profiles = [str(item).strip().lower() for item in benchmark_raw.get(
        "profiles", ["dfn_nmc", "dfn_lfp", "ecm_nmc", "ecm_lfp"]
    )]
    if not benchmark_rates or any(rate <= 0 for rate in benchmark_rates):
        raise ValueError("benchmark.rates_c must contain positive C-rates.")
    benchmark_repeats = int(benchmark_raw.get("repeats", 2))
    if benchmark_repeats < 1:
        raise ValueError("benchmark.repeats must be >= 1.")
    if not benchmark_profiles:
        raise ValueError("benchmark.profiles must not be empty.")

    logic = str(termination_raw.get("logic", "any_of")).strip().lower()
    if logic != "any_of":
        raise ValueError("termination.logic currently supports only 'any_of'.")

    conditions: list[TerminationCondition] = []
    for index, condition_raw in enumerate(termination_raw.get("conditions", [])):
        if not isinstance(condition_raw, dict):
            raise ValueError(f"termination.conditions[{index}] must be a mapping.")
        try:
            metric = str(condition_raw["metric"]).strip()
            op = str(condition_raw["op"]).strip()
            threshold = float(condition_raw["threshold"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"termination.conditions[{index}] requires metric/op/threshold with numeric threshold."
            ) from exc
        name_raw = condition_raw.get("name")
        name = None if name_raw is None else str(name_raw)
        conditions.append(
            TerminationCondition(
                metric=metric,
                op=op,
                threshold=threshold,
                name=name,
            )
        )

    termination_config = TerminationConfig(
        enabled=bool(termination_raw.get("enabled", True)),
        logic=logic,
        must_hit=bool(termination_raw.get("must_hit", False)),
        apply_to_experiment_modes=bool(termination_raw.get("apply_to_experiment_modes", True)),
        conditions=conditions,
    )
    _validate_termination_config(termination_config)
    soc_switch_config = SocSwitchApproxConfig(
        enabled=bool(soc_switch_raw.get("enabled", False)),
        soc_start=soc_start,
        discharge_rate_c=float(soc_switch_raw.get("discharge_rate_c", 1.0)),
        discharge_to_soc=float(soc_switch_raw.get("discharge_to_soc", 0.30)),
        charge_rate_c=float(soc_switch_raw.get("charge_rate_c", 1.0)),
        charge_to_soc=float(soc_switch_raw.get("charge_to_soc", 0.90)),
        period_s=float(soc_switch_raw.get("period_s", 0.1)),
        temp_k=temp_k,
    )
    _validate_soc_switch_approx_config(soc_switch_config, float(raw["initial_soc"]))
    if bool(charge_compare_raw.get("enabled", False)) and soc_switch_config.enabled:
        raise ValueError("timeseries.charge_compare and timeseries.soc_switch_approx cannot both be enabled.")

    return Config(
        model_type=model_type,
        chemistry=chemistry,
        nominal_capacity_ah=float(raw["nominal_capacity_ah"]),
        initial_soc=float(raw["initial_soc"]),
        ambient_temp_k=ambient_temp_k,
        initial_cell_temp_k=initial_cell_temp_k,
        voltage_low_v=float(raw["voltage_low_v"]),
        voltage_high_v=float(raw["voltage_high_v"]),
        discharge_rates_c=[float(rate) for rate in raw["discharge_rates_c"]],
        charge_cc_rate=float(raw["charge_cc_rate"]),
        cv_cutoff_c_rate=float(raw["cv_cutoff_c_rate"]),
        rest_min=float(raw["rest_min"]),
        output_dir=Path(raw["output_dir"]),
        parameter_set=str(raw.get("parameter_set", "Chen2020")),
        thermal=thermal,
        thermal_coupling=ThermalCouplingConfig(
            enabled=thermal_coupling_enabled,
            boundary_mode=thermal_boundary_mode,
        ),
        thermal_params=thermal_params,
        thermal_property_scales=thermal_property_scales,
        temperature_dependence=temperature_dependence_config,
        ecm_rc_elements=ecm_rc_elements,
        ecm_fitted_pack_json=ecm_fitted_pack_path,
        period_s=int(raw.get("period_s", 30)),
        solver_rtol=float(solver.get("rtol", 1e-6)),
        solver_atol=float(solver.get("atol", 1e-8)),
        sanity_gate=SanityGateConfig(
            enabled=bool(sanity_raw.get("enabled", True)),
            rate_c=float(sanity_raw.get("rate_c", 0.5)),
            discharge_to_v=float(sanity_raw.get("discharge_to_v", 3.6)),
            charge_to_v=float(sanity_raw.get("charge_to_v", 4.1)),
            period_s=int(sanity_raw.get("period_s", 30)),
        ),
        hppc=HppcConfig(
            enabled=bool(hppc_raw.get("enabled", True)),
            soc_start=float(hppc_raw.get("soc_start", 1.0)),
            soc_end=float(hppc_raw.get("soc_end", 0.05)),
            soc_step=float(hppc_raw.get("soc_step", 0.05)),
            pulse_c_rate=float(hppc_raw.get("pulse_c_rate", 1.0)),
            discharge_s=float(hppc_raw.get("discharge_s", 10.0)),
            charge_s=float(hppc_raw.get("charge_s", 10.0)),
            rest_minutes=float(hppc_raw.get("rest_minutes", 30.0)),
            period_s=float(hppc_raw.get("period_s", 0.1)),
        ),
        timeseries=TimeseriesSuiteConfig(
            enabled=bool(timeseries_raw.get("enabled", False)),
            csv_path=timeseries_csv_path,
            period_s=float(timeseries_raw["period_s"]) if "period_s" in timeseries_raw else None,
            use_temp_as_ambient_boundary=bool(timeseries_raw.get("use_temp_as_ambient_boundary", False)),
            allow_early_stop=bool(timeseries_raw.get("allow_early_stop", False)),
            charge_compare=ChargeCompareConfig(
                enabled=bool(charge_compare_raw.get("enabled", False)),
                soc_start=float(charge_compare_raw.get("soc_start", 0.0)),
                rates_c=rates_c,
                period_by_rate_s=period_by_rate_s,
                cv_cutoff_c_rate=float(charge_compare_raw.get("cv_cutoff_c_rate", 0.05)),
                voltage_high_v=float(charge_compare_raw.get("voltage_high_v", 4.2)),
            ),
            soc_switch_approx=soc_switch_config,
        ),
        quality_gate=QualityGateConfig(
            enabled=bool(quality_gate_raw.get("enabled", True)),
            min_convergence_rate=float(quality_gate_raw.get("min_convergence_rate", 0.95)),
            max_repeat_delta_final_soc=float(quality_gate_raw.get("max_repeat_delta_final_soc", 5e-4)),
            max_repeat_delta_min_v=float(quality_gate_raw.get("max_repeat_delta_min_v", 5e-3)),
            require_polarization_trend=bool(quality_gate_raw.get("require_polarization_trend", True)),
            enforce=bool(quality_gate_raw.get("enforce", True)),
        ),
        identification_inputs=IdentificationInputsConfig(
            enabled=bool(identification_raw.get("enabled", False)),
            strict=bool(identification_raw.get("strict", True)),
            ocv_points_csv=ocv_points_path,
            cc_cycle_csv=cc_cycle_path,
            hppc_points_csv=hppc_points_path,
        ),
        benchmark=BenchmarkConfig(
            enabled=bool(benchmark_raw.get("enabled", True)),
            rates_c=benchmark_rates,
            repeats=benchmark_repeats,
            rest_min=float(benchmark_raw.get("rest_min", 5.0)),
            charge_cc_rate=float(benchmark_raw.get("charge_cc_rate", 0.5)),
            cv_cutoff_c_rate=float(benchmark_raw.get("cv_cutoff_c_rate", 0.05)),
            period_s=int(benchmark_raw.get("period_s", 30)),
            profiles=benchmark_profiles,
        ),
        termination=termination_config,
    )


def _extract_series(solution: pybamm.Solution, names: list[str]) -> np.ndarray:
    for name in names:
        try:
            return solution[name].entries
        except KeyError:
            continue
    raise KeyError(f"None of these variables were found: {names}")


def _extract_soc(solution: pybamm.Solution, config: Config, initial_soc: float | None = None) -> np.ndarray:
    try:
        return _extract_series(solution, ["State of Charge", "X-averaged cell SOC", "SoC"])
    except KeyError:
        discharge_ah = _extract_series(solution, ["Discharge capacity [A.h]"])
        origin_soc = config.initial_soc if initial_soc is None else initial_soc
        return np.clip(origin_soc - (discharge_ah / config.nominal_capacity_ah), 0.0, 1.0)


def _extract_temperature(solution: pybamm.Solution, config: Config, size: int) -> np.ndarray:
    try:
        return _extract_series(
            solution,
            [
                "Volume-averaged cell temperature [K]",
                "Cell temperature [K]",
                "X-averaged cell temperature [K]",
            ],
        )
    except KeyError:
        return np.full(size, config.ambient_temp_k)


def _extract_boundary_temperature(solution: pybamm.Solution, config: Config, size: int) -> np.ndarray:
    try:
        return _extract_series(solution, ["Ambient temperature [K]"])
    except KeyError:
        return np.full(size, config.ambient_temp_k)


def _solution_to_frame(solution: pybamm.Solution, config: Config, initial_soc: float | None = None) -> pd.DataFrame:
    time_s = _extract_series(solution, ["Time [s]"])
    current_a = _extract_series(solution, ["Current [A]"])
    voltage_v = _extract_series(solution, ["Terminal voltage [V]", "Battery voltage [V]", "Voltage [V]"])
    try:
        ocv_v = _extract_series(solution, ["Open-circuit voltage [V]", "Battery open-circuit voltage [V]"])
    except KeyError:
        ocv_v = np.full(len(time_s), np.nan)
    soc = _extract_soc(solution, config, initial_soc=initial_soc)
    cell_temp_k = _extract_temperature(solution, config, len(time_s))
    boundary_temp_k = _extract_boundary_temperature(solution, config, len(time_s))
    return pd.DataFrame(
        {
            "time_s": time_s,
            "current_a": current_a,
            "voltage_v": voltage_v,
            "ocv_v": ocv_v,
            "soc": soc,
            "cell_temperature_k": cell_temp_k,
            "boundary_temperature_k": boundary_temp_k,
        }
    )


def _validate_timeseries_frame(profile: pd.DataFrame) -> pd.DataFrame:
    required = ["time_s", "current_a", "temp_k"]
    missing = [column for column in required if column not in profile.columns]
    if missing:
        raise ValueError(f"Timeseries CSV missing required columns: {missing}")
    if profile.empty:
        raise ValueError("Timeseries CSV must contain at least one row.")

    cleaned = profile.loc[:, required].copy()
    for column in required:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    if cleaned.isna().any().any():
        raise ValueError("Timeseries CSV contains NaN or non-numeric values in required columns.")

    times = cleaned["time_s"].to_numpy(dtype=float)
    if abs(float(times[0])) > 1e-9:
        raise ValueError("Timeseries time_s must start at 0.0 seconds.")
    if np.any(np.diff(times) <= 0):
        raise ValueError("Timeseries time_s must be strictly increasing.")
    return cleaned


def _validate_soc_switch_approx_config(approx: SocSwitchApproxConfig, initial_soc_default: float) -> float:
    if not approx.enabled:
        return float(initial_soc_default)
    soc_start = float(initial_soc_default if approx.soc_start is None else approx.soc_start)
    discharge_rate_c = float(approx.discharge_rate_c)
    charge_rate_c = float(approx.charge_rate_c)
    discharge_to_soc = float(approx.discharge_to_soc)
    charge_to_soc = float(approx.charge_to_soc)
    period_s = float(approx.period_s)

    if not (0.0 <= soc_start <= 1.0):
        raise ValueError("timeseries.soc_switch_approx.soc_start must be within [0, 1].")
    if discharge_rate_c <= 0 or charge_rate_c <= 0:
        raise ValueError("timeseries.soc_switch_approx discharge_rate_c and charge_rate_c must be > 0.")
    if period_s <= 0:
        raise ValueError("timeseries.soc_switch_approx.period_s must be > 0.")
    if not (0.0 <= discharge_to_soc < soc_start):
        raise ValueError("timeseries.soc_switch_approx.discharge_to_soc must satisfy 0 <= discharge_to_soc < soc_start.")
    if not (discharge_to_soc < charge_to_soc <= 1.0):
        raise ValueError(
            "timeseries.soc_switch_approx.charge_to_soc must satisfy discharge_to_soc < charge_to_soc <= 1."
        )
    return soc_start


def _build_soc_switch_approx_profile(config: Config) -> tuple[pd.DataFrame, dict[str, float]]:
    approx = config.timeseries.soc_switch_approx
    soc_start = _validate_soc_switch_approx_config(approx, config.initial_soc)
    discharge_rate_c = float(approx.discharge_rate_c)
    charge_rate_c = float(approx.charge_rate_c)
    discharge_to_soc = float(approx.discharge_to_soc)
    charge_to_soc = float(approx.charge_to_soc)
    period_s = float(approx.period_s)
    temp_k = float(config.ambient_temp_k if approx.temp_k is None else approx.temp_k)

    discharge_duration_s = (soc_start - discharge_to_soc) / discharge_rate_c * 3600.0
    charge_duration_s = (charge_to_soc - discharge_to_soc) / charge_rate_c * 3600.0
    if discharge_duration_s <= 0 or charge_duration_s <= 0:
        raise ValueError("timeseries.soc_switch_approx produced non-positive segment duration.")

    discharge_current_a = discharge_rate_c * config.nominal_capacity_ah
    charge_current_a = -charge_rate_c * config.nominal_capacity_ah

    discharge_time = np.arange(0.0, discharge_duration_s + period_s * 0.5, period_s, dtype=float)
    charge_time_rel = np.arange(period_s, charge_duration_s + period_s * 0.5, period_s, dtype=float)
    charge_time = discharge_duration_s + charge_time_rel
    time_s = np.concatenate([discharge_time, charge_time])
    current_a = np.concatenate(
        [
            np.full(discharge_time.shape, discharge_current_a, dtype=float),
            np.full(charge_time.shape, charge_current_a, dtype=float),
        ]
    )
    temp_series = np.full(time_s.shape, temp_k, dtype=float)

    profile = pd.DataFrame(
        {
            "time_s": time_s,
            "current_a": current_a,
            "temp_k": temp_series,
        }
    )
    metadata = {
        "soc_start": float(soc_start),
        "discharge_to_soc": float(discharge_to_soc),
        "charge_to_soc": float(charge_to_soc),
        "discharge_rate_c": float(discharge_rate_c),
        "charge_rate_c": float(charge_rate_c),
        "period_s": float(period_s),
        "temp_k": float(temp_k),
        "predicted_switch_time_s": float(discharge_duration_s),
        "predicted_end_time_s": float(discharge_duration_s + charge_duration_s),
    }
    return profile, metadata


def _wants_timeseries_thermal_boundary(config: Config) -> bool:
    return bool(
        config.thermal == "lumped"
        and config.thermal_coupling.enabled
        and config.thermal_coupling.boundary_mode == "timeseries"
    )


def _thermal_boundary_fallback_warning(config: Config, mode: str) -> str | None:
    if not _wants_timeseries_thermal_boundary(config):
        return None
    return (
        f"{mode}: thermal boundary_mode='timeseries' requires an explicit time-temperature sequence; "
        "falling back to constant ambient_temp_k."
    )


def _has_thermal_param_overrides(config: Config) -> bool:
    return any(getattr(config.thermal_params, key) is not None for key in _THERMAL_PARAM_TO_PYBAMM_KEY)


def _thermal_param_scope_warning(config: Config, mode: str) -> str | None:
    if not _has_thermal_param_overrides(config):
        return None
    if config.model_type == "dfn" and config.thermal == "lumped":
        return None
    return (
        f"{mode}: model.thermal_params is configured but applies only when model.type='dfn' "
        "and model.thermal='lumped'; keeping bundled thermal parameters for current mode."
    )


def _validate_termination_config(termination: TerminationConfig) -> None:
    if termination.logic != "any_of":
        raise ValueError("termination.logic currently supports only 'any_of'.")
    for index, condition in enumerate(termination.conditions):
        if condition.metric == "temperature_k":
            raise ValueError(
                "termination.conditions[{idx}].metric='temperature_k' is no longer supported. "
                "Use 'cell_temperature_k' or 'boundary_temperature_k'.".format(idx=index)
            )
        if condition.metric not in _ALLOWED_TERMINATION_METRICS:
            raise ValueError(
                f"termination.conditions[{index}].metric must be one of {sorted(_ALLOWED_TERMINATION_METRICS)}"
            )
        if condition.op not in _ALLOWED_TERMINATION_OPS:
            raise ValueError(
                f"termination.conditions[{index}].op must be one of {sorted(_ALLOWED_TERMINATION_OPS)}"
            )
        if not np.isfinite(condition.threshold):
            raise ValueError(f"termination.conditions[{index}].threshold must be finite.")


def _termination_values(frame: pd.DataFrame, metric: str) -> np.ndarray:
    if metric == "current_abs_a":
        return np.abs(frame["current_a"].to_numpy(dtype=float))
    if metric not in frame.columns:
        raise ValueError(f"Termination metric '{metric}' is not available in output frame.")
    return frame[metric].to_numpy(dtype=float)


def _first_hit_index(values: np.ndarray, op: str, threshold: float) -> int | None:
    if op == ">=":
        indices = np.where(values >= threshold)[0]
    elif op == "<=":
        indices = np.where(values <= threshold)[0]
    else:
        raise ValueError(f"Unsupported termination op: {op}")
    if indices.size == 0:
        return None
    return int(indices[0])


def _apply_termination(
    frame: pd.DataFrame, termination: TerminationConfig
) -> tuple[pd.DataFrame, TerminationResult, str | None]:
    if not termination.enabled or not termination.conditions:
        return frame, _default_termination_result(), None

    _validate_termination_config(termination)
    if frame.empty:
        message = "No data points available for termination evaluation."
        result = _default_termination_result()
        result = replace(result, reason=message)
        if termination.must_hit:
            return frame, result, message
        return frame, result, None

    hits: list[tuple[int, int, TerminationCondition, float]] = []
    for condition_index, condition in enumerate(termination.conditions):
        values = _termination_values(frame, condition.metric)
        hit_index = _first_hit_index(values, condition.op, condition.threshold)
        if hit_index is None:
            continue
        hits.append((hit_index, condition_index, condition, float(values[hit_index])))

    if not hits:
        message = "No termination condition was met."
        result = _default_termination_result()
        result = replace(result, reason=message)
        if termination.must_hit:
            return frame, result, message
        return frame, result, None

    hit_index, _, condition, value = min(hits, key=lambda item: (item[0], item[1]))
    hit_time = float(frame["time_s"].iloc[hit_index])
    reason = condition.name or f"{condition.metric} {condition.op} {condition.threshold}"
    result = TerminationResult(
        hit=True,
        reason=reason,
        time_s=hit_time,
        index=hit_index,
        metric=condition.metric,
        op=condition.op,
        threshold=condition.threshold,
        value=value,
    )
    return frame.iloc[: hit_index + 1].copy(), result, None


def _apply_termination_with_context(
    frame: pd.DataFrame, config: Config, *, context_mode: str
) -> tuple[pd.DataFrame, TerminationResult, str | None]:
    if context_mode in {"baseline", "charge_compare"} and not config.termination.apply_to_experiment_modes:
        return frame, _default_termination_result(), None
    return _apply_termination(frame, config.termination)


def _build_hppc_profile(config: Config) -> pd.DataFrame:
    period_s = config.hppc.period_s
    if period_s <= 0:
        raise ValueError("hppc.period_s must be positive.")

    current_a = config.nominal_capacity_ah * config.hppc.pulse_c_rate
    rest_s = config.hppc.rest_minutes * 60.0
    segments = [
        (config.hppc.discharge_s, current_a),
        (rest_s, 0.0),
        (config.hppc.charge_s, -current_a),
        (rest_s, 0.0),
    ]

    times: list[float] = []
    currents: list[float] = []
    cursor = 0.0
    for duration_s, segment_current in segments:
        if duration_s <= 0:
            continue
        n_steps = max(1, int(np.ceil(duration_s / period_s)))
        segment_times = np.linspace(cursor, cursor + duration_s, n_steps + 1)
        if not times:
            times.extend(segment_times.tolist())
            currents.extend([segment_current] * len(segment_times))
        else:
            times.extend(segment_times[1:].tolist())
            currents.extend([segment_current] * (len(segment_times) - 1))
        cursor = float(segment_times[-1])

    if not times:
        raise ValueError("HPPC profile generation produced no timeline points.")
    return pd.DataFrame(
        {
            "time_s": np.array(times, dtype=float),
            "current_a": np.array(currents, dtype=float),
            "temp_k": np.full(len(times), config.ambient_temp_k, dtype=float),
        }
    )


def _load_timeseries_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Timeseries CSV not found: {path}")
    frame = pd.read_csv(path)
    return _validate_timeseries_frame(frame)


def _build_baseline_experiment(config: Config, rate_c: float) -> pybamm.Experiment:
    steps = [
        f"Discharge at {rate_c}C until {config.voltage_low_v} V",
        f"Rest for {config.rest_min} minutes",
        f"Charge at {config.charge_cc_rate}C until {config.voltage_high_v} V",
        f"Hold at {config.voltage_high_v} V until {config.cv_cutoff_c_rate}C",
        f"Rest for {config.rest_min} minutes",
    ]
    return pybamm.Experiment(steps, period=f"{config.period_s} seconds")


def _build_sanity_experiment(config: Config) -> pybamm.Experiment:
    gate = config.sanity_gate
    steps = [
        f"Discharge at {gate.rate_c}C until {gate.discharge_to_v} V",
        f"Charge at {gate.rate_c}C until {gate.charge_to_v} V",
    ]
    return pybamm.Experiment(steps, period=f"{gate.period_s} seconds")


def _build_hppc_experiment(config: Config) -> pybamm.Experiment:
    hppc = config.hppc
    steps = [
        f"Discharge at {hppc.pulse_c_rate}C for {hppc.discharge_s} seconds",
        f"Rest for {hppc.rest_minutes} minutes",
        f"Charge at {hppc.pulse_c_rate}C for {hppc.charge_s} seconds",
        f"Rest for {hppc.rest_minutes} minutes",
    ]
    return pybamm.Experiment(steps, period=f"{hppc.period_s} seconds")


def _format_rate_label(rate_c: float) -> str:
    if np.isclose(rate_c, round(rate_c), atol=1e-10):
        token = f"{rate_c:.1f}"
    elif np.isclose(rate_c * 10.0, round(rate_c * 10.0), atol=1e-10):
        token = f"{rate_c:.1f}"
    else:
        token = f"{rate_c:.3f}".rstrip("0").rstrip(".")
    return token.replace(".", "p")


def _build_charge_compare_experiment(rate_c: float, voltage_high_v: float, cv_cutoff_c_rate: float, period_s: float) -> pybamm.Experiment:
    if period_s <= 0:
        raise ValueError(f"Charge compare period must be positive (rate={rate_c}).")
    steps = [
        f"Charge at {rate_c}C until {voltage_high_v} V",
        f"Hold at {voltage_high_v} V until {cv_cutoff_c_rate}C",
    ]
    return pybamm.Experiment(steps, period=f"{period_s} seconds")


def _load_ecm_fitted_pack(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"ECM fitted pack not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema_version = str(payload.get("schema_version", "")).strip()
    if schema_version != _ECM_TEMP_PACK_SCHEMA_VERSION:
        raise ValueError(
            "Legacy ECM fitted pack format is no longer supported. "
            f"Expected schema_version='{_ECM_TEMP_PACK_SCHEMA_VERSION}'. "
            "Please regenerate the fitted pack with the new SOC×temperature pipeline."
        )

    try:
        ecm_order = int(payload["ecm_order"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ECM fitted pack field 'ecm_order' must be an integer.") from exc
    if ecm_order not in _ALLOWED_ECM_RC_ELEMENTS:
        raise ValueError(f"ECM fitted pack field 'ecm_order' must be one of {sorted(_ALLOWED_ECM_RC_ELEMENTS)}.")

    required_fields = {"soc_axis", "temp_c_axis", "ocv_v", "r0_ohm_map", "r1_ohm_map", "c1_f_map"}
    if ecm_order == 2:
        required_fields |= {"r2_ohm_map", "c2_f_map"}
    missing = sorted(required_fields - set(payload.keys()))
    if missing:
        raise ValueError(f"ECM fitted pack missing fields: {missing}")

    soc_axis = np.asarray(payload["soc_axis"], dtype=float)
    temp_c_axis = np.asarray(payload["temp_c_axis"], dtype=float)
    ocv_v = np.asarray(payload["ocv_v"], dtype=float)
    if soc_axis.ndim != 1 or soc_axis.size < 2:
        raise ValueError("ECM fitted pack 'soc_axis' must be a 1-D array with at least two points.")
    if temp_c_axis.ndim != 1 or temp_c_axis.size < 2:
        raise ValueError("ECM fitted pack 'temp_c_axis' must be a 1-D array with at least two points.")
    if ocv_v.ndim != 1 or ocv_v.size != soc_axis.size:
        raise ValueError("ECM fitted pack 'ocv_v' must be 1-D with same length as 'soc_axis'.")
    if not np.all(np.isfinite(soc_axis)) or not np.all(np.isfinite(temp_c_axis)) or not np.all(np.isfinite(ocv_v)):
        raise ValueError("ECM fitted pack axes/ocv contain non-finite values.")
    if np.any(np.diff(soc_axis) <= 0):
        raise ValueError("ECM fitted pack 'soc_axis' must be strictly increasing.")
    if np.any(np.diff(temp_c_axis) <= 0):
        raise ValueError("ECM fitted pack 'temp_c_axis' must be strictly increasing.")
    if np.min(soc_axis) < -1e-12 or np.max(soc_axis) > 1.0 + 1e-12:
        raise ValueError("ECM fitted pack 'soc_axis' must stay within [0, 1].")

    expected_shape = (temp_c_axis.size, soc_axis.size)

    def _map_field(name: str, *, positive: bool) -> np.ndarray:
        values = np.asarray(payload[name], dtype=float)
        if values.ndim != 2:
            raise ValueError(f"ECM fitted pack '{name}' must be a 2-D array with shape [temp, soc].")
        if values.shape != expected_shape:
            raise ValueError(
                f"ECM fitted pack '{name}' shape mismatch: expected {expected_shape}, got {values.shape}."
            )
        if not np.all(np.isfinite(values)):
            raise ValueError(f"ECM fitted pack '{name}' contains non-finite values.")
        if positive and np.any(values <= 0):
            raise ValueError(f"ECM fitted pack '{name}' must contain positive values.")
        return values

    maps: dict[str, np.ndarray] = {
        "r0_ohm_map": _map_field("r0_ohm_map", positive=True),
        "r1_ohm_map": _map_field("r1_ohm_map", positive=True),
        "c1_f_map": _map_field("c1_f_map", positive=True),
    }
    if ecm_order == 2:
        maps["r2_ohm_map"] = _map_field("r2_ohm_map", positive=True)
        maps["c2_f_map"] = _map_field("c2_f_map", positive=True)

    return {
        "ecm_order": ecm_order,
        "soc_axis": soc_axis,
        "temp_c_axis": temp_c_axis,
        "ocv_v": ocv_v,
        "maps": maps,
    }


def _apply_ecm_fitted_pack(values: pybamm.ParameterValues, pack_path: Path, *, ecm_rc_elements: int) -> None:
    pack = _load_ecm_fitted_pack(pack_path)
    pack_order = int(pack["ecm_order"])
    if pack_order != ecm_rc_elements:
        raise ValueError(
            f"ECM fitted pack order mismatch: pack ecm_order={pack_order}, "
            f"but config model.ecm_rc_elements={ecm_rc_elements}."
        )

    soc_axis = np.asarray(pack["soc_axis"], dtype=float)
    temp_c_axis = np.asarray(pack["temp_c_axis"], dtype=float)
    ocv_v = np.asarray(pack["ocv_v"], dtype=float)
    maps: dict[str, np.ndarray] = pack["maps"]
    soc_min = float(soc_axis[0])
    soc_max = float(soc_axis[-1])
    temp_min = float(temp_c_axis[0])
    temp_max = float(temp_c_axis[-1])

    def _clamp_soc(target: pybamm.Symbol) -> pybamm.Symbol:
        return pybamm.maximum(pybamm.Scalar(soc_min), pybamm.minimum(target, pybamm.Scalar(soc_max)))

    def _clamp_temp_c(target: pybamm.Symbol) -> pybamm.Symbol:
        return pybamm.maximum(pybamm.Scalar(temp_min), pybamm.minimum(target, pybamm.Scalar(temp_max)))

    def _soc_interpolant(target: pybamm.Symbol, data: np.ndarray, name: str) -> pybamm.Symbol:
        return pybamm.Interpolant(soc_axis, data, target, name=name)

    def _temp_soc_interpolant(
        temp_c: pybamm.Symbol, soc: pybamm.Symbol, data: np.ndarray, name: str
    ) -> pybamm.Symbol:
        return pybamm.Interpolant(
            (temp_c_axis, soc_axis),
            data,
            [_clamp_temp_c(temp_c), _clamp_soc(soc)],
            name=name,
        )

    def ocv_fn(sto: pybamm.Symbol) -> pybamm.Symbol:
        return _soc_interpolant(_clamp_soc(sto), ocv_v, "ecm_fitted_ocv")

    def r0_fn(t_cell: pybamm.Symbol, _current: pybamm.Symbol, soc: pybamm.Symbol) -> pybamm.Symbol:
        return _temp_soc_interpolant(t_cell, soc, maps["r0_ohm_map"], "ecm_fitted_r0")

    def r1_fn(t_cell: pybamm.Symbol, _current: pybamm.Symbol, soc: pybamm.Symbol) -> pybamm.Symbol:
        return _temp_soc_interpolant(t_cell, soc, maps["r1_ohm_map"], "ecm_fitted_r1")

    def c1_fn(t_cell: pybamm.Symbol, _current: pybamm.Symbol, soc: pybamm.Symbol) -> pybamm.Symbol:
        return _temp_soc_interpolant(t_cell, soc, maps["c1_f_map"], "ecm_fitted_c1")

    updates: dict[str, Any] = {
        "Open-circuit voltage [V]": ocv_fn,
        "R0 [Ohm]": r0_fn,
        "R1 [Ohm]": r1_fn,
        "C1 [F]": c1_fn,
    }
    if pack_order == 2:

        def r2_fn(t_cell: pybamm.Symbol, _current: pybamm.Symbol, soc: pybamm.Symbol) -> pybamm.Symbol:
            return _temp_soc_interpolant(t_cell, soc, maps["r2_ohm_map"], "ecm_fitted_r2")

        def c2_fn(t_cell: pybamm.Symbol, _current: pybamm.Symbol, soc: pybamm.Symbol) -> pybamm.Symbol:
            return _temp_soc_interpolant(t_cell, soc, maps["c2_f_map"], "ecm_fitted_c2")

        updates["R2 [Ohm]"] = r2_fn
        updates["C2 [F]"] = c2_fn
        updates["Element-2 initial overpotential [V]"] = 0.0
    values.update(updates)


def _arrhenius_multiplier(
    temperature_k: pybamm.Symbol, *, ea_j_mol: float, reference_temp_k: float
) -> pybamm.Symbol:
    return pybamm.exp(
        (float(ea_j_mol) / pybamm.constants.R)
        * ((1.0 / float(reference_temp_k)) - (1.0 / temperature_k))
    )


def _wrap_parameter_with_arrhenius_scaling(
    base_parameter: Any, *, ea_j_mol: float, reference_temp_k: float
) -> Any:
    def wrapped(*args: Any) -> Any:
        base_value = base_parameter(*args) if callable(base_parameter) else base_parameter
        if len(args) == 0:
            return base_value
        temperature_k = args[-1]
        return base_value * _arrhenius_multiplier(
            temperature_k,
            ea_j_mol=float(ea_j_mol),
            reference_temp_k=float(reference_temp_k),
        )

    return wrapped


def _apply_dfn_arrhenius_overrides(
    values: pybamm.ParameterValues, config: Config
) -> list[dict[str, Any]]:
    if config.model_type != "dfn":
        return []
    dfn_temp = config.temperature_dependence.dfn
    if not dfn_temp.enabled:
        return []

    updates: dict[str, Any] = {}
    applied: list[dict[str, Any]] = []
    for override_field, parameter_key in _DFN_ARRHENIUS_PARAMETER_MAP.items():
        ea_value = getattr(dfn_temp.arrhenius_overrides, override_field)
        if ea_value is None:
            continue
        if parameter_key not in values.keys():
            raise ValueError(
                f"Temperature dependence override requires parameter '{parameter_key}' in parameter set "
                f"'{config.parameter_set}'."
            )
        base_parameter = values[parameter_key]
        updates[parameter_key] = _wrap_parameter_with_arrhenius_scaling(
            base_parameter,
            ea_j_mol=float(ea_value),
            reference_temp_k=float(dfn_temp.reference_temp_k),
        )
        applied.append(
            {
                "parameter": parameter_key,
                "activation_energy_j_mol": float(ea_value),
                "reference_temp_k": float(dfn_temp.reference_temp_k),
                "source": f"model.temperature_dependence.dfn.arrhenius_overrides.{override_field}",
            }
        )

    if updates:
        values.update(updates)
    return applied


def _apply_lfp_lumped_thermal_proxy_overrides(
    values: pybamm.ParameterValues, config: Config
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not (config.model_type == "dfn" and config.chemistry == "lfp" and config.thermal == "lumped"):
        return {}, []
    proxy = pybamm.ParameterValues(_LFP_LUMPED_PROXY_SOURCE_SET)
    updates: dict[str, Any] = {}
    applied: list[dict[str, Any]] = []
    for parameter_key in _LFP_LUMPED_PROXY_PARAMETER_KEYS:
        if parameter_key in values.keys():
            continue
        if parameter_key not in proxy.keys():
            raise ValueError(
                f"LFP lumped thermal proxy requires parameter '{parameter_key}' in source "
                f"'{_LFP_LUMPED_PROXY_SOURCE_SET}'."
            )
        source_value = proxy[parameter_key]
        try:
            target_value: Any = float(source_value)
        except (TypeError, ValueError):
            target_value = source_value
        updates[parameter_key] = target_value
        applied.append(
            {
                "parameter": parameter_key,
                "base_value": None,
                "target_value": target_value,
                "source": f"lfp_lumped_proxy:{_LFP_LUMPED_PROXY_SOURCE_SET}",
            }
        )
    return updates, applied


def _apply_thermal_property_scales(
    values: pybamm.ParameterValues, config: Config
) -> list[dict[str, Any]]:
    if not (config.model_type == "dfn" and config.thermal == "lumped"):
        return []
    scales = config.thermal_property_scales
    entries: list[dict[str, Any]] = []
    updates: dict[str, Any] = {}

    def _scale_many(parameter_keys: tuple[str, ...], *, scale: float, scale_name: str) -> None:
        if np.isclose(scale, 1.0, atol=1e-12):
            return
        for parameter_key in parameter_keys:
            if parameter_key not in values.keys():
                continue
            source_value = values[parameter_key]
            try:
                base_value = float(source_value)
            except (TypeError, ValueError):
                continue
            target_value = float(base_value * scale)
            updates[parameter_key] = target_value
            entries.append(
                {
                    "parameter": parameter_key,
                    "base_value": base_value,
                    "target_value": target_value,
                    "source": f"model.thermal_property_scales.{scale_name}",
                }
            )

    _scale_many(
        _THERMAL_HEAT_CAPACITY_PARAMETER_KEYS,
        scale=float(scales.heat_capacity_scale),
        scale_name="heat_capacity_scale",
    )
    _scale_many(
        _THERMAL_CONDUCTIVITY_PARAMETER_KEYS,
        scale=float(scales.thermal_conductivity_scale),
        scale_name="thermal_conductivity_scale",
    )
    if updates:
        values.update(updates)
    return entries


def _build_parameter_values(config: Config) -> tuple[pybamm.ParameterValues, dict[str, Any]]:
    values = pybamm.ParameterValues(config.parameter_set)
    capacity_key = None
    for key in ["Nominal cell capacity [A.h]", "Cell capacity [A.h]"]:
        if key in values.keys():
            capacity_key = key
            break
    if capacity_key is None:
        raise ValueError("Parameter set must define either 'Nominal cell capacity [A.h]' or 'Cell capacity [A.h]'.")

    base_nominal = float(values[capacity_key])
    if base_nominal <= 0:
        raise ValueError("Base nominal cell capacity must be positive.")

    parallel_key = "Number of electrodes connected in parallel to make a cell"
    has_parallel = parallel_key in values.keys()
    base_parallel = float(values[parallel_key]) if has_parallel else 1.0

    ratio = config.nominal_capacity_ah / base_nominal
    parallel_after = base_parallel * ratio
    updates: dict[str, Any] = {
        capacity_key: config.nominal_capacity_ah,
    }
    if has_parallel and config.model_type == "dfn":
        updates[parallel_key] = parallel_after
    elif has_parallel:
        updates[parallel_key] = base_parallel
    if "Ambient temperature [K]" in values.keys():
        updates["Ambient temperature [K]"] = config.ambient_temp_k
    if "Initial temperature [K]" in values.keys():
        updates["Initial temperature [K]"] = config.initial_cell_temp_k
    if "Initial SoC" in values.keys():
        updates["Initial SoC"] = _effective_initial_soc(config, config.initial_soc)
    thermal_overrides_requested = _has_thermal_param_overrides(config)
    thermal_overrides_applied = bool(config.model_type == "dfn" and config.thermal == "lumped")
    thermal_overrides: list[dict[str, Any]] = []
    if thermal_overrides_requested and thermal_overrides_applied:
        for config_key, parameter_key in _THERMAL_PARAM_TO_PYBAMM_KEY.items():
            target_value = getattr(config.thermal_params, config_key)
            if target_value is None:
                continue
            base_value: Any = None
            if parameter_key in values.keys():
                source = values[parameter_key]
                try:
                    base_value = float(source)
                except (TypeError, ValueError):
                    base_value = source
            updates[parameter_key] = float(target_value)
            thermal_overrides.append(
                {
                    "config_key": config_key,
                    "parameter": parameter_key,
                    "base_value": base_value,
                    "target_value": float(target_value),
                    "source": "model.thermal_params",
                }
            )
    lfp_lumped_proxy_updates, lfp_lumped_proxy_overrides = _apply_lfp_lumped_thermal_proxy_overrides(values, config)
    updates.update(lfp_lumped_proxy_updates)
    values.update(updates)
    thermal_property_scale_overrides = _apply_thermal_property_scales(values, config)
    dfn_arrhenius_applied = _apply_dfn_arrhenius_overrides(values, config)
    if config.model_type == "ecm" and config.ecm_fitted_pack_json is not None:
        _apply_ecm_fitted_pack(values, config.ecm_fitted_pack_json, ecm_rc_elements=config.ecm_rc_elements)
    scaling = {
        "model_type": config.model_type,
        "chemistry": config.chemistry,
        "capacity_parameter": capacity_key,
        "base_nominal_capacity_ah": base_nominal,
        "target_nominal_capacity_ah": config.nominal_capacity_ah,
        "scale_ratio": ratio,
        "parallel_parameter_present": float(has_parallel),
        "parallel_before": base_parallel,
        "parallel_after": parallel_after,
        "thermal_overrides_requested": thermal_overrides_requested,
        "thermal_overrides_applied": thermal_overrides_applied and bool(
            thermal_overrides or lfp_lumped_proxy_overrides or thermal_property_scale_overrides
        ),
        "thermal_overrides": thermal_overrides,
        "lfp_lumped_thermal_proxy_overrides": lfp_lumped_proxy_overrides,
        "thermal_property_scale_overrides": thermal_property_scale_overrides,
        "thermal_property_scales": {
            "heat_capacity_scale": float(config.thermal_property_scales.heat_capacity_scale),
            "thermal_conductivity_scale": float(config.thermal_property_scales.thermal_conductivity_scale),
        },
        "dfn_temperature_dependence_enabled": bool(config.temperature_dependence.dfn.enabled),
        "dfn_temperature_reference_temp_k": float(config.temperature_dependence.dfn.reference_temp_k),
        "dfn_arrhenius_overrides_applied": dfn_arrhenius_applied,
    }
    return values, scaling


def _write_parameter_audit(config: Config, output_dir: Path, scaling: dict[str, Any]) -> Path:
    audit_path = output_dir / "parameter_audit.json"
    quality_level = _resolve_parameter_quality_level(config)
    scaled_entries = [
        {
            "parameter": scaling["capacity_parameter"],
            "base_value": scaling["base_nominal_capacity_ah"],
            "target_value": scaling["target_nominal_capacity_ah"],
            "migration_tag": "scaled_from_reference_cell",
        }
    ]
    if bool(scaling["parallel_parameter_present"]):
        scaled_entries.append(
            {
                "parameter": "Number of electrodes connected in parallel to make a cell",
                "base_value": scaling["parallel_before"],
                "target_value": scaling["parallel_after"],
                "migration_tag": "parallel_count_scaled_with_capacity"
                if config.model_type == "dfn"
                else "parallel_count_kept_for_model_compatibility",
            }
        )

    if config.chemistry == "lfp":
        pending_identification = [
            "LFP OCP fit under target temperature window",
            "Rate-dependent resistance and diffusion calibration via HPPC",
            "Cell-specific thermal behavior and heat transfer coefficients",
        ]
    else:
        pending_identification = [
            "NMC622-specific OCP fit",
            "NMC622 reaction-rate constants over SOC/temperature",
            "Cell-specific thermal behavior and heat transfer coefficients",
            "Rate-dependent resistance and diffusion calibration via HPPC",
        ]

    if config.model_type == "ecm":
        reused = [
            "Equivalent-circuit structure (Thevenin) and default ECM parameters from PyBaMM",
        ]
    else:
        reused = [
            f"Electrolyte transport and kinetic baseline from {config.parameter_set}",
            "Default DFN structure and domain assumptions from PyBaMM",
        ]

    disclaimer = (
        "Proxy parameters for baseline simulation only; absolute accuracy is not yet claimed."
        if quality_level == "proxy"
        else "Identified ECM fitted pack in use; results are calibrated against DFN reference curves."
    )
    thermal_override_entries = list(scaling.get("thermal_overrides", []))
    thermal_proxy_entries = list(scaling.get("lfp_lumped_thermal_proxy_overrides", []))
    thermal_scale_entries = list(scaling.get("thermal_property_scale_overrides", []))
    dfn_arrhenius_entries = list(scaling.get("dfn_arrhenius_overrides_applied", []))
    audit = {
        "base_parameter_set": config.parameter_set,
        "parameter_pack": {
            "model_type": config.model_type,
            "chemistry": config.chemistry,
            "source": "PyBaMM bundled parameter set",
            "version": pybamm.__version__,
            "quality_level": quality_level,
            "ecm_rc_elements": config.ecm_rc_elements if config.model_type == "ecm" else None,
            "migration_tag": "identified_pack_for_simulation"
            if quality_level == "identified"
            else "proxy_pack_for_baseline_simulation",
            "fitted_pack_json": str(config.ecm_fitted_pack_json) if config.ecm_fitted_pack_json is not None else None,
        },
        "scaling": scaling,
        "scaled": scaled_entries,
        "thermal_overrides": thermal_override_entries,
        "thermal_proxy_overrides": thermal_proxy_entries,
        "thermal_scale_overrides": thermal_scale_entries,
        "temperature_dependence": {
            "dfn_enabled": bool(config.temperature_dependence.dfn.enabled),
            "dfn_reference_temp_k": float(config.temperature_dependence.dfn.reference_temp_k),
            "dfn_arrhenius_overrides_applied": dfn_arrhenius_entries,
        },
        "reused": reused,
        "pending_identification": pending_identification,
        "disclaimer": disclaimer,
    }
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit_path


def _write_empty_timeseries(csv_path: Path) -> None:
    pd.DataFrame(
        columns=[
            "time_s",
            "current_a",
            "voltage_v",
            "ocv_v",
            "soc",
            "cell_temperature_k",
            "boundary_temperature_k",
        ]
    ).to_csv(csv_path, index=False)


def _strictly_monotonic(values: np.ndarray) -> bool:
    if values.size < 2:
        return True
    return bool(np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0))


def _validate_identification_frame(
    *,
    dataset_name: str,
    csv_path: Path,
    required_columns: list[str],
) -> tuple[dict[str, Any], bool]:
    report: dict[str, Any] = {
        "path": str(csv_path),
        "provided": True,
        "valid": False,
        "row_count": 0,
        "errors": [],
    }
    try:
        frame = pd.read_csv(csv_path)
    except Exception as exc:
        report["errors"].append(f"{dataset_name}: read failed - {exc}")
        return report, False

    report["row_count"] = int(len(frame))
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        report["errors"].append(f"{dataset_name}: missing required columns {missing}")
        return report, False

    numeric = frame.loc[:, required_columns].copy()
    for column in required_columns:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    if numeric.isna().any().any():
        report["errors"].append(f"{dataset_name}: contains NaN or non-numeric values.")
        return report, False

    if dataset_name == "ocv_points":
        soc = numeric["soc"].to_numpy(dtype=float)
        if np.any((soc < 0) | (soc > 1)):
            report["errors"].append("ocv_points: soc must be within [0, 1].")
        if not _strictly_monotonic(soc):
            report["errors"].append("ocv_points: soc must be strictly monotonic.")
    elif dataset_name == "cc_cycle":
        time_s = numeric["time_s"].to_numpy(dtype=float)
        if np.any(np.diff(time_s) <= 0):
            report["errors"].append("cc_cycle: time_s must be strictly increasing.")
    elif dataset_name == "hppc_points":
        soc_target = numeric["soc_target"].to_numpy(dtype=float)
        if np.any((soc_target < 0) | (soc_target > 1)):
            report["errors"].append("hppc_points: soc_target must be within [0, 1].")
        if not _strictly_monotonic(soc_target):
            report["errors"].append("hppc_points: soc_target must be strictly monotonic.")
        if np.any(numeric["r_dis_10s_ohm"].to_numpy(dtype=float) <= 0):
            report["errors"].append("hppc_points: r_dis_10s_ohm must be > 0.")
        if np.any(numeric["r_chg_10s_ohm"].to_numpy(dtype=float) <= 0):
            report["errors"].append("hppc_points: r_chg_10s_ohm must be > 0.")

    if np.any(numeric["temp_k"].to_numpy(dtype=float) <= 0):
        report["errors"].append(f"{dataset_name}: temp_k must be > 0 K.")

    report["valid"] = len(report["errors"]) == 0
    return report, bool(report["valid"])


def validate_identification_inputs(config: Config) -> dict[str, Any]:
    ident = config.identification_inputs
    payload: dict[str, Any] = {
        "enabled": ident.enabled,
        "strict": ident.strict,
        "passed": True,
        "datasets": {
            "ocv_points": {"provided": False, "valid": False, "path": None, "row_count": 0, "errors": []},
            "cc_cycle": {"provided": False, "valid": False, "path": None, "row_count": 0, "errors": []},
            "hppc_points": {"provided": False, "valid": False, "path": None, "row_count": 0, "errors": []},
        },
        "errors": [],
    }
    if not ident.enabled:
        return payload

    datasets = [
        ("ocv_points", ident.ocv_points_csv, ["soc", "ocv_v", "temp_k"]),
        ("cc_cycle", ident.cc_cycle_csv, ["time_s", "current_a", "voltage_v", "temp_k"]),
        ("hppc_points", ident.hppc_points_csv, ["soc_target", "r_dis_10s_ohm", "r_chg_10s_ohm", "temp_k"]),
    ]
    for dataset_name, dataset_path, required_columns in datasets:
        if dataset_path is None:
            if ident.strict:
                payload["errors"].append(f"{dataset_name}: path not provided.")
            continue
        report, valid = _validate_identification_frame(
            dataset_name=dataset_name,
            csv_path=dataset_path,
            required_columns=required_columns,
        )
        payload["datasets"][dataset_name] = report
        if not valid:
            payload["errors"].extend(report["errors"])

    provided_all = all(path is not None for _, path, _ in datasets)
    datasets_valid = all(payload["datasets"][name]["valid"] for name, _, _ in datasets)
    if ident.strict:
        payload["passed"] = bool(provided_all and datasets_valid and not payload["errors"])
    else:
        any_valid = any(payload["datasets"][name]["valid"] for name, _, _ in datasets)
        payload["passed"] = bool(any_valid and not payload["errors"])
    return payload


def _merge_identification_validation(
    summary: dict[str, Any],
    validation: dict[str, Any],
    *,
    context: str,
) -> None:
    summary["identification_inputs_validation"] = validation
    if not validation.get("enabled", False):
        return
    if validation.get("strict", False) and not validation.get("passed", False):
        summary["all_converged"] = False
        warnings = summary.get("warnings", [])
        warnings.append(f"{context}: strict identification input validation failed.")
        summary["warnings"] = _dedupe_messages(warnings)


def _load_matplotlib_pyplot():
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _solve_with_warning_capture(
    sim: pybamm.Simulation,
    initial_soc: float,
    t_eval: np.ndarray | None = None,
    use_initial_soc: bool = True,
) -> tuple[pybamm.Solution | None, float, list[str], str | None]:
    logger = pybamm.logger
    capture = _WarningCapture()
    original_level = logger.level
    logger.addHandler(capture)
    if original_level > logging.WARNING:
        logger.setLevel(logging.WARNING)

    start = time.perf_counter()
    try:
        solve_kwargs: dict[str, Any] = {}
        if t_eval is not None:
            solve_kwargs["t_eval"] = t_eval
        if use_initial_soc:
            solve_kwargs["initial_soc"] = initial_soc
        solution = sim.solve(**solve_kwargs)
        error = None
    except Exception as exc:
        solution = None
        error = str(exc)
    runtime = time.perf_counter() - start

    logger.removeHandler(capture)
    logger.setLevel(original_level)
    return solution, runtime, _dedupe_messages(capture.messages), error


def simulate_from_timeseries(
    config: Config,
    base_values: pybamm.ParameterValues,
    profile: pd.DataFrame,
    initial_soc: float,
) -> tuple[pd.DataFrame | None, float, list[str], str | None]:
    validated = _validate_timeseries_frame(profile)
    times = validated["time_s"].to_numpy(dtype=float)
    currents = validated["current_a"].to_numpy(dtype=float)
    input_temps = validated["temp_k"].to_numpy(dtype=float)

    model = _build_core_model(config)
    idaklu_solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    effective_initial_soc = _effective_initial_soc(config, initial_soc)
    use_initial_soc = _uses_initial_soc_argument(config)
    parameter_values = _set_initial_soc_if_supported(base_values, effective_initial_soc)
    pre_warnings: list[str] = []
    boundary_from_timeseries = False
    updates: dict[str, Any] = {
        "Current function [A]": pybamm.Interpolant(times, currents, pybamm.t),
    }
    disable_built_in_cutoffs = True
    if config.model_type == "dfn" and config.chemistry == "lfp" and config.thermal == "lumped":
        disable_built_in_cutoffs = False
        pre_warnings.append(
            "Keeping built-in voltage cutoffs for LFP DFN lumped replay to avoid non-physical divergence."
        )
    if disable_built_in_cutoffs:
        # Replay mode should follow the provided current profile without stopping at built-in cutoffs.
        if "Lower voltage cut-off [V]" in parameter_values.keys():
            updates["Lower voltage cut-off [V]"] = 0.0
        if "Upper voltage cut-off [V]" in parameter_values.keys():
            updates["Upper voltage cut-off [V]"] = 6.0
    if _wants_timeseries_thermal_boundary(config):
        if "Ambient temperature [K]" in parameter_values.keys():
            updates["Ambient temperature [K]"] = pybamm.Interpolant(times, input_temps, pybamm.t)
            boundary_from_timeseries = True
        else:
            pre_warnings.append(
                "Timeseries: thermal boundary_mode='timeseries' requested but parameter set has no "
                "'Ambient temperature [K]'; using constant ambient_temp_k."
            )
    parameter_values.update(updates)
    def _solve_with_solver(solver_obj: pybamm.BaseSolver) -> tuple[pybamm.Solution | None, float, list[str], str | None]:
        sim = pybamm.Simulation(
            model=model,
            parameter_values=parameter_values,
            solver=solver_obj,
        )
        return _solve_with_warning_capture(
            sim, effective_initial_soc, t_eval=times, use_initial_soc=use_initial_soc
        )

    prefer_casadi_primary = _prefer_casadi_primary_solver(config)
    if prefer_casadi_primary:
        primary_solver: pybamm.BaseSolver = pybamm.CasadiSolver(
            mode="safe",
            rtol=config.solver_rtol,
            atol=config.solver_atol,
        )
    else:
        primary_solver = idaklu_solver

    solution, runtime, warnings, error = _solve_with_solver(primary_solver)
    if prefer_casadi_primary:
        warnings = _dedupe_messages(
            pre_warnings
            + ["Using CasadiSolver(mode='safe') as primary solver for LFP DFN lumped thermal replay."]
            + warnings
        )
    else:
        warnings = _dedupe_messages(pre_warnings + warnings)
    tried_casadi = prefer_casadi_primary

    def _retry_with_casadi(
        reason: str,
        *,
        rtol: float,
        atol: float,
    ) -> tuple[pybamm.Solution | None, float, list[str], str | None]:
        nonlocal tried_casadi
        tried_casadi = True
        fallback_solver = pybamm.CasadiSolver(mode="safe", rtol=rtol, atol=atol)
        fb_solution, fb_runtime, fb_warnings, fb_error = _solve_with_solver(fallback_solver)
        merged_warnings = _dedupe_messages(
            warnings
            + [reason]
            + fb_warnings
        )
        return fb_solution, fb_runtime, merged_warnings, fb_error

    total_runtime = runtime
    if (
        not prefer_casadi_primary
        and (solution is None or isinstance(solution, pybamm.EmptySolution))
        and _should_retry_with_casadi(config, error)
    ):
        solution, fb_runtime, warnings, fb_error = _retry_with_casadi(
            "IDAKLU solver failed with stiff error signature; retrying with CasadiSolver(mode='safe').",
            rtol=config.solver_rtol,
            atol=config.solver_atol,
        )
        total_runtime += fb_runtime
        if solution is not None and not isinstance(solution, pybamm.EmptySolution):
            error = fb_error
        else:
            error = fb_error or error
        if (
            (solution is None or isinstance(solution, pybamm.EmptySolution))
            and _should_retry_with_casadi(config, error)
        ):
            relaxed_rtol = max(config.solver_rtol * 20.0, 1e-5)
            relaxed_atol = max(config.solver_atol * 20.0, 1e-7)
            solution, fb_runtime_relaxed, warnings, fb_error_relaxed = _retry_with_casadi(
                (
                    "CasadiSolver(mode='safe') fallback failed with strict tolerances; "
                    "retrying once with relaxed tolerances."
                ),
                rtol=relaxed_rtol,
                atol=relaxed_atol,
            )
            total_runtime += fb_runtime_relaxed
            if solution is not None and not isinstance(solution, pybamm.EmptySolution):
                error = fb_error_relaxed
            else:
                error = fb_error_relaxed or error

    runtime = total_runtime
    if solution is None or isinstance(solution, pybamm.EmptySolution):
        return None, runtime, warnings, error or "Timeseries simulation produced an empty solution."

    dense = _solution_to_frame(solution, config, initial_soc=effective_initial_soc)
    dense_time = dense["time_s"].to_numpy(dtype=float)
    if dense_time.size == 0:
        return None, runtime, warnings, "Timeseries simulation produced no time points."
    if dense_time[-1] < times[-1] - 1e-9 and not tried_casadi:
        solution, fb_runtime, warnings, fb_error = _retry_with_casadi(
            (
                "IDAKLU solver terminated early in replay mode; "
                "retrying with CasadiSolver(mode='safe')."
            )
            ,
            rtol=config.solver_rtol,
            atol=config.solver_atol,
        )
        total_runtime += fb_runtime
        runtime = total_runtime
        error = fb_error
        if solution is None or isinstance(solution, pybamm.EmptySolution):
            return None, runtime, warnings, error or "Timeseries simulation produced an empty solution."
        dense = _solution_to_frame(solution, config, initial_soc=effective_initial_soc)
        dense_time = dense["time_s"].to_numpy(dtype=float)
        if dense_time.size == 0:
            return None, runtime, warnings, "Timeseries simulation produced no time points."

    output_times = times
    output_currents = currents
    output_input_temps = input_temps
    if dense_time[-1] < times[-1] - 1e-9:
        if config.timeseries.allow_early_stop:
            keep_mask = times <= dense_time[-1] + 1e-9
            if not np.any(keep_mask):
                return (
                    None,
                    runtime,
                    warnings,
                    "Timeseries simulation terminated before first sample point.",
                )
            output_times = times[keep_mask]
            output_currents = currents[keep_mask]
            output_input_temps = input_temps[keep_mask]
            warnings = _dedupe_messages(
                warnings
                + [
                    (
                        "Timeseries simulation terminated early "
                        f"({dense_time[-1]:.3f}s < {times[-1]:.3f}s); "
                        "truncated output because timeseries.allow_early_stop=true."
                    )
                ]
            )
        else:
            return (
                None,
                runtime,
                warnings,
                f"Timeseries simulation terminated early ({dense_time[-1]:.3f}s < {times[-1]:.3f}s).",
            )

    dense_cell_temperature = dense["cell_temperature_k"].to_numpy(dtype=float)
    if "boundary_temperature_k" in dense.columns:
        dense_boundary_temperature = dense["boundary_temperature_k"].to_numpy(dtype=float)
    else:
        dense_boundary_temperature = np.full(dense_time.shape, config.ambient_temp_k, dtype=float)

    if config.thermal == "lumped":
        cell_temperature_out = np.interp(output_times, dense_time, dense_cell_temperature)
    else:
        cell_temperature_out = output_input_temps

    if _wants_timeseries_thermal_boundary(config) and boundary_from_timeseries:
        boundary_temperature_out = output_input_temps
    elif config.thermal == "lumped":
        boundary_temperature_out = np.interp(output_times, dense_time, dense_boundary_temperature)
    else:
        boundary_temperature_out = output_input_temps

    frame = pd.DataFrame(
        {
            "time_s": output_times,
            "current_a": output_currents,
            "voltage_v": np.interp(output_times, dense_time, dense["voltage_v"].to_numpy(dtype=float)),
            "ocv_v": np.interp(output_times, dense_time, dense["ocv_v"].to_numpy(dtype=float)),
            "soc": np.interp(output_times, dense_time, dense["soc"].to_numpy(dtype=float)),
            "cell_temperature_k": cell_temperature_out,
            "boundary_temperature_k": boundary_temperature_out,
        }
    )
    return frame, runtime, warnings, error


def _run_sanity_gate(config: Config, base_values: pybamm.ParameterValues, output_dir: Path) -> SanityGateResult:
    json_path = output_dir / "sanity_gate.json"
    csv_path = output_dir / "sanity_gate.csv"
    if not config.sanity_gate.enabled:
        _write_empty_timeseries(csv_path)
        result = SanityGateResult(
            enabled=False,
            passed=True,
            converged=True,
            has_positive_current=False,
            has_negative_current=False,
            warning_messages=[],
            runtime_s=0.0,
            artifact_csv=str(csv_path),
            artifact_json=str(json_path),
            error=None,
        )
        json_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        return result

    model = _build_core_model(config)
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    initial_soc = _effective_initial_soc(config, config.initial_soc)
    parameter_values = _set_initial_soc_if_supported(base_values, initial_soc)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=parameter_values,
        experiment=_build_sanity_experiment(config),
        solver=solver,
    )
    solution, runtime, warnings, error = _solve_with_warning_capture(
        sim, initial_soc, use_initial_soc=_uses_initial_soc_argument(config)
    )
    boundary_warning = _thermal_boundary_fallback_warning(config, mode="sanity_gate")
    if boundary_warning:
        warnings = _dedupe_messages([boundary_warning] + warnings)
    has_pos = False
    has_neg = False
    if solution is None or isinstance(solution, pybamm.EmptySolution):
        _write_empty_timeseries(csv_path)
        converged = False
        if error is None:
            error = "Sanity gate produced an empty solution."
    else:
        frame = _solution_to_frame(solution, config)
        frame.to_csv(csv_path, index=False)
        current = frame.loc[np.abs(frame["current_a"]) > 1e-6, "current_a"]
        has_pos = bool((current > 0).any())
        has_neg = bool((current < 0).any())
        converged = True

    infeasible = [m for m in warnings if _warning_is_infeasible(m)]
    passed = converged and error is None and has_pos and has_neg and not infeasible
    result = SanityGateResult(
        enabled=True,
        passed=passed,
        converged=converged,
        has_positive_current=has_pos,
        has_negative_current=has_neg,
        warning_messages=warnings,
        runtime_s=runtime,
        artifact_csv=str(csv_path),
        artifact_json=str(json_path),
        error=error,
    )
    json_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result


def _run_baseline_case(config: Config, base_values: pybamm.ParameterValues, rate_c: float, output_dir: Path) -> RunSummary:
    csv_path = output_dir / f"{_case_id(rate_c)}.csv"
    model = _build_core_model(config)
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    initial_soc = _effective_initial_soc(config, config.initial_soc)
    parameter_values = _set_initial_soc_if_supported(base_values, initial_soc)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=parameter_values,
        experiment=_build_baseline_experiment(config, rate_c),
        solver=solver,
    )
    start = time.perf_counter()
    try:
        solve_kwargs: dict[str, Any] = {}
        if _uses_initial_soc_argument(config):
            solve_kwargs["initial_soc"] = initial_soc
        solution = sim.solve(**solve_kwargs)
    except Exception as exc:
        return RunSummary(
            case_id=_case_id(rate_c),
            converged=False,
            min_v=None,
            max_v=None,
            final_soc=None,
            runtime_s=time.perf_counter() - start,
            csv_path=None,
            termination=replace(_default_termination_result(), reason="Simulation solve failed."),
            error=str(exc),
        )
    runtime = time.perf_counter() - start
    frame = _solution_to_frame(solution, config)
    frame, termination, termination_error = _apply_termination_with_context(
        frame, config, context_mode="baseline"
    )
    frame.to_csv(csv_path, index=False)
    converged = termination_error is None
    case_error = termination_error
    return RunSummary(
        case_id=_case_id(rate_c),
        converged=converged,
        min_v=float(np.min(frame["voltage_v"])),
        max_v=float(np.max(frame["voltage_v"])),
        final_soc=float(frame["soc"].iloc[-1]),
        runtime_s=runtime,
        csv_path=str(csv_path),
        termination=termination,
        error=case_error,
    )


def _cc_transition_time(frame: pd.DataFrame, voltage_high_v: float) -> float | None:
    voltage = frame["voltage_v"].to_numpy(dtype=float)
    time_s = frame["time_s"].to_numpy(dtype=float)
    threshold = voltage_high_v - 1e-3
    indices = np.where(voltage >= threshold)[0]
    if indices.size == 0:
        return None
    return float(time_s[int(indices[0])])


def _run_charge_compare_case(
    config: Config,
    base_values: pybamm.ParameterValues,
    rate_c: float,
    period_s: float,
    output_dir: Path,
) -> tuple[ChargeCompareCaseResult, list[str]]:
    case_id = f"charge_case_{_format_rate_label(rate_c)}c"
    csv_path = output_dir / f"{case_id}.csv"
    experiment = _build_charge_compare_experiment(
        rate_c=rate_c,
        voltage_high_v=config.timeseries.charge_compare.voltage_high_v,
        cv_cutoff_c_rate=config.timeseries.charge_compare.cv_cutoff_c_rate,
        period_s=period_s,
    )

    model = _build_core_model(config)
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    initial_soc = _effective_initial_soc(config, config.timeseries.charge_compare.soc_start)
    parameter_values = _set_initial_soc_if_supported(base_values, initial_soc)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=parameter_values,
        experiment=experiment,
        solver=solver,
    )
    solution, runtime, warnings, error = _solve_with_warning_capture(
        sim,
        initial_soc=initial_soc,
        use_initial_soc=_uses_initial_soc_argument(config),
    )
    if solution is None or isinstance(solution, pybamm.EmptySolution):
        _write_empty_timeseries(csv_path)
        case = ChargeCompareCaseResult(
            case_id=case_id,
            rate_c=rate_c,
            period_s=period_s,
            converged=False,
            runtime_s=runtime,
            final_soc=None,
            final_voltage_v=None,
            charge_time_s=None,
            cc_end_time_s=None,
            cv_time_s=None,
            csv_path=str(csv_path),
            termination=replace(_default_termination_result(), reason="Charge compare simulation failed."),
            error=error or "Charge compare simulation produced an empty solution.",
        )
        return case, warnings

    frame = _solution_to_frame(solution, config, initial_soc=initial_soc)
    frame, termination, termination_error = _apply_termination_with_context(
        frame, config, context_mode="charge_compare"
    )
    frame.to_csv(csv_path, index=False)
    final_soc = float(frame["soc"].iloc[-1])
    charge_time_s = float(frame["time_s"].iloc[-1])
    cc_end_time_s = _cc_transition_time(frame, config.timeseries.charge_compare.voltage_high_v)
    cv_time_s = None if cc_end_time_s is None else max(0.0, charge_time_s - cc_end_time_s)
    case_error = error or termination_error
    if case_error is None and cv_time_s is not None and cv_time_s <= 0:
        case_error = "CV segment duration is zero."
    if case_error is None and final_soc < 0.99:
        case_error = f"Final SOC {final_soc:.4f} did not reach target 1.0."

    case = ChargeCompareCaseResult(
        case_id=case_id,
        rate_c=rate_c,
        period_s=period_s,
        converged=case_error is None,
        runtime_s=runtime,
        final_soc=final_soc,
        final_voltage_v=float(frame["voltage_v"].iloc[-1]),
        charge_time_s=charge_time_s,
        cc_end_time_s=cc_end_time_s,
        cv_time_s=cv_time_s,
        csv_path=str(csv_path),
        termination=termination,
        error=case_error,
    )
    return case, warnings


def _write_charge_compare_summary_csv(output_dir: Path, cases: list[ChargeCompareCaseResult]) -> Path:
    csv_path = output_dir / "charge_compare_summary.csv"
    rows = [asdict(case) for case in cases]
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def _period_for_rate(period_by_rate_s: dict[float, float], rate_c: float) -> float:
    for mapped_rate, period in period_by_rate_s.items():
        if np.isclose(rate_c, mapped_rate, atol=1e-10):
            return period
    raise ValueError(f"No sampling period configured for rate {rate_c}C.")


def _write_overlay(output_dir: Path, case_summaries: list[RunSummary], filename: str, title: str) -> tuple[Path, str | None]:
    plot_path = output_dir / filename
    valid = [case for case in case_summaries if case.converged and case.csv_path]
    if not valid:
        plot_path.write_bytes(_ONE_PIXEL_PNG)
        return plot_path, "No converged cases available; wrote placeholder PNG."

    mpl_dir = output_dir / ".mplconfig"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir.resolve()))
    try:
        plt = _load_matplotlib_pyplot()
    except Exception:
        plot_path.write_bytes(_ONE_PIXEL_PNG)
        return plot_path, "matplotlib unavailable; wrote placeholder PNG."

    fig, axis = plt.subplots(figsize=(8, 4.5), dpi=150)
    for case in valid:
        frame = pd.read_csv(case.csv_path)
        axis.plot(frame["time_s"] / 3600, frame["voltage_v"], label=case.case_id)
    axis.set_xlabel("Time [h]")
    axis.set_ylabel("Terminal Voltage [V]")
    axis.set_title(title)
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(plot_path)
    plt.close(fig)
    return plot_path, None


def _resolve_benchmark_profiles(config: Config) -> dict[str, Config]:
    disabled_timeseries = replace(
        config.timeseries,
        enabled=False,
        csv_path=None,
        charge_compare=replace(config.timeseries.charge_compare, enabled=False),
    )
    common = replace(
        config,
        discharge_rates_c=list(config.benchmark.rates_c),
        charge_cc_rate=config.benchmark.charge_cc_rate,
        cv_cutoff_c_rate=config.benchmark.cv_cutoff_c_rate,
        rest_min=config.benchmark.rest_min,
        period_s=config.benchmark.period_s,
        sanity_gate=replace(config.sanity_gate, enabled=False),
        hppc=replace(config.hppc, enabled=False),
        timeseries=disabled_timeseries,
        identification_inputs=replace(config.identification_inputs, enabled=False),
    )
    return {
        "dfn_nmc": replace(
            common,
            model_type="dfn",
            chemistry="nmc",
            nominal_capacity_ah=150.0,
            parameter_set="Chen2020",
            voltage_low_v=2.8,
            voltage_high_v=4.2,
        ),
        "dfn_lfp": replace(
            common,
            model_type="dfn",
            chemistry="lfp",
            nominal_capacity_ah=130.0,
            parameter_set="Prada2013",
            voltage_low_v=2.5,
            voltage_high_v=3.6,
        ),
        "ecm_nmc": replace(
            common,
            model_type="ecm",
            chemistry="nmc",
            nominal_capacity_ah=150.0,
            parameter_set="ECM_Example",
            voltage_low_v=3.2,
            voltage_high_v=4.2,
        ),
        "ecm_lfp": replace(
            common,
            model_type="ecm",
            chemistry="lfp",
            nominal_capacity_ah=130.0,
            parameter_set="ECM_Example",
            voltage_low_v=3.2,
            voltage_high_v=4.2,
        ),
    }


def _benchmark_discharge_mean_voltage(csv_path: str | None) -> float | None:
    if not csv_path:
        return None
    path = Path(csv_path)
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if frame.empty or "current_a" not in frame or "voltage_v" not in frame:
        return None

    pos = frame[frame["current_a"] > 1e-6]
    neg = frame[frame["current_a"] < -1e-6]

    def _soc_drop(candidate: pd.DataFrame) -> float:
        if len(candidate) < 2 or "soc" not in candidate:
            return float("-inf")
        return float(candidate["soc"].iloc[0] - candidate["soc"].iloc[-1])

    if len(pos) >= 2 and len(neg) >= 2:
        segment = pos if _soc_drop(pos) >= _soc_drop(neg) else neg
    elif len(pos) >= 2:
        segment = pos
    elif len(neg) >= 2:
        segment = neg
    else:
        return None
    return float(segment["voltage_v"].mean())


def _write_benchmark_report(
    report_path: Path,
    *,
    benchmark_payload: dict[str, Any],
    quality_gate_payload: dict[str, Any],
    matrix_rows: list[dict[str, Any]],
) -> None:
    proxy_profiles = sorted(
        {
            str(row.get("profile_id"))
            for row in matrix_rows
            if _infer_parameter_quality_level(str(row.get("parameter_set", ""))) == "proxy"
        }
    )
    suggestions: list[str] = []
    for failure in benchmark_payload["failures"]:
        category = str(failure.get("category", ""))
        if category == "convergence_rate":
            suggestions.append("Reduce protocol stiffness or relax solver tolerances for failing profile-rate cases.")
        elif category == "repeatability_final_soc":
            suggestions.append("Inspect initial-condition determinism and numerical tolerances for SOC repeatability.")
        elif category == "repeatability_min_v":
            suggestions.append("Inspect voltage interpolation and event handling around cutoff regions.")
        elif category == "polarization_trend":
            suggestions.append("Check sign convention and discharge-segment extraction for trend calculation.")
        elif category == "identification_inputs":
            suggestions.append("Fix identification template data quality or disable strict validation for this benchmark run.")
    suggestions = _dedupe_messages(suggestions)

    lines = [
        "# Benchmark Compare Report",
        "",
        "## Gate Conclusion",
        f"- Gate passed: `{benchmark_payload['passed']}`",
        f"- Passed: `{benchmark_payload['passed']}`",
        f"- Total cases: `{benchmark_payload['total_cases']}`",
        f"- Converged cases: `{benchmark_payload['converged_cases']}`",
        f"- Convergence rate: `{benchmark_payload['convergence_rate']:.4f}`",
        "",
        "## Quality Gate",
        f"- Enabled: `{quality_gate_payload['enabled']}`",
        f"- Enforce: `{quality_gate_payload['enforce']}`",
        f"- Passed: `{quality_gate_payload['passed']}`",
        "",
        "## Parameter Quality",
        f"- Proxy profiles: `{proxy_profiles}`",
        "",
        "## Trend Checks",
    ]
    for check in benchmark_payload["trend_checks"]:
        lines.append(
            f"- `{check['profile_id']} / repeat_{check['repeat']}`: "
            f"`{check['high_rate']}C` ({check['high_rate_mean_v']}) < "
            f"`{check['low_rate']}C` ({check['low_rate_mean_v']}) -> `{check['passed']}`"
        )
    if benchmark_payload["failures"]:
        lines.append("")
        lines.append("## Failures")
        for failure in benchmark_payload["failures"]:
            lines.append(
                "- "
                f"[{failure['category']}] profile={failure['profile_id']} "
                f"rate={failure['rate_c']} repeat={failure['repeat']} "
                f"reason={failure['reason']} observed={failure['observed']} threshold={failure['threshold']}"
            )
    if suggestions:
        lines.append("")
        lines.append("## Recommended Actions")
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
    lines.extend(["", "## Matrix Rows", f"- Rows: `{len(matrix_rows)}`"])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    ident_validation = validate_identification_inputs(config)
    profile_map = _resolve_benchmark_profiles(config)
    requested_profiles = [profile for profile in config.benchmark.profiles if profile]
    invalid_profiles = [profile for profile in requested_profiles if profile not in profile_map]
    if invalid_profiles:
        raise ValueError(
            f"benchmark.profiles contains unsupported entries: {invalid_profiles}. "
            f"Supported: {sorted(profile_map)}"
        )
    selected_profiles = requested_profiles or list(profile_map.keys())

    matrix_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    thermal_param_warning = _thermal_param_scope_warning(config, mode="benchmark")
    if thermal_param_warning:
        warnings.append(thermal_param_warning)
    if not config.benchmark.enabled:
        matrix_path = output_dir / "benchmark_matrix.csv"
        pd.DataFrame(matrix_rows).to_csv(matrix_path, index=False)
        benchmark_summary_path = output_dir / "benchmark_summary.json"
        report_path = output_dir / "benchmark_compare_report.md"
        benchmark_payload = {
            "passed": True,
            "total_cases": 0,
            "converged_cases": 0,
            "convergence_rate": 1.0,
            "repeatability": {"max_delta_final_soc": 0.0, "max_delta_min_v": 0.0, "details": []},
            "trend_checks": [],
            "failures": [],
            "artifacts": {
                "benchmark_matrix_csv": str(matrix_path),
                "benchmark_summary_json": str(benchmark_summary_path),
                "benchmark_compare_report_md": str(report_path),
            },
        }
        quality_gate_payload = {
            "enabled": config.quality_gate.enabled,
            "enforce": config.quality_gate.enforce,
            "passed": True,
            "thresholds": asdict(config.quality_gate),
            "metrics": {
                "convergence_rate": 1.0,
                "max_delta_final_soc": 0.0,
                "max_delta_min_v": 0.0,
                "repeat_pairs": 0,
                "failed_trend_checks": 0,
                "failure_count": 0,
            },
        }
        benchmark_summary_path.write_text(json.dumps(benchmark_payload, indent=2), encoding="utf-8")
        _write_benchmark_report(
            report_path,
            benchmark_payload=benchmark_payload,
            quality_gate_payload=quality_gate_payload,
            matrix_rows=matrix_rows,
        )
        config_dict = _config_to_summary_dict(config)
        disabled_warnings = ["Benchmark disabled by configuration."]
        if thermal_param_warning:
            disabled_warnings.append(thermal_param_warning)
        summary = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "benchmark",
            "all_converged": True,
            "config": config_dict,
            "termination_policy": asdict(config.termination),
            "termination_hits": 0,
            "artifacts": benchmark_payload["artifacts"],
            "quality_gate": quality_gate_payload,
            "benchmark": benchmark_payload,
            "cases": [],
            "warnings": _dedupe_messages(disabled_warnings),
        }
        _merge_identification_validation(summary, ident_validation, context="benchmark")
        _write_summary_json(output_dir, summary)
        return summary

    for profile_id in selected_profiles:
        profile_cfg = profile_map[profile_id]
        for repeat_index in range(1, max(1, config.benchmark.repeats) + 1):
            repeat_output = output_dir / "benchmark_runs" / profile_id / f"repeat_{repeat_index}"
            run_cfg = replace(profile_cfg, output_dir=repeat_output)
            run_summary = run_baseline_pipeline(run_cfg)
            warnings.extend(run_summary.get("warnings", []))
            for case in run_summary.get("cases", []):
                case_rate = _rate_from_case_id(str(case.get("case_id", "")))
                matrix_rows.append(
                    {
                        "profile_id": profile_id,
                        "model_type": profile_cfg.model_type,
                        "chemistry": profile_cfg.chemistry,
                        "nominal_capacity_ah": profile_cfg.nominal_capacity_ah,
                        "parameter_set": profile_cfg.parameter_set,
                        "repeat": repeat_index,
                        "rate_c": case_rate,
                        "case_id": case["case_id"],
                        "converged": bool(case["converged"]),
                        "runtime_s": case["runtime_s"],
                        "min_v": case["min_v"],
                        "max_v": case["max_v"],
                        "final_soc": case["final_soc"],
                        "csv_path": case["csv_path"],
                        "error": case.get("error"),
                        "termination_hit": case.get("termination", {}).get("hit", False),
                        "mean_discharge_voltage_v": _benchmark_discharge_mean_voltage(case.get("csv_path")),
                    }
                )

    matrix_path = output_dir / "benchmark_matrix.csv"
    pd.DataFrame(matrix_rows).to_csv(matrix_path, index=False)

    total_cases = len(matrix_rows)
    converged_cases = sum(1 for row in matrix_rows if row["converged"])
    convergence_rate = (converged_cases / total_cases) if total_cases else 0.0

    repeat_details: list[dict[str, Any]] = []
    max_delta_final_soc = 0.0
    max_delta_min_v = 0.0
    rates = sorted(set(float(rate) for rate in config.benchmark.rates_c))
    for profile_id in selected_profiles:
        for rate_c in rates:
            candidates = [
                row
                for row in matrix_rows
                if row["profile_id"] == profile_id
                and row["rate_c"] is not None
                and np.isclose(float(row["rate_c"]), float(rate_c), atol=1e-10)
            ]
            candidates = sorted(candidates, key=lambda row: int(row["repeat"]))
            if len(candidates) < 2:
                continue
            left = candidates[0]
            right = candidates[1]
            if left["converged"] and right["converged"] and left["final_soc"] is not None and right["final_soc"] is not None:
                delta_soc = abs(float(left["final_soc"]) - float(right["final_soc"]))
            else:
                delta_soc = float("inf")
            if left["converged"] and right["converged"] and left["min_v"] is not None and right["min_v"] is not None:
                delta_min_v = abs(float(left["min_v"]) - float(right["min_v"]))
            else:
                delta_min_v = float("inf")
            if np.isfinite(delta_soc):
                max_delta_final_soc = max(max_delta_final_soc, delta_soc)
            if np.isfinite(delta_min_v):
                max_delta_min_v = max(max_delta_min_v, delta_min_v)
            repeat_details.append(
                {
                    "profile_id": profile_id,
                    "rate_c": rate_c,
                    "repeat_a": int(left["repeat"]),
                    "repeat_b": int(right["repeat"]),
                    "delta_final_soc": None if not np.isfinite(delta_soc) else float(delta_soc),
                    "delta_min_v": None if not np.isfinite(delta_min_v) else float(delta_min_v),
                    "passed": bool(np.isfinite(delta_soc) and np.isfinite(delta_min_v)),
                }
            )

    low_rate = float(rates[0]) if rates else 0.2
    high_rate = float(rates[-1]) if rates else 1.0
    trend_checks: list[dict[str, Any]] = []
    for profile_id in selected_profiles:
        for repeat_index in range(1, max(1, config.benchmark.repeats) + 1):
            low_rows = [
                row
                for row in matrix_rows
                if row["profile_id"] == profile_id
                and int(row["repeat"]) == repeat_index
                and row["rate_c"] is not None
                and np.isclose(float(row["rate_c"]), low_rate, atol=1e-10)
            ]
            high_rows = [
                row
                for row in matrix_rows
                if row["profile_id"] == profile_id
                and int(row["repeat"]) == repeat_index
                and row["rate_c"] is not None
                and np.isclose(float(row["rate_c"]), high_rate, atol=1e-10)
            ]
            low_v = low_rows[0]["mean_discharge_voltage_v"] if low_rows else None
            high_v = high_rows[0]["mean_discharge_voltage_v"] if high_rows else None
            passed = (
                low_v is not None
                and high_v is not None
                and np.isfinite(float(low_v))
                and np.isfinite(float(high_v))
                and float(high_v) < float(low_v)
            )
            trend_checks.append(
                {
                    "profile_id": profile_id,
                    "repeat": repeat_index,
                    "low_rate": low_rate,
                    "high_rate": high_rate,
                    "low_rate_mean_v": None if low_v is None else float(low_v),
                    "high_rate_mean_v": None if high_v is None else float(high_v),
                    "passed": bool(passed),
                }
            )

    quality_gate = config.quality_gate
    failures: list[dict[str, Any]] = []

    def _failure(
        *,
        category: str,
        reason: str,
        profile_id: str | None = None,
        rate_c: float | None = None,
        repeat: int | None = None,
        observed: float | str | None = None,
        threshold: float | str | None = None,
    ) -> dict[str, Any]:
        return {
            "category": category,
            "reason": reason,
            "profile_id": profile_id,
            "rate_c": None if rate_c is None else float(rate_c),
            "repeat": None if repeat is None else int(repeat),
            "observed": observed,
            "threshold": threshold,
        }

    if quality_gate.enabled:
        if convergence_rate < quality_gate.min_convergence_rate:
            failed_rows = [row for row in matrix_rows if not row["converged"]]
            if failed_rows:
                for row in failed_rows:
                    failures.append(
                        _failure(
                            category="convergence_rate",
                            reason=row.get("error") or "Case did not converge.",
                            profile_id=str(row.get("profile_id")),
                            rate_c=row.get("rate_c"),
                            repeat=int(row.get("repeat")) if row.get("repeat") is not None else None,
                            observed=convergence_rate,
                            threshold=quality_gate.min_convergence_rate,
                        )
                    )
            else:
                failures.append(
                    _failure(
                        category="convergence_rate",
                        reason="Convergence rate below threshold.",
                        observed=convergence_rate,
                        threshold=quality_gate.min_convergence_rate,
                    )
                )
        if repeat_details:
            if max_delta_final_soc > quality_gate.max_repeat_delta_final_soc:
                for detail in repeat_details:
                    delta = detail.get("delta_final_soc")
                    if delta is None or float(delta) <= quality_gate.max_repeat_delta_final_soc:
                        continue
                    failures.append(
                        _failure(
                            category="repeatability_final_soc",
                            reason="Repeatability(delta_final_soc) exceeded threshold.",
                            profile_id=str(detail.get("profile_id")),
                            rate_c=detail.get("rate_c"),
                            repeat=int(detail.get("repeat_b")) if detail.get("repeat_b") is not None else None,
                            observed=float(delta),
                            threshold=quality_gate.max_repeat_delta_final_soc,
                        )
                    )
            if max_delta_min_v > quality_gate.max_repeat_delta_min_v:
                for detail in repeat_details:
                    delta = detail.get("delta_min_v")
                    if delta is None or float(delta) <= quality_gate.max_repeat_delta_min_v:
                        continue
                    failures.append(
                        _failure(
                            category="repeatability_min_v",
                            reason="Repeatability(delta_min_v) exceeded threshold.",
                            profile_id=str(detail.get("profile_id")),
                            rate_c=detail.get("rate_c"),
                            repeat=int(detail.get("repeat_b")) if detail.get("repeat_b") is not None else None,
                            observed=float(delta),
                            threshold=quality_gate.max_repeat_delta_min_v,
                        )
                    )
        else:
            failures.append(
                _failure(
                    category="repeatability_data",
                    reason="Repeatability checks could not be computed (insufficient repeat data).",
                    observed=0,
                    threshold=2,
                )
            )
        if quality_gate.require_polarization_trend:
            failed_trends = [check for check in trend_checks if not check["passed"]]
            for check in failed_trends:
                failures.append(
                    _failure(
                        category="polarization_trend",
                        reason="Polarization trend check failed.",
                        profile_id=str(check.get("profile_id")),
                        rate_c=float(check.get("high_rate")) if check.get("high_rate") is not None else None,
                        repeat=int(check.get("repeat")) if check.get("repeat") is not None else None,
                        observed=check.get("high_rate_mean_v"),
                        threshold=check.get("low_rate_mean_v"),
                    )
                )

    if ident_validation.get("enabled") and ident_validation.get("strict") and not ident_validation.get("passed"):
        failures.append(
            _failure(
                category="identification_inputs",
                reason="Strict identification input validation failed.",
                observed=False,
                threshold=True,
            )
        )

    gate_passed = len(failures) == 0
    quality_gate_payload = {
        "enabled": quality_gate.enabled,
        "enforce": quality_gate.enforce,
        "passed": gate_passed,
        "thresholds": asdict(quality_gate),
        "metrics": {
            "convergence_rate": convergence_rate,
            "max_delta_final_soc": max_delta_final_soc,
            "max_delta_min_v": max_delta_min_v,
            "repeat_pairs": len(repeat_details),
            "failed_trend_checks": sum(1 for check in trend_checks if not check["passed"]),
            "failure_count": len(failures),
        },
    }

    benchmark_summary_path = output_dir / "benchmark_summary.json"
    report_path = output_dir / "benchmark_compare_report.md"
    benchmark_payload: dict[str, Any] = {
        "passed": gate_passed,
        "total_cases": total_cases,
        "converged_cases": converged_cases,
        "convergence_rate": convergence_rate,
        "repeatability": {
            "max_delta_final_soc": max_delta_final_soc,
            "max_delta_min_v": max_delta_min_v,
            "details": repeat_details,
        },
        "trend_checks": trend_checks,
        "failures": failures,
        "artifacts": {
            "benchmark_matrix_csv": str(matrix_path),
            "benchmark_summary_json": str(benchmark_summary_path),
            "benchmark_compare_report_md": str(report_path),
        },
    }
    benchmark_summary_path.write_text(json.dumps(benchmark_payload, indent=2), encoding="utf-8")
    _write_benchmark_report(
        report_path,
        benchmark_payload=benchmark_payload,
        quality_gate_payload=quality_gate_payload,
        matrix_rows=matrix_rows,
    )

    config_dict = _config_to_summary_dict(config)

    all_converged = gate_passed if quality_gate.enforce else True
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "benchmark",
        "all_converged": all_converged,
        "config": config_dict,
        "termination_policy": asdict(config.termination),
        "termination_hits": sum(1 for row in matrix_rows if row.get("termination_hit")),
        "artifacts": {
            "benchmark_matrix_csv": str(matrix_path),
            "benchmark_summary_json": str(benchmark_summary_path),
            "benchmark_compare_report_md": str(report_path),
        },
        "quality_gate": quality_gate_payload,
        "benchmark": benchmark_payload,
        "cases": matrix_rows,
    }
    proxy_profiles = sorted(
        {
            profile_id
            for profile_id in selected_profiles
            if _infer_parameter_quality_level(profile_map[profile_id].parameter_set) == "proxy"
        }
    )
    if proxy_profiles:
        warnings.append(
            "Proxy parameter packs used in benchmark profiles: "
            + ", ".join(proxy_profiles)
            + ". Interpret absolute values cautiously."
        )
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    _merge_identification_validation(summary, ident_validation, context="benchmark")
    _write_summary_json(output_dir, summary)
    return summary


def run_baseline_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ident_validation = validate_identification_inputs(config)

    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)
    gate = _run_sanity_gate(config, base_values, output_dir)

    config_dict = _config_to_summary_dict(config)
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "baseline",
        "all_converged": False,
        "config": config_dict,
        "termination_policy": asdict(config.termination),
        "termination_hits": 0,
        "artifacts": {
            "parameter_audit": str(audit_path),
            "sanity_gate_csv": gate.artifact_csv,
            "sanity_gate_json": gate.artifact_json,
            "voltage_overlay_png": None,
        },
        "sanity_gate": asdict(gate),
        "cases": [],
    }
    boundary_warning = _thermal_boundary_fallback_warning(config, mode="baseline")
    thermal_param_warning = _thermal_param_scope_warning(config, mode="baseline")
    if not gate.passed:
        warnings = list(gate.warning_messages)
        if boundary_warning:
            warnings.append(boundary_warning)
        if thermal_param_warning:
            warnings.append(thermal_param_warning)
        warnings.append("Sanity gate failed; batch simulations were blocked.")
        if gate.error:
            warnings.append(f"Sanity gate error: {gate.error}")
        proxy_warning = _proxy_parameter_warning(config)
        if proxy_warning:
            warnings.append(proxy_warning)
        summary["warnings"] = _dedupe_messages(warnings)
        _merge_identification_validation(summary, ident_validation, context="baseline")
        _write_summary_json(output_dir, summary)
        return summary

    cases = [_run_baseline_case(config, base_values, rate_c, output_dir) for rate_c in config.discharge_rates_c]
    overlay_path, overlay_warning = _write_overlay(output_dir, cases, "voltage_overlay.png", "DFN Baseline Voltage Overlay")

    summary["all_converged"] = all(case.converged for case in cases)
    summary["termination_hits"] = sum(1 for case in cases if case.termination.hit)
    summary["artifacts"]["voltage_overlay_png"] = str(overlay_path)
    summary["cases"] = [asdict(case) for case in cases]

    warnings: list[str] = []
    if boundary_warning:
        warnings.append(boundary_warning)
    if thermal_param_warning:
        warnings.append(thermal_param_warning)
    if overlay_warning:
        warnings.append(overlay_warning)
    for case in cases:
        if not case.converged or not case.csv_path:
            continue
        frame = pd.read_csv(case.csv_path)
        nonzero = frame.loc[np.abs(frame["current_a"]) > 1e-6, "current_a"]
        if nonzero.empty:
            warnings.append(f"{case.case_id}: no active current segment detected.")
            continue
        if not (float(nonzero.max()) > 0 and float(nonzero.min()) < 0):
            warnings.append(
                f"{case.case_id}: charge/discharge sign reversal not detected; "
                "charge or CV steps may have been skipped."
            )
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    proxy_warning = _proxy_parameter_warning(config)
    if proxy_warning:
        warning_list = summary.get("warnings", [])
        warning_list.append(proxy_warning)
        summary["warnings"] = _dedupe_messages(warning_list)

    _merge_identification_validation(summary, ident_validation, context="baseline")
    _write_summary_json(output_dir, summary)
    return summary


def _soc_grid(start: float, end: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("hppc.soc_step must be positive.")
    if not (0 <= end <= start <= 1):
        raise ValueError("hppc SOC range must satisfy 0 <= soc_end <= soc_start <= 1.")
    values: list[float] = []
    current = start
    epsilon = step * 0.1
    while current >= end - epsilon:
        values.append(round(current, 10))
        current -= step
    return values


def _first_after(indices: np.ndarray, index: int) -> int | None:
    subset = indices[indices > index]
    if subset.size == 0:
        return None
    return int(subset[0])


def _compute_hppc_metrics(frame: pd.DataFrame) -> tuple[dict[str, float] | None, str | None]:
    current = frame["current_a"].to_numpy()
    voltage = frame["voltage_v"].to_numpy()
    tol = 1e-6

    dis_idx = np.where(current > tol)[0]
    if dis_idx.size == 0:
        return None, "No discharge pulse segment detected."
    dis_end = int(dis_idx[-1])

    rest_idx = np.where(np.abs(current) <= tol)[0]
    dis_rest_start = _first_after(rest_idx, dis_end)
    if dis_rest_start is None:
        return None, "No rest segment found after discharge pulse."

    chg_idx = np.where(current < -tol)[0]
    chg_idx = chg_idx[chg_idx > dis_rest_start]
    if chg_idx.size == 0:
        return None, "No charge pulse segment detected."
    chg_end = int(chg_idx[-1])

    chg_rest_start = _first_after(rest_idx, chg_end)
    if chg_rest_start is None:
        return None, "No rest segment found after charge pulse."

    v_dis_end = float(voltage[dis_end])
    v_dis_rest_start = float(voltage[dis_rest_start])
    i_dis_end = float(current[dis_end])
    i_dis_rest_start = float(current[dis_rest_start])
    delta_i_dis = i_dis_rest_start - i_dis_end
    if abs(delta_i_dis) <= tol:
        return None, "Discharge pulse current delta too small for R_dis calculation."
    r_dis = abs((v_dis_rest_start - v_dis_end) / delta_i_dis)

    v_chg_end = float(voltage[chg_end])
    v_chg_rest_start = float(voltage[chg_rest_start])
    i_chg_end = float(current[chg_end])
    i_chg_rest_start = float(current[chg_rest_start])
    delta_i_chg = i_chg_rest_start - i_chg_end
    if abs(delta_i_chg) <= tol:
        return None, "Charge pulse current delta too small for R_chg calculation."
    r_chg = abs((v_chg_rest_start - v_chg_end) / delta_i_chg)

    if not np.isfinite(r_dis) or r_dis <= 0:
        return None, "Computed R_dis_10s is invalid."
    if not np.isfinite(r_chg) or r_chg <= 0:
        return None, "Computed R_chg_10s is invalid."

    return {
        "v_dis_end": v_dis_end,
        "v_dis_rest_start": v_dis_rest_start,
        "r_dis_10s_ohm": float(r_dis),
        "v_chg_end": v_chg_end,
        "v_chg_rest_start": v_chg_rest_start,
        "r_chg_10s_ohm": float(r_chg),
    }, None


def _run_hppc_point(
    config: Config,
    base_values: pybamm.ParameterValues,
    soc: float,
    output_dir: Path,
    profile: pd.DataFrame,
) -> HppcPointResult:
    csv_path = output_dir / f"hppc_point_soc_{_soc_label(soc)}.csv"
    frame, runtime, warnings, error = simulate_from_timeseries(
        config=config,
        base_values=base_values,
        profile=profile,
        initial_soc=soc,
    )
    if frame is None:
        _write_empty_timeseries(csv_path)
        return HppcPointResult(
            soc_target=soc,
            passed=False,
            runtime_s=runtime,
            v_dis_end=None,
            v_dis_rest_start=None,
            r_dis_10s_ohm=None,
            v_chg_end=None,
            v_chg_rest_start=None,
            r_chg_10s_ohm=None,
            has_positive_current=False,
            has_negative_current=False,
            csv_path=str(csv_path),
            warning_messages=warnings,
            termination=replace(_default_termination_result(), reason="Timeseries simulation failed."),
            error=error,
        )

    frame, termination, termination_error = _apply_termination(frame, config.termination)
    frame.to_csv(csv_path, index=False)
    nonzero = frame.loc[np.abs(frame["current_a"]) > 1e-6, "current_a"]
    has_pos = bool((nonzero > 0).any())
    has_neg = bool((nonzero < 0).any())
    metrics, metric_error = _compute_hppc_metrics(frame)
    infeasible = [message for message in warnings if _warning_is_infeasible(message)]

    point_error = error or termination_error
    if point_error is None and not has_pos:
        point_error = "No positive current segment detected."
    if point_error is None and not has_neg:
        point_error = "No negative current segment detected."
    if point_error is None and metric_error:
        point_error = metric_error
    if point_error is None and infeasible:
        point_error = "Infeasible-step warning detected in HPPC point."
    passed = point_error is None

    metrics = metrics or {
        "v_dis_end": None,
        "v_dis_rest_start": None,
        "r_dis_10s_ohm": None,
        "v_chg_end": None,
        "v_chg_rest_start": None,
        "r_chg_10s_ohm": None,
    }
    return HppcPointResult(
        soc_target=soc,
        passed=passed,
        runtime_s=runtime,
        v_dis_end=metrics["v_dis_end"],
        v_dis_rest_start=metrics["v_dis_rest_start"],
        r_dis_10s_ohm=metrics["r_dis_10s_ohm"],
        v_chg_end=metrics["v_chg_end"],
        v_chg_rest_start=metrics["v_chg_rest_start"],
        r_chg_10s_ohm=metrics["r_chg_10s_ohm"],
        has_positive_current=has_pos,
        has_negative_current=has_neg,
        csv_path=str(csv_path),
        warning_messages=warnings,
        termination=termination,
        error=point_error,
    )


def _write_hppc_summary_csv(output_dir: Path, points: list[HppcPointResult]) -> Path:
    csv_path = output_dir / "hppc_summary.csv"
    rows = [
        {
            "soc_target": point.soc_target,
            "v_dis_end": point.v_dis_end,
            "v_dis_rest_start": point.v_dis_rest_start,
            "r_dis_10s_ohm": point.r_dis_10s_ohm,
            "v_chg_end": point.v_chg_end,
            "v_chg_rest_start": point.v_chg_rest_start,
            "r_chg_10s_ohm": point.r_chg_10s_ohm,
            "runtime_s": point.runtime_s,
            "passed": point.passed,
            "csv_path": point.csv_path,
            "termination_hit": point.termination.hit,
            "termination_time_s": point.termination.time_s,
            "termination_reason": point.termination.reason,
            "error": point.error,
        }
        for point in points
    ]
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def _write_hppc_overlay(output_dir: Path, points: list[HppcPointResult]) -> tuple[Path, str | None]:
    plot_path = output_dir / "hppc_voltage_overlay.png"
    csv_points = [point for point in points if point.csv_path and Path(point.csv_path).exists()]
    if not csv_points:
        plot_path.write_bytes(_ONE_PIXEL_PNG)
        return plot_path, "No HPPC points available for overlay; wrote placeholder PNG."

    mpl_dir = output_dir / ".mplconfig"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir.resolve()))
    try:
        plt = _load_matplotlib_pyplot()
    except Exception:
        plot_path.write_bytes(_ONE_PIXEL_PNG)
        return plot_path, "matplotlib unavailable; wrote placeholder PNG."

    fig, axis = plt.subplots(figsize=(8, 4.5), dpi=150)
    for point in csv_points:
        frame = pd.read_csv(point.csv_path)
        axis.plot(frame["time_s"], frame["voltage_v"], label=f"SOC {point.soc_target * 100:.0f}%")
    axis.set_xlabel("Time [s]")
    axis.set_ylabel("Terminal Voltage [V]")
    axis.set_title("HPPC Voltage Overlay")
    axis.grid(True, alpha=0.3)
    axis.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(plot_path)
    plt.close(fig)
    return plot_path, None


def run_hppc_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ident_validation = validate_identification_inputs(config)
    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)

    points: list[HppcPointResult] = []
    stop_reason: str | None = None
    if config.hppc.enabled:
        try:
            hppc_profile = _build_hppc_profile(config)
        except Exception as exc:
            hppc_profile = None
            stop_reason = f"HPPC profile build failed: {exc}"
        targets = _soc_grid(config.hppc.soc_start, config.hppc.soc_end, config.hppc.soc_step)
        if hppc_profile is not None:
            for soc in targets:
                point = _run_hppc_point(config, base_values, soc, output_dir, hppc_profile)
                points.append(point)
                if not point.passed:
                    stop_reason = f"SOC {soc * 100:.0f}%: {point.error or 'HPPC point failed.'}"
                    break
        total_points = len(targets)
    else:
        total_points = 0
        stop_reason = "HPPC disabled by configuration."

    completed_points = len(points)
    passed = config.hppc.enabled and completed_points == total_points and stop_reason is None
    if not config.hppc.enabled:
        passed = True

    summary_csv = _write_hppc_summary_csv(output_dir, points)
    overlay_png, overlay_warning = _write_hppc_overlay(output_dir, points)
    hppc_payload = {
        "enabled": config.hppc.enabled,
        "passed": passed,
        "stop_reason": stop_reason,
        "completed_points": completed_points,
        "total_points": total_points,
        "termination_policy": asdict(config.termination),
        "termination_hits": sum(1 for point in points if point.termination.hit),
        "artifacts": {
            "hppc_summary_csv": str(summary_csv),
            "hppc_summary_json": str(output_dir / "hppc_summary.json"),
            "hppc_voltage_overlay_png": str(overlay_png),
        },
        "points": [asdict(point) for point in points],
    }
    if overlay_warning:
        hppc_payload["overlay_warning"] = overlay_warning
    (output_dir / "hppc_summary.json").write_text(json.dumps(hppc_payload, indent=2), encoding="utf-8")

    config_dict = _config_to_summary_dict(config)
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "hppc",
        "all_converged": passed,
        "config": config_dict,
        "termination_policy": asdict(config.termination),
        "termination_hits": sum(1 for point in points if point.termination.hit),
        "artifacts": {
            "parameter_audit": str(audit_path),
            "hppc_summary_csv": str(summary_csv),
            "hppc_summary_json": str(output_dir / "hppc_summary.json"),
            "hppc_voltage_overlay_png": str(overlay_png),
        },
        "hppc": hppc_payload,
        "cases": [],
    }
    warnings: list[str] = []
    boundary_warning = _thermal_boundary_fallback_warning(config, mode="hppc")
    thermal_param_warning = _thermal_param_scope_warning(config, mode="hppc")
    if boundary_warning:
        warnings.append(boundary_warning)
    if thermal_param_warning:
        warnings.append(thermal_param_warning)
    if overlay_warning:
        warnings.append(overlay_warning)
    for point in points:
        warnings.extend(point.warning_messages)
    if stop_reason and config.hppc.enabled:
        warnings.append(f"HPPC fail-fast triggered: {stop_reason}")
    proxy_warning = _proxy_parameter_warning(config)
    if proxy_warning:
        warnings.append(proxy_warning)
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    _merge_identification_validation(summary, ident_validation, context="hppc")
    _write_summary_json(output_dir, summary)
    return summary


def run_charge_compare_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ident_validation = validate_identification_inputs(config)
    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)

    charge_cfg = config.timeseries.charge_compare
    warnings: list[str] = []
    boundary_warning = _thermal_boundary_fallback_warning(config, mode="timeseries|charge_compare")
    thermal_param_warning = _thermal_param_scope_warning(config, mode="timeseries|charge_compare")
    if boundary_warning:
        warnings.append(boundary_warning)
    if thermal_param_warning:
        warnings.append(thermal_param_warning)
    stop_reason: str | None = None
    cases: list[ChargeCompareCaseResult] = []

    if not config.timeseries.enabled:
        stop_reason = "Timeseries mode disabled by configuration."
    elif not charge_cfg.enabled:
        stop_reason = "Charge compare disabled by configuration."
    else:
        if not (0.0 <= charge_cfg.soc_start <= 1.0):
            raise ValueError("timeseries.charge_compare.soc_start must be within [0, 1].")
        if not charge_cfg.rates_c:
            raise ValueError("timeseries.charge_compare.rates_c must not be empty.")

        for rate_c in charge_cfg.rates_c:
            try:
                period_s = _period_for_rate(charge_cfg.period_by_rate_s, rate_c)
                case, case_warnings = _run_charge_compare_case(config, base_values, rate_c, period_s, output_dir)
                warnings.extend(case_warnings)
            except Exception as exc:
                case = ChargeCompareCaseResult(
                    case_id=f"charge_case_{_format_rate_label(rate_c)}c",
                    rate_c=rate_c,
                    period_s=0.0,
                    converged=False,
                    runtime_s=0.0,
                    final_soc=None,
                    final_voltage_v=None,
                    charge_time_s=None,
                    cc_end_time_s=None,
                    cv_time_s=None,
                    csv_path=None,
                    termination=replace(_default_termination_result(), reason="Charge compare case setup failed."),
                    error=str(exc),
                )
            cases.append(case)

    summary_csv = _write_charge_compare_summary_csv(output_dir, cases)
    overlay_png, overlay_warning = _write_overlay(
        output_dir=output_dir,
        case_summaries=[
            RunSummary(
                case_id=case.case_id,
                converged=case.converged,
                min_v=None,
                max_v=None,
                final_soc=case.final_soc,
                runtime_s=case.runtime_s,
                csv_path=case.csv_path,
                error=case.error,
            )
            for case in cases
        ],
        filename="charge_compare_overlay.png",
        title="CC-CV Charge Compare Overlay",
    )
    if overlay_warning:
        warnings.append(overlay_warning)

    completed_cases = sum(1 for case in cases if case.converged)
    total_cases = len(cases)
    passed = bool(charge_cfg.enabled and completed_cases == total_cases and stop_reason is None)
    if not charge_cfg.enabled:
        passed = True

    charge_payload: dict[str, Any] = {
        "enabled": charge_cfg.enabled,
        "passed": passed,
        "completed_cases": completed_cases,
        "total_cases": total_cases,
        "termination_policy": asdict(config.termination),
        "termination_hits": sum(1 for case in cases if case.termination.hit),
        "artifacts": {
            "charge_compare_summary_csv": str(summary_csv),
            "charge_compare_summary_json": str(output_dir / "charge_compare_summary.json"),
            "charge_compare_overlay_png": str(overlay_png),
        },
        "cases": [asdict(case) for case in cases],
    }
    if stop_reason:
        charge_payload["stop_reason"] = stop_reason

    (output_dir / "charge_compare_summary.json").write_text(json.dumps(charge_payload, indent=2), encoding="utf-8")

    config_dict = _config_to_summary_dict(config)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "timeseries",
        "all_converged": passed,
        "config": config_dict,
        "termination_policy": asdict(config.termination),
        "termination_hits": sum(1 for case in cases if case.termination.hit),
        "artifacts": {
            "parameter_audit": str(audit_path),
            "charge_compare_summary_csv": str(summary_csv),
            "charge_compare_summary_json": str(output_dir / "charge_compare_summary.json"),
            "charge_compare_overlay_png": str(overlay_png),
        },
        "charge_compare": charge_payload,
        "cases": [asdict(case) for case in cases],
    }
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    if stop_reason and charge_cfg.enabled:
        stop_warnings = summary.get("warnings", [])
        stop_warnings.append(f"Charge compare incomplete: {stop_reason}")
        summary["warnings"] = _dedupe_messages(stop_warnings)
    proxy_warning = _proxy_parameter_warning(config)
    if proxy_warning:
        warning_list = summary.get("warnings", [])
        warning_list.append(proxy_warning)
        summary["warnings"] = _dedupe_messages(warning_list)
    _merge_identification_validation(summary, ident_validation, context="timeseries-charge-compare")
    _write_summary_json(output_dir, summary)
    return summary


def run_timeseries_pipeline(config: Config) -> dict[str, Any]:
    if config.timeseries.charge_compare.enabled:
        return run_charge_compare_pipeline(config)

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ident_validation = validate_identification_inputs(config)
    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)

    output_csv = output_dir / "timeseries_output.csv"
    output_json = output_dir / "timeseries_summary.json"
    stop_reason: str | None = None
    warnings: list[str] = []
    thermal_param_warning = _thermal_param_scope_warning(config, mode="timeseries")
    if thermal_param_warning:
        warnings.append(thermal_param_warning)
    case: RunSummary | None = None
    source_csv: str | None = None
    frame_result: pd.DataFrame | None = None
    soc_switch_payload: dict[str, Any] | None = None
    generated_profile_csv: Path | None = None

    if not config.timeseries.enabled:
        stop_reason = "Timeseries mode disabled by configuration."
        _write_empty_timeseries(output_csv)
    elif (not config.timeseries.soc_switch_approx.enabled) and config.timeseries.csv_path is None:
        stop_reason = "timeseries.csv_path is required when mode=timeseries."
        _write_empty_timeseries(output_csv)
    else:
        try:
            initial_soc_for_run = config.initial_soc
            if config.timeseries.soc_switch_approx.enabled:
                profile, switch_meta = _build_soc_switch_approx_profile(config)
                initial_soc_for_run = float(switch_meta["soc_start"])
                source_csv = "generated:soc_switch_approx"
                generated_profile_csv = output_dir / "soc_switch_approx_input.csv"
                profile.to_csv(generated_profile_csv, index=False)
                soc_switch_payload = {
                    "enabled": True,
                    "source": "generated_timeseries",
                    **switch_meta,
                    "soc_at_predicted_switch": None,
                    "switch_soc_error": None,
                    "final_soc": None,
                    "final_soc_error": None,
                }
            else:
                source_csv = str(config.timeseries.csv_path)
                profile = _load_timeseries_csv(config.timeseries.csv_path)
            frame, runtime, sim_warnings, sim_error = simulate_from_timeseries(
                config=config,
                base_values=base_values,
                profile=profile,
                initial_soc=initial_soc_for_run,
            )
            warnings.extend(sim_warnings)
            if frame is None:
                stop_reason = sim_error or "Timeseries simulation failed."
                _write_empty_timeseries(output_csv)
                case = RunSummary(
                    case_id="timeseries_case",
                    converged=False,
                    min_v=None,
                    max_v=None,
                    final_soc=None,
                    runtime_s=runtime,
                    csv_path=None,
                    termination=replace(_default_termination_result(), reason="Timeseries simulation failed."),
                    error=stop_reason,
                )
            else:
                frame, termination, termination_error = _apply_termination_with_context(
                    frame, config, context_mode="timeseries"
                )
                frame.to_csv(output_csv, index=False)
                frame_result = frame
                case_error = sim_error or termination_error
                case = RunSummary(
                    case_id="timeseries_case",
                    converged=case_error is None,
                    min_v=float(np.min(frame["voltage_v"])),
                    max_v=float(np.max(frame["voltage_v"])),
                    final_soc=float(frame["soc"].iloc[-1]),
                    runtime_s=runtime,
                    csv_path=str(output_csv),
                    termination=termination,
                    error=case_error,
                )
                if case_error:
                    stop_reason = case_error
        except Exception as exc:
            stop_reason = str(exc)
            _write_empty_timeseries(output_csv)
            case = RunSummary(
                case_id="timeseries_case",
                converged=False,
                min_v=None,
                max_v=None,
                final_soc=None,
                runtime_s=0.0,
                csv_path=None,
                termination=replace(_default_termination_result(), reason="Timeseries pipeline exception."),
                error=stop_reason,
            )

    if soc_switch_payload is not None and frame_result is not None and not frame_result.empty:
        switch_time = float(soc_switch_payload["predicted_switch_time_s"])
        target_discharge_soc = float(soc_switch_payload["discharge_to_soc"])
        target_charge_soc = float(soc_switch_payload["charge_to_soc"])
        time_values = frame_result["time_s"].to_numpy(dtype=float)
        soc_values = frame_result["soc"].to_numpy(dtype=float)
        if switch_time <= float(time_values[-1]) + 1e-9:
            soc_at_switch = float(np.interp(switch_time, time_values, soc_values))
            soc_switch_payload["soc_at_predicted_switch"] = soc_at_switch
            soc_switch_payload["switch_soc_error"] = soc_at_switch - target_discharge_soc
        final_soc_val = float(soc_values[-1])
        soc_switch_payload["final_soc"] = final_soc_val
        soc_switch_payload["final_soc_error"] = final_soc_val - target_charge_soc

    passed = bool(case and case.converged and stop_reason is None)
    payload = {
        "enabled": config.timeseries.enabled,
        "passed": passed,
        "stop_reason": stop_reason,
        "source_csv": source_csv,
        "termination_policy": asdict(config.termination),
        "termination_hits": int(bool(case and case.termination.hit)),
        "artifacts": {
            "timeseries_output_csv": str(output_csv),
            "timeseries_summary_json": str(output_json),
        },
        "case": asdict(case) if case else None,
    }
    if generated_profile_csv is not None:
        payload["artifacts"]["soc_switch_approx_input_csv"] = str(generated_profile_csv)
    if soc_switch_payload is not None:
        payload["soc_switch_approx"] = soc_switch_payload
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    config_dict = _config_to_summary_dict(config)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "timeseries",
        "all_converged": passed,
        "config": config_dict,
        "termination_policy": asdict(config.termination),
        "termination_hits": int(bool(case and case.termination.hit)),
        "artifacts": {
            "parameter_audit": str(audit_path),
            "timeseries_output_csv": str(output_csv),
            "timeseries_summary_json": str(output_json),
        },
        "timeseries": payload,
        "cases": [asdict(case)] if case else [],
    }
    if generated_profile_csv is not None:
        summary["artifacts"]["soc_switch_approx_input_csv"] = str(generated_profile_csv)
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    if stop_reason and config.timeseries.enabled:
        stop_warnings = summary.get("warnings", [])
        stop_warnings.append(f"Timeseries fail-fast triggered: {stop_reason}")
        summary["warnings"] = _dedupe_messages(stop_warnings)
    proxy_warning = _proxy_parameter_warning(config)
    if proxy_warning:
        warning_list = summary.get("warnings", [])
        warning_list.append(proxy_warning)
        summary["warnings"] = _dedupe_messages(warning_list)
    _merge_identification_validation(summary, ident_validation, context="timeseries")
    _write_summary_json(output_dir, summary)
    return summary


def run_pipeline(config: Config, mode: str = "baseline") -> dict[str, Any]:
    if mode == "baseline":
        return run_baseline_pipeline(config)
    if mode == "hppc":
        return run_hppc_pipeline(config)
    if mode == "timeseries":
        return run_timeseries_pipeline(config)
    if mode == "benchmark":
        return run_benchmark_pipeline(config)
    raise ValueError(f"Unsupported mode: {mode}")


def run_from_config(
    config_path: str | Path,
    output_dir_override: str | Path | None = None,
    mode: str = "baseline",
) -> dict[str, Any]:
    config = load_config(config_path)
    if output_dir_override:
        config = replace(config, output_dir=Path(output_dir_override))
    return run_pipeline(config, mode=mode)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run baseline, HPPC, timeseries, or benchmark PyBaMM simulations.")
    parser.add_argument("--config", required=True, help="Path to YAML config file.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    parser.add_argument(
        "--mode",
        choices=["baseline", "hppc", "timeseries", "benchmark"],
        default="baseline",
        help="Simulation mode.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    summary = run_from_config(config_path=args.config, output_dir_override=args.output_dir, mode=args.mode)
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
