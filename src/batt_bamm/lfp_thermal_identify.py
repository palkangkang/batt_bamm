from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import least_squares

from batt_bamm.main import load_config
from batt_bamm.thermal_eval import run_thermal_eval


class ThermalIdentifyConfig:
    def __init__(
        self,
        *,
        base_config_path: Path,
        short_eval_config_path: Path,
        long_eval_config_path: Path,
        short_target_summary_json: Path,
        long_target_summary_json: Path,
        output_dir: Path,
        bootstrap_target_if_missing: bool,
        max_nfev: int,
        initial_guess_h: float,
        initial_guess_heat_capacity_scale: float,
        initial_guess_thermal_conductivity_scale: float,
        lower_h: float,
        lower_heat_capacity_scale: float,
        lower_thermal_conductivity_scale: float,
        upper_h: float,
        upper_heat_capacity_scale: float,
        upper_thermal_conductivity_scale: float,
        weight_cell_temperature: float,
        weight_boundary_temperature: float,
        write_round_config: bool,
    ) -> None:
        self.base_config_path = base_config_path
        self.short_eval_config_path = short_eval_config_path
        self.long_eval_config_path = long_eval_config_path
        self.short_target_summary_json = short_target_summary_json
        self.long_target_summary_json = long_target_summary_json
        self.output_dir = output_dir
        self.bootstrap_target_if_missing = bootstrap_target_if_missing
        self.max_nfev = max_nfev
        self.initial_guess_h = initial_guess_h
        self.initial_guess_heat_capacity_scale = initial_guess_heat_capacity_scale
        self.initial_guess_thermal_conductivity_scale = initial_guess_thermal_conductivity_scale
        self.lower_h = lower_h
        self.lower_heat_capacity_scale = lower_heat_capacity_scale
        self.lower_thermal_conductivity_scale = lower_thermal_conductivity_scale
        self.upper_h = upper_h
        self.upper_heat_capacity_scale = upper_heat_capacity_scale
        self.upper_thermal_conductivity_scale = upper_thermal_conductivity_scale
        self.weight_cell_temperature = weight_cell_temperature
        self.weight_boundary_temperature = weight_boundary_temperature
        self.write_round_config = write_round_config


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _resolve_path(raw: Any, *, base_dir: Path, field_name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field_name} must be a non-empty string path.")
    path = Path(raw.strip())
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _as_positive_float(value: Any, *, field_name: str, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")
    if allow_zero:
        if number < 0:
            raise ValueError(f"{field_name} must be >= 0.")
    elif number <= 0:
        raise ValueError(f"{field_name} must be > 0.")
    return number


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y"}:
        return True
    if token in {"0", "false", "no", "n"}:
        return False
    return default


def _as_positive_int(value: Any, *, field_name: str, default: int) -> int:
    if value is None:
        return int(default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be > 0.")
    return number


def load_identify_config(config_path: str | Path) -> ThermalIdentifyConfig:
    config_path = Path(config_path).resolve()
    raw = _read_yaml(config_path)
    base_dir = config_path.parent

    base_config_path = _resolve_path(raw.get("base_config_path"), base_dir=base_dir, field_name="base_config_path")
    short_eval_config_path = _resolve_path(
        raw.get("short_eval_config_path"), base_dir=base_dir, field_name="short_eval_config_path"
    )
    long_eval_config_path = _resolve_path(
        raw.get("long_eval_config_path"), base_dir=base_dir, field_name="long_eval_config_path"
    )

    output_dir = _resolve_path(raw.get("output_dir"), base_dir=base_dir, field_name="output_dir")
    target_raw = raw.get("target", {})
    if target_raw is None:
        target_raw = {}
    if not isinstance(target_raw, dict):
        raise ValueError("target must be a mapping when provided.")

    short_target_summary_json = _resolve_path(
        target_raw.get("short_summary_json"), base_dir=base_dir, field_name="target.short_summary_json"
    )
    long_target_summary_json = _resolve_path(
        target_raw.get("long_summary_json"), base_dir=base_dir, field_name="target.long_summary_json"
    )
    bootstrap_target_if_missing = _as_bool(target_raw.get("bootstrap_if_missing"), default=False)

    fit_raw = raw.get("fit", {})
    if fit_raw is None:
        fit_raw = {}
    if not isinstance(fit_raw, dict):
        raise ValueError("fit must be a mapping when provided.")

    max_nfev = _as_positive_int(fit_raw.get("max_nfev"), field_name="fit.max_nfev", default=8)
    initial_guess_raw = fit_raw.get("initial_guess", {})
    if initial_guess_raw is None:
        initial_guess_raw = {}
    if not isinstance(initial_guess_raw, dict):
        raise ValueError("fit.initial_guess must be a mapping when provided.")
    lower_bounds_raw = fit_raw.get("lower_bounds", {})
    if lower_bounds_raw is None:
        lower_bounds_raw = {}
    if not isinstance(lower_bounds_raw, dict):
        raise ValueError("fit.lower_bounds must be a mapping when provided.")
    upper_bounds_raw = fit_raw.get("upper_bounds", {})
    if upper_bounds_raw is None:
        upper_bounds_raw = {}
    if not isinstance(upper_bounds_raw, dict):
        raise ValueError("fit.upper_bounds must be a mapping when provided.")

    initial_guess_h = _as_positive_float(
        initial_guess_raw.get("total_heat_transfer_coefficient_w_m2_k", 120.0),
        field_name="fit.initial_guess.total_heat_transfer_coefficient_w_m2_k",
    )
    initial_guess_heat_capacity_scale = _as_positive_float(
        initial_guess_raw.get("heat_capacity_scale", 1.0),
        field_name="fit.initial_guess.heat_capacity_scale",
    )
    initial_guess_thermal_conductivity_scale = _as_positive_float(
        initial_guess_raw.get("thermal_conductivity_scale", 1.0),
        field_name="fit.initial_guess.thermal_conductivity_scale",
    )

    lower_h = _as_positive_float(
        lower_bounds_raw.get("total_heat_transfer_coefficient_w_m2_k", 30.0),
        field_name="fit.lower_bounds.total_heat_transfer_coefficient_w_m2_k",
    )
    lower_heat_capacity_scale = _as_positive_float(
        lower_bounds_raw.get("heat_capacity_scale", 0.5),
        field_name="fit.lower_bounds.heat_capacity_scale",
    )
    lower_thermal_conductivity_scale = _as_positive_float(
        lower_bounds_raw.get("thermal_conductivity_scale", 0.5),
        field_name="fit.lower_bounds.thermal_conductivity_scale",
    )

    upper_h = _as_positive_float(
        upper_bounds_raw.get("total_heat_transfer_coefficient_w_m2_k", 400.0),
        field_name="fit.upper_bounds.total_heat_transfer_coefficient_w_m2_k",
    )
    upper_heat_capacity_scale = _as_positive_float(
        upper_bounds_raw.get("heat_capacity_scale", 2.0),
        field_name="fit.upper_bounds.heat_capacity_scale",
    )
    upper_thermal_conductivity_scale = _as_positive_float(
        upper_bounds_raw.get("thermal_conductivity_scale", 2.0),
        field_name="fit.upper_bounds.thermal_conductivity_scale",
    )

    if not (lower_h < upper_h):
        raise ValueError("fit bounds invalid: lower_h must be < upper_h.")
    if not (lower_heat_capacity_scale < upper_heat_capacity_scale):
        raise ValueError("fit bounds invalid: lower_heat_capacity_scale must be < upper_heat_capacity_scale.")
    if not (lower_thermal_conductivity_scale < upper_thermal_conductivity_scale):
        raise ValueError("fit bounds invalid: lower_thermal_conductivity_scale must be < upper_thermal_conductivity_scale.")

    weights_raw = fit_raw.get("weights", {})
    if weights_raw is None:
        weights_raw = {}
    if not isinstance(weights_raw, dict):
        raise ValueError("fit.weights must be a mapping when provided.")
    weight_cell_temperature = _as_positive_float(
        weights_raw.get("cell_temperature", 1.0),
        field_name="fit.weights.cell_temperature",
        allow_zero=True,
    )
    weight_boundary_temperature = _as_positive_float(
        weights_raw.get("boundary_temperature", 0.3),
        field_name="fit.weights.boundary_temperature",
        allow_zero=True,
    )
    if np.isclose(weight_cell_temperature, 0.0) and np.isclose(weight_boundary_temperature, 0.0):
        raise ValueError("At least one temperature weight must be > 0.")

    output_raw = raw.get("output", {})
    if output_raw is None:
        output_raw = {}
    if not isinstance(output_raw, dict):
        raise ValueError("output must be a mapping when provided.")
    write_round_config = _as_bool(output_raw.get("write_round_config"), default=True)

    return ThermalIdentifyConfig(
        base_config_path=base_config_path,
        short_eval_config_path=short_eval_config_path,
        long_eval_config_path=long_eval_config_path,
        short_target_summary_json=short_target_summary_json,
        long_target_summary_json=long_target_summary_json,
        output_dir=output_dir,
        bootstrap_target_if_missing=bootstrap_target_if_missing,
        max_nfev=max_nfev,
        initial_guess_h=initial_guess_h,
        initial_guess_heat_capacity_scale=initial_guess_heat_capacity_scale,
        initial_guess_thermal_conductivity_scale=initial_guess_thermal_conductivity_scale,
        lower_h=lower_h,
        lower_heat_capacity_scale=lower_heat_capacity_scale,
        lower_thermal_conductivity_scale=lower_thermal_conductivity_scale,
        upper_h=upper_h,
        upper_heat_capacity_scale=upper_heat_capacity_scale,
        upper_thermal_conductivity_scale=upper_thermal_conductivity_scale,
        weight_cell_temperature=weight_cell_temperature,
        weight_boundary_temperature=weight_boundary_temperature,
        write_round_config=write_round_config,
    )


def _load_case_artifacts(summary_json: Path) -> dict[str, Path]:
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Thermal summary must be an object: {summary_json}")
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"Thermal summary has no cases: {summary_json}")
    mapping: dict[str, Path] = {}
    base_dir = summary_json.parent
    for row in cases:
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id", "")).strip()
        csv_raw = row.get("artifact_csv")
        if not case_id or not isinstance(csv_raw, str) or not csv_raw.strip():
            continue
        csv_path = Path(csv_raw.strip())
        if not csv_path.is_absolute():
            csv_path = (base_dir / csv_path).resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"Case artifact CSV not found: {csv_path}")
        mapping[case_id] = csv_path
    if not mapping:
        raise ValueError(f"Thermal summary has no readable artifact_csv entries: {summary_json}")
    return mapping


def _load_case_frames(case_csv_map: dict[str, Path]) -> dict[str, pd.DataFrame]:
    required = {
        "time_s",
        "cell_temperature_k",
        "boundary_temperature_k",
    }
    frames: dict[str, pd.DataFrame] = {}
    for case_id, csv_path in case_csv_map.items():
        frame = pd.read_csv(csv_path)
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{case_id}: missing columns in {csv_path}: {missing}")
        frame = frame.copy()
        frame["time_s"] = pd.to_numeric(frame["time_s"], errors="coerce")
        frame["cell_temperature_k"] = pd.to_numeric(frame["cell_temperature_k"], errors="coerce")
        frame["boundary_temperature_k"] = pd.to_numeric(frame["boundary_temperature_k"], errors="coerce")
        frame = frame.dropna(subset=["time_s", "cell_temperature_k", "boundary_temperature_k"]).reset_index(drop=True)
        if frame.empty:
            raise ValueError(f"{case_id}: frame is empty after numeric cleanup: {csv_path}")
        if np.any(np.diff(frame["time_s"].to_numpy(dtype=float)) < 0):
            raise ValueError(f"{case_id}: time_s must be non-decreasing in {csv_path}")
        frames[case_id] = frame
    return frames


def _prepare_bootstrap_targets(config: ThermalIdentifyConfig) -> None:
    if config.short_target_summary_json.exists() and config.long_target_summary_json.exists():
        return
    if not config.bootstrap_target_if_missing:
        missing = []
        if not config.short_target_summary_json.exists():
            missing.append(str(config.short_target_summary_json))
        if not config.long_target_summary_json.exists():
            missing.append(str(config.long_target_summary_json))
        raise FileNotFoundError(f"Target summary not found: {missing}")

    short_out = config.short_target_summary_json.parent
    long_out = config.long_target_summary_json.parent
    short_out.mkdir(parents=True, exist_ok=True)
    long_out.mkdir(parents=True, exist_ok=True)
    run_thermal_eval(config.short_eval_config_path, output_dir_override=short_out)
    run_thermal_eval(config.long_eval_config_path, output_dir_override=long_out)


def _run_eval_with_overrides(
    *,
    eval_config_path: Path,
    base_config_template_path: Path,
    output_dir: Path,
    h_w_m2_k: float,
    heat_capacity_scale: float,
    thermal_conductivity_scale: float,
) -> dict[str, Any]:
    base_raw = _read_yaml(base_config_template_path)
    model = base_raw.get("model", {})
    if not isinstance(model, dict):
        model = {}
        base_raw["model"] = model
    thermal_params = model.get("thermal_params", {})
    if not isinstance(thermal_params, dict):
        thermal_params = {}
    thermal_params["total_heat_transfer_coefficient_w_m2_k"] = float(h_w_m2_k)
    model["thermal_params"] = thermal_params

    thermal_scales = model.get("thermal_property_scales", {})
    if not isinstance(thermal_scales, dict):
        thermal_scales = {}
    thermal_scales["heat_capacity_scale"] = float(heat_capacity_scale)
    thermal_scales["thermal_conductivity_scale"] = float(thermal_conductivity_scale)
    model["thermal_property_scales"] = thermal_scales

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir = output_dir / "_generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    base_override_path = generated_dir / "base_override.yaml"
    _write_yaml(base_override_path, base_raw)

    eval_raw = _read_yaml(eval_config_path)
    eval_raw["base_config_path"] = str(base_override_path)
    eval_override_path = generated_dir / "eval_override.yaml"
    _write_yaml(eval_override_path, eval_raw)

    return run_thermal_eval(eval_override_path, output_dir_override=output_dir)


def _aligned_temperature_residuals(
    *,
    target_frame: pd.DataFrame,
    candidate_frame: pd.DataFrame,
    weight_cell_temperature: float,
    weight_boundary_temperature: float,
) -> tuple[np.ndarray, dict[str, float]]:
    target_time = target_frame["time_s"].to_numpy(dtype=float)
    candidate_time = candidate_frame["time_s"].to_numpy(dtype=float)
    overlap_end = min(float(np.max(target_time)), float(np.max(candidate_time)))
    if overlap_end <= 0:
        raise ValueError("No positive overlap in case timeseries.")
    keep = target_time <= overlap_end + 1e-12
    if not np.any(keep):
        raise ValueError("No overlap samples for residual calculation.")
    sample_t = target_time[keep]

    target_cell = target_frame.loc[keep, "cell_temperature_k"].to_numpy(dtype=float)
    target_boundary = target_frame.loc[keep, "boundary_temperature_k"].to_numpy(dtype=float)
    candidate_cell = np.interp(sample_t, candidate_time, candidate_frame["cell_temperature_k"].to_numpy(dtype=float))
    candidate_boundary = np.interp(
        sample_t, candidate_time, candidate_frame["boundary_temperature_k"].to_numpy(dtype=float)
    )

    residual_parts: list[np.ndarray] = []
    if weight_cell_temperature > 0:
        residual_parts.append((candidate_cell - target_cell) * float(weight_cell_temperature))
    if weight_boundary_temperature > 0:
        residual_parts.append((candidate_boundary - target_boundary) * float(weight_boundary_temperature))
    residual = np.concatenate(residual_parts) if residual_parts else np.array([], dtype=float)
    metrics = {
        "cell_rmse_k": float(np.sqrt(np.mean(np.square(candidate_cell - target_cell)))),
        "boundary_rmse_k": float(np.sqrt(np.mean(np.square(candidate_boundary - target_boundary)))),
        "sample_count": float(sample_t.size),
    }
    return residual, metrics


def _aggregate_metrics(residuals: np.ndarray, metrics_rows: list[dict[str, float]]) -> dict[str, float]:
    if residuals.size == 0:
        raise ValueError("Residual vector is empty.")
    cell_rmse_values = [row["cell_rmse_k"] for row in metrics_rows]
    boundary_rmse_values = [row["boundary_rmse_k"] for row in metrics_rows]
    sample_count = sum(row["sample_count"] for row in metrics_rows)
    return {
        "global_rmse_weighted": float(np.sqrt(np.mean(np.square(residuals)))),
        "mean_cell_rmse_k": float(np.mean(cell_rmse_values)),
        "mean_boundary_rmse_k": float(np.mean(boundary_rmse_values)),
        "sample_count": float(sample_count),
    }


def _compare_eval_to_target(
    *,
    target_case_frames: dict[str, pd.DataFrame],
    candidate_summary: dict[str, Any],
    weight_cell_temperature: float,
    weight_boundary_temperature: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    case_csv_map: dict[str, Path] = {}
    cases = candidate_summary.get("cases", [])
    if isinstance(cases, list):
        for row in cases:
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id", "")).strip()
            csv_raw = row.get("artifact_csv")
            if not case_id or not isinstance(csv_raw, str) or not csv_raw.strip():
                continue
            csv_path = Path(csv_raw.strip())
            if not csv_path.is_absolute():
                csv_path = (Path.cwd() / csv_path).resolve()
            case_csv_map[case_id] = csv_path

    residual_list: list[np.ndarray] = []
    metrics_rows: list[dict[str, float]] = []
    compared_case_ids: list[str] = []
    for case_id, target_frame in target_case_frames.items():
        candidate_csv = case_csv_map.get(case_id)
        if candidate_csv is None or not candidate_csv.exists():
            raise FileNotFoundError(f"Candidate result missing case CSV for case_id={case_id}")
        candidate_frame = pd.read_csv(candidate_csv)
        required = {"time_s", "cell_temperature_k", "boundary_temperature_k"}
        missing = sorted(required - set(candidate_frame.columns))
        if missing:
            raise ValueError(f"{case_id}: candidate frame missing columns: {missing}")
        candidate_frame = candidate_frame.copy()
        for column in ["time_s", "cell_temperature_k", "boundary_temperature_k"]:
            candidate_frame[column] = pd.to_numeric(candidate_frame[column], errors="coerce")
        candidate_frame = candidate_frame.dropna(subset=["time_s", "cell_temperature_k", "boundary_temperature_k"])
        if candidate_frame.empty:
            raise ValueError(f"{case_id}: candidate frame is empty.")

        residual_case, metrics_case = _aligned_temperature_residuals(
            target_frame=target_frame,
            candidate_frame=candidate_frame,
            weight_cell_temperature=weight_cell_temperature,
            weight_boundary_temperature=weight_boundary_temperature,
        )
        residual_list.append(residual_case)
        metrics_rows.append(metrics_case)
        compared_case_ids.append(case_id)

    residuals = np.concatenate(residual_list) if residual_list else np.array([], dtype=float)
    agg = _aggregate_metrics(residuals, metrics_rows)
    return residuals, {
        "compared_case_ids": compared_case_ids,
        "per_case_metrics": metrics_rows,
        "aggregate": agg,
    }


def _round_key(values: np.ndarray) -> tuple[float, float, float]:
    return (round(float(values[0]), 6), round(float(values[1]), 6), round(float(values[2]), 6))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_round_config(
    *,
    params: dict[str, float],
    output_dir: Path,
    source_config: ThermalIdentifyConfig,
) -> Path:
    repo = _repo_root()
    path = repo / "configs" / "cells" / "lfp_130ah" / "thermal_identified_round1.yaml"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "base_config_path": str(source_config.base_config_path),
            "short_eval_config_path": str(source_config.short_eval_config_path),
            "long_eval_config_path": str(source_config.long_eval_config_path),
            "short_target_summary_json": str(source_config.short_target_summary_json),
            "long_target_summary_json": str(source_config.long_target_summary_json),
            "run_output_dir": str(output_dir),
        },
        "model": {
            "thermal_params": {
                "total_heat_transfer_coefficient_w_m2_k": float(params["h_w_m2_k"]),
            },
            "thermal_property_scales": {
                "heat_capacity_scale": float(params["heat_capacity_scale"]),
                "thermal_conductivity_scale": float(params["thermal_conductivity_scale"]),
            },
        },
    }
    _write_yaml(path, payload)
    return path


def run_lfp_thermal_identification(config_path: str | Path) -> dict[str, Any]:
    cfg = load_identify_config(config_path)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_config(cfg.base_config_path)
    if base_cfg.model_type != "dfn" or base_cfg.chemistry != "lfp":
        raise ValueError("LFP thermal identification requires DFN+LFP base config.")
    if base_cfg.thermal != "lumped":
        raise ValueError("LFP thermal identification requires model.thermal=lumped in base config.")

    _prepare_bootstrap_targets(cfg)
    short_target_frames = _load_case_frames(_load_case_artifacts(cfg.short_target_summary_json))
    long_target_frames = _load_case_frames(_load_case_artifacts(cfg.long_target_summary_json))

    trials: list[dict[str, Any]] = []
    cache: dict[tuple[float, float, float], np.ndarray] = {}
    cache_metrics: dict[tuple[float, float, float], dict[str, Any]] = {}

    fit_runs_root = cfg.output_dir / "fit_runs"
    fit_runs_root.mkdir(parents=True, exist_ok=True)

    def objective(params: np.ndarray) -> np.ndarray:
        key = _round_key(params)
        if key in cache:
            return cache[key]
        h_w_m2_k, heat_capacity_scale, thermal_conductivity_scale = (
            float(params[0]),
            float(params[1]),
            float(params[2]),
        )
        trial_index = len(trials) + 1
        trial_dir = fit_runs_root / f"trial_{trial_index:03d}"
        trial_payload: dict[str, Any] = {
            "trial": trial_index,
            "h_w_m2_k": h_w_m2_k,
            "heat_capacity_scale": heat_capacity_scale,
            "thermal_conductivity_scale": thermal_conductivity_scale,
        }
        try:
            summary = _run_eval_with_overrides(
                eval_config_path=cfg.short_eval_config_path,
                base_config_template_path=cfg.base_config_path,
                output_dir=trial_dir,
                h_w_m2_k=h_w_m2_k,
                heat_capacity_scale=heat_capacity_scale,
                thermal_conductivity_scale=thermal_conductivity_scale,
            )
            residuals, metrics = _compare_eval_to_target(
                target_case_frames=short_target_frames,
                candidate_summary=summary,
                weight_cell_temperature=cfg.weight_cell_temperature,
                weight_boundary_temperature=cfg.weight_boundary_temperature,
            )
            agg = metrics["aggregate"]
            trial_payload.update(
                {
                    "converged": bool(summary.get("passed", False)),
                    "global_rmse_weighted": float(agg["global_rmse_weighted"]),
                    "mean_cell_rmse_k": float(agg["mean_cell_rmse_k"]),
                    "mean_boundary_rmse_k": float(agg["mean_boundary_rmse_k"]),
                    "sample_count": int(agg["sample_count"]),
                    "error": None,
                    "trial_output_dir": str(trial_dir),
                }
            )
        except Exception as exc:
            residuals = np.full(8, 1e3, dtype=float)
            trial_payload.update(
                {
                    "converged": False,
                    "global_rmse_weighted": None,
                    "mean_cell_rmse_k": None,
                    "mean_boundary_rmse_k": None,
                    "sample_count": 0,
                    "error": str(exc),
                    "trial_output_dir": str(trial_dir),
                }
            )
            metrics = {"error": str(exc)}
        trials.append(trial_payload)
        cache[key] = residuals
        cache_metrics[key] = metrics
        return residuals

    x0 = np.array(
        [
            cfg.initial_guess_h,
            cfg.initial_guess_heat_capacity_scale,
            cfg.initial_guess_thermal_conductivity_scale,
        ],
        dtype=float,
    )
    bounds = (
        np.array(
            [
                cfg.lower_h,
                cfg.lower_heat_capacity_scale,
                cfg.lower_thermal_conductivity_scale,
            ],
            dtype=float,
        ),
        np.array(
            [
                cfg.upper_h,
                cfg.upper_heat_capacity_scale,
                cfg.upper_thermal_conductivity_scale,
            ],
            dtype=float,
        ),
    )

    baseline_residual = objective(x0)
    baseline_metrics = cache_metrics[_round_key(x0)]
    result = least_squares(
        objective,
        x0=x0,
        bounds=bounds,
        method="trf",
        loss="soft_l1",
        f_scale=1.0,
        max_nfev=int(cfg.max_nfev),
    )

    x_best = result.x.astype(float)
    best_key = _round_key(x_best)
    if best_key not in cache:
        objective(x_best)
    short_best_metrics = cache_metrics.get(best_key, {})

    short_best_dir = cfg.output_dir / "short_best"
    short_best_summary = _run_eval_with_overrides(
        eval_config_path=cfg.short_eval_config_path,
        base_config_template_path=cfg.base_config_path,
        output_dir=short_best_dir,
        h_w_m2_k=float(x_best[0]),
        heat_capacity_scale=float(x_best[1]),
        thermal_conductivity_scale=float(x_best[2]),
    )
    short_best_residual, short_best_compare = _compare_eval_to_target(
        target_case_frames=short_target_frames,
        candidate_summary=short_best_summary,
        weight_cell_temperature=cfg.weight_cell_temperature,
        weight_boundary_temperature=cfg.weight_boundary_temperature,
    )

    long_best_dir = cfg.output_dir / "long_best"
    long_best_summary = _run_eval_with_overrides(
        eval_config_path=cfg.long_eval_config_path,
        base_config_template_path=cfg.base_config_path,
        output_dir=long_best_dir,
        h_w_m2_k=float(x_best[0]),
        heat_capacity_scale=float(x_best[1]),
        thermal_conductivity_scale=float(x_best[2]),
    )
    long_best_residual, long_best_compare = _compare_eval_to_target(
        target_case_frames=long_target_frames,
        candidate_summary=long_best_summary,
        weight_cell_temperature=cfg.weight_cell_temperature,
        weight_boundary_temperature=cfg.weight_boundary_temperature,
    )

    trial_csv = cfg.output_dir / "lfp_thermal_ident_trials.csv"
    pd.DataFrame(trials).to_csv(trial_csv, index=False)

    best_params = {
        "h_w_m2_k": float(x_best[0]),
        "heat_capacity_scale": float(x_best[1]),
        "thermal_conductivity_scale": float(x_best[2]),
    }
    round_config_path: Path | None = None
    if cfg.write_round_config:
        round_config_path = _write_round_config(params=best_params, output_dir=cfg.output_dir, source_config=cfg)

    short_baseline_rmse = float(np.sqrt(np.mean(np.square(baseline_residual))))
    short_best_rmse = float(np.sqrt(np.mean(np.square(short_best_residual))))
    improvement = None
    if short_baseline_rmse > 0:
        improvement = float((short_baseline_rmse - short_best_rmse) / short_baseline_rmse)

    fit_converged = bool(result.success or float(result.cost) <= 1e-8)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": fit_converged,
        "stop_reason": None if fit_converged else result.message,
        "base_config_path": str(cfg.base_config_path),
        "short_eval_config_path": str(cfg.short_eval_config_path),
        "long_eval_config_path": str(cfg.long_eval_config_path),
        "short_target_summary_json": str(cfg.short_target_summary_json),
        "long_target_summary_json": str(cfg.long_target_summary_json),
        "max_nfev": int(cfg.max_nfev),
        "fit_result": {
            "status": int(result.status),
            "message": str(result.message),
            "nfev": int(result.nfev),
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "converged_for_round": fit_converged,
        },
        "baseline_guess": {
            "h_w_m2_k": float(x0[0]),
            "heat_capacity_scale": float(x0[1]),
            "thermal_conductivity_scale": float(x0[2]),
            "short_rmse_weighted": short_baseline_rmse,
            "metrics": baseline_metrics,
        },
        "best_params": best_params,
        "short_best_metrics": {
            "short_rmse_weighted": short_best_rmse,
            "improvement_vs_baseline": improvement,
            "metrics_cache_entry": short_best_metrics,
            "compare": short_best_compare,
            "summary_passed": bool(short_best_summary.get("passed", False)),
            "summary_completed_cases": short_best_summary.get("completed_cases"),
            "summary_total_cases": short_best_summary.get("total_cases"),
        },
        "long_best_metrics": {
            "long_rmse_weighted": float(np.sqrt(np.mean(np.square(long_best_residual)))),
            "compare": long_best_compare,
            "summary_passed": bool(long_best_summary.get("passed", False)),
            "summary_completed_cases": long_best_summary.get("completed_cases"),
            "summary_total_cases": long_best_summary.get("total_cases"),
        },
        "artifacts": {
            "trials_csv": str(trial_csv),
            "summary_json": str(cfg.output_dir / "lfp_thermal_ident_summary.json"),
            "short_best_summary_json": str(short_best_dir / "thermal_eval_summary.json"),
            "long_best_summary_json": str(long_best_dir / "thermal_eval_summary.json"),
            "short_best_manifest_json": str(short_best_dir / "thermal_eval_manifest.json"),
            "long_best_manifest_json": str(long_best_dir / "thermal_eval_manifest.json"),
            "round_config_yaml": None if round_config_path is None else str(round_config_path),
        },
    }
    summary_path = cfg.output_dir / "lfp_thermal_ident_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Identify LFP DFN lumped thermal parameters (h, heat-capacity scale, conductivity scale)."
    )
    parser.add_argument("--config", required=True, help="Path to thermal-identification YAML config.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_lfp_thermal_identification(args.config)
    print(json.dumps(summary, indent=2))
    return 0 if bool(summary.get("passed", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
