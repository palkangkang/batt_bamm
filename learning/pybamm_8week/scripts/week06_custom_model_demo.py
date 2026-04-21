from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pybamm


def _build_custom_ode_model() -> tuple[pybamm.BaseModel, pybamm.ParameterValues]:
    model = pybamm.BaseModel(name="week06_single_state_decay")
    x = pybamm.Variable("x")
    k = pybamm.Parameter("k")
    model.rhs = {x: -k * x}
    model.initial_conditions = {x: pybamm.Scalar(1.0)}
    model.variables = {"x": x}
    params = pybamm.ParameterValues({"k": 1.0 / 1800.0})
    return model, params


def _extract_voltage(solution: pybamm.Solution) -> tuple[np.ndarray, np.ndarray]:
    candidates = ["Terminal voltage [V]", "Voltage [V]", "Battery voltage [V]"]
    time_s = solution["Time [s]"].entries
    for name in candidates:
        try:
            voltage_v = solution[name].entries
            return np.asarray(time_s, dtype=float), np.asarray(voltage_v, dtype=float)
        except KeyError:
            continue
    raise KeyError("No voltage variable found for built-in model.")


def _write_csv(path: Path, time_s: np.ndarray, custom_x: np.ndarray, spm_voltage_interp: np.ndarray) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["time_s", "custom_state_x", "spm_voltage_v_interp"])
        writer.writeheader()
        for t, x, v in zip(time_s, custom_x, spm_voltage_interp):
            writer.writerow(
                {
                    "time_s": f"{float(t):.6f}",
                    "custom_state_x": f"{float(x):.6f}",
                    "spm_voltage_v_interp": f"{float(v):.6f}",
                }
            )


def _write_plot(path: Path, time_s: np.ndarray, custom_x: np.ndarray, spm_voltage_interp: np.ndarray) -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), dpi=140, sharex=True)
    ax1.plot(time_s, custom_x, color="tab:blue", linewidth=1.8)
    ax1.set_ylabel("Custom state x [-]")
    ax1.set_title("Week 6 Custom ODE Output")
    ax1.grid(True, alpha=0.3)

    ax2.plot(time_s, spm_voltage_interp, color="tab:orange", linewidth=1.8)
    ax2.set_xlabel("Time [s]")
    ax2.set_ylabel("SPM voltage [V]")
    ax2.set_title("Built-in SPM Reference (Interpolated)")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _write_notes(path: Path) -> None:
    text = f"""# Week 6 Compare Notes

Generated at: {datetime.now(timezone.utc).isoformat()}

## Purpose

This script demonstrates:

1. How to define a custom ODE model in PyBaMM.
2. How to solve it with `Simulation`.
3. How to compare trend behavior against one built-in battery model output.

## Important Caution

The custom state `x` is not a physical battery voltage quantity.
The comparison is trend-level only to validate modeling workflow mechanics.
"""
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 6 custom model extension demo.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/pybamm_learning/week06"),
        help="Output directory.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    custom_model, custom_params = _build_custom_ode_model()
    custom_sim = pybamm.Simulation(custom_model, parameter_values=custom_params)
    t_eval = np.linspace(0.0, 3600.0, 240)
    custom_solution = custom_sim.solve(t_eval=t_eval)
    custom_x = np.asarray(custom_solution["x"].entries, dtype=float)
    custom_t = np.asarray(custom_solution["Time [s]"].entries, dtype=float)

    spm_model = pybamm.lithium_ion.SPM()
    spm_sim = pybamm.Simulation(spm_model)
    spm_solution = spm_sim.solve([0.0, 3600.0])
    spm_t, spm_v = _extract_voltage(spm_solution)
    spm_v_interp = np.interp(custom_t, spm_t, spm_v)

    _write_csv(output_dir / "week06_custom_model_output.csv", custom_t, custom_x, spm_v_interp)
    _write_plot(output_dir / "week06_custom_model_plot.png", custom_t, custom_x, spm_v_interp)
    _write_notes(output_dir / "week06_compare_notes.md")

    print(f"Week 6 outputs written to: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
