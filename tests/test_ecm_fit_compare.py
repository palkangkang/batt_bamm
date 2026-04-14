from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from batt_bamm.ecm_fit_compare import (
    _evaluate_2rc_gate,
    _simulate_thevenin_voltage,
    _simulate_thevenin_voltage_2rc,
    fit_ecm_parameters_from_dfn_hppc,
    run_ecm_fit_compare_pipeline,
)
from batt_bamm.main import run_from_config


def _write_timeseries_csv(path: Path, *, duration_s: float, period_s: float, current_a: float, temp_k: float) -> None:
    times = np.arange(0.0, duration_s + period_s * 0.5, period_s)
    frame = pd.DataFrame(
        {
            "time_s": times,
            "current_a": np.full(times.shape, current_a, dtype=float),
            "temp_k": np.full(times.shape, temp_k, dtype=float),
        }
    )
    frame.to_csv(path, index=False)


class TestEcmFitCompare(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"ecm_fit_compare_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_fit_recovers_synthetic_thevenin_window(self) -> None:
        soc_target = 0.8
        time_s = np.arange(0.0, 40.0 + 0.5, 0.5)
        current = np.zeros_like(time_s)
        current[time_s < 10.0] = 20.0
        current[(time_s >= 20.0) & (time_s < 30.0)] = -20.0

        true = {"ocv_v": 3.75, "r0_ohm": 1.0e-3, "r1_ohm": 2.0e-3, "c1_f": 22000.0}
        voltage = _simulate_thevenin_voltage(
            time_s=time_s,
            current_a=current,
            ocv_v=true["ocv_v"],
            r0_ohm=true["r0_ohm"],
            r1_ohm=true["r1_ohm"],
            c1_f=true["c1_f"],
        )
        dt_h = np.diff(time_s, prepend=time_s[0]) / 3600.0
        soc = np.clip(soc_target - np.cumsum(current * dt_h / 150.0), 0.0, 1.0)

        point_csv = self._root / "synthetic_hppc_point.csv"
        pd.DataFrame(
            {
                "time_s": time_s,
                "current_a": current,
                "voltage_v": voltage,
                "ocv_v": np.full(time_s.shape, true["ocv_v"], dtype=float),
                "soc": soc,
                "temperature_k": np.full(time_s.shape, 298.15, dtype=float),
            }
        ).to_csv(point_csv, index=False)

        summary_csv = self._root / "synthetic_hppc_summary.csv"
        pd.DataFrame(
            [
                {
                    "soc_target": soc_target,
                    "passed": True,
                    "csv_path": str(point_csv),
                }
            ]
        ).to_csv(summary_csv, index=False)

        fit_dir = self._root / "fit_synthetic"
        result = fit_ecm_parameters_from_dfn_hppc(
            dfn_hppc_summary_csv=summary_csv,
            output_dir=fit_dir,
        )
        pack = json.loads(Path(result["fitted_pack_json"]).read_text(encoding="utf-8"))
        self.assertEqual(len(pack["soc"]), 1)
        self.assertTrue(np.isclose(pack["ocv_v"][0], true["ocv_v"], atol=0.03))
        self.assertTrue(np.isclose(pack["r0_ohm"][0], true["r0_ohm"], rtol=0.25))
        self.assertTrue(np.isclose(pack["r1_ohm"][0], true["r1_ohm"], rtol=0.35))
        self.assertTrue(np.isclose(pack["c1_f"][0], true["c1_f"], rtol=0.40))

    def test_fit_recovers_synthetic_thevenin_window_2rc(self) -> None:
        soc_target = 0.7
        time_s = np.arange(0.0, 40.0 + 0.2, 0.2)
        current = np.zeros_like(time_s)
        current[time_s < 10.0] = 25.0
        current[(time_s >= 20.0) & (time_s < 30.0)] = -25.0

        true = {
            "ocv_v": 3.62,
            "r0_ohm": 1.2e-3,
            "r1_ohm": 1.8e-3,
            "c1_f": 20000.0,
            "r2_ohm": 4.0e-3,
            "c2_f": 90000.0,
        }
        voltage = _simulate_thevenin_voltage_2rc(
            time_s=time_s,
            current_a=current,
            ocv_v=true["ocv_v"],
            r0_ohm=true["r0_ohm"],
            r1_ohm=true["r1_ohm"],
            c1_f=true["c1_f"],
            r2_ohm=true["r2_ohm"],
            c2_f=true["c2_f"],
        )
        dt_h = np.diff(time_s, prepend=time_s[0]) / 3600.0
        soc = np.clip(soc_target - np.cumsum(current * dt_h / 150.0), 0.0, 1.0)

        point_csv = self._root / "synthetic_hppc_point_2rc.csv"
        pd.DataFrame(
            {
                "time_s": time_s,
                "current_a": current,
                "voltage_v": voltage,
                "ocv_v": np.full(time_s.shape, true["ocv_v"], dtype=float),
                "soc": soc,
                "temperature_k": np.full(time_s.shape, 298.15, dtype=float),
            }
        ).to_csv(point_csv, index=False)

        summary_csv = self._root / "synthetic_hppc_summary_2rc.csv"
        pd.DataFrame(
            [
                {
                    "soc_target": soc_target,
                    "passed": True,
                    "csv_path": str(point_csv),
                }
            ]
        ).to_csv(summary_csv, index=False)

        fit_dir = self._root / "fit_synthetic_2rc"
        result = fit_ecm_parameters_from_dfn_hppc(
            dfn_hppc_summary_csv=summary_csv,
            output_dir=fit_dir,
            ecm_order=2,
            loss_dynamic_weight=0.7,
        )
        pack = json.loads(Path(result["fitted_pack_json"]).read_text(encoding="utf-8"))
        self.assertEqual(pack["ecm_order"], 2)
        self.assertTrue(np.isclose(pack["ocv_v"][0], true["ocv_v"], atol=0.04))
        self.assertTrue(np.isclose(pack["r0_ohm"][0], true["r0_ohm"], rtol=0.35))
        self.assertGreater(pack["r1_ohm"][0], 0.0)
        self.assertGreater(pack["c1_f"][0], 0.0)
        self.assertGreater(pack["r2_ohm"][0], 0.0)
        self.assertGreater(pack["c2_f"][0], 0.0)
        reconstructed = _simulate_thevenin_voltage_2rc(
            time_s=time_s,
            current_a=current,
            ocv_v=float(pack["ocv_v"][0]),
            r0_ohm=float(pack["r0_ohm"][0]),
            r1_ohm=float(pack["r1_ohm"][0]),
            c1_f=float(pack["c1_f"][0]),
            r2_ohm=float(pack["r2_ohm"][0]),
            c2_f=float(pack["c2_f"][0]),
        )
        mae = float(np.mean(np.abs(reconstructed - voltage)))
        self.assertLess(mae, 1e-3)

    def test_ecm_fitted_pack_integration_with_timeseries(self) -> None:
        profile_csv = self._root / "ecm_timeseries_profile.csv"
        _write_timeseries_csv(profile_csv, duration_s=20.0, period_s=1.0, current_a=5.0, temp_k=298.15)

        fitted_pack = self._root / "manual_fitted_pack.json"
        fitted_pack.write_text(
            json.dumps(
                {
                    "soc": [0.0, 0.5, 1.0],
                    "ocv_v": [3.2, 3.6, 4.1],
                    "r0_ohm": [0.001, 0.0012, 0.0015],
                    "r1_ohm": [0.0018, 0.0021, 0.0025],
                    "c1_f": [18000.0, 22000.0, 26000.0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        output_with = self._root / "ecm_timeseries_with_fitted_pack"
        cfg_with = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 0.8,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 3.2,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "ECM_Example",
            "model": {
                "type": "ecm",
                "thermal": "isothermal",
                "ecm_fitted_pack_json": str(fitted_pack),
            },
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": True, "csv_path": str(profile_csv), "charge_compare": {"enabled": False}},
            "quality_gate": {"enabled": False, "enforce": False},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "output_dir": str(output_with),
        }
        cfg_with_path = self._root / "ecm_timeseries_with_fitted_pack.yaml"
        cfg_with_path.write_text(yaml.safe_dump(cfg_with), encoding="utf-8")
        summary_with = run_from_config(cfg_with_path, mode="timeseries")
        self.assertTrue(summary_with["all_converged"])
        audit_with = json.loads(Path(summary_with["artifacts"]["parameter_audit"]).read_text(encoding="utf-8"))
        self.assertEqual(audit_with["parameter_pack"]["quality_level"], "identified")
        self.assertEqual(
            Path(audit_with["parameter_pack"]["fitted_pack_json"]).resolve(),
            fitted_pack.resolve(),
        )

        output_without = self._root / "ecm_timeseries_without_fitted_pack"
        cfg_without = json.loads(json.dumps(cfg_with))
        cfg_without["model"].pop("ecm_fitted_pack_json")
        cfg_without["output_dir"] = str(output_without)
        cfg_without_path = self._root / "ecm_timeseries_without_fitted_pack.yaml"
        cfg_without_path.write_text(yaml.safe_dump(cfg_without), encoding="utf-8")
        summary_without = run_from_config(cfg_without_path, mode="timeseries")
        self.assertTrue(summary_without["all_converged"])
        audit_without = json.loads(Path(summary_without["artifacts"]["parameter_audit"]).read_text(encoding="utf-8"))
        self.assertEqual(audit_without["parameter_pack"]["quality_level"], "proxy")

    def test_ecm_2rc_fitted_pack_integration_with_timeseries(self) -> None:
        profile_csv = self._root / "ecm_timeseries_profile_2rc.csv"
        _write_timeseries_csv(profile_csv, duration_s=15.0, period_s=0.5, current_a=6.0, temp_k=298.15)

        fitted_pack = self._root / "manual_fitted_pack_2rc.json"
        fitted_pack.write_text(
            json.dumps(
                {
                    "ecm_order": 2,
                    "soc": [0.0, 0.5, 1.0],
                    "ocv_v": [3.1, 3.45, 3.95],
                    "r0_ohm": [0.0012, 0.0015, 0.0018],
                    "r1_ohm": [0.0020, 0.0025, 0.0030],
                    "c1_f": [18000.0, 22000.0, 26000.0],
                    "r2_ohm": [0.0035, 0.0040, 0.0045],
                    "c2_f": [80000.0, 90000.0, 100000.0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        output_dir = self._root / "ecm_timeseries_with_fitted_pack_2rc"
        cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 0.75,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 3.0,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "ECM_Example",
            "model": {
                "type": "ecm",
                "thermal": "isothermal",
                "ecm_rc_elements": 2,
                "ecm_fitted_pack_json": str(fitted_pack),
            },
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": True, "csv_path": str(profile_csv), "charge_compare": {"enabled": False}},
            "quality_gate": {"enabled": False, "enforce": False},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "output_dir": str(output_dir),
        }
        cfg_path = self._root / "ecm_timeseries_with_fitted_pack_2rc.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        audit = json.loads(Path(summary["artifacts"]["parameter_audit"]).read_text(encoding="utf-8"))
        self.assertEqual(audit["parameter_pack"]["quality_level"], "identified")
        self.assertEqual(audit["parameter_pack"]["ecm_rc_elements"], 2)

    def test_fit_compare_pipeline_smoke_outputs(self) -> None:
        dfn_cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
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
                "soc_end": 0.89,
                "soc_step": 0.05,
                "pulse_c_rate": 0.2,
                "discharge_s": 4,
                "charge_s": 4,
                "rest_minutes": 0.1,
                "period_s": 0.2,
            },
            "timeseries": {"enabled": False},
            "output_dir": str(self._root / "unused_dfn"),
        }
        ecm_cfg = json.loads(json.dumps(dfn_cfg))
        ecm_cfg["model"] = {"type": "ecm", "thermal": "isothermal"}
        ecm_cfg["parameter_set"] = "ECM_Example"
        ecm_cfg["voltage_low_v"] = 3.2
        ecm_cfg["output_dir"] = str(self._root / "unused_ecm")

        dfn_cfg_path = self._root / "fit_compare_dfn.yaml"
        ecm_cfg_path = self._root / "fit_compare_ecm.yaml"
        dfn_cfg_path.write_text(yaml.safe_dump(dfn_cfg), encoding="utf-8")
        ecm_cfg_path.write_text(yaml.safe_dump(ecm_cfg), encoding="utf-8")

        output_ok = self._root / "fit_compare_ok"
        summary_ok = run_ecm_fit_compare_pipeline(
            dfn_config_path=dfn_cfg_path,
            ecm_config_path=ecm_cfg_path,
            output_dir=output_ok,
            improve_threshold=-1.0,
            cell_id="nmc150_fit_compare_smoke",
        )
        self.assertIn("passed", summary_ok)
        self.assertIn("threshold", summary_ok)
        self.assertIn("baseline_metrics", summary_ok)
        self.assertIn("optimized_metrics", summary_ok)
        self.assertIn("coverage", summary_ok)
        self.assertIn("artifacts", summary_ok)
        self.assertTrue((output_ok / "fit" / "ecm_fitted_pack.json").exists())
        self.assertTrue((output_ok / "fit" / "ecm_fit_points.csv").exists())
        self.assertTrue((output_ok / "compare_before" / "hppc_compare_summary.json").exists())
        after_summary_json = summary_ok["artifacts"].get("compare_after_summary_json")
        if isinstance(after_summary_json, str):
            self.assertTrue(Path(after_summary_json).exists())
        self.assertTrue((output_ok / "ecm_fit_compare_summary.json").exists())
        self.assertTrue((output_ok / "ecm_fit_compare_report.md").exists())

    def test_fit_compare_pipeline_2rc_outputs(self) -> None:
        dfn_cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
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
                "soc_end": 0.95,
                "soc_step": 0.02,
                "pulse_c_rate": 0.2,
                "discharge_s": 4,
                "charge_s": 4,
                "rest_minutes": 0.1,
                "period_s": 0.2,
            },
            "timeseries": {"enabled": False},
            "output_dir": str(self._root / "unused_dfn_2rc"),
        }
        ecm_cfg = json.loads(json.dumps(dfn_cfg))
        ecm_cfg["model"] = {"type": "ecm", "thermal": "isothermal", "ecm_rc_elements": 2}
        ecm_cfg["parameter_set"] = "ECM_Example"
        ecm_cfg["voltage_low_v"] = 3.2
        ecm_cfg["output_dir"] = str(self._root / "unused_ecm_2rc")

        dfn_cfg_path = self._root / "fit_compare_dfn_2rc.yaml"
        ecm_cfg_path = self._root / "fit_compare_ecm_2rc.yaml"
        dfn_cfg_path.write_text(yaml.safe_dump(dfn_cfg), encoding="utf-8")
        ecm_cfg_path.write_text(yaml.safe_dump(ecm_cfg), encoding="utf-8")

        output_ok = self._root / "fit_compare_ok_2rc"
        summary_ok = run_ecm_fit_compare_pipeline(
            dfn_config_path=dfn_cfg_path,
            ecm_config_path=ecm_cfg_path,
            output_dir=output_ok,
            improve_threshold=-1.0,
            ecm_order=2,
            loss_dynamic_weight=0.7,
            gate_profile="off",
            cell_id="nmc150_fit_compare_smoke_2rc",
        )
        self.assertIn("ecm_order", summary_ok)
        self.assertEqual(summary_ok["ecm_order"], 2)
        self.assertIn("metrics_static_dynamic", summary_ok)
        self.assertTrue((output_ok / "fit" / "ecm_fitted_pack_2rc.json").exists())
        self.assertTrue((output_ok / "fit" / "ecm_fit_points_2rc.csv").exists())
        self.assertTrue((output_ok / "ecm_fit_compare_summary_2rc.json").exists())
        self.assertTrue((output_ok / "ecm_fit_compare_report_2rc.md").exists())

    def test_gate_2rc_failure_payload(self) -> None:
        gate = _evaluate_2rc_gate(
            {
                "mae_static_v": 0.006,
                "mae_dynamic_v": 0.025,
                "p95_dynamic_v": 0.040,
            },
            {
                "mae_static_mv": 5.0,
                "mae_dynamic_mv": 20.0,
                "p95_dynamic_mv": 30.0,
            },
        )
        self.assertFalse(gate["passed"])
        self.assertTrue(len(gate["failures"]) >= 1)


if __name__ == "__main__":
    unittest.main()
