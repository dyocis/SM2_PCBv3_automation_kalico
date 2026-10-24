"""Integrated sensor failures with the pinned real VOC algorithm."""
import copy
import unittest
import test_sm2_sgp40 as sgp_tests
import test_sm2_bme280 as bme_tests
from test_sm2_bme280 import ROOT, load

gia = load("real_gia", ROOT / "tests/fixtures/sgp40_gia.py")


class RealAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.h = sgp_tests.SGPTests()
        self.h.setUp()
        self.h.sensor._gia = gia.GasIndexAlgorithm(1.)
        self.h.sensor._gia.set_states(12372.094, 285.303)

    def warm(self):
        self.h.start()
        for _ in range(10):
            self.h.tick()
            self.h.reactor.now += 1

    def test_blackout_not_published_as_clean_air(self):
        self.h.start()
        self.h.tick()
        self.assertEqual(self.h.sensor.voc, 0)
        self.assertFalse(self.h.status()["valid"])
        self.assertEqual(self.h.status()["error"], "WARMING_UP")
        self.assertEqual(self.h.samples, [])
        for _ in range(10):
            self.h.reactor.now += 1
            self.h.tick()
        self.assertTrue(self.h.status()["valid"])
        self.assertTrue(all(value > 0 for value in self.h.samples))

    def test_crc_failure_does_not_train_real_algorithm(self):
        self.warm()
        before = copy.deepcopy(vars(self.h.sensor._gia))
        self.h.bad_crc = True
        self.h.tick()
        after = vars(self.h.sensor._gia).copy()
        before.pop("calibrating")
        after.pop("calibrating")
        self.assertEqual(before, after)
        self.assertFalse(self.h.status()["valid"])

    def test_actual_bme_failure_invalidates_sgp_compensation(self):
        bme = bme_tests.SensorTests()
        bme.setUp()
        bme.sensor._tick(100)
        self.h.sensor.references = {"temperature": bme.sensor, "humidity": bme.sensor}
        self.h.start()
        bme.reactor.now = self.h.reactor.now
        bme.sensor._tick(bme.reactor.now)
        self.h.tick()
        before = copy.deepcopy(self.h.sensor._gia.get_states())
        bme.bus_status = 1
        bme.sensor._tick(bme.reactor.now)
        count = len(self.h.sent)
        self.h.tick()
        self.assertEqual(count, len(self.h.sent))
        self.assertEqual(before, self.h.sensor._gia.get_states())
        self.assertIsNone(self.h.status()["gas"])

    def test_short_gap_resets_algorithm_and_restores_saved_states(self):
        self.warm()
        self.h.bad_crc = True
        self.h.tick()
        self.assertTrue(self.h.sensor.algorithm_interrupted)
        self.h.bad_crc = False
        self.h.tick()  # measurement start, no new sample yet
        self.h.reactor.now += 1
        self.h.tick()
        self.assertFalse(self.h.sensor.algorithm_interrupted)
        self.assertTrue(self.h.status()["valid"])

    def test_extended_interruption_does_not_reuse_old_learning(self):
        self.warm()
        self.h.bad_crc = True
        self.h.tick()
        self.h.bad_crc = False
        self.h.reactor.now += 601
        self.h.tick()
        self.h.tick()
        self.assertFalse(self.h.status()["valid"])
        self.assertTrue(self.h.status()["restart_required"])


if __name__ == "__main__": unittest.main()
