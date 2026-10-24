# Optional ambient-only BME280 driver for the pinned Kalico source.
# Copyright (C) 2026 David Yocis
# Distributed under the GNU GPLv3 license.
import math

from . import bme280
from .sm2_i2c import SM2I2CReport, SM2I2CError


class SM2BME280(bme280.BME280):
    """Reuse Bosch compensation, but own sampling and failure lifecycle.

    Not installer-enabled. Consumers must check valid before using values.
    The ordinary BME280 factory and all thermal protections are unchanged.
    """
    def __init__(self, config):
        if not config.get_name().startswith("temperature_sensor "):
            raise config.error("SM2_BME280 is for ambient temperature_sensor sections only")
        super().__init__(config)
        self.i2c = SM2I2CReport(self.i2c)
        self.valid = False
        self.last_sample_time = None
        self.last_error = "STARTING"
        self.failures = 0
        self.initialized = False
        self.connect_seen = False
        self.latched = False
        self.init_deadline = None
        self.lifecycle_timer = self.reactor.register_timer(self._tick)
        prefix = "danger:non_critical_mcu_%s:" % self.mcu.get_name()
        self.printer.register_event_handler(prefix + "disconnected", self._lost)
        self.printer.register_event_handler("klippy:shutdown", self._lost)
        self.printer.register_event_handler("klippy:disconnect", self._lost)

    def _lost(self, *args):
        self.latched = True
        self.valid = False
        self.last_error = "RESTART_REQUIRED"
        self.reactor.update_timer(self.lifecycle_timer, self.reactor.NEVER)

    def handle_connect(self):
        if self.connect_seen:
            self._lost()
            return
        self.connect_seen = True
        self.reactor.update_timer(self.lifecycle_timer, self.reactor.NOW)

    def read_id(self):
        chip_id = super().read_id()
        if chip_id != 0x60:
            raise SM2I2CError("SM2_BME280 requires chip ID 0x60")
        return chip_id

    def read_register(self, reg_name, read_len):
        if self.init_deadline is not None and self.reactor.monotonic() >= self.init_deadline:
            raise SM2I2CError("BME280 initialization timed out")
        return super().read_register(reg_name, read_len)

    def _initialize(self):
        self.init_deadline = self.reactor.monotonic() + 2.0
        self.sample_timer = None
        try:
            self._init_bmxx80()
            if not self.dig.get("T1") or not self.dig.get("P1"):
                raise SM2I2CError("BME280 invalid calibration coefficients")
            self.initialized = True
        finally:
            # The base initializer creates a sampling timer. We use exactly
            # one lifecycle timer, including across failed initialization.
            if self.sample_timer is not None:
                self.reactor.unregister_timer(self.sample_timer)
            self.sample_timer = self.lifecycle_timer
            self.init_deadline = None

    def _tick(self, eventtime):
        if self.failures >= 5:
            return self.reactor.NEVER
        if (self.latched or self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            self._lost()
            return self.reactor.NEVER
        try:
            if not self.initialized:
                self._initialize()
            data = self.read_register("PRESSURE_MSB", 8)
            temp_raw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
            pressure_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
            humid_raw = (data[6] << 8) | data[7]
            if temp_raw == 0x80000 or pressure_raw == 0x80000 or humid_raw == 0x8000:
                raise SM2I2CError("BME280 measurement disabled or unavailable")
            temp = self._compensate_temp(temp_raw)
            pressure = self._compensate_pressure_bme280(pressure_raw) / 100.0
            humidity = self._compensate_humidity_bme280(humid_raw)
            if not all(math.isfinite(v) for v in (temp, pressure, humidity)):
                raise SM2I2CError("BME280 non-finite measurement")
        except SM2I2CError as exc:
            self.valid = False
            self.last_error = str(exc)
            self.failures += 1
            # Retry at most five consecutive failures. Do not silently keep
            # polling a broken bus forever or resume after MCU reconnect.
            return (self.reactor.monotonic() + min(2 ** self.failures, 30)
                    if self.failures < 5 else self.reactor.NEVER)
        if (self.latched or self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            self._lost()
            return self.reactor.NEVER
        if temp < self.min_temp or temp > self.max_temp:
            self.valid = False
            self.last_error = "TEMPERATURE_OUT_OF_RANGE"
            self.printer.invoke_shutdown("SM2 BME280 temperature outside configured range")
            return self.reactor.NEVER
        self.temp, self.pressure, self.humidity = temp, pressure, humidity
        self.last_sample_time = self.reactor.monotonic()
        self.valid = True
        self.last_error = None
        self.failures = 0
        self._callback(self.mcu.estimated_print_time(self.last_sample_time), self.temp)
        return self.last_sample_time + bme280.REPORT_TIME

    def get_status(self, eventtime):
        if (self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            self._lost()
        age = None if self.last_sample_time is None else eventtime - self.last_sample_time
        valid = (self.valid and not self.latched and age is not None
                 and 0 <= age <= 3 * bme280.REPORT_TIME
                 and not self.mcu.non_critical_disconnected
                 and not getattr(self.mcu, "_reconnecting", False))
        return dict(temperature=self.temp if valid else None,
                    pressure=self.pressure if valid else None,
                    humidity=self.humidity if valid else None,
                    valid=bool(valid), sample_age=age,
                    last_sample_time=self.last_sample_time,
                    error=(self.last_error or "STALE") if not valid else None,
                    consecutive_errors=self.failures,
                    restart_required=self.latched or self.failures >= 5)


def load_config(config):
    heaters = config.get_printer().load_object(config, "heaters")
    heaters.add_sensor_factory("SM2_BME280", SM2BME280)
