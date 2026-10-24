"""Exercise real upstream SGP40 initialization/CRC with a recording VOC algorithm."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

from test_sm2_bme280 import ROOT, Reactor, bus, load


class RecordingAlgorithm:
    def __init__(self, interval):
        self.sampling_interval = interval
        self.calibrating = False
        self.inputs = []
        self.raw = 0
        self.peers = []
    def set_states(self, *args): pass
    def get_states(self): return (12345, 285)
    def process(self, raw):
        self.inputs.append(raw)
        self.raw = raw
        return 100
    def apply_variance_floor(self, peer): self.peers.append(peer)
    def reset(self): self.inputs.clear()


gia = ModuleType("sm2_test.sgp40.gia")
gia.GasIndexAlgorithm = RecordingAlgorithm
sys.modules[gia.__name__] = gia
spec = importlib.util.spec_from_file_location("sm2_test.sgp40",
    ROOT / "tests/fixtures/sgp40_upstream.py", submodule_search_locations=[])
upstream = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = upstream
spec.loader.exec_module(upstream)
driver = load("sm2_sgp40", ROOT / "klippy/extras/sm2_sgp40.py")


def word(value):
    data = value.to_bytes(2, "big")
    return data + bytes([upstream._generate_crc(data)])


class SGPTests(unittest.TestCase):
    def setUp(self):
        self.reactor = Reactor()
        self.events, self.objects, self.sent = {}, {}, []
        self.callbacks, self.samples, self.saved = [], [], []
        self.reference = dict(valid=True, temperature=25., humidity=40.)
        self.objects["bme280 BME_IN"] = SimpleNamespace(get_status=lambda t: self.reference.copy())
        self.objects["gcode"] = SimpleNamespace(register_mux_command=lambda *a, **kw: None)
        self.objects["heaters"] = SimpleNamespace(lookup_heater=lambda n: SimpleNamespace(get_temp=lambda t: (20, 0)))
        self.objects["configfile"] = SimpleNamespace(set=lambda *a: self.saved.append(a))
        self.printer = SimpleNamespace(
            get_reactor=lambda: self.reactor, command_error=TimeoutError,
            config_error=ValueError, get_start_args=lambda: {},
            add_object=lambda k, v: self.objects.update({k: v}),
            lookup_object=lambda k: self.objects[k],
            register_event_handler=lambda k, v: self.events.setdefault(k, []).append(v))
        self.mcu = SimpleNamespace(get_printer=lambda: self.printer,
            get_name=lambda: "SM", is_non_critical=True,
            non_critical_disconnected=False, _reconnecting=False,
            register_config_callback=self.callbacks.append,
            estimated_print_time=lambda t: t,
            lookup_query_command=lambda *a, **kw: SimpleNamespace(send=self.send))
        bus.MCU_I2C_from_config = lambda *a, **kw: SimpleNamespace(
            get_mcu=lambda: self.mcu, get_oid=lambda: 7,
            get_i2c_address=lambda: 0x59, get_command_queue=lambda: "queue")
        self.config = SimpleNamespace(get_printer=lambda: self.printer,
            get_name=lambda: "temperature_sensor SGP_IN", error=ValueError,
            get=lambda k, default=None: "bme280 BME_IN" if k.startswith("ref_") else default,
            getlist=lambda k, default: default,
            getfloat=lambda k, default=None, **kw: default)
        self.sensor = driver.SM2SGP40(self.config)
        self.callbacks[0]()
        self.sensor.setup_callback(lambda t, v: self.samples.append(v))
        self.sensor._handle_connect()
        self.sensor._handle_ready()
        self.mode = None
        self.bad_crc = False
        self.status_code = 0
        self.self_test = 0xd400

    def send(self, args, **kw):
        self.sent.append(args)
        if args[2]:
            data = word(self.self_test if self.mode == "selftest" else 13000)
            if self.bad_crc: data = data[:-1] + bytes([data[-1] ^ 1])
        else:
            self.mode = "selftest" if args[1] == bytes(upstream.SELF_TEST_CMD) else "measure"
            data = b""
        return dict(oid=7, status=self.status_code, response=data)

    def tick(self): return self.sensor._handle_step(self.reactor.now)
    def status(self): return self.sensor.get_status(self.reactor.now)
    def start(self):
        self.tick()
        self.reactor.now += 1

    def test_first_trigger_does_not_publish_a_fabricated_sample(self):
        self.start()
        self.assertFalse(self.status()["valid"])
        self.assertEqual(self.samples, [])
        self.tick()
        self.assertTrue(self.status()["valid"])
        self.assertEqual(self.sensor._gia.inputs, [13000])
        self.assertEqual(self.samples, [100])
        self.assertEqual(len(self.reactor.timers), 1)

    def test_crc_error_never_reaches_algorithm_or_callback(self):
        self.start()
        self.bad_crc = True
        self.tick()
        self.assertEqual(self.sensor._gia.inputs, [])
        self.assertEqual(self.samples, [])
        self.assertFalse(self.status()["valid"])
        self.assertIn("CRC", self.status()["error"])

    def test_repeated_bad_crc_exhausts_retries_despite_successful_triggers(self):
        self.start()
        self.bad_crc = True
        for _ in range(9): self.tick()
        self.assertEqual(self.sensor.failures, 5)
        count = len(self.sent)
        self.assertEqual(self.tick(), self.reactor.NEVER)
        self.assertEqual(len(self.sent), count)

    def test_invalid_compensation_stops_learning_and_bus_traffic(self):
        self.start()
        for change in (dict(valid=False), dict(temperature=None),
                       dict(temperature=float("nan")), dict(humidity=101)):
            self.reference = dict(valid=True, temperature=25., humidity=40.)
            self.reference.update(change)
            count = len(self.sent)
            self.tick()
            self.assertEqual(len(self.sent), count)
        self.assertEqual(self.sensor._gia.inputs, [])

    def test_sample_uses_compensation_from_measurement_start(self):
        self.start()
        self.reference["temperature"] = 30
        self.tick()
        self.assertEqual(self.status()["temperature"], 25)
        self.assertEqual(self.sensor.pending_compensation, (30, 40))

    def test_reference_failure_during_read_discards_sample(self):
        self.start()
        original = self.sensor.i2c.command.send
        def fail_ref(*args, **kwargs):
            response = original(*args, **kwargs)
            self.reference["valid"] = False
            return response
        self.sensor.i2c.command.send = fail_ref
        self.tick()
        self.assertEqual(self.sensor._gia.inputs, [])

    def test_selftest_failure_is_not_accepted(self):
        self.self_test = 0x0000
        self.tick()
        self.assertFalse(self.sensor.initialized)
        self.assertIn("self test", self.status()["error"])
        self.assertEqual(self.samples, [])

    def test_stale_status_and_calibration_save_are_rejected(self):
        self.start()
        self.tick()
        self.reactor.now += 4
        self.assertIsNone(self.status()["gas"])
        gcmd = SimpleNamespace(error=ValueError, respond_info=lambda s: None)
        with self.assertRaises(ValueError): self.sensor.calibrate_gcode(gcmd)
        self.assertEqual(self.saved, [])

    def test_disconnect_latches_after_flags_clear(self):
        self.start()
        self.mcu.non_critical_disconnected = True
        self.assertFalse(self.status()["valid"])
        self.mcu.non_critical_disconnected = False
        count = len(self.sent)
        self.sensor._handle_ready()
        self.assertEqual(self.tick(), self.reactor.NEVER)
        self.assertEqual(len(self.sent), count)

    def test_transport_errors_exhaust_retries(self):
        self.status_code = 2
        for _ in range(5): self.tick()
        self.assertTrue(self.status()["restart_required"])
        self.assertEqual(self.samples, [])

    def test_status_inhibits_immediately_when_reference_becomes_invalid(self):
        self.start()
        self.tick()
        self.reference["valid"] = False
        self.assertFalse(self.status()["valid"])
        self.assertIsNone(self.status()["gas"])

    def test_missing_validity_reference_is_configuration_error(self):
        del self.reference["valid"]
        with self.assertRaises(ValueError): self.sensor._handle_connect()

    def test_valid_calibration_save_preserves_upstream_config_fields(self):
        self.start()
        self.tick()
        self.sensor.calibrate_gcode(SimpleNamespace(error=ValueError, respond_info=lambda s: None))
        self.assertEqual([args[:2] for args in self.saved],
                         [("temperature_sensor SGP_IN", "voc_mean"),
                          ("temperature_sensor SGP_IN", "voc_stddev")])


if __name__ == "__main__": unittest.main()
