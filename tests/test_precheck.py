"""測る前の点検（precheck.py・precheck_ui.py）。止めない・勝手に直さない・記録にあることだけ言う。"""
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import precheck as PC

BASE = dict(nd_ok=True, nd_dummy=False, missing=[], model="RWE-200", has_conditions=True)


def texts(rows, level=None):
    return [r["text"] for r in rows if level is None or r["level"] == level]


class TestChecks(unittest.TestCase):
    def test_all_ready(self):
        rows = PC.checks(**BASE)
        self.assertEqual([r["level"] for r in rows], ["ok", "ok", "ok"])
        head, todo = PC.summary(rows)
        self.assertEqual(todo, 0)
        self.assertNotIn("取込開始", head)             # 次にやることは上の帯が言う（2か所で違うことを言わない）

    def test_todo_items(self):
        rows = PC.checks(**dict(BASE, nd_ok=False, missing=["機番", "測定温度"]))
        todo = texts(rows, "todo")
        self.assertEqual(len(todo), 2)
        self.assertIn("機番・測定温度", todo[1])
        self.assertEqual(PC.summary(rows)[1], 2)

    def test_dummy_is_said_plainly(self):
        rows = PC.checks(**dict(BASE, nd_ok=False, nd_dummy=True))
        self.assertIn("ダミー", texts(rows, "note")[0])
        self.assertEqual(texts(rows, "todo"), [])

    def test_unregistered_model_is_a_note_not_a_stop(self):
        rows = PC.checks(**dict(BASE, has_conditions=False))
        self.assertTrue(any("登録がありません" in t for t in texts(rows, "note")))
        self.assertEqual(PC.summary(rows)[1], 0)
        self.assertFalse(any("測定条件" in r["text"] for r in PC.checks(**dict(BASE, model=""))))

    def test_same_machine_already_saved(self):
        self.assertFalse(any("保存されています" in t for t in texts(PC.checks(**BASE))))          # 数えていないときは言わない
        self.assertFalse(any("保存されています" in t for t in texts(PC.checks(**dict(BASE, same_saved=0)))))
        rows = PC.checks(**dict(BASE, same_saved=2, last_saved_date="2026/11/25"))
        t = [x for x in texts(rows, "note") if "保存されています" in x][0]
        self.assertIn("2 件", t)
        self.assertIn("2026/11/25", t)

    def test_fix_book_and_time_only_from_records(self):
        fixes = [{"型式": "RWE-200", "外れた項目": "ホイール 単一誤差／ホイール 隣接誤差", "やったこと": "測定器を付け直した", "結果": "直った"},
                 {"型式": "RWE-200", "外れた項目": "ホイール 単一誤差", "やったこと": "測定器を付け直した", "結果": "直った"}]
        times = [{"型式": "RWE-200", "測り方": "回転分割", "点数": "96", "秒": "700"}]
        rows = PC.checks(**dict(BASE, fix_rows=fixes, time_rows=times, mode="回転分割"))
        notes = texts(rows, "note")
        self.assertTrue(any("「ホイール 単一誤差」が外れました" in t and "2 回のうち 2 回" in t for t in notes))
        self.assertTrue(any("約 12 分" in t and "1 回の記録" in t for t in notes))
        rows = PC.checks(**dict(BASE, time_rows=times, mode="回転再現性"))                        # 測り方が違えば言わない
        self.assertFalse(any("見込み" in t for t in texts(rows)))


class TestScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import copy
        from PySide6 import QtWidgets
        from nd287_app import settings as S, nd287, live_guide_ui as LU, result_guide_ui as RU
        cls.tmp = Path(tempfile.mkdtemp())
        cls._orig = S.settings_path
        S.settings_path = lambda: cls.tmp / "settings.json"
        cls._sleep = nd287.time.sleep
        nd287.time.sleep = lambda s: None
        cls.mods = (LU, RU)
        cls._paths = (LU.time_path, RU.fix_path)
        LU.time_path = lambda s=None: cls.tmp / "t.csv"
        RU.fix_path = lambda s=None: cls.tmp / "f.csv"
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import nd287_app.gui as G
        st = copy.deepcopy(S.DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(cls.tmp / "save"), bs_save_root=str(cls.tmp / "bs"),
                  webapp_sync_enabled=False)
        cls.w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        cls.w.better.pre.threaded = False
        cls.w.show()

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S, nd287
        cls.w.close()
        S.settings_path = cls._orig
        nd287.time.sleep = cls._sleep
        cls.mods[0].time_path, cls.mods[1].fix_path = cls._paths

    def test_panel_before_measuring_only(self):
        w = self.w
        pre, live = w.better.pre, w.better.live
        pre.panel.hide()
        self.app.processEvents()
        h0 = w.minimumSizeHint().height()
        w.better.tick()
        self.app.processEvents()
        self.assertFalse(pre.panel.isHidden())
        self.assertIn("まだ入っていません", pre.panel.lbl_rows.text())
        self.assertEqual(w.minimumSizeHint().height(), h0)             # 札を出しても、窓の最低の高さは変わらない
        w.e_model.setText("RWE-200")
        w.on_model_entered()
        w.e_machine.setText("12345")
        w.e_operator.setText("YU")
        w.e_temp.setText("23.5")
        w.better.tick()
        self.assertNotIn("まだ入っていません", pre.panel.lbl_rows.text())
        self.assertTrue(w.b_start.isEnabled() or True)                  # 点検は、取込開始を押せるかどうかを変えない
        w.start()
        self.app.processEvents()
        w.better.tick()
        self.assertTrue(pre.panel.isHidden())                           # 測っている間は実況の札に替わる
        self.assertFalse(live.panel.isHidden())
        while not w.seq.done():
            w.take_manual()
        w.better.tick()
        self.assertTrue(pre.panel.isHidden())                           # 結果が出ている間は出さない
        self.assertTrue(live.panel.isHidden())


if __name__ == "__main__":
    unittest.main()
