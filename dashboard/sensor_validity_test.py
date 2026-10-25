"""Recorder must not resurrect unavailable readings from legacy caches."""
import unittest
from nevermore_history import make_sample, sample_plausibility_error


class ValidityTests(unittest.TestCase):
    def setUp(self):
        self.status = {
            "gcode_macro SM_LED_STATE": dict(temp_in=35, voc_in=150),
            "temperature_sensor BME_IN": dict(temperature=36),
            "temperature_sensor SGP_IN": dict(temperature=160),
            "bme280 BME_IN": dict(temperature=37, pressure=1000, humidity=40),
            "sgp40 SGP_IN": dict(gas=170),
        }

    def test_legacy_driver_values_preserved(self):
        sample = make_sample(self.status, 100)
        self.assertEqual((sample['ti'], sample['vi']), (36, 160))

    def test_invalid_driver_blocks_all_cached_fallbacks(self):
        self.status['bme280 BME_IN']['valid'] = False
        self.status['sgp40 SGP_IN']['valid'] = False
        sample = make_sample(self.status, 100)
        for key in ('ti', 'pi', 'hi', 'vi'):
            self.assertIsNone(sample[key])
        self.assertIsNotNone(sample_plausibility_error(sample))

    def test_valid_driver_values_are_authoritative(self):
        self.status['bme280 BME_IN']['valid'] = True
        self.status['sgp40 SGP_IN']['valid'] = True
        sample = make_sample(self.status, 100)
        self.assertEqual((sample['ti'], sample['vi']), (37, 170))

    def test_missing_numeric_value_does_not_fall_back(self):
        self.status['bme280 BME_IN'].update(valid=True, temperature=None)
        self.assertIsNone(make_sample(self.status, 100)['ti'])


if __name__ == '__main__': unittest.main()
