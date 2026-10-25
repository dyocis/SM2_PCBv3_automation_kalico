"""Recovery-bundle tests with synthetic printer evidence and saved firmware."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_sm2_preflight as baseline
from bundle_sm2_recovery import bundle, firmware_evidence
from stage_sm2_config import digest
from verify_sm2_recovery import verify


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = baseline.PreflightTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.info, self.status = fixture.root, fixture.info, fixture.status
        self.destination = tempfile.TemporaryDirectory()
        self.addCleanup(self.destination.cleanup)
        self.output = Path(self.destination.name) / "recovery"
        self.config = self.root / "config/printer.cfg"
        self.config.parent.mkdir()
        self.info["config_file"] = str(self.config)
        variables = self.config.parent / "variables.cfg"
        variables.write_text("[Variables]\nbaseline = 12\n")
        config = self.status["configfile"]["config"]
        config["save_variables"] = dict(filename=str(variables))
        self.config.write_text("\n".join("[%s]\n%s\n" %
            (name, "\n".join("%s: %s" % item for item in settings.items()))
            for name, settings in config.items()))
        self.automation = self.root / "automation"
        (self.automation / "dashboard").mkdir(parents=True)
        for name in ("app.js", "nevermore_history.py"):
            (self.automation / "dashboard" / name).write_text("# original " + name)
        (self.root / ".config").write_text('CONFIG_MCU="stm32g0b1xx"\n'
            'CONFIG_CLOCK_REF_FREQ=8000000\nCONFIG_FLASH_APPLICATION_ADDRESS=0x8000000\n'
            'CONFIG_STM32_USB_PA11_PA12=y\n')
        (self.root / "out").mkdir()
        self.dictionary = dict(version="recorded-baseline", config=dict(MCU="stm32g0b1xx"), commands={})
        self.save_dictionary()
        for name in ("klipper.bin", "klipper.elf"):
            (self.root / "out" / name).write_bytes(b"synthetic-test-only")

    def save_dictionary(self):
        (self.root / "out/klipper.dict").write_text(json.dumps(self.dictionary))

    def collect(self):
        return bundle(self.root, self.automation, self.config, self.root, self.info,
                      self.status, self.output,
                      head=lambda p: baseline.BASE_COMMIT if p == self.root else baseline.SGP_COMMIT)

    def test_copies_verified_with_module_absence_and_explicit_firmware_limit(self):
        manifest = self.collect()
        self.assertFalse(manifest["installation_allowed"])
        self.assertTrue(manifest["firmware"]["metadata_matches_runtime"])
        self.assertFalse(manifest["firmware"]["flashed_binary_identity_verified"])
        self.assertTrue(all(not m["originally_present"] for m in manifest["module_inventory"]))
        for entry in manifest["artifacts"]:
            self.assertEqual(digest((self.output / entry["backup"]).read_bytes()), entry["sha256"])
            self.assertEqual(Path(entry["source"]).read_bytes(), (self.output / entry["backup"]).read_bytes())
        self.assertEqual(digest((self.output / "runtime-evidence.json").read_bytes()),
                         manifest["runtime_evidence_sha256"])
        self.assertEqual(verify(self.output), manifest)

    def test_copied_bundle_corruption_is_detected(self):
        manifest = self.collect()
        (self.output / manifest["artifacts"][0]["backup"]).write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify(self.output)

    def test_verifier_refuses_paths_outside_bundle(self):
        manifest = self.collect()
        manifest["artifacts"][0]["backup"] = "../unrelated"
        (self.output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Invalid bundle path"):
            verify(self.output)

    def test_existing_module_is_backed_up_and_not_marked_new(self):
        path = self.root / "klippy/extras/sm2_connection.py"
        path.write_text("# installed monitor")
        manifest = self.collect()
        self.assertTrue(next(m for m in manifest["module_inventory"] if m["path"] == str(path))["originally_present"])
        self.assertTrue(any(e["source"] == str(path) for e in manifest["artifacts"]))

    def test_wrong_firmware_version_or_chip_is_rejected(self):
        self.dictionary["version"] = "different"
        self.save_dictionary()
        with self.assertRaisesRegex(ValueError, "version differs"):
            self.collect()
        self.dictionary["version"] = "recorded-baseline"
        self.dictionary["config"]["MCU"] = "stm32h723xx"
        self.save_dictionary()
        with self.assertRaisesRegex(ValueError, "another MCU"):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_missing_binary_or_wrong_build_profile_rejected(self):
        (self.root / "out/klipper.bin").unlink()
        with self.assertRaises(FileNotFoundError):
            self.collect()
        (self.root / ".config").write_text("CONFIG_MCU=wrong\n")
        with self.assertRaisesRegex(ValueError, "build profile"):
            self.collect()

    def test_missing_saved_variables_or_active_printer_rejected(self):
        self.status["configfile"]["config"].pop("save_variables")
        with self.assertRaisesRegex(ValueError, "Saved-variable"):
            self.collect()
        self.status["print_stats"]["state"] = "printing"
        with self.assertRaisesRegex(ValueError, "Baseline audit failed"):
            self.collect()

    def test_changed_source_during_capture_has_no_completion_manifest(self):
        write = Path.write_bytes
        def changing_write(path, data):
            result = write(path, data)
            if path.parent.name == "artifacts":
                (self.automation / "dashboard/app.js").write_text("# changed during capture")
            return result
        with patch.object(Path, "write_bytes", changing_write):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                self.collect()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_existing_destination_never_overwritten(self):
        self.output.mkdir()
        (self.output / "keep").write_text("preserve")
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual((self.output / "keep").read_text(), "preserve")

    def test_prototype_protocol_is_not_a_baseline_recovery_image(self):
        self.dictionary["commands"] = {"sm2_i2c_report_v1 oid=%c": 99}
        self.save_dictionary()
        with self.assertRaisesRegex(ValueError, "experimental protocol"):
            firmware_evidence(self.root, self.status["mcu SM"])


if __name__ == "__main__":
    unittest.main()
