from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pybamm


def _extract_time_and_voltage(solution: pybamm.Solution) -> tuple[list[float], list[float]]:
    voltage_names = ["Terminal voltage [V]", "Voltage [V]", "Battery voltage [V]"]
    t = solution["Time [s]"].entries.tolist()
    for name in voltage_names:
        try:
            v = solution[name].entries.tolist()
            return t, v
        except KeyError:
            continue
    raise KeyError("No usable voltage variable found.")


def _run_case(label: str, solver: pybamm.BaseSolver, var_pts: dict[str, int]) -> dict[str, Any]:
    model = pybamm.lithium_ion.DFN()
    experiment = pybamm.Experiment(["Discharge at 1C until 3.2 V"])
    sim = pybamm.Simulation(model, experiment=experiment, solver=solver, var_pts=var_pts)
    t0 = time.perf_counter()
    solution = sim.solve()
    runtime_s = time.perf_counter() - t0
    time_s, voltage_v = _extract_time_and_voltage(solution)
    return {
        "case": label,
        "status": "ok",
        "runtime_s": runtime_s,
        "n_points": len(time_s),
        "final_voltage_v": float(voltage_v[-1]) if voltage_v else None,
        "time_s": time_s,
        "voltage_v": voltage_v,
        "error": "",
    }


def _build_cases() -> list[tuple[str, pybamm.BaseSolver, dict[str, int]]]:
    coarse = {"x_n": 20, "x_s": 20, "x_p": 20, "r_n": 20, "r_p": 20}
    fine = {"x_n": 60, "x_s": 60, "x_p": 60, "r_n": 60, "r_p": 60}
    cases: list[tuple[str, pybamm.BaseSolver, dict[str, int]]] = [
        ("casadi_coarse", pybamm.CasadiSolver(), coarse),
        ("casadi_fine", pybamm.CasadiSolver(), fine),
    ]
    try:
        cases.append(("idaklu_coarse", pybamm.IDAKLUSolver(), coarse))
    except Exception:
        pass
    return cases


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["case", "status", "runtime_s", "n_points", "final_voltage_v", "error"],
        )
        writer.writeheader()
        for row in rows:
            runtime = ""
            if row["runtime_s"] is not None:
                runtime = f"{float(row['runtime_s']):.6f}"
            final_voltage = ""
            if row["final_voltage_v"] is not None:
                final_voltage = f"{float(row['final_voltage_v']):.6f}"
            writer.writerow(
                {
                    "case": row["case"],
                    "status": row["status"],
                    "runtime_s": runtime,
                    "n_points": row["n_points"] if row["n_points"] is not None else "",
                    "final_voltage_v": final_voltage,
                    "error": row["error"],
                }
            )


def _write_plot(path: Path, rows: list[dict[str, Any]]) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    ok_rows = [r for r in rows if r["status"] == "ok"]
    for row in ok_rows:
        ax.plot(row["time_s"], row["voltage_v"], label=row["case"], linewidth=1.6)
    ax.set_title("Week 3 Solver/Mesh Compare")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Voltage [V]")
    ax.grid(True, alpha=0.3)
    if ok_rows:
        ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _write_notes(path: Path, rows: list[dict[str, Any]]) -> None:
    ok_rows = [r for r in rows if r["status"] == "ok"]
    failed_rows = [r for r in rows if r["status"] != "ok"]
    lines = [
        "# Week 3 Observations",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Completed Cases",
    ]
    for row in ok_rows:
        lines.append(f"- {row['case']}: runtime={float(row['runtime_s']):.3f}s, points={row['n_points']}")
    if failed_rows:
        lines.append("")
        lines.append("## Failed Cases")
        for row in failed_rows:
            lines.append(f"- {row['case']}: {row['error']}")
    lines.append("")
    lines.append("## Fill-in Analysis")
    lines.append("1. Did coarser mesh preserve trend-level conclusions?")
    lines.append("2. Which setup is best for exploratory runs?")
    lines.append("3. Which setup is best for final reporting and why?")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 3 solver and mesh comparison runner.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/pybamm_learning/week03"),
        help="Output directory.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for label, solver, var_pts in _build_cases():
        print(f"Running {label}...")
        try:
            results.append(_run_case(label, solver, var_pts))
        except Exception as exc:
            results.append(
                {
                    "case": label,
                    "status": "error",
                    "runtime_s": None,
                    "n_points": None,
                    "final_voltage_v": None,
                    "time_s": [],
                    "voltage_v": [],
                    "error": str(exc),
                }
            )

    _write_csv(output_dir / "week03_solver_mesh_compare.csv", results)
    _write_plot(output_dir / "week03_solver_mesh_compare_voltage.png", results)
    _write_notes(output_dir / "week03_observations.md", results)

    print(f"Week 3 outputs written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
