from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import least_squares

from batt_bamm.ecm_fit_compare import _simulate_thevenin_voltage, _simulate_thevenin_voltage_2rc
from batt_bamm.main import _build_parameter_values, load_config, simulate_from_timeseries


_ECM_SCHEMA_VERSION = "ecm_temp_2d_v1"
_DEFAULT_SOC_GRID = [0.2, 0.4, 0.6, 0.8]
_DEFAULT_TEMP_AXIS_C = [24.999, 25.001]


@dataclass(frozen=True)
class CaseData:
    case_id: str
    csv_path: Path
    split: str
    initial_soc: float
    nominal_capacity_ah: float
    ambient_temp_k: float
    weight: float
    frame: pd.DataFrame
    normalized_csv: Path


@dataclass(frozen=True)
class TuneConfig:
    config_path: Path
    base_config_path: Path
    output_dir: Path
    manifest_csv: Path
    current_sign: str
    default_temp_k: float
    min_train_cases: int
    models: list[str]
    loss_dynamic_weight: float
    current_dynamic_threshold_a: float
    max_nfev: int
    ecm_enabled: bool
    ecm_order: int
    ecm_fallback_to_1rc: bool
    ecm_soc_grid: list[float]
    ecm_temp_axis_c: list[float]
    dfn_enabled: bool
    dfn_run_only_if_ecm_passed: bool
    dfn_max_nfev: int
    dfn_capacity_scale_bounds: tuple[float, float]
    dfn_initial_soc_bounds: tuple[float, float]
    thermal_enabled: bool


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _to_path(value: Any, *, base_dir: Path, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty path string.")
    path = Path(value.strip())
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _as_positive_float(value: Any, *, field_name: str, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{field_name} is required.")
        value = default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive number.") from exc
    if not np.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field_name} must be a positive finite number.")
    return parsed


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "y", "on"}:
            return True
        if token in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _normalize_float_list(value: Any, *, default: list[float], field_name: str, min_len: int = 1) -> list[float]:
    source = default if value is None else value
    if not isinstance(source, list) or len(source) < min_len:
        raise ValueError(f"{field_name} must contain at least {min_len} values.")
    parsed = [float(item) for item in source]
    if not all(np.isfinite(item) for item in parsed):
        raise ValueError(f"{field_name} contains non-finite values.")
    if any(b <= a for a, b in zip(parsed, parsed[1:])):
        raise ValueError(f"{field_name} must be strictly increasing.")
    return parsed


def load_tune_config(config_path: str | Path) -> TuneConfig:
    config_path = Path(config_path).resolve()
    raw = _read_yaml(config_path)
    base_dir = config_path.parent
    target = raw.get("target", {})
    fit = raw.get("fit", {})
    if not isinstance(target, dict):
        raise ValueError("target must be a mapping.")
    if not isinstance(fit, dict):
        raise ValueError("fit must be a mapping.")

    ecm_raw = fit.get("ecm", {})
    dfn_raw = fit.get("dfn", {})
    thermal_raw = fit.get("thermal", {})
    if ecm_raw is None:
        ecm_raw = {}
    if dfn_raw is None:
        dfn_raw = {}
    if thermal_raw is None:
        thermal_raw = {}
    if not isinstance(ecm_raw, dict) or not isinstance(dfn_raw, dict) or not isinstance(thermal_raw, dict):
        raise ValueError("fit.ecm, fit.dfn and fit.thermal must be mappings when provided.")

    current_sign = str(target.get("current_sign", "discharge_positive")).strip().lower()
    if current_sign not in {"discharge_positive", "charge_positive"}:
        raise ValueError("target.current_sign must be 'discharge_positive' or 'charge_positive'.")

    models = [str(item).strip().lower() for item in fit.get("models", ["ecm", "dfn"])]
    unknown_models = sorted(set(models) - {"ecm", "dfn"})
    if unknown_models:
        raise ValueError(f"fit.models contains unsupported entries: {unknown_models}")

    ecm_order = int(ecm_raw.get("ecm_order", 2))
    if ecm_order not in {1, 2}:
        raise ValueError("fit.ecm.ecm_order must be 1 or 2.")

    dfn_bounds = dfn_raw.get("bounds", {})
    if dfn_bounds is None:
        dfn_bounds = {}
    if not isinstance(dfn_bounds, dict):
        raise ValueError("fit.dfn.bounds must be a mapping when provided.")
    capacity_bounds = dfn_bounds.get("capacity_scale", [0.9, 1.1])
    initial_soc_bounds = dfn_bounds.get("initial_soc", [0.05, 0.99])
    if not isinstance(capacity_bounds, list) or len(capacity_bounds) != 2:
        raise ValueError("fit.dfn.bounds.capacity_scale must be [lower, upper].")
    if not isinstance(initial_soc_bounds, list) or len(initial_soc_bounds) != 2:
        raise ValueError("fit.dfn.bounds.initial_soc must be [lower, upper].")
    capacity_tuple = (float(capacity_bounds[0]), float(capacity_bounds[1]))
    initial_soc_tuple = (float(initial_soc_bounds[0]), float(initial_soc_bounds[1]))
    if capacity_tuple[0] <= 0 or capacity_tuple[0] >= capacity_tuple[1]:
        raise ValueError("fit.dfn.bounds.capacity_scale must be positive and increasing.")
    if initial_soc_tuple[0] < 0 or initial_soc_tuple[1] > 1 or initial_soc_tuple[0] >= initial_soc_tuple[1]:
        raise ValueError("fit.dfn.bounds.initial_soc must stay within [0, 1] and be increasing.")

    return TuneConfig(
        config_path=config_path,
        base_config_path=_to_path(raw.get("base_config_path"), base_dir=base_dir, field_name="base_config_path"),
        output_dir=_to_path(raw.get("output_dir"), base_dir=base_dir, field_name="output_dir"),
        manifest_csv=_to_path(target.get("manifest_csv"), base_dir=base_dir, field_name="target.manifest_csv"),
        current_sign=current_sign,
        default_temp_k=_as_positive_float(target.get("default_temp_k"), field_name="target.default_temp_k", default=298.15),
        min_train_cases=int(fit.get("min_train_cases", 2)),
        models=models,
        loss_dynamic_weight=float(fit.get("loss_dynamic_weight", 0.7)),
        current_dynamic_threshold_a=float(fit.get("current_dynamic_threshold_a", 1.0)),
        max_nfev=int(fit.get("max_nfev", 40)),
        ecm_enabled=_as_bool(ecm_raw.get("enabled"), default=("ecm" in models)),
        ecm_order=ecm_order,
        ecm_fallback_to_1rc=_as_bool(ecm_raw.get("fallback_to_1rc"), default=True),
        ecm_soc_grid=_normalize_float_list(
            ecm_raw.get("soc_grid"),
            default=_DEFAULT_SOC_GRID,
            field_name="fit.ecm.soc_grid",
            min_len=2,
        ),
        ecm_temp_axis_c=_normalize_float_list(
            ecm_raw.get("temp_axis_c"),
            default=_DEFAULT_TEMP_AXIS_C,
            field_name="fit.ecm.temp_axis_c",
            min_len=2,
        ),
        dfn_enabled=_as_bool(dfn_raw.get("enabled"), default=("dfn" in models)),
        dfn_run_only_if_ecm_passed=_as_bool(dfn_raw.get("run_only_if_ecm_passed"), default=True),
        dfn_max_nfev=int(dfn_raw.get("max_nfev", max(1, int(fit.get("max_nfev", 8))))),
        dfn_capacity_scale_bounds=capacity_tuple,
        dfn_initial_soc_bounds=initial_soc_tuple,
        thermal_enabled=_as_bool(thermal_raw.get("enabled"), default=False),
    )


def _diagnostic_row(case_id: str, severity: str, stage: str, message: str, suggestion: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "severity": severity,
        "stage": stage,
        "message": message,
        "suggestion": suggestion,
    }


def _load_cases(config: TuneConfig) -> tuple[list[CaseData], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    if not config.manifest_csv.exists():
        raise FileNotFoundError(f"Manifest CSV not found: {config.manifest_csv}")
    manifest = pd.read_csv(config.manifest_csv)
    required = [
        "case_id",
        "csv_path",
        "split",
        "initial_soc",
        "nominal_capacity_ah",
        "ambient_temp_k",
        "weight",
    ]
    missing = [column for column in required if column not in manifest.columns]
    if missing:
        raise ValueError(f"Manifest missing required columns: {missing}")

    normalized_dir = config.output_dir / "normalized_inputs"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    cases: list[CaseData] = []
    for index, row in manifest.iterrows():
        case_id = str(row.get("case_id", "")).strip() or f"case_{index:03d}"
        try:
            csv_path = _to_path(row.get("csv_path"), base_dir=config.manifest_csv.parent, field_name=f"manifest[{case_id}].csv_path")
            split = str(row.get("split", "train")).strip().lower()
            if split not in {"train", "validation"}:
                raise ValueError("split must be 'train' or 'validation'.")
            initial_soc = float(row.get("initial_soc"))
            capacity_ah = _as_positive_float(row.get("nominal_capacity_ah"), field_name=f"{case_id}.nominal_capacity_ah")
            ambient_temp_k = _as_positive_float(row.get("ambient_temp_k"), field_name=f"{case_id}.ambient_temp_k")
            weight = _as_positive_float(row.get("weight"), field_name=f"{case_id}.weight", default=1.0)
            if not (0.0 <= initial_soc <= 1.0):
                raise ValueError("initial_soc must be within [0, 1].")
            frame = _load_external_case_csv(
                case_id=case_id,
                csv_path=csv_path,
                default_temp_k=ambient_temp_k if np.isfinite(ambient_temp_k) else config.default_temp_k,
                current_sign=config.current_sign,
            )
        except Exception as exc:
            diagnostics.append(
                _diagnostic_row(
                    case_id,
                    "error",
                    "input",
                    str(exc),
                    "检查 manifest 与测试 CSV 字段、数值类型、时间递增性和电流符号。",
                )
            )
            continue

        normalized_csv = normalized_dir / f"{case_id}.csv"
        frame.to_csv(normalized_csv, index=False)
        cases.append(
            CaseData(
                case_id=case_id,
                csv_path=csv_path,
                split=split,
                initial_soc=initial_soc,
                nominal_capacity_ah=capacity_ah,
                ambient_temp_k=ambient_temp_k,
                weight=weight,
                frame=frame,
                normalized_csv=normalized_csv,
            )
        )
    return cases, diagnostics


def _load_external_case_csv(*, case_id: str, csv_path: Path, default_temp_k: float, current_sign: str) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"Case CSV not found: {csv_path}")
    frame = pd.read_csv(csv_path)
    required = ["time_s", "current_a", "voltage_v"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{case_id}: missing required columns {missing}.")
    if frame.empty:
        raise ValueError(f"{case_id}: CSV is empty.")
    cleaned = frame.copy()
    if "temp_k" not in cleaned.columns:
        cleaned["temp_k"] = float(default_temp_k)
    for column in ["time_s", "current_a", "voltage_v", "temp_k"]:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    if cleaned[["time_s", "current_a", "voltage_v", "temp_k"]].isna().any().any():
        raise ValueError(f"{case_id}: required numeric columns contain NaN or non-numeric values.")
    cleaned = cleaned.sort_values("time_s").reset_index(drop=True)
    time_s = cleaned["time_s"].to_numpy(dtype=float)
    if np.any(np.diff(time_s) <= 0):
        raise ValueError(f"{case_id}: time_s must be strictly increasing with no duplicate samples.")
    cleaned["time_s"] = cleaned["time_s"] - float(cleaned["time_s"].iloc[0])
    if current_sign == "charge_positive":
        cleaned["current_a"] = -cleaned["current_a"]
    voltage = cleaned["voltage_v"].to_numpy(dtype=float)
    if np.any((voltage <= 0.0) | (voltage >= 6.0)):
        raise ValueError(f"{case_id}: voltage_v contains values outside the broad physical range (0, 6) V.")
    if np.any(cleaned["temp_k"].to_numpy(dtype=float) <= 0):
        raise ValueError(f"{case_id}: temp_k must be > 0 K.")
    if np.nanmax(np.abs(cleaned["current_a"].to_numpy(dtype=float))) <= 1e-12:
        raise ValueError(f"{case_id}: current_a is effectively zero for all rows.")
    return cleaned.loc[:, ["time_s", "current_a", "voltage_v", "temp_k"]]


def _estimate_soc(case: CaseData) -> np.ndarray:
    time_s = case.frame["time_s"].to_numpy(dtype=float)
    current_a = case.frame["current_a"].to_numpy(dtype=float)
    dt_s = np.diff(time_s, prepend=time_s[0])
    discharged_ah = np.cumsum(current_a * dt_s) / 3600.0
    return np.clip(float(case.initial_soc) - discharged_ah / float(case.nominal_capacity_ah), 0.0, 1.0)


def _weight_vector(current_a: np.ndarray, *, dynamic_weight: float, current_threshold_a: float) -> np.ndarray:
    dynamic = np.abs(current_a) > float(current_threshold_a)
    weights = np.where(dynamic, float(dynamic_weight), max(1.0 - float(dynamic_weight), 1e-6))
    return np.sqrt(weights)


def _fit_ecm(cases: list[CaseData], config: TuneConfig) -> dict[str, Any]:
    train_cases = [case for case in cases if case.split == "train"]
    if len(train_cases) < config.min_train_cases:
        raise ValueError(
            f"ECM fit requires at least {config.min_train_cases} valid train cases; got {len(train_cases)}."
        )

    diagnostics: list[dict[str, Any]] = []
    requested_order = int(config.ecm_order)
    tried_orders = [requested_order]
    if requested_order == 2 and config.ecm_fallback_to_1rc:
        tried_orders.append(1)

    last_error: str | None = None
    for order in tried_orders:
        try:
            fit_payload = _fit_ecm_order(train_cases, config, order=order)
            fit_payload["degraded"] = bool(order != requested_order)
            fit_payload["requested_ecm_order"] = requested_order
            if order != requested_order:
                diagnostics.append(
                    _diagnostic_row(
                        "ALL",
                        "warning",
                        "ecm",
                        f"Requested {requested_order}RC failed; degraded to {order}RC.",
                        "优先检查动态脉冲是否足够，或保留 1RC 作为低成本基线。",
                    )
                )
            fit_payload["diagnostics"] = diagnostics
            return fit_payload
        except Exception as exc:
            last_error = str(exc)
            diagnostics.append(
                _diagnostic_row(
                    "ALL",
                    "warning" if order != tried_orders[-1] else "error",
                    "ecm",
                    f"{order}RC fit failed: {exc}",
                    "检查 SOC 覆盖、电流激励、静置段长度；必要时降低 ecm_order 或放宽 max_nfev。",
                )
            )
    raise ValueError(last_error or "ECM fit failed.")


def _fit_ecm_order(cases: list[CaseData], config: TuneConfig, *, order: int) -> dict[str, Any]:
    dynamic_rows = sum(
        int(np.sum(np.abs(case.frame["current_a"].to_numpy(dtype=float)) > config.current_dynamic_threshold_a))
        for case in cases
    )
    if dynamic_rows < 3:
        raise ValueError("not enough dynamic current rows for ECM fitting.")

    all_voltage = np.concatenate([case.frame["voltage_v"].to_numpy(dtype=float) for case in cases])
    guess_ocv = float(np.nanmedian(all_voltage))
    if order == 1:
        x0 = np.array([guess_ocv, 1e-3, 2e-3, 2e4], dtype=float)
        lower = np.array([2.0, 1e-6, 1e-6, 1e2], dtype=float)
        upper = np.array([5.0, 5e-2, 2e-1, 2e6], dtype=float)
    else:
        x0 = np.array([guess_ocv, 1e-3, 2e-3, 2e4, 4e-3, 1e5], dtype=float)
        lower = np.array([2.0, 1e-6, 1e-6, 1e2, 1e-6, 1e2], dtype=float)
        upper = np.array([5.0, 5e-2, 2e-1, 2e6, 2e-1, 2e7], dtype=float)

    def residual(params: np.ndarray) -> np.ndarray:
        chunks: list[np.ndarray] = []
        for case in cases:
            time_s = case.frame["time_s"].to_numpy(dtype=float)
            current_a = case.frame["current_a"].to_numpy(dtype=float)
            voltage_v = case.frame["voltage_v"].to_numpy(dtype=float)
            if order == 1:
                pred = _simulate_thevenin_voltage(
                    time_s=time_s,
                    current_a=current_a,
                    ocv_v=float(params[0]),
                    r0_ohm=float(params[1]),
                    r1_ohm=float(params[2]),
                    c1_f=float(params[3]),
                )
            else:
                pred = _simulate_thevenin_voltage_2rc(
                    time_s=time_s,
                    current_a=current_a,
                    ocv_v=float(params[0]),
                    r0_ohm=float(params[1]),
                    r1_ohm=float(params[2]),
                    c1_f=float(params[3]),
                    r2_ohm=float(params[4]),
                    c2_f=float(params[5]),
                )
            weights = _weight_vector(
                current_a,
                dynamic_weight=config.loss_dynamic_weight,
                current_threshold_a=config.current_dynamic_threshold_a,
            )
            chunks.append((pred - voltage_v) * weights * math.sqrt(float(case.weight)))
        return np.concatenate(chunks)

    result = least_squares(
        residual,
        x0=x0,
        bounds=(lower, upper),
        method="trf",
        loss="soft_l1",
        f_scale=0.02,
        max_nfev=max(1, int(config.max_nfev)),
    )
    if not result.success:
        raise ValueError(result.message)

    params = [float(item) for item in result.x]
    metrics_rows = [_evaluate_ecm_case(case, params=params, order=order, config=config) for case in cases]
    train_residuals = np.concatenate([row["residual_v"] for row in metrics_rows])
    fit_metrics = {
        "case_count": int(len(cases)),
        "row_count": int(sum(len(case.frame) for case in cases)),
        "mean_mae_v": float(np.mean(np.abs(train_residuals))),
        "rmse_v": float(np.sqrt(np.mean(np.square(train_residuals)))),
        "max_abs_v": float(np.max(np.abs(train_residuals))),
        "nfev": int(result.nfev),
        "cost": float(result.cost),
    }
    return {
        "order": int(order),
        "params": params,
        "fit_metrics": fit_metrics,
        "case_metrics": [row["metrics"] for row in metrics_rows],
    }


def _evaluate_ecm_case(
    case: CaseData, *, params: list[float], order: int, config: TuneConfig
) -> dict[str, Any]:
    time_s = case.frame["time_s"].to_numpy(dtype=float)
    current_a = case.frame["current_a"].to_numpy(dtype=float)
    voltage_v = case.frame["voltage_v"].to_numpy(dtype=float)
    if order == 1:
        pred = _simulate_thevenin_voltage(time_s=time_s, current_a=current_a, ocv_v=params[0], r0_ohm=params[1], r1_ohm=params[2], c1_f=params[3])
    else:
        pred = _simulate_thevenin_voltage_2rc(
            time_s=time_s,
            current_a=current_a,
            ocv_v=params[0],
            r0_ohm=params[1],
            r1_ohm=params[2],
            c1_f=params[3],
            r2_ohm=params[4],
            c2_f=params[5],
        )
    residual = pred - voltage_v
    dynamic = np.abs(current_a) > config.current_dynamic_threshold_a
    metrics = {
        "case_id": case.case_id,
        "split": case.split,
        "mae_v": float(np.mean(np.abs(residual))),
        "rmse_v": float(np.sqrt(np.mean(np.square(residual)))),
        "mae_dynamic_v": float(np.mean(np.abs(residual[dynamic]))) if np.any(dynamic) else None,
        "mae_static_v": float(np.mean(np.abs(residual[~dynamic]))) if np.any(~dynamic) else None,
    }
    return {"metrics": metrics, "residual_v": residual, "predicted_v": pred}


def _write_ecm_artifacts(
    cases: list[CaseData],
    config: TuneConfig,
    ecm_result: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    order = int(ecm_result["order"])
    suffix = "_2rc" if order == 2 else ""
    fit_dir = config.output_dir / "ecm_fit"
    fit_dir.mkdir(parents=True, exist_ok=True)
    params = [float(item) for item in ecm_result["params"]]
    soc_axis = [float(item) for item in config.ecm_soc_grid]
    temp_axis = [float(item) for item in config.ecm_temp_axis_c]
    shape = (len(temp_axis), len(soc_axis))
    pack: dict[str, Any] = {
        "schema_version": _ECM_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "external_test_data",
        "source_manifest_csv": str(config.manifest_csv),
        "model": f"thevenin_{order}rc",
        "ecm_order": order,
        "fit_config": {
            "loss_dynamic_weight": float(config.loss_dynamic_weight),
            "loss_static_weight": float(1.0 - config.loss_dynamic_weight),
            "current_dynamic_threshold_a": float(config.current_dynamic_threshold_a),
            "requested_ecm_order": int(ecm_result["requested_ecm_order"]),
            "degraded": bool(ecm_result["degraded"]),
        },
        "soc_axis": soc_axis,
        "temp_c_axis": temp_axis,
        "ocv_v": [params[0] for _ in soc_axis],
        "r0_ohm_map": np.full(shape, params[1], dtype=float).tolist(),
        "r1_ohm_map": np.full(shape, params[2], dtype=float).tolist(),
        "c1_f_map": np.full(shape, params[3], dtype=float).tolist(),
        "fit_metrics": ecm_result["fit_metrics"],
    }
    if order == 2:
        pack["r2_ohm_map"] = np.full(shape, params[4], dtype=float).tolist()
        pack["c2_f_map"] = np.full(shape, params[5], dtype=float).tolist()
    pack_path = fit_dir / f"ecm_fitted_pack_temp_2d{suffix}.json"
    _write_json(pack_path, pack)

    points_rows = []
    for soc in soc_axis:
        row = {
            "soc_target": soc,
            "ecm_order": order,
            "ocv_v": params[0],
            "r0_ohm": params[1],
            "r1_ohm": params[2],
            "c1_f": params[3],
            "rmse_v": ecm_result["fit_metrics"]["rmse_v"],
            "mae_v": ecm_result["fit_metrics"]["mean_mae_v"],
        }
        if order == 2:
            row["r2_ohm"] = params[4]
            row["c2_f"] = params[5]
        points_rows.append(row)
    points_path = fit_dir / f"ecm_fit_points_temp_2d{suffix}.csv"
    pd.DataFrame(points_rows).to_csv(points_path, index=False)

    case_metric_path = fit_dir / "ecm_case_metrics.csv"
    pd.DataFrame(ecm_result["case_metrics"]).to_csv(case_metric_path, index=False)

    overlay_dir = fit_dir / "case_predictions"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        eval_case = _evaluate_ecm_case(case, params=params, order=order, config=config)
        frame = case.frame.copy()
        frame["ecm_voltage_v"] = eval_case["predicted_v"]
        frame["ecm_residual_v"] = frame["ecm_voltage_v"] - frame["voltage_v"]
        frame.to_csv(overlay_dir / f"{case.case_id}_observed_vs_ecm.csv", index=False)

    diagnostics.extend(ecm_result.get("diagnostics", []))
    return {
        "ecm_fitted_pack_json": str(pack_path),
        "ecm_fit_points_csv": str(points_path),
        "ecm_case_metrics_csv": str(case_metric_path),
        "ecm_case_predictions_dir": str(overlay_dir),
    }


def _fit_dfn_micro(cases: list[CaseData], config: TuneConfig, ecm_passed: bool) -> dict[str, Any]:
    if config.dfn_run_only_if_ecm_passed and not ecm_passed:
        return {
            "enabled": bool(config.dfn_enabled),
            "attempted": False,
            "passed": False,
            "stop_reason": "DFN micro-tune skipped because ECM fit did not pass.",
            "diagnostics": [
                _diagnostic_row(
                    "ALL",
                    "warning",
                    "dfn",
                    "DFN micro-tune skipped because ECM fit failed.",
                    "先修复 ECM 数据质量或参数窗口，再运行 DFN 微调。",
                )
            ],
        }
    train_cases = [case for case in cases if case.split == "train"]
    if not train_cases:
        return {
            "enabled": bool(config.dfn_enabled),
            "attempted": False,
            "passed": False,
            "stop_reason": "No train cases available for DFN micro-tune.",
            "diagnostics": [
                _diagnostic_row("ALL", "error", "dfn", "No train cases available.", "至少提供一个 split=train 的有效 case。")
            ],
        }

    base_cfg = load_config(config.base_config_path)
    x0 = np.array(
        [
            float(np.clip(np.median([case.initial_soc for case in train_cases]), *config.dfn_initial_soc_bounds)),
            1.0,
        ],
        dtype=float,
    )
    lower = np.array([config.dfn_initial_soc_bounds[0], config.dfn_capacity_scale_bounds[0]], dtype=float)
    upper = np.array([config.dfn_initial_soc_bounds[1], config.dfn_capacity_scale_bounds[1]], dtype=float)
    best: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []

    def evaluate(params: np.ndarray) -> np.ndarray:
        nonlocal best
        initial_soc = float(params[0])
        capacity_scale = float(params[1])
        residual_parts: list[np.ndarray] = []
        run_metrics: list[dict[str, Any]] = []
        for case in train_cases:
            try:
                trial_cfg = _replace_config_values(
                    base_cfg,
                    model_type="dfn",
                    thermal="isothermal",
                    initial_soc=initial_soc,
                    nominal_capacity_ah=float(case.nominal_capacity_ah * capacity_scale),
                    output_dir=config.output_dir / "_dfn_trial_scratch",
                )
                values, _ = _build_parameter_values(trial_cfg)
                profile = case.frame.loc[:, ["time_s", "current_a", "temp_k"]].copy()
                frame, runtime, warnings, error = simulate_from_timeseries(
                    trial_cfg,
                    values,
                    profile,
                    initial_soc=initial_soc,
                )
                if frame is None or error:
                    raise ValueError(error or "DFN simulation returned no frame.")
                pred = frame["voltage_v"].to_numpy(dtype=float)
                obs = case.frame["voltage_v"].to_numpy(dtype=float)[: pred.size]
                current = case.frame["current_a"].to_numpy(dtype=float)[: pred.size]
                offset = float(np.mean(obs - pred))
                residual = (pred + offset - obs) * _weight_vector(
                    current,
                    dynamic_weight=config.loss_dynamic_weight,
                    current_threshold_a=config.current_dynamic_threshold_a,
                )
                residual_parts.append(residual * math.sqrt(float(case.weight)))
                run_metrics.append(
                    {
                        "case_id": case.case_id,
                        "runtime_s": float(runtime),
                        "voltage_offset_v": offset,
                        "mae_v": float(np.mean(np.abs(pred + offset - obs))),
                        "warnings": warnings,
                    }
                )
            except Exception as exc:
                diagnostics.append(
                    _diagnostic_row(
                        case.case_id,
                        "warning",
                        "dfn",
                        f"DFN simulation failed: {exc}",
                        "缩短数据窗口、放宽求解器容差，或先只使用 ECM 拟合结果。",
                    )
                )
                residual_parts.append(np.full(case.frame.shape[0], 10.0, dtype=float))
        residual_vector = np.concatenate(residual_parts)
        mae = float(np.mean(np.abs(residual_vector)))
        if not best or mae < float(best.get("weighted_mae_v", float("inf"))):
            best = {
                "initial_soc": initial_soc,
                "capacity_scale": capacity_scale,
                "weighted_mae_v": mae,
                "case_metrics": run_metrics,
            }
        return residual_vector

    try:
        if config.dfn_max_nfev <= 1:
            residual = evaluate(x0)
            success = bool(np.all(np.isfinite(residual)))
            message = "single evaluation"
            nfev = 1
        else:
            result = least_squares(
                evaluate,
                x0=x0,
                bounds=(lower, upper),
                method="trf",
                loss="soft_l1",
                f_scale=0.02,
                max_nfev=int(config.dfn_max_nfev),
            )
            success = bool(result.success or np.isfinite(result.cost))
            message = str(result.message)
            nfev = int(result.nfev)
    except Exception as exc:
        return {
            "enabled": True,
            "attempted": True,
            "passed": False,
            "stop_reason": str(exc),
            "diagnostics": diagnostics
            + [
                _diagnostic_row(
                    "ALL",
                    "error",
                    "dfn",
                    f"DFN micro-tune failed: {exc}",
                    "保留 ECM 结果；减少 DFN 参数范围或降低数据长度后重试。",
                )
            ],
        }

    dfn_dir = config.output_dir / "dfn_micro_tune"
    dfn_dir.mkdir(parents=True, exist_ok=True)
    best_initial_soc = float(best.get("initial_soc", x0[0]))
    best_capacity_scale = float(best.get("capacity_scale", x0[1]))
    first_capacity = float(train_cases[0].nominal_capacity_ah)
    fitted_config = _config_to_yaml_dict(config.base_config_path)
    fitted_config["initial_soc"] = best_initial_soc
    fitted_config["nominal_capacity_ah"] = first_capacity * best_capacity_scale
    fitted_config["output_dir"] = str((config.output_dir / "dfn_replay").resolve())
    model_block = fitted_config.setdefault("model", {})
    if isinstance(model_block, dict):
        model_block["type"] = "dfn"
        model_block["thermal"] = "isothermal"
    fitted_config_path = dfn_dir / "dfn_fitted_config.yaml"
    _write_yaml(fitted_config_path, fitted_config)
    metrics_path = dfn_dir / "dfn_case_metrics.csv"
    pd.DataFrame(best.get("case_metrics", [])).to_csv(metrics_path, index=False)
    return {
        "enabled": True,
        "attempted": True,
        "passed": success,
        "stop_reason": None if success else message,
        "nfev": nfev,
        "initial_soc": best_initial_soc,
        "capacity_scale": best_capacity_scale,
        "weighted_mae_v": best.get("weighted_mae_v"),
        "fitted_config_yaml": str(fitted_config_path),
        "case_metrics_csv": str(metrics_path),
        "diagnostics": diagnostics,
        "note": "voltage_offset_v is used only for objective diagnostics and is not persisted as a DFN physical parameter.",
    }


def _replace_config_values(config: Any, **updates: Any) -> Any:
    from dataclasses import replace

    return replace(config, **updates)


def _config_to_yaml_dict(path: Path) -> dict[str, Any]:
    payload = _read_yaml(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Base config must be a mapping: {path}")
    return json.loads(json.dumps(payload))


def _write_diagnostics(output_dir: Path, diagnostics: list[dict[str, Any]]) -> Path:
    path = output_dir / "case_diagnostics.csv"
    columns = ["case_id", "severity", "stage", "message", "suggestion"]
    pd.DataFrame(diagnostics, columns=columns).to_csv(path, index=False)
    return path


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# 外部测试数据参数调优报告",
        "",
        "## 总结",
        f"- 通过：{summary.get('passed')}",
        f"- 停止原因：{summary.get('stop_reason')}",
        f"- 有效样本数：{summary.get('valid_case_count')}",
        "",
        "## ECM 结果",
        f"- 已启用：{summary.get('ecm', {}).get('enabled')}",
        f"- 已尝试：{summary.get('ecm', {}).get('attempted')}",
        f"- 通过：{summary.get('ecm', {}).get('passed')}",
        f"- 阶数：{summary.get('ecm', {}).get('ecm_order')}",
        f"- 降级：{summary.get('ecm', {}).get('degraded')}",
        "",
        "## DFN 微调结果",
        f"- 已启用：{summary.get('dfn', {}).get('enabled')}",
        f"- 已尝试：{summary.get('dfn', {}).get('attempted')}",
        f"- 通过：{summary.get('dfn', {}).get('passed')}",
        f"- 说明：{summary.get('dfn', {}).get('note')}",
        "",
        "## 失败提示",
    ]
    diagnostics = summary.get("diagnostics", [])
    if diagnostics:
        for item in diagnostics:
            lines.append(
                f"- [{item.get('severity')}/{item.get('stage')}] {item.get('case_id')}: "
                f"{item.get('message')} 建议：{item.get('suggestion')}"
            )
    else:
        lines.append("- 无。")
    lines.extend(
        [
            "",
            "## 模板要求",
            "- manifest 必需列：case_id,csv_path,split,initial_soc,nominal_capacity_ah,ambient_temp_k,weight",
            "- 测试 CSV 必需列：time_s,current_a,voltage_v",
            "- 测试 CSV 可选列：temp_k,soc,case_note",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_external_parameter_tune(
    config_path: str | Path,
    output_dir_override: str | Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = load_tune_config(config_path)
    if output_dir_override is not None:
        config = TuneConfig(**{**config.__dict__, "output_dir": Path(output_dir_override).resolve()})
    config.output_dir.mkdir(parents=True, exist_ok=True)

    diagnostics: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}
    cases, input_diagnostics = _load_cases(config)
    diagnostics.extend(input_diagnostics)
    train_cases = [case for case in cases if case.split == "train"]
    if len(train_cases) < config.min_train_cases:
        diagnostics.append(
            _diagnostic_row(
                "ALL",
                "error",
                "input",
                f"Valid train cases below min_train_cases ({len(train_cases)} < {config.min_train_cases}).",
                "补充 split=train 的有效数据，或降低 fit.min_train_cases。",
            )
        )

    ecm_summary: dict[str, Any] = {"enabled": bool(config.ecm_enabled), "attempted": False, "passed": False}
    dfn_summary: dict[str, Any] = {"enabled": bool(config.dfn_enabled), "attempted": False, "passed": False}
    stop_reason: str | None = None

    if config.ecm_enabled and len(train_cases) >= config.min_train_cases:
        ecm_summary["attempted"] = True
        try:
            ecm_result = _fit_ecm(cases, config)
            ecm_artifacts = _write_ecm_artifacts(cases, config, ecm_result, diagnostics)
            artifacts.update(ecm_artifacts)
            ecm_summary.update(
                {
                    "passed": True,
                    "ecm_order": int(ecm_result["order"]),
                    "requested_ecm_order": int(ecm_result["requested_ecm_order"]),
                    "degraded": bool(ecm_result["degraded"]),
                    "fit_metrics": ecm_result["fit_metrics"],
                }
            )
        except Exception as exc:
            stop_reason = f"ECM fit failed: {exc}"
            diagnostics.append(
                _diagnostic_row(
                    "ALL",
                    "error",
                    "ecm",
                    str(exc),
                    "先检查数据质量、电流方向和 SOC 覆盖；ECM 未通过时默认不继续 DFN 微调。",
                )
            )

    if config.dfn_enabled:
        dfn_summary = _fit_dfn_micro(cases, config, ecm_passed=bool(ecm_summary.get("passed")))
        artifacts.update(
            {
                key: value
                for key, value in {
                    "dfn_fitted_config_yaml": dfn_summary.get("fitted_config_yaml"),
                    "dfn_case_metrics_csv": dfn_summary.get("case_metrics_csv"),
                }.items()
                if value
            }
        )
        diagnostics.extend(dfn_summary.get("diagnostics", []))

    diagnostics_path = _write_diagnostics(config.output_dir, diagnostics)
    artifacts["case_diagnostics_csv"] = str(diagnostics_path)

    passed = bool(ecm_summary.get("passed")) and (not config.dfn_enabled or bool(dfn_summary.get("passed")))
    if stop_reason is None and not passed:
        stop_reason = "One or more enabled fit stages did not pass."
    if passed:
        stop_reason = None

    summary = {
        "schema_version": "external_parameter_tune_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "stop_reason": stop_reason,
        "runtime_s": float(time.perf_counter() - started),
        "config_path": str(config.config_path),
        "base_config_path": str(config.base_config_path),
        "manifest_csv": str(config.manifest_csv),
        "valid_case_count": int(len(cases)),
        "train_case_count": int(len(train_cases)),
        "validation_case_count": int(len([case for case in cases if case.split == "validation"])),
        "thermal": {
            "enabled": bool(config.thermal_enabled),
            "reserved_only": True,
            "message": "Thermal coupled tuning is intentionally not active in this runner version.",
        },
        "ecm": ecm_summary,
        "dfn": dfn_summary,
        "artifacts": artifacts,
        "diagnostics": diagnostics,
        "template_requirements": {
            "manifest_required_columns": [
                "case_id",
                "csv_path",
                "split",
                "initial_soc",
                "nominal_capacity_ah",
                "ambient_temp_k",
                "weight",
            ],
            "case_required_columns": ["time_s", "current_a", "voltage_v"],
            "case_optional_columns": ["temp_k", "soc", "case_note"],
        },
    }
    summary_path = config.output_dir / "external_fit_summary.json"
    report_path = config.output_dir / "fit_acceptance_report.md"
    _write_json(summary_path, summary)
    _write_report(report_path, summary)
    summary["artifacts"]["external_fit_summary_json"] = str(summary_path)
    summary["artifacts"]["fit_acceptance_report_md"] = str(report_path)
    _write_json(summary_path, summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tune ECM first, then DFN micro parameters from external test data.")
    parser.add_argument("--config", required=True, help="Path to external parameter tune YAML config.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_external_parameter_tune(args.config, output_dir_override=args.output_dir)
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
