"""Apply/rollback on disposable fake printer files only; no service operations."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_sm2_deployment as fixtures
import apply_sm2_deployment as executor
from prepare_sm2_deployment import prepare
from prepare_sm2_i2c import ROOT
from stage_sm2_config import digest


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DeploymentTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.output
        self.recovery = self.fixture.fixture.output
        self.plan = prepare(self.recovery, ROOT, self.root)
        self.sha = digest((self.root / "plan.json").read_bytes())

    def run_action(self, action):
        return executor.execute(self.root, self.recovery, self.sha, action, guard=lambda: None)

    def assert_originals(self):
        for entry in self.plan["changes"]:
            target = Path(entry["resolved_destination"])
            if entry["originally_present"]:
                self.assertEqual(digest(target.read_bytes()), entry["before_sha256"])
            else:
                self.assertFalse(target.exists())

    def test_check_does_not_write_and_apply_rollback_roundtrip(self):
        self.assertEqual(self.run_action("check")["changed_files"], 0)
        self.assertFalse((self.root / "execution.json").exists())
        self.assertEqual(self.run_action("apply")["status"], "applied")
        for entry in self.plan["changes"]:
            self.assertEqual(digest(Path(entry["resolved_destination"]).read_bytes()), entry["after_sha256"])
        self.assertEqual(self.run_action("rollback")["status"], "rolled_back")
        self.assert_originals()
        self.assertEqual(self.run_action("rollback")["status"], "rolled_back")

    def test_changed_source_refused_before_any_write(self):
        original = next(e for e in self.plan["changes"] if e["originally_present"])
        Path(original["resolved_destination"]).write_bytes(b"user edit")
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.run_action("apply")
        self.assertFalse((self.root / "execution.json").exists())

    def test_bad_payload_and_reviewed_hash_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "reviewed plan"):
            executor.execute(self.root, self.recovery, "wrong", "apply", guard=lambda: None)
        (self.root / self.plan["changes"][0]["forward"]).write_bytes(b"corruption")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.run_action("apply")
        self.assert_originals()

    def test_failure_after_replace_can_resume_or_rollback(self):
        real_write = executor.atomic_write
        count = 0
        def interrupted(path, data, mode=0o600):
            nonlocal count
            real_write(path, data, mode)
            if path.name != "execution.json":
                count += 1
                if count == 2:
                    raise OSError("simulated power loss after file replacement")
        with patch.object(executor, "atomic_write", interrupted):
            with self.assertRaisesRegex(OSError, "simulated power loss"):
                self.run_action("apply")
        self.assertEqual(self.run_action("apply")["status"], "applied")
        self.run_action("rollback")
        self.assert_originals()

    def test_failure_before_replace_can_rollback_partial_install(self):
        real_write = executor.atomic_write
        count = 0
        def interrupted(path, data, mode=0o600):
            nonlocal count
            if path.name != "execution.json":
                count += 1
                if count == 2:
                    raise OSError("simulated interruption before replacement")
            real_write(path, data, mode)
        with patch.object(executor, "atomic_write", interrupted):
            with self.assertRaises(OSError):
                self.run_action("apply")
        self.run_action("rollback")
        self.assert_originals()

    def test_rollback_refuses_user_changes_and_needs_journal(self):
        with self.assertRaisesRegex(ValueError, "No execution journal"):
            self.run_action("rollback")
        self.run_action("apply")
        new = next(e for e in self.plan["changes"] if not e["originally_present"])
        Path(new["resolved_destination"]).write_bytes(b"later user change")
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.run_action("rollback")
        self.assertEqual(Path(new["resolved_destination"]).read_bytes(), b"later user change")

    def test_unrecorded_new_module_is_never_claimed(self):
        new = next(e for e in self.plan["changes"] if not e["originally_present"])
        target = Path(new["resolved_destination"])
        target.write_bytes((self.root / new["forward"]).read_bytes())
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "Unrecorded"):
            self.run_action("apply")
        self.assertTrue(target.exists())

    def test_service_guard_is_checked_before_each_write(self):
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError("service restarted")
        with self.assertRaisesRegex(ValueError, "service restarted"):
            executor.execute(self.root, self.recovery, self.sha, "apply", guard=guard)
        self.run_action("rollback")
        self.assert_originals()

    def test_systemd_guard_rejects_active_failed_or_unknown(self):
        for state in ("active", "failed", "activating"):
            with self.subTest(state=state), patch.object(executor.subprocess, "run", return_value=
                    SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=%s\nSubState=dead\n" % state)):
                with self.assertRaisesRegex(ValueError, "must be stopped"):
                    executor.require_stopped()

    @unittest.skipIf(os.name != "posix", "POSIX permissions checked by Linux CI")
    def test_permission_drift_rejected(self):
        entry = next(e for e in self.plan["changes"] if e["originally_present"])
        Path(entry["resolved_destination"]).chmod(0o600 if entry["original_mode"] != 0o600 else 0o640)
        with self.assertRaisesRegex(ValueError, "permissions changed"):
            self.run_action("apply")

    @unittest.skipIf(os.name != "posix", "Linux symlink preservation")
    def test_existing_module_symlink_survives_apply_and_rollback(self):
        fixture = self.fixture.fixture
        target = fixture.root / "original-connection.py"
        target.write_text("# existing connection monitor")
        alias = fixture.root / "klippy/extras/sm2_connection.py"
        alias.symlink_to(target)
        fixture.output = fixture.output.parent / "linked-recovery"
        fixture.collect()
        self.recovery = fixture.output
        self.root = fixture.output.parent / "linked-plan"
        self.plan = prepare(self.recovery, ROOT, self.root)
        self.sha = digest((self.root / "plan.json").read_bytes())
        self.run_action("apply")
        self.assertTrue(alias.is_symlink())
        self.run_action("rollback")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(target.read_text(), "# existing connection monitor")

    @unittest.skipIf(os.name != "posix", "Linux flock required")
    def test_global_lock_prevents_concurrent_plans(self):
        with patch.object(Path, "home", return_value=self.fixture.fixture.root):
            with executor.process_lock(self.root):
                with self.assertRaises(BlockingIOError):
                    with executor.process_lock(self.recovery):
                        self.fail("Second plan obtained deployment lock")


if __name__ == "__main__":
    unittest.main()
