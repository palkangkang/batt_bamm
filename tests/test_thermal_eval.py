from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import pandas as pd
import yaml

from batt_bamm.main import _build_parameter_values, load_config
from batt_bamm.thermal_eval import run_thermal_eval


def _base_config(output_dir: Path) -> dict:
    return {
        "chemistry": "nmc",
        "nominal_capacity_ah": 150,
        "initial_soc": 1.0,
        "ambient_temp_k": 298.15,
        "voltage_low_v": 2.8,
        "voltage_high_v": 4.2,
        "discharge_rates_c": [0.5],
        "charge_cc_rate": 0.5,
        "cv_cutoff_c_rate": 0.05,
        "rest_min": 5,
        "period_s": 30,
        "parameter_set": "Chen2020",
        "model": {
            "type": "dfn",
            "thermal": "lumped",
            "thermal_coupling": {"enabled": False, "boundary_mode": "constant"},
            "thermal_params": {
                "total_heat_transfer_coefficient_w_m2_k": 120.0,
                "cell_volume_m3": 0.000726,
                "cell_cooling_surface_area_m2": 0.0512674863,
            },
        },
        "solver": {"rtol": 1e-6, "atol": 1e-8},
        "sanity_gate": {"enabled": False},
        "hppc": {"enabled": False},
        "timeseries": {"enabled": False, "csv_path": ""},
        "quality_gate": {"enabled": False, "enforce": False},
        "benchmark": {"enabled": False},
        "identification_inputs": {"enabled": False, "strict": True},
        "termination": {
            "enabled": True,
            "logic": "any_of",
            "must_hit": False,
            "apply_to_experiment_modes": True,
            "conditions": [],
        },
        "output_dir": str(output_dir),
    }


class TestThermalEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"thermal_eval_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_initial_cell_temperature_fallback_and_override(self) -> None:
        base_path = self._root / "base_initial_temp.yaml"
        base_payload = _base_config(self._root / "out_initial_temp")
        base_path.write_text(yaml.safe_dump(base_payload), encoding="utf-8")
        cfg = load_config(base_path)
        self.assertEqual(cfg.initial_cell_temp_k, cfg.ambient_temp_k)
        values, _ = _build_parameter_values(cfg)
        self.assertAlmostEqual(float(values["Initial temperature [K]"]), cfg.ambient_temp_k, places=9)

        override_path = self._root / "base_initial_temp_override.yaml"
        override_payload = json.loads(json.dumps(base_payload))
        override_payload["initial_cell_temp_k"] = 283.15
        override_path.write_text(yaml.safe_dump(override_payload), encoding="utf-8")
        cfg_override = load_config(override_path)
        self.assertAlmostEqual(cfg_override.initial_cell_temp_k, 283.15, places=9)
        values_override, _ = _build_parameter_values(cfg_override)
        self.assertAlmostEqual(float(values_override["Initial temperature [K]"]), 283.15, places=9)

    def test_initial_cell_temperature_invalid_fail_fast(self) -> None:
        invalid_path = self._root / "base_initial_temp_invalid.yaml"
        payload = _base_config(self._root / "out_invalid")
        payload["initial_cell_temp_k"] = 0
        invalid_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_config(invalid_path)
        self.assertIn("initial_cell_temp_k", str(ctx.exception))

    def test_repo_lfp_baseline_has_thermal_adaptation(self) -> None:
        cfg_path = Path("configs/cells/lfp_130ah/baseline_130ah_lfp.yaml").resolve()
        cfg = load_config(cfg_path)
        self.assertEqual(cfg.model_type, "dfn")
        self.assertEqual(cfg.thermal, "lumped")
        self.assertAlmostEqual(float(cfg.thermal_params.total_heat_transfer_coefficient_w_m2_k), 120.0, places=12)
        self.assertAlmostEqual(float(cfg.thermal_params.cell_volume_m3), 0.0006292, places=12)
        self.assertAlmostEqual(float(cfg.thermal_params.cell_cooling_surface_area_m2), 0.0466025934722914, places=12)
        metrics = {cond.metric for cond in cfg.termination.conditions}
        self.assertIn("cell_temperature_k", metrics)
        self.assertIn("boundary_temperature_k", metrics)
        _, scaling = _build_parameter_values(cfg)
        proxy_overrides = scaling.get("lfp_lumped_thermal_proxy_overrides", [])
        self.assertGreaterEqual(len(proxy_overrides), 1)
        proxy_keys = {row["parameter"] for row in proxy_overrides}
        self.assertIn("Negative current collector thickness [m]", proxy_keys)
        self.assertIn("Positive current collector conductivity [S.m-1]", proxy_keys)

    def test_thermal_eval_smoke_outputs(self) -> None:
        base_path = self._root / "base_smoke.yaml"
        base_payload = _base_config(self._root / "out_base_smoke")
        base_path.write_text(yaml.safe_dump(base_payload), encoding="utf-8")

        eval_output = self._root / "thermal_eval_smoke_outputs"
        eval_cfg = {
            "base_config_path": str(base_path),
            "sampling_period_s": 1.0,
            "output_dir": str(eval_output),
            "cases": [
                {
                    "case_id": "smoke_discharge",
                    "ambient_temp_c": 25,
                    "initial_cell_temp_c": 25,
                    "soc_start": 1.0,
                    "soc_end": 0.98,
                    "rate_c": 1.0,
                },
                {
                    "case_id": "smoke_charge",
                    "ambient_temp_c": 25,
                    "initial_cell_temp_c": 10,
                    "soc_start": 0.0,
                    "soc_end": 0.02,
                    "rate_c": 1.0,
                },
            ],
        }
        eval_cfg_path = self._root / "thermal_eval_smoke.yaml"
        eval_cfg_path.write_text(yaml.safe_dump(eval_cfg), encoding="utf-8")

        summary = run_thermal_eval(eval_cfg_path)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary.get("temperature_unit"), "C")
        self.assertTrue((eval_output / "thermal_eval_summary.csv").exists())
        self.assertTrue((eval_output / "thermal_eval_summary.json").exists())
        self.assertTrue((eval_output / "thermal_eval_manifest.json").exists())
        self.assertTrue((eval_output / "thermal_eval_temperature_overlay.png").exists())
        summary_csv = pd.read_csv(eval_output / "thermal_eval_summary.csv")
        self.assertIn("ambient_temp_c", summary_csv.columns)
        self.assertIn("initial_cell_temp_c", summary_csv.columns)
        self.assertIn("max_cell_temperature_c", summary_csv.columns)
        self.assertIn("max_boundary_temperature_c", summary_csv.columns)
        self.assertNotIn("ambient_temp_k", summary_csv.columns)
        self.assertNotIn("initial_cell_temp_k", summary_csv.columns)
        self.assertNotIn("max_cell_temperature_k", summary_csv.columns)
        self.assertNotIn("max_boundary_temperature_k", summary_csv.columns)

        summary_row = summary["cases"][0]
        self.assertIn("ambient_temp_c", summary_row)
        self.assertIn("initial_cell_temp_c", summary_row)
        self.assertIn("max_cell_temperature_c", summary_row)
        self.assertIn("max_boundary_temperature_c", summary_row)
        self.assertNotIn("ambient_temp_k", summary_row)
        self.assertNotIn("initial_cell_temp_k", summary_row)
        self.assertNotIn("max_cell_temperature_k", summary_row)
        self.assertNotIn("max_boundary_temperature_k", summary_row)

        for case_id in ("smoke_discharge", "smoke_charge"):
            csv_path = eval_output / f"thermal_case_{case_id}.csv"
            self.assertTrue(csv_path.exists())
            frame = pd.read_csv(csv_path)
            self.assertEqual(
                list(frame.columns),
                ["time_s", "current_a", "voltage_v", "soc", "cell_temperature_k", "boundary_temperature_k"],
            )

    def test_thermal_eval_early_termination_kept_as_valid_output(self) -> None:
        base_path = self._root / "base_early_term.yaml"
        base_payload = _base_config(self._root / "out_base_early")
        base_payload["voltage_low_v"] = 4.19
        base_path.write_text(yaml.safe_dump(base_payload), encoding="utf-8")

        eval_output = self._root / "thermal_eval_early_outputs"
        eval_cfg = {
            "base_config_path": str(base_path),
            "sampling_period_s": 1.0,
            "output_dir": str(eval_output),
            "cases": [
                {
                    "case_id": "early_voltage_stop",
                    "ambient_temp_c": 25,
                    "initial_cell_temp_c": 25,
                    "soc_start": 1.0,
                    "soc_end": 0.0,
                    "rate_c": 0.5,
                }
            ],
        }
        eval_cfg_path = self._root / "thermal_eval_early.yaml"
        eval_cfg_path.write_text(yaml.safe_dump(eval_cfg), encoding="utf-8")

        summary = run_thermal_eval(eval_cfg_path)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["total_cases"], 1)
        self.assertEqual(summary.get("temperature_unit"), "C")
        row = summary["cases"][0]
        self.assertTrue(row["termination_hit"])
        self.assertEqual(row["termination_metric"], "voltage_v")
        self.assertGreater(float(row["final_soc"]), 0.0)


if __name__ == "__main__":
    unittest.main()
