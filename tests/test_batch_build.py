# -*- coding: utf-8 -*-
"""型式ごとの測定プログラム一括作成"""
import tempfile
import unittest
from pathlib import Path

from nd287_app import batch_build, fanuc

COND = dict(model="RTH-538", close="", interval_h=80000, interval_w=2400,
            n_h=1, n_w=1, div1=45, div2=10, order=[])
COND_SEMI = dict(COND, close="セミ")
COND_NO_PITCH = dict(model="TN-450", close="", interval_h=0, interval_w=0,
                     n_h=1, n_w=1, div1=0, div2=0, order=[])


class TestProgramForModel(unittest.TestCase):
    def test_pitch_comes_from_conditions(self):
        # 間隔H=80000（0.0001°単位）÷ 1/N_H=1 → 8°刻み
        text = batch_build.program_for_model(COND, fanuc.FanucConfig(axis="Z"))
        self.assertIn("G00Z8.", text)
        self.assertIn("O0100(RTH-538)", text)

    def test_close_goes_into_the_comment(self):
        text = batch_build.program_for_model(COND_SEMI, fanuc.FanucConfig())
        self.assertIn("RTH-538", text)

    def test_generated_program_is_machine_readable(self):
        cfg = fanuc.FanucConfig(axis="Z")
        text = batch_build.program_for_model(COND, cfg)
        self.assertEqual(fanuc.validate(text, cfg), [])
        self.assertNotIn(";", text)

    def test_missing_pitch_raises(self):
        with self.assertRaises(ValueError):
            batch_build.program_for_model(COND_NO_PITCH, fanuc.FanucConfig())


class TestSafeName(unittest.TestCase):
    def test_replaces_path_characters(self):
        self.assertEqual(batch_build.safe_name("AB/CD"), "AB_CD")
        self.assertEqual(batch_build.safe_name(r"A\B"), "A_B")

    def test_empty_falls_back(self):
        self.assertEqual(batch_build.safe_name(""), "MODEL")
        self.assertEqual(batch_build.safe_name("..."), "MODEL")


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.out = Path(self.d.name)
        self.cfg = fanuc.FanucConfig(axis="Z")

    def tearDown(self):
        self.d.cleanup()

    def test_folder_layout(self):
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="folder")
        self.assertEqual(rep[0][2], "作成")
        self.assertTrue((self.out / "RTH-538" / "RTH-538.NC").exists())

    def test_flat_layout_for_memory_card(self):
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        self.assertEqual(rep[0][2], "作成")
        self.assertTrue((self.out / "RTH-538.NC").exists())

    def test_same_model_different_close_do_not_collide(self):
        rep = batch_build.batch_programs([COND, COND_SEMI], self.cfg, self.out,
                                         layout="flat")
        self.assertEqual([r[2] for r in rep], ["作成", "作成"])
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ["RTH-538.NC", "RTH-538_セミ.NC"])

    def test_existing_file_is_not_overwritten(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        before = (self.out / "RTH-538.NC").read_bytes()
        (self.out / "RTH-538.NC").write_bytes(b"KEEP")
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        self.assertEqual(rep[0][2], "既存のため飛ばした")
        self.assertEqual((self.out / "RTH-538.NC").read_bytes(), b"KEEP")
        self.assertTrue(before)

    def test_overwrite_option(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        (self.out / "RTH-538.NC").write_bytes(b"KEEP")
        rep = batch_build.batch_programs([COND], self.cfg, self.out, layout="flat",
                                         overwrite=True)
        self.assertEqual(rep[0][2], "作成")
        self.assertNotEqual((self.out / "RTH-538.NC").read_bytes(), b"KEEP")

    def test_unbuildable_model_is_reported_not_skipped(self):
        rep = batch_build.batch_programs([COND, COND_NO_PITCH], self.cfg, self.out)
        states = {r[0]: r[2] for r in rep}
        self.assertEqual(states["RTH-538"], "作成")
        self.assertEqual(states["TN-450"], "作れない")

    def test_written_bytes_are_machine_format(self):
        batch_build.batch_programs([COND], self.cfg, self.out, layout="flat")
        data = (self.out / "RTH-538.NC").read_bytes()
        self.assertIn(b"\n\r\r", data)
        self.assertNotIn(b";", data)

    def test_missing_out_dir_raises(self):
        with self.assertRaises(NotADirectoryError):
            batch_build.batch_programs([COND], self.cfg, self.out / "nope")

    def test_summarize(self):
        rep = batch_build.batch_programs([COND, COND_NO_PITCH], self.cfg, self.out)
        text = batch_build.summarize(rep)
        self.assertIn("作成 1件", text)
        self.assertIn("作れない 1件", text)


if __name__ == "__main__":
    unittest.main()


class TestFitToScreen(unittest.TestCase):
    """ダイアログは必ず画面に収まる大きさで開く。

    固定の大きさで開くと、小さいノートPCや拡大表示のときに画面から
    はみ出して下のボタンが押せなくなる。
    """

    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.QtWidgets = QtWidgets

    def test_large_request_is_capped_to_the_screen(self):
        from nd287_app.gui import fit_to_screen
        dlg = self.QtWidgets.QDialog()
        avail = self.app.primaryScreen().availableGeometry()
        w, h = fit_to_screen(dlg, 9999, 9999)
        self.assertLessEqual(w, avail.width())
        self.assertLessEqual(h, avail.height())
        dlg.deleteLater()

    def test_small_request_is_kept(self):
        from nd287_app.gui import fit_to_screen
        dlg = self.QtWidgets.QDialog()
        w, h = fit_to_screen(dlg, 400, 300)
        self.assertEqual((w, h), (400, 300))
        dlg.deleteLater()

    def test_never_smaller_than_usable(self):
        from nd287_app.gui import fit_to_screen
        dlg = self.QtWidgets.QDialog()
        w, h = fit_to_screen(dlg, 10, 10)
        self.assertGreaterEqual(w, 360)
        self.assertGreaterEqual(h, 280)
        dlg.deleteLater()


class TestDialogsFitOnSmallScreen(unittest.TestCase):
    """どのダイアログも 1366x768 のノートPCに収まること。

    文字サイズを上げると「ダイアログの最小サイズ」が画面を超え、
    下のボタンが押せなくなる事故が繰り返し起きた。最小サイズで判定する。
    """

    # ノートPC(1366x768)と、小さい画面(1024x768)の両方で見る。
    # タスクバーぶんを引いた実効の高さで判定する。
    SCREENS = ((1366, 730), (1024, 730))

    @classmethod
    def setUpClass(cls):
        """本番の settings.json を絶対に触らせない。

        画面を閉じると _persist() が走って save_settings が呼ばれる。
        パス未指定だとリポジトリ／exe と同じ場所の本番ファイルを
        素の既定値で書き潰す（実際に起きていた）。
        """
        import tempfile
        from pathlib import Path
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        import shutil
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _check(self, make):
        import copy
        from nd287_app import themes
        from nd287_app.settings import DEFAULTS
        bad = []
        for pt in (7, 12, 16, 22):
            themes.apply_font(self.app, pt)
            themes.apply_theme(self.app, themes.DEFAULT_THEME)
            dlg = make(copy.deepcopy(DEFAULTS))
            dlg.show()
            for _ in range(5):
                self.app.processEvents()
            m = dlg.minimumSizeHint()
            for (w, h) in self.SCREENS:
                if m.width() > w or m.height() > h:
                    bad.append(f"{pt}pt {m.width()}x{m.height()} > {w}x{h}")
                    break
            dlg.close()
            dlg.deleteLater()
            self.app.processEvents()
        themes.apply_font(self.app, 7)
        return bad

    def test_param_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.ParamDialog(None, s)), [])

    def test_param_wizard_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.ParamWizardDialog(None, s)), [])

    def test_param_wizard_dialog_step4(self):
        """かんたん作成の段④（測定プログラムを送る）。分け方・一覧・Gコード・長い文でも収まる。"""
        import nd287_app.gui as G

        def make(s):
            w = G.ParamWizardDialog(None, s)
            w._prog_auto = False
            w._p_masters = {}
            w._step = 4
            w._p_choice = None
            for k in ("one", "two"):
                w.rad_p[k].setEnabled(True)
            w.lbl_p_kind.setText("製品から回転か傾斜か分かりませんでした。回転として作ります。違うときは下で選び直してください。" * 2)
            w.cmb_p_kind.setVisible(True)
            w.lbl_p_why.setText("分割と再現性を別々に2本で作ります。" * 3)
            w.lbl_p_prev.setText("前回と同じプログラムです（2026-11-24 10:00:00・号機23で送りました）。" * 2)
            w.lbl_p_prev.setVisible(True)
            w.lbl_p_problem.setText("O100 が分割用と再現性用で重なっています" * 3)
            w.lbl_p_problem.setVisible(True)
            w.lbl_p_done.setText("機械の前で O0100・O0101 を選んでサイクルスタートしてください。運転はPCからは始めません。")
            w.lbl_p_done.setVisible(True)
            w.b_p_lf.setVisible(True)
            w.tbl_p.setRowCount(3)
            w.txt_p_code.setPlainText("O0100\n" * 50)
            w.b_p_detail.setChecked(True)
            w.b_prog.setVisible(True)
            w._apply_mode()
            return w
        self.assertEqual(self._check(make), [])

    def test_param_wizard_dialog_semi_rows(self):
        """かんたん作成の段①に出る「位置を読む検出器」（フルクロの製品のとき）。長い理由・根拠・一覧でも収まる。"""
        import nd287_app.gui as G

        def make(s):
            w = G.ParamWizardDialog(None, s)
            w._set_semi_visible(True, True)
            w.lbl_semi.setText("セミクロにはできません: 回転：CMR が 2 以外のため確かめていません（値: 4）" * 2)
            w.txt_semi.setPlainText("1821 レファレンスカウンタ　45000 → 2000（同じ型式の実物の組 1/1 組）\n" * 30)
            w.lbl_semi_ref.setText("同じ型式のセミクロ実物があります: R50014861.prm（参考）。計算した値と 7/8 番号が一致。"
                                   "違う番号: 1821（計算 2000 ／ 実物 91）" * 2)
            w.lbl_semi_ref.setVisible(True)
            w._apply_mode()
            return w
        self.assertEqual(self._check(make), [])

    def test_batch_program_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.BatchProgramDialog(None, s)), [])

    def test_basic_origin_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.BasicOriginDialog(None, [], "x", None)), [])

    def test_param_db_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(
            lambda s: G.ParamDBDialog(G.ParamDialog(None, s))), [])

    def test_settings_dialog(self):
        # ここで文字サイズを変えるので、この画面が入りきらないと
        # 大きくした本人が元に戻せなくなる
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.SettingsDialog(None, s)), [])

    def test_ftp_server_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.FtpServerDialog(None, s)), [])

    def test_focas_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.FocasDialog(None, s)), [])

    def test_focas_write_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(
            lambda s: G.FocasWriteDialog(None, s, lambda raw, zero_scope="product": None)), [])

    def test_focas_write_dialog_with_servo_entry_visible(self):
        import nd287_app.gui as G

        def make(s):
            d = G.FocasWriteDialog(None, s, lambda raw, zero_scope="product": None,
                                   rule_ctx={"motor": "αiF2/5000-D", "servo_req": ["90J0>=28.0"]})
            d.servo_box.setVisible(True)
            d.lbl_semi_band.setText("セミクロに変換して書きます" * 4)
            d.lbl_semi_band.setVisible(True)
            return d
        self.assertEqual(self._check(make), [])

    def test_controller_edit_and_rules_dialogs(self):
        import nd287_app.gui as G
        from nd287_app import controllers
        c = controllers.Controller("99", cnc="35i-MB", voltage="AC200V", caps={"X": "20A", "Y": "40A"},
                                   amps={"X": "A06B-6240-H206"}, b_corr="○", d_drive="○")
        self.assertEqual(self._check(lambda s: G.ControllerEditDialog(None, c)), [])
        self.assertEqual(self._check(lambda s: G.MotorRulesDialog(None, "no_such_rules.csv")), [])

    def test_focas_program_send_dialog(self):
        import nd287_app.gui as G
        from nd287_app import fanuc

        def make(s):
            nc = fanuc.nc_bytes("%\nO0555(TEST)\nM30\n%\n", fanuc.EOB_PUNCH)
            d = G.FocasProgramSendDialog(None, s, nc, fanuc.EOB_PUNCH)
            d.lbl_estop.setText("今は書けません。\n" + "\n".join(["運転中または停止中です（運転: 運転中・生の値 3）"] * 4))
            d.lbl_estop.setVisible(True)
            d.lbl_backup.setText("点検の指摘があります: " + "x" * 200)
            d.txt_result.setPlainText("結果の行\n" * 10)
            return d
        self.assertEqual(self._check(make), [])

    def test_focas_program_send_dialog_in_wizard_mode(self):
        import nd287_app.gui as G
        from nd287_app import fanuc

        def make(s):
            d = G.FocasProgramSendDialog(None, s, b"", fanuc.EOB_PUNCH, wizard=True)
            d.lbl_estop.setText("今は書けません。\n" + "\n".join(["運転中または停止中です"] * 4))
            d.lbl_estop.setVisible(True)
            d.txt_result.setPlainText("結果の行\n" * 10)
            return d
        self.assertEqual(self._check(make), [])

    def test_program_dialog_with_focas_send(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.ProgramDialog(None, s, {})), [])

    def test_focas_write_dialog_with_a_long_list_and_long_texts(self):
        """一覧が長くても・理由の文が長くても、最小サイズが画面を超えない。"""
        import nd287_app.gui as G

        def make(s):
            d = G.FocasWriteDialog(None, s, lambda raw, zero_scope="product": None)
            d.tbl.setRowCount(300)
            d.lbl_why.setText("書けません: " + " ／ ".join(["運転中または停止中です（運転: 運転中・生の値 3）"] * 6))
            d.lbl_summary.setText("機種 31i M　CNC ID 3C7B5D01-49C1F435-5E28C389-6592C486　" * 4)
            d.txt_result.setPlainText("結果の行" + chr(10) + "x" * 80)
            return d
        self.assertEqual(self._check(make), [])

    def test_help_dialog(self):
        import nd287_app.gui as G
        self.assertEqual(self._check(lambda s: G.HelpDialog(None)), [])
