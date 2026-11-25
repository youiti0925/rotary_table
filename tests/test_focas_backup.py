# -*- coding: utf-8 -*-
"""focas_backup（控え）。偽物の機械へ直接つなぐ。実機にはつながない。設計書 §11.2 の 71〜78。"""

import os
import copy
import hashlib
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import controllers                  # noqa: E402
from nd287_app import focas_backup as B            # noqa: E402
from nd287_app import focas_client as C            # noqa: E402
from nd287_app.settings import DEFAULTS            # noqa: E402
from fake_client import DirectClient               # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "機械バックアップ"
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings["focas_backup_dir"] = str(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def client(self, scenario=None):
        return DirectClient(scenario).connect()

    def take(self, scenario=None, client=None, **kw):
        c = client or self.client(scenario)
        r = B.take_backup(c, self.root, self.settings, host="127.0.0.1", port=8193, **kw)
        return c, r


class TestBackupFolder(Base):
    def test_folder_shape_and_info(self):
        c, r = self.take()
        self.assertTrue(r.complete, r.problems)
        self.assertEqual(r.folder.parent.name, "3C7B5D01_31i")
        self.assertRegex(r.folder.name, r"^\d{8}_\d{6}_手動$")
        for name in ("パラメータ.DAT", "ピッチ誤差補正.DAT", "カスタムマクロ変数.DAT", "工具補正.DAT",
                     "型情報.csv", B.INFO_NAME, B.TABLE_NAME,
                     "プログラム/CNC_MEM/USER/PATH1/O901", "プログラム/CNC_MEM/USER/LIBRARY/O9001"):
            self.assertTrue((r.folder / name).is_file(), name)
        info = json.loads((r.folder / B.INFO_NAME).read_text(encoding="utf-8"))
        self.assertEqual(info["source"], "focas")
        self.assertEqual(info["cnc_id"], "3C7B5D01-49C1F435-5E28C389-6592C486")
        self.assertEqual(info["cnc"]["version"], "18.0")
        self.assertEqual((info["host"], info["port"]), ("127.0.0.1", 8193))
        self.assertTrue(info["complete"])
        self.assertEqual(info["programs"]["listed"], 4)
        self.assertEqual(info["programs"]["saved"], 4)
        self.assertEqual(info["program_info"]["remaining"], 33)
        self.assertEqual(info["numbers"], 29)
        for rel, meta in info["files"].items():
            data = (r.folder / rel).read_bytes()
            self.assertEqual(meta["size"], len(data), rel)
            self.assertEqual(meta["sha256"], hashlib.sha256(data).hexdigest(), rel)
        self.assertIn("完全", (r.folder / B.TABLE_NAME).read_text(encoding="utf-8"))

    def test_unit_in_folder_name_only_when_chosen(self):
        c, r = self.take()
        self.assertEqual(r.folder.parent.name, "3C7B5D01_31i")
        c, r2 = self.take(client=c, unit="23")
        self.assertEqual(r2.folder.parent.name, "3C7B5D01_31i_号機23")

    def test_two_backups_in_the_same_second_do_not_overwrite(self):
        c = self.client()
        now = time.localtime()
        _, a = self.take(client=c, now=now)
        _, b = self.take(client=c, now=now)
        self.assertNotEqual(a.folder, b.folder)
        self.assertTrue(a.folder.is_dir() and b.folder.is_dir())

    def test_latest_backup_finds_newest_of_the_machine(self):
        c = self.client()
        self.take(client=c, now=time.strptime("2026-11-22 10:00:00", "%Y-%m-%d %H:%M:%S"))
        _, r2 = self.take(client=c, now=time.strptime("2026-11-22 12:00:00", "%Y-%m-%d %H:%M:%S"))
        folder, info = B.latest_backup(self.root, "3C7B5D01-49C1F435-5E28C389-6592C486")
        self.assertEqual(folder, r2.folder)
        self.assertIsNone(B.latest_backup(self.root, "00000000-0-0-0"))
        self.assertIsNone(B.latest_backup(self.tmp / "ない"))


class TestDestinationGuard(Base):
    def test_basic_dir_same_inside_and_variants_are_refused(self):
        basic = self.tmp / "BASIC"
        basic.mkdir()
        s = {"param_basic_dir": str(basic)}
        bads = [basic, basic / "控え", basic / "a" / "b", str(basic) + os.sep,
                str(basic) + os.sep + ".." + os.sep + "BASIC" + os.sep + "x"]
        if os.name == "nt":                # 大文字小文字を区別しない・\\ と / が同じ、は Windows の決まり
            bads += [str(basic).upper(), str(basic).replace("\\", "/")]
        for bad in bads:
            with self.assertRaises(B.BackupError, msg=str(bad)):
                B.check_destination(bad, s)
        # 別の場所・親の場所は許す
        B.check_destination(self.tmp / "別", s)
        B.check_destination(self.tmp, s)
        B.check_destination(str(basic) + "_other", s)             # 名前が前方一致するだけの別フォルダ

    def test_product_dir_is_refused_too(self):
        prod = self.tmp / "製品"
        prod.mkdir()
        with self.assertRaises(B.BackupError):
            B.check_destination(prod / "x", {"param_product_dir": str(prod)})

    def test_empty_destination_is_refused(self):
        with self.assertRaises(B.BackupError):
            B.check_destination("", {})

    def test_take_backup_refuses_before_touching_the_machine(self):
        basic = self.tmp / "BASIC"
        basic.mkdir()
        self.settings["param_basic_dir"] = str(basic)
        self.root = basic / "FOCAS"
        c = self.client()
        before = list(c.calls)
        with self.assertRaises(B.BackupError):
            B.take_backup(c, self.root, self.settings)
        self.assertEqual(c.calls, before)                         # 機械を1度も読んでいない
        self.assertFalse(self.root.exists())

    def test_relative_backup_dir_is_under_app_folder(self):
        p = B.resolve_root({"focas_backup_dir": "機械バックアップ"})
        self.assertTrue(p.is_absolute())
        self.assertEqual(p.name, "機械バックアップ")
        self.assertEqual(B.resolve_root({"focas_backup_dir": str(self.root)}), self.root)


class TestBytesAndNames(Base):
    def test_bytes_are_saved_unchanged_with_lf(self):
        c, r = self.take()
        m = c.machine
        self.assertEqual((r.folder / "パラメータ.DAT").read_bytes(), m.dump(2))
        self.assertEqual((r.folder / "ピッチ誤差補正.DAT").read_bytes(), m.dump(3))
        self.assertNotIn(b"\r", (r.folder / "パラメータ.DAT").read_bytes())   # LF CR CR に直していない
        self.assertEqual((r.folder / "プログラム/CNC_MEM/USER/PATH1/O901").read_bytes(),
                         m.programs["//CNC_MEM/USER/PATH1/O901"][0])

    def test_awkward_bytes_survive(self):
        raw = b"%\nO1(A) \n\r\rG04X0.\x00 \n\r\r  \n%"
        path = "//CNC_MEM/USER/PATH1/O555"
        c = self.client()
        c.machine.add_program(path, raw)
        r = B.take_backup(c, self.root, self.settings)
        self.assertEqual((r.folder / "プログラム/CNC_MEM/USER/PATH1/O555").read_bytes(), raw)

    def test_no_file_looks_like_a_basic_file(self):
        c, r = self.take()
        for p in self.root.rglob("*"):
            self.assertIsNone(re.match(r"^F\d+BASIC", p.name, re.I), p)
        for d in (self.root, r.folder.parent, r.folder):
            for unit in ("23", "1", "0"):
                self.assertIsNone(controllers.basic_file_for_unit(str(d), unit), (d, unit))

    def test_awkward_program_names_stay_inside_the_backup(self):
        c = self.client()
        c.machine.add_program("//CNC_MEM/USER/PATH1/O 7:7", b"%\nO7\n%")
        r = B.take_backup(c, self.root, self.settings)
        for p in r.folder.rglob("*"):
            self.assertTrue(str(p.resolve()).startswith(str(r.folder.resolve())))


class TestPostCheck(Base):
    def test_percent_in_the_middle_makes_it_incomplete(self):
        c = self.client()
        txt = c.machine.param_dat.decode("latin-1").split("\n")
        txt.insert(10, "%")
        c.machine.param_dat = "\n".join(txt).encode("latin-1")
        r = B.take_backup(c, self.root, self.settings)
        self.assertFalse(r.complete)
        self.assertTrue(any("途中に %" in p for p in r.problems), r.problems)
        info = B.load_info(r.folder)
        self.assertFalse(info["complete"])
        self.assertTrue((r.folder / "パラメータ.DAT").is_file())      # 取れた分は残す
        self.assertIn("不完全", (r.folder / B.TABLE_NAME).read_text(encoding="utf-8"))

    def test_required_dump_that_never_ends_is_incomplete(self):
        c = self.client()
        c.machine.pitch = b"%\nN11000Q0P0 \n"                      # 末尾 % がない
        r = B.take_backup(c, self.root, self.settings)
        self.assertFalse(r.complete)
        self.assertIn("ピッチ誤差補正.DAT", r.info["unread"])
        self.assertFalse((r.folder / "ピッチ誤差補正.DAT").exists())

    def test_unreadable_work_offset_is_recorded_and_backup_goes_on(self):
        c, r = self.take()
        self.assertIn("ワーク原点オフセット.DAT", r.info["unread"])
        self.assertIn("6", r.info["unread"]["ワーク原点オフセット.DAT"])
        self.assertTrue(r.complete)                               # 読めない種別があっても全体は続く
        self.assertIn("ワーク原点オフセット", (r.folder / B.TABLE_NAME).read_text(encoding="utf-8"))
        self.assertTrue((r.folder / "プログラム/CNC_MEM/USER/PATH1/O901").is_file())

    def test_program_count_mismatch_is_reported(self):
        c = self.client()
        orig = c.lib._cnc_upstart4

        def fail_one(h, code, path):
            if code.value == 0 and bytes(path).endswith(b"O2100"):
                return 5
            return orig(h, code, path)
        c.lib.cnc_upstart4.impl = fail_one
        r = B.take_backup(c, self.root, self.settings)
        self.assertFalse(r.complete)
        self.assertEqual((r.info["programs"]["listed"], r.info["programs"]["saved"]), (4, 3))
        self.assertTrue(any("O2100" in p for p in r.problems))

    def test_cancel_stops_and_marks_incomplete(self):
        c = self.client()
        n = [0]

        def cancel():
            n[0] += 1
            return n[0] > 3
        r = B.take_backup(c, self.root, self.settings, cancel=cancel)
        self.assertFalse(r.complete)
        self.assertTrue(any("中止" in p for p in r.problems))

    def test_connection_lost_leaves_an_incomplete_record_and_raises(self):
        c = self.client({"dump_pad_lines": 500})
        c.lib.sc["lost_after"] = c.lib.count + 6
        with self.assertRaises(C.FocasError) as cm:
            B.take_backup(c, self.root, self.settings)
        self.assertEqual(cm.exception.kind, "lost")
        folder, info = B.latest_backup(self.root)
        self.assertFalse(info["complete"])
        self.assertIn("途中で止まりました", " ".join(info["problems"]))

    def test_progress_is_reported(self):
        got = []
        c = self.client()
        B.take_backup(c, self.root, self.settings, progress=lambda d, t, n: got.append((d, t, n)))
        self.assertTrue(got)
        self.assertEqual(got[-1][0], got[-1][1])


class TestUnitCandidates(Base):
    IDENT = {"cnc_type": "31", "mt_type": "M", "version": "18.0", "axes": 6}

    def _ctl(self, unit, cnc, ver, n_axes):
        caps = {a: "20A" for a in "XYZABC"[:n_axes]}
        return controllers.Controller(unit, cnc, ver, caps=caps)

    def test_candidates_by_model_version_and_axes(self):
        ctls = [self._ctl("23", "31i-MA", "18.0", 6), self._ctl("24", "31i-MA", "18.0", 4),
                self._ctl("25", "31i-MA", "17.0", 6), self._ctl("26", "0i-MD", "18.0", 6),
                self._ctl("27", "31i-MB", "18.0", 6)]
        self.assertEqual(B.unit_candidates(self.IDENT, ctls), ["23", "27"])
        self.assertEqual(B.unit_candidates(self.IDENT, []), [])

    def test_real_master_gives_unit_23_but_never_decides(self):
        ctls = controllers.load_controllers(str(REPO / "マスタ" / "制御装置マスタ.csv"))
        cands = B.unit_candidates(self.IDENT, ctls)
        self.assertIn("23", cands)
        c = self.client()
        r = B.take_backup(c, self.root, self.settings, controllers=ctls)
        self.assertEqual(r.info["unit_candidates"], cands)
        self.assertEqual(r.info["unit"], "")                      # 候補があっても決め打ちしない
        self.assertEqual(r.folder.parent.name, "3C7B5D01_31i")

    def test_one_candidate_is_not_chosen_either(self):
        ctls = [self._ctl("23", "31i-MA", "18.0", 6)]
        c = self.client()
        r = B.take_backup(c, self.root, self.settings, controllers=ctls)
        self.assertEqual(r.info["unit_candidates"], ["23"])
        self.assertEqual(r.info["unit"], "")


class TestProgramListEnd(Base):
    """A-1: プログラム一覧が最後まで読めていないのに「完全」になってはいけない。
    実機（31i）では alldir は 3、subdir は 0・件数 0 で終わるのが正常。"""

    def test_normal_machine_is_complete_with_no_warning(self):
        c, r = self.take()
        self.assertTrue(r.complete, r.problems)
        self.assertEqual(r.warnings, [])
        rcs = r.info["programs"]["list_rcs"]
        self.assertTrue(rcs)
        self.assertTrue(all((x["rc_sub"], x["rc_all"]) == (0, 3) for x in rcs))
        self.assertEqual(r.info["programs"]["folders"], 4)
        self.assertEqual(r.info["warnings"], [])

    def test_alldir_not_ending_with_3_makes_it_incomplete_and_shows_the_rc(self):
        for rc in (5, 6, -1, 0):
            sc = {"alldir_end_rc": 0} if rc == 0 else {"force_rc": {"cnc_rdpdf_alldir": rc}}
            c, r = self.take(sc)
            self.assertFalse(r.complete, rc)
            text = " ".join(r.problems)
            self.assertIn("プログラムの一覧を最後まで読めませんでした", text, rc)
            self.assertIn(f"プログラム一覧の戻り値 {rc}", text, rc)
            info = json.loads((r.folder / B.INFO_NAME).read_text(encoding="utf-8"))
            self.assertFalse(info["complete"])
            self.assertTrue(any(x["rc_all"] == rc for x in info["programs"]["list_rcs"]), rc)

    def test_subdir_nonzero_makes_it_incomplete(self):
        c, r = self.take({"force_rc": {"cnc_rdpdf_subdir": 5}})
        self.assertFalse(r.complete)
        self.assertIn("フォルダ一覧の戻り値 5", " ".join(r.problems))

    def test_busy_a_few_times_is_retried_and_still_complete(self):
        c, r = self.take({"busy_first": {"cnc_rdpdf_alldir": 2}})
        self.assertTrue(r.complete, r.problems)

    def test_registered_count_mismatch_is_only_a_warning(self):
        c, r = self.take({"registered": 72})
        self.assertTrue(r.complete, r.problems)                   # 不完全にはしない（推測のため）
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("72", r.warnings[0])
        self.assertIn("目安", r.warnings[0])
        self.assertTrue(r.info["warnings"])
        table = (r.folder / B.TABLE_NAME).read_text(encoding="utf-8")
        self.assertIn("目安", table)
        self.assertIn("不完全とはしていません", table)

    def test_real_machine_count_72_is_62_programs_plus_10_folders(self):
        # 実機: プログラム 62本 + フォルダ 10個 = 登録 72。数え方が同じなら警告は出ない
        c, r = self.take({"extra_programs": 58})                 # 4+58=62 本
        self.assertEqual(r.info["programs"]["listed"], 62)
        self.assertEqual(r.info["program_info"]["registered"], 62 + 4)   # 偽物のフォルダは4個
        self.assertEqual(r.warnings, [])


class TestParamInfoIncomplete(Base):
    """B-2: 型情報の読みが途中で 0 以外になったら、最後の戻り値と次の番号を残して不完全にする。"""

    def test_rdparainfo_failing_midway_is_incomplete_and_recorded(self):
        c = self.client({"rdparainfo_fail_at": 2, "rdparainfo_fail_rc": 5})
        # 100件ずつだと1回で終わるので、5件ずつの読みを直接頼んで途中失敗を作る
        orig = c.call

        def small(cmd, args=None, **kw):
            if cmd == "param_info":
                args = {"count": 5}
            return orig(cmd, args, **kw)
        c.call = small
        r = B.take_backup(c, self.root, self.settings)
        self.assertFalse(r.complete)
        text = " ".join(r.problems)
        self.assertIn("型情報を最後まで読めませんでした", text)
        self.assertIn("最後の戻り値 5", text)
        info = json.loads((r.folder / B.INFO_NAME).read_text(encoding="utf-8"))
        self.assertEqual(info["types_end"]["last_rc"], 5)
        self.assertGreater(info["types_end"]["next_no"], 0)


class TestDestinationAliases(Base):
    """B-9: 割当ドライブ・ジャンクション・短い名前でも、BASIC・製品フォルダの中と見抜く。"""

    def setUp(self):
        super().setUp()
        self.basic = self.tmp / "BASIC_FOLDER_LONG_NAME"
        self.basic.mkdir()

    def _junction(self, link, target):
        import os
        import subprocess
        if os.name != "nt":
            os.symlink(str(target), str(link), target_is_directory=True)
            return
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        if r.returncode != 0:
            self.skipTest("ジャンクションを作れない環境です")

    def test_junction_to_basic_dir_is_refused(self):
        link = self.tmp / "ただのリンク"
        self._junction(link, self.basic)
        with self.assertRaises(B.BackupError):
            B.check_destination(link / "控え", {"param_basic_dir": str(self.basic)})
        with self.assertRaises(B.BackupError):
            B.check_destination(link, {"param_basic_dir": str(self.basic)})
        # 逆向き: BASIC 側の設定がリンクで、置き場所が本物の場所
        with self.assertRaises(B.BackupError):
            B.check_destination(self.basic / "x", {"param_basic_dir": str(link)})

    def test_short_8dot3_name_is_refused(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows だけ")
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(self.basic), buf, 520)
        short = buf.value if n else ""
        if not short or short.lower() == str(self.basic).lower():
            self.skipTest("この場所では短い名前が作られていません")
        with self.assertRaises(B.BackupError):
            B.check_destination(Path(short) / "控え", {"param_basic_dir": str(self.basic)})

    def test_subst_drive_is_refused(self):
        import os
        import subprocess
        if os.name != "nt":
            self.skipTest("Windows だけ")
        used = {c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(c + ":\\")}
        free = [c for c in "RSTUVWXYZ" if c not in used]
        if not free:
            self.skipTest("空いているドライブ文字がありません")
        d = free[0] + ":"
        r = subprocess.run(["subst", d, str(self.basic)], capture_output=True)
        if r.returncode != 0:
            self.skipTest("subst を使えない環境です")
        try:
            with self.assertRaises(B.BackupError):
                B.check_destination(d + "\\控え", {"param_basic_dir": str(self.basic)})
        finally:
            subprocess.run(["subst", d, "/d"], capture_output=True)

    def test_unrelated_folder_with_junction_is_still_allowed(self):
        other = self.tmp / "別の場所"
        other.mkdir()
        link = self.tmp / "別へのリンク"
        self._junction(link, other)
        B.check_destination(link / "控え", {"param_basic_dir": str(self.basic)})

    def test_variants_normalise_slashes_and_case(self):
        # 大文字小文字を区別しないのは Windows の決まり（Linux では別のフォルダ）
        text = str(self.basic).upper() if os.name == "nt" else str(self.basic)
        v = B._variants(text + "/")
        self.assertIn(B._norm(self.basic), v)


if __name__ == "__main__":
    unittest.main()
