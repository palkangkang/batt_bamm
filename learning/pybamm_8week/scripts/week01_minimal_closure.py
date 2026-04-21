from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import pybamm


def _extract_time_and_voltage(solution: pybamm.Solution) -> tuple[list[float], list[float], str]:
    voltage_names = [
        "Terminal voltage [V]",
        "Voltage [V]",
        "Battery voltage [V]",
    ]
    time_s = solution["Time [s]"].entries
    for name in voltage_names:
        try:
            voltage = solution[name].entries
            return time_s.tolist(), voltage.tolist(), name
        except KeyError:
            continue
    raise KeyError(f"Could not find a voltage variable in candidates: {voltage_names}")


def _plot_curve(time_s: list[float], voltage_v: list[float], title: str, output_png: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    ax.plot(time_s, voltage_v, linewidth=1.8)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Voltage [V]")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_png)
    plt.close(fig)


def _run_single_discharge() -> tuple[pybamm.Solution, float]:
    model = pybamm.lithium_ion.DFN()
    simulation = pybamm.Simulation(model)
    t0 = time.perf_counter()
    solution = simulation.solve([0, 3600])
    elapsed_s = time.perf_counter() - t0
    return solution, elapsed_s


def _run_experiment_cycle() -> tuple[pybamm.Solution, float]:
    model = pybamm.lithium_ion.DFN()
    experiment = pybamm.Experiment(
        [
            "Discharge at 1C until 3.2 V",
            "Rest for 10 minutes",
            "Charge at 0.5C until 4.1 V",
            "Hold at 4.1 V until C/20",
            "Rest for 10 minutes",
        ]
    )
    simulation = pybamm.Simulation(model, experiment=experiment)
    t0 = time.perf_counter()
    solution = simulation.solve()
    elapsed_s = time.perf_counter() - t0
    return solution, elapsed_s


def _write_run_log(path: Path, discharge_runtime_s: float, experiment_runtime_s: float, voltage_name: str) -> None:
    content = f"""# Week 1 Run Log

Generated at: {datetime.now(timezone.utc).isoformat()}

## Runs Completed

1. DFN single discharge
2. DFN experiment cycle

## Runtime

- Single discharge runtime (s): {discharge_runtime_s:.3f}
- Experiment runtime (s): {experiment_runtime_s:.3f}

## Concept Notes

- `Simulation`: wraps model, parameter values, discretization, and solver flow.
- `Experiment`: defines protocol steps in human-readable strings.
- `solve()`: executes the numerical solve and returns a `Solution`.
- `plot()`: quick plotting helper (this script uses matplotlib for reproducible files).

## Output Variable Used

- Voltage variable selected: `{voltage_name}`
"""
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 1 minimal closure runner for PyBaMM.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/pybamm_learning/week01"),
        help="Output directory for plots and logs.",
    )
    args = parser.parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    discharge_solution, discharge_runtime_s = _run_single_discharge()
    experiment_solution, experiment_runtime_s = _run_experiment_cycle()

    discharge_t, discharge_v, voltage_name_1 = _extract_time_and_voltage(discharge_solution)
    experiment_t, experiment_v, voltage_name_2 = _extract_time_and_voltage(experiment_solution)

    _plot_curve(
        discharge_t,
        discharge_v,
        title="Week 1 DFN Single Discharge",
        output_png=output_dir / "week01_dfn_discharge_voltage.png",
    )
    _plot_curve(
        experiment_t,
        experiment_v,
        title="Week 1 DFN Experiment Cycle",
        output_png=output_dir / "week01_experiment_voltage.png",
    )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pybamm_version": pybamm.__version__,
        "runs": {
            "single_discharge": {
                "runtime_s": discharge_runtime_s,
                "duration_simulated_s": float(discharge_t[-1]) if discharge_t else 0.0,
                "voltage_variable": voltage_name_1,
            },
            "experiment_cycle": {
                "runtime_s": experiment_runtime_s,
                "duration_simulated_s": float(experiment_t[-1]) if experiment_t else 0.0,
                "voltage_variable": voltage_name_2,
            },
        },
        "artifacts": {
            "dfn_plot": str((output_dir / "week01_dfn_discharge_voltage.png").resolve()),
            "experiment_plot": str((output_dir / "week01_experiment_voltage.png").resolve()),
            "run_log": str((output_dir / "week01_run_log.md").resolve()),
        },
    }

    summary_path = output_dir / "week01_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    _write_run_log(
        output_dir / "week01_run_log.md",
        discharge_runtime_s=discharge_runtime_s,
        experiment_runtime_s=experiment_runtime_s,
        voltage_name=voltage_name_1,
    )

    print(f"Week 1 outputs written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
