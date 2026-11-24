# -*- coding: utf-8 -*-
import unittest

from nd287_app import wizard_steps as W


class TestSteps(unittest.TestCase):
    def test_step1_needs_number_search_check_motor(self):
        self.assertFalse(W.step_state(1)["can_next"])
        self.assertIn("探す", W.step_state(1, seiban="1")["missing"][0])
        self.assertIn("チェック", W.step_state(1, seiban="1", found=True)["missing"][0])
        r = W.step_state(1, seiban="1", found=True, selected=1)
        self.assertFalse(r["can_next"])
        self.assertIn("モーター", r["missing"][0])
        self.assertTrue(W.step_state(1, seiban="1", found=True, selected=1, motor="temp")["can_next"])

    def test_step1_non_fanuc_stops(self):
        r = W.step_state(1, seiban="1", found=True, selected=1, non_fanuc=True, motor="product")
        self.assertFalse(r["can_next"])
        self.assertIn("FANUC", r["missing"][0])

    def test_step2_needs_controller_and_basic(self):
        self.assertFalse(W.step_state(2)["can_next"])
        r = W.step_state(2, ctrl_chosen=True)
        self.assertIn("BASIC", r["missing"][0])
        self.assertTrue(W.step_state(2, ctrl_chosen=True, basic_ok=True)["can_next"])

    def test_summary_only_decided_things(self):
        self.assertEqual(W.summary(seiban="50014984", motor="temp", motor_id="299"),
                         "50014984 ／ 仮モーター（299）")


if __name__ == "__main__":
    unittest.main()
