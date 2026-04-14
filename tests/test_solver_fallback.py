from __future__ import annotations

import time
import unittest
from pathlib import Path

import yaml

from batt_bamm.main import run_from_config


class TestDfnHppcSolverFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"solver_fallback_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)
        cls._output_dir = cls._root / "outputs"

        cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 30,
            "period_s": 30,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "quality_gate": {"enabled": False, "enforce": False},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "sanity_gate": {"enabled": False},
            "hppc": {
                "enabled": True,
                "soc_start": 0.99,
                "soc_end": 0.99,
                "soc_step": 0.05,
                "pulse_c_rate": 1.0,
                "discharge_s": 10,
                "charge_s": 5,
                "rest_minutes": 30,
                "period_s": 1.0,
            },
            "timeseries": {"enabled": False},
            "output_dir": str(cls._output_dir),
        }
        cfg_path = cls._root / "dfn_hppc_fallback.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cfg_path, mode="hppc")

    def test_idaklu_fallback_allows_completion(self) -> None:
        summary = self._summary
        self.assertTrue(summary["all_converged"])
        hppc = summary["hppc"]
        self.assertTrue(hppc["passed"])
        self.assertEqual(hppc["completed_points"], 1)
        self.assertEqual(hppc["total_points"], 1)
        point = hppc["points"][0]
        self.assertTrue(point["passed"])
        warnings = [str(msg) for msg in point.get("warning_messages", [])]
        self.assertTrue(any("retrying with CasadiSolver" in msg for msg in warnings))


if __name__ == "__main__":
    unittest.main()


class TestEcmHppcReplayFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"solver_fallback_ecm_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)
        cls._output_dir = cls._root / "outputs"

        cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 3.2,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 30,
            "period_s": 30,
            "parameter_set": "ECM_Example",
            "model": {"type": "ecm", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "quality_gate": {"enabled": False, "enforce": False},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "sanity_gate": {"enabled": False},
            "hppc": {
                "enabled": True,
                "soc_start": 0.99,
                "soc_end": 0.99,
                "soc_step": 0.05,
                "pulse_c_rate": 1.0,
                "discharge_s": 10,
                "charge_s": 5,
                "rest_minutes": 30,
                "period_s": 1.0,
            },
            "timeseries": {"enabled": False},
            "output_dir": str(cls._output_dir),
        }
        cfg_path = cls._root / "ecm_hppc_fallback.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cfg_path, mode="hppc")

    def test_ecm_early_termination_fallback_allows_completion(self) -> None:
        summary = self._summary
        self.assertTrue(summary["all_converged"])
        hppc = summary["hppc"]
        self.assertTrue(hppc["passed"])
        self.assertEqual(hppc["completed_points"], 1)
        self.assertEqual(hppc["total_points"], 1)
        point = hppc["points"][0]
        self.assertTrue(point["passed"])
        self.assertIsNone(point["error"])
        warnings = [str(msg) for msg in point.get("warning_messages", [])]
        if warnings:
            self.assertTrue(
                any("terminated early in replay mode" in msg or "retrying with CasadiSolver" in msg for msg in warnings)
            )
