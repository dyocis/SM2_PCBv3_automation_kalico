"""Notification transitions and incident accounting, without printer access."""
import unittest
from types import SimpleNamespace
from test_sm2_bme280 import health, transport


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.messages = []
        printer = SimpleNamespace(
            get_reactor=lambda: SimpleNamespace(monotonic=lambda: self.now),
            lookup_object=lambda name: SimpleNamespace(respond_info=self.messages.append))
        self.health = health.SensorHealth(printer, "SGP_IN")

    def fail(self, kind="i2c_bus", exhausted=False):
        self.health.failure(transport.SM2I2CError("test " + kind, kind), exhausted)

    def test_retries_are_one_incident_and_recovery_requires_explicit_sample(self):
        for _ in range(4): self.fail()
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.health.get_status()["incident_count"], 1)
        self.assertEqual(self.health.get_status()["failed_attempt_count"], 4)
        self.assertEqual(self.health.get_status()["incidents_by_kind"], {"i2c_bus": 1})
        self.now = 22
        self.health.recovered()
        self.health.recovered()
        self.assertEqual(len(self.messages), 2)
        self.assertIn("12.0s", self.messages[-1])
        self.assertFalse(self.health.get_status()["fault_active"])

    def test_exhaustion_message_once(self):
        self.fail()
        self.fail(exhausted=True)
        self.fail(exhausted=True)
        self.assertEqual(len(self.messages), 2)
        self.assertTrue(self.health.get_status()["retries_exhausted"])

    def test_error_categories_do_not_count_crc_or_reference_as_bus_errors(self):
        for kind in ("i2c_bus", "crc", "reference", "transport"):
            self.fail(kind)
        self.assertEqual(self.health.get_status()["errors_by_kind"],
                         dict(i2c_bus=1, crc=1, reference=1, transport=1))

    def test_console_rate_limit_preserves_counts_and_log_events(self):
        self.fail()
        self.health.recovered()
        self.now += 1
        with self.assertLogs(level="WARNING") as logs:
            self.fail()
            self.health.recovered()
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(len(logs.output), 2)
        self.assertEqual(self.health.get_status()["incident_count"], 2)
        self.now += 60
        self.fail()
        self.assertEqual(len(self.messages), 3)

    def test_timestamps_session_duration_and_status_copy(self):
        self.fail()
        self.now += 5
        self.health.recovered()
        status = self.health.get_status()
        self.assertEqual(status["observed_seconds"], 5)
        self.assertTrue(status["last_fault_utc"].endswith("+00:00"))
        self.assertTrue(status["last_recovery_utc"].endswith("+00:00"))
        status["errors_by_kind"].clear()
        self.assertEqual(self.health.get_status()["errors_by_kind"]["i2c_bus"], 1)


if __name__ == "__main__": unittest.main()
