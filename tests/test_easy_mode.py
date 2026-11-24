# -*- coding: utf-8 -*-
"""はじめてモード（初心者用の画面）。機械は偽物。実機の確認ではない。

2026-11-24 利用者: 今の画面は通常として残し、別に本当の初心者用を作る。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from nd287_app import easy_art, easy_flow as F, easy_machine   # noqa: E402


def S(aut=1, run=0, emergency=0, pw=False, online=True):
    st = F.machine_state({"values": {"aut": aut, "run": run, "emergency": emergency, "alarm": 0}},
                         [{"code": "PW0000"}] if pw else [], online=online)
    return st


class TestFlow(unittest.TestCase):
    def ids(self, st, ctx=None):
        return [s["id"] for s in F.STEPS if F.needed(s["id"], st, ctx or {})]

    def test_already_ok_machine_skips_power_and_estop(self):
        ids = self.ids(S(aut=3))
        self.assertNotIn("power", ids)
        self.assertNotIn("estop", ids)
        self.assertIn("dial_mdi", ids)

    def test_offline_machine_starts_with_power_then_origin_return(self):
        ids = self.ids(F.OFFLINE, {"powered_on": True})
        self.assertEqual(ids[:3], ["intro", "power", "estop"])
        self.assertIn("jog_origin", ids)

    def test_origin_return_only_after_power(self):
        self.assertNotIn("jog_origin", self.ids(S()))
        self.assertIn("jog_origin", self.ids(S(), {"cycled_in_wizard": True}))

    def test_cycle_power_only_when_machine_asks(self):
        self.assertNotIn("cycle_power", self.ids(S()))
        self.assertIn("cycle_power", self.ids(S(pw=True)))
        self.assertNotIn("cycle_power", self.ids(S(pw=True), {"cycled": True}))

    def test_counter_steps_follow_the_setting(self):
        self.assertIn("counter0", self.ids(S(), {"counter_reset": True}))
        self.assertNotIn("counter0", self.ids(S(), {"counter_reset": False}))
        self.assertNotIn("start2", self.ids(S(), {"counter_reset": False}))

    def test_detect_from_machine(self):
        self.assertTrue(F.done_now("estop", S(emergency=0), {}))
        self.assertFalse(F.done_now("estop", S(emergency=1), {}))
        self.assertTrue(F.done_now("dial_mdi", S(aut=0), {}))
        self.assertFalse(F.done_now("dial_mdi", S(aut=1), {}))
        self.assertTrue(F.done_now("start1", S(run=3), {}))
        self.assertFalse(F.done_now("power", F.OFFLINE, {}))

    def test_offline_is_never_done_and_never_guessed(self):
        for sid in ("estop", "dial_mdi", "dial_mem", "dial_ref", "start1"):
            self.assertFalse(F.done_now(sid, F.OFFLINE, {}))

    def test_cycle_done_needs_the_machine_to_go_away_and_come_back(self):
        st = S(pw=False)
        self.assertFalse(F.is_done("cycled", st, {"offline": False}))
        self.assertTrue(F.is_done("cycled", st, {"offline": True}))
        self.assertFalse(F.is_done("cycled", S(pw=True), {"offline": True}))

    def test_unreadable_values_get_a_manual_button(self):
        st = S()
        st["aut"] = None
        self.assertFalse(F.can_detect("aut_mdi", st))
        self.assertIsNone(F.watch_line("dial_mdi", st))

    def test_watch_lines(self):
        self.assertEqual(F.watch_line("estop", S(emergency=1))[0], "stop")
        self.assertEqual(F.watch_line("dial_mdi", S(aut=1))[1], "いまは「メモリ運転」です")
        self.assertEqual(F.watch_line("dial_mdi", S(aut=0))[0], "ok")
        self.assertEqual(F.watch_line("power", F.OFFLINE)[0], "wait")

    def test_pc_steps_wait_for_their_result(self):
        self.assertFalse(F.done_now("machine_in", S(), {}))
        self.assertTrue(F.done_now("machine_in", S(), {"handoff": True}))
        self.assertTrue(F.done_now("machine_in", S(), {"skip_machine": True}))
        self.assertFalse(F.done_now("fields", S(), {"missing": ("機番",)}))
        self.assertTrue(F.done_now("fields", S(), {"missing": ()}))
        self.assertFalse(F.done_now("result", S(), {"saved": False}))
        self.assertTrue(F.done_now("result", S(), {"saved": True}))

    def test_every_step_has_text_and_a_chapter(self):
        for s in F.STEPS:
            self.assertTrue(s["title"])
            self.assertTrue(0 <= s["ch"] < len(F.CHAPTERS))
        chs = [s["ch"] for s in F.STEPS]
        self.assertEqual(chs, sorted(chs))          # 章は戻らない（一方通行）

    def test_no_abbreviations_in_beginner_text(self):
        # 利用者: 略語・内部用語を使わない
        bad = ("MDI を", "FOCAS", "DGPR", "PWE", "N2165", "BASIC")
        for s in F.STEPS:
            text = s["title"] + "".join(s["lines"])
            for b in bad:
                self.assertNotIn(b, text)


class TestArt(unittest.TestCase):
    def test_svg_wellformed(self):
        import xml.dom.minidom as md
        pics = [easy_art.panel(p, 3, 0.5) for p in list(easy_art.PANEL_SPOTS) + ["none"]]
        pics += [easy_art.dial(3, 5, False), easy_art.dial(2, 3, True), easy_art.estop(False), easy_art.estop(True),
                 easy_art.power(False), easy_art.power(True, True), easy_art.knob(False), easy_art.knob(True),
                 easy_art.start(False), easy_art.start(True, True), easy_art.keys(False), easy_art.keys(True, "O0101"),
                 easy_art.jog(False), easy_art.counter(False), easy_art.counter(True), easy_art.scene(),
                 easy_art.auto(True, "x"), easy_art.auto(False, "x"), easy_art.product(True), easy_art.measure(0, 36),
                 easy_art.measure(20, 36), easy_art.verdict(True), easy_art.verdict(False), easy_art.verdict(None),
                 easy_art.verdict(True, saved=True), easy_art.table_picture(), easy_art.blank()]
        for svg in pics:
            md.parseString(svg.encode("utf-8"))

    def test_dial_names_are_the_real_switch_positions(self):
        self.assertEqual(len(easy_art.MODES), 8)
        for m in ("MDI", "原点復帰", "メモリ運転", "記憶編集", "自動測定"):
            self.assertIn(m, easy_art.MODES)


class FakeHost:
    """測定の画面の代わり。機械は偽物。"""

    def __init__(self):
        from PySide6 import QtWidgets
        self.e_model = QtWidgets.QLineEdit("RTV-510")
        self.e_machine = QtWidgets.QLineEdit("")
        self.e_operator = QtWidgets.QLineEdit("")
        self.e_temp = QtWidgets.QLineEdit("")
        self.capturing = False
        self.finished = False
        self.saved = False
        self.ng = 0
        self.calls = []

    def easy_ctx(self):
        missing = tuple(n for n, e in (("機番", self.e_machine), ("名前", self.e_operator), ("測定温度", self.e_temp))
                        if not e.text().strip())
        return dict(missing=missing, capturing=self.capturing, count=(10, 36) if self.capturing else (0, 0),
                    finished=self.finished, ng=self.ng, has_spec=True, saved=self.saved, saved_path="x.BS",
                    programs=(100,), next_mode="", mode="回転分割", counter_reset=True)

    def easy_start_capture(self):
        self.calls.append("start")
        self.capturing = True

    def easy_save(self):
        self.calls.append("save")
        self.saved = True

    def easy_next_mode(self, mode):
        self.calls.append(("mode", mode))

    def easy_open_wizard(self):
        self.calls.append("wizard")
        return {"handoff": True, "cycled": False}

    def on_model_entered(self):
        pass


class FakePoller:
    def __init__(self, st):
        self.st = st
        self.log = []

    def state(self):
        return dict(self.st)

    def pause(self):
        self.log.append("pause")

    def resume(self):
        self.log.append("resume")

    def stop(self):
        self.log.append("stop")


class TestDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self, st):
        from nd287_app import easy_mode
        host, poller = FakeHost(), FakePoller(st)
        dlg = easy_mode.EasyModeDialog(host, poller)
        dlg.show()
        self.app.processEvents()
        return dlg, host, poller

    def sid(self, dlg):
        return F.STEPS[dlg.i]["id"]

    def test_walk_through_to_the_end(self):
        dlg, host, poller = self.make(S(aut=0))
        self.assertEqual(self.sid(dlg), "intro")
        dlg._next()                                   # 用意するもの → （電源・非常停止・MDI は済んでいる）→ 機械に入れる
        self.assertEqual(self.sid(dlg), "machine_in")
        self.assertFalse(dlg.b_next.isEnabled())      # 機械に入れるまで先へ進めない
        dlg._run_wizard()
        self.assertEqual(poller.log, ["pause", "resume"])     # 窓が機械に書く間、読む係は止まる
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "fields")
        self.assertFalse(dlg.b_next.isEnabled())
        host.e_machine.setText("266704")
        host.e_operator.setText("ゆう")
        host.e_temp.setText("23.5")
        dlg._refresh()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "dial_mem")  # 機械は MDI（aut=0）なのでメモリ運転へ合わせる
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(aut=1)                           # 機械の様子が変わった
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "prog_select")
        self.assertEqual(dlg.lbl_title.text(), "O0100 を呼び出す")
        self.assertEqual(dlg.b_next.text(), "やりました ▶")
        dlg._next(); dlg._next()
        self.assertEqual(self.sid(dlg), "start1")
        self.assertIn("start", host.calls)            # 起動の前に、PC の取込を始める
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(aut=1, run=3)
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "counter0")
        dlg._next()
        self.assertEqual(self.sid(dlg), "start2")
        dlg._next()
        self.assertEqual(self.sid(dlg), "measuring")
        self.assertFalse(dlg.b_next.isEnabled())
        host.capturing, host.finished = False, True
        dlg._after_measuring()
        self.assertEqual(self.sid(dlg), "result")
        self.assertFalse(dlg.b_next.isEnabled())
        dlg._save()
        self.assertEqual(host.calls[-1], "save")
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "finish")
        dlg.close()
        self.assertIn("stop", poller.log)

    def test_back_goes_to_the_step_before(self):
        dlg, host, poller = self.make(S(aut=0))
        dlg._next()
        self.assertEqual(self.sid(dlg), "machine_in")
        dlg._back()
        self.assertEqual(self.sid(dlg), "intro")
        self.assertFalse(dlg.b_back.isEnabled())
        dlg.close()

    def test_offline_machine_shows_power_first(self):
        dlg, host, poller = self.make(F.OFFLINE)
        dlg._next()
        self.assertEqual(self.sid(dlg), "power")
        self.assertFalse(dlg.b_next.isEnabled())
        self.assertIn("さがして", dlg.lbl_watch.text())
        poller.st = S(emergency=1, aut=0)
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "estop")
        self.assertIn("非常停止がかかっています", dlg.lbl_watch.text())
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(emergency=0, aut=0)
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg.close()

    def test_unreadable_mode_gets_a_manual_button(self):
        st = S(aut=1)
        st["aut"] = None
        dlg, host, poller = self.make(st)
        dlg._enter(F.INDEX["dial_mdi"])
        self.assertFalse(dlg.b_manual.isHidden())
        self.assertFalse(dlg.b_next.isEnabled())
        dlg._manual_done()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg.close()

    def test_only_needed_buttons_are_shown(self):
        """見えるボタンは、もどる・次へと、その手順で要るものだけ。"""
        dlg, host, poller = self.make(S(aut=0))
        vis = lambda: sorted(b.text() for b in dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["x"]).QPushButton)
                             if not b.isHidden())
        self.assertEqual(len(vis()), 2)
        dlg._next()
        self.app.processEvents()
        self.assertEqual(len(vis()), 4)               # もどる・次へ・機械に入れる・飛ばす
        dlg.close()

    def test_fits_small_screens(self):
        dlg, host, poller = self.make(S())
        for sid in F.INDEX:
            dlg._enter(F.INDEX[sid])
            self.app.processEvents()
            m = dlg.minimumSizeHint()
            self.assertLessEqual(m.width(), 1024, sid)
            self.assertLessEqual(m.height(), 730, sid)
        host.capturing = False          # 測っている途中だと、閉じるときに確認が出て止まる
        dlg.close()


class TestPoller(unittest.TestCase):
    def test_reads_status_and_follows_power_cycle(self):
        from fake_client import DirectClient
        clients = []

        def factory(st):
            c = DirectClient()
            clients.append(c)
            return c

        up = [True]
        p = easy_machine.MachinePoller({"focas_host": "127.0.0.1"}, factory, port_open=lambda *a, **k: up[0])
        st = p.poll_once()
        self.assertTrue(st["online"])
        self.assertIn(st["aut"], range(0, 11))
        self.assertIsNotNone(st["emergency"])
        # 電源が切れた
        up[0] = False
        p._drop()
        self.assertFalse(p.poll_once()["online"])
        up[0] = True
        self.assertTrue(p.poll_once()["online"])


if __name__ == "__main__":
    unittest.main()
