from __future__ import annotations

import argparse
import csv
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import pybamm


def _solve_and_time(simulation: pybamm.Simulation, t_eval: list[float], inputs: dict[str, float] | None = None) -> float:
    t0 = time.perf_counter()
    simulation.solve(t_eval, inputs=inputs)
    return time.perf_counter() - t0


def _run_baseline_rebuild(currents_a: list[float], duration_s: float) -> list[float]:
    runtimes: list[float] = []
    for current in currents_a:
        model = pybamm.lithium_ion.DFN()
        params = model.default_parameter_values.copy()
        params.update({"Current function [A]": current})
        sim = pybamm.Simulation(model, parameter_values=params)
        runtimes.append(_solve_and_time(sim, [0.0, duration_s]))
    return runtimes


def _run_input_parameter_reuse(currents_a: list[float], duration_s: float) -> list[float]:
    model = pybamm.lithium_ion.DFN()
    params = model.default_parameter_values.copy()
    params.update({"Current function [A]": pybamm.InputParameter("Current [A]")})
    sim = pybamm.Simulation(model, parameter_values=params)
    runtimes: list[float] = []
    for current in currents_a:
        runtimes.append(_solve_and_time(sim, [0.0, duration_s], inputs={"Current [A]": current}))
    return runtimes


def _write_csv(
    path: Path,
    currents_a: list[float],
    baseline: list[float],
    input_param: list[float],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["current_a", "baseline_runtime_s", "input_parameter_runtime_s", "speedup_ratio"],
        )
        writer.writeheader()
        for current, b, i in zip(currents_a, baseline, input_param):
            ratio = b / i if i > 0 else float("inf")
            writer.writerow(
                {
                    "current_a": f"{current:.6f}",
                    "baseline_runtime_s": f"{b:.6f}",
                    "input_parameter_runtime_s": f"{i:.6f}",
                    "speedup_ratio": f"{ratio:.6f}",
                }
            )


def _write_summary(path: Path, currents_a: list[float], baseline: list[float], input_param: list[float]) -> None:
    mean_baseline = statistics.mean(baseline)
    mean_input = statistics.mean(input_param)
    speedup = mean_baseline / mean_input if mean_input > 0 else float("inf")
    lines = [
        "# Week 5 Performance Summary",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"PyBaMM version: {pybamm.__version__}",
        "",
        "## Setup",
        f"- Currents tested (A): {currents_a}",
        f"- Mean baseline runtime (s): {mean_baseline:.6f}",
        f"- Mean input-parameter runtime (s): {mean_input:.6f}",
        f"- Mean speedup ratio: {speedup:.6f}",
        "",
        "## Interpretation Prompt",
        "1. Is speedup large enough for your expected sweep scale?",
        "2. Does reuse introduce any reproducibility concern in your workflow?",
        "3. Which workloads should keep baseline rebuild for safety?",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 5 performance script using Input Parameters.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/pybamm_learning/week05"),
        help="Output directory.",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=1800.0,
        help="Simulation end time in seconds.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    currents_a = [5.0, 10.0, 15.0, 20.0]

    print("Running baseline rebuild benchmark...")
    baseline_runtimes = _run_baseline_rebuild(currents_a, args.duration_s)

    print("Running input-parameter reuse benchmark...")
    input_param_runtimes = _run_input_parameter_reuse(currents_a, args.duration_s)

    _write_csv(output_dir / "week05_perf_compare.csv", currents_a, baseline_runtimes, input_param_runtimes)
    _write_summary(output_dir / "week05_perf_summary.md", currents_a, baseline_runtimes, input_param_runtimes)

    print(f"Week 5 outputs written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
