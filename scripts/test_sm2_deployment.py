"""Offline deployment-plan completeness and conflict tests."""
import json
from pathlib import Path
import unittest

import test_sm2_recovery as recovery_tests
from prepare_sm2_deployment import prepare
from prepare_sm2_i2c import ROOT
from stage_sm2_config import digest


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        fixture = recovery_tests.RecoveryTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        macro_dir = fixture.config.parent / "macros"
        macro_dir.mkdir()
        for source in (ROOT / "config").glob("*.cfg"):
            (macro_dir / source.name).write_bytes(source.read_bytes())
        with fixture.config.open("a") as config:
            config.write("\n[include macros/*.cfg]\n")
        fixture.collect()
        self.output = fixture.output.parent / "deployment"

    def test_plan_contains_forward_rollback_and_required_registration(self):
        plan = prepare(self.fixture.output, ROOT, self.output)
        self.assertFalse(plan["installation_allowed"])
        self.assertEqual(len(plan["changes"]), 1 + len(list((ROOT / "config").glob("*.cfg"))) + 5 + 2)
        for entry in plan["changes"]:
            self.assertEqual(digest((self.output / entry["forward"]).read_bytes()), entry["after_sha256"])
            if entry["originally_present"]:
                self.assertIsInstance(entry["original_mode"], int)
                self.assertEqual(digest((self.output / entry["rollback"]).read_bytes()), entry["before_sha256"])
            else:
                self.assertIsNone(entry["rollback"])
                self.assertTrue(Path(entry["destination"]).name.startswith("sm2_"))
        root = next(e for e in plan["changes"] if e["destination"] == str(self.fixture.config.resolve()))
        text = (self.output / root["forward"]).read_text()
        self.assertLess(text.index("[sm2_sgp40]"), text.index("[temperature_sensor"))

    def test_missing_active_macro_requires_review(self):
        # A checksum-valid bundle is still incomplete if an active macro is absent.
        config_path = self.fixture.output / "config/manifest.json"
        config = json.loads(config_path.read_text())
        config["files"] = [e for e in config["files"] if not e["source"].endswith("SM2_Control.cfg")]
        config_path.write_text(json.dumps(config))
        manifest_path = self.fixture.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["config_manifest_sha256"] = digest(config_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Expected one active config"):
            prepare(self.fixture.output, ROOT, self.output)
        self.assertFalse(self.output.exists())

    def test_corrupt_recovery_fails_before_plan_directory_created(self):
        (self.fixture.output / "runtime-evidence.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            prepare(self.fixture.output, ROOT, self.output)
        self.assertFalse(self.output.exists())

    def test_plan_never_overwrites_existing_directory(self):
        self.output.mkdir()
        (self.output / "keep").write_text("keep")
        with self.assertRaises(FileExistsError):
            prepare(self.fixture.output, ROOT, self.output)
        self.assertEqual((self.output / "keep").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
