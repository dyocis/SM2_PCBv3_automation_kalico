# Sensor fault observability. No hardware commands or automatic resets.
import logging
from datetime import datetime, timezone


class SensorHealth:
    def __init__(self, printer, name):
        self.printer = printer
        self.name = name
        self.clock = printer.get_reactor().monotonic
        self.started = self.clock()
        self.failures = 0
        self.incidents = 0
        self.by_kind = {}
        self.incidents_by_kind = {}
        self.episode_kinds = set()
        self.active = False
        self.stopped = False
        self.last_fault = self.last_recovery = self.last_reason = None
        self.incident_started = None
        self.last_console_fault = float("-inf")
        self.announced = False

    def _message(self, message, console=True):
        logging.warning("SM2 sensor %s: %s", self.name, message)
        if console:
            # Direct response avoids gcode/config comment and quoting parsing.
            self.printer.lookup_object("gcode").respond_info(
                "SM2 sensor %s: %s" % (self.name, message))

    def failure(self, error, exhausted=False):
        now = self.clock()
        kind = getattr(error, "kind", "sensor")
        self.failures += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        self.last_reason = str(error)
        self.last_fault = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.active:
            self.episode_kinds = set()
            self.active = True
            self.incidents += 1
            self.incident_started = now
            self.announced = now - self.last_console_fault >= 60
            if self.announced:
                self.last_console_fault = now
            self._message("FAULT (%s): %s. Readings unavailable; incident %d."
                          % (kind, error, self.incidents), self.announced)
        if kind not in self.episode_kinds:
            if self.episode_kinds:
                self._message("Additional fault (%s) in incident %d: %s"
                              % (kind, self.incidents, error), False)
            self.episode_kinds.add(kind)
            self.incidents_by_kind[kind] = self.incidents_by_kind.get(kind, 0) + 1
        if exhausted and not self.stopped:
            self.stopped = True
            self._message("Retries exhausted; readings remain unavailable. "
                          "Inspect sensor/bus before restarting while idle.", self.announced)

    def recovered(self):
        if not self.active:
            return
        elapsed = self.clock() - self.incident_started
        self.last_recovery = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._message("RECOVERED after %.1fs: fresh valid sample received. "
                      "Session totals: %d incidents, %d failed attempts."
                      % (elapsed, self.incidents, self.failures), self.announced)
        self.active = False
        self.stopped = False

    def get_status(self):
        return dict(incident_count=self.incidents, failed_attempt_count=self.failures,
                    errors_by_kind=dict(self.by_kind), fault_active=self.active,
                    incidents_by_kind=dict(self.incidents_by_kind),
                    retries_exhausted=self.stopped, last_error=self.last_reason,
                    last_fault_utc=self.last_fault, last_recovery_utc=self.last_recovery,
                    observed_seconds=max(0., self.clock() - self.started))
