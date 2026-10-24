# Optional SGP40 driver for the SM2 reporting protocol.
# Copyright (C) 2026 David Yocis
# Distributed under the GNU GPLv3 license.
import math

from . import sgp40
from .sm2_i2c import SM2I2CReport, SM2I2CError
from .sm2_sensor_health import SensorHealth


class SM2SGP40(sgp40.SGP40):
    """Opt-in prototype. Requires validity-aware compensation and consumers."""
    def __init__(self, config):
        if not config.get_name().startswith("temperature_sensor "):
            raise config.error("SM2_SGP40 is for ambient temperature_sensor sections only")
        super().__init__(config)
        if not self.temp_sensor or not self.humidity_sensor:
            raise config.error("SM2_SGP40 requires temperature and humidity reference sensors")
        self.i2c = SM2I2CReport(self.i2c)
        self.health = SensorHealth(self.printer, self.name)
        self.valid = False
        self.last_sample_time = None
        self.last_error = "STARTING"
        self.failures = 0
        self.latched = False
        self.initialized = False
        self.pending_compensation = None
        self.references = {}
        self.step_timer = self.reactor.register_timer(self._handle_step)
        prefix = "danger:non_critical_mcu_%s:" % self.mcu.get_name()
        for event in (prefix + "disconnected", prefix + "reconnected",
                      "klippy:shutdown", "klippy:disconnect"):
            self.printer.register_event_handler(event, self._lost)

    def _lost(self, *args):
        self.latched = True
        self.valid = False
        self._measuring = False
        self.pending_compensation = None
        self._gia.calibrating = False
        self.last_error = "RESTART_REQUIRED"
        self.reactor.update_timer(self.step_timer, self.reactor.NEVER)

    def _handle_connect(self):
        # Do not invoke the upstream global reference-bus monkey patch.
        for field, name in (("temperature", self.temp_sensor),
                            ("humidity", self.humidity_sensor)):
            sensor = self.printer.lookup_object(name)
            status = sensor.get_status(self.reactor.monotonic())
            if field not in status or "valid" not in status:
                raise self.printer.config_error("SM2_SGP40 needs validity-aware reference " + name)
            self.references[field] = sensor
        if self._sync_peer_name:
            self._sync_peer = self.printer.lookup_object("sgp40 " + self._sync_peer_name)
            if not isinstance(self._sync_peer, SM2SGP40):
                raise self.printer.config_error("SM2_SGP40 sync peer must also use SM2_SGP40")

    def _handle_ready(self):
        super()._handle_ready()
        if not self.latched:
            self.reactor.update_timer(self.step_timer, self.reactor.NOW)

    def _init_sgp40(self):
        self.i2c.i2c_write(sgp40.HEATER_OFF_CMD)
        self._wait_ms(50)
        self.i2c.i2c_write(sgp40.SELF_TEST_CMD)
        self._wait_ms(500)
        if self._read()[0] != 0xD400:
            raise SM2I2CError("SGP40 self test failed")
        self.initialized = True

    def _read(self, count=1):
        if not isinstance(count, int) or not 1 <= count <= 10:
            raise SM2I2CError("Invalid SGP40 word count")
        response = self.i2c.i2c_read([], count * 3)["response"]
        if len(response) != count * 3:
            raise SM2I2CError("Incomplete SGP40 response")
        words = []
        for pos in range(0, len(response), 3):
            word = response[pos:pos + 2]
            if not sgp40._check_crc8(word, response[pos + 2]):
                raise SM2I2CError("SGP40 CRC mismatch", "crc")
            words.append(int.from_bytes(word, "big"))
        return words

    def _compensation(self, eventtime):
        values = {}
        for field, sensor in self.references.items():
            status = sensor.get_status(eventtime)
            value = status.get(field)
            if (status.get("valid") is not True or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise SM2I2CError("SGP40 reference %s unavailable" % field, "reference")
            values[field] = value
        if set(values) != {"temperature", "humidity"}:
            raise SM2I2CError("SGP40 compensation references unavailable")
        if not -45 <= values["temperature"] <= 130 or not 0 <= values["humidity"] <= 100:
            raise SM2I2CError("SGP40 compensation outside sensor range")
        return values["temperature"], values["humidity"]

    def _handle_step(self, eventtime):
        if self.failures >= 5:
            return self.reactor.NEVER
        if (self.latched or self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            self._lost()
            return self.reactor.NEVER
        try:
            if not self.initialized:
                self._init_sgp40()
            temp, humidity = self._compensation(self.reactor.monotonic())
            if self._measuring:
                raw = self._read()[0]  # CRC validation must precede algorithm learning.
                self._compensation(self.reactor.monotonic())
                if self.latched:
                    raise SM2I2CError("SGP40 disconnected during measurement")
                self._gia.calibrating = not self._is_hot(eventtime)
                if (self._sync_peer is not None
                        and self._sync_peer.get_status(self.reactor.monotonic())["valid"]):
                    self._gia.apply_variance_floor(self._sync_peer._gia)
                self.voc = self._gia.process(raw)
                self.raw = self._gia.raw
                self.temp, self.humidity = self.pending_compensation
                self.last_sample_time = self.reactor.monotonic()
                self.valid = True
                self.failures = 0
                self._callback(self.mcu.estimated_print_time(self.last_sample_time), self.voc)
                self._wait_ms(20)
            cmd = (sgp40.MEASURE_RAW_CMD_PREFIX + sgp40._humidity_to_ticks(humidity)
                   + sgp40._temperature_to_ticks(temp))
            self.i2c.i2c_write(cmd)
            self.pending_compensation = (temp, humidity)
            self._measuring = True
        except SM2I2CError as exc:
            self.valid = False
            self.last_error = str(exc)
            self._gia.calibrating = False
            self._measuring = False
            self.pending_compensation = None
            self.failures += 1
            self.health.failure(exc, exhausted=self.failures >= 5)
            return (self.reactor.monotonic() + min(2 ** self.failures, 30)
                    if self.failures < 5 and not self.latched else self.reactor.NEVER)
        self.last_error = None if self.valid else "WAITING_FOR_SAMPLE"
        if self.valid:
            self.health.recovered()
        return self.reactor.monotonic() + self._gia.sampling_interval

    def get_status(self, eventtime):
        if (self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            self._lost()
        age = None if self.last_sample_time is None else eventtime - self.last_sample_time
        valid = (self.valid and not self.latched and age is not None
                 and 0 <= age <= 3 * self._gia.sampling_interval)
        error = self.last_error or "STALE"
        if valid:
            try:
                self._compensation(eventtime)
            except SM2I2CError as exc:
                valid = False
                error = str(exc)
        return dict(temperature=self.temp if valid else None,
                    humidity=self.humidity if valid else None,
                    gas_raw=self.raw if valid else None, gas=self.voc if valid else None,
                    valid=bool(valid), sample_age=age,
                    last_sample_time=self.last_sample_time,
                    error=error if not valid else None,
                    consecutive_errors=self.failures,
                    restart_required=self.latched or self.failures >= 5,
                    health=self.health.get_status())

    def query_gcode(self, gcmd):
        status = self.get_status(self.reactor.monotonic())
        if not status["valid"]:
            gcmd.respond_info("SGP40 %s: reading unavailable (%s)" %
                              (self.name, status["error"]))
            return
        super().query_gcode(gcmd)

    def calibrate_gcode(self, gcmd):
        if not self.get_status(self.reactor.monotonic())["valid"]:
            raise gcmd.error("Cannot save SGP40 calibration while readings are unavailable")
        super().calibrate_gcode(gcmd)


def load_config(config):
    heaters = config.get_printer().load_object(config, "heaters")
    heaters.add_sensor_factory("SM2_SGP40", SM2SGP40)
