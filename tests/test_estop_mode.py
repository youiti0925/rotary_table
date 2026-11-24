# -*- coding: utf-8 -*-
"""パラメータを書くときの非常停止（2026-11-24 利用者が決定: 社内の手順は「パラメータは、非常停止で変更」）。"""
import unittest

from nd287_app import focas_ops as F


def status(emergency, run=0, motion=0):
    return {"values": {"run": run, "motion": motion, "emergency": emergency},
            "text": {"run": "x", "motion": "x", "emergency": "x"}, "interpreted": True}


ALARMS = [{"code": "SW0100", "text": "PWE"}]
BK = {"complete": True, "cnc_id": "X"}


def ok(mode, emergency, for_params=True, run=0):
    F.set_estop_mode(mode)
    try:
        w = F.writable_state(status(emergency, run=run), ALARMS, backup_info=BK, cnc_id="X", for_params=for_params)
        return w["ok"], w["reasons"]
    finally:
        F.set_estop_mode("estop_only")


class TestEstopMode(unittest.TestCase):
    def test_default_is_estop_only(self):
        self.assertEqual(F._ESTOP_MODE, "estop_only")
        self.assertEqual(F.set_estop_mode("でたらめ"), "estop_only")

    def test_estop_only(self):
        self.assertTrue(ok("estop_only", 1)[0])                    # 非常停止がかかっている → 書ける
        good, reasons = ok("estop_only", 0)                        # 解除している → 非常停止にしてと出る
        self.assertFalse(good)
        self.assertTrue(any("非常停止にして書きます" in r for r in reasons))

    def test_running_is_still_refused_in_estop(self):
        good, reasons = ok("estop_only", 1, run=3)                  # 運転中は、非常停止でも書かない
        self.assertFalse(good)
        self.assertTrue(any("運転中" in r for r in reasons))

    def test_released_only_and_either(self):
        self.assertTrue(ok("released_only", 0)[0])
        self.assertFalse(ok("released_only", 1)[0])
        self.assertTrue(ok("either", 0)[0])
        self.assertTrue(ok("either", 1)[0])

    def test_program_send_ignores_the_setting(self):
        for mode in ("estop_only", "either"):
            self.assertFalse(ok(mode, 1, for_params=False)[0], mode)   # 測定プログラムは、非常停止のままでは送らない
            self.assertTrue(ok(mode, 0, for_params=False)[0], mode)
