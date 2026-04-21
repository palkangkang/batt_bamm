from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import pybamm


def _extract_time_and_voltage(solution: pybamm.Solution) -> tuple[list[float], list[float], str]:
    candidates = ["Terminal voltage [V]", "Voltage [V]", "Battery voltage [V]"]
    time_s = solution["Time [s]"].entries.tolist()
    for name in candidates:
        try:
            voltage_v = solution[name].entries.tolist()
            return time_s, voltage_v, name
        except KeyError:
            continue
    raise KeyError(f"No voltage variable found in {candidates}")


def _build_models() -> dict[str, pybamm.BaseModel]:
    return {
        "SPM": pybamm.lithium_ion.SPM(),
        "SPMe": pybamm.lithium_ion.SPMe(),
        "DFN": pybamm.lithium_ion.DFN(),
    }


def _run_model(model_name: str, model: pybamm.BaseModel) -> dict[str, object]:
    experiment = pybamm.Experiment(
        [
            "Discharge at 1C until 3.2 V",
            "Rest for 10 minutes",
            "Charge at 0.5C until 4.1 V",
            "Hold at 4.1 V until C/20",
        ]
    )
    simulation = pybamm.Simulation(model, experiment=experiment)
    t0 = time.perf_counter()
    solution = simulation.solve()
    runtime_s = time.perf_counter() - t0
    time_s, voltage_v, voltage_var_name = _extract_time_and_voltage(solution)
    final_voltage_v = float(voltage_v[-1]) if voltage_v else None
    min_voltage_v = float(min(voltage_v)) if voltage_v else None
    return {
        "model": model_name,
        "runtime_s": runtime_s,
        "final_voltage_v": final_voltage_v,
        "min_voltage_v": min_voltage_v,
        "time_s": time_s,
        "voltage_v": voltage_v,
        "voltage_var_name": voltage_var_name,
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "runtime_s", "final_voltage_v", "min_voltage_v"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "model": row["model"],
                    "runtime_s": f"{float(row['runtime_s']):.6f}",
                    "final_voltage_v": f"{float(row['final_voltage_v']):.6f}",
                    "min_voltage_v": f"{float(row['min_voltage_v']):.6f}",
                }
            )


def _write_plot(path: Path, rows: list[dict[str, object]]) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    for row in rows:
        ax.plot(row["time_s"], row["voltage_v"], label=row["model"], linewidth=1.6)
    ax.set_title("Week 2 Model Compare: SPM vs SPMe vs DFN")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Voltage [V]")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _write_conclusions(path: Path, rows: list[dict[str, object]]) -> None:
    sorted_runtime = sorted(rows, key=lambda r: float(r["runtime_s"]))
    fastest = sorted_runtime[0]["model"]
    slowest = sorted_runtime[-1]["model"]
    content = f"""# Week 2 Conclusions

Generated at: {datetime.now(timezone.utc).isoformat()}

## Evidence Files

- CSV: `week02_model_compare.csv`
- Plot: `week02_model_compare_voltage.png`

## Initial Observation

- Fastest runtime model in this run: `{fastest}`
- Slowest runtime model in this run: `{slowest}`

## Fill-in Analysis

1. Which model had best speed-to-fidelity balance for your use case?
2. Did curve differences affect your interpretation?
3. Which model would you choose for parameter sweeps and why?
"""
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 2 model comparison runner.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/pybamm_learning/week02"),
        help="Output directory.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for name, model in _build_models().items():
        print(f"Running {name}...")
        results.append(_run_model(name, model))

    _write_csv(output_dir / "week02_model_compare.csv", results)
    _write_plot(output_dir / "week02_model_compare_voltage.png", results)
    _write_conclusions(output_dir / "week02_conclusions.md", results)

    print(f"Week 2 outputs written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
