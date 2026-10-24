"""Offline ambient sensor lifecycle against the pinned base driver."""
import importlib.util
from pathlib import Path
import sys
import struct
from types import ModuleType, SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
pkg = ModuleType("sm2_test")
pkg.__path__ = []
sys.modules[pkg.__name__] = pkg
bus = ModuleType("sm2_test.bus")
sys.modules[bus.__name__] = bus
danger = ModuleType("sm2_test.danger_options")
danger.get_danger_options = lambda: SimpleNamespace(temp_ignore_limits=False)
sys.modules[danger.__name__] = danger


def load(name, path):
    spec = importlib.util.spec_from_file_location("sm2_test." + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load("bme280", ROOT / "tests/fixtures/kalico_bme280.py")
transport = load("sm2_i2c", ROOT / "klippy/extras/sm2_i2c.py")
driver = load("sm2_bme280", ROOT / "klippy/extras/sm2_bme280.py")


class Reactor:
    NEVER = float("inf")
    NOW = 0
    def __init__(self):
        self.now = 100
        self.timers = []
    def monotonic(self): return self.now
    def pause(self, when): self.now = when
    def register_timer(self, cb):
        self.timers.append(cb)
        return cb
    def unregister_timer(self, timer): self.timers.remove(timer)
    def update_timer(self, timer, when): pass


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.reactor = Reactor()
        self.events = {}
        self.shutdowns = []
        self.samples = []
        self.sent = []
        self.config_callbacks = []
        self.printer = SimpleNamespace(
            get_reactor=lambda: self.reactor, command_error=TimeoutError,
            add_object=lambda *a: None, get_start_args=lambda: {},
            register_event_handler=lambda k, v: self.events.setdefault(k, []).append(v),
            invoke_shutdown=self.shutdowns.append)
        self.mcu = SimpleNamespace(
            get_printer=lambda: self.printer, get_name=lambda: "SM",
            is_non_critical=True, non_critical_disconnected=False, _reconnecting=False,
            register_config_callback=self.config_callbacks.append,
            get_non_critical_reconnect_event_name=lambda: "danger:non_critical_mcu_SM:reconnected",
            estimated_print_time=lambda t: t,
            lookup_query_command=lambda *a, **kw: SimpleNamespace(send=self.send))
        bus.MCU_I2C_from_config = lambda *a, **kw: SimpleNamespace(
            get_mcu=lambda: self.mcu, get_oid=lambda: 7,
            get_i2c_address=lambda: 0x77, get_command_queue=lambda: "queue")
        self.config = SimpleNamespace(get_printer=lambda: self.printer,
            get_name=lambda: "temperature_sensor BME_IN",
            getint=lambda name, default: default, error=ValueError)
        self.sensor = driver.SM2BME280(self.config)
        self.config_callbacks[0]()
        self.sensor.setup_callback(lambda t, v: self.samples.append(v))
        self.sensor.setup_minmax(0, 100)
        self.sensor.initialized = True
        # Bosch reference temperature/pressure calibration example.
        self.sensor.dig = dict(T1=27504, T2=26435, T3=-1000,
            P1=36477, P2=-10685, P3=3024, P4=2855, P5=140,
            P6=-7, P7=15500, P8=-14600, P9=6000,
            H1=75, H2=362, H3=0, H4=325, H5=50, H6=30)
        self.data = bytes.fromhex("655ac07eed007530")
        self.bus_status = 0

    def send(self, args, **kwargs):
        self.sent.append(args)
        return dict(oid=7, status=self.bus_status,
                    response=self.data if args[2] else b"")

    def status(self): return self.sensor.get_status(self.reactor.now)

    def test_real_compensation_and_freshness(self):
        self.sensor._tick(100)
        self.assertAlmostEqual(self.status()["temperature"], 25.08, places=2)
        self.assertAlmostEqual(self.status()["pressure"], 1006.53, places=2)
        self.assertTrue(self.status()["valid"])
        self.reactor.now += 2.5
        self.assertFalse(self.status()["valid"])
        self.assertIsNone(self.status()["temperature"])
        self.assertEqual(self.status()["error"], "STALE")

    def test_error_invalidates_all_fields_without_publishing_zero(self):
        self.sensor._tick(100)
        self.bus_status = 1
        self.assertEqual(self.sensor._tick(100), 102)
        for field in ("temperature", "humidity", "pressure"):
            self.assertIsNone(self.status()[field])
        self.assertEqual(len(self.samples), 1)
        self.assertEqual(self.shutdowns, [])

    def test_bounded_retry_and_successful_transient_recovery(self):
        self.bus_status = 2
        self.sensor._tick(100)
        self.bus_status = 0
        self.sensor._tick(100)
        self.assertTrue(self.status()["valid"])
        self.assertEqual(self.sensor.failures, 0)
        self.bus_status = 2
        for _ in range(5): last = self.sensor._tick(100)
        self.assertEqual(last, self.reactor.NEVER)
        count = len(self.sent)
        self.sensor._tick(100)
        self.assertEqual(len(self.sent), count)
        self.assertTrue(self.status()["restart_required"])

    def test_disconnect_does_not_rearm_after_reconnect(self):
        self.sensor.handle_connect()
        self.sensor._tick(100)
        self.mcu.non_critical_disconnected = True
        self.assertFalse(self.status()["valid"])
        self.mcu.non_critical_disconnected = False
        self.sensor.handle_connect()
        count = len(self.sent)
        self.assertEqual(self.sensor._tick(100), self.reactor.NEVER)
        self.assertEqual(len(self.sent), count)
        self.assertTrue(self.status()["restart_required"])

    def test_temperature_limit_still_shuts_down(self):
        self.sensor.setup_minmax(0, 20)
        self.sensor._tick(100)
        self.assertEqual(len(self.shutdowns), 1)
        self.assertFalse(self.status()["valid"])
        self.assertEqual(self.samples, [])

    def test_disabled_measurement_is_not_a_sample(self):
        self.data = bytes.fromhex("655ac08000007530")
        self.sensor._tick(100)
        self.assertFalse(self.status()["valid"])
        self.assertEqual(self.samples, [])

    def test_wrong_chip_rejected_before_reset(self):
        self.sensor.initialized = False
        self.data = b"\x58"
        self.sensor._tick(100)
        self.assertIn("chip ID", self.status()["error"])
        self.assertTrue(all(args[2] for args in self.sent))

    def test_stuck_initialization_is_bounded(self):
        self.sensor.initialized = False
        def reply(args, **kwargs):
            data = b"\x60" if args[1] == b"\xd0" else b"\x01" if args[2] else b""
            return dict(oid=7, status=0, response=data)
        self.sensor.i2c.command.send = reply
        self.sensor._tick(100)
        self.assertLess(self.reactor.now, 103)
        self.assertIn("timed out", self.status()["error"])
        self.assertEqual(len(self.reactor.timers), 1)

    def test_real_initializer_routes_every_transaction_through_adapter(self):
        self.sensor.initialized = False
        dig = self.sensor.dig
        cal1 = struct.pack("<HhhHhhhhhhhh", *(dig[k] for k in
            ("T1", "T2", "T3", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9")))
        cal1 += bytes([0, dig["H1"]])
        cal2 = struct.pack("<hBBBBb", dig["H2"], dig["H3"], dig["H4"] >> 4,
            (dig["H4"] & 15) | ((dig["H5"] & 15) << 4), dig["H5"] >> 4, dig["H6"])
        cal2 += bytes(9)
        registers = {0xd0: b"\x60", 0xf3: b"\x00", 0x88: cal1,
                     0xe1: cal2, 0xf7: self.data}
        def reply(args, **kwargs):
            self.sent.append(args)
            data = registers[args[1][0]] if args[2] else b""
            return dict(oid=7, status=0, response=data)
        self.sensor.i2c.command.send = reply
        self.sensor._tick(100)
        self.assertTrue(self.status()["valid"])
        self.assertAlmostEqual(self.sensor.temp, 25.08, places=2)
        self.assertEqual(len(self.reactor.timers), 1)
        self.assertTrue(any(args[1] == b"\xe0\xb6" for args in self.sent))

    def test_partial_initialization_does_not_leak_timers(self):
        self.sensor.initialized = False
        def reply(args, **kwargs):
            status = 1 if args[1] == b"\x88" else 0
            data = b"\x60" if args[1] == b"\xd0" else b"\x00" if args[2] else b""
            return dict(oid=7, status=status, response=data)
        self.sensor.i2c.command.send = reply
        for _ in range(5):
            self.sensor._tick(100)
            self.assertEqual(len(self.reactor.timers), 1)
        self.assertFalse(self.sensor.initialized)

    def test_heater_use_rejected(self):
        self.config.get_name = lambda: "heater_generic unsafe"
        with self.assertRaises(ValueError): driver.SM2BME280(self.config)


if __name__ == "__main__": unittest.main()
