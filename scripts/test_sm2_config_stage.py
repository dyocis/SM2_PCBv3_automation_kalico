"""Test config provenance, refusal paths, and verified non-destructive staging."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stage_sm2_config import TARGETS, REGISTRATION, digest, inspect_tree, plan, stage


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.main = self.config / "printer.cfg"
        self.main.write_text("# retained header\n[include sensors/*.cfg]\n[mcu SM]\nserial: placeholder\n")
        self.sensors = self.config / "sensors"
        self.sensors.mkdir()
        self.runtime = {}
        for index, (name, kind) in enumerate(TARGETS.items()):
            text = "[%s]\nsensor_type: %s # retain this comment\ni2c_mcu: SM\n" % (name, kind)
            (self.sensors / (str(index) + ".cfg")).write_bytes(text.replace("\n", "\r\n").encode())
            self.runtime[name] = dict(sensor_type=kind, i2c_mcu="SM")

    def test_verified_backups_factory_edits_and_registration_no_live_changes(self):
        before = {path: path.read_bytes() for path in self.config.rglob("*.cfg")}
        output = self.root / "stage"
        manifest = stage(self.main, self.runtime, output)
        self.assertFalse(manifest["installation_allowed"])
        self.assertEqual(len(manifest["files"]), 5)
        self.assertEqual(sum("candidate" in e for e in manifest["files"]), 5)
        for entry in manifest["files"]:
            original = (output / entry["backup"]).read_bytes()
            self.assertEqual(digest(original), entry["sha256"])
            self.assertEqual(original, before[Path(entry["source"])] )
            if "candidate" in entry:
                candidate = (output / entry["candidate"]).read_bytes()
                if Path(entry["source"]) == self.main.resolve():
                    newline = "\r\n" if b"\r\n" in original else "\n"
                    self.assertEqual(candidate, REGISTRATION.replace("\n", newline).encode() + original)
                    self.assertLess(candidate.index(b"[sm2_bme280]"), candidate.index(b"[include "))
                    self.assertLess(candidate.index(b"[sm2_sgp40]"), candidate.index(b"[include "))
                else:
                    self.assertEqual(candidate.replace(b"sensor_type: SM2_", b"sensor_type: "), original)
                self.assertEqual(digest(candidate), entry["candidate_sha256"])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(json.loads((output / "manifest.json").read_text()), manifest)

    def test_duplicate_target_is_rejected_before_staging(self):
        (self.sensors / "extra.cfg").write_text("[temperature_sensor BME_IN]\nsensor_type: BME280\n")
        with self.assertRaisesRegex(ValueError, "Duplicate target"):
            stage(self.main, self.runtime, self.root / "stage")
        self.assertFalse((self.root / "stage").exists())

    def test_cycle_and_repeated_include_are_rejected(self):
        self.main.write_text("[include printer.cfg]\n")
        with self.assertRaisesRegex(ValueError, "Recursive"):
            inspect_tree(self.main)
        self.main.write_text("[include sensors/0.cfg]\n[include sensors/0.cfg]\n")
        with self.assertRaisesRegex(ValueError, "Repeated"):
            inspect_tree(self.main)

    def test_missing_glob_or_target_is_rejected(self):
        self.main.write_text("[include missing/*.cfg]\n")
        with self.assertRaisesRegex(ValueError, "matched no files"):
            inspect_tree(self.main)
        self.main.write_text("[mcu SM]\nserial: placeholder\n")
        with self.assertRaisesRegex(ValueError, "Missing target"):
            inspect_tree(self.main)

    def test_parameterized_and_inline_include_require_review(self):
        self.main.write_text("[include sensors/*.cfg]\nvariable: value\n")
        with self.assertRaisesRegex(ValueError, "Parameterized"):
            inspect_tree(self.main)
        self.main.write_text("[gcode_macro X]\ngcode: !!include snippet\n")
        with self.assertRaisesRegex(ValueError, "Inline include"):
            inspect_tree(self.main)

    def test_disk_runtime_drift_and_partial_migration_rejected(self):
        self.runtime["temperature_sensor BME_IN"]["i2c_mcu"] = "mcu"
        with self.assertRaisesRegex(ValueError, "Disk/runtime"):
            plan(self.main, self.runtime)
        path = self.sensors / "0.cfg"
        path.write_text(path.read_text().replace("BME280", "SM2_BME280"))
        with self.assertRaisesRegex(ValueError, "Unexpected baseline"):
            inspect_tree(self.main)

    def test_saved_calibration_is_preserved_without_becoming_duplicate_section(self):
        with self.main.open("a") as file:
            file.write("#*# [temperature_sensor SGP_IN]\n#*# voc_mean = 12345\n")
        self.runtime["temperature_sensor SGP_IN"]["voc_mean"] = "12345"
        files, candidates = plan(self.main, self.runtime)
        self.assertIn(b"voc_mean = 12345", files[self.main.resolve()])
        self.assertTrue(candidates[self.main.resolve()].endswith(files[self.main.resolve()]))

    def test_existing_registration_is_rejected(self):
        with self.main.open("a") as file:
            file.write("[sm2_bme280]\n")
        with self.assertRaisesRegex(ValueError, "registration already present"):
            plan(self.main, self.runtime)

    def test_stage_cannot_be_loaded_by_config_globs_or_overwrite_existing_directory(self):
        with self.assertRaisesRegex(ValueError, "outside active"):
            stage(self.main, self.runtime, self.config / "stage")
        output = self.root / "stage"
        output.mkdir()
        (output / "keep").write_text("existing")
        with self.assertRaises(FileExistsError):
            stage(self.main, self.runtime, output)
        self.assertEqual((output / "keep").read_text(), "existing")

    def test_broad_parent_glob_cannot_capture_staged_backups(self):
        self.main.write_text("[include ../*/sensors/*.cfg]\n")
        with self.assertRaisesRegex(ValueError, "include glob search root"):
            stage(self.main, self.runtime, self.root / "stage")
        self.assertFalse((self.root / "stage").exists())

    def test_config_change_during_backup_leaves_no_completed_manifest(self):
        output = self.root / "stage"
        write = Path.write_bytes
        def changing_write(path, data):
            result = write(path, data)
            if path.name.endswith("original.cfg"):
                with self.main.open("a") as file:
                    file.write("# changed during backup\n")
            return result
        with patch.object(Path, "write_bytes", changing_write):
            with self.assertRaisesRegex(ValueError, "changed during backup"):
                stage(self.main, self.runtime, output)
        self.assertFalse((output / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
