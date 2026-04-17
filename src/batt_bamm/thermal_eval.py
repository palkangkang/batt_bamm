from __future__ import annotations

import argparse
import base64
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from batt_bamm.main import (
    Config,
    TerminationCondition,
    TerminationConfig,
    load_config,
    run_pipeline,
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y7lvtQAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class ThermalEvalCase:
    case_id: str
    ambient_temp_k: float
    initial_cell_temp_k: float
    soc_start: float
    soc_end: float
    rate_c: float


def _k_to_c(value_k: float | None) -> float | None:
    if value_k is None:
        return None
    return float(value_k) - 273.15


def _resolve_temp_k(case_raw: dict[str, Any], *, key_prefix: str) -> float:
    key_k = f"{key_prefix}_temp_k"
    key_c = f"{key_prefix}_temp_c"
    if key_k in case_raw and case_raw[key_k] is not None:
        value = float(case_raw[key_k])
    elif key_c in case_raw and case_raw[key_c] is not None:
        value = float(case_raw[key_c]) + 273.15
    else:
        raise ValueError(f"cases[].{key_prefix}_temp_k or cases[].{key_prefix}_temp_c must be provided.")
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"cases[].{key_prefix}_temp_* must be a positive finite number in Kelvin.")
    return value


def _parse_case(case_raw: dict[str, Any], *, index: int) -> ThermalEvalCase:
    if not isinstance(case_raw, dict):
        raise ValueError(f"cases[{index}] must be a mapping.")
    case_id = str(case_raw.get("case_id", f"case_{index + 1:02d}")).strip()
    if not case_id:
        raise ValueError(f"cases[{index}].case_id must not be empty.")
    try:
        soc_start = float(case_raw["soc_start"])
        soc_end = float(case_raw["soc_end"])
        rate_c = float(case_raw["rate_c"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"cases[{index}] requires numeric soc_start/soc_end/rate_c.") from exc
    if not (0.0 <= soc_start <= 1.0 and 0.0 <= soc_end <= 1.0):
        raise ValueError(f"cases[{index}] soc_start/soc_end must be within [0, 1].")
    if np.isclose(soc_start, soc_end, atol=1e-12):
        raise ValueError(f"cases[{index}] soc_start and soc_end must differ.")
    if not np.isfinite(rate_c) or rate_c <= 0:
        raise ValueError(f"cases[{index}] rate_c must be a positive finite number.")

    return ThermalEvalCase(
        case_id=case_id,
        ambient_temp_k=_resolve_temp_k(case_raw, key_prefix="ambient"),
        initial_cell_temp_k=_resolve_temp_k(case_raw, key_prefix="initial_cell"),
        soc_start=soc_start,
        soc_end=soc_end,
        rate_c=rate_c,
    )


def _build_case_profile(case: ThermalEvalCase, *, nominal_capacity_ah: float, sampling_period_s: float) -> pd.DataFrame:
    duration_s = abs(case.soc_end - case.soc_start) / case.rate_c * 3600.0
    if duration_s <= 0:
        raise ValueError(f"{case.case_id}: computed duration is non-positive.")
    direction_sign = 1.0 if case.soc_end < case.soc_start else -1.0
    current_a = direction_sign * case.rate_c * nominal_capacity_ah
    times = np.arange(0.0, duration_s + sampling_period_s * 0.5, sampling_period_s, dtype=float)
    return pd.DataFrame(
        {
            "time_s": times,
            "current_a": np.full(times.shape, current_a, dtype=float),
            "temp_k": np.full(times.shape, case.ambient_temp_k, dtype=float),
        }
    )


def _case_termination(base_cfg: Config, case: ThermalEvalCase) -> TerminationConfig:
    direction_is_discharge = case.soc_end < case.soc_start
    conditions = [
        TerminationCondition(
            metric="cell_temperature_k",
            op=">=",
            threshold=333.15,
            name="cell_temp_limit_60c",
        ),
        TerminationCondition(
            metric="boundary_temperature_k",
            op=">=",
            threshold=323.15,
            name="boundary_temp_limit_50c",
        ),
        TerminationCondition(
            metric="voltage_v",
            op="<=" if direction_is_discharge else ">=",
            threshold=base_cfg.voltage_low_v if direction_is_discharge else base_cfg.voltage_high_v,
            name="voltage_default_limit",
        ),
        TerminationCondition(
            metric="soc",
            op="<=" if direction_is_discharge else ">=",
            threshold=case.soc_end,
            name="soc_target_limit",
        ),
    ]
    return TerminationConfig(
        enabled=True,
        logic="any_of",
        must_hit=False,
        apply_to_experiment_modes=True,
        conditions=conditions,
    )


def _write_temperature_overlay(output_png: Path, rows: list[dict[str, Any]]) -> str | None:
    series_rows = [row for row in rows if row.get("artifact_csv") and Path(str(row["artifact_csv"])).exists()]
    if not series_rows:
        output_png.write_bytes(_ONE_PIXEL_PNG)
        return "No case csv available; wrote placeholder PNG."
    mpl_dir = output_png.parent / ".mplconfig"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir.resolve()))
    try:
        import matplotlib.pyplot as plt
    except Exception:
        output_png.write_bytes(_ONE_PIXEL_PNG)
        return "matplotlib unavailable; wrote placeholder PNG."

    fig, axis = plt.subplots(figsize=(9, 5), dpi=150)
    for row in series_rows:
        frame = pd.read_csv(str(row["artifact_csv"]))
        if frame.empty:
            continue
        axis.plot(
            frame["time_s"] / 3600.0,
            pd.to_numeric(frame["cell_temperature_k"], errors="coerce") - 273.15,
            label=str(row["case_id"]),
        )
    axis.set_xlabel("Time [h]")
    axis.set_ylabel("Cell Temperature [°C]")
    axis.set_title("Thermal Eval Temperature Overlay")
    axis.grid(True, alpha=0.3)
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_png)
    plt.close(fig)
    return None


def run_thermal_eval(config_path: str | Path, output_dir_override: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Thermal eval config must be a mapping.")

    base_config_raw = raw.get("base_config_path")
    if not isinstance(base_config_raw, str) or not base_config_raw.strip():
        raise ValueError("base_config_path is required.")
    base_config_path = Path(base_config_raw.strip())
    if not base_config_path.is_absolute():
        base_config_path = (config_path.parent / base_config_path).resolve()
    if not base_config_path.exists():
        raise ValueError(f"base_config_path not found: {base_config_path}")

    sampling_period_s = float(raw.get("sampling_period_s", 1.0))
    if not np.isfinite(sampling_period_s) or sampling_period_s <= 0:
        raise ValueError("sampling_period_s must be a positive finite number.")

    output_dir_raw = output_dir_override if output_dir_override is not None else raw.get("output_dir", "")
    if not output_dir_raw:
        raise ValueError("output_dir is required.")
    output_dir = Path(str(output_dir_raw))
    if not output_dir.is_absolute():
        output_dir = (config_path.parent / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cases_raw = raw.get("cases", [])
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError("cases must be a non-empty list.")
    cases = [_parse_case(item, index=index) for index, item in enumerate(cases_raw)]

    base_cfg = load_config(base_config_path)
    if base_cfg.model_type != "dfn":
        raise ValueError("Thermal eval requires DFN base config.")

    rows: list[dict[str, Any]] = []
    for case in cases:
        case_run_dir = output_dir / "case_runs" / case.case_id
        case_run_dir.mkdir(parents=True, exist_ok=True)
        profile = _build_case_profile(case, nominal_capacity_ah=base_cfg.nominal_capacity_ah, sampling_period_s=sampling_period_s)
        profile_csv = case_run_dir / "timeseries_input.csv"
        profile.to_csv(profile_csv, index=False)

        direction_is_discharge = case.soc_end < case.soc_start
        timeseries_cfg = replace(
            base_cfg.timeseries,
            enabled=True,
            csv_path=profile_csv,
            period_s=sampling_period_s,
            use_temp_as_ambient_boundary=False,
            allow_early_stop=True,
            charge_compare=replace(base_cfg.timeseries.charge_compare, enabled=False),
            soc_switch_approx=replace(base_cfg.timeseries.soc_switch_approx, enabled=False),
        )
        run_cfg = replace(
            base_cfg,
            model_type="dfn",
            thermal="lumped",
            output_dir=case_run_dir,
            initial_soc=case.soc_start,
            ambient_temp_k=case.ambient_temp_k,
            initial_cell_temp_k=case.initial_cell_temp_k,
            sanity_gate=replace(base_cfg.sanity_gate, enabled=False),
            hppc=replace(base_cfg.hppc, enabled=False),
            benchmark=replace(base_cfg.benchmark, enabled=False),
            quality_gate=replace(base_cfg.quality_gate, enabled=False, enforce=False),
            identification_inputs=replace(base_cfg.identification_inputs, enabled=False),
            thermal_coupling=replace(base_cfg.thermal_coupling, enabled=False, boundary_mode="constant"),
            timeseries=timeseries_cfg,
            termination=_case_termination(base_cfg, case),
        )
        run_summary = run_pipeline(run_cfg, mode="timeseries")
        case_block = run_summary.get("timeseries", {})
        case_result = case_block.get("case", {}) if isinstance(case_block, dict) else {}
        source_csv = case_result.get("csv_path")
        export_csv = output_dir / f"thermal_case_{case.case_id}.csv"
        export_columns = [
            "time_s",
            "current_a",
            "voltage_v",
            "soc",
            "cell_temperature_k",
            "boundary_temperature_k",
        ]
        if source_csv and Path(str(source_csv)).exists():
            frame = pd.read_csv(str(source_csv))
            missing = [column for column in export_columns if column not in frame.columns]
            if missing:
                raise ValueError(f"{case.case_id}: missing required output columns: {missing}")
            frame = frame[export_columns].copy()
            frame.to_csv(export_csv, index=False)
        else:
            pd.DataFrame(
                columns=export_columns
            ).to_csv(export_csv, index=False)
            frame = pd.read_csv(export_csv)

        final_voltage = float(frame["voltage_v"].iloc[-1]) if not frame.empty else None
        final_soc = float(frame["soc"].iloc[-1]) if not frame.empty else None
        max_cell_temp = float(frame["cell_temperature_k"].max()) if not frame.empty else None
        max_boundary_temp = float(frame["boundary_temperature_k"].max()) if not frame.empty else None
        term = case_result.get("termination", {}) if isinstance(case_result, dict) else {}
        rows.append(
            {
                "case_id": case.case_id,
                "ambient_temp_c": _k_to_c(case.ambient_temp_k),
                "initial_cell_temp_c": _k_to_c(case.initial_cell_temp_k),
                "soc_start": case.soc_start,
                "soc_end": case.soc_end,
                "rate_c": case.rate_c,
                "direction": "discharge" if direction_is_discharge else "charge",
                "sampling_period_s": sampling_period_s,
                "converged": bool(case_result.get("converged", False)),
                "stop_reason": case_block.get("stop_reason"),
                "termination_hit": bool(term.get("hit", False)),
                "termination_reason": term.get("reason"),
                "termination_metric": term.get("metric"),
                "termination_time_s": term.get("time_s"),
                "final_voltage_v": final_voltage,
                "final_soc": final_soc,
                "max_cell_temperature_c": _k_to_c(max_cell_temp),
                "max_boundary_temperature_c": _k_to_c(max_boundary_temp),
                "artifact_csv": str(export_csv),
                "run_summary_json": str(case_run_dir / "summary.json"),
            }
        )

    summary_csv = output_dir / "thermal_eval_summary.csv"
    pd.DataFrame(rows).to_csv(summary_csv, index=False)
    overlay_png = output_dir / "thermal_eval_temperature_overlay.png"
    overlay_warning = _write_temperature_overlay(overlay_png, rows)

    passed = all(bool(row.get("converged")) for row in rows)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_config_path": str(base_config_path),
        "sampling_period_s": sampling_period_s,
        "temperature_unit": "C",
        "passed": passed,
        "total_cases": len(rows),
        "completed_cases": sum(1 for row in rows if row.get("converged")),
        "artifacts": {
            "thermal_eval_summary_csv": str(summary_csv),
            "thermal_eval_summary_json": str(output_dir / "thermal_eval_summary.json"),
            "thermal_eval_manifest_json": str(output_dir / "thermal_eval_manifest.json"),
            "thermal_eval_temperature_overlay_png": str(overlay_png),
        },
        "cases": rows,
    }
    if overlay_warning:
        summary["overlay_warning"] = overlay_warning

    summary_path = output_dir / "thermal_eval_summary.json"
    manifest_path = output_dir / "thermal_eval_manifest.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run thermal-eval timeseries matrix for DFN NMC cases.")
    parser.add_argument("--config", required=True, help="Path to thermal eval YAML config.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    summary = run_thermal_eval(config_path=args.config, output_dir_override=args.output_dir)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("passed", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
