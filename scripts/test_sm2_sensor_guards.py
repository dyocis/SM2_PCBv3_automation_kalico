"""Render actual macros with invalid ambient sensors; off paths must survive."""
import unittest
from test_peltier_thermal import JINJA, MACROS, make_printer
from test_temperature_auto import AUTOMATION
from test_uv_thermal import printer_state


class GuardTests(unittest.TestCase):
    def render(self, name, printer, **params):
        printer["sm2_connection"] = dict(available=True, sensors_ready=False)
        return JINJA.from_string((MACROS | AUTOMATION)[name]).render(printer=printer, params=params)

    def test_uv_on_inhibited_but_off_still_cools(self):
        p = printer_state(uv_active=True)
        on = self.render("NEVERMORE_UV", p, VALUE=1)
        self.assertIn("NEVERMORE_SENSOR_INHIBIT", on)
        self.assertNotIn("SET_PIN PIN=uv VALUE=1", on)
        off = self.render("NEVERMORE_UV", p, VALUE=0)
        self.assertIn("SET_PIN PIN=uv VALUE=0", off)
        self.assertIn("ID=NEVERMORE_UV_COOLDOWN_COMPLETE", off)
        self.assertIn("NEVERMORE_FILTER SPEED=1", off)

    def test_peltier_on_inhibited_but_off_still_cools(self):
        p = make_printer(active=True)
        on = self.render("NEVERMORE_PELTIER", p, VALUE=1)
        self.assertIn("NEVERMORE_SENSOR_INHIBIT", on)
        self.assertNotIn("SET_PIN PIN=peltier VALUE=1", on)
        off = self.render("NEVERMORE_PELTIER", p, VALUE=0)
        self.assertIn("SET_PIN PIN=peltier VALUE=0", off)
        self.assertIn("ID=NEVERMORE_PELTIER_COOLDOWN_COMPLETE", off)
        self.assertIn("NEVERMORE_FILTER SPEED=1", off)

    def test_queued_enable_rechecks_sensor_state(self):
        result = self.render("NEVERMORE_PELTIER_ENABLE", make_printer(pending=True))
        self.assertIn("NEVERMORE_SENSOR_INHIBIT", result)
        self.assertNotIn("SET_PIN PIN=peltier VALUE=1", result)

    def test_inhibit_routes_active_outputs_to_off_and_cancels_pending(self):
        p = make_printer(active=True, pending=True)
        p["gcode_macro SM_LED_STATE"]["uv"] = 1
        result = self.render("NEVERMORE_SENSOR_INHIBIT", p)
        self.assertIn("NEVERMORE_PELTIER VALUE=0 AUTO=1", result)
        self.assertIn("NEVERMORE_UV VALUE=0 AUTO=1", result)
        self.assertIn("ID=NEVERMORE_PELTIER_ENABLE DURATION=0", result)

    def test_ambient_consumers_do_not_evaluate_missing_sensor_objects(self):
        for name in ("NEVERMORE_AUTO_CONTROL", "NEVERMORE_AUTO_PELTIER",
                     "NEVERMORE_PURGE_CONTROL", "NEVERMORE_POST_PRINT_VENT_START",
                     "NEVERMORE_UPDATE_SENSORS", "NEVERMORE_SGP_CALIBRATION_START",
                     "SGP40_CALIBRATION_PREP", "SGP40_CALIBRATION_GUARD",
                     "SGP40_CALIBRATION_TICK", "SGP40_CALIBRATION_FINISH"):
            with self.subTest(macro=name):
                result = self.render(name, {})
                self.assertIn("NEVERMORE_SENSOR_INHIBIT", result)
                self.assertNotIn("SET_PIN", result)
                self.assertNotIn("SAVE_CONFIG", result)
                self.assertNotIn("CALIBRATE_SGP40", result)
                if name.startswith("SGP40_CALIBRATION_"):
                    self.assertIn("NEVERMORE_SGP_CALIBRATION_ABORT", result)

    def test_status_reports_unavailable_without_cached_values(self):
        result = self.render("NEVERMORE_STATUS_JSON", {})
        self.assertIn('"telemetry_valid":false', result)

    def test_thermal_trip_still_runs_without_ambient_reading(self):
        p = make_printer(active=True, hot=90)
        del p["temperature_sensor BME_IN"]
        result = self.render("NEVERMORE_PELTIER_THERMAL_TRIP", p)
        self.assertIn("SET_PIN PIN=peltier VALUE=0", result)
        self.assertIn("ID=NEVERMORE_PELTIER_COOLDOWN_COMPLETE", result)
        self.assertIn("VARIABLE=peltier_fault_chamber VALUE=-1", result)

    def test_uv_cooldown_completion_not_blocked(self):
        result = self.render("NEVERMORE_UV_COOLDOWN_COMPLETE", {})
        self.assertIn("VARIABLE=uv_cooldown_active VALUE=0", result)


if __name__ == "__main__": unittest.main()
