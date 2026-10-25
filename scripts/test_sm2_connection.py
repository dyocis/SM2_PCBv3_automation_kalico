#!/usr/bin/env python3
"""Connection lifecycle and real macro gating; no hardware is contacted."""
import configparser
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sm2_connection", ROOT / "klippy/extras/sm2_connection.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
JINJA = Environment(undefined=StrictUndefined,
                    variable_start_string="{", variable_end_string="}")


class FakePrinter:
    def __init__(self):
        self.events = {}
        self.messages = []
        self.sensors = {}
        self.mcu = SimpleNamespace(is_non_critical=True,
                                   non_critical_disconnected=False,
                                   _reconnecting=False)

    def register_event_handler(self, event, callback):
        self.events[event] = callback

    def lookup_object(self, name, default=None):
        if name == "gcode": return self
        if name.startswith("mcu "): return self.mcu
        return self.sensors.get(name, default)

    def respond_info(self, message, log=False):
        self.messages.append(message)


class ConnectionTests(unittest.TestCase):
    def test_partial_or_disappearing_validity_sources_never_enable_starts(self):
        self.start()
        self.printer.sensors["bme280 BME_IN"] = SimpleNamespace(get_status=lambda t: {"valid": True})
        self.assertFalse(self.status()["sensors_ready"])
        self.printer.sensors.clear()
        self.assertFalse(self.status()["sensors_ready"])
        self.assertTrue(self.status()["sensors_managed"])

    def test_sensor_warmup_then_fault_latches_without_blocking_off_commands(self):
        self.start()
        values = {name: False for name in ("bme280 BME_IN", "bme280 BME_OUT", "sgp40 SGP_IN", "sgp40 SGP_OUT")}
        for name in values:
            self.printer.sensors[name] = SimpleNamespace(get_status=lambda t, key=name: {"valid": values[key]})
        self.assertFalse(self.status()["sensors_ready"])
        self.assertFalse(self.status()["sensor_restart_required"])
        values.update(dict.fromkeys(values, True))
        self.assertTrue(self.status()["sensors_ready"])
        values["sgp40 SGP_IN"] = False
        self.assertFalse(self.status()["sensors_ready"])
        self.assertTrue(self.status()["available"])
        values["sgp40 SGP_IN"] = True
        self.assertFalse(self.status()["sensors_ready"])
        self.assertTrue(self.status()["sensor_restart_required"])

    def setUp(self):
        self.printer = FakePrinter()
        config = SimpleNamespace(get_printer=lambda: self.printer,
                                 get=lambda name, default: default,
                                 error=ValueError)
        self.gate = MODULE.load_config(config)

    def emit(self, event):
        self.printer.events[event]()

    def start(self):
        self.emit("klippy:connect")
        self.emit("klippy:ready")

    def status(self):
        return self.gate.get_status(123.0)

    def test_ready_required_before_available(self):
        self.assertFalse(self.status()["available"])
        self.emit("klippy:connect")
        self.assertFalse(self.status()["available"])
        self.emit("klippy:ready")
        self.assertTrue(self.status()["available"])

    def test_disconnect_reconnect_never_rearms(self):
        self.start()
        self.printer.mcu.non_critical_disconnected = True
        self.emit("danger:non_critical_mcu_SM:disconnected")
        self.assertFalse(self.status()["available"])
        self.printer.mcu.non_critical_disconnected = False
        self.emit("danger:non_critical_mcu_SM:reconnected")
        self.assertTrue(self.status()["connected"])
        self.assertTrue(self.status()["restart_required"])
        self.assertFalse(self.status()["available"])
        self.emit("klippy:ready")
        self.assertFalse(self.status()["available"])
        self.assertEqual(len(self.printer.messages), 1)

    def test_live_flags_override_stale_exported_status(self):
        self.start()
        self.printer.mcu.get_status = lambda _: dict(non_critical_disconnected=False)
        self.printer.mcu.non_critical_disconnected = True
        self.assertFalse(self.status()["available"])
        self.printer.mcu.non_critical_disconnected = False
        self.assertFalse(self.status()["available"])

    def test_reconnect_attempt_and_missed_disconnect_event(self):
        self.start()
        self.printer.mcu._reconnecting = True
        self.assertFalse(self.status()["available"])
        self.printer.mcu._reconnecting = False
        self.emit("danger:non_critical_mcu_SM:reconnected")
        self.assertFalse(self.status()["available"])

    def test_initial_absence_latches(self):
        self.printer.mcu.non_critical_disconnected = True
        self.start()
        self.assertFalse(self.status()["available"])
        self.assertTrue(self.status()["restart_required"])

    def test_shutdown_closes_gate(self):
        self.start()
        self.emit("klippy:shutdown")
        self.assertFalse(self.status()["available"])

    def test_critical_or_mainline_mcu_rejected(self):
        self.printer.mcu.is_non_critical = False
        with self.assertRaisesRegex(ValueError, "is_non_critical"):
            self.start()


class MacroGateTests(unittest.TestCase):
    def test_all_control_templates_inhibit_without_reading_sensors(self):
        count = 0
        for path in (ROOT / "config").glob("*.cfg"):
            config = configparser.RawConfigParser(strict=False)
            config.read(path, encoding="utf-8")
            for section in config.sections():
                if not section.startswith(("gcode_macro ", "delayed_gcode ")):
                    continue
                if section == "gcode_macro SM_LED_STATE":
                    continue
                source = config.get(section, "gcode", fallback="")
                if not source.strip():
                    continue
                count += 1
                for printer in ({}, {"sm2_connection": {"available": False}}):
                    with self.subTest(section=section, monitor_present=bool(printer)):
                        # Any attempt to read a temperature/output object fails:
                        # only the connection object exists in this fake printer.
                        rendered = JINJA.from_string(source).render(
                            printer=printer, params={})
                        commands = [line.strip() for line in rendered.splitlines()
                                    if line.strip() and not line.lstrip().startswith("#")]
                        self.assertTrue(all(line.startswith("RESPOND ")
                                            for line in commands), commands)
        self.assertGreater(count, 40)


if __name__ == "__main__":
    unittest.main()
