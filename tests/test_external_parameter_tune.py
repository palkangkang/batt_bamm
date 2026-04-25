from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import pandas as pd
import yaml

from batt_bamm.external_parameter_tune import run_external_parameter_tune


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestExternalParameterTune(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"external_parameter_tune_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_example_flow_outputs(self) -> None:
        config_path = REPO_ROOT / "configs" / "setups" / "external_parameter_tune_example.yaml"
        output_dir = self._root / "example_flow"
        summary = run_external_parameter_tune(config_path, output_dir_override=output_dir)

        self.assertIn("ecm", summary)
        self.assertIn("dfn", summary)
        self.assertTrue(summary["ecm"]["attempted"])
        self.assertTrue(summary["ecm"]["passed"])
        self.assertTrue(summary["dfn"]["attempted"])
        self.assertIn("ecm_fitted_pack_json", summary["artifacts"])
        self.assertIn("dfn_fitted_config_yaml", summary["artifacts"])
        self.assertTrue((output_dir / "external_fit_summary.json").exists())
        self.assertTrue((output_dir / "fit_acceptance_report.md").exists())
        self.assertTrue((output_dir / "case_diagnostics.csv").exists())

        pack = json.loads(Path(summary["artifacts"]["ecm_fitted_pack_json"]).read_text(encoding="utf-8"))
        self.assertEqual(pack["schema_version"], "ecm_temp_2d_v1")
        self.assertIn(pack["ecm_order"], {1, 2})
        self.assertEqual(len(pack["temp_c_axis"]), 2)
        self.assertEqual(len(pack["soc_axis"]), 4)

    def test_arbitrary_waveform_config_outputs(self) -> None:
        config_path = REPO_ROOT / "configs" / "setups" / "external_parameter_tune_arbitrary_waveform.yaml"
        output_dir = self._root / "arbitrary_waveform"
        summary = run_external_parameter_tune(config_path, output_dir_override=output_dir)

        self.assertTrue(summary["ecm"]["attempted"])
        self.assertTrue(summary["ecm"]["passed"])
        self.assertTrue(summary["dfn"]["attempted"])
        self.assertEqual(summary["valid_case_count"], 1)
        self.assertEqual(summary["train_case_count"], 1)
        self.assertTrue((output_dir / "normalized_inputs" / "case_arbitrary_waveform.csv").exists())
        self.assertTrue(Path(summary["artifacts"]["ecm_fitted_pack_json"]).exists())
        self.assertTrue(Path(summary["artifacts"]["dfn_fitted_config_yaml"]).exists())

    def test_input_failure_writes_actionable_diagnostics(self) -> None:
        bad_case = self._root / "bad_case.csv"
        pd.DataFrame(
            {
                "time_s": [0.0, 1.0, 2.0],
                "current_a": [10.0, 10.0, 10.0],
            }
        ).to_csv(bad_case, index=False)
        manifest = self._root / "bad_manifest.csv"
        pd.DataFrame(
            [
                {
                    "case_id": "bad_missing_voltage",
                    "csv_path": str(bad_case),
                    "split": "train",
                    "initial_soc": 0.9,
                    "nominal_capacity_ah": 150,
                    "ambient_temp_k": 298.15,
                    "weight": 1.0,
                }
            ]
        ).to_csv(manifest, index=False)
        cfg = {
            "base_config_path": str(REPO_ROOT / "configs" / "cells" / "nmc622_150ah" / "baseline_150ah_nmc622.yaml"),
            "output_dir": str(self._root / "bad_output"),
            "target": {
                "manifest_csv": str(manifest),
                "current_sign": "discharge_positive",
                "default_temp_k": 298.15,
            },
            "fit": {
                "min_train_cases": 1,
                "models": ["ecm", "dfn"],
                "ecm": {"enabled": True, "ecm_order": 2, "fallback_to_1rc": True},
                "dfn": {"enabled": True, "run_only_if_ecm_passed": True, "max_nfev": 1},
                "thermal": {"enabled": False},
            },
        }
        cfg_path = self._root / "bad_config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_external_parameter_tune(cfg_path)
        self.assertFalse(summary["passed"])
        self.assertFalse(summary["ecm"]["attempted"])
        self.assertFalse(summary["dfn"]["attempted"])
        diagnostics = pd.read_csv(summary["artifacts"]["case_diagnostics_csv"])
        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertTrue(any("voltage" in str(message).lower() for message in diagnostics["message"]))


if __name__ == "__main__":
    unittest.main()
