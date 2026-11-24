# -*- coding: utf-8 -*-
"""製品のモーター番号（2020）が機械と違うときの、DGPR の2段階書込（偽物の機械だけ。実機にはつながない）。

1段目: 2020 と DGPR（2000 の #1 を 0）だけを書く。サーボの値（2021〜）は書かない
（DGPR を使うと機械が標準値で上書きするので、電源を入れ直したあとに書く）。
2段目: 電源を入れ直し、つなぎ直して控えを取り直し、残りの差を一覧にして書く。
"""

import unittest

from test_focas_write_dialog import WriteDialogBase


def _add_params(machine, lines, types):
    """偽機械に、土台に無い番号（2000・2020・2021）を足す（控えの丸ごと吸い出しにも入れる）。"""
    txt = machine.param_dat.decode("latin-1")
    marker = "N02165Q1"
    assert marker in txt
    txt = txt.replace(marker, "".join(lines) + marker, 1)
    machine.param_dat = txt.encode("latin-1")
    from fake_fwlib import split_param_text
    machine.params = split_param_text(txt)
    machine.types.update(types)


AX = "".join(f"A{i}P{{}}" for i in range(1, 3))


class DgprBase(WriteDialogBase):
    VALUES = {"2020": "299", "2021": "500"}

    def setUp(self):
        super().setUp()
        _add_params(self.client.machine, [
            "N02000Q1A1P00000010A2P00000010A3P00000010A4P01010011A5P00000010A6P00000010 \n",
            "N02020Q1A1P1A2P1A3P1A4P{}A5P1A6P1 \n".format(self.MACHINE_2020),
            "N02021Q1A1P0A2P0A3P0A4P8A5P0A6P0 \n",
        ], {2000: 68, 2020: 6, 2021: 6})
        from nd287_app import focas_helper as H
        self.settings["focas_verified_kinds"] = {"31i": [
            H.kind_key(6, "A4"), H.kind_key(68, "A4"), H.kind_key(17, ""), "byte/none"]}

    MACHINE_2020 = "100"

    def make_plan_fn(self, values=None, zero_params=("1850", "1851", "1852")):
        from nd287_app import param_build as P
        values = values or self.VALUES

        def plan_fn(raw, zero_scope="product", keep_motor=False, axis=None):
            # 本物の plan_fn と同じく、仮モーターなら 2020 を prepare に渡す前に除く
            v = {k: x for k, x in values.items() if not (keep_motor and k == "2020")}
            return P.prepare(raw, v, axis=axis or 4, reference="", zero_params=zero_params,
                             zero_scope=zero_scope)
        return plan_fn

    def nos(self, d):
        return [r[0] for r in self.rows(d)]

    def written(self):
        return [w[:2] for w in self.client.machine.writes]


class TestStage1(DgprBase):
    def test_first_stage_writes_only_2020_and_dgpr_with_other_bits_kept(self):
        d = self.dialog()
        self.go(d)
        rows = self.rows(d)
        self.assertEqual([(r[0], r[1]) for r in rows], [("N02000", "A4"), ("N02020", "A4")])
        dg = rows[0]
        self.assertEqual(dg[3], "01010011")
        self.assertEqual(dg[4], "01010001")          # #1 だけ 0。ほかのビットは機械の今のまま
        self.assertEqual(rows[1][3:5], ["100", "299"])
        self.assertNotIn("N02021", self.nos(d))      # サーボの値は1段目では書かない
        self.assertFalse(d.lbl_stage1.isHidden())
        self.assertIn("1段目", d.lbl_stage1.text())
        self.assertEqual(len(self.client.machine.writes), 0)     # 押すまで書かない
        d.b_write.click()
        self.wait(d)
        self.assertEqual(self.machine_value(2020, "A4"), "299")
        self.assertEqual(self.machine_value(2000, "A4"), "01010001")
        self.assertEqual(self.machine_value(2021, "A4"), "8")    # サーボの値は触っていない
        self.assertEqual(sorted(n for n, _ in self.written()), [2000, 2020])

    def test_after_writing_a_yellow_power_cycle_notice_is_shown_not_green(self):
        d = self.dialog()
        self.go(d)
        self.assertTrue(d.lbl_power.isHidden())
        d.b_write.click()
        self.wait(d)
        self.assertFalse(d.lbl_power.isHidden())
        self.assertIn("電源を入れ直してください", d.lbl_power.text())
        self.assertIn("つなぎ直して続ける", d.lbl_power.text())
        self.assertIn("#fef9c3", d.lbl_power.styleSheet())
        self.assertNotIn("#dcfce7", d.lbl_power.styleSheet())
        self.assertFalse(d.b_continue.isHidden())
        self.assertTrue(d.b_continue.isEnabled())

    def test_second_stage_after_power_cycle_lists_the_rest_and_writes_it(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        # 電源の入れ直し: DGPR は機械が 1 に戻し、サーボの値は標準値（0）になる（偽機械で再現）
        self.client.machine.set_param(2000, "A4", "01010011")
        self.client.machine.set_param(2021, "A4", "0")
        d.b_continue.click()
        self.wait(d)
        self.wait(d)
        self.assertIsNotNone(d._backup)               # 控えを取り直した
        self.assertIsNone(d._stage1)
        self.assertTrue(d.lbl_stage1.isHidden())
        self.assertTrue(d.lbl_power.isHidden())
        self.assertEqual(self.nos(d), ["N02021"])     # 残り（製品のサーボの値）だけ
        self.assertEqual(self.rows(d)[0][3:5], ["0", "500"])
        before = len(self.client.machine.writes)
        d.b_write.click()
        self.wait(d)
        self.assertEqual([n for n, _ in self.written()[before:]], [2021])
        self.assertEqual(self.machine_value(2021, "A4"), "500")
        self.assertEqual(self.machine_value(2020, "A4"), "299")

    def test_continue_button_is_hidden_until_first_stage_is_written(self):
        d = self.dialog()
        self.assertTrue(d.b_continue.isHidden())
        self.go(d)
        self.assertTrue(d.b_continue.isHidden())


class TestNoDgpr(DgprBase):
    MACHINE_2020 = "299"            # 機械の今の 2020 が製品と同じ

    def test_same_motor_has_no_dgpr_row_and_one_stage(self):
        d = self.dialog()
        self.go(d)
        self.assertNotIn("N02000", self.nos(d))
        self.assertEqual(self.nos(d), ["N02021"])
        self.assertIsNone(d._stage1)
        self.assertTrue(d.lbl_stage1.isHidden())
        d.b_write.click()
        self.wait(d)
        self.assertTrue(d.lbl_power.isHidden())
        self.assertTrue(d.b_continue.isHidden())
        self.assertEqual(self.machine_value(2000, "A4"), "01010011")     # DGPR は触らない


class TestTempMotor(DgprBase):
    def test_temporary_motor_never_writes_2020_or_dgpr(self):
        d = self.dialog(keep_motor=True, motor_id="100", product_axes=(4,))
        self.go(d)
        self.assertNotIn("N02000", self.nos(d))
        self.assertNotIn("N02020", self.nos(d))
        self.assertEqual(self.nos(d), ["N02021"])
        self.assertIsNone(d._stage1)
        d.b_write.click()
        self.wait(d)
        self.assertEqual(self.machine_value(2020, "A4"), "100")
        self.assertEqual(self.machine_value(2000, "A4"), "01010011")
        self.assertTrue(d.lbl_power.isHidden())


if __name__ == "__main__":
    unittest.main()
