from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from batt_bamm.main import _soc_grid, load_config, run_from_config

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y7lvtQAAAAASUVORK5CYII="
)


def _to_path(value: Any) -> Path | None:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for item in values:
        if item not in seen:
            seen.append(item)
    return seen


def _load_hppc_summary_csv(path: Path, side: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "soc_target",
        "v_dis_end",
        "v_dis_rest_start",
        "r_dis_10s_ohm",
        "passed",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{side} hppc summary missing columns: {missing}")
    frame = frame.copy()
    frame["soc_target"] = pd.to_numeric(frame["soc_target"], errors="coerce")
    frame["v_dis_end"] = pd.to_numeric(frame["v_dis_end"], errors="coerce")
    frame["v_dis_rest_start"] = pd.to_numeric(frame["v_dis_rest_start"], errors="coerce")
    frame["r_dis_10s_ohm"] = pd.to_numeric(frame["r_dis_10s_ohm"], errors="coerce")
    if pd.api.types.is_bool_dtype(frame["passed"]):
        passed = frame["passed"]
    elif pd.api.types.is_numeric_dtype(frame["passed"]):
        passed = frame["passed"] != 0
    else:
        passed = (
            frame["passed"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"1", "true", "yes", "y"})
        )
    frame["passed"] = passed
    frame = frame.loc[frame["passed"]].copy()
    if frame.empty:
        raise ValueError(f"{side} hppc summary has no passed points.")
    return frame


def _write_overlay(df: pd.DataFrame, png_path: Path) -> str | None:
    if df.empty:
        png_path.write_bytes(_ONE_PIXEL_PNG)
        return "No aligned SOC points; wrote placeholder PNG."
    try:
        os.environ.setdefault("MPLBACKEND", "Agg")
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception:
        png_path.write_bytes(_ONE_PIXEL_PNG)
        return "matplotlib unavailable; wrote placeholder PNG."

    fig, axis = plt.subplots(figsize=(8, 4.5), dpi=150)
    axis.plot(df["soc_target"] * 100.0, df["delta_v_dis_end_v"], marker="o", linewidth=1.4)
    axis.axhline(0.0, color="black", linestyle="--", linewidth=0.8, alpha=0.6)
    axis.set_xlabel("SOC [%]")
    axis.set_ylabel("DFN - ECM V_dis_end [V]")
    axis.set_title("HPPC Voltage Delta by SOC")
    axis.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)
    return None


def _run_side(config_path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = run_from_config(config_path, mode="hppc")
    hppc = summary.get("hppc", {})
    csv_path = _to_path(hppc.get("artifacts", {}).get("hppc_summary_csv"))
    run_payload = {
        "label": label,
        "config_path": str(config_path.resolve()),
        "output_dir": summary.get("config", {}).get("output_dir"),
        "summary_mode": summary.get("mode"),
        "all_converged": bool(summary.get("all_converged")),
        "passed": bool(hppc.get("passed", False)),
        "completed_points": int(hppc.get("completed_points", 0)),
        "total_points": int(hppc.get("total_points", 0)),
        "hppc_summary_csv": None if csv_path is None else str(csv_path),
        "stop_reason": hppc.get("stop_reason"),
        "warnings": summary.get("warnings", []),
    }
    return summary, run_payload


def run_compare_pipeline(
    dfn_config_path: str | Path,
    ecm_config_path: str | Path,
    output_dir: str | Path,
    *,
    cell_id: str = "150Ah_NMC",
) -> dict[str, Any]:
    dfn_config_path = Path(dfn_config_path)
    ecm_config_path = Path(ecm_config_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    dfn_cfg = load_config(dfn_config_path)
    ecm_cfg = load_config(ecm_config_path)
    dfn_grid = _soc_grid(dfn_cfg.hppc.soc_start, dfn_cfg.hppc.soc_end, dfn_cfg.hppc.soc_step)
    ecm_grid = _soc_grid(ecm_cfg.hppc.soc_start, ecm_cfg.hppc.soc_end, ecm_cfg.hppc.soc_step)

    dfn_summary, dfn_run = _run_side(dfn_config_path, "dfn")
    ecm_summary, ecm_run = _run_side(ecm_config_path, "ecm")

    compare_csv = output_dir / "hppc_compare_by_soc.csv"
    compare_json = output_dir / "hppc_compare_summary.json"
    compare_png = output_dir / "hppc_compare_voltage_delta.png"
    compare_report = output_dir / "hppc_compare_report.md"

    stop_reason: str | None = None
    warnings: list[str] = []

    if dfn_cfg.model_type != "dfn":
        stop_reason = "DFN config model.type must be dfn."
    elif ecm_cfg.model_type != "ecm":
        stop_reason = "ECM config model.type must be ecm."
    elif dfn_cfg.chemistry != ecm_cfg.chemistry:
        stop_reason = "DFN/ECM chemistry must match for compare."
    elif not np.isclose(dfn_cfg.nominal_capacity_ah, ecm_cfg.nominal_capacity_ah, atol=1e-12):
        stop_reason = "DFN/ECM nominal_capacity_ah must match for compare."
    elif len(dfn_grid) != len(ecm_grid) or not np.allclose(np.array(dfn_grid), np.array(ecm_grid), atol=1e-10):
        stop_reason = "DFN/ECM SOC grids do not match."
    elif not (dfn_run["all_converged"] and dfn_run["passed"]):
        stop_reason = f"DFN HPPC run failed: {dfn_run['stop_reason'] or 'unknown'}"
    elif not (ecm_run["all_converged"] and ecm_run["passed"]):
        stop_reason = f"ECM HPPC run failed: {ecm_run['stop_reason'] or 'unknown'}"

    metrics: dict[str, Any] = {
        "mae_v_dis_end_v": None,
        "max_abs_delta_v_dis_end_v": None,
        "rmse_v_dis_end_v": None,
        "worst_soc_target": None,
    }
    table = pd.DataFrame(
        columns=[
            "soc_target",
            "v_dis_end_dfn",
            "v_dis_end_ecm",
            "delta_v_dis_end_v",
            "abs_delta_v_dis_end_v",
            "v_dis_rest_start_dfn",
            "v_dis_rest_start_ecm",
            "delta_v_dis_rest_start_v",
            "r_dis_10s_ohm_dfn",
            "r_dis_10s_ohm_ecm",
            "delta_r_dis_10s_ohm",
        ]
    )

    if stop_reason is None:
        try:
            dfn_csv_path = _to_path(dfn_run["hppc_summary_csv"])
            ecm_csv_path = _to_path(ecm_run["hppc_summary_csv"])
            if dfn_csv_path is None or ecm_csv_path is None:
                raise ValueError("Missing HPPC summary CSV path from one or both runs.")
            dfn_table = _load_hppc_summary_csv(dfn_csv_path, "DFN")
            ecm_table = _load_hppc_summary_csv(ecm_csv_path, "ECM")

            merged = dfn_table.merge(ecm_table, on="soc_target", suffixes=("_dfn", "_ecm"), how="inner")
            merged = merged.sort_values("soc_target", ascending=False).reset_index(drop=True)
            if len(merged) != len(dfn_grid):
                raise ValueError(
                    f"Aligned SOC points mismatch: expected {len(dfn_grid)} but got {len(merged)}."
                )
            table = pd.DataFrame(
                {
                    "soc_target": merged["soc_target"],
                    "v_dis_end_dfn": merged["v_dis_end_dfn"],
                    "v_dis_end_ecm": merged["v_dis_end_ecm"],
                    "delta_v_dis_end_v": merged["v_dis_end_dfn"] - merged["v_dis_end_ecm"],
                    "abs_delta_v_dis_end_v": np.abs(merged["v_dis_end_dfn"] - merged["v_dis_end_ecm"]),
                    "v_dis_rest_start_dfn": merged["v_dis_rest_start_dfn"],
                    "v_dis_rest_start_ecm": merged["v_dis_rest_start_ecm"],
                    "delta_v_dis_rest_start_v": merged["v_dis_rest_start_dfn"] - merged["v_dis_rest_start_ecm"],
                    "r_dis_10s_ohm_dfn": merged["r_dis_10s_ohm_dfn"],
                    "r_dis_10s_ohm_ecm": merged["r_dis_10s_ohm_ecm"],
                    "delta_r_dis_10s_ohm": merged["r_dis_10s_ohm_dfn"] - merged["r_dis_10s_ohm_ecm"],
                }
            )
            delta = table["delta_v_dis_end_v"].to_numpy(dtype=float)
            abs_delta = table["abs_delta_v_dis_end_v"].to_numpy(dtype=float)
            worst_idx = int(np.argmax(abs_delta))
            metrics = {
                "mae_v_dis_end_v": float(np.mean(abs_delta)),
                "max_abs_delta_v_dis_end_v": float(np.max(abs_delta)),
                "rmse_v_dis_end_v": float(np.sqrt(np.mean(np.square(delta)))),
                "worst_soc_target": float(table.loc[worst_idx, "soc_target"]),
            }
        except Exception as exc:
            stop_reason = str(exc)

    table.to_csv(compare_csv, index=False)
    overlay_warning = _write_overlay(table, compare_png)
    if overlay_warning:
        warnings.append(overlay_warning)

    warnings.extend(dfn_run.get("warnings", []))
    warnings.extend(ecm_run.get("warnings", []))

    passed = stop_reason is None
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cell_id": cell_id,
        "chemistry": dfn_cfg.chemistry,
        "nominal_capacity_ah": dfn_cfg.nominal_capacity_ah,
        "soc_grid": dfn_grid,
        "dfn_run": dfn_run,
        "ecm_run": ecm_run,
        "completed_points": int(len(table)),
        "passed": passed,
        "metrics": metrics,
        "artifacts": {
            "hppc_compare_by_soc_csv": str(compare_csv),
            "hppc_compare_summary_json": str(compare_json),
            "hppc_compare_voltage_delta_png": str(compare_png),
            "hppc_compare_report_md": str(compare_report),
        },
    }
    if stop_reason:
        summary["stop_reason"] = stop_reason
    if warnings:
        summary["warnings"] = _dedupe(warnings)

    lines = [
        "# HPPC DFN vs ECM Compare Report",
        "",
        f"- Cell ID: `{cell_id}`",
        f"- Chemistry: `{summary['chemistry']}`",
        f"- Nominal capacity [Ah]: `{summary['nominal_capacity_ah']}`",
        f"- Passed: `{passed}`",
        f"- Completed points: `{summary['completed_points']}`",
    ]
    if stop_reason:
        lines.extend(["", "## Stop Reason", f"- {stop_reason}"])
    lines.extend(
        [
            "",
            "## Metrics",
            f"- mae_v_dis_end_v: `{metrics['mae_v_dis_end_v']}`",
            f"- max_abs_delta_v_dis_end_v: `{metrics['max_abs_delta_v_dis_end_v']}`",
            f"- rmse_v_dis_end_v: `{metrics['rmse_v_dis_end_v']}`",
            f"- worst_soc_target: `{metrics['worst_soc_target']}`",
            "",
            "## Artifacts",
            f"- compare_csv: `{compare_csv}`",
            f"- compare_json: `{compare_json}`",
            f"- compare_png: `{compare_png}`",
        ]
    )
    compare_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    compare_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare DFN vs ECM HPPC voltage response.")
    parser.add_argument("--dfn-config", required=True, help="DFN HPPC config path.")
    parser.add_argument("--ecm-config", required=True, help="ECM HPPC config path.")
    parser.add_argument("--output-dir", required=True, help="Output directory for compare artifacts.")
    parser.add_argument("--cell-id", default="150Ah_NMC", help="Cell identifier for compare summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_compare_pipeline(
        dfn_config_path=args.dfn_config,
        ecm_config_path=args.ecm_config,
        output_dir=args.output_dir,
        cell_id=args.cell_id,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
