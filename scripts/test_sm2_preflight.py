"""Offline preflight rejection tests; no printer or services contacted."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from preflight_sm2_i2c import audit, ROOT, BASE_COMMIT, SGP_COMMIT


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for destination, fixture in (
            ("klippy/extras/bme280.py", "kalico_bme280.py"),
            ("klippy/extras/sgp40/__init__.py", "sgp40_upstream.py"),
            ("klippy/extras/sgp40/gia.py", "sgp40_gia.py"),
            ("src/i2ccmds.c", "kalico_i2ccmds.c"),
        ):
            path = self.root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / "tests/fixtures" / fixture).read_bytes())
        self.info = dict(app="Kalico", state="ready", klipper_path=str(self.root),
                         software_version="v2026.10.00-157-g3ec85e6a")
        config = {"mcu SM": dict(is_non_critical="True", serial=
                  "/dev/serial/by-id/usb-Klipper_stm32g0b1xx_TEST-if00")}
        for side, bus in (("IN", "i2c1_PB8_PB9"), ("OUT", "i2c2_PB10_PB11")):
            config["temperature_sensor BME_" + side] = dict(sensor_type="BME280",
                i2c_mcu="SM", i2c_bus=bus, i2c_address="119")
            config["temperature_sensor SGP_" + side] = dict(sensor_type="SGP40",
                i2c_mcu="SM", i2c_bus=bus, ref_temp_sensor="bme280 BME_" + side,
                ref_humidity_sensor="bme280 BME_" + side,
                sync_with="SGP_OUT" if side == "IN" else "SGP_IN")
        self.status = dict(webhooks=dict(state="ready"), print_stats=dict(state="complete"),
                           idle_timeout=dict(state="Idle"),
                           configfile=dict(config=config, save_config_pending=False))
        self.status.update({"output_pin peltier": dict(value=0), "output_pin uv": dict(value=0),
                            "mcu SM": dict(mcu_version="recorded-baseline")})

    def run_audit(self, **kwargs):
        return audit(self.root, self.info, self.status,
                     head=kwargs.get("head", lambda path: BASE_COMMIT if path == self.root else SGP_COMMIT))

    def test_matched_baseline_passes_but_never_authorizes_installation(self):
        report = self.run_audit()
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["baseline_checks_passed"])
        self.assertFalse(report["installation_allowed"])
        self.assertEqual(len(report["evidence"]["sources"]), 4)

    def test_active_paused_or_unknown_prints_fail(self):
        for state in ("printing", "paused", "error", None):
            with self.subTest(state=state):
                self.status["print_stats"]["state"] = state
                self.assertFalse(self.run_audit()["baseline_checks_passed"])

    def test_missing_idle_output_or_pending_config_evidence_fails(self):
        baseline = copy.deepcopy(self.status)
        for key in ("idle_timeout", "output_pin peltier", "output_pin uv", "configfile"):
            self.status = copy.deepcopy(baseline)
            del self.status[key]
            with self.subTest(key=key):
                self.assertFalse(self.run_audit()["baseline_checks_passed"])

    def test_active_output_and_unapplied_config_fail(self):
        self.status["output_pin uv"]["value"] = 1
        self.status["configfile"]["save_config_pending"] = True
        report = self.run_audit()
        self.assertIn("Output uv is not confirmed off", report["errors"])
        self.assertIn("Pending config save state is missing or not clear", report["errors"])

    def test_unused_checkout_or_changed_running_version_fails(self):
        self.info["klipper_path"] = str(self.root / "different")
        self.info["software_version"] += "-dirty"
        self.assertGreaterEqual(len(self.run_audit()["errors"]), 2)

    def test_changed_installed_driver_detected_even_with_matching_git_head(self):
        (self.root / "klippy/extras/sgp40/gia.py").write_text("# unexpected installed file")
        report = self.run_audit()
        self.assertTrue(any("Installed source differs" in e for e in report["errors"]))

    def test_missing_installed_driver_fails(self):
        (self.root / "klippy/extras/bme280.py").unlink()
        self.assertFalse(self.run_audit()["baseline_checks_passed"])

    @unittest.skipIf(os.name == "nt", "Linux CI exercises installed package symlinks")
    def test_symlinked_installed_package_is_the_source_inspected(self):
        package = self.root / "klippy/extras/sgp40"
        actual = self.root / "installed-package"
        package.rename(actual)
        package.symlink_to(actual, target_is_directory=True)
        seen = []
        def head(path):
            seen.append(path)
            return BASE_COMMIT if path == self.root else SGP_COMMIT
        self.assertTrue(self.run_audit(head=head)["baseline_checks_passed"])
        self.assertIn(actual, seen)
        (actual / "gia.py").write_text("# changed real installed source")
        self.assertFalse(self.run_audit(head=head)["baseline_checks_passed"])

    def test_wrong_checkout_revision_fails(self):
        report = self.run_audit(head=lambda path: "unsupported")
        self.assertIn("Unsupported Kalico checkout revision", report["errors"])
        self.assertIn("Unsupported installed SGP40 revision", report["errors"])

    def test_mixed_factories_wrong_peer_and_extra_bus_device_fail(self):
        config = self.status["configfile"]["config"]
        config["temperature_sensor SGP_IN"].update(sensor_type="SM2_SGP40", sync_with="other")
        config["temperature_sensor extra"] = dict(i2c_mcu="SM")
        errors = self.run_audit()["errors"]
        for fragment in ("baseline factory", "sync peer", "additional SM I2C", "already configured"):
            self.assertTrue(any(fragment in error for error in errors), fragment)

    def test_wrong_mcu_address_bus_or_compensation_fails(self):
        section = self.status["configfile"]["config"]["temperature_sensor SGP_IN"]
        section.update(i2c_mcu="mcu", i2c_address="bad", i2c_bus="other",
                       ref_temp_sensor="bme280 BME_OUT")
        self.assertGreaterEqual(len(self.run_audit()["errors"]), 4)


if __name__ == "__main__":
    unittest.main()
