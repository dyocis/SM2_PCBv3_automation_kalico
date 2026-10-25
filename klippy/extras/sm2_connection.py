# SM2 connection gate for Kalico.
# Copyright (C) 2026 David Yocis
# This file may be distributed under the terms of the GNU GPLv3 license.


class SM2Connection:
    """Latch loss of SM communication; never silently re-arm automation.

    Observes connection lifecycle and validity reported by opt-in sensors.
    Legacy sensors provide no freshness guarantee. This monitor does not
    force physical outputs off or suppress firmware shutdowns.
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.mcu_name = config.get("mcu", "SM")
        if self.mcu_name == "mcu":
            raise config.error("sm2_connection requires a secondary MCU")
        self.config_error = config.error
        self.mcu = None
        self.ready = False
        self.latched = False
        self.reason = "STARTING"
        self.disconnect_count = 0
        self.sensors_armed = False
        self.sensor_latched = False
        self.sensor_mode_seen = False
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._shutdown)
        prefix = "danger:non_critical_mcu_%s:" % self.mcu_name
        self.printer.register_event_handler(prefix + "disconnected", self._lost)
        self.printer.register_event_handler(prefix + "reconnected", self._returned)

    def _connect(self):
        self.mcu = self.printer.lookup_object("mcu " + self.mcu_name)
        if not getattr(self.mcu, "is_non_critical", False):
            raise self.config_error(
                "sm2_connection requires Kalico and is_non_critical: True "
                "on [mcu %s]" % self.mcu_name)
        if not hasattr(self.mcu, "non_critical_disconnected"):
            raise self.config_error("Unsupported Kalico MCU connection API")
        if self.mcu.non_critical_disconnected:
            self._lost()

    def _ready(self):
        self.ready = True
        if not self.latched:
            self.reason = "READY"

    def _shutdown(self, *args):
        self.ready = False
        self.reason = "HOST_STOPPED"

    def _lost(self, *args):
        if not self.latched:
            self.printer.lookup_object("gcode").respond_info(
                "SM2 connection lost: automation inhibited. Reconnect the "
                "board, inspect its outputs, then restart Kalico while idle.",
                log=True)
        self.latched = True
        self.reason = "DISCONNECTED"
        self.disconnect_count += 1

    def _returned(self, *args):
        # A reconnect must latch even if a disconnect event was missed.
        self.latched = True
        self.reason = "RESTART_REQUIRED"

    def get_status(self, eventtime=None):
        if eventtime is None:
            eventtime = self.printer.get_reactor().monotonic()
        readings = []
        for name in ("bme280 BME_IN", "bme280 BME_OUT", "sgp40 SGP_IN", "sgp40 SGP_OUT"):
            sensor = self.printer.lookup_object(name, None)
            readings.append(sensor.get_status(eventtime) if sensor is not None else {})
        self.sensor_mode_seen |= any("valid" in reading for reading in readings)
        managed = self.sensor_mode_seen
        fresh = all(reading.get("valid") is True for reading in readings)
        if managed:
            if fresh:
                self.sensors_armed = True
            elif self.sensors_armed:
                self.sensor_latched = True
        sensors_ready = (not managed or fresh) and not self.sensor_latched
        disconnected = self.mcu is None or self.mcu.non_critical_disconnected
        reconnecting = bool(getattr(self.mcu, "_reconnecting", False))
        # Use the live MCU flags: the exported MCU status can lag a disconnect.
        # Reading the gate during that interval must also prevent later re-arm.
        if self.mcu is not None and (disconnected or reconnecting):
            self.latched = True
        connected = not disconnected and not reconnecting
        available = self.ready and connected and not self.latched
        reason = self.reason
        if disconnected:
            reason = "DISCONNECTED" if self.mcu is not None else "STARTING"
        elif reconnecting:
            reason = "RECONNECTING"
        elif self.latched:
            reason = "RESTART_REQUIRED"
        return dict(available=available, connected=connected,
                    restart_required=self.latched, reason=reason,
                    disconnect_count=self.disconnect_count,
                    sensors_ready=sensors_ready, sensors_managed=managed,
                    sensor_restart_required=self.sensor_latched)


def load_config(config):
    return SM2Connection(config)
