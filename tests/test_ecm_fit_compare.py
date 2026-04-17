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


def _manual_temp_2d_pack(*, ecm_order: int = 1) -> dict:
    pack = {
        "schema_version": "ecm_temp_2d_v1",
        "ecm_order": ecm_order,
        "soc_axis": [0.0, 0.5, 1.0],
        "temp_c_axis": [-10.0, 25.0, 45.0],
        "ocv_v": [3.2, 3.6, 4.05],
        "r0_ohm_map": [
            [0.0022, 0.0019, 0.0016],
            [0.0015, 0.0013, 0.0011],
            [0.0012, 0.0010, 0.0009],
        ],
        "r1_ohm_map": [
            [0.0040, 0.0035, 0.0030],
            [0.0027, 0.0023, 0.0020],
            [0.0023, 0.0020, 0.0017],
        ],
        "c1_f_map": [
            [12000.0, 15000.0, 18000.0],
            [18000.0, 22000.0, 26000.0],
            [22000.0, 26000.0, 30000.0],
        ],
    }
    if ecm_order == 2:
        pack["r2_ohm_map"] = [
            [0.0055, 0.0048, 0.0042],
            [0.0042, 0.0036, 0.0030],
            [0.0036, 0.0030, 0.0026],
        ]
        pack["c2_f_map"] = [
            [60000.0, 70000.0, 80000.0],
            [80000.0, 95000.0, 110000.0],
            [95000.0, 110000.0, 125000.0],
        ]
    return pack


def _temp_dependent_thevenin_params(temp_c: float) -> dict[str, float]:
    factor = 1.0 + 0.01 * (25.0 - float(temp_c))
    return {
        "r0_ohm": 1.1e-3 * factor,
        "r1_ohm": 2.1e-3 * factor,
        "c1_f": 22000.0 / factor,
        "r2_ohm": 3.8e-3 * factor,
        "c2_f": 90000.0 / factor,
    }


def _build_synthetic_hppc_summary_for_temperature(
    root: Path, *, temp_c: float, soc_targets: list[float], ecm_order: int
) -> Path:
    temp_tag = str(temp_c).replace("-", "m").replace(".", "p")
    summary_rows = []
    time_s = np.arange(0.0, 40.0 + 0.2, 0.2)
    current = np.zeros_like(time_s)
    current[time_s < 10.0] = 22.0
    current[(time_s >= 20.0) & (time_s < 30.0)] = -22.0
    params = _temp_dependent_thevenin_params(temp_c)

    for soc_target in soc_targets:
        ocv_v = 3.55 + 0.55 * (soc_target - 0.3)
        if ecm_order == 1:
            voltage = _simulate_thevenin_voltage(
                time_s=time_s,
                current_a=current,
                ocv_v=ocv_v,
                r0_ohm=params["r0_ohm"],
                r1_ohm=params["r1_ohm"],
                c1_f=params["c1_f"],
            )
        else:
            voltage = _simulate_thevenin_voltage_2rc(
                time_s=time_s,
                current_a=current,
                ocv_v=ocv_v,
                r0_ohm=params["r0_ohm"],
                r1_ohm=params["r1_ohm"],
                c1_f=params["c1_f"],
                r2_ohm=params["r2_ohm"],
                c2_f=params["c2_f"],
            )
        dt_h = np.diff(time_s, prepend=time_s[0]) / 3600.0
        soc = np.clip(soc_target - np.cumsum(current * dt_h / 150.0), 0.0, 1.0)
        point_csv = root / f"synthetic_point_t{temp_tag}_soc{int(round(soc_target * 100)):03d}.csv"
        pd.DataFrame(
            {
                "time_s": time_s,
                "current_a": current,
                "voltage_v": voltage,
                "ocv_v": np.full(time_s.shape, ocv_v, dtype=float),
                "soc": soc,
                "cell_temperature_k": np.full(time_s.shape, temp_c + 273.15, dtype=float),
                "boundary_temperature_k": np.full(time_s.shape, temp_c + 273.15, dtype=float),
            }
        ).to_csv(point_csv, index=False)
        summary_rows.append(
            {
                "soc_target": float(soc_target),
                "passed": True,
                "csv_path": str(point_csv),
            }
        )

    summary_csv = root / f"synthetic_summary_t{temp_tag}.csv"
    pd.DataFrame(summary_rows).to_csv(summary_csv, index=False)
    return summary_csv


class TestEcmFitCompare(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"ecm_fit_compare_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_fit_recovers_multi_temperature_pack_1rc(self) -> None:
        summaries = {
            -10.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=-10.0, soc_targets=[0.8, 0.6], ecm_order=1
            ),
            25.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=25.0, soc_targets=[0.8, 0.6], ecm_order=1
            ),
            45.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=45.0, soc_targets=[0.8, 0.6], ecm_order=1
            ),
        }
        fit_dir = self._root / "fit_synthetic_temp_1rc"
        result = fit_ecm_parameters_from_dfn_hppc(
            dfn_hppc_summary_csv_by_temp_c=summaries,
            output_dir=fit_dir,
            ecm_order=1,
            loss_dynamic_weight=0.7,
        )
        pack = json.loads(Path(result["fitted_pack_json"]).read_text(encoding="utf-8"))
        self.assertEqual(pack["schema_version"], "ecm_temp_2d_v1")
        self.assertEqual(pack["ecm_order"], 1)
        self.assertEqual(pack["temp_c_axis"], [-10.0, 25.0, 45.0])
        self.assertEqual(len(pack["soc_axis"]), 2)
        r0_map = np.asarray(pack["r0_ohm_map"], dtype=float)
        self.assertEqual(r0_map.shape, (3, 2))
        self.assertTrue(np.all(r0_map[0, :] > r0_map[-1, :]))

    def test_fit_recovers_multi_temperature_pack_2rc(self) -> None:
        summaries = {
            -10.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=-10.0, soc_targets=[0.8, 0.6], ecm_order=2
            ),
            25.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=25.0, soc_targets=[0.8, 0.6], ecm_order=2
            ),
            45.0: _build_synthetic_hppc_summary_for_temperature(
                self._root, temp_c=45.0, soc_targets=[0.8, 0.6], ecm_order=2
            ),
        }
        fit_dir = self._root / "fit_synthetic_temp_2rc"
        result = fit_ecm_parameters_from_dfn_hppc(
            dfn_hppc_summary_csv_by_temp_c=summaries,
            output_dir=fit_dir,
            ecm_order=2,
            loss_dynamic_weight=0.7,
        )
        pack = json.loads(Path(result["fitted_pack_json"]).read_text(encoding="utf-8"))
        self.assertEqual(pack["schema_version"], "ecm_temp_2d_v1")
        self.assertEqual(pack["ecm_order"], 2)
        self.assertIn("r2_ohm_map", pack)
        self.assertIn("c2_f_map", pack)
        self.assertEqual(np.asarray(pack["r2_ohm_map"], dtype=float).shape, (3, 2))

    def test_ecm_fitted_pack_integration_with_timeseries(self) -> None:
        profile_csv = self._root / "ecm_timeseries_profile.csv"
        _write_timeseries_csv(profile_csv, duration_s=20.0, period_s=1.0, current_a=5.0, temp_k=298.15)

        fitted_pack = self._root / "manual_fitted_pack_temp_2d.json"
        fitted_pack.write_text(json.dumps(_manual_temp_2d_pack(ecm_order=1), indent=2), encoding="utf-8")

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

    def test_legacy_ecm_pack_rejected(self) -> None:
        profile_csv = self._root / "legacy_pack_timeseries_profile.csv"
        _write_timeseries_csv(profile_csv, duration_s=10.0, period_s=1.0, current_a=4.0, temp_k=298.15)
        legacy_pack = self._root / "legacy_pack.json"
        legacy_pack.write_text(
            json.dumps(
                {
                    "soc": [0.0, 0.5, 1.0],
                    "ocv_v": [3.1, 3.5, 4.0],
                    "r0_ohm": [0.001, 0.0012, 0.0014],
                    "r1_ohm": [0.0018, 0.0021, 0.0024],
                    "c1_f": [18000.0, 22000.0, 26000.0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        cfg = {
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
                "ecm_fitted_pack_json": str(legacy_pack),
            },
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": True, "csv_path": str(profile_csv), "charge_compare": {"enabled": False}},
            "quality_gate": {"enabled": False, "enforce": False},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "output_dir": str(self._root / "legacy_pack_output"),
        }
        cfg_path = self._root / "legacy_pack_cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Legacy ECM fitted pack format is no longer supported"):
            run_from_config(cfg_path, mode="timeseries")

    def test_ecm_2rc_fitted_pack_integration_with_timeseries(self) -> None:
        profile_csv = self._root / "ecm_timeseries_profile_2rc.csv"
        _write_timeseries_csv(profile_csv, duration_s=15.0, period_s=0.5, current_a=6.0, temp_k=298.15)

        fitted_pack = self._root / "manual_fitted_pack_temp_2d_2rc.json"
        fitted_pack.write_text(json.dumps(_manual_temp_2d_pack(ecm_order=2), indent=2), encoding="utf-8")

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
            fit_temperature_grid_c=[-10.0, 25.0],
            cell_id="nmc150_fit_compare_smoke",
        )
        self.assertIn("passed", summary_ok)
        self.assertIn("fit_temperature_grid_c", summary_ok)
        self.assertTrue((output_ok / "fit" / "ecm_fitted_pack_temp_2d.json").exists())
        self.assertTrue((output_ok / "fit" / "ecm_fit_points_temp_2d.csv").exists())
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
            fit_temperature_grid_c=[-10.0, 25.0],
            gate_profile="off",
            cell_id="nmc150_fit_compare_smoke_2rc",
        )
        self.assertIn("ecm_order", summary_ok)
        self.assertEqual(summary_ok["ecm_order"], 2)
        self.assertIn("metrics_static_dynamic", summary_ok)
        self.assertTrue((output_ok / "fit" / "ecm_fitted_pack_temp_2d_2rc.json").exists())
        self.assertTrue((output_ok / "fit" / "ecm_fit_points_temp_2d_2rc.csv").exists())
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
