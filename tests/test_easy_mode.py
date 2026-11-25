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
        return [s["id"] for s in F.STEPS if "measure" in s["fl"] and F.needed(s["id"], st, ctx or {})]

    def test_already_ok_machine_skips_power(self):
        ids = self.ids(S(aut=3))
        self.assertNotIn("power", ids)
        self.assertIn("dial_mdi", ids)

    def test_parameters_are_written_with_the_emergency_stop_pressed(self):
        """利用者の決定（2026-11-24）: パラメータは非常停止がかかっているときだけ書ける。測定プログラムは解除してから。"""
        ids = self.ids(S(emergency=0), {"param_estop": "estop_only"})
        self.assertNotIn("estop", ids)                       # 先に解除する手順は出さない
        self.assertLess(ids.index("estop_press"), ids.index("machine_in"))
        # すでに押してあれば、押す手順は出さず、書いたあとに解除する手順が出る
        ids = self.ids(S(emergency=1), {"param_estop": "estop_only"})
        self.assertNotIn("estop_press", ids)
        self.assertGreater(ids.index("estop_release"), ids.index("machine_in"))
        # 解除してから書く設定（released_only）は、前の順
        ids = self.ids(S(emergency=1), {"param_estop": "released_only"})
        self.assertIn("estop", ids)
        self.assertNotIn("estop_press", ids)
        self.assertNotIn("estop_release", ids)
        # どちらでもよい設定
        ids = self.ids(S(emergency=0), {"param_estop": "either"})
        self.assertNotIn("estop_press", ids)
        self.assertNotIn("estop", ids)

    def test_branches_decide_which_steps_appear(self):
        """はじめに選ぶ分かれ道（パラメータ・プログラムそれぞれ 自動／手）で、出る手順が変わる。"""
        base = {"param_estop": "estop_only"}
        both_auto = self.ids(S(emergency=1), dict(base, params="auto", prog="auto"))
        self.assertIn("machine_in", both_auto)
        self.assertNotIn("params_manual", both_auto)
        self.assertNotIn("prog_manual", both_auto)
        p_hand = self.ids(S(emergency=1), dict(base, params="manual", prog="auto"))
        self.assertIn("params_manual", p_hand)
        self.assertIn("machine_in", p_hand)               # プログラムは自動で入れる
        g_hand = self.ids(S(emergency=1), dict(base, params="auto", prog="manual"))
        self.assertIn("prog_manual", g_hand)
        self.assertIn("machine_in", g_hand)               # パラメータは自動で入れる
        both_hand = self.ids(S(emergency=1), dict(base, params="manual", prog="manual"))
        self.assertNotIn("machine_in", both_hand)         # アプリが機械に入れるものが無い
        self.assertLess(both_hand.index("params_manual"), both_hand.index("prog_manual"))
        self.assertLess(both_hand.index("choose"), both_hand.index("params_manual"))
        # 2つ選ぶまで「次へ」は押せない
        self.assertFalse(F.done_now("choose", S(), {}))
        self.assertFalse(F.done_now("choose", S(), {"params": "auto"}))
        self.assertTrue(F.done_now("choose", S(), {"params": "auto", "prog": "manual"}))

    def test_estop_detect(self):
        self.assertTrue(F.is_done("estop_on", S(emergency=1)))
        self.assertFalse(F.is_done("estop_on", S(emergency=0)))
        self.assertEqual(F.watch_line("estop_press", S(emergency=0))[0], "wait")
        self.assertEqual(F.watch_line("estop_press", S(emergency=1))[0], "ok")

    def test_offline_machine_starts_with_power_then_origin_return(self):
        ids = self.ids(F.OFFLINE, {"powered_on": True, "param_estop": "released_only"})
        self.assertEqual(ids[:4], ["intro", "choose", "power", "estop"])
        ids = self.ids(F.OFFLINE, {"powered_on": True})
        self.assertEqual(ids[:4], ["intro", "choose", "power", "dial_mdi"])
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
        self.assertTrue(F.done_now("estop_press", S(emergency=1), {}))
        self.assertTrue(F.done_now("estop_release", S(emergency=0), {}))
        self.assertFalse(F.done_now("estop", S(emergency=1), {}))
        self.assertTrue(F.done_now("dial_mdi", S(aut=0), {}))
        self.assertFalse(F.done_now("dial_mdi", S(aut=1), {}))
        self.assertTrue(F.done_now("start1", S(run=3), {}))
        self.assertFalse(F.done_now("power", F.OFFLINE, {}))

    def test_offline_is_never_done_and_never_guessed(self):
        for sid in ("estop", "estop_press", "estop_release", "dial_mdi", "dial_mem", "dial_ref", "start1"):
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
        self.assertIn("「メモリ運転」と答えています", F.watch_line("dial_mdi", S(aut=1))[1])
        self.assertEqual(F.watch_line("dial_mdi", S(aut=0))[0], "ok")
        self.assertEqual(F.watch_line("power", F.OFFLINE)[0], "wait")

    def test_machine_in_intro_says_the_unit_is_decided_from_the_order_before_connecting(self):
        """「機械に入れる」の案内: どの号機かは、伝票番号（製品）から決めて、そのあとつなぐ（つないでから号機を決めるのではない）。"""
        lines = " ".join(F.STEPS[F.INDEX["machine_in"]]["lines"])
        self.assertIn("受注伝票番号", lines)
        self.assertIn("号機の一覧", lines)
        self.assertLess(lines.index("受注伝票番号"), lines.index("号機"))
        self.assertLess(lines.index("号機"), lines.index("つないで"))
        self.assertNotIn("号機を自動で決めます", lines)

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
            self.assertTrue(s["fl"], s["id"])                 # どれかの流れに入っている
            for fl, ch in s["fl"].items():
                self.assertTrue(0 <= ch < len(F.FLOWS.get(fl, F.FLOWS["measure"])["chapters"]), s["id"])
        chs = [s["ch"] for s in F.STEPS if s["ch"] is not None]
        self.assertEqual(chs, sorted(chs))          # 章は戻らない（一方通行）

    def test_no_abbreviations_in_beginner_text(self):
        # 利用者: 略語・内部用語を使わない
        bad = ("MDI を", "FOCAS", "DGPR", "PWE", "N2165", "BASIC")
        for s in F.STEPS:
            text = s["title"] + "".join(s["lines"])
            for b in bad:
                self.assertNotIn(b, text)


class TestWizardBrief(unittest.TestCase):
    """かんたん作成の長い説明は、絵と短い言葉にする。長い文は、止まっているときだけ小さく添える。"""

    def test_steps_one_and_two_use_short_words(self):
        b = F.wizard_brief(1, missing=["受注伝票番号を入れて『探す』を押してください"])
        self.assertEqual((b["pic"], b["kind"], b["say"]), ("product", "do", "伝票の番号を入れて「探す」を押す"))
        self.assertEqual(F.wizard_brief(1, missing=[])["kind"], "done")
        self.assertEqual(F.wizard_brief(2, missing=["制御装置（号機）を選んでください"])["say"], "号機を選ぶ")
        self.assertEqual(F.wizard_brief(2, missing=["その号機の BASIC が見つかりません。『使うBASIC』を指定してください"])["say"],
                         "元の設定ファイルが見つかりません")       # 略語を出さない

    def test_real_guide_texts_become_short(self):
        from nd287_app import flow_guide as G
        cases = [
            (dict(), "「つなぐ」を押す"),
            (dict(connected=True), "「控えを取る」を押す"),
            (dict(busy_mode="connect"), "機械につないでいます…"),
            (dict(busy_mode="backup"), "機械の中身を写しています…"),
            (dict(busy_mode="write"), "機械に書いています。さわらない"),
            (dict(connected=True, estop="非常停止中です"), "非常停止がかかっています。もどす"),
            (dict(connected=True, has_backup=True, plan_ready=True, n_items=12), "「機械に書く」を押す"),
            (dict(connected=True, has_backup=True, plan_ready=True, result_state="done", result_all_ok=True),
             "入りました ✓"),
            (dict(connected=True, has_backup=True, plan_ready=True, result_state="done", result_all_ok=True,
                  power_cycle=True), "電源を入れ直す"),
            (dict(connected=True, has_backup=True, plan_ready=True, reasons=("書くものがありません（機械はすでに同じ値です）",)),
             "もう同じ値が入っています ✓"),
        ]
        for kw, want in cases:
            g = G.writer_guide(**kw)
            b = F.wizard_brief(3, g["kind"], g["text"])
            self.assertEqual(b["say"], want, g["text"])
            self.assertLessEqual(len(b["say"]), 30)
        # 非常停止の向きを間違えない: 書くときは「押す」、プログラムを送るときは「もどす」
        g = G.writer_guide(connected=True, has_backup=True, plan_ready=True, n_items=3, reasons=(
            "パラメータは非常停止にして書きます（社内の手順）。機械の非常停止ボタンを押してください（いまの非常停止の値 0）",))
        b = F.wizard_brief(3, g["kind"], g["text"])
        self.assertEqual((b["say"], b["pic"]), ("非常停止を押す", "estop_press"))
        g = G.program_guide(connected=True, estop="今は書けません。\n非常停止です（生の値 1）")
        b = F.wizard_brief(4, g["kind"], g["text"])
        self.assertEqual((b["say"], b["pic"]), ("非常停止がかかっています。もどす", "estop"))
        g = G.program_guide(connected=True, has_backup=True)
        self.assertEqual(F.wizard_brief(4, g["kind"], g["text"])["say"], "「送る」を押す")

    def test_unknown_text_falls_back_by_kind_and_stop_keeps_the_reason(self):
        b = F.wizard_brief(3, "stop", "知らない理由です")
        self.assertEqual(b["say"], "止まっています")
        self.assertEqual(b["detail"], "知らない理由です")
        b = F.wizard_brief(3, "wait", "知らない文")
        self.assertEqual((b["say"], b["detail"]), ("待っています…", ""))

    def test_done_points_to_the_next_button(self):
        b = F.wizard_brief(4, "done", "x", done=True)
        self.assertEqual((b["kind"], b["say"]), ("done", "下の「測定へ進む」を押す"))

    def test_power_cycle_shows_the_power_picture(self):
        self.assertEqual(F.wizard_brief(3, "do", "機械の電源を切って、入れ直してください")["pic"], "power")


class TestArt(unittest.TestCase):
    def test_svg_wellformed(self):
        import xml.dom.minidom as md
        pics = [easy_art.panel(p, 3, 0.5) for p in list(easy_art.PANEL_SPOTS) + ["none"]]
        pics += [easy_art.dial(3, 5, False), easy_art.dial(2, 3, True), easy_art.estop(False), easy_art.estop(True), easy_art.estop_press(False), easy_art.estop_press(True),
                 easy_art.power(False), easy_art.power(True, True), easy_art.knob(False), easy_art.knob(True),
                 easy_art.start(False), easy_art.start(True, True), easy_art.keys(False), easy_art.keys(True, "O0101"),
                 easy_art.jog(False), easy_art.counter(False), easy_art.counter(True), easy_art.scene(),
                 easy_art.auto(True, "x"), easy_art.auto(False, "x"), easy_art.product(True), easy_art.measure(0, 36),
                 easy_art.measure(20, 36), easy_art.verdict(True), easy_art.verdict(False), easy_art.verdict(None),
                 easy_art.verdict(True, saved=True), easy_art.table_picture(), easy_art.blank(),
                 easy_art.tile_measure(), easy_art.tile_program(), easy_art.tile_results(), easy_art.tile_alarm(),
                 easy_art.folder(), easy_art.printer(), easy_art.alarm_screen(True), easy_art.alarm_screen(False)]
        for svg in pics:
            md.parseString(svg.encode("utf-8"))

    def test_dial_names_are_the_real_switch_positions(self):
        self.assertEqual(len(easy_art.MODES), 8)
        for m in ("MDI", "原点復帰", "メモリ運転", "記憶編集", "自動測定"):
            self.assertIn(m, easy_art.MODES)


class FakeWizard:
    """かんたん作成の代わり（中身は別の試験）。"""
    def __new__(cls, parent):
        from PySide6 import QtWidgets

        class W(QtWidgets.QDialog):
            def __init__(self, parent):
                super().__init__(parent)
                self.handoff = None
                self.power_cycled = False
                self._writer = None
                self.b_mode = QtWidgets.QPushButton("全部表示", self)
                self.b_measure = QtWidgets.QPushButton("測定へ進む ▶", self)
                self.b_measure.clicked.connect(self.accept)
        return W(parent)


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
                    programs=(100,), next_mode="", mode="回転分割", counter_reset=True,
                    has_data=self.has_data, view_ng=self.view_ng, view_spec=True,
                    ng_items=getattr(self, "ng_items", []))

    def easy_start_capture(self):
        self.calls.append("start")
        self.capturing = True

    def easy_save(self):
        self.calls.append("save")
        self.saved = True

    def easy_next_mode(self, mode):
        self.calls.append(("mode", mode))

    def easy_cond_get(self):
        return dict(getattr(self, "cond", dict(wheel=10.0, worm=1.0, range=20.0, start=0.0, wstart=0.0, wend=90.0,
                                               blocks=4, repeats=7, rstart=0.0, rend=270.0)))

    def easy_cond_set(self, key, val):
        self.cond = self.easy_cond_get()
        self.cond[key] = val
        self.calls.append(("cond_set", key, val))

    def easy_cond_mode(self, kind, tilt):
        self.calls.append(("cond_mode", kind, tilt))

    def easy_cond_state(self):
        c = self.easy_cond_get()
        n = int(c["blocks"])
        blocks = [c["rstart"] + i * (c["rend"] - c["rstart"]) / (n - 1) for i in range(n)] if n > 1 else [c["rstart"]]
        return dict(model=self.e_model.text(), mode="回転分割", tilt=False, div="会社の一覧", rep="なし",
                    div_same_master=False, blocks=blocks)

    def easy_cond_register(self, kind, tilt):
        self.calls.append(("cond_reg", kind, tilt))
        return ["分割: 登録しました"]

    def easy_undo(self):
        self.calls.append("undo")

    def easy_cancel(self):
        self.calls.append("cancel")
        self.capturing = False

    def easy_open_folder(self):
        self.calls.append("folder")

    def easy_param_list(self):
        self.calls.append("param_list")

    def easy_make_wizard(self, parent):
        self.calls.append("wizard")
        self.wiz = FakeWizard(parent)
        return self.wiz

    def easy_wizard_result(self, w):
        return {"handoff": bool(w.handoff), "cycled": w.power_cycled}

    def on_model_entered(self):
        pass

    has_data = False
    view_ng = 0

    def easy_open_past(self):
        self.calls.append("past")
        self.has_data = True

    def easy_print(self):
        self.calls.append("print")

    def easy_pdf(self):
        self.calls.append("pdf")

    def easy_alarm_info(self, q):
        self.calls.append(("alarm", q))
        return [("PS0010", "使えないGコード", "指令が無効", "プログラムを直す")] if q else []


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

    def make(self, st, flow="measure"):
        from nd287_app import easy_mode
        host, poller = FakeHost(), FakePoller(st)
        dlg = easy_mode.EasyModeDialog(host, poller)
        dlg.show()
        self.app.processEvents()
        if flow:                                       # 入り口のタイルから流れを選ぶ
            dlg._start_flow(flow)
            self.app.processEvents()
        return dlg, host, poller

    def sid(self, dlg):
        return F.STEPS[dlg.i]["id"]

    def pass_choose(self, dlg, params="auto", prog="auto"):
        """用意するもの → 分かれ道（選ぶ）→ 次の手順。"""
        dlg._next()
        self.assertEqual(self.sid(dlg), "choose")
        dlg.ctx["params"], dlg.ctx["prog"] = params, prog
        dlg._refresh()
        dlg._next()
        self.skip_cond_check(dlg)

    def skip_cond_check(self, dlg):
        """「測り方を確かめる」は、機械の手順（電源・つまみ・非常停止）のあと。そこまで来ていたら、そのまま進む。"""
        if self.sid(dlg) == "cond_check":
            dlg._next()

    def test_walk_through_to_the_end(self):
        dlg, host, poller = self.make(S(aut=0))
        self.assertEqual(self.sid(dlg), "intro")
        self.pass_choose(dlg)                          # 用意するもの → 分かれ道 → （電源・MDI は済んでいる）→ 非常停止を押す
        self.assertEqual(self.sid(dlg), "estop_press")
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(aut=0, emergency=1)              # 押した（パラメータは非常停止中に書く）
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.skip_cond_check(dlg)
        self.assertEqual(self.sid(dlg), "machine_in")
        self.assertIsNotNone(dlg._wiz)                # かんたん作成は、この画面の中に入っている
        self.assertFalse(dlg.embed.isHidden())
        self.assertTrue(dlg.b_next.isHidden())        # 外側の「次へ」は出ない（中が案内する）
        self.assertEqual(poller.log, ["pause"])       # 中が機械に書く間、読む係は止まる
        host.wiz.handoff = {"steps": [{"mode": "回転分割", "programs": [100]}]}
        host.wiz.accept()                              # 中の「測定へ進む」
        self.app.processEvents()
        self.assertEqual(poller.log, ["pause", "resume"])
        self.assertIsNone(dlg._wiz)
        self.assertEqual(self.sid(dlg), "estop_release")   # 書き終えたら、非常停止をもどす（プログラム・運転は解除してから）
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(aut=0, emergency=0)
        dlg._tick()
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

    def test_starts_at_home_with_four_tiles(self):
        dlg, host, poller = self.make(S(), flow=None)
        self.assertEqual(self.sid(dlg), "home")
        self.assertEqual(set(dlg.tiles), {"measure", "program", "results", "alarm", "cond", "words"})
        self.assertFalse(dlg.home.isHidden())
        self.assertTrue(dlg.b_next.isHidden())
        dlg._start_flow("measure")
        self.assertEqual(self.sid(dlg), "intro")
        self.assertTrue(dlg.home.isHidden())
        dlg._back()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_program_flow(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1), flow="program")
        self.assertEqual(self.sid(dlg), "prog_intro")
        dlg._next()
        self.assertEqual(self.sid(dlg), "machine_in")       # 電源・MDI・非常停止は済んでいるので、すぐ機械に入れる
        self.assertIsNotNone(dlg._wiz)
        host.wiz.handoff = {"steps": [{"mode": "回転分割", "programs": [100]}]}
        host.wiz.accept()
        self.app.processEvents()
        self.assertEqual(self.sid(dlg), "estop_release")      # 書き終えたら非常停止をもどす
        poller.st = S(aut=0, emergency=0)
        dlg._tick()
        dlg._next()
        self.assertEqual(self.sid(dlg), "prog_done")
        dlg._next()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_results_flow(self):
        dlg, host, poller = self.make(S(), flow="results")
        self.assertEqual(self.sid(dlg), "open_past")
        self.assertFalse(dlg.b_next.isEnabled())
        dlg._open_past()
        self.assertIn("past", host.calls)
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "view_result")
        dlg._next()
        self.assertEqual(self.sid(dlg), "output")
        dlg._print()
        dlg._pdf()
        self.assertEqual(host.calls[-2:], ["print", "pdf"])
        dlg._next()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_alarm_flow(self):
        dlg, host, poller = self.make(S(), flow="alarm")
        self.assertEqual(self.sid(dlg), "alarm_look")
        dlg.e_alarm.setText("PS0010")
        self.assertIn("プログラムを直す", dlg.lbl_alarm.text())
        dlg.e_alarm.setText("")
        self.assertIn("番号を入れて", dlg.lbl_alarm.text())
        dlg._next()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_back_goes_to_the_step_before(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        self.pass_choose(dlg)
        self.assertEqual(self.sid(dlg), "machine_in")
        dlg._back()
        self.assertEqual(self.sid(dlg), "cond_check")
        dlg._back()
        self.assertEqual(self.sid(dlg), "choose")
        dlg._back()
        self.assertEqual(self.sid(dlg), "intro")
        dlg._back()
        self.assertEqual(self.sid(dlg), "home")
        self.assertFalse(dlg.b_back.isEnabled())
        dlg.close()

    def test_offline_machine_shows_power_first(self):
        dlg, host, poller = self.make(F.OFFLINE)
        self.pass_choose(dlg)
        self.assertEqual(self.sid(dlg), "power")
        self.assertFalse(dlg.b_next.isEnabled())
        self.assertIn("さがして", dlg.lbl_watch.text())
        poller.st = S(emergency=0, aut=0)              # 電源が入った。非常停止はかかっていない
        dlg._tick()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "estop_press")  # パラメータは非常停止を押して書く
        self.assertIn("解除されています", dlg.lbl_watch.text())
        self.assertFalse(dlg.b_next.isEnabled())
        poller.st = S(emergency=1, aut=0)
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
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        vis = lambda: sorted(b.text() for b in dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["x"]).QPushButton)
                             if not b.isHidden() and not dlg.embed.isAncestorOf(b))
        self.assertEqual(len(vis()), 5)               # もどる・一旦やめる・？ことば・アラーム・次へ
        self.pass_choose(dlg)
        self.app.processEvents()
        self.assertEqual(len(vis()), 5)               # もどる・飛ばす（あとは中のかんたん作成が案内する。ここの「次へ」は隠れる）
        self.assertTrue(dlg.b_next.isHidden())
        dlg.close()

    def test_choose_buttons_set_the_branch(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        dlg._next()
        self.assertEqual(self.sid(dlg), "choose")
        self.assertTrue(dlg.b_next.isEnabled())             # 初めから「自動・自動」が選ばれている（人に決めさせない）
        self.assertTrue(dlg._choice_btns[("params", "auto")].isChecked())
        self.assertTrue(dlg._choice_btns[("prog", "auto")].isChecked())
        dlg._choice_btns[("params", "manual")].click()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._choice_btns[("prog", "auto")].click()
        self.assertTrue(dlg.b_next.isEnabled())
        self.assertTrue(dlg._choice_btns[("params", "manual")].isChecked())
        self.assertFalse(dlg._choice_btns[("params", "auto")].isChecked())
        dlg._next()
        self.skip_cond_check(dlg)
        self.assertEqual(self.sid(dlg), "machine_in")       # プログラムは自動で入れる（パラメータは手の分を、このあと打つ）
        dlg.close()

    def test_both_manual_flow_has_no_machine_in(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        self.pass_choose(dlg, "manual", "manual")
        self.assertEqual(self.sid(dlg), "params_manual")
        self.assertIsNone(dlg._wiz)
        self.assertEqual(dlg.b_next.text(), "やりました ▶")
        dlg._param_list_calls = []
        host.easy_param_list = lambda: dlg._param_list_calls.append(1)
        dlg._param_list()
        self.assertEqual(dlg._param_list_calls, [1])
        dlg._next()
        self.assertEqual(self.sid(dlg), "estop_release")     # 打ち終えたら、非常停止をもどしてから、プログラム
        poller.st = S(aut=0, emergency=0)
        dlg._tick()
        dlg._next()
        self.assertEqual(self.sid(dlg), "prog_manual")
        dlg.close()

    def test_manual_program_ends_the_wizard_when_params_are_in(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        self.pass_choose(dlg, "auto", "manual")
        self.assertEqual(self.sid(dlg), "machine_in")
        self.assertIsNotNone(dlg._wiz)
        dlg._wiz._params_ok = False
        dlg._wiz._params_same = False
        dlg._tick()
        self.assertEqual(self.sid(dlg), "machine_in")       # まだ入っていない
        dlg._wiz._params_ok = True
        dlg._tick()
        self.app.processEvents()
        self.assertIsNone(dlg._wiz)
        self.assertNotEqual(self.sid(dlg), "machine_in")    # プログラムは送らず、先へ
        dlg.close()

    def _enter_measuring(self, dlg, host):
        host.capturing = True
        dlg._enter(F.INDEX["measuring"])
        dlg.app_ = self.app

    def test_back_while_measuring_opens_pause_not_previous_step(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        self._enter_measuring(dlg, host)
        asked = []
        dlg._ask_pause = lambda capturing, opts: (asked.append((capturing, [k for k, _ in opts])), "continue")[1]
        before = dlg.i
        dlg._back()
        self.assertEqual(dlg.i, before)                      # 前の手順へは行かない
        self.assertTrue(asked and asked[0][0])               # 測っている途中の画面（まず機械を止める）
        self.assertEqual(asked[0][1][0], "continue")
        self.assertIn("remeasure", asked[0][1])
        self.assertNotIn("cancel", host.calls)               # 押しただけでは何も捨てない
        host.capturing = False
        dlg.close()

    def test_pause_actions_undo_restart_home(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        self._enter_measuring(dlg, host)
        dlg._ask_pause = lambda c, o: "undo"
        dlg._open_pause()
        self.assertIn("undo", host.calls)
        dlg._yesno = lambda *a, **k: False                   # 「消してよいか」にいいえ
        dlg._ask_pause = lambda c, o: "remeasure"
        dlg._open_pause()
        self.assertNotIn("cancel", host.calls)
        dlg._yesno = lambda *a, **k: True
        dlg._open_pause()
        self.assertIn("cancel", host.calls)
        self.assertEqual(self.sid(dlg), "prog_select")       # 最初から測り直す
        host.capturing = True
        dlg._ask_pause = lambda c, o: "home"
        dlg._open_pause()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_unsaved_result_is_asked_before_leaving(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        host.finished = True
        host.saved = False
        dlg._enter(F.INDEX["result"])
        asked = []
        dlg._yesno = lambda text, *a, **k: (asked.append(text), False)[1]
        dlg._go_home()
        self.assertTrue(asked and "保存していません" in asked[0])
        self.assertEqual(self.sid(dlg), "result")            # いいえ → 動かない
        dlg._back()
        self.assertEqual(self.sid(dlg), "result")
        dlg._yesno = lambda *a, **k: True
        dlg._go_home()
        self.assertEqual(self.sid(dlg), "home")
        host.saved = True                                     # 保存済みなら聞かない
        dlg._enter(F.INDEX["result"])
        dlg._yesno = lambda *a, **k: self.fail("保存済みなのに聞いた")
        dlg._go_home()
        dlg.close()

    def test_pause_options_when_unsaved_and_idle(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        host.finished = True
        host.saved = False
        dlg._enter(F.INDEX["result"])
        keys = [k for k, _ in dlg._pause_options()]
        self.assertEqual(keys[0], "continue")
        self.assertIn("save", keys)
        host.saved = True
        keys = [k for k, _ in dlg._pause_options()]
        self.assertNotIn("save", keys)
        self.assertIn("home", keys)
        dlg.close()

    def test_pause_button_hidden_on_home_and_pictures_render(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0), flow=None)
        dlg._go_home()
        self.assertTrue(dlg.b_pause.isHidden())
        dlg._start_flow("measure")
        self.assertFalse(dlg.b_pause.isHidden())
        from nd287_app import easy_art
        for svg in (easy_art.pause_stop(), easy_art.unsaved()):
            from PySide6 import QtSvg, QtCore
            self.assertTrue(QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8"))).isValid())
        from nd287_app.easy_mode import PauseDialog
        PauseDialog(dlg, True, [("continue", "つづける")]).close()
        PauseDialog(dlg, False, [("continue", "つづける"), ("save", "保存する")]).close()
        dlg.close()

    def test_condition_flow_division_only(self):
        dlg, host, poller = self.make(S(), flow="cond")
        self.assertEqual(self.sid(dlg), "cond_kind")
        self.assertTrue(dlg.b_next.isEnabled())               # 初めから「両方・いまの回転/傾斜」が選ばれている
        self.assertTrue(dlg._choice_btns[("cond_kind", "one")].isChecked())
        dlg._choice_btns[("cond_kind", "division")].click()   # 違うときだけ押し直す
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertIn(("cond_mode", "division", False), host.calls)
        self.assertEqual(self.sid(dlg), "cond_div")           # 分割だけ → 再現性の手順は出ない
        self.assertIn("36 か所", dlg.lbl_cond.text())           # 360 ÷ 10
        dlg._cond_spins["wheel"].setValue(15)
        self.assertIn(("cond_set", "wheel", 15.0), host.calls)
        self.assertIn("24 か所", dlg.lbl_cond.text())
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_reg")
        self.assertFalse(dlg.b_next.isEnabled())              # 登録するまで進めない
        dlg._cond_register()
        self.assertIn(("cond_reg", "division", False), host.calls)
        self.assertEqual(self.sid(dlg), "cond_done")
        self.assertIn("分割: 登録しました", dlg.lbl_lines.text())
        dlg._next()
        self.assertEqual(self.sid(dlg), "home")
        dlg.close()

    def test_condition_flow_both_shows_repeat_marks(self):
        dlg, host, poller = self.make(S(), flow="cond")
        dlg._choice_btns[("cond_kind", "one")].click()
        dlg._choice_btns[("cond_tilt", True)].click()
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_div")
        self.assertIn("wstart", dlg._cond_spins)               # 傾斜は どこから・どこまで
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_rep")
        self.assertIn("4 か所 × 7 回", dlg.lbl_cond.text())
        self.assertIn("90°", dlg.lbl_cond.text())
        dlg.close()

    def test_measure_flow_checks_conditions_and_returns(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        dlg._next()
        dlg.ctx["params"], dlg.ctx["prog"] = "auto", "auto"
        dlg._refresh()
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_check")           # 測る前に、測り方を確かめる
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._cond_from_check()                                  # その場で決めて登録する
        self.assertEqual(self.sid(dlg), "cond_kind")
        dlg._choice_btns[("cond_kind", "one")].click()
        dlg._choice_btns[("cond_tilt", False)].click()
        dlg._next()
        dlg._next()
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_reg")
        dlg._cond_register()
        self.assertEqual(self.sid(dlg), "cond_done")
        self.assertEqual(dlg.b_next.text(), "もどる ▶")
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_check")           # 測るの流れにもどる
        self.assertEqual(dlg.flow, "measure")
        dlg._next()
        self.assertEqual(self.sid(dlg), "machine_in")
        dlg.close()

    def test_words_cards_and_dialog(self):
        from nd287_app import easy_words
        from nd287_app.easy_mode import WordsDialog
        from PySide6 import QtSvg, QtCore
        import nd287_app.easy_art as A
        for cid, c in easy_words.CARDS.items():
            self.assertLessEqual(len(c["lines"]), 3, cid)           # 1枚＝3行まで
            self.assertTrue(hasattr(A, c["art"]), cid)
            svg = A.verdict(False) if c["art"] == "verdict" else getattr(A, c["art"])()
            self.assertTrue(QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8"))).isValid(), cid)
        for sid, ids in easy_words.FOR_STEP.items():
            self.assertIn(sid, F.INDEX)
            for cid in ids:
                self.assertIn(cid, easy_words.CARDS)
        self.assertEqual(easy_words.cards_for("home"), easy_words.ALL)
        self.assertIn("規格が画面に無いときは", "".join(easy_words.CARDS["verdict"]["lines"]))
        d = WordsDialog(None, easy_words.cards_for("result"))
        d.show_card("graph")
        self.assertEqual(d.lbl_t.text(), "グラフの見方")
        d.close()
        self.assertEqual(easy_words.ng_lines([("a", "1")] * 10, limit=2)[-1], "…ほか 8 か所")

    def test_words_button_and_home_tile(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1), flow=None)
        self.assertIn("words", dlg.tiles)
        self.assertTrue(dlg.b_words.isHidden())                  # ホームには出さない
        opened = []
        dlg._open_words = lambda ids: opened.append(tuple(ids))
        dlg._start_flow("words")
        self.assertEqual(opened[-1], __import__("nd287_app.easy_words", fromlist=["x"]).ALL)
        dlg._start_flow("measure")
        self.assertFalse(dlg.b_words.isHidden())
        dlg.b_words.click()
        self.assertEqual(len(opened), 2)
        dlg.close()

    def test_result_lists_the_ng_items(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        host.finished = True
        host.ng = 2
        host.ng_items = [("ホイール CW 精度PP", "12.3"), ("総合バックラッシ", "8.1")]
        dlg._enter(F.INDEX["result"])
        self.assertIn("2", dlg.lbl_lines.text())
        box = dlg.extra.itemAt(0).widget()                     # 外れた所は、1行ずつの部品（値は右、折り返さない）
        self.assertEqual(box.objectName(), "ng_rows")
        from PySide6 import QtWidgets
        texts = [w.text() for w in box.findChildren(QtWidgets.QLabel)]
        self.assertIn("・ホイール CW 精度PP", texts)
        self.assertIn("12.3", texts)
        self.assertFalse(any(w.wordWrap() for w in box.findChildren(QtWidgets.QLabel) if w.text() in ("12.3", "8.1")))
        host.saved = True
        dlg.close()

    def test_ng_rows_with_a_long_value_do_not_crush_the_name_column(self):
        from PySide6 import QtWidgets
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        host.finished = True
        host.ng = 3
        host.ng_items = [("ホイール CCW 単一誤差", "-3.9″"), ("ウォーム CW 傾き", "+5.2″"),
                         ("総合バックラッシ", "MIN -0.4\"　規格 ≧5.0\"　NG")]
        dlg.resize(1024, 768)
        dlg._enter(F.INDEX["result"])
        self._wait(200)
        box = dlg._extra_widgets["ng"]
        for lb in box.findChildren(QtWidgets.QLabel):
            if lb.text().startswith("・"):
                self.assertGreaterEqual(lb.width(), 150, (lb.text(), lb.width()))        # 項目名の欄が細くならない
                if lb.wordWrap() and lb.text():
                    self.assertGreaterEqual(lb.height(), lb.heightForWidth(lb.width()) - 2)   # 切れない
        host.saved = True
        dlg.close()

    def _wait(self, ms):
        import time
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def test_back_from_result_skips_measuring_and_does_not_bounce(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        dlg._enter(F.INDEX["start2"])
        dlg.history.append(dlg.i)
        host.finished = True
        dlg._enter(F.INDEX["measuring"])                      # 測り終わっている: 1.2 秒後に結果へ
        self._wait(1700)
        self.assertEqual(self.sid(dlg), "result")
        host.saved = True                                     # 保存した（保存前なら、いまの確認がそのまま働く）
        dlg._back()
        self.assertEqual(self.sid(dlg), "start2")             # 「測っています」には入らない
        self._wait(1700)
        self.assertEqual(self.sid(dlg), "start2")             # 結果へ戻されない
        self.assertTrue(host.finished)                        # 値は消えていない
        dlg.close()

    def test_back_from_result_unsaved_still_asks(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        dlg._enter(F.INDEX["start2"])
        dlg.history.append(dlg.i)
        host.finished = True
        dlg._enter(F.INDEX["measuring"])
        self._wait(1700)
        asked = []
        dlg._yesno = lambda text, *a, **k: (asked.append(text), False)[1]
        dlg._back()
        self.assertTrue(asked)
        self.assertEqual(self.sid(dlg), "result")
        dlg.close()

    def test_power_step_has_an_offline_fallback_and_later_steps_follow(self):
        dlg, host, poller = self.make(F.OFFLINE)
        self.pass_choose(dlg)
        self.assertEqual(self.sid(dlg), "power")
        self.assertTrue(dlg.b_manual.isHidden())               # はじめは待つ
        self.assertFalse(dlg.b_next.isEnabled())
        dlg._entered_at -= 40
        dlg._refresh()
        self.assertFalse(dlg.b_manual.isHidden())
        self.assertIn("機械とつながっていません（電源と LAN ケーブル）", dlg.b_manual.text())
        self.assertIn("つながらないまま進む", dlg.b_manual.text())
        dlg.b_manual.click()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertNotEqual(self.sid(dlg), "power")
        # 機械の手順は、待たずに「やりました」を出す（機械がつながらないので、人が確認する）
        guard = 0
        while F.step(self.sid(dlg))["kind"] != "machine" and guard < 20:
            dlg._next()
            guard += 1
        self.assertEqual(F.step(self.sid(dlg))["kind"], "machine")
        dlg._refresh()
        self.assertFalse(dlg.b_manual.isHidden())
        dlg.b_manual.click()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg.close()

    def test_manual_fallback_is_a_real_button(self):
        from PySide6 import QtWidgets
        dlg, host, poller = self.make(S(aut=1, emergency=0))
        dlg._enter(F.INDEX["dial_mdi"])                         # つまみは「メモリ」→ MDI ではない
        dlg._entered_at -= 40
        dlg._refresh()
        self.assertIsInstance(dlg.b_manual, QtWidgets.QPushButton)
        self.assertFalse(dlg.b_manual.isHidden())
        self.assertEqual(dlg.b_manual.objectName(), "fallback")  # 下線だけの小さい文字ではない
        self.assertGreaterEqual(dlg.b_manual.sizeHint().height(), 40)
        self.assertGreaterEqual(dlg.b_manual.sizeHint().width(), 200)
        self.assertNotEqual(dlg.b_manual.objectName(), "sub")
        dlg.close()

    def test_right_column_is_centered_and_scaled_at_all_sizes(self):
        from PySide6 import QtCore
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        sizes = {}
        for (w, h) in ((1600, 980), (1366, 768), (1024, 768)):
            dlg.resize(w, h)
            self._wait(100)
            for sid in ("intro", "dial_mdi"):
                dlg._enter(F.INDEX[sid])
                self._wait(100)
                top = dlg.lbl_who.mapTo(dlg.right_w, QtCore.QPoint(0, 0)).y() if dlg.lbl_who.isVisible() else                     dlg.lbl_title.mapTo(dlg.right_w, QtCore.QPoint(0, 0)).y()
                kids = [x for x in (dlg.lbl_who, dlg.lbl_title, dlg.lbl_lines, dlg.lbl_watch, dlg.b_manual, dlg.v_map) if x.isVisible()]
                bottom = max(x.mapTo(dlg.right_w, QtCore.QPoint(0, x.height())).y() for x in kids)
                gap_top, gap_bot = top, dlg.right_w.height() - bottom
                self.assertLess(abs(gap_top - gap_bot), 12, (w, h, sid, gap_top, gap_bot))   # 上下の余白が同じ（まん中）
            sizes[w] = int(dlg.lbl_title.font().pixelSize() if dlg.lbl_title.font().pixelSize() > 0 else 0)
        dlg._enter(F.INDEX["dial_mdi"])
        self.assertTrue(dlg.v_map.isVisible())                  # 操作盤の絵は右の空きに置く
        self.assertTrue(dlg.right_w.isAncestorOf(dlg.v_map))
        dlg.resize(1600, 980)
        self._wait(100)
        self.assertIn("font-size:36px", dlg.right_w.styleSheet())    # 大きい画面では文字も大きく
        dlg.resize(1024, 768)
        self._wait(100)
        self.assertIn("font-size:26px", dlg.right_w.styleSheet())
        dlg.close()

    def test_finish_shows_folder_and_elided_path_with_tooltip(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        host.finished = True
        host.saved = True
        long = r"C:\Users\AA4344\AppData\Local\Temp\nd287v_tpiple1a\RBS\T-001.csv"
        orig = host.easy_ctx
        host.easy_ctx = lambda: dict(orig(), saved_path=long)
        dlg.resize(1024, 768)
        dlg._enter(F.INDEX["finish"])
        self._wait(150)
        self.assertIn("RBS", dlg.lbl_lines.text())
        self.assertIn("T-001.csv", dlg.lbl_lines.text())
        self.assertNotIn("AppData", dlg.lbl_lines.text())
        el = dlg._extra_widgets["path"]
        self.assertEqual(el.toolTip(), long)
        self.assertEqual(el.full(), long)
        self.assertLessEqual(el.height(), 30)                   # 1行
        self.assertIn("…", el.text())
        dlg.close()

    def test_photo_lookup_and_toggle(self):
        import tempfile
        from pathlib import Path
        from nd287_app import easy_photo
        from PySide6 import QtGui
        base = Path(tempfile.mkdtemp())
        self.assertEqual(sorted(easy_photo.missing(base)), sorted(easy_photo.NAMES))    # 何も置いていない
        self.assertIsNone(easy_photo.step_photo(F.step("power"), base))
        (base / "写真").mkdir()
        img = QtGui.QImage(40, 30, QtGui.QImage.Format_RGB32)
        img.fill(0x336699)
        self.assertTrue(img.save(str(base / "写真" / "power.png")))
        self.assertEqual(easy_photo.step_photo(F.step("power"), base).name, "power.png")
        self.assertNotIn("power", easy_photo.missing(base))
        self.assertIsNone(easy_photo.step_photo(F.step("estop"), base))                  # 置いていない所は絵のまま
        dlg, host, poller = self.make(F.OFFLINE)
        dlg.photo_base = base
        dlg._enter(F.INDEX["power"])
        self.assertFalse(dlg.b_photo.isHidden())              # 写真があれば、切り替えが出る
        self.assertIsNotNone(getattr(dlg.v_main, "_pix", None))   # 初めは写真
        dlg.b_photo.click()                                    # 絵にもどす
        self.assertIsNone(getattr(dlg.v_main, "_pix", None))
        dlg._enter(F.INDEX["estop"])
        self.assertTrue(dlg.b_photo.isHidden())               # 写真が無い手順では出ない
        dlg.close()
        import shutil
        shutil.rmtree(base, ignore_errors=True)

    def test_new_dialogs_fit_small_screens(self):
        from nd287_app import easy_words
        from nd287_app.easy_mode import WordsDialog, PauseDialog
        from PySide6 import QtWidgets
        f = QtWidgets.QApplication.instance().font()
        for pt in (7, 12, 22):
            f.setPointSize(pt)
            QtWidgets.QApplication.instance().setFont(f)
            for d in (WordsDialog(None, easy_words.ALL), WordsDialog(None, ("graph",)),
                      PauseDialog(None, True, [("continue", "つづける"), ("undo", "1か所もどす（測った値は残ります）"),
                                               ("remeasure", "この測定をやめて、最初から測り直す"), ("home", "やめてホームへ")]),
                      PauseDialog(None, False, [("continue", "つづける"), ("save", "保存する"), ("home", "保存しないでホームへ")])):
                m = d.minimumSizeHint()
                self.assertLessEqual(m.width(), 1024, (pt, type(d).__name__))
                self.assertLessEqual(m.height(), 730, (pt, type(d).__name__))
                d.close()
        f.setPointSize(9)
        QtWidgets.QApplication.instance().setFont(f)

    def test_alarm_button_is_always_there_and_shows_count(self):
        dlg, host, poller = self.make(S(aut=0, emergency=0))
        self.assertFalse(dlg.b_alarm.isHidden())
        self.assertEqual(dlg.b_alarm.text(), "アラーム")
        poller.st = F.machine_state({"values": {"emergency": 0, "aut": 0}}, [{"code": "SV0436", "text": "x"}], online=True)
        dlg.st = poller.st
        dlg._refresh()
        self.assertIn("1 件", dlg.b_alarm.text())
        before = dlg.i
        dlg.b_alarm.click()
        self.assertEqual(self.sid(dlg), "alarm_look")          # どの手順からでも飛べる
        self.assertTrue(dlg.b_alarm.isHidden())                # 着いたら、このボタンは要らない
        dlg.close()

    def test_condition_needs_model(self):
        dlg, host, poller = self.make(S(), flow="cond")
        host.e_model.setText("")
        dlg._choice_btns[("cond_kind", "repeat")].click()
        dlg._choice_btns[("cond_tilt", False)].click()
        self.assertFalse(dlg.b_next.isEnabled())              # 型式が空
        host.e_model.setText("RWE-200")
        dlg._refresh()
        self.assertTrue(dlg.b_next.isEnabled())
        dlg._next()
        self.assertEqual(self.sid(dlg), "cond_rep")           # 再現性だけ → 分割の手順は出ない
        dlg.close()

    def test_condition_pictures_render(self):
        from nd287_app import easy_art as A
        from PySide6 import QtSvg, QtCore
        for svg in (A.tile_cond(), A.choice_kind("division"), A.choice_kind("repeat"), A.choice_kind("one"),
                    A.choice_tilt(True), A.choice_tilt(False), A.cond_dial(10), A.cond_dial(0.5),
                    A.cond_dial(marks=[0, 90, 180, 270]), A.cond_drawer()):
            self.assertTrue(QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8"))).isValid())

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

    def test_alarm_read_failure_is_none_not_empty(self):
        class C:
            def __init__(self, fail):
                self.fail = fail

            def call(self, cmd, args=None, **k):
                if cmd == "alarms":
                    if self.fail:
                        raise RuntimeError("x")
                    return {"alarms": []}
                if cmd == "status":
                    return {"values": {"aut": 1, "run": 0, "emergency": 0, "alarm": 0}}
                return {}

            def close(self):
                pass
        for fail, want in ((True, None), (False, [])):
            p = easy_machine.MachinePoller({"focas_host": "127.0.0.1"}, lambda st, f=fail: C(f),
                                           port_open=lambda *a, **k: True)
            st = p.poll_once()
            self.assertTrue(st["online"])
            self.assertEqual(st["alarms"], want)
        self.assertIsNone(F.OFFLINE["alarms"])
        self.assertEqual(F.machine_state({"values": {}}, [], online=True)["alarms"], [])


from nd287_app import alarm_watch   # noqa: E402


class TestAlarmStep(unittest.TestCase):
    """アラームの手順（① 一覧 ② 意味 ③ 確かめる ④ 記録）。機械は偽物。実機の確認ではない。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "アラーム記録.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def st(self, alarms, online=True):
        return F.machine_state({"values": {"aut": 1, "run": 0, "emergency": 0, "alarm": 1 if alarms else 0}},
                               alarms, online=online)

    def make(self, st, flow="alarm"):
        from nd287_app import easy_mode
        host, poller = FakeHost(), FakePoller(st)
        dlg = easy_mode.EasyModeDialog(host, poller)
        dlg._alarm_log_path = lambda: self.log            # 記録は一時の場所へ（本番の場所は触らない）
        dlg.show()
        self.app.processEvents()
        if flow:
            dlg._start_flow(flow)
            self.app.processEvents()
        return dlg, host, poller

    def buttons(self, dlg):
        return [dlg.alarm_list.itemAt(i).widget() for i in range(dlg.alarm_list.count())
                if dlg.alarm_list.itemAt(i).widget() is not None
                and dlg.alarm_list.itemAt(i).widget().__class__.__name__ == "QPushButton"]

    PW = {"code": "PW0000", "text": "POWER MUST BE OFF"}
    SW = {"code": "SW0100", "text": "PARAMETER WRITE ENABLE"}
    SV = {"code": "SV0436", "text": "SERVO ALARM"}

    def test_lists_all_machine_alarms_verbatim(self):
        dlg, host, poller = self.make(self.st([self.PW, self.SV, self.SW]))
        texts = [b.text() for b in self.buttons(dlg)]
        self.assertEqual(len(texts), 3)
        self.assertIn("POWER MUST BE OFF", texts[0])
        self.assertIn("SV0436", texts[1])
        self.assertTrue(texts[2].startswith("●"))                 # 異常ではないものは印が違う
        self.assertIn("3 件", dlg.lbl_alarm_head.text())
        dlg.close()

    def test_offline_says_cable_and_keeps_typing(self):
        dlg, host, poller = self.make(self.st([], online=False))
        self.assertIn("機械につながっていません（電源と LAN ケーブル）", dlg.lbl_alarm_head.text())
        self.assertEqual(self.buttons(dlg), [])
        dlg.e_alarm.setText("PS0010")
        self.assertIn("プログラムを直す", dlg.lbl_alarm.text())
        dlg.close()

    def test_known_alarm_shows_meaning_and_picture(self):
        dlg, host, poller = self.make(self.st([self.PW]))
        self.assertIn("PW0000", dlg.lbl_alarm.text())             # 最初の異常なものは、最初から意味を出す
        self.assertIn("やること", dlg.lbl_alarm.text())
        self.assertIn("POWER MUST BE OFF", dlg.lbl_al_big.text())
        self.assertEqual(dlg._al["pic"], "power_cycle")
        self.assertFalse(dlg.b_al_check.isHidden())
        dlg.close()

    def test_unknown_alarm_says_not_in_list_and_shows_machine_text_big(self):
        dlg, host, poller = self.make(self.st([self.SV]))
        self.assertIn("アプリの一覧にありません", dlg.lbl_alarm.text())
        self.assertNotIn("原因:", dlg.lbl_alarm.text())            # 無いものを有るように見せない
        self.assertIn("SERVO ALARM", dlg.lbl_al_big.text())
        self.assertIsNone(dlg._al["pic"])
        dlg.close()

    def test_normal_alarm_is_not_abnormal(self):
        dlg, host, poller = self.make(self.st([self.SW]))
        self.assertIsNone(dlg._al["sel"])                          # 異常なものが無いので、自動では選ばない
        self.buttons(dlg)[0].click()
        self.assertIn("これは異常ではありません（設定を書けるようにしている印）", dlg.lbl_alarm.text())
        self.assertTrue(dlg.b_al_check.isHidden())
        dlg.close()

    def test_typed_code_not_in_list_says_so(self):
        dlg, host, poller = self.make(self.st([]))
        dlg.e_alarm.setText("SV0436")
        self.assertIn("アプリの一覧にありません", dlg.lbl_alarm.text())
        dlg.close()

    def test_check_gone_then_ask_once_and_record(self):
        dlg, host, poller = self.make(self.st([self.PW, self.SW]))
        poller.st = self.st([self.SW])                             # 電源を入れ直した → PW0000 が消えた
        dlg._alarm_check()
        t = dlg.lbl_al_result.text()
        self.assertIn("消えました ✓  PW0000", t)
        self.assertIn("直りました", t)
        self.assertFalse(dlg.al_ask.isHidden())                    # 1回だけ聞く
        dlg._alarm_fix_free()                                      # 何も書かないときは記録しない
        self.assertFalse(self.log.exists())
        dlg.e_al_fix.setText("電源を入れ直した")
        dlg._alarm_fix_free()
        self.assertTrue(dlg.al_ask.isHidden())
        rows = alarm_watch.read_records(self.log)
        self.assertEqual((rows[0]["番号"], rows[0]["機械の文字"], rows[0]["やったこと"], rows[0]["結果"]),
                         ("PW0000", "POWER MUST BE OFF", "電源を入れ直した", "直った"))
        dlg._alarm_check()                                         # もう一度確かめても、同じ番号は聞き直さない
        self.assertTrue(dlg.al_ask.isHidden())
        # 次に同じ番号が出たら、「前はこれで直りました」が一番上
        poller.st = self.st([self.PW])
        dlg._tick()
        dlg._enter(F.INDEX["alarm_look"])
        self.app.processEvents()
        self.assertTrue(dlg.lbl_alarm.text().startswith("前はこれで直りました: 電源を入れ直した"))
        dlg.close()

    def test_check_with_standard_remedy_button(self):
        dlg, host, poller = self.make(self.st([self.PW]))
        poller.st = self.st([])
        dlg._alarm_check()
        dlg._alarm_fix_std()
        rows = alarm_watch.read_records(self.log)
        self.assertIn("電源を切って入れ直す", rows[0]["やったこと"])
        dlg.close()

    def test_check_still_and_new(self):
        dlg, host, poller = self.make(self.st([self.PW, self.SV]))
        poller.st = self.st([self.PW, {"code": "OT0510", "text": "OVER TRAVEL"}])
        dlg._alarm_check()
        t = dlg.lbl_al_result.text()
        self.assertIn("消えました ✓  SV0436", t)
        self.assertIn("まだ出ています  PW0000", t)
        self.assertIn("新しく出ました  OT0510", t)
        self.assertNotIn("直りました。", t)
        self.assertTrue(dlg.al_ask.isHidden())
        self.assertFalse(self.log.exists())
        dlg.close()

    def test_check_offline_does_not_say_fixed(self):
        dlg, host, poller = self.make(self.st([self.PW]))
        poller.st = self.st([], online=False)
        dlg._alarm_check()
        self.assertIn("機械につながっていません", dlg.lbl_al_result.text())
        self.assertTrue(dlg.al_ask.isHidden())
        dlg.close()

    def test_list_follows_the_machine_each_tick(self):
        dlg, host, poller = self.make(self.st([]))
        self.assertEqual(self.buttons(dlg), [])
        poller.st = self.st([self.SV])
        dlg._tick()
        self.assertEqual(len(self.buttons(dlg)), 1)
        dlg.close()

    def test_open_alarm_and_return_to_previous_step(self):
        dlg, host, poller = self.make(self.st([self.PW]), flow="measure")
        before = dlg.i
        flow, hist = dlg.flow, list(dlg.history)
        dlg.open_alarm()
        self.assertEqual(self.sid(dlg), "alarm_look")
        self.assertEqual(dlg.b_next.text(), "もとの手順にもどる")      # 「ホームへ」ではない
        dlg._next()
        self.assertEqual((dlg.i, dlg.flow, dlg.history), (before, flow, hist))
        dlg.open_alarm()
        dlg._alarm_back()
        self.assertEqual((dlg.i, dlg.flow, dlg.history), (before, flow, hist))
        dlg.open_alarm()                                           # 画面の「戻る」でも、飛ぶ前の手順にもどる
        dlg._back()
        self.assertEqual(dlg.i, before)
        dlg.close()

    def test_unread_alarms_are_not_called_fixed(self):
        # つながっているのに読めなかった（None）: 「ありません」「直りました」と言わない。④ も聞かない
        dlg, host, poller = self.make(self.st([self.PW]))
        poller.st = self.st(None)
        poller.st["alarms"] = None
        dlg._alarm_check()
        self.assertIn("読めませんでした", dlg.lbl_al_result.text())
        self.assertNotIn("直りました", dlg.lbl_al_result.text())
        self.assertTrue(dlg.al_ask.isHidden())
        self.assertIn("読めませんでした", dlg.lbl_alarm_head.text())
        self.assertNotIn("ありません", dlg.lbl_alarm_head.text())
        # 読めた「なし」は、今までどおり直った
        poller.st = self.st([])
        dlg._alarm_check()
        self.assertIn("直りました", dlg.lbl_al_result.text())
        dlg.close()

    def test_offline_head_is_not_unread(self):
        dlg, host, poller = self.make(self.st(None, online=False))
        self.assertIn("つながっていません", dlg.lbl_alarm_head.text())
        dlg.close()

    def test_typed_rs232_and_sw0100(self):
        from nd287_app import fanuc_alarms as FA
        dlg, host, poller = self.make(self.st([]))
        db = FA.load_alarms()
        host.easy_alarm_info = lambda q: [(a["code"], a["title"], a["cause"], a["remedy"])
                                          for a in FA.search_alarms_ranked(db, q)[:5]]
        rows = host.easy_alarm_info("RS232")
        dlg.e_alarm.setText("RS232")
        if rows:                                                   # 今までの検索の先頭をそのまま出す
            self.assertIn(rows[0][0], dlg.lbl_alarm.text())
        self.assertNotIn("一覧にありません", dlg.lbl_alarm.text())
        dlg.e_alarm.setText("SW0100")
        self.assertIn("これは異常ではありません（設定を書けるようにしている印）", dlg.lbl_alarm.text())
        dlg.close()

    def test_legend_line(self):
        dlg, host, poller = self.make(self.st([self.PW]))
        from PySide6 import QtWidgets
        texts = [w.text() for w in dlg.alarm_box.findChildren(QtWidgets.QLabel)]
        self.assertIn("▲ 異常　● 異常ではない", texts)
        dlg.close()

    def test_alarm_from_tile_has_no_return_button(self):
        dlg, host, poller = self.make(self.st([]), flow="alarm")
        self.assertIsNone(dlg._alarm_return)
        dlg.close()

    def sid(self, dlg):
        return F.STEPS[dlg.i]["id"]

    def test_never_resets_alarms(self):
        # 読むだけ。アラームを消す・リセットする呼び出しは host にも poller にも無い
        dlg, host, poller = self.make(self.st([self.PW]))
        dlg._alarm_check()
        self.assertEqual(poller.log, [])
        self.assertTrue(all(c != "reset" for c in host.calls))
        dlg.close()


if __name__ == "__main__":
    unittest.main()


class TestMachineStateAlarms(unittest.TestCase):
    def test_alarm_type_no_axis_are_kept(self):
        st = F.machine_state({"values": {"emergency": 0}}, [{"code": "SV0436", "text": "x", "type": 3, "no": 436, "axis": 2},
                                                           {"code": "PS0010", "text": "y"}], online=True)
        self.assertEqual((st["alarms"][0]["type"], st["alarms"][0]["no"], st["alarms"][0]["axis"]), (3, 436, 2))
        self.assertIsNone(st["alarms"][1]["axis"])            # 読めなかった値は None（推測で埋めない）


class TestLayoutAndModeMismatch(TestDialog):
    def test_embedded_wizard_uses_the_whole_width(self):
        dlg, host, poller = self.make(S(aut=0, emergency=1))
        dlg._enter(F.INDEX["machine_in"])
        self.app.processEvents()
        self.assertTrue(dlg.art_wrap.isHidden())              # 左の絵の場所ごと隠れる（左を空けない）
        self.assertGreater(dlg.embed.width(), dlg.width() * 0.8)
        dlg.close()

    def test_mode_mismatch_offers_the_manual_button_early_and_says_the_number(self):
        import time
        from nd287_app import easy_mode
        dlg, host, poller = self.make(S(aut=2, emergency=1))
        dlg._enter(F.INDEX["dial_mdi"])
        self.assertTrue(dlg.b_manual.isHidden())
        dlg._entered_at = time.time() - easy_mode.FALLBACK_MISMATCH_SEC - 1
        dlg._refresh()
        self.assertFalse(dlg.b_manual.isHidden())              # 読みが合わなくても、人が進められる
        self.assertIn("数字は 2", dlg.lbl_watch.text())
        dlg.close()
