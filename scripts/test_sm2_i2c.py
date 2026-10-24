#!/usr/bin/env python3
"""Offline I2C command/adapter fault injection, including compiled C in CI."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from prepare_sm2_i2c import ROOT, patched_source, normalized

spec = importlib.util.spec_from_file_location("sm2_i2c", ROOT / "klippy/extras/sm2_i2c.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.callbacks = []
        self.reply = dict(oid=7, status=0, response=b"abc")
        self.mcu = SimpleNamespace(is_non_critical=True,
                                  non_critical_disconnected=False,
                                  _reconnecting=False,
                                  register_config_callback=self.callbacks.append,
                                  lookup_query_command=self.lookup)
        self.bus = SimpleNamespace(get_mcu=lambda: self.mcu,
                                   get_oid=lambda: 7,
                                   get_i2c_address=lambda: 0x59,
                                   get_command_queue=lambda: "queue")
        self.adapter = transport.SM2I2CReport(self.bus)
        self.callbacks[0]()

    def lookup(self, command, response, **kwargs):
        self.assertEqual(command, transport.COMMAND)
        self.assertEqual(response, transport.RESPONSE)
        self.assertEqual(kwargs, dict(oid=7, cq="queue"))
        return SimpleNamespace(send=self.send)

    def send(self, args, **kwargs):
        self.sent.append((args, kwargs))
        return self.reply

    def test_read_and_no_automatic_replay(self):
        self.assertEqual(self.adapter.i2c_read([1], 3)["response"], b"abc")
        self.assertEqual(self.sent, [([7, b"\x01", 3],
                                    dict(minclock=0, reqclock=0, retry=False))])

    def test_write_waits_for_status(self):
        self.reply["response"] = b""
        self.adapter.i2c_write([0x26, 0x0f], minclock=10, reqclock=20)
        self.assertFalse(self.sent[0][1]["retry"])
        self.assertEqual(self.sent[0][1]["reqclock"], 20)

    def test_each_error_and_unknown_status_rejects_payload(self):
        for status in (1, 2, 3, 4, 5, 6, 255, None):
            with self.subTest(status=status):
                self.reply["status"] = status
                with self.assertRaises(transport.SM2I2CError):
                    self.adapter.i2c_read([], 3)

    def test_failed_write_not_silently_accepted(self):
        self.reply.update(status=1, response=b"")
        with self.assertRaises(transport.SM2I2CError):
            self.adapter.i2c_write([1])

    def test_short_or_wrong_device_reply_is_invalid(self):
        for reply in (dict(oid=7, status=0, response=b"a"),
                      dict(oid=8, status=0, response=b"abc"),
                      dict(oid=7, status=0, response=None)):
            self.reply = reply
            with self.assertRaises(transport.SM2I2CError):
                self.adapter.i2c_read([], 3)

    def test_absence_and_reconnect_do_not_send(self):
        for flag in ("non_critical_disconnected", "_reconnecting"):
            setattr(self.mcu, flag, True)
            with self.assertRaises(transport.SM2I2CError):
                self.adapter.i2c_read([], 3)
            setattr(self.mcu, flag, False)
        self.assertEqual(self.sent, [])

    def test_disconnect_during_query_discards_reply(self):
        def send(*args, **kwargs):
            self.mcu.non_critical_disconnected = True
            return self.reply
        self.adapter.command.send = send
        with self.assertRaises(transport.SM2I2CError):
            self.adapter.i2c_read([], 3)

    def test_missing_firmware_has_no_fatal_command_fallback(self):
        def missing(*args, **kwargs):
            raise RuntimeError("Unknown command")
        self.mcu.lookup_query_command = missing
        with self.assertRaises(RuntimeError):
            self.adapter.build_config()
        self.assertIsNone(self.adapter.command)
        with self.assertRaises(transport.SM2I2CError):
            self.adapter.i2c_read([], 3)

    def test_bounds_checked_before_send(self):
        for data, size in (([], 0), ([], 33), ([0]*33, 1), ([], -1)):
            with self.assertRaises(transport.SM2I2CError):
                self.adapter.i2c_read(data, size)
        self.assertEqual(self.sent, [])

    def test_critical_mcu_and_unrelated_address_rejected(self):
        self.mcu.is_non_critical = False
        with self.assertRaises(transport.SM2I2CError):
            transport.SM2I2CReport(self.bus)
        self.mcu.is_non_critical = True
        self.bus.get_i2c_address = lambda: 0x40
        with self.assertRaises(transport.SM2I2CError):
            transport.SM2I2CReport(self.bus)


class FirmwareTests(unittest.TestCase):
    def test_patch_preserves_original_and_is_idempotent(self):
        base = (ROOT / "tests/fixtures/kalico_i2ccmds.c").read_bytes()
        patched = patched_source(base)
        self.assertTrue(patched.startswith(normalized(base)))
        self.assertEqual(patched_source(patched), patched)

    def test_patch_refuses_unreviewed_source(self):
        with self.assertRaises(ValueError):
            patched_source(b"different firmware source")

    def test_compiled_real_commands_with_injected_bus_failures(self):
        cc = os.environ.get("CC", "gcc")
        if not shutil.which(cc):
            if "CC" in os.environ:
                self.fail("Required C compiler not found: " + cc)
            self.skipTest("No local C compiler; CI requires gcc")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "board").mkdir()
            shutil.copyfile(ROOT / "tests/i2c_stubs/common.h", tmp / "common.h")
            for name in ("autoconf.h", "basecmd.h", "command.h", "sched.h",
                         "board/gpio.h", "i2c_software.h", "i2ccmds.h"):
                (tmp / name).write_text('#include "common.h"\n')
            base = (ROOT / "tests/fixtures/kalico_i2ccmds.c").read_bytes()
            (tmp / "candidate.c").write_bytes(patched_source(base))
            shutil.copyfile(ROOT / "tests/i2c_report_harness.c", tmp / "test.c")
            executable = tmp / ("test.exe" if os.name == "nt" else "test")
            subprocess.run([cc, "-std=gnu11", "-O0", "-g", "-Wall", "-Wextra",
                            "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable",
                            "-I", str(tmp), str(tmp / "test.c"), "-o", str(executable)],
                           check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
