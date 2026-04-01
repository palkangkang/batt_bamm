from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from batt_bamm.main import run_from_config


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
            "model": {"thermal": "isothermal"},
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
        ratio = scaling["scale_ratio"]
        nominal_ratio = scaling["target_nominal_capacity_ah"] / scaling["base_nominal_capacity_ah"]
        parallel_ratio = scaling["parallel_after"] / scaling["parallel_before"]
        self.assertTrue(np.isclose(ratio, nominal_ratio, atol=1e-12))
        self.assertTrue(np.isclose(parallel_ratio, nominal_ratio, atol=1e-12))

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
        self.assertIn("generated_at_utc", summary)
        self.assertIn("all_converged", summary)
        self.assertIn("config", summary)
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
        expected_case = {"case_id", "converged", "min_v", "max_v", "final_soc", "runtime_s", "csv_path", "error"}
        self.assertEqual(set(case), expected_case)


class TestHppcPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"hppc_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._base_config = {
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
            "model": {"thermal": "isothermal"},
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
        hppc = self._summary["hppc"]
        expected = {"enabled", "passed", "stop_reason", "completed_points", "total_points", "artifacts", "points"}
        self.assertEqual(set(hppc), expected)
        self.assertTrue(hppc["enabled"])
        self.assertTrue(hppc["passed"])
        self.assertEqual(hppc["completed_points"], 3)
        self.assertEqual(hppc["total_points"], 3)

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
            "model": {"thermal": "isothermal"},
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
        payload = self._summary["timeseries"]
        expected = {"enabled", "passed", "stop_reason", "source_csv", "artifacts", "case"}
        self.assertEqual(set(payload), expected)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["passed"])
        self.assertIsNone(payload["stop_reason"])
        self.assertEqual(payload["source_csv"], str(self._csv_path.resolve()))

    def test_timeseries_physical_sanity(self) -> None:
        frame = pd.read_csv(self._output / "timeseries_output.csv")
        self.assertTrue((frame["voltage_v"] > 0).all())
        self.assertTrue((frame["voltage_v"] < 5).all())
        self.assertLessEqual(frame["soc"].iloc[-1], frame["soc"].iloc[0] + 1e-6)
        self.assertTrue(np.allclose(frame["temperature_k"].to_numpy(), 298.15))

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


class TestChargeComparePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_root = Path.cwd() / ".tmp_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._root = temp_root / f"charge_compare_pipeline_{int(time.time() * 1000)}"
        cls._root.mkdir(parents=True, exist_ok=False)

        cls._base_config = {
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
            "model": {"thermal": "isothermal"},
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
        self.assertIn("charge_compare", self._summary)
        payload = self._summary["charge_compare"]
        expected = {"enabled", "passed", "completed_cases", "total_cases", "artifacts", "cases"}
        self.assertEqual(set(payload), expected)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["total_cases"], 3)
        self.assertEqual(len(payload["cases"]), 3)

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


if __name__ == "__main__":
    unittest.main()
