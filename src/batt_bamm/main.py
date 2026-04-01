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
class TimeseriesSuiteConfig:
    enabled: bool = False
    csv_path: Path | None = None
    period_s: float | None = None
    charge_compare: ChargeCompareConfig = field(default_factory=ChargeCompareConfig)


@dataclass(frozen=True)
class Config:
    nominal_capacity_ah: float
    initial_soc: float
    ambient_temp_k: float
    voltage_low_v: float
    voltage_high_v: float
    discharge_rates_c: list[float]
    charge_cc_rate: float
    cv_cutoff_c_rate: float
    rest_min: float
    output_dir: Path
    parameter_set: str = "Chen2020"
    thermal: str = "isothermal"
    period_s: int = 30
    solver_rtol: float = 1e-6
    solver_atol: float = 1e-8
    sanity_gate: SanityGateConfig = field(default_factory=SanityGateConfig)
    hppc: HppcConfig = field(default_factory=HppcConfig)
    timeseries: TimeseriesSuiteConfig = field(default_factory=TimeseriesSuiteConfig)


@dataclass(frozen=True)
class RunSummary:
    case_id: str
    converged: bool
    min_v: float | None
    max_v: float | None
    final_soc: float | None
    runtime_s: float
    csv_path: str | None
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


def _warning_is_infeasible(message: str) -> bool:
    lower = message.lower()
    return any(token in lower for token in _WARNING_TOKENS)


def _case_id(rate_c: float) -> str:
    return f"case_{str(rate_c).replace('.', 'p')}c"


def _soc_label(soc: float) -> str:
    return f"{int(round(soc * 100)):03d}"


def load_config(config_path: str | Path) -> Config:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    if not raw:
        raise ValueError("Config file is empty")

    solver = raw.get("solver", {})
    model = raw.get("model", {})
    sanity_raw = raw.get("sanity_gate", {})
    hppc_raw = raw.get("hppc", {})
    timeseries_raw = raw.get("timeseries", {})

    timeseries_csv_raw = timeseries_raw.get("csv_path")
    timeseries_csv_path: Path | None = None
    if isinstance(timeseries_csv_raw, str) and timeseries_csv_raw.strip():
        candidate = Path(timeseries_csv_raw.strip())
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        timeseries_csv_path = candidate

    charge_compare_raw = timeseries_raw.get("charge_compare", {})
    rates_raw = charge_compare_raw.get("rates_c", [0.1, 1.0 / 3.0, 1.0])
    rates_c = [float(rate) for rate in rates_raw]
    period_by_rate_raw = charge_compare_raw.get(
        "period_by_rate_s",
        {0.1: 1.0, 1.0 / 3.0: 0.1, 1.0: 0.1},
    )
    period_by_rate_s = {float(rate_key): float(period_value) for rate_key, period_value in period_by_rate_raw.items()}

    return Config(
        nominal_capacity_ah=float(raw["nominal_capacity_ah"]),
        initial_soc=float(raw["initial_soc"]),
        ambient_temp_k=float(raw["ambient_temp_k"]),
        voltage_low_v=float(raw["voltage_low_v"]),
        voltage_high_v=float(raw["voltage_high_v"]),
        discharge_rates_c=[float(rate) for rate in raw["discharge_rates_c"]],
        charge_cc_rate=float(raw["charge_cc_rate"]),
        cv_cutoff_c_rate=float(raw["cv_cutoff_c_rate"]),
        rest_min=float(raw["rest_min"]),
        output_dir=Path(raw["output_dir"]),
        parameter_set=str(raw.get("parameter_set", "Chen2020")),
        thermal=str(model.get("thermal", "isothermal")),
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
            charge_compare=ChargeCompareConfig(
                enabled=bool(charge_compare_raw.get("enabled", False)),
                soc_start=float(charge_compare_raw.get("soc_start", 0.0)),
                rates_c=rates_c,
                period_by_rate_s=period_by_rate_s,
                cv_cutoff_c_rate=float(charge_compare_raw.get("cv_cutoff_c_rate", 0.05)),
                voltage_high_v=float(charge_compare_raw.get("voltage_high_v", 4.2)),
            ),
        ),
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
        return _extract_series(solution, ["State of Charge", "X-averaged cell SOC"])
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


def _solution_to_frame(solution: pybamm.Solution, config: Config, initial_soc: float | None = None) -> pd.DataFrame:
    time_s = _extract_series(solution, ["Time [s]"])
    current_a = _extract_series(solution, ["Current [A]"])
    voltage_v = _extract_series(solution, ["Terminal voltage [V]"])
    soc = _extract_soc(solution, config, initial_soc=initial_soc)
    temp_k = _extract_temperature(solution, config, len(time_s))
    return pd.DataFrame(
        {
            "time_s": time_s,
            "current_a": current_a,
            "voltage_v": voltage_v,
            "soc": soc,
            "temperature_k": temp_k,
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


def _build_parameter_values(config: Config) -> tuple[pybamm.ParameterValues, dict[str, float]]:
    values = pybamm.ParameterValues(config.parameter_set)
    base_nominal = float(values["Nominal cell capacity [A.h]"])
    if base_nominal <= 0:
        raise ValueError("Base nominal cell capacity must be positive.")

    try:
        base_parallel = float(values["Number of electrodes connected in parallel to make a cell"])
    except KeyError:
        base_parallel = 1.0

    ratio = config.nominal_capacity_ah / base_nominal
    parallel_after = base_parallel * ratio
    values.update(
        {
            "Nominal cell capacity [A.h]": config.nominal_capacity_ah,
            "Number of electrodes connected in parallel to make a cell": parallel_after,
            "Ambient temperature [K]": config.ambient_temp_k,
            "Initial temperature [K]": config.ambient_temp_k,
        }
    )
    scaling = {
        "base_nominal_capacity_ah": base_nominal,
        "target_nominal_capacity_ah": config.nominal_capacity_ah,
        "scale_ratio": ratio,
        "parallel_before": base_parallel,
        "parallel_after": parallel_after,
    }
    return values, scaling


def _write_parameter_audit(config: Config, output_dir: Path, scaling: dict[str, float]) -> Path:
    audit_path = output_dir / "parameter_audit.json"
    audit = {
        "base_parameter_set": config.parameter_set,
        "scaling": scaling,
        "scaled": [
            {
                "parameter": "Nominal cell capacity [A.h]",
                "base_value": scaling["base_nominal_capacity_ah"],
                "target_value": scaling["target_nominal_capacity_ah"],
                "migration_tag": "scaled_from_reference_cell",
            },
            {
                "parameter": "Number of electrodes connected in parallel to make a cell",
                "base_value": scaling["parallel_before"],
                "target_value": scaling["parallel_after"],
                "migration_tag": "parallel_count_scaled_with_capacity",
            },
        ],
        "reused": [
            "Electrolyte transport and kinetic baseline from Chen2020",
            "Default DFN structure and domain assumptions from PyBaMM",
        ],
        "pending_identification": [
            "NMC622-specific OCP fit",
            "NMC622 reaction-rate constants over SOC/temperature",
            "Cell-specific thermal behavior and heat transfer coefficients",
            "Rate-dependent resistance and diffusion calibration via HPPC",
        ],
        "disclaimer": "Proxy parameters for baseline simulation only; absolute accuracy is not yet claimed.",
    }
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit_path


def _write_empty_timeseries(csv_path: Path) -> None:
    pd.DataFrame(columns=["time_s", "current_a", "voltage_v", "soc", "temperature_k"]).to_csv(csv_path, index=False)


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
) -> tuple[pybamm.Solution | None, float, list[str], str | None]:
    logger = pybamm.logger
    capture = _WarningCapture()
    original_level = logger.level
    logger.addHandler(capture)
    if original_level > logging.WARNING:
        logger.setLevel(logging.WARNING)

    start = time.perf_counter()
    try:
        if t_eval is None:
            solution = sim.solve(initial_soc=initial_soc)
        else:
            solution = sim.solve(t_eval=t_eval, initial_soc=initial_soc)
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

    model = pybamm.lithium_ion.DFN(options={"thermal": config.thermal})
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    parameter_values = base_values.copy()
    parameter_values.update(
        {
            "Current function [A]": pybamm.Interpolant(times, currents, pybamm.t),
            # Replay mode should follow the provided current profile without stopping at built-in cutoffs.
            "Lower voltage cut-off [V]": 0.0,
            "Upper voltage cut-off [V]": 6.0,
        }
    )
    sim = pybamm.Simulation(
        model=model,
        parameter_values=parameter_values,
        solver=solver,
    )

    solution, runtime, warnings, error = _solve_with_warning_capture(sim, initial_soc, t_eval=times)
    if solution is None or isinstance(solution, pybamm.EmptySolution):
        return None, runtime, warnings, error or "Timeseries simulation produced an empty solution."

    dense = _solution_to_frame(solution, config, initial_soc=initial_soc)
    dense_time = dense["time_s"].to_numpy(dtype=float)
    if dense_time.size == 0:
        return None, runtime, warnings, "Timeseries simulation produced no time points."
    if dense_time[-1] < times[-1] - 1e-9:
        return (
            None,
            runtime,
            warnings,
            f"Timeseries simulation terminated early ({dense_time[-1]:.3f}s < {times[-1]:.3f}s).",
        )

    frame = pd.DataFrame(
        {
            "time_s": times,
            "current_a": currents,
            "voltage_v": np.interp(times, dense_time, dense["voltage_v"].to_numpy(dtype=float)),
            "soc": np.interp(times, dense_time, dense["soc"].to_numpy(dtype=float)),
            "temperature_k": input_temps,
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

    model = pybamm.lithium_ion.DFN(options={"thermal": config.thermal})
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=base_values.copy(),
        experiment=_build_sanity_experiment(config),
        solver=solver,
    )
    solution, runtime, warnings, error = _solve_with_warning_capture(sim, config.initial_soc)
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
    model = pybamm.lithium_ion.DFN(options={"thermal": config.thermal})
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=base_values.copy(),
        experiment=_build_baseline_experiment(config, rate_c),
        solver=solver,
    )
    start = time.perf_counter()
    try:
        solution = sim.solve(initial_soc=config.initial_soc)
    except Exception as exc:
        return RunSummary(
            case_id=_case_id(rate_c),
            converged=False,
            min_v=None,
            max_v=None,
            final_soc=None,
            runtime_s=time.perf_counter() - start,
            csv_path=None,
            error=str(exc),
        )
    runtime = time.perf_counter() - start
    frame = _solution_to_frame(solution, config)
    frame.to_csv(csv_path, index=False)
    return RunSummary(
        case_id=_case_id(rate_c),
        converged=True,
        min_v=float(np.min(frame["voltage_v"])),
        max_v=float(np.max(frame["voltage_v"])),
        final_soc=float(frame["soc"].iloc[-1]),
        runtime_s=runtime,
        csv_path=str(csv_path),
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

    model = pybamm.lithium_ion.DFN(options={"thermal": config.thermal})
    solver = pybamm.IDAKLUSolver(rtol=config.solver_rtol, atol=config.solver_atol)
    sim = pybamm.Simulation(
        model=model,
        parameter_values=base_values.copy(),
        experiment=experiment,
        solver=solver,
    )
    solution, runtime, warnings, error = _solve_with_warning_capture(
        sim,
        initial_soc=config.timeseries.charge_compare.soc_start,
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
            error=error or "Charge compare simulation produced an empty solution.",
        )
        return case, warnings

    frame = _solution_to_frame(solution, config, initial_soc=config.timeseries.charge_compare.soc_start)
    frame.to_csv(csv_path, index=False)
    final_soc = float(frame["soc"].iloc[-1])
    charge_time_s = float(frame["time_s"].iloc[-1])
    cc_end_time_s = _cc_transition_time(frame, config.timeseries.charge_compare.voltage_high_v)
    cv_time_s = None if cc_end_time_s is None else max(0.0, charge_time_s - cc_end_time_s)
    case_error = error
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


def run_baseline_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)
    gate = _run_sanity_gate(config, base_values, output_dir)

    config_dict = asdict(config)
    config_dict["output_dir"] = str(config.output_dir)
    if config.timeseries.csv_path is not None:
        config_dict["timeseries"]["csv_path"] = str(config.timeseries.csv_path)
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "baseline",
        "all_converged": False,
        "config": config_dict,
        "artifacts": {
            "parameter_audit": str(audit_path),
            "sanity_gate_csv": gate.artifact_csv,
            "sanity_gate_json": gate.artifact_json,
            "voltage_overlay_png": None,
        },
        "sanity_gate": asdict(gate),
        "cases": [],
    }
    if not gate.passed:
        warnings = list(gate.warning_messages)
        warnings.append("Sanity gate failed; batch simulations were blocked.")
        if gate.error:
            warnings.append(f"Sanity gate error: {gate.error}")
        summary["warnings"] = _dedupe_messages(warnings)
        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    cases = [_run_baseline_case(config, base_values, rate_c, output_dir) for rate_c in config.discharge_rates_c]
    overlay_path, overlay_warning = _write_overlay(output_dir, cases, "voltage_overlay.png", "DFN Baseline Voltage Overlay")

    summary["all_converged"] = all(case.converged for case in cases)
    summary["artifacts"]["voltage_overlay_png"] = str(overlay_path)
    summary["cases"] = [asdict(case) for case in cases]

    warnings: list[str] = []
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

    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _soc_grid(start: float, end: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("hppc.soc_step must be positive.")
    if not (0 < end <= start <= 1):
        raise ValueError("hppc SOC range must satisfy 0 < soc_end <= soc_start <= 1.")
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
            error=error,
        )

    frame.to_csv(csv_path, index=False)
    nonzero = frame.loc[np.abs(frame["current_a"]) > 1e-6, "current_a"]
    has_pos = bool((nonzero > 0).any())
    has_neg = bool((nonzero < 0).any())
    metrics, metric_error = _compute_hppc_metrics(frame)
    infeasible = [message for message in warnings if _warning_is_infeasible(message)]

    point_error = error
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

    config_dict = asdict(config)
    config_dict["output_dir"] = str(config.output_dir)
    if config.timeseries.csv_path is not None:
        config_dict["timeseries"]["csv_path"] = str(config.timeseries.csv_path)
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "hppc",
        "all_converged": passed,
        "config": config_dict,
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
    if overlay_warning:
        warnings.append(overlay_warning)
    for point in points:
        warnings.extend(point.warning_messages)
    if stop_reason and config.hppc.enabled:
        warnings.append(f"HPPC fail-fast triggered: {stop_reason}")
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_charge_compare_pipeline(config: Config) -> dict[str, Any]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)

    charge_cfg = config.timeseries.charge_compare
    warnings: list[str] = []
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

    config_dict = asdict(config)
    config_dict["output_dir"] = str(config.output_dir)
    if config.timeseries.csv_path is not None:
        config_dict["timeseries"]["csv_path"] = str(config.timeseries.csv_path)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "timeseries",
        "all_converged": passed,
        "config": config_dict,
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
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_timeseries_pipeline(config: Config) -> dict[str, Any]:
    if config.timeseries.charge_compare.enabled:
        return run_charge_compare_pipeline(config)

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    base_values, scaling = _build_parameter_values(config)
    audit_path = _write_parameter_audit(config, output_dir, scaling)

    output_csv = output_dir / "timeseries_output.csv"
    output_json = output_dir / "timeseries_summary.json"
    stop_reason: str | None = None
    warnings: list[str] = []
    case: RunSummary | None = None
    source_csv: str | None = None

    if not config.timeseries.enabled:
        stop_reason = "Timeseries mode disabled by configuration."
        _write_empty_timeseries(output_csv)
    elif config.timeseries.csv_path is None:
        stop_reason = "timeseries.csv_path is required when mode=timeseries."
        _write_empty_timeseries(output_csv)
    else:
        source_csv = str(config.timeseries.csv_path)
        try:
            profile = _load_timeseries_csv(config.timeseries.csv_path)
            frame, runtime, sim_warnings, sim_error = simulate_from_timeseries(
                config=config,
                base_values=base_values,
                profile=profile,
                initial_soc=config.initial_soc,
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
                    error=stop_reason,
                )
            else:
                frame.to_csv(output_csv, index=False)
                case = RunSummary(
                    case_id="timeseries_case",
                    converged=True,
                    min_v=float(np.min(frame["voltage_v"])),
                    max_v=float(np.max(frame["voltage_v"])),
                    final_soc=float(frame["soc"].iloc[-1]),
                    runtime_s=runtime,
                    csv_path=str(output_csv),
                )
                if sim_error:
                    stop_reason = sim_error
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
                error=stop_reason,
            )

    passed = bool(case and case.converged and stop_reason is None)
    payload = {
        "enabled": config.timeseries.enabled,
        "passed": passed,
        "stop_reason": stop_reason,
        "source_csv": source_csv,
        "artifacts": {
            "timeseries_output_csv": str(output_csv),
            "timeseries_summary_json": str(output_json),
        },
        "case": asdict(case) if case else None,
    }
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    config_dict = asdict(config)
    config_dict["output_dir"] = str(config.output_dir)
    if config.timeseries.csv_path is not None:
        config_dict["timeseries"]["csv_path"] = str(config.timeseries.csv_path)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "timeseries",
        "all_converged": passed,
        "config": config_dict,
        "artifacts": {
            "parameter_audit": str(audit_path),
            "timeseries_output_csv": str(output_csv),
            "timeseries_summary_json": str(output_json),
        },
        "timeseries": payload,
        "cases": [asdict(case)] if case else [],
    }
    if warnings:
        summary["warnings"] = _dedupe_messages(warnings)
    if stop_reason and config.timeseries.enabled:
        stop_warnings = summary.get("warnings", [])
        stop_warnings.append(f"Timeseries fail-fast triggered: {stop_reason}")
        summary["warnings"] = _dedupe_messages(stop_warnings)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_pipeline(config: Config, mode: str = "baseline") -> dict[str, Any]:
    if mode == "baseline":
        return run_baseline_pipeline(config)
    if mode == "hppc":
        return run_hppc_pipeline(config)
    if mode == "timeseries":
        return run_timeseries_pipeline(config)
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
    parser = argparse.ArgumentParser(description="Run baseline, HPPC, or timeseries PyBaMM DFN simulations.")
    parser.add_argument("--config", required=True, help="Path to YAML config file.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    parser.add_argument(
        "--mode",
        choices=["baseline", "hppc", "timeseries"],
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
