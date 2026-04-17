from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from batt_bamm.hppc_compare import run_compare_pipeline
from batt_bamm.main import _soc_grid, load_config, run_from_config


def _discharge_segment(frame: pd.DataFrame) -> pd.DataFrame:
    pos = frame[frame["current_a"] > 1e-6]
    neg = frame[frame["current_a"] < -1e-6]

    def soc_drop(candidate: pd.DataFrame) -> float:
        if len(candidate) < 2:
            return float("-inf")
        return float(candidate["soc"].iloc[0] - candidate["soc"].iloc[-1])

    if len(pos) >= 2 and len(neg) >= 2:
        return pos if soc_drop(pos) >= soc_drop(neg) else neg
    if len(pos) >= 2:
        return pos
    if len(neg) >= 2:
        return neg
    return frame.iloc[0:0]


def _charge_segment(frame: pd.DataFrame) -> pd.DataFrame:
    neg = frame[frame["current_a"] < -1e-6]
    pos = frame[frame["current_a"] > 1e-6]

    def soc_rise(candidate: pd.DataFrame) -> float:
        if len(candidate) < 2:
            return float("-inf")
        return float(candidate["soc"].iloc[-1] - candidate["soc"].iloc[0])

    if len(neg) >= 2 and len(pos) >= 2:
        return neg if soc_rise(neg) >= soc_rise(pos) else pos
    if len(neg) >= 2:
        return neg
    if len(pos) >= 2:
        return pos
    return frame.iloc[0:0]


def _deep_copy_dict(payload: dict) -> dict:
    return json.loads(json.dumps(payload))


def _write_timeseries_csv(
    path: Path,
    *,
    duration_s: float = 60.0,
    period_s: float = 1.0,
    current_a: float = 20.0,
    temp_k: float = 298.15,
) -> None:
    times = np.arange(0.0, duration_s + period_s * 0.5, period_s)
    frame = pd.DataFrame(
        {
            "time_s": times,
            "current_a": np.full(times.shape, current_a, dtype=float),
            "temp_k": np.full(times.shape, temp_k, dtype=float),
        }
    )
    frame.to_csv(path, index=False)


class TestBaselinePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"baseline_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._base_config = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 0.5, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 120,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {
                "enabled": True,
                "rate_c": 0.5,
                "discharge_to_v": 3.6,
                "charge_to_v": 4.1,
                "period_s": 30,
            },
            "hppc": {"enabled": False},
            "timeseries": {"enabled": False},
        }

        cls._batch_output = cls._root / "batch_outputs"
        batch_config = _deep_copy_dict(cls._base_config)
        batch_config["output_dir"] = str(cls._batch_output)
        batch_config_path = cls._root / "batch_config.yaml"
        batch_config_path.write_text(yaml.safe_dump(batch_config), encoding="utf-8")
        cls._batch_summary = run_from_config(batch_config_path, mode="baseline")

    def test_smoke_single_case_files_exist(self) -> None:
        self.assertTrue((self._batch_output / "summary.json").exists())
        self.assertTrue((self._batch_output / "parameter_audit.json").exists())
        self.assertTrue((self._batch_output / "sanity_gate.csv").exists())
        self.assertTrue((self._batch_output / "sanity_gate.json").exists())
        self.assertTrue((self._batch_output / "voltage_overlay.png").exists())
        self.assertTrue((self._batch_output / "case_0p2c.csv").exists())

    def test_batch_cases_present(self) -> None:
        case_ids = {case["case_id"] for case in self._batch_summary["cases"]}
        self.assertEqual(case_ids, {"case_0p2c", "case_0p5c", "case_1p0c"})

    def test_sanity_gate_passes_with_default_config(self) -> None:
        gate = self._batch_summary["sanity_gate"]
        self.assertTrue(gate["enabled"])
        self.assertTrue(gate["passed"])
        self.assertTrue(gate["converged"])
        self.assertTrue(gate["has_positive_current"])
        self.assertTrue(gate["has_negative_current"])
        self.assertEqual(gate["warning_messages"], [])

    def test_parameter_scaling_ratio(self) -> None:
        audit_path = self._batch_output / "parameter_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        scaling = audit["scaling"]
        parameter_pack = audit["parameter_pack"]
        ratio = scaling["scale_ratio"]
        nominal_ratio = scaling["target_nominal_capacity_ah"] / scaling["base_nominal_capacity_ah"]
        parallel_ratio = scaling["parallel_after"] / scaling["parallel_before"]
        self.assertTrue(np.isclose(ratio, nominal_ratio, atol=1e-12))
        self.assertTrue(np.isclose(parallel_ratio, nominal_ratio, atol=1e-12))
        self.assertIn(parameter_pack["quality_level"], {"proxy", "identified"})
        self.assertEqual(audit.get("thermal_overrides"), [])

    def test_thermal_params_override_applies_and_audits(self) -> None:
        output_dir = self._root / "thermal_params_override_baseline"
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(output_dir)
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_params"] = {
            "total_heat_transfer_coefficient_w_m2_k": 120.0,
            "cell_volume_m3": 0.000726,
            "cell_cooling_surface_area_m2": 0.0512674863,
        }
        cfg["sanity_gate"]["enabled"] = False
        cfg["discharge_rates_c"] = [0.2]
        cfg["period_s"] = 300
        cfg_path = self._root / "thermal_params_override_baseline.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="baseline")
        self.assertTrue(summary["all_converged"])
        audit = json.loads((output_dir / "parameter_audit.json").read_text(encoding="utf-8"))
        overrides = audit.get("thermal_overrides", [])
        self.assertEqual(len(overrides), 3)
        expected = {
            "Total heat transfer coefficient [W.m-2.K-1]": 120.0,
            "Cell volume [m3]": 0.000726,
            "Cell cooling surface area [m2]": 0.0512674863,
        }
        observed = {item["parameter"]: float(item["target_value"]) for item in overrides}
        self.assertEqual(set(observed), set(expected))
        for key, value in expected.items():
            self.assertTrue(np.isclose(observed[key], value, atol=1e-12), msg=f"Mismatch for {key}")
        self.assertTrue(audit["scaling"]["thermal_overrides_requested"])
        self.assertTrue(audit["scaling"]["thermal_overrides_applied"])

    def test_thermal_params_invalid_value_fail_fast(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "thermal_params_invalid")
        cfg["model"]["thermal_params"] = {
            "cell_volume_m3": 0.0,
        }
        cfg_path = self._root / "thermal_params_invalid.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            run_from_config(cfg_path, mode="baseline")
        self.assertIn("model.thermal_params.cell_volume_m3", str(ctx.exception))

    def test_dfn_arrhenius_overrides_applied_and_audited(self) -> None:
        output_dir = self._root / "arrhenius_override_baseline"
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(output_dir)
        cfg["discharge_rates_c"] = [0.2]
        cfg["sanity_gate"]["enabled"] = False
        cfg["period_s"] = 300
        cfg["model"]["temperature_dependence"] = {
            "dfn": {
                "enabled": True,
                "reference_temp_k": 298.15,
                "arrhenius_overrides": {
                    "negative_exchange_current_ea_j_mol": 25000.0,
                    "positive_exchange_current_ea_j_mol": 28000.0,
                },
            }
        }
        cfg_path = self._root / "arrhenius_override_baseline.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="baseline")
        self.assertTrue(summary["all_converged"])
        audit = json.loads((output_dir / "parameter_audit.json").read_text(encoding="utf-8"))
        temp_dep = audit.get("temperature_dependence", {})
        self.assertTrue(temp_dep.get("dfn_enabled"))
        self.assertTrue(np.isclose(float(temp_dep.get("dfn_reference_temp_k")), 298.15, atol=1e-12))
        applied = temp_dep.get("dfn_arrhenius_overrides_applied", [])
        self.assertEqual(len(applied), 2)
        keys = {entry.get("parameter") for entry in applied}
        self.assertIn("Negative electrode exchange-current density [A.m-2]", keys)
        self.assertIn("Positive electrode exchange-current density [A.m-2]", keys)

    def test_dfn_arrhenius_override_invalid_value_fail_fast(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "arrhenius_invalid")
        cfg["model"]["temperature_dependence"] = {
            "dfn": {
                "enabled": True,
                "arrhenius_overrides": {
                    "negative_particle_diffusivity_ea_j_mol": -1.0,
                },
            }
        }
        cfg_path = self._root / "arrhenius_invalid.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            run_from_config(cfg_path, mode="baseline")
        self.assertIn(
            "model.temperature_dependence.dfn.arrhenius_overrides.negative_particle_diffusivity_ea_j_mol",
            str(ctx.exception),
        )

    def test_physical_sanity(self) -> None:
        for case in self._batch_summary["cases"]:
            self.assertTrue(case["converged"], msg=f"{case['case_id']} failed to converge")
            frame = pd.read_csv(case["csv_path"])

            self.assertTrue((frame["voltage_v"] > 0).all())
            self.assertTrue((frame["voltage_v"] < 5).all())
            self.assertTrue((frame["soc"] >= -0.05).all())
            self.assertTrue((frame["soc"] <= 1.05).all())

            discharge = _discharge_segment(frame)
            self.assertGreaterEqual(len(discharge), 2)
            self.assertGreaterEqual(discharge["soc"].iloc[0], discharge["soc"].iloc[-1] - 1e-3)

        frame_0p2 = pd.read_csv(self._batch_output / "case_0p2c.csv")
        frame_1p0 = pd.read_csv(self._batch_output / "case_1p0c.csv")
        mean_v_0p2 = _discharge_segment(frame_0p2)["voltage_v"].mean()
        mean_v_1p0 = _discharge_segment(frame_1p0)["voltage_v"].mean()
        self.assertLess(mean_v_1p0, mean_v_0p2)

    def test_repeatability(self) -> None:
        output_a = self._root / "repeat_a"
        output_b = self._root / "repeat_b"

        repeat_config = _deep_copy_dict(self._base_config)
        repeat_config["discharge_rates_c"] = [0.5]
        repeat_config["output_dir"] = str(output_a)
        config_path = self._root / "repeat_config.yaml"
        config_path.write_text(yaml.safe_dump(repeat_config), encoding="utf-8")

        summary_a = run_from_config(config_path, output_dir_override=output_a, mode="baseline")
        summary_b = run_from_config(config_path, output_dir_override=output_b, mode="baseline")

        case_a = summary_a["cases"][0]
        case_b = summary_b["cases"][0]
        self.assertTrue(case_a["converged"])
        self.assertTrue(case_b["converged"])

        for key in ("min_v", "max_v", "final_soc"):
            self.assertTrue(np.isclose(case_a[key], case_b[key], atol=5e-4), msg=f"Mismatch at {key}")

    def test_gate_failure_blocks_batch(self) -> None:
        fail_output = self._root / "gate_fail"
        fail_config = _deep_copy_dict(self._base_config)
        fail_config["output_dir"] = str(fail_output)
        fail_config["sanity_gate"] = {
            "enabled": True,
            "rate_c": 0.5,
            "discharge_to_v": 4.3,
            "charge_to_v": 4.1,
            "period_s": 30,
        }
        fail_config_path = self._root / "gate_fail.yaml"
        fail_config_path.write_text(yaml.safe_dump(fail_config), encoding="utf-8")

        fail_summary = run_from_config(fail_config_path, mode="baseline")
        self.assertFalse(fail_summary["sanity_gate"]["passed"])
        self.assertFalse(fail_summary["all_converged"])
        self.assertEqual(fail_summary["cases"], [])
        self.assertIsNone(fail_summary["artifacts"]["voltage_overlay_png"])
        self.assertFalse((fail_output / "case_0p2c.csv").exists())

    def test_summary_contract(self) -> None:
        summary_path = self._batch_output / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertIn("contract_version", summary)
        self.assertEqual(summary["contract_version"], "3.0.0")
        self.assertIn("contract_fields", summary)
        self.assertIn("stable_top_level_fields", summary["contract_fields"])
        self.assertIn("generated_at_utc", summary)
        self.assertIn("all_converged", summary)
        self.assertIn("config", summary)
        self.assertIn("termination_policy", summary)
        self.assertIn("termination_hits", summary)
        self.assertIn("artifacts", summary)
        self.assertIn("sanity_gate", summary)
        self.assertIn("cases", summary)

        artifacts = summary["artifacts"]
        self.assertEqual(
            set(artifacts),
            {"parameter_audit", "sanity_gate_csv", "sanity_gate_json", "voltage_overlay_png"},
        )

        gate = summary["sanity_gate"]
        expected_gate = {
            "enabled",
            "passed",
            "converged",
            "has_positive_current",
            "has_negative_current",
            "warning_messages",
            "runtime_s",
            "artifact_csv",
            "artifact_json",
            "error",
        }
        self.assertEqual(set(gate), expected_gate)

        case = summary["cases"][0]
        expected_case = {"case_id", "converged", "min_v", "max_v", "final_soc", "runtime_s", "csv_path", "termination", "error"}
        self.assertEqual(set(case), expected_case)
        self.assertEqual(
            set(case["termination"]),
            {"hit", "reason", "time_s", "index", "metric", "op", "threshold", "value"},
        )

    def test_baseline_termination_opt_out_for_experiment_modes(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "baseline_termination_opt_out"
        cfg["output_dir"] = str(output_dir)
        cfg["discharge_rates_c"] = [0.2]
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "apply_to_experiment_modes": False,
            "conditions": [
                {"metric": "time_s", "op": ">=", "threshold": 10.0, "name": "short_stop"},
            ],
        }
        cfg_path = self._root / "baseline_termination_opt_out.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="baseline")
        self.assertTrue(summary["all_converged"])
        case = summary["cases"][0]
        self.assertTrue(case["converged"])
        self.assertFalse(case["termination"]["hit"])
        frame = pd.read_csv(case["csv_path"])
        self.assertGreater(float(frame["time_s"].iloc[-1]), 10.0)

    def test_baseline_lumped_timeseries_boundary_falls_back_with_warning(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "baseline_lumped_boundary_fallback"
        cfg["output_dir"] = str(output_dir)
        cfg["discharge_rates_c"] = [0.2]
        cfg["sanity_gate"]["enabled"] = False
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_coupling"] = {"enabled": True, "boundary_mode": "timeseries"}
        cfg_path = self._root / "baseline_lumped_boundary_fallback.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="baseline")
        self.assertTrue(summary["all_converged"])
        warnings = [str(item) for item in summary.get("warnings", [])]
        self.assertTrue(any("falling back to constant ambient_temp_k" in item for item in warnings))


class TestHppcPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"hppc_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._base_config = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 0.5, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 120,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {
                "enabled": True,
                "soc_start": 1.0,
                "soc_end": 0.9,
                "soc_step": 0.05,
                "pulse_c_rate": 1.0,
                "discharge_s": 10,
                "charge_s": 10,
                "rest_minutes": 0.1,
                "period_s": 0.1,
            },
            "timeseries": {"enabled": False},
        }

        cls._output = cls._root / "hppc_outputs"
        cfg = _deep_copy_dict(cls._base_config)
        cfg["output_dir"] = str(cls._output)
        cls._config_path = cls._root / "hppc_config.yaml"
        cls._config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cls._config_path, mode="hppc")

    def test_hppc_smoke_outputs_exist(self) -> None:
        self.assertTrue((self._output / "summary.json").exists())
        self.assertTrue((self._output / "hppc_summary.csv").exists())
        self.assertTrue((self._output / "hppc_summary.json").exists())
        self.assertTrue((self._output / "hppc_voltage_overlay.png").exists())
        self.assertTrue((self._output / "hppc_point_soc_100.csv").exists())
        self.assertTrue((self._output / "hppc_point_soc_095.csv").exists())
        self.assertTrue((self._output / "hppc_point_soc_090.csv").exists())

    def test_hppc_contract(self) -> None:
        self.assertTrue(self._summary["all_converged"])
        self.assertEqual(self._summary["mode"], "hppc")
        self.assertEqual(self._summary["contract_version"], "3.0.0")
        self.assertIn("contract_fields", self._summary)
        hppc = self._summary["hppc"]
        expected = {
            "enabled",
            "passed",
            "stop_reason",
            "completed_points",
            "total_points",
            "termination_policy",
            "termination_hits",
            "artifacts",
            "points",
        }
        self.assertEqual(set(hppc), expected)
        self.assertTrue(hppc["enabled"])
        self.assertTrue(hppc["passed"])
        self.assertEqual(hppc["completed_points"], 3)
        self.assertEqual(hppc["total_points"], 3)
        self.assertGreaterEqual(hppc["termination_hits"], 0)

    def test_hppc_protocol_segments_present(self) -> None:
        for point in self._summary["hppc"]["points"]:
            frame = pd.read_csv(point["csv_path"])
            self.assertTrue((frame["current_a"] > 1e-6).any())
            self.assertTrue((frame["current_a"] < -1e-6).any())
            self.assertTrue((np.abs(frame["current_a"]) <= 1e-6).any())

    def test_hppc_resistance_metrics_positive(self) -> None:
        for point in self._summary["hppc"]["points"]:
            self.assertTrue(point["passed"])
            self.assertIsNotNone(point["r_dis_10s_ohm"])
            self.assertIsNotNone(point["r_chg_10s_ohm"])
            self.assertGreater(point["r_dis_10s_ohm"], 0.0)
            self.assertGreater(point["r_chg_10s_ohm"], 0.0)
            self.assertIn("termination", point)

    def test_hppc_fail_fast(self) -> None:
        fail_output = self._root / "hppc_fail"
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(fail_output)
        cfg["hppc"]["pulse_c_rate"] = 0.0
        fail_cfg = self._root / "hppc_fail.yaml"
        fail_cfg.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        fail_summary = run_from_config(fail_cfg, mode="hppc")
        hppc = fail_summary["hppc"]
        self.assertFalse(hppc["passed"])
        self.assertFalse(fail_summary["all_converged"])
        self.assertLess(hppc["completed_points"], hppc["total_points"])
        self.assertEqual(len(hppc["points"]), 1)
        self.assertFalse((fail_output / "hppc_point_soc_095.csv").exists())

    def test_soc_grid_includes_zero_endpoint(self) -> None:
        grid = _soc_grid(1.0, 0.0, 0.05)
        self.assertEqual(grid[0], 1.0)
        self.assertEqual(grid[-1], 0.0)
        self.assertEqual(len(grid), 21)


class TestHppcComparePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"hppc_compare_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        dfn_output = cls._root / "dfn_run"
        ecm_output = cls._root / "ecm_run"
        compare_output = cls._root / "compare_ok"
        cls._compare_output = compare_output

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
            "quality_gate": {"enabled": True, "enforce": True},
            "benchmark": {"enabled": False},
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "sanity_gate": {"enabled": False},
            "hppc": {
                "enabled": True,
                "soc_start": 0.95,
                "soc_end": 0.85,
                "soc_step": 0.05,
                "pulse_c_rate": 1.0,
                "discharge_s": 10,
                "charge_s": 10,
                "rest_minutes": 0.1,
                "period_s": 0.2,
            },
            "timeseries": {"enabled": False},
            "output_dir": str(dfn_output),
        }
        ecm_cfg = _deep_copy_dict(dfn_cfg)
        ecm_cfg["model"] = {"type": "ecm", "thermal": "isothermal"}
        ecm_cfg["parameter_set"] = "ECM_Example"
        ecm_cfg["voltage_low_v"] = 3.2
        ecm_cfg["output_dir"] = str(ecm_output)

        cls._dfn_cfg_path = cls._root / "dfn_hppc.yaml"
        cls._ecm_cfg_path = cls._root / "ecm_hppc.yaml"
        cls._dfn_cfg_path.write_text(yaml.safe_dump(dfn_cfg), encoding="utf-8")
        cls._ecm_cfg_path.write_text(yaml.safe_dump(ecm_cfg), encoding="utf-8")

        cls._summary = run_compare_pipeline(
            dfn_config_path=cls._dfn_cfg_path,
            ecm_config_path=cls._ecm_cfg_path,
            output_dir=compare_output,
            cell_id="nmc150_compare_smoke",
        )

    def test_compare_smoke_outputs_exist(self) -> None:
        self.assertTrue((self._compare_output / "hppc_compare_by_soc.csv").exists())
        self.assertTrue((self._compare_output / "hppc_compare_summary.json").exists())
        self.assertTrue((self._compare_output / "hppc_compare_voltage_delta.png").exists())
        self.assertTrue((self._compare_output / "hppc_compare_report.md").exists())

    def test_compare_contract(self) -> None:
        summary = self._summary
        self.assertTrue(summary["passed"])
        expected = {
            "generated_at_utc",
            "cell_id",
            "chemistry",
            "nominal_capacity_ah",
            "soc_grid",
            "dfn_run",
            "ecm_run",
            "completed_points",
            "passed",
            "metrics",
            "artifacts",
        }
        self.assertTrue(expected.issubset(set(summary.keys())))
        self.assertEqual(summary["completed_points"], 3)
        self.assertIn("mae_v_dis_end_v", summary["metrics"])
        self.assertIn("max_abs_delta_v_dis_end_v", summary["metrics"])
        self.assertIn("rmse_v_dis_end_v", summary["metrics"])
        self.assertIn("worst_soc_target", summary["metrics"])

    def test_compare_csv_contract(self) -> None:
        frame = pd.read_csv(self._compare_output / "hppc_compare_by_soc.csv")
        expected_cols = {
            "soc_target",
            "v_dis_end_dfn",
            "v_dis_end_ecm",
            "delta_v_dis_end_v",
            "abs_delta_v_dis_end_v",
            "v_dis_rest_start_dfn",
            "v_dis_rest_start_ecm",
            "delta_v_dis_rest_start_v",
            "r_dis_10s_ohm_dfn",
            "r_dis_10s_ohm_ecm",
            "delta_r_dis_10s_ohm",
        }
        self.assertEqual(set(frame.columns), expected_cols)
        self.assertEqual(len(frame), 3)

    def test_compare_failure_semantics(self) -> None:
        fail_output = self._root / "compare_fail"
        ecm_fail_cfg = yaml.safe_load(self._ecm_cfg_path.read_text(encoding="utf-8"))
        ecm_fail_cfg["hppc"]["pulse_c_rate"] = 0.0
        ecm_fail_cfg["output_dir"] = str(self._root / "ecm_fail_run")
        ecm_fail_path = self._root / "ecm_fail_hppc.yaml"
        ecm_fail_path.write_text(yaml.safe_dump(ecm_fail_cfg), encoding="utf-8")

        summary = run_compare_pipeline(
            dfn_config_path=self._dfn_cfg_path,
            ecm_config_path=ecm_fail_path,
            output_dir=fail_output,
            cell_id="nmc150_compare_fail",
        )
        self.assertFalse(summary["passed"])
        self.assertIn("ECM HPPC run failed", summary["stop_reason"])
        self.assertTrue((fail_output / "hppc_compare_summary.json").exists())
        self.assertTrue((fail_output / "hppc_compare_by_soc.csv").exists())


class TestTimeseriesPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"timeseries_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._csv_path = cls._root / "input_timeseries.csv"
        _write_timeseries_csv(cls._csv_path, duration_s=120.0, period_s=1.0, current_a=20.0, temp_k=298.15)

        cls._base_config = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 0.5, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {
                "enabled": True,
                "csv_path": str(cls._csv_path),
                "period_s": 1.0,
            },
        }

        cls._output = cls._root / "timeseries_outputs"
        cfg = _deep_copy_dict(cls._base_config)
        cfg["output_dir"] = str(cls._output)
        cls._config_path = cls._root / "timeseries_config.yaml"
        cls._config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cls._config_path, mode="timeseries")

    def test_timeseries_smoke_outputs_exist(self) -> None:
        self.assertTrue((self._output / "summary.json").exists())
        self.assertTrue((self._output / "parameter_audit.json").exists())
        self.assertTrue((self._output / "timeseries_output.csv").exists())
        self.assertTrue((self._output / "timeseries_summary.json").exists())

    def test_timeseries_contract(self) -> None:
        self.assertTrue(self._summary["all_converged"])
        self.assertEqual(self._summary["mode"], "timeseries")
        self.assertEqual(self._summary["contract_version"], "3.0.0")
        self.assertIn("contract_fields", self._summary)
        payload = self._summary["timeseries"]
        expected = {
            "enabled",
            "passed",
            "stop_reason",
            "source_csv",
            "termination_policy",
            "termination_hits",
            "artifacts",
            "case",
        }
        self.assertEqual(set(payload), expected)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["passed"])
        self.assertIsNone(payload["stop_reason"])
        self.assertEqual(payload["source_csv"], str(self._csv_path.resolve()))

    def test_timeseries_physical_sanity(self) -> None:
        frame = pd.read_csv(self._output / "timeseries_output.csv")
        self.assertIn("ocv_v", frame.columns)
        self.assertIn("cell_temperature_k", frame.columns)
        self.assertIn("boundary_temperature_k", frame.columns)
        self.assertTrue((frame["voltage_v"] > 0).all())
        self.assertTrue((frame["voltage_v"] < 5).all())
        self.assertLessEqual(frame["soc"].iloc[-1], frame["soc"].iloc[0] + 1e-6)
        self.assertTrue(np.allclose(frame["cell_temperature_k"].to_numpy(), 298.15))
        self.assertTrue(np.allclose(frame["boundary_temperature_k"].to_numpy(), 298.15))
        self.assertNotIn("temperature_k", frame.columns)

    def test_timeseries_lumped_shows_temperature_rise(self) -> None:
        hot_csv = self._root / "input_timeseries_hot.csv"
        _write_timeseries_csv(hot_csv, duration_s=180.0, period_s=1.0, current_a=300.0, temp_k=298.15)

        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "lumped_outputs"
        cfg["output_dir"] = str(output_dir)
        cfg["model"]["thermal"] = "lumped"
        cfg["timeseries"]["csv_path"] = str(hot_csv)
        cfg_path = self._root / "timeseries_lumped.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        cell_temp = frame["cell_temperature_k"].to_numpy(dtype=float)
        boundary_temp = frame["boundary_temperature_k"].to_numpy(dtype=float)
        self.assertGreater(float(np.max(cell_temp) - np.min(cell_temp)), 1e-6)
        self.assertTrue(np.allclose(boundary_temp, 298.15))

    def test_timeseries_repeatability(self) -> None:
        output_a = self._root / "repeat_a"
        output_b = self._root / "repeat_b"
        summary_a = run_from_config(self._config_path, output_dir_override=output_a, mode="timeseries")
        summary_b = run_from_config(self._config_path, output_dir_override=output_b, mode="timeseries")
        case_a = summary_a["timeseries"]["case"]
        case_b = summary_b["timeseries"]["case"]
        self.assertTrue(case_a["converged"])
        self.assertTrue(case_b["converged"])
        for key in ("min_v", "max_v", "final_soc"):
            self.assertTrue(np.isclose(case_a[key], case_b[key], atol=5e-4), msg=f"Mismatch at {key}")

    def test_timeseries_soc_switch_approx_cycle(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "soc_switch_approx_outputs"
        cfg["output_dir"] = str(output_dir)
        cfg["initial_soc"] = 0.80
        cfg["ambient_temp_k"] = 298.15
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_coupling"] = {"enabled": True, "boundary_mode": "constant"}
        cfg["timeseries"]["csv_path"] = ""
        cfg["timeseries"]["charge_compare"] = {"enabled": False}
        cfg["timeseries"]["soc_switch_approx"] = {
            "enabled": True,
            "soc_start": 0.80,
            "discharge_rate_c": 1.0,
            "discharge_to_soc": 0.78,
            "charge_rate_c": 1.0,
            "charge_to_soc": 0.79,
            "period_s": 0.5,
            "temp_k": 298.15,
        }
        cfg_path = self._root / "timeseries_soc_switch_approx.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        payload = summary["timeseries"]
        self.assertEqual(payload["source_csv"], "generated:soc_switch_approx")
        self.assertIn("soc_switch_approx", payload)
        self.assertIn("soc_switch_approx_input_csv", payload["artifacts"])
        self.assertTrue(Path(payload["artifacts"]["soc_switch_approx_input_csv"]).exists())

        approx = payload["soc_switch_approx"]
        self.assertTrue(approx["enabled"])
        self.assertGreater(float(approx["predicted_switch_time_s"]), 0.0)
        self.assertGreater(float(approx["predicted_end_time_s"]), float(approx["predicted_switch_time_s"]))
        self.assertIsNotNone(approx["soc_at_predicted_switch"])
        self.assertIsNotNone(approx["final_soc"])
        self.assertLess(abs(float(approx["switch_soc_error"])), 0.03)
        self.assertLess(abs(float(approx["final_soc_error"])), 0.03)

        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        current = frame["current_a"].to_numpy(dtype=float)
        self.assertTrue(np.any(current > 0))
        self.assertTrue(np.any(current < 0))
        self.assertTrue(np.allclose(frame["boundary_temperature_k"].to_numpy(dtype=float), 298.15))

    def test_timeseries_soc_switch_approx_conflict_with_charge_compare(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "soc_switch_conflict")
        cfg["timeseries"]["charge_compare"] = {"enabled": True}
        cfg["timeseries"]["soc_switch_approx"] = {
            "enabled": True,
            "soc_start": 0.99,
            "discharge_rate_c": 1.0,
            "discharge_to_soc": 0.30,
            "charge_rate_c": 1.0,
            "charge_to_soc": 0.90,
            "period_s": 0.1,
        }
        cfg_path = self._root / "timeseries_soc_switch_conflict.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            run_from_config(cfg_path, mode="timeseries")
        self.assertIn("cannot both be enabled", str(ctx.exception))

    def test_timeseries_soc_switch_approx_invalid_bounds(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "soc_switch_invalid_bounds")
        cfg["timeseries"]["csv_path"] = ""
        cfg["timeseries"]["charge_compare"] = {"enabled": False}
        cfg["timeseries"]["soc_switch_approx"] = {
            "enabled": True,
            "soc_start": 0.40,
            "discharge_rate_c": 1.0,
            "discharge_to_soc": 0.45,
            "charge_rate_c": 1.0,
            "charge_to_soc": 0.90,
            "period_s": 0.1,
        }
        cfg_path = self._root / "timeseries_soc_switch_invalid_bounds.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            run_from_config(cfg_path, mode="timeseries")
        self.assertIn("discharge_to_soc", str(ctx.exception))

    def test_timeseries_validation_fail_fast(self) -> None:
        bad_csv = self._root / "bad_input_non_monotonic.csv"
        pd.DataFrame(
            {
                "time_s": [0.0, 2.0, 1.0],
                "current_a": [10.0, 10.0, 10.0],
                "temp_k": [298.15, 298.15, 298.15],
            }
        ).to_csv(bad_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        fail_output = self._root / "fail_outputs"
        cfg["output_dir"] = str(fail_output)
        cfg["timeseries"]["csv_path"] = str(bad_csv)
        fail_config_path = self._root / "timeseries_fail.yaml"
        fail_config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(fail_config_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        self.assertFalse(summary["timeseries"]["passed"])
        self.assertIn("strictly increasing", summary["timeseries"]["stop_reason"])
        frame = pd.read_csv(fail_output / "timeseries_output.csv")
        self.assertTrue(frame.empty)

    def test_timeseries_validation_missing_column(self) -> None:
        bad_csv = self._root / "bad_input_missing_col.csv"
        pd.DataFrame({"time_s": [0.0, 1.0], "current_a": [10.0, 10.0]}).to_csv(bad_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        fail_output = self._root / "fail_missing_col"
        cfg["output_dir"] = str(fail_output)
        cfg["timeseries"]["csv_path"] = str(bad_csv)
        cfg_path = self._root / "timeseries_fail_missing_col.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        self.assertIn("missing required columns", summary["timeseries"]["stop_reason"])

    def test_timeseries_validation_nan(self) -> None:
        bad_csv = self._root / "bad_input_nan.csv"
        pd.DataFrame(
            {
                "time_s": [0.0, 1.0, 2.0],
                "current_a": [10.0, np.nan, 10.0],
                "temp_k": [298.15, 298.15, 298.15],
            }
        ).to_csv(bad_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        fail_output = self._root / "fail_nan"
        cfg["output_dir"] = str(fail_output)
        cfg["timeseries"]["csv_path"] = str(bad_csv)
        cfg_path = self._root / "timeseries_fail_nan.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        self.assertIn("NaN", summary["timeseries"]["stop_reason"])

    def test_timeseries_invalid_thermal_option(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["model"]["thermal"] = "x-full"
        cfg["output_dir"] = str(self._root / "invalid_thermal")
        cfg_path = self._root / "timeseries_invalid_thermal.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError):
            run_from_config(cfg_path, mode="timeseries")

    def test_timeseries_thermal_params_scope_warning_when_not_lumped(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "timeseries_thermal_scope_warning")
        cfg["model"]["thermal"] = "isothermal"
        cfg["model"]["thermal_params"] = {
            "total_heat_transfer_coefficient_w_m2_k": 120.0,
        }
        cfg_path = self._root / "timeseries_thermal_scope_warning.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        warnings = [str(item) for item in summary.get("warnings", [])]
        self.assertTrue(any("model.thermal_params is configured but applies only" in item for item in warnings))

    def test_timeseries_termination_time_cutoff(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "termination_time_cutoff"
        cfg["output_dir"] = str(output_dir)
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "conditions": [
                {"metric": "time_s", "op": ">=", "threshold": 40.0, "name": "time_gate"},
            ],
        }
        cfg_path = self._root / "timeseries_termination_time.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        case = summary["timeseries"]["case"]
        self.assertTrue(case["converged"])
        self.assertTrue(case["termination"]["hit"])
        self.assertEqual(case["termination"]["reason"], "time_gate")
        self.assertTrue(np.isclose(case["termination"]["time_s"], 40.0, atol=1.0))
        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        self.assertLessEqual(float(frame["time_s"].iloc[-1]), 40.0 + 1e-6)

    def test_timeseries_termination_ocv_metric(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "termination_ocv_cutoff"
        cfg["output_dir"] = str(output_dir)
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "conditions": [
                {"metric": "ocv_v", "op": ">=", "threshold": 0.0, "name": "ocv_available"},
            ],
        }
        cfg_path = self._root / "timeseries_termination_ocv.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        case = summary["timeseries"]["case"]
        self.assertTrue(case["termination"]["hit"])
        self.assertEqual(case["termination"]["metric"], "ocv_v")
        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        self.assertIn("ocv_v", frame.columns)

    def test_timeseries_termination_cell_temperature_metric(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "termination_cell_temperature"
        cfg["output_dir"] = str(output_dir)
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "conditions": [
                {"metric": "cell_temperature_k", "op": ">=", "threshold": 298.1, "name": "cell_temp_gate"},
            ],
        }
        cfg_path = self._root / "timeseries_termination_cell_temperature.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        case = summary["timeseries"]["case"]
        self.assertTrue(case["termination"]["hit"])
        self.assertEqual(case["termination"]["metric"], "cell_temperature_k")
        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        self.assertIn("cell_temperature_k", frame.columns)

    def test_timeseries_termination_boundary_temperature_metric(self) -> None:
        boundary_csv = self._root / "input_timeseries_boundary_for_termination.csv"
        times = np.arange(0.0, 121.0, 1.0)
        boundary_series = np.linspace(298.15, 310.15, len(times))
        frame = pd.DataFrame(
            {
                "time_s": times,
                "current_a": np.zeros(times.shape, dtype=float),
                "temp_k": boundary_series,
            }
        )
        frame.to_csv(boundary_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "termination_boundary_temperature"
        cfg["output_dir"] = str(output_dir)
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_coupling"] = {"enabled": True, "boundary_mode": "timeseries"}
        cfg["timeseries"]["csv_path"] = str(boundary_csv)
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "conditions": [
                {"metric": "boundary_temperature_k", "op": ">=", "threshold": 304.0, "name": "boundary_temp_gate"},
            ],
        }
        cfg_path = self._root / "timeseries_termination_boundary_temperature.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        case = summary["timeseries"]["case"]
        self.assertTrue(case["termination"]["hit"])
        self.assertEqual(case["termination"]["metric"], "boundary_temperature_k")
        self.assertGreaterEqual(float(case["termination"]["value"]), 304.0)
        out = pd.read_csv(output_dir / "timeseries_output.csv")
        self.assertIn("boundary_temperature_k", out.columns)

    def test_timeseries_lumped_temp_boundary_from_csv(self) -> None:
        boundary_csv = self._root / "input_timeseries_boundary.csv"
        times = np.arange(0.0, 121.0, 1.0)
        frame = pd.DataFrame(
            {
                "time_s": times,
                "current_a": np.zeros(times.shape, dtype=float),
                "temp_k": np.linspace(298.15, 310.15, len(times)),
            }
        )
        frame.to_csv(boundary_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "lumped_boundary_outputs"
        cfg["output_dir"] = str(output_dir)
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_coupling"] = {"enabled": True, "boundary_mode": "timeseries"}
        cfg["timeseries"]["csv_path"] = str(boundary_csv)
        cfg["timeseries"].pop("use_temp_as_ambient_boundary", None)
        cfg_path = self._root / "timeseries_lumped_boundary.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        out = pd.read_csv(output_dir / "timeseries_output.csv")
        cell_temp = out["cell_temperature_k"].to_numpy(dtype=float)
        boundary_temp = out["boundary_temperature_k"].to_numpy(dtype=float)
        self.assertGreater(float(np.max(cell_temp) - np.min(cell_temp)), 1e-3)
        self.assertTrue(np.allclose(boundary_temp, np.linspace(298.15, 310.15, len(boundary_temp))))

    def test_timeseries_legacy_temp_boundary_flag_compatibility(self) -> None:
        boundary_csv = self._root / "input_timeseries_boundary_legacy.csv"
        times = np.arange(0.0, 121.0, 1.0)
        frame = pd.DataFrame(
            {
                "time_s": times,
                "current_a": np.zeros(times.shape, dtype=float),
                "temp_k": np.linspace(298.15, 307.15, len(times)),
            }
        )
        frame.to_csv(boundary_csv, index=False)

        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "lumped_boundary_legacy_outputs"
        cfg["output_dir"] = str(output_dir)
        cfg["model"]["thermal"] = "lumped"
        cfg["timeseries"]["csv_path"] = str(boundary_csv)
        cfg["timeseries"]["use_temp_as_ambient_boundary"] = True
        cfg_path = self._root / "timeseries_lumped_boundary_legacy.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        out = pd.read_csv(output_dir / "timeseries_output.csv")
        cell_temp = out["cell_temperature_k"].to_numpy(dtype=float)
        boundary_temp = out["boundary_temperature_k"].to_numpy(dtype=float)
        self.assertGreater(float(np.max(cell_temp) - np.min(cell_temp)), 1e-3)
        self.assertTrue(np.allclose(boundary_temp, np.linspace(298.15, 307.15, len(boundary_temp))))

    def test_timeseries_invalid_thermal_boundary_mode(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["model"]["thermal"] = "lumped"
        cfg["model"]["thermal_coupling"] = {"enabled": True, "boundary_mode": "invalid-mode"}
        cfg["output_dir"] = str(self._root / "invalid_thermal_boundary_mode")
        cfg_path = self._root / "timeseries_invalid_thermal_boundary_mode.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError):
            run_from_config(cfg_path, mode="timeseries")

    def test_timeseries_deprecated_termination_temperature_metric_rejected(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        cfg["output_dir"] = str(self._root / "deprecated_temperature_metric")
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": False,
            "conditions": [
                {"metric": "temperature_k", "op": ">=", "threshold": 298.15},
            ],
        }
        cfg_path = self._root / "timeseries_deprecated_temperature_metric.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            run_from_config(cfg_path, mode="timeseries")
        self.assertIn("no longer supported", str(ctx.exception))
        self.assertIn("cell_temperature_k", str(ctx.exception))

    def test_timeseries_termination_must_hit_failure(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        output_dir = self._root / "termination_must_hit_failure"
        cfg["output_dir"] = str(output_dir)
        cfg["termination"] = {
            "enabled": True,
            "logic": "any_of",
            "must_hit": True,
            "conditions": [
                {"metric": "soc", "op": "<=", "threshold": -0.1},
            ],
        }
        cfg_path = self._root / "timeseries_termination_must_hit.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        self.assertIn("No termination condition was met", summary["timeseries"]["stop_reason"])
        case = summary["timeseries"]["case"]
        self.assertFalse(case["converged"])
        self.assertFalse(case["termination"]["hit"])


class TestChargeComparePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"charge_compare_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._base_config = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 0.5, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {
                "enabled": True,
                "csv_path": "",
                "period_s": 0.1,
                "charge_compare": {
                    "enabled": True,
                    "soc_start": 0.9,
                    "rates_c": [0.1, 0.3333333333, 1.0],
                    "period_by_rate_s": {
                        0.1: 1.0,
                        0.3333333333: 0.1,
                        1.0: 0.1,
                    },
                    "cv_cutoff_c_rate": 0.2,
                    "voltage_high_v": 4.2,
                },
            },
        }

        cls._output = cls._root / "charge_compare_outputs"
        cfg = _deep_copy_dict(cls._base_config)
        cfg["output_dir"] = str(cls._output)
        cls._config_path = cls._root / "charge_compare_config.yaml"
        cls._config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cls._config_path, mode="timeseries")

    def test_charge_compare_smoke_outputs_exist(self) -> None:
        self.assertTrue((self._output / "summary.json").exists())
        self.assertTrue((self._output / "charge_compare_summary.csv").exists())
        self.assertTrue((self._output / "charge_compare_summary.json").exists())
        self.assertTrue((self._output / "charge_compare_overlay.png").exists())
        self.assertTrue((self._output / "charge_case_0p1c.csv").exists())
        self.assertTrue((self._output / "charge_case_0p333c.csv").exists())
        self.assertTrue((self._output / "charge_case_1p0c.csv").exists())

    def test_charge_compare_contract(self) -> None:
        self.assertEqual(self._summary["mode"], "timeseries")
        self.assertEqual(self._summary["contract_version"], "3.0.0")
        self.assertIn("contract_fields", self._summary)
        self.assertIn("charge_compare", self._summary)
        payload = self._summary["charge_compare"]
        expected = {
            "enabled",
            "passed",
            "completed_cases",
            "total_cases",
            "termination_policy",
            "termination_hits",
            "artifacts",
            "cases",
        }
        self.assertEqual(set(payload), expected)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["total_cases"], 3)
        self.assertEqual(len(payload["cases"]), 3)
        self.assertTrue(all("termination" in case for case in payload["cases"]))

    def test_charge_compare_sampling(self) -> None:
        for case in self._summary["charge_compare"]["cases"]:
            if not case["converged"]:
                continue
            frame = pd.read_csv(case["csv_path"])
            dt = np.diff(frame["time_s"].to_numpy())
            self.assertTrue((dt > 0).all())
            expected_period = case["period_s"]
            self.assertTrue(np.isclose(np.median(dt), expected_period, atol=1e-2))

    def test_charge_compare_protocol_and_physics(self) -> None:
        for case in self._summary["charge_compare"]["cases"]:
            if not case["converged"]:
                continue
            frame = pd.read_csv(case["csv_path"])
            charge = _charge_segment(frame)
            self.assertGreaterEqual(len(charge), 2)
            self.assertGreaterEqual(charge["soc"].iloc[-1], charge["soc"].iloc[0] - 1e-3)
            self.assertTrue((frame["voltage_v"] > 0).all())
            self.assertLessEqual(frame["voltage_v"].max(), 4.5)
            self.assertIsNotNone(case["cv_time_s"])
            self.assertGreater(case["cv_time_s"], 0.0)

    def test_charge_compare_failure_continues_other_cases(self) -> None:
        cfg = _deep_copy_dict(self._base_config)
        fail_output = self._root / "charge_compare_fail"
        cfg["output_dir"] = str(fail_output)
        mapping = cfg["timeseries"]["charge_compare"]["period_by_rate_s"]
        mapping.pop(1.0, None)
        mapping.pop("1.0", None)
        fail_config_path = self._root / "charge_compare_fail_config.yaml"
        fail_config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(fail_config_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        payload = summary["charge_compare"]
        self.assertEqual(payload["total_cases"], 3)
        self.assertEqual(len(payload["cases"]), 3)
        failed = [case for case in payload["cases"] if not case["converged"]]
        self.assertGreaterEqual(len(failed), 1)
        self.assertTrue(any("No sampling period configured" in (case["error"] or "") for case in failed))


class TestModelChemistryCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"coverage_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

    def test_ecm_timeseries_smoke(self) -> None:
        csv_path = self._root / "ecm_timeseries.csv"
        _write_timeseries_csv(csv_path, duration_s=120.0, period_s=1.0, current_a=20.0, temp_k=298.15)
        output_dir = self._root / "ecm_timeseries_outputs"
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
            "rest_min": 1,
            "period_s": 30,
            "parameter_set": "ECM_Example",
            "model": {"type": "ecm", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {
                "enabled": True,
                "csv_path": str(csv_path),
                "period_s": 1.0,
                "charge_compare": {"enabled": False},
            },
            "output_dir": str(output_dir),
        }
        cfg_path = self._root / "ecm_timeseries.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertEqual(summary["mode"], "timeseries")
        self.assertTrue(summary["all_converged"])
        case = summary["timeseries"]["case"]
        self.assertTrue(case["converged"])
        frame = pd.read_csv(output_dir / "timeseries_output.csv")
        self.assertIn("ocv_v", frame.columns)
        self.assertFalse(frame.empty)

    def test_lfp_dfn_baseline_smoke(self) -> None:
        output_dir = self._root / "lfp_baseline_outputs"
        cfg = {
            "chemistry": "lfp",
            "nominal_capacity_ah": 130,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.5,
            "voltage_high_v": 3.6,
            "discharge_rates_c": [0.2],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 1,
            "period_s": 60,
            "parameter_set": "Prada2013",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": False},
            "output_dir": str(output_dir),
        }
        cfg_path = self._root / "lfp_baseline.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(cfg_path, mode="baseline")
        self.assertEqual(summary["mode"], "baseline")
        self.assertEqual(len(summary["cases"]), 1)
        self.assertTrue(summary["cases"][0]["converged"])
        audit = json.loads((output_dir / "parameter_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["parameter_pack"]["chemistry"], "lfp")
        self.assertEqual(audit["parameter_pack"]["model_type"], "dfn")


class TestBenchmarkPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"benchmark_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cfg = {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "quality_gate": {
                "enabled": True,
                "min_convergence_rate": 0.95,
                "max_repeat_delta_final_soc": 5e-4,
                "max_repeat_delta_min_v": 5e-3,
                "require_polarization_trend": True,
                "enforce": True,
            },
            "benchmark": {
                "enabled": True,
                "rates_c": [0.2, 1.0],
                "repeats": 2,
                "profiles": ["dfn_nmc"],
            },
            "identification_inputs": {"enabled": False, "strict": True},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": False},
        }

        cls._output = cls._root / "benchmark_outputs"
        cfg["output_dir"] = str(cls._output)
        cls._config_path = cls._root / "benchmark_config.yaml"
        cls._config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls._summary = run_from_config(cls._config_path, mode="benchmark")

    def test_benchmark_smoke_outputs_and_contract(self) -> None:
        self.assertTrue((self._output / "summary.json").exists())
        self.assertTrue((self._output / "benchmark_matrix.csv").exists())
        self.assertTrue((self._output / "benchmark_summary.json").exists())
        self.assertTrue((self._output / "benchmark_compare_report.md").exists())
        self.assertEqual(self._summary["mode"], "benchmark")
        self.assertEqual(self._summary["contract_version"], "3.0.0")
        self.assertIn("contract_fields", self._summary)
        self.assertIn("quality_gate", self._summary)
        self.assertIn("benchmark", self._summary)
        self.assertIn("identification_inputs_validation", self._summary)
        self.assertIn("thresholds", self._summary["quality_gate"])
        benchmark = self._summary["benchmark"]
        expected = {
            "passed",
            "total_cases",
            "converged_cases",
            "convergence_rate",
            "repeatability",
            "trend_checks",
            "failures",
            "artifacts",
        }
        self.assertEqual(set(benchmark), expected)
        self.assertGreaterEqual(benchmark["total_cases"], 2)
        self.assertGreaterEqual(benchmark["converged_cases"], 1)

    def test_benchmark_quality_gate_failure(self) -> None:
        cfg = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
        fail_output = self._root / "benchmark_gate_fail"
        cfg["output_dir"] = str(fail_output)
        cfg["quality_gate"]["min_convergence_rate"] = 1.1
        fail_cfg_path = self._root / "benchmark_gate_fail.yaml"
        fail_cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        summary = run_from_config(fail_cfg_path, mode="benchmark")
        self.assertFalse(summary["quality_gate"]["passed"])
        self.assertFalse(summary["all_converged"])
        self.assertGreaterEqual(len(summary["benchmark"]["failures"]), 1)
        failure = summary["benchmark"]["failures"][0]
        self.assertEqual(
            set(failure),
            {"category", "reason", "profile_id", "rate_c", "repeat", "observed", "threshold"},
        )


class TestIdentificationInputValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"ident_inputs_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._timeseries_csv = cls._root / "timeseries.csv"
        _write_timeseries_csv(cls._timeseries_csv, duration_s=20.0, period_s=1.0, current_a=10.0, temp_k=298.15)
        cls._ocv = cls._root / "ocv.csv"
        pd.DataFrame(
            {"soc": [0.0, 0.5, 1.0], "ocv_v": [3.0, 3.6, 4.2], "temp_k": [298.15, 298.15, 298.15]}
        ).to_csv(cls._ocv, index=False)
        cls._cc = cls._root / "cc.csv"
        pd.DataFrame(
            {
                "time_s": [0.0, 1.0, 2.0],
                "current_a": [10.0, 10.0, 10.0],
                "voltage_v": [4.2, 4.19, 4.18],
                "temp_k": [298.15, 298.15, 298.15],
            }
        ).to_csv(cls._cc, index=False)
        cls._hppc = cls._root / "hppc.csv"
        pd.DataFrame(
            {
                "soc_target": [1.0, 0.5, 0.1],
                "r_dis_10s_ohm": [0.002, 0.003, 0.004],
                "r_chg_10s_ohm": [0.0022, 0.0031, 0.0042],
                "temp_k": [298.15, 298.15, 298.15],
            }
        ).to_csv(cls._hppc, index=False)

    def _base_cfg(self, output_dir: Path) -> dict:
        return {
            "chemistry": "nmc",
            "nominal_capacity_ah": 150,
            "initial_soc": 1.0,
            "ambient_temp_k": 298.15,
            "voltage_low_v": 2.8,
            "voltage_high_v": 4.2,
            "discharge_rates_c": [0.2, 1.0],
            "charge_cc_rate": 0.5,
            "cv_cutoff_c_rate": 0.05,
            "rest_min": 5,
            "period_s": 30,
            "parameter_set": "Chen2020",
            "model": {"type": "dfn", "thermal": "isothermal"},
            "solver": {"rtol": 1e-6, "atol": 1e-8},
            "quality_gate": {"enabled": True, "enforce": True},
            "benchmark": {"enabled": False},
            "termination": {"enabled": True, "logic": "any_of", "must_hit": False, "conditions": []},
            "sanity_gate": {"enabled": False},
            "hppc": {"enabled": False},
            "timeseries": {"enabled": True, "csv_path": str(self._timeseries_csv), "charge_compare": {"enabled": False}},
            "output_dir": str(output_dir),
        }

    def test_identification_inputs_pass(self) -> None:
        out = self._root / "ident_ok"
        cfg = self._base_cfg(out)
        cfg["identification_inputs"] = {
            "enabled": True,
            "strict": True,
            "ocv_points_csv": str(self._ocv),
            "cc_cycle_csv": str(self._cc),
            "hppc_points_csv": str(self._hppc),
        }
        cfg_path = self._root / "ident_ok.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertTrue(summary["all_converged"])
        validation = summary["identification_inputs_validation"]
        self.assertTrue(validation["enabled"])
        self.assertTrue(validation["passed"])

    def test_identification_inputs_strict_fail(self) -> None:
        out = self._root / "ident_fail"
        cfg = self._base_cfg(out)
        bad_ocv = self._root / "bad_ocv.csv"
        pd.DataFrame({"soc": [0.0, 0.5, 1.0], "temp_k": [298.15, 298.15, 298.15]}).to_csv(bad_ocv, index=False)
        cfg["identification_inputs"] = {
            "enabled": True,
            "strict": True,
            "ocv_points_csv": str(bad_ocv),
            "cc_cycle_csv": str(self._cc),
            "hppc_points_csv": str(self._hppc),
        }
        cfg_path = self._root / "ident_fail.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        summary = run_from_config(cfg_path, mode="timeseries")
        self.assertFalse(summary["all_converged"])
        validation = summary["identification_inputs_validation"]
        self.assertFalse(validation["passed"])
        self.assertGreaterEqual(len(validation["errors"]), 1)


class TestTemperatureDependenceConfigSamples(unittest.TestCase):
    def test_nmc_dfn_temperature_dependence_sample_loads(self) -> None:
        cfg_path = Path("configs/cells/nmc622_150ah/baseline_150ah_nmc622_temp_dep_example.yaml").resolve()
        cfg = load_config(cfg_path)
        self.assertEqual(cfg.model_type, "dfn")
        self.assertTrue(cfg.temperature_dependence.dfn.enabled)
        overrides = cfg.temperature_dependence.dfn.arrhenius_overrides
        self.assertIsNotNone(overrides.negative_particle_diffusivity_ea_j_mol)
        self.assertIsNotNone(overrides.positive_particle_diffusivity_ea_j_mol)
        self.assertIsNotNone(overrides.negative_exchange_current_ea_j_mol)
        self.assertIsNotNone(overrides.positive_exchange_current_ea_j_mol)

    def test_lfp_ecm_temperature_dependence_sample_loads(self) -> None:
        cfg_path = Path("configs/cells/lfp_130ah/baseline_130ah_lfp_ecm_temp_dep_example.yaml").resolve()
        cfg = load_config(cfg_path)
        self.assertEqual(cfg.model_type, "ecm")
        self.assertEqual(cfg.ecm_rc_elements, 2)
        self.assertIsNotNone(cfg.ecm_fitted_pack_json)
        self.assertIn("ecm_fitted_pack_temp_2d_2rc.json", str(cfg.ecm_fitted_pack_json))


if __name__ == "__main__":
    unittest.main()
