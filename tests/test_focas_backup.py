# -*- coding: utf-8 -*-
"""focas_backup（控え）。偽物の機械へ直接つなぐ。実機にはつながない。設計書 §11.2 の 71〜78。"""

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
        for bad in (basic, basic / "控え", basic / "a" / "b",
                    str(basic).upper(), str(basic) + "\\", str(basic).replace("\\", "/"),
                    str(basic) + "\\..\\BASIC\\x"):
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


if __name__ == "__main__":
    unittest.main()
