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

from batt_bamm.hppc_compare import run_compare_pipeline
from batt_bamm.main import run_from_config

_CURRENT_DYNAMIC_THRESHOLD_A = 1e-6
_DEFAULT_FIT_TEMPERATURE_GRID_C = [-10.0, 25.0, 45.0]
_GATE_PROFILE_TARGET = {
    "mae_static_mv": 5.0,
    "mae_dynamic_mv": 20.0,
    "p95_dynamic_mv": 30.0,
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _to_path(value: Any, *, base_dir: Path) -> Path | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    path = Path(token)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _safe_float(value: Any) -> float | None:
    try:
        data = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(data):
        return None
    return data


def _nan_agg(values: np.ndarray, reducer: str) -> float | None:
    if values.size == 0:
        return None
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    if reducer == "mean":
        return float(np.mean(finite))
    if reducer == "max":
        return float(np.max(finite))
    raise ValueError(f"Unsupported reducer: {reducer}")


def _build_weight_vector(current_a: np.ndarray, dynamic_weight: float, current_threshold_a: float) -> np.ndarray:
    weight_dynamic = float(dynamic_weight)
    if not 0.0 <= weight_dynamic <= 1.0:
        raise ValueError("loss_dynamic_weight must be within [0, 1].")
    weight_static = 1.0 - weight_dynamic
    dynamic_mask = np.abs(current_a) > float(current_threshold_a)
    weights = np.where(dynamic_mask, weight_dynamic, weight_static).astype(float)
    if np.all(weights <= 0):
        weights = np.ones_like(weights, dtype=float)
    return np.sqrt(weights)


def _simulate_thevenin_voltage(
    *,
    time_s: np.ndarray,
    current_a: np.ndarray,
    ocv_v: float,
    r0_ohm: float,
    r1_ohm: float,
    c1_f: float,
) -> np.ndarray:
    if time_s.ndim != 1 or current_a.ndim != 1 or time_s.size != current_a.size or time_s.size < 2:
        raise ValueError("time/current arrays must be 1-D with equal length >= 2.")
    if np.any(np.diff(time_s) <= 0):
        raise ValueError("time_s must be strictly increasing for Thevenin simulation.")
    if r0_ohm <= 0 or r1_ohm <= 0 or c1_f <= 0:
        raise ValueError("r0/r1/c1 must be positive.")

    v1 = np.zeros_like(time_s, dtype=float)
    tau = float(r1_ohm * c1_f)
    for idx in range(1, time_s.size):
        dt = float(time_s[idx] - time_s[idx - 1])
        v1[idx] = v1[idx - 1] + dt * ((-v1[idx - 1] / tau) + (float(current_a[idx - 1]) / c1_f))
    return float(ocv_v) - (current_a * float(r0_ohm)) - v1


def _simulate_thevenin_voltage_2rc(
    *,
    time_s: np.ndarray,
    current_a: np.ndarray,
    ocv_v: float,
    r0_ohm: float,
    r1_ohm: float,
    c1_f: float,
    r2_ohm: float,
    c2_f: float,
) -> np.ndarray:
    if time_s.ndim != 1 or current_a.ndim != 1 or time_s.size != current_a.size or time_s.size < 2:
        raise ValueError("time/current arrays must be 1-D with equal length >= 2.")
    if np.any(np.diff(time_s) <= 0):
        raise ValueError("time_s must be strictly increasing for Thevenin simulation.")
    if r0_ohm <= 0 or r1_ohm <= 0 or c1_f <= 0 or r2_ohm <= 0 or c2_f <= 0:
        raise ValueError("r0/r1/c1/r2/c2 must be positive.")

    v1 = np.zeros_like(time_s, dtype=float)
    v2 = np.zeros_like(time_s, dtype=float)
    tau1 = float(r1_ohm * c1_f)
    tau2 = float(r2_ohm * c2_f)
    for idx in range(1, time_s.size):
        dt = float(time_s[idx] - time_s[idx - 1])
        current_prev = float(current_a[idx - 1])
        v1[idx] = v1[idx - 1] + dt * ((-v1[idx - 1] / tau1) + (current_prev / c1_f))
        v2[idx] = v2[idx - 1] + dt * ((-v2[idx - 1] / tau2) + (current_prev / c2_f))
    return float(ocv_v) - (current_a * float(r0_ohm)) - v1 - v2


def _extract_discharge_window(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"time_s", "current_a", "voltage_v", "soc"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"HPPC point frame missing columns: {missing}")
    if frame.empty:
        raise ValueError("HPPC point frame is empty.")

    data = frame.copy()
    tol = 1e-6
    discharge_idx = np.where(data["current_a"].to_numpy(dtype=float) > tol)[0]
    if discharge_idx.size == 0:
        raise ValueError("No positive-current discharge pulse found for fitting.")
    discharge_start = int(discharge_idx[0])
    discharge_end = int(discharge_idx[-1])

    charge_idx = np.where(data["current_a"].to_numpy(dtype=float) < -tol)[0]
    after_discharge_charge = charge_idx[charge_idx > discharge_end]
    if after_discharge_charge.size > 0:
        window_end = int(after_discharge_charge[0] - 1)
    else:
        window_end = int(len(data) - 1)
    if window_end <= discharge_end:
        raise ValueError("No rest segment detected after discharge pulse for fitting window.")
    return data.iloc[discharge_start : window_end + 1].reset_index(drop=True)


def _fit_thevenin_single_window(
    window: pd.DataFrame,
    soc_target: float,
    *,
    ecm_order: int,
    loss_dynamic_weight: float,
    current_threshold_a: float,
) -> dict[str, float]:
    time_s = window["time_s"].to_numpy(dtype=float)
    current_a = window["current_a"].to_numpy(dtype=float)
    voltage_v = window["voltage_v"].to_numpy(dtype=float)
    if time_s.size < 3:
        raise ValueError(f"SOC {soc_target:.3f}: fit window too short.")
    time_local = time_s - float(time_s[0])

    guess_ocv = float(np.nanmedian(voltage_v))
    if not np.isfinite(guess_ocv):
        raise ValueError(f"SOC {soc_target:.3f}: invalid voltage in fit window.")
    if ecm_order == 1:
        x0 = np.array([guess_ocv, 1e-3, 2e-3, 2e4], dtype=float)
        lower = np.array([2.0, 1e-6, 1e-6, 1e2], dtype=float)
        upper = np.array([5.0, 5e-2, 2e-1, 2e6], dtype=float)
    elif ecm_order == 2:
        x0 = np.array([guess_ocv, 1e-3, 2e-3, 2e4, 4e-3, 1e5], dtype=float)
        lower = np.array([2.0, 1e-6, 1e-6, 1e2, 1e-6, 1e2], dtype=float)
        upper = np.array([5.0, 5e-2, 2e-1, 2e6, 2e-1, 2e7], dtype=float)
    else:
        raise ValueError(f"Unsupported ecm_order={ecm_order}.")
    weighted_root = _build_weight_vector(current_a, loss_dynamic_weight, current_threshold_a)

    def residual(params: np.ndarray) -> np.ndarray:
        if ecm_order == 1:
            ocv_v, r0_ohm, r1_ohm, c1_f = params
            pred = _simulate_thevenin_voltage(
                time_s=time_local,
                current_a=current_a,
                ocv_v=float(ocv_v),
                r0_ohm=float(r0_ohm),
                r1_ohm=float(r1_ohm),
                c1_f=float(c1_f),
            )
        else:
            ocv_v, r0_ohm, r1_ohm, c1_f, r2_ohm, c2_f = params
            pred = _simulate_thevenin_voltage_2rc(
                time_s=time_local,
                current_a=current_a,
                ocv_v=float(ocv_v),
                r0_ohm=float(r0_ohm),
                r1_ohm=float(r1_ohm),
                c1_f=float(c1_f),
                r2_ohm=float(r2_ohm),
                c2_f=float(c2_f),
            )
        return (pred - voltage_v) * weighted_root

    result = least_squares(
        residual,
        x0=x0,
        bounds=(lower, upper),
        method="trf",
        loss="soft_l1",
        f_scale=0.02,
        max_nfev=400,
    )
    if not result.success:
        raise ValueError(f"SOC {soc_target:.3f}: least_squares failed - {result.message}")

    parameters = [float(item) for item in result.x]
    if ecm_order == 1:
        ocv_v, r0_ohm, r1_ohm, c1_f = parameters
        r2_ohm = None
        c2_f = None
        predicted = _simulate_thevenin_voltage(
            time_s=time_local,
            current_a=current_a,
            ocv_v=ocv_v,
            r0_ohm=r0_ohm,
            r1_ohm=r1_ohm,
            c1_f=c1_f,
        )
    else:
        ocv_v, r0_ohm, r1_ohm, c1_f, r2_ohm, c2_f = parameters
        predicted = _simulate_thevenin_voltage_2rc(
            time_s=time_local,
            current_a=current_a,
            ocv_v=ocv_v,
            r0_ohm=r0_ohm,
            r1_ohm=r1_ohm,
            c1_f=c1_f,
            r2_ohm=float(r2_ohm),
            c2_f=float(c2_f),
        )
    residual_v = predicted - voltage_v
    rmse_v = float(np.sqrt(np.mean(np.square(residual_v))))
    mae_v = float(np.mean(np.abs(residual_v)))
    dynamic_mask = np.abs(current_a) > float(current_threshold_a)
    static_mask = ~dynamic_mask
    abs_residual = np.abs(residual_v)
    mae_dynamic_v = float(np.mean(abs_residual[dynamic_mask])) if np.any(dynamic_mask) else None
    mae_static_v = float(np.mean(abs_residual[static_mask])) if np.any(static_mask) else None
    p95_dynamic_v = float(np.percentile(abs_residual[dynamic_mask], 95.0)) if np.any(dynamic_mask) else None
    max_dynamic_v = float(np.max(abs_residual[dynamic_mask])) if np.any(dynamic_mask) else None
    return {
        "soc_target": float(soc_target),
        "ecm_order": int(ecm_order),
        "ocv_v": ocv_v,
        "r0_ohm": r0_ohm,
        "r1_ohm": r1_ohm,
        "c1_f": c1_f,
        "r2_ohm": r2_ohm,
        "c2_f": c2_f,
        "rmse_v": rmse_v,
        "mae_v": mae_v,
        "mae_static_v": mae_static_v,
        "mae_dynamic_v": mae_dynamic_v,
        "p95_dynamic_v": p95_dynamic_v,
        "max_dynamic_v": max_dynamic_v,
        "dynamic_rows": int(np.sum(dynamic_mask)),
        "static_rows": int(np.sum(static_mask)),
        "window_rows": int(window.shape[0]),
    }


def fit_ecm_parameters_from_dfn_hppc(
    *,
    dfn_hppc_summary_csv: Path | None = None,
    dfn_hppc_summary_csv_by_temp_c: dict[float, Path] | None = None,
    output_dir: Path,
    source_config_path: Path | None = None,
    ecm_order: int = 1,
    loss_dynamic_weight: float = 0.7,
    current_threshold_a: float = _CURRENT_DYNAMIC_THRESHOLD_A,
) -> dict[str, Any]:
    if ecm_order not in (1, 2):
        raise ValueError("ecm_order must be 1 or 2.")
    output_dir.mkdir(parents=True, exist_ok=True)

    if dfn_hppc_summary_csv_by_temp_c is None:
        if dfn_hppc_summary_csv is None:
            raise ValueError("Either dfn_hppc_summary_csv or dfn_hppc_summary_csv_by_temp_c must be provided.")
        dfn_hppc_summary_csv_by_temp_c = {25.0: Path(dfn_hppc_summary_csv)}
    if len(dfn_hppc_summary_csv_by_temp_c) < 2:
        raise ValueError("At least two temperatures are required for SOC×temperature ECM fitting.")

    datasets: dict[float, Path] = {}
    for temp_c_raw, summary_csv_raw in dfn_hppc_summary_csv_by_temp_c.items():
        temp_c = float(temp_c_raw)
        if not np.isfinite(temp_c):
            raise ValueError("Fit temperature grid contains non-finite value.")
        summary_csv = Path(summary_csv_raw)
        if not summary_csv.exists():
            raise FileNotFoundError(f"DFN HPPC summary CSV not found for {temp_c}°C: {summary_csv}")
        datasets[temp_c] = summary_csv.resolve()

    fitted_points: list[dict[str, Any]] = []
    required = {"soc_target", "passed", "csv_path"}
    for temp_c in sorted(datasets.keys()):
        summary_csv = datasets[temp_c]
        summary = pd.read_csv(summary_csv)
        missing = sorted(required - set(summary.columns))
        if missing:
            raise ValueError(f"DFN HPPC summary missing required columns at {temp_c}°C: {missing}")

        rows = summary.copy()
        rows["passed"] = rows["passed"].map(_as_bool)
        rows = rows.loc[rows["passed"]].copy()
        if rows.empty:
            raise ValueError(f"DFN HPPC summary has no passed rows at {temp_c}°C.")
        rows["soc_target"] = pd.to_numeric(rows["soc_target"], errors="coerce")
        rows = rows.dropna(subset=["soc_target"])
        rows = rows.sort_values("soc_target", ascending=False).reset_index(drop=True)

        for item in rows.to_dict(orient="records"):
            csv_path = _to_path(item.get("csv_path"), base_dir=summary_csv.parent)
            if csv_path is None or not csv_path.exists():
                raise FileNotFoundError(f"HPPC point csv_path is invalid at {temp_c}°C: {item.get('csv_path')}")
            point_frame = pd.read_csv(csv_path)
            fit_window = _extract_discharge_window(point_frame)
            soc_target = float(item["soc_target"])
            point_fit = _fit_thevenin_single_window(
                fit_window,
                soc_target=soc_target,
                ecm_order=ecm_order,
                loss_dynamic_weight=loss_dynamic_weight,
                current_threshold_a=current_threshold_a,
            )
            point_fit["csv_path"] = str(csv_path)
            point_fit["temp_c"] = float(temp_c)
            fitted_points.append(point_fit)

    fit_points = pd.DataFrame(fitted_points).sort_values(["temp_c", "soc_target"], ascending=[True, False]).reset_index(drop=True)
    suffix = "_2rc" if ecm_order == 2 else ""
    fit_points_path = output_dir / f"ecm_fit_points_temp_2d{suffix}.csv"
    fit_points.to_csv(fit_points_path, index=False)

    temp_axis = sorted(float(value) for value in fit_points["temp_c"].dropna().unique().tolist())
    soc_sets = []
    for temp_c in temp_axis:
        group = fit_points.loc[np.isclose(fit_points["temp_c"], temp_c, atol=1e-10), "soc_target"]
        tokens = {round(float(v), 10) for v in group.to_numpy(dtype=float)}
        soc_sets.append(tokens)
    common_soc_tokens = set.intersection(*soc_sets) if soc_sets else set()
    if not common_soc_tokens:
        raise ValueError("No common SOC grid across temperature datasets for 2-D ECM fit pack.")
    soc_axis = sorted(float(v) for v in common_soc_tokens)

    aligned_rows: list[dict[str, Any]] = []
    for temp_c in temp_axis:
        rows_temp = fit_points.loc[np.isclose(fit_points["temp_c"], temp_c, atol=1e-10)].copy()
        rows_temp["_soc_token"] = rows_temp["soc_target"].round(10)
        rows_temp = rows_temp.loc[rows_temp["_soc_token"].isin(common_soc_tokens)].copy()
        rows_temp = rows_temp.sort_values("_soc_token", ascending=True).drop_duplicates(subset=["_soc_token"], keep="first")
        if len(rows_temp) != len(soc_axis):
            raise ValueError(f"SOC alignment failed at {temp_c}°C for 2-D ECM fit pack.")
        aligned_rows.extend(rows_temp.to_dict(orient="records"))
    aligned = pd.DataFrame(aligned_rows).sort_values(["temp_c", "soc_target"]).reset_index(drop=True)

    def _build_map(column: str) -> np.ndarray:
        matrix = np.empty((len(temp_axis), len(soc_axis)), dtype=float)
        for i, temp_c in enumerate(temp_axis):
            group = aligned.loc[np.isclose(aligned["temp_c"], temp_c, atol=1e-10)].copy()
            group = group.sort_values("soc_target", ascending=True).reset_index(drop=True)
            values = group[column].to_numpy(dtype=float)
            if values.size != len(soc_axis):
                raise ValueError(f"Map build failed for {column} at {temp_c}°C.")
            matrix[i, :] = values
        return matrix

    r0_map = _build_map("r0_ohm")
    r1_map = _build_map("r1_ohm")
    c1_map = _build_map("c1_f")
    if ecm_order == 2:
        r2_map = _build_map("r2_ohm")
        c2_map = _build_map("c2_f")

    reference_temp_for_ocv = min(temp_axis, key=lambda value: abs(value - 25.0))
    ref_group = aligned.loc[np.isclose(aligned["temp_c"], reference_temp_for_ocv, atol=1e-10)].copy()
    ref_group = ref_group.sort_values("soc_target", ascending=True).reset_index(drop=True)
    ocv_v = ref_group["ocv_v"].to_numpy(dtype=float)

    pack_payload: dict[str, Any] = {
        "schema_version": "ecm_temp_2d_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_hppc_summary_csv_by_temp_c": {str(temp_c): str(path) for temp_c, path in datasets.items()},
        "source_config_path": None if source_config_path is None else str(source_config_path.resolve()),
        "model": f"thevenin_{ecm_order}rc",
        "ecm_order": int(ecm_order),
        "fit_config": {
            "loss_dynamic_weight": float(loss_dynamic_weight),
            "loss_static_weight": float(1.0 - loss_dynamic_weight),
            "current_dynamic_threshold_a": float(current_threshold_a),
            "fit_temperature_grid_c": [float(temp) for temp in temp_axis],
        },
        "soc_axis": [float(v) for v in soc_axis],
        "temp_c_axis": [float(v) for v in temp_axis],
        "ocv_v": [float(v) for v in ocv_v],
        "r0_ohm_map": r0_map.tolist(),
        "r1_ohm_map": r1_map.tolist(),
        "c1_f_map": c1_map.tolist(),
        "fit_metrics": {
            "point_count": int(len(aligned)),
            "temperature_count": int(len(temp_axis)),
            "soc_count": int(len(soc_axis)),
            "mean_rmse_v": float(np.mean(aligned["rmse_v"].to_numpy(dtype=float))),
            "max_rmse_v": float(np.max(aligned["rmse_v"].to_numpy(dtype=float))),
            "mean_mae_v": float(np.mean(aligned["mae_v"].to_numpy(dtype=float))),
            "max_mae_v": float(np.max(aligned["mae_v"].to_numpy(dtype=float))),
            "mean_mae_static_v": _nan_agg(aligned["mae_static_v"].to_numpy(dtype=float), "mean"),
            "mean_mae_dynamic_v": _nan_agg(aligned["mae_dynamic_v"].to_numpy(dtype=float), "mean"),
            "max_p95_dynamic_v": _nan_agg(aligned["p95_dynamic_v"].to_numpy(dtype=float), "max"),
        },
    }
    if ecm_order == 2:
        pack_payload["r2_ohm_map"] = r2_map.tolist()
        pack_payload["c2_f_map"] = c2_map.tolist()
    pack_path = output_dir / f"ecm_fitted_pack_temp_2d{suffix}.json"
    pack_path.write_text(json.dumps(pack_payload, indent=2), encoding="utf-8")

    return {
        "fit_points_csv": str(fit_points_path),
        "fitted_pack_json": str(pack_path),
        "fit_metrics": pack_payload["fit_metrics"],
        "fit_temperature_grid_c": [float(value) for value in temp_axis],
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Config must be a YAML mapping: {path}")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _normalize_fit_temperature_grid(fit_temperature_grid_c: list[float] | None) -> list[float]:
    source = _DEFAULT_FIT_TEMPERATURE_GRID_C if fit_temperature_grid_c is None else fit_temperature_grid_c
    if not source:
        raise ValueError("fit_temperature_grid_c must not be empty.")
    normalized = []
    for temp in source:
        value = float(temp)
        if not np.isfinite(value):
            raise ValueError("fit_temperature_grid_c contains non-finite value.")
        normalized.append(value)
    unique_sorted = sorted({round(value, 10) for value in normalized})
    if len(unique_sorted) < 2:
        raise ValueError("fit_temperature_grid_c must contain at least two distinct temperatures.")
    return [float(v) for v in unique_sorted]


def _temp_tag(temp_c: float) -> str:
    token = f"{temp_c:.2f}".replace(".", "p").replace("-", "m")
    return f"{token}c"


def _run_dfn_hppc_for_fit_temperature(
    *,
    dfn_config_path: Path,
    output_dir: Path,
    fit_temperature_grid_c: list[float],
) -> dict[float, Path]:
    fit_inputs_dir = output_dir / "_generated_configs" / "fit_inputs"
    fit_inputs_dir.mkdir(parents=True, exist_ok=True)
    dfn_template = _read_yaml(dfn_config_path)
    if not isinstance(dfn_template.get("model", {}), dict) or str(dfn_template["model"].get("type", "dfn")).strip().lower() != "dfn":
        raise ValueError("dfn-config must contain model.type=dfn.")

    summary_by_temp: dict[float, Path] = {}
    for temp_c in fit_temperature_grid_c:
        temp_k = float(temp_c + 273.15)
        cfg = json.loads(json.dumps(dfn_template))
        cfg["ambient_temp_k"] = temp_k
        cfg["initial_cell_temp_k"] = temp_k
        cfg["output_dir"] = str((output_dir / "fit_inputs" / f"dfn_hppc_{_temp_tag(temp_c)}").resolve())
        cfg_path = fit_inputs_dir / f"dfn_hppc_{_temp_tag(temp_c)}.yaml"
        _write_yaml(cfg_path, cfg)
        run = run_from_config(config_path=cfg_path, mode="hppc")
        if not bool(run.get("all_converged", False)):
            stop_reason = str(run.get("hppc", {}).get("stop_reason") or "unknown")
            raise ValueError(f"DFN HPPC fit input failed at {temp_c}°C: {stop_reason}")
        summary_csv = _to_path(run.get("artifacts", {}).get("hppc_summary_csv"), base_dir=cfg_path.parent)
        if summary_csv is None or not summary_csv.exists():
            raise ValueError(f"Missing DFN hppc_summary_csv artifact at {temp_c}°C.")
        summary_by_temp[float(temp_c)] = summary_csv.resolve()
    return summary_by_temp


def _prepare_compare_configs(
    *,
    dfn_config_path: Path,
    ecm_config_path: Path,
    output_dir: Path,
    run_tag: str,
    fitted_pack_json: Path | None,
    ecm_order: int | None,
) -> tuple[Path, Path]:
    run_dir = output_dir / "_generated_configs" / run_tag
    dfn_cfg = _read_yaml(dfn_config_path)
    ecm_cfg = _read_yaml(ecm_config_path)

    dfn_cfg["output_dir"] = str((output_dir / run_tag / "dfn").resolve())
    ecm_cfg["output_dir"] = str((output_dir / run_tag / "ecm").resolve())
    model_block = ecm_cfg.setdefault("model", {})
    if not isinstance(model_block, dict):
        raise ValueError("ECM config 'model' block must be a mapping.")
    if ecm_order is not None:
        model_block["ecm_rc_elements"] = int(ecm_order)
    if fitted_pack_json is not None:
        model_block["ecm_fitted_pack_json"] = str(fitted_pack_json.resolve())
    else:
        model_block.pop("ecm_fitted_pack_json", None)

    dfn_generated = run_dir / "dfn.yaml"
    ecm_generated = run_dir / "ecm.yaml"
    _write_yaml(dfn_generated, dfn_cfg)
    _write_yaml(ecm_generated, ecm_cfg)
    return dfn_generated, ecm_generated


def _extract_mae(summary: dict[str, Any]) -> float | None:
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict):
        return None
    return _safe_float(metrics.get("mae_v_dis_end_v"))


def _as_passed_flag(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series
    if pd.api.types.is_numeric_dtype(series):
        return series != 0
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def _to_abs_path(value: Any, *, base_dir: Path) -> Path | None:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _collect_voltage_delta_series(
    *,
    dfn_hppc_summary_csv: Path | None,
    ecm_hppc_summary_csv: Path | None,
    current_threshold_a: float,
) -> dict[str, Any]:
    if dfn_hppc_summary_csv is None or ecm_hppc_summary_csv is None:
        return {
            "mae_static_v": None,
            "mae_dynamic_v": None,
            "p95_dynamic_v": None,
            "max_dynamic_v": None,
            "points_used": 0,
            "samples_total": 0,
            "samples_dynamic": 0,
            "samples_static": 0,
            "error": "Missing hppc_summary_csv path.",
        }

    try:
        dfn_summary = pd.read_csv(dfn_hppc_summary_csv)
        ecm_summary = pd.read_csv(ecm_hppc_summary_csv)
    except Exception as exc:
        return {
            "mae_static_v": None,
            "mae_dynamic_v": None,
            "p95_dynamic_v": None,
            "max_dynamic_v": None,
            "points_used": 0,
            "samples_total": 0,
            "samples_dynamic": 0,
            "samples_static": 0,
            "error": f"Failed to read hppc summary CSV: {exc}",
        }

    required = {"soc_target", "passed", "csv_path"}
    missing_dfn = sorted(required - set(dfn_summary.columns))
    missing_ecm = sorted(required - set(ecm_summary.columns))
    if missing_dfn or missing_ecm:
        return {
            "mae_static_v": None,
            "mae_dynamic_v": None,
            "p95_dynamic_v": None,
            "max_dynamic_v": None,
            "points_used": 0,
            "samples_total": 0,
            "samples_dynamic": 0,
            "samples_static": 0,
            "error": f"Missing required columns. dfn={missing_dfn}, ecm={missing_ecm}",
        }

    dfn_summary = dfn_summary.copy()
    ecm_summary = ecm_summary.copy()
    dfn_summary["passed"] = _as_passed_flag(dfn_summary["passed"])
    ecm_summary["passed"] = _as_passed_flag(ecm_summary["passed"])
    dfn_summary["soc_target"] = pd.to_numeric(dfn_summary["soc_target"], errors="coerce")
    ecm_summary["soc_target"] = pd.to_numeric(ecm_summary["soc_target"], errors="coerce")
    dfn_summary = dfn_summary.loc[dfn_summary["passed"]].dropna(subset=["soc_target"])
    ecm_summary = ecm_summary.loc[ecm_summary["passed"]].dropna(subset=["soc_target"])
    merged = dfn_summary.merge(ecm_summary, on="soc_target", suffixes=("_dfn", "_ecm"), how="inner")
    merged = merged.sort_values("soc_target", ascending=False).reset_index(drop=True)
    if merged.empty:
        return {
            "mae_static_v": None,
            "mae_dynamic_v": None,
            "p95_dynamic_v": None,
            "max_dynamic_v": None,
            "points_used": 0,
            "samples_total": 0,
            "samples_dynamic": 0,
            "samples_static": 0,
            "error": "No aligned passed SOC points.",
        }

    abs_delta_dynamic_parts: list[np.ndarray] = []
    abs_delta_static_parts: list[np.ndarray] = []
    total_samples = 0
    points_used = 0
    for row in merged.to_dict(orient="records"):
        dfn_csv_path = _to_abs_path(row.get("csv_path_dfn"), base_dir=dfn_hppc_summary_csv.parent)
        ecm_csv_path = _to_abs_path(row.get("csv_path_ecm"), base_dir=ecm_hppc_summary_csv.parent)
        if dfn_csv_path is None or ecm_csv_path is None or not dfn_csv_path.exists() or not ecm_csv_path.exists():
            continue
        dfn_point = pd.read_csv(dfn_csv_path)
        ecm_point = pd.read_csv(ecm_csv_path)
        if "voltage_v" not in dfn_point.columns or "voltage_v" not in ecm_point.columns:
            continue
        if "current_a" not in dfn_point.columns:
            continue
        n = min(len(dfn_point), len(ecm_point))
        if n <= 1:
            continue
        dfn_v = pd.to_numeric(dfn_point["voltage_v"].iloc[:n], errors="coerce").to_numpy(dtype=float)
        ecm_v = pd.to_numeric(ecm_point["voltage_v"].iloc[:n], errors="coerce").to_numpy(dtype=float)
        current = pd.to_numeric(dfn_point["current_a"].iloc[:n], errors="coerce").to_numpy(dtype=float)
        finite_mask = np.isfinite(dfn_v) & np.isfinite(ecm_v) & np.isfinite(current)
        if not np.any(finite_mask):
            continue
        delta = np.abs(dfn_v[finite_mask] - ecm_v[finite_mask])
        dynamic_mask = np.abs(current[finite_mask]) > float(current_threshold_a)
        if np.any(dynamic_mask):
            abs_delta_dynamic_parts.append(delta[dynamic_mask])
        if np.any(~dynamic_mask):
            abs_delta_static_parts.append(delta[~dynamic_mask])
        total_samples += int(delta.size)
        points_used += 1

    abs_dynamic = np.concatenate(abs_delta_dynamic_parts) if abs_delta_dynamic_parts else np.array([], dtype=float)
    abs_static = np.concatenate(abs_delta_static_parts) if abs_delta_static_parts else np.array([], dtype=float)
    return {
        "mae_static_v": None if abs_static.size == 0 else float(np.mean(abs_static)),
        "mae_dynamic_v": None if abs_dynamic.size == 0 else float(np.mean(abs_dynamic)),
        "p95_dynamic_v": None if abs_dynamic.size == 0 else float(np.percentile(abs_dynamic, 95.0)),
        "max_dynamic_v": None if abs_dynamic.size == 0 else float(np.max(abs_dynamic)),
        "points_used": int(points_used),
        "samples_total": int(total_samples),
        "samples_dynamic": int(abs_dynamic.size),
        "samples_static": int(abs_static.size),
    }


def _resolve_gate_thresholds(profile: str) -> dict[str, float] | None:
    token = profile.strip().lower()
    if token == "off":
        return None
    if token == "target":
        return dict(_GATE_PROFILE_TARGET)
    raise ValueError("gate_profile must be one of: off, target")


def _evaluate_2rc_gate(
    metrics_static_dynamic: dict[str, Any], thresholds_mv: dict[str, float] | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": thresholds_mv is not None,
        "passed": True,
        "thresholds": thresholds_mv,
        "checks": [],
        "failures": [],
    }
    if thresholds_mv is None:
        return payload

    checks = [
        ("mae_static_v", "<=", float(thresholds_mv["mae_static_mv"]) / 1000.0, "static MAE"),
        ("mae_dynamic_v", "<=", float(thresholds_mv["mae_dynamic_mv"]) / 1000.0, "dynamic MAE"),
        ("p95_dynamic_v", "<=", float(thresholds_mv["p95_dynamic_mv"]) / 1000.0, "dynamic P95"),
    ]
    for metric_key, op, threshold_v, label in checks:
        value = _safe_float(metrics_static_dynamic.get(metric_key))
        if value is None:
            payload["checks"].append(
                {
                    "metric": metric_key,
                    "label": label,
                    "passed": False,
                    "reason": "metric unavailable",
                    "value_v": None,
                    "threshold_v": threshold_v,
                    "op": op,
                }
            )
            payload["failures"].append(f"{label} unavailable")
            continue
        passed = value <= threshold_v
        payload["checks"].append(
            {
                "metric": metric_key,
                "label": label,
                "passed": bool(passed),
                "value_v": value,
                "threshold_v": threshold_v,
                "op": op,
            }
        )
        if not passed:
            payload["failures"].append(
                f"{label} failed: {metric_key}={value:.6f}V > {threshold_v:.6f}V"
            )
    payload["passed"] = len(payload["failures"]) == 0
    return payload


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# ECM Fit Compare Report",
        "",
        f"- Passed: `{payload.get('passed')}`",
        f"- Improvement threshold: `{payload.get('threshold')}`",
        f"- Improvement: `{payload.get('improvement')}`",
        "",
        "## Baseline Metrics",
        f"- {json.dumps(payload.get('baseline_metrics', {}), ensure_ascii=False)}",
        "",
        "## Optimized Metrics",
        f"- {json.dumps(payload.get('optimized_metrics', {}), ensure_ascii=False)}",
        "",
        "## Coverage",
        f"- {json.dumps(payload.get('coverage', {}), ensure_ascii=False)}",
    ]
    metrics_static_dynamic = payload.get("metrics_static_dynamic")
    if isinstance(metrics_static_dynamic, dict):
        lines.extend(
            [
                "",
                "## Static/Dynamic Metrics (Optimized)",
                f"- {json.dumps(metrics_static_dynamic, ensure_ascii=False)}",
            ]
        )
    gate_payload = payload.get("gate_2rc")
    if isinstance(gate_payload, dict):
        lines.extend(
            [
                "",
                "## 2RC Gate",
                f"- {json.dumps(gate_payload, ensure_ascii=False)}",
            ]
        )
    stop_reason = payload.get("stop_reason")
    if stop_reason:
        lines.extend(["", "## Stop Reason", f"- {stop_reason}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ecm_fit_compare_pipeline(
    *,
    dfn_config_path: str | Path,
    ecm_config_path: str | Path,
    output_dir: str | Path,
    improve_threshold: float = 0.2,
    ecm_order: int = 1,
    loss_dynamic_weight: float = 0.7,
    fit_temperature_grid_c: list[float] | None = None,
    gate_profile: str = "target",
    cell_id: str = "150Ah_NMC",
) -> dict[str, Any]:
    if ecm_order not in (1, 2):
        raise ValueError("ecm_order must be 1 or 2.")
    dfn_config_path = Path(dfn_config_path).resolve()
    ecm_config_path = Path(ecm_config_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    gate_thresholds = _resolve_gate_thresholds(gate_profile)
    normalized_temp_grid_c = _normalize_fit_temperature_grid(fit_temperature_grid_c)
    suffix = "_2rc" if ecm_order == 2 else ""

    dfn_before_cfg, ecm_before_cfg = _prepare_compare_configs(
        dfn_config_path=dfn_config_path,
        ecm_config_path=ecm_config_path,
        output_dir=output_dir,
        run_tag="before",
        fitted_pack_json=None,
        ecm_order=ecm_order,
    )
    compare_before_dir = output_dir / "compare_before"
    before = run_compare_pipeline(
        dfn_config_path=dfn_before_cfg,
        ecm_config_path=ecm_before_cfg,
        output_dir=compare_before_dir,
        cell_id=cell_id,
    )

    dfn_hppc_summary_csv_by_temp_c = _run_dfn_hppc_for_fit_temperature(
        dfn_config_path=dfn_config_path,
        output_dir=output_dir,
        fit_temperature_grid_c=normalized_temp_grid_c,
    )
    fit_output_dir = output_dir / "fit"
    compare_after_dir = output_dir / "compare_after"
    fit_result: dict[str, Any] | None = None
    after: dict[str, Any] = {}
    fitted_pack_json: Path | None = None
    setup_error: str | None = None
    try:
        fit_result = fit_ecm_parameters_from_dfn_hppc(
            dfn_hppc_summary_csv_by_temp_c=dfn_hppc_summary_csv_by_temp_c,
            output_dir=fit_output_dir,
            source_config_path=dfn_config_path,
            ecm_order=ecm_order,
            loss_dynamic_weight=loss_dynamic_weight,
            current_threshold_a=_CURRENT_DYNAMIC_THRESHOLD_A,
        )
        fitted_pack_json = Path(fit_result["fitted_pack_json"]).resolve()
        dfn_after_cfg, ecm_after_cfg = _prepare_compare_configs(
            dfn_config_path=dfn_config_path,
            ecm_config_path=ecm_config_path,
            output_dir=output_dir,
            run_tag="after",
            fitted_pack_json=fitted_pack_json,
            ecm_order=ecm_order,
        )
        after = run_compare_pipeline(
            dfn_config_path=dfn_after_cfg,
            ecm_config_path=ecm_after_cfg,
            output_dir=compare_after_dir,
            cell_id=cell_id,
        )
    except Exception as exc:
        setup_error = f"Fit/optimized compare failed: {exc}"

    baseline_mae = _extract_mae(before)
    optimized_mae = _extract_mae(after)
    if baseline_mae is None or optimized_mae is None or baseline_mae <= 0:
        improvement = None
    else:
        improvement = float((baseline_mae - optimized_mae) / baseline_mae)

    expected_points = int(len(before.get("soc_grid", [])))
    coverage = {
        "expected_points": expected_points,
        "before_completed_points": int(before.get("completed_points", 0)),
        "after_completed_points": int(after.get("completed_points", 0)),
        "before_full_coverage": bool(before.get("completed_points", 0) == expected_points),
        "after_full_coverage": bool(after.get("completed_points", 0) == expected_points),
    }
    coverage_ok = bool(coverage["before_full_coverage"] and coverage["after_full_coverage"])
    before_static_dynamic = _collect_voltage_delta_series(
        dfn_hppc_summary_csv=_to_abs_path(before.get("dfn_run", {}).get("hppc_summary_csv"), base_dir=compare_before_dir),
        ecm_hppc_summary_csv=_to_abs_path(before.get("ecm_run", {}).get("hppc_summary_csv"), base_dir=compare_before_dir),
        current_threshold_a=_CURRENT_DYNAMIC_THRESHOLD_A,
    )
    optimized_static_dynamic = _collect_voltage_delta_series(
        dfn_hppc_summary_csv=_to_abs_path(after.get("dfn_run", {}).get("hppc_summary_csv"), base_dir=compare_after_dir),
        ecm_hppc_summary_csv=_to_abs_path(after.get("ecm_run", {}).get("hppc_summary_csv"), base_dir=compare_after_dir),
        current_threshold_a=_CURRENT_DYNAMIC_THRESHOLD_A,
    )
    gate_2rc = _evaluate_2rc_gate(optimized_static_dynamic, gate_thresholds if ecm_order == 2 else None)

    stop_reason: str | None = None
    passed = True
    failures: list[str] = []
    if setup_error is not None:
        stop_reason = setup_error
        passed = False
        failures.append(setup_error)
    elif not before.get("passed"):
        stop_reason = f"Baseline compare failed: {before.get('stop_reason') or 'unknown'}"
        passed = False
        failures.append(stop_reason)
    elif not after.get("passed"):
        stop_reason = f"Optimized compare failed: {after.get('stop_reason') or 'unknown'}"
        passed = False
        failures.append(stop_reason)
    elif not coverage_ok:
        stop_reason = "Coverage check failed: expected full SOC grid on both baseline and optimized runs."
        passed = False
        failures.append(stop_reason)
    elif improvement is None:
        stop_reason = "Improvement is unavailable due to invalid MAE metrics."
        passed = False
        failures.append(stop_reason)
    elif improvement < float(improve_threshold):
        stop_reason = (
            f"Improvement below threshold: improvement={improvement:.6f} < threshold={float(improve_threshold):.6f}"
        )
        passed = False
        failures.append(stop_reason)
    if passed and ecm_order == 2 and not gate_2rc.get("passed", True):
        stop_reason = "2RC quality gate failed."
        passed = False
        failures.extend(gate_2rc.get("failures", []))

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "threshold": float(improve_threshold),
        "ecm_order": int(ecm_order),
        "loss_dynamic_weight": float(loss_dynamic_weight),
        "fit_temperature_grid_c": [float(v) for v in normalized_temp_grid_c],
        "gate_profile": gate_profile,
        "baseline_metrics": before.get("metrics", {}),
        "optimized_metrics": after.get("metrics", {}),
        "improvement": improvement,
        "coverage": coverage,
        "baseline_static_dynamic_metrics": before_static_dynamic,
        "metrics_static_dynamic": optimized_static_dynamic,
        "gate_2rc": gate_2rc,
        "artifacts": {
            "ecm_fitted_pack_json": None if fitted_pack_json is None else str(fitted_pack_json),
            "ecm_fit_points_csv": None
            if fit_result is None
            else str(Path(fit_result["fit_points_csv"]).resolve()),
            "dfn_hppc_summary_csv_by_temp_c": {
                str(temp_c): str(path)
                for temp_c, path in dfn_hppc_summary_csv_by_temp_c.items()
            },
            "compare_before_summary_json": str(compare_before_dir / "hppc_compare_summary.json"),
            "compare_after_summary_json": str(compare_after_dir / "hppc_compare_summary.json"),
            "ecm_fit_compare_summary_json": str(output_dir / f"ecm_fit_compare_summary{suffix}.json"),
            "ecm_fit_compare_report_md": str(output_dir / f"ecm_fit_compare_report{suffix}.md"),
        },
    }
    if failures:
        summary["failures"] = failures
    if stop_reason:
        summary["stop_reason"] = stop_reason

    summary_path = output_dir / f"ecm_fit_compare_summary{suffix}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_report(output_dir / f"ecm_fit_compare_report{suffix}.md", summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fit ECM parameters from DFN HPPC and compare before/after.")
    parser.add_argument("--dfn-config", required=True, help="DFN HPPC config path.")
    parser.add_argument("--ecm-config", required=True, help="ECM HPPC config path.")
    parser.add_argument("--output-dir", required=True, help="Output directory for fit and compare artifacts.")
    parser.add_argument("--improve-threshold", type=float, default=0.2, help="Relative MAE improvement threshold.")
    parser.add_argument(
        "--ecm-order",
        type=int,
        default=1,
        choices=[1, 2],
        help="ECM RC order used for fit/replay (1 or 2).",
    )
    parser.add_argument(
        "--loss-dynamic-weight",
        type=float,
        default=0.7,
        help="Dynamic segment weight in weighted fitting loss (0~1).",
    )
    parser.add_argument(
        "--fit-temperature-grid-c",
        nargs="+",
        type=float,
        default=[-10.0, 25.0, 45.0],
        help="Temperature grid (degC) for DFN HPPC fitting inputs, e.g. -10 25 45.",
    )
    parser.add_argument(
        "--gate-profile",
        default="target",
        choices=["off", "target"],
        help="2RC gate profile. 'target' enforces static/dynamic residual thresholds.",
    )
    parser.add_argument("--cell-id", default="150Ah_NMC", help="Cell identifier for reports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_ecm_fit_compare_pipeline(
        dfn_config_path=args.dfn_config,
        ecm_config_path=args.ecm_config,
        output_dir=args.output_dir,
        improve_threshold=float(args.improve_threshold),
        ecm_order=int(args.ecm_order),
        loss_dynamic_weight=float(args.loss_dynamic_weight),
        fit_temperature_grid_c=list(args.fit_temperature_grid_c),
        gate_profile=str(args.gate_profile),
        cell_id=str(args.cell_id),
    )
    print(json.dumps(summary, indent=2))
    return 0 if bool(summary.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
