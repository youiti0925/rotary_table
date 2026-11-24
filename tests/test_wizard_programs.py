# -*- coding: utf-8 -*-
"""かんたん作成 段④（測定プログラムを送る）の、Qt を使わない部分。

番号の割り当て・分け方の自動判定・本数の組み立て・セット送信の決め方・送った記録。
機械は偽物（tests/fake_client.DirectClient）。実機の動きの確認ではない。
"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_client import DirectClient                      # noqa: E402
from nd287_app import batch_build, fanuc                  # noqa: E402
from nd287_app import focas_backup as B                   # noqa: E402
from nd287_app import focas_ops as O                      # noqa: E402
from nd287_app import wizard_programs as WP               # noqa: E402
from nd287_app.masters import find_entry, load_conditions  # noqa: E402
from nd287_app.settings import DEFAULTS                   # noqa: E402

FOLDER = "//CNC_MEM/USER/PATH1/"
EOB = fanuc.EOB_PUNCH


def masters(repeat=True, tilt_repeat=False):
    m = {"conditions": load_conditions(REPO / "マスタ" / "測定条件.csv"),
         "user_rotary": {}, "user_tilt": {}, "user_rotary_repeat": {}, "user_tilt_repeat": {}}
    if repeat:
        m["user_rotary_repeat"]["RTV-506"] = {"型式": "RTV-506", "ブロック数": "4", "回数": "5",
                                              "再現開始": "0", "再現終了": "270"}
    if tilt_repeat:
        m["user_tilt_repeat"]["TT-1"] = {"型式": "TT-1", "ブロック数": "3", "回数": "2",
                                         "再現開始": "-30", "再現終了": "30"}
    return m


def settings(**kw):
    s = copy.deepcopy(DEFAULTS)
    s.update(kw)
    return s


class TestKindAndChoice(unittest.TestCase):
    def test_kind_auto_and_ambiguous(self):
        self.assertEqual(WP.decide_kind(["回転"])[::2], (False, False))
        self.assertEqual(WP.decide_kind(["傾斜"])[::2], (True, False))
        t, note, amb = WP.decide_kind([])
        self.assertEqual((t, amb), (False, True))          # 分からなければ回転・選び直せる
        self.assertIn("回転として作ります", note)
        t, note, amb = WP.decide_kind(["回転", "傾斜"])
        self.assertEqual((t, amb), (False, True))

    def test_no_repeat_means_division_only_with_reason(self):
        allowed, choice, why = WP.default_choice(True, False, "two")
        self.assertEqual((allowed, choice), ({"division"}, "division"))
        self.assertIn("再現性の条件が登録されていない", why)

    def test_both_uses_last_choice_else_one(self):
        self.assertEqual(WP.default_choice(True, True, "")[1], "one")
        self.assertEqual(WP.default_choice(True, True, "two")[1], "two")
        self.assertEqual(WP.default_choice(True, True, "xx")[1], "one")
        self.assertIn("別々に2本で作ります", WP.default_choice(True, True, "two")[2])
        self.assertEqual(WP.default_choice(False, True, "one")[1], "repeat")
        self.assertEqual(WP.default_choice(False, False, "one")[1], "")


class TestNumbers(unittest.TestCase):
    """再現性の番号を決めるルール（固定）: 分割の次の番号。再現性サブ・保護領域とぶつかれば次へ進む。"""

    def test_repeat_number_rule(self):
        self.assertEqual(WP.repeat_number(100, 1000), 101)
        self.assertEqual(WP.repeat_number(100, 101), 102)          # サブとぶつかる → 次
        self.assertEqual(WP.repeat_number(999, 1000), 1001)
        with self.assertRaises(ValueError):                         # 8000〜9999 は保護領域
            WP.repeat_number(7999, 1000)

    def test_assign_per_choice(self):
        nums = lambda c, **k: [(s["role"], s["number"]) for s in WP.assign_numbers(c, 100, 1000, **k)]  # noqa: E731
        self.assertEqual(nums("one"), [("main", 100), ("sub", 1000)])
        self.assertEqual(nums("two"), [("division", 100), ("repeat", 101), ("sub", 1000)])
        self.assertEqual(nums("division"), [("main", 100)])
        self.assertEqual(nums("repeat"), [("main", 100), ("sub", 1000)])
        self.assertEqual(nums("one", use_sub=False), [("main", 100)])

    def test_check_numbers(self):
        slots = WP.assign_numbers("two", 100, 1000)
        self.assertEqual(WP.check_numbers(slots)[0], [])
        slots[1]["number"] = 100
        self.assertTrue(WP.check_numbers(slots)[0])
        slots[1]["number"] = 0
        self.assertTrue(WP.check_numbers(slots)[0])
        slots[1]["number"] = 8500
        problems, notes = WP.check_numbers(slots)
        self.assertEqual(problems, [])
        self.assertTrue(notes)


class TestPlan(unittest.TestCase):
    def plan(self, choice=None, model="RTV-506", kinds=("回転",), m=None, s=None, **kw):
        return WP.make_plan(s or settings(), m if m is not None else masters(), model, list(kinds),
                            choice=choice, **kw)

    def test_each_choice_count_numbers_and_bytes(self):
        expect = {"one": (1, [[100, 1000]]), "two": (2, [[100], [101, 1000]]),
                  "division": (1, [[100]]), "repeat": (1, [[100, 1000]])}
        for choice, (n, nums) in expect.items():
            pl = self.plan(choice)
            self.assertEqual(pl["problems"], [], choice)
            self.assertEqual(len(pl["programs"]), n, choice)
            self.assertEqual([p["numbers"] for p in pl["programs"]], nums, choice)
            for p in pl["programs"]:
                self.assertEqual(p["nc"], fanuc.nc_bytes(p["text"], EOB))     # nc_bytes を通した形のまま
                self.assertIn(b"\n\r\r", p["nc"])                              # LF CR CR
                self.assertNotIn(b";", p["nc"])
                got = [q["number"] for q in O.split_programs(p["nc"], EOB)]
                self.assertEqual(got, p["numbers"], choice)

    def test_no_repeat_registered_gives_division_only(self):
        pl = self.plan(m=masters(repeat=False), choice="two")
        self.assertEqual(pl["choice"], "division")
        self.assertEqual(pl["allowed"], {"division"})
        self.assertIn("再現性の条件が登録されていない", pl["why"])
        self.assertEqual(len(pl["programs"]), 1)

    def test_division_is_the_same_text_as_program_for_model(self):
        pl = self.plan("division")
        cfg = fanuc.FanucConfig.from_settings(settings())
        cond = find_entry(masters()["conditions"], "RTV-506")
        want = batch_build.program_for_model(cond, cfg, rotary=True, title="RTV-506 BUNKATSU")
        self.assertEqual(pl["programs"][0]["text"], want)

    def test_last_choice_from_settings_is_the_default(self):
        self.assertEqual(self.plan(s=settings(wizard_prog_choice="two"))["choice"], "two")
        self.assertEqual(self.plan()["choice"], "one")

    def test_repeat_sub_number_never_collides(self):
        pl = self.plan("two", s=settings(fanuc_main_number=100, fanuc_rep_sub_number=101))
        nums = [n for p in pl["programs"] for n in p["numbers"]]
        self.assertEqual(sorted(nums), [100, 101, 102])
        self.assertEqual(len(nums), len(set(nums)))

    def test_changed_numbers_are_used_and_duplicates_refused(self):
        pl = self.plan("two", numbers={"division": 200, "repeat": 201})
        self.assertEqual([p["numbers"][0] for p in pl["programs"]], [200, 201])
        bad = self.plan("two", numbers={"division": 200, "repeat": 200})
        self.assertTrue(bad["problems"])
        self.assertEqual(bad["programs"], [])

    def test_tilt_does_not_use_rotary_conditions(self):
        pl = self.plan(kinds=("傾斜",), model="RTV-506")
        self.assertTrue(pl["tilt"])
        self.assertFalse(pl["has_division"])                 # 回転の測定条件は傾斜に使わない
        self.assertTrue(pl["problems"] or pl["choice"] == "")

    def test_tilt_with_registered_conditions(self):
        m = masters(repeat=False, tilt_repeat=True)
        m["user_tilt"]["TT-1"] = {"型式": "TT-1", "開始角度": "-30", "終了角度": "30", "刻み": "10",
                                  "ウォーム刻み": "0.5", "ウォーム範囲": "5", "ウォーム開始": "0"}
        pl = self.plan(model="TT-1", kinds=("傾斜",), m=m, choice="one")
        self.assertEqual(pl["problems"], [])
        self.assertIn("傾斜", pl["programs"][0]["lines"][100])

    def test_product_model_with_suffix_is_found(self):
        # 製品データの型式は「RTV-506,WA」のようにカンマ以降が付く（実物 Model=RTV-510,WA）
        pl = self.plan("one", model="RTV-506,WA")
        self.assertEqual(pl["problems"], [])
        self.assertTrue(pl["has_division"] and pl["has_repeat"])

    def test_unknown_model_cannot_make_programs(self):
        pl = self.plan(model="NOPE-1")
        self.assertEqual(pl["programs"], [])
        self.assertTrue(pl["problems"])

    def test_word_lines(self):
        pl = self.plan("one")
        lines = pl["programs"][0]["lines"]
        self.assertTrue(lines[100].startswith("O0100：回転 "))
        self.assertIn("0°に戻る", lines[100])
        self.assertNotIn("G0", lines[100])
        self.assertIn("本体といっしょに", lines[1000])

    def test_lf_resend_nc_only_for_given_numbers(self):
        pl = self.plan("two")
        nc = WP.lf_resend_nc(pl["programs"], [101])
        self.assertEqual([q["number"] for q in O.split_programs(nc, fanuc.EOB_LF)], [101])
        self.assertNotIn(b"\r", nc)                          # LF のみ（人が押したときだけ使う形）


class TestRowStatus(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(WP.row_status({"written": True, "match": True})[0], "OK")
        self.assertEqual(WP.row_status({"written": True, "match": False})[0], "ここが違う")
        self.assertIn("もう入っています", WP.row_status(
            {"skipped": True, "note": O.SKIP_TEXT["same"]})[0])
        self.assertEqual(WP.row_status(None)[1], "none")
        self.assertEqual(WP.mismatched_numbers([{"number": 5, "written": True, "match": False},
                                                {"number": 6, "written": True, "match": True}]), [5])


def nc_of(*numbers):
    text = "%\n" + "".join(f"O{n:04d}(T{n})\nG90\nM30\n" for n in numbers) + "%\n"
    return fanuc.nc_bytes(text, EOB)


class TestSetSend(unittest.TestCase):
    def test_join_programs_round_trip(self):
        nc = nc_of(555, 556)
        progs = O.split_programs(nc, EOB)
        self.assertEqual(O.join_programs(progs, EOB), nc)
        self.assertEqual([q["number"] for q in O.split_programs(O.join_programs(progs[1:], EOB), EOB)], [556])

    def test_plan_set_send_rules(self):
        progs = [{"number": n} for n in (1, 2, 3)]
        g = [[1, 2]]
        ps = O.plan_set_send
        self.assertEqual(ps(progs, {1: "new", 2: "new", 3: "new"}, g, ()), {1: "send", 2: "send", 3: "send"})
        self.assertEqual(ps(progs, {1: "new", 2: "same", 3: "same"}, g, ()), {1: "send", 2: "same", 3: "same"})
        self.assertEqual(ps(progs, {1: "same", 2: "same", 3: "new"}, g, ()), {1: "same", 2: "same", 3: "send"})
        # 組の片方の中身が違う: 既定は組ごと飛ばす（片方だけ送らない）
        self.assertEqual(ps(progs, {1: "new", 2: "differs", 3: "new"}, g, ()),
                         {1: "hold_set", 2: "hold_differs", 3: "send"})
        self.assertEqual(ps(progs, {1: "new", 2: "differs", 3: "new"}, g, [2]),
                         {1: "send", 2: "replace", 3: "send"})


class Machine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = DirectClient({}).connect()
        self.addCleanup(self.c.close)
        res = B.take_backup(self.c, self.tmp.name, {}, purpose="測定プログラム書込前")
        self.assertTrue(res.complete, res.problems)
        self.info, self.cid = res.info, self.c.call("identify")["cnc_id"]

    def send(self, nc, **kw):
        return O.run_programs_send(self.c, nc, EOB, approved=True, folder=FOLDER, cnc_id=self.cid,
                                   backup_info=self.info, **kw)

    def test_inspect_new_same_differs(self):
        st = O.inspect_programs(self.c, nc_of(555, 901, 1000), EOB, folder=FOLDER)
        self.assertEqual(st[555], "new")
        self.assertEqual(st[901], "differs")                 # 機械の O901 は別の中身
        self.assertEqual(self.c.lib.called("cnc_dwnstart4"), 0)      # 読むだけ
        self.send(nc_of(555))
        self.assertEqual(O.inspect_programs(self.c, nc_of(555), EOB, folder=FOLDER)[555], "same")

    def test_send_new_then_same_is_skipped_without_writing(self):
        res = self.send(nc_of(555, 556), groups=[[555, 556]])
        self.assertTrue(res["ok"], res)
        self.assertEqual([it["match"] for it in res["items"]], [True, True])
        writes = self.c.lib.called("cnc_dwnstart4")
        again = self.send(nc_of(555, 556), groups=[[555, 556]])
        self.assertTrue(again["ok"])
        self.assertTrue(all(it["skipped"] and it["skip_reason"] == "same" for it in again["items"]))
        self.assertIn("送らなくて大丈夫", WP.row_status(again["items"][0])[0])
        self.assertEqual(self.c.lib.called("cnc_dwnstart4"), writes)   # 1本も書いていない
        self.assertEqual(self.c.lib.called("cnc_pdf_del"), 0)

    def test_different_content_is_skipped_by_default_and_set_stays_together(self):
        before = dict(self.c.machine.programs)
        res = self.send(nc_of(555, 901), groups=[[555, 901]])      # 901 は機械に別の中身がある
        by = {it["number"]: it for it in res["items"]}
        self.assertEqual(by[901]["skip_reason"], "hold_differs")
        self.assertEqual(by[555]["skip_reason"], "hold_set")        # 組の片方だけは送らない
        self.assertEqual(self.c.machine.programs, before)
        self.assertEqual(self.c.lib.called("cnc_dwnstart4"), 0)

    def test_replace_only_when_checked(self):
        res = self.send(nc_of(555, 901), groups=[[555, 901]], overwrite_numbers=[901])
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.c.lib.called("cnc_pdf_del"), 1)
        self.assertEqual(self.c.machine.deleted_programs, [FOLDER + "O901"])
        self.assertEqual(self.c.machine.programs[FOLDER + "O901"][0],
                         O.split_programs(nc_of(555, 901), EOB)[1]["expect"])
        self.assertIn(FOLDER + "O555", self.c.machine.programs)

    def test_not_approved_and_bad_state_write_nothing(self):
        with self.assertRaises(O.WriteRefused):
            O.run_programs_send(self.c, nc_of(555), EOB, approved=False, folder=FOLDER, cnc_id=self.cid,
                                backup_info=self.info)
        c2 = DirectClient({"status": {"run": 3}}).connect()
        self.addCleanup(c2.close)
        with self.assertRaises(O.WriteRefused):
            O.run_programs_send(c2, nc_of(555), EOB, approved=True, folder=FOLDER, cnc_id=self.cid,
                                backup_info=self.info)
        self.assertEqual(c2.lib.called("cnc_dwnstart4"), 0)


class TestLog(unittest.TestCase):
    def test_log_and_same_as_last(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / WP.LOG_NAME
        pl = WP.make_plan(settings(), masters(), "RTV-506", ["回転"], choice="one")
        fps = WP.fingerprints(pl["programs"])
        items = [{"number": 100, "written": True, "match": True},
                 {"number": 1000, "written": True, "match": True},
                 {"number": 5, "skipped": True, "written": False}]
        self.assertEqual(WP.append_log(path, "RTV-506", "23", "one", items, fps, now="2026-11-24 10:00:00"), 2)
        rows = WP.read_log(path)
        self.assertEqual([r["番号"] for r in rows], ["100", "1000"])
        self.assertEqual(rows[0]["号機"], "23")
        self.assertEqual(WP.same_as_last(rows, "RTV-506", fps)["日時"], "2026-11-24 10:00:00")
        self.assertIsNone(WP.same_as_last(rows, "RTV-999", fps))
        other = WP.fingerprints(WP.make_plan(settings(fanuc_dwell_sec=3.0), masters(), "RTV-506", ["回転"],
                                             choice="one")["programs"])
        self.assertIsNone(WP.same_as_last(rows, "RTV-506", other))     # 中身が変われば「同じ」ではない
        self.assertEqual(WP.append_log(path, "RTV-506", "", "one", [], fps), 0)


if __name__ == "__main__":
    unittest.main()
