# -*- coding: utf-8 -*-
import unittest

from nd287_app import servo_req as S

REQ = "90J0>=28.0|90J5>=13.0|90JP>=12.0"


class TestServoReq(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(S.parse_servo("90J0/26.0"), ("90J0", 26.0))
        self.assertEqual(S.parse_servo("90j5 13.0"), ("90J5", 13.0))
        self.assertIsNone(S.parse_servo(""))
        self.assertEqual(S.parse_requirement(REQ), {"90J0": 28.0, "90J5": 13.0, "90JP": 12.0})

    def test_old_version_is_refused_new_is_ok(self):
        ok, msg = S.check("90J0/26.0", REQ)
        self.assertIs(ok, False)
        self.assertIn("古い", msg)
        self.assertIs(S.check("90J0/28.0", REQ)[0], True)
        self.assertIs(S.check("90J5/12.0", REQ)[0], False)
        self.assertIs(S.check("90JP/12.0", REQ)[0], True)

    def test_unknown_is_not_a_block(self):
        self.assertIsNone(S.check("", REQ)[0])
        self.assertIsNone(S.check("90B6/0001", REQ)[0])      # 資料に載っていない系列は止めない（点検できないと出す）
        self.assertIsNone(S.check("90J0/26.0", "")[0])        # 規則が空なら点検しない

    def test_write_dialog_blocks_only_on_real_old_version(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import nd287_app.gui as G
        from PySide6 import QtWidgets
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        d = G.FocasWriteDialog(None, {}, lambda raw, zero_scope="product": None,
                               rule_ctx={"motor": "αiF2/5000-D", "rules": [], "servo_req": [REQ]})
        d._connected, d._wplan = True, {"blockers": [], "items": [1]}
        d._ident = {"cnc_id": "X", "servo": "90J0/26.0"}
        ok, reasons = d._writable()
        self.assertTrue(any("古い" in r for r in reasons))
        d._ident = {"cnc_id": "X", "servo": "90J0/28.0"}
        self.assertFalse(any("古い" in r for r in d._writable()[1]))
        d._ident = {"cnc_id": "X"}                              # 読めていない: 止めない
        self.assertFalse(any("古い" in r for r in d._writable()[1]))

    def test_manual_entry_is_checked_and_shown_only_when_needed(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import nd287_app.gui as G
        from PySide6 import QtWidgets
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        d = G.FocasWriteDialog(None, {}, lambda raw, zero_scope="product": None,
                               rule_ctx={"motor": "αiF2/5000-D", "rules": [], "servo_req": [REQ]})
        d._connected, d._wplan = True, {"blockers": [], "items": [1]}
        d._ident = {"cnc_id": "X"}
        d._refresh_enabled()
        self.assertFalse(d.servo_box.isHidden())               # 読めていない＋必要な版数がある → 入力欄を出す
        d.e_servo_manual.setText("90J0 26.0")
        self.assertTrue(any("古い" in r for r in d._writable()[1]))
        d.e_servo_manual.setText("90J0 28.0")
        self.assertFalse(any("古い" in r for r in d._writable()[1]))
        d._ident = {"cnc_id": "X", "servo": "90J0/28.0"}        # 機械から読めたら欄は隠す
        d._refresh_enabled()
        self.assertTrue(d.servo_box.isHidden())
        d2 = G.FocasWriteDialog(None, {}, lambda raw, zero_scope="product": None, rule_ctx={"motor": "αiF2/5000", "servo_req": []})
        d2._connected, d2._ident = True, {"cnc_id": "X"}
        d2._refresh_enabled()
        self.assertTrue(d2.servo_box.isHidden())               # -D でないモーターには出さない
