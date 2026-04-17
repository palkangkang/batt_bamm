from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import yaml

from batt_bamm.lfp_thermal_identify import load_identify_config, run_lfp_thermal_identification


def _base_lfp_config(output_dir: Path) -> dict:
    return {
        "chemistry": "lfp",
        "nominal_capacity_ah": 130,
        "initial_soc": 1.0,
        "ambient_temp_k": 298.15,
        "voltage_low_v": 2.5,
        "voltage_high_v": 3.6,
        "discharge_rates_c": [0.5],
        "charge_cc_rate": 0.5,
        "cv_cutoff_c_rate": 0.05,
        "rest_min": 5,
        "period_s": 30,
        "parameter_set": "Prada2013",
        "model": {
            "type": "dfn",
            "thermal": "lumped",
            "thermal_coupling": {"enabled": False, "boundary_mode": "constant"},
            "thermal_params": {
                "total_heat_transfer_coefficient_w_m2_k": 120.0,
                "cell_volume_m3": 0.0006292,
                "cell_cooling_surface_area_m2": 0.0466025934722914,
            },
            "thermal_property_scales": {
                "heat_capacity_scale": 1.0,
                "thermal_conductivity_scale": 1.0,
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


class TestLfpThermalIdentify(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"lfp_thermal_ident_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_identify_round_smoke(self) -> None:
        base_cfg_path = self._root / "base_lfp.yaml"
        base_cfg_path.write_text(yaml.safe_dump(_base_lfp_config(self._root / "base_out")), encoding="utf-8")

        short_eval_path = self._root / "short_eval.yaml"
        short_eval_path.write_text(
            yaml.safe_dump(
                {
                    "base_config_path": str(base_cfg_path),
                    "sampling_period_s": 1.0,
                    "output_dir": str(self._root / "short_target"),
                    "cases": [
                        {
                            "case_id": "short_case",
                            "ambient_temp_c": 25,
                            "initial_cell_temp_c": 25,
                            "soc_start": 1.0,
                            "soc_end": 0.995,
                            "rate_c": 0.5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        long_eval_path = self._root / "long_eval.yaml"
        long_eval_path.write_text(
            yaml.safe_dump(
                {
                    "base_config_path": str(base_cfg_path),
                    "sampling_period_s": 1.0,
                    "output_dir": str(self._root / "long_target"),
                    "cases": [
                        {
                            "case_id": "long_case",
                            "ambient_temp_c": 25,
                            "initial_cell_temp_c": 25,
                            "soc_start": 1.0,
                            "soc_end": 0.99,
                            "rate_c": 0.5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        identify_cfg_path = self._root / "identify.yaml"
        identify_cfg_path.write_text(
            yaml.safe_dump(
                {
                    "base_config_path": str(base_cfg_path),
                    "short_eval_config_path": str(short_eval_path),
                    "long_eval_config_path": str(long_eval_path),
                    "output_dir": str(self._root / "identify_out"),
                    "target": {
                        "short_summary_json": str(self._root / "short_target" / "thermal_eval_summary.json"),
                        "long_summary_json": str(self._root / "long_target" / "thermal_eval_summary.json"),
                        "bootstrap_if_missing": True,
                    },
                    "fit": {
                        "max_nfev": 3,
                        "initial_guess": {
                            "total_heat_transfer_coefficient_w_m2_k": 120.0,
                            "heat_capacity_scale": 1.0,
                            "thermal_conductivity_scale": 1.0,
                        },
                        "lower_bounds": {
                            "total_heat_transfer_coefficient_w_m2_k": 50.0,
                            "heat_capacity_scale": 0.8,
                            "thermal_conductivity_scale": 0.8,
                        },
                        "upper_bounds": {
                            "total_heat_transfer_coefficient_w_m2_k": 200.0,
                            "heat_capacity_scale": 1.2,
                            "thermal_conductivity_scale": 1.2,
                        },
                        "weights": {"cell_temperature": 1.0, "boundary_temperature": 0.2},
                    },
                    "output": {"write_round_config": False},
                }
            ),
            encoding="utf-8",
        )

        parsed = load_identify_config(identify_cfg_path)
        self.assertTrue(parsed.bootstrap_target_if_missing)
        self.assertEqual(parsed.max_nfev, 3)

        summary = run_lfp_thermal_identification(identify_cfg_path)
        self.assertIn("best_params", summary)
        self.assertIn("short_best_metrics", summary)
        self.assertIn("long_best_metrics", summary)
        artifacts = summary.get("artifacts", {})
        self.assertTrue(Path(artifacts["summary_json"]).exists())
        self.assertTrue(Path(artifacts["trials_csv"]).exists())
        self.assertTrue(Path(artifacts["short_best_summary_json"]).exists())
        self.assertTrue(Path(artifacts["long_best_summary_json"]).exists())
        self.assertIsNone(artifacts.get("round_config_yaml"))

        loaded_summary = json.loads(Path(artifacts["summary_json"]).read_text(encoding="utf-8"))
        self.assertIn("fit_result", loaded_summary)


if __name__ == "__main__":
    unittest.main()
