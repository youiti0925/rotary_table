# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import controllers as C

REAL = Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv"


class TestControllerMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctls = C.load_controllers(str(REAL))

    def test_loads_all_units(self):
        units = [c.unit for c in self.ctls]
        self.assertEqual(len(units), 23)
        self.assertIn("10", units)
        self.assertIn("86", units)

    def test_capacities_per_axis(self):
        c10 = next(c for c in self.ctls if c.unit == "10")
        self.assertEqual(c10.cnc, "18i-M")
        self.assertEqual([c10.caps[a] for a in ("X", "Y", "Z", "A")],
                         ["20A", "40A", "80A", "160A"])
        # 号機23 は B/C 軸まである
        c23 = next(c for c in self.ctls if c.unit == "23")
        self.assertEqual(c23.caps["B"], "80A")
        self.assertEqual(c23.caps["C"], "160A")
        self.assertEqual(c23.axes(), ["X", "Y", "Z", "A", "B", "C"])

    def test_all_capacities_sorted(self):
        self.assertEqual(C.all_capacities(self.ctls), ["20A", "40A", "80A", "160A"])

    def test_filter_by_capacity(self):
        # 20A の軸を持つ号機だけ
        got = {c.unit for c in C.filter_by_capacity(self.ctls, "20A")}
        self.assertIn("10", got)       # X=20A
        self.assertNotIn("17", got)    # 17 は全軸160A
        # 容量空なら全件
        self.assertEqual(len(C.filter_by_capacity(self.ctls, "")), 23)

    def test_axes_with_capacity(self):
        c10 = next(c for c in self.ctls if c.unit == "10")
        self.assertEqual(c10.axes_with_capacity("80A"), ["Z"])
        self.assertEqual(c10.axes_with_capacity("160A"), ["A"])

    def test_norm_cap(self):
        self.assertEqual(C._norm_cap("40"), "40A")
        self.assertEqual(C._norm_cap(" 80a "), "80A")
        self.assertEqual(C._norm_cap(""), "")

    def test_basic_file_for_unit_token_match(self):
        d = tempfile.mkdtemp()
        for n in ("F10BASIC.PRM", "F100BASIC.PRM", "F86BASIC.prm"):
            (Path(d) / n).write_text("%\n", encoding="cp932")
        self.assertTrue(C.basic_file_for_unit(d, "10").endswith("F10BASIC.PRM"))
        self.assertTrue(C.basic_file_for_unit(d, "86").endswith("F86BASIC.prm"))
        # '1' は 'F100'/'F10' の数字トークン(100/10)に一致しない
        self.assertIsNone(C.basic_file_for_unit(d, "1"))
        self.assertIsNone(C.basic_file_for_unit(d, "99"))

    def test_basic_file_varied_extensions(self):
        # 実データは .DAT・拡張子なし・.dat 等もある。すべて拾えること
        d = tempfile.mkdtemp()
        (Path(d) / "F35BASIC").write_text("%\n", encoding="cp932")        # 拡張子なし
        (Path(d) / "F10BASIC.dat").write_text("%\n", encoding="cp932")    # .dat
        (Path(d) / "F86BASIC.DAT").write_text("%\n", encoding="cp932")    # .DAT
        self.assertTrue(C.basic_file_for_unit(d, "35").endswith("F35BASIC"))
        self.assertTrue(C.basic_file_for_unit(d, "10").endswith("F10BASIC.dat"))
        self.assertTrue(C.basic_file_for_unit(d, "86").endswith("F86BASIC.DAT"))

    def test_basic_file_prefers_prm(self):
        # 同じ号機に複数あれば .prm を優先
        d = tempfile.mkdtemp()
        (Path(d) / "F30BASIC.DAT").write_text("%\n", encoding="cp932")
        (Path(d) / "F30BASIC.PRM").write_text("%\n", encoding="cp932")
        self.assertTrue(C.basic_file_for_unit(d, "30").endswith("F30BASIC.PRM"))


class TestBasicScan(unittest.TestCase):
    # 実機同様 \n\r\r 区切りのネイティブBASIC（X,Y,Z,A 軸、AMR=25/45/85/165）
    BASIC = ("%\n\r\r"
             "N01020Q1A1P88A2P89A3P90A4P65A5P0A6P0\n\r\r"
             "N01023Q1A1P1A2P2A3P3A4P4\n\r\r"
             "N02020Q1A1P255A2P273A3P293A4P303\n\r\r"
             "N02165Q1A1P25A2P45A3P85A4P165\n\r\r"
             "%\n")

    def test_amr_to_capacity(self):
        self.assertEqual(C.amr_to_capacity(25), "20A")
        self.assertEqual(C.amr_to_capacity(45), "40A")
        self.assertEqual(C.amr_to_capacity(85), "80A")
        self.assertEqual(C.amr_to_capacity(165), "160A")
        self.assertEqual(C.amr_to_capacity(13), "10A")     # AMR-5寄せ
        self.assertEqual(C.amr_to_capacity(""), "")
        self.assertEqual(C.amr_to_capacity(999), "")

    def test_controller_from_basic_text(self):
        ctl = C.controller_from_basic_text(self.BASIC, "30")
        self.assertEqual(ctl.unit, "30")
        self.assertEqual(ctl.axes(), ["X", "Y", "Z", "A"])
        self.assertEqual(ctl.caps_text(), "X:20A Y:40A Z:80A A:160A")
        self.assertIn("ID255", ctl.amps["X"])

    def test_missing_2020_does_not_write_none(self):
        # 2020(モーターID)が無いBASIC → "IDNone/AMR25" をマスタに書かない
        basic = ("N01020Q1A1P88A2P89\r\n"
                 "N02165Q1A1P25A2P45\r\n")
        ctl = C.controller_from_basic_text(basic, "9")
        self.assertEqual(ctl.amps["X"], "AMR25")
        self.assertEqual(ctl.amps["Y"], "AMR45")
        self.assertNotIn("None", ctl.amps["X"])

    def test_scan_basic_folder(self):
        d = tempfile.mkdtemp()
        (Path(d) / "F30BASIC.DAT").write_bytes(self.BASIC.encode("cp932"))
        (Path(d) / "F84BASIC").write_bytes(
            self.BASIC.replace("A1P25A2P45A3P85A4P165",
                               "A1P85A2P165A3P85A4P165").encode("cp932"))
        scanned = C.scan_basic_folder(d)
        units = {c.unit: c for c, _ in scanned}
        self.assertEqual(set(units), {"30", "84"})
        self.assertEqual(units["84"].caps_text(), "X:80A Y:160A Z:80A A:160A")

    def test_diff_and_append(self):
        ctl30 = C.controller_from_basic_text(self.BASIC, "30")
        ctl10 = C.controller_from_basic_text(self.BASIC, "10")
        existing = [C.Controller("10", caps={"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})]
        diff = C.diff_scanned_vs_master([(ctl30, "F30"), (ctl10, "F10")], existing)
        by = {d["unit"]: d for d in diff}
        self.assertEqual(by["30"]["status"], "new")
        self.assertEqual(by["10"]["status"], "same")
        # 追記して読み直すと号機30が増えている（既存は保持）
        d = tempfile.mkdtemp()
        path = Path(d) / "master.csv"
        path.write_bytes(("," .join(C.MASTER_HEADER) + "\r\n"
                          "10,18i-M,,AC200V,,20A,40A,80A,160A\r\n").encode("cp932"))
        added = C.append_units_to_master(str(path), [ctl30])
        self.assertEqual(added, 1)
        reloaded = {c.unit: c for c in C.load_controllers(str(path))}
        self.assertEqual(set(reloaded), {"10", "30"})
        self.assertEqual(reloaded["30"].caps_text(), "X:20A Y:40A Z:80A A:160A")
        self.assertEqual(reloaded["10"].cnc, "18i-M")      # 既存行は保持


class TestManualRegister(unittest.TestCase):
    def _path(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "m.csv"
        p.write_bytes((",".join(C.MASTER_HEADER) + "\r\n"
                       "10,18i-M,,AC200V,,20A,40A,80A,160A\r\n").encode("cp932"))
        return str(p)

    def test_add_new_controller(self):
        path = self._path()
        ctl = C.Controller("40", cnc="0i-MF", voltage="AC200V",
                           caps={"X": "40A", "A": "160A"})
        self.assertEqual(C.upsert_controller(path, ctl), "added")
        d = {c.unit: c for c in C.load_controllers(path)}
        self.assertEqual(set(d), {"10", "40"})
        self.assertEqual(d["40"].cnc, "0i-MF")
        self.assertEqual(d["40"].caps_text(), "X:40A A:160A")
        self.assertEqual(d["10"].cnc, "18i-M")          # 既存は保持

    def test_update_existing_controller(self):
        path = self._path()
        ctl = C.Controller("10", cnc="18i-MB", voltage="AC200V",
                           caps={"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})
        self.assertEqual(C.upsert_controller(path, ctl), "updated")
        d = {c.unit: c for c in C.load_controllers(path)}
        self.assertEqual(len(d), 1)
        self.assertEqual(d["10"].cnc, "18i-MB")

    def test_delete_controller(self):
        path = self._path()
        self.assertTrue(C.delete_controller(path, "10"))
        self.assertEqual(C.load_controllers(path), [])
        self.assertFalse(C.delete_controller(path, "99"))


if __name__ == "__main__":
    unittest.main()


class TestBasicFileOriginPriority(unittest.TestCase):
    """号機からBASICを選ぶとき、実機が出したものを優先する。

    以前は拡張子（.prm を最優先）で決めていたため、F23 のように実機の .DAT と
    PC製の .prm が並んでいると、制御装置が読み込めなかった .prm を掴んでいた。
    """

    MACHINE = b"%\n\r\rN00000Q1L1P0\n\r\rN01825Q1A1P3000\n\r\r%\n\r\r"
    PC = b"%\nN00000Q1L1P0\nN01825Q1A1P3000\n%\n"

    def _dir(self, files):
        d = tempfile.mkdtemp()
        for name, data in files.items():
            (Path(d) / name).write_bytes(data)
        return d

    def test_machine_dat_beats_pc_prm(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE, "F23BASIC.prm": self.PC})
        self.assertTrue(
            C.basic_file_for_unit(d, "23").endswith("F23BASIC.DAT"))

    def test_machine_prm_beats_pc_dat(self):
        # 逆の並びでも、拡張子ではなく中身で選ぶ
        d = self._dir({"F82BASIC.PRM": self.MACHINE, "F82BASIC.DAT": self.PC})
        self.assertTrue(
            C.basic_file_for_unit(d, "82").endswith("F82BASIC.PRM"))

    def test_extension_order_still_applies_within_same_origin(self):
        d = self._dir({"F30BASIC.txt": self.MACHINE, "F30BASIC.prm": self.MACHINE})
        self.assertTrue(
            C.basic_file_for_unit(d, "30").endswith("F30BASIC.prm"))

    def test_pc_only_is_still_returned(self):
        # 実機が無ければPC製でも返す（使えないより使える方がよい。画面で赤く出す）
        d = self._dir({"F80BASIC.prm": self.PC})
        self.assertTrue(
            C.basic_file_for_unit(d, "80").endswith("F80BASIC.prm"))

    def test_choice_reports_origin(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE, "F23BASIC.prm": self.PC})
        path, info = C.basic_choice_for_unit(d, "23")
        self.assertTrue(path.endswith("F23BASIC.DAT"))
        self.assertEqual(info["verdict"], "machine")

    def test_choice_missing_unit(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE})
        self.assertEqual(C.basic_choice_for_unit(d, "99"), (None, None))

    def test_unit_number_still_exact(self):
        d = self._dir({"F10BASIC.DAT": self.MACHINE, "F100BASIC.DAT": self.MACHINE})
        self.assertTrue(
            C.basic_file_for_unit(d, "10").endswith("F10BASIC.DAT"))


class TestBasicCoverage(unittest.TestCase):
    """号機マスタ × BASICフォルダ ＝「実機から何を取ってくればよいか」"""

    MACHINE = b"%\n\r\rN00000Q1L1P0\n\r\rN01825Q1A1P3000\n\r\r%\n\r\r"
    CONVERTED = MACHINE.replace(b"\n\r\r", b"\r\n\r\n\r\n")
    PC = b"%\nN00000Q1L1P0\nN01825Q1A1P3000\n%\n"

    def _dir(self, files):
        d = tempfile.mkdtemp()
        for name, data in files.items():
            (Path(d) / name).write_bytes(data)
        return d

    def test_three_way_split(self):
        d = self._dir({
            "F22BASIC.txt": self.MACHINE,     # 実機あり
            "F23BASIC.DAT": self.MACHINE,     # 実機あり（PC製も並ぶ）
            "F23BASIC.prm": self.PC,
            "F24BASIC.prm": self.PC,          # PC製しか無い
            "F17BASIC.DAT": self.CONVERTED,   # 実機（改行変換済）も実機扱い
            "F99BASIC.DAT": self.MACHINE,     # 号機マスタに無い
        })
        cov = C.basic_coverage(d, ["17", "22", "23", "24", "81"])
        self.assertEqual(sorted(u for u, _f in cov["ok"]), ["17", "22", "23"])
        self.assertEqual([u for u, _n in cov["pc_only"]], ["24"])
        self.assertEqual(cov["missing"], ["81"])
        self.assertEqual([u for u, _n in cov["extra"]], ["99"])

    def test_ok_points_at_the_machine_file(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE, "F23BASIC.prm": self.PC})
        cov = C.basic_coverage(d, ["23"])
        self.assertEqual(cov["ok"], [("23", "F23BASIC.DAT")])

    def test_pc_only_lists_what_is_there(self):
        d = self._dir({"F24BASIC.prm": self.PC, "F24BASIC.txt": self.PC})
        cov = C.basic_coverage(d, ["24"])
        self.assertEqual(cov["pc_only"][0][0], "24")
        self.assertEqual(sorted(cov["pc_only"][0][1]), ["F24BASIC.prm", "F24BASIC.txt"])

    def test_summary_text(self):
        d = self._dir({"F22BASIC.txt": self.MACHINE, "F24BASIC.prm": self.PC})
        cov = C.basic_coverage(d, ["22", "24", "81"])
        text = C.coverage_summary(cov)
        self.assertIn("F24", text)
        self.assertIn("F81", text)
        self.assertIn("実機のBASICがある 1台", text)

    def test_everything_covered_has_no_warnings(self):
        d = self._dir({"F22BASIC.txt": self.MACHINE})
        cov = C.basic_coverage(d, ["22"])
        self.assertEqual(cov["pc_only"], [])
        self.assertEqual(cov["missing"], [])

    def test_leading_zero_units_match(self):
        d = self._dir({"F10BASIC.DAT": self.MACHINE})
        cov = C.basic_coverage(d, ["010"])
        self.assertEqual([u for u, _f in cov["ok"]], ["010"])

    def test_missing_folder(self):
        cov = C.basic_coverage("/no/such/dir", ["22"])
        self.assertEqual(cov["missing"], ["22"])
        self.assertEqual(cov["ok"], [])

    def test_files_by_unit_ignores_non_basic(self):
        d = self._dir({"F22BASIC.txt": self.MACHINE, "readme.txt": b"hello"})
        self.assertEqual(list(C.basic_files_by_unit(d)), ["22"])


class TestCapacityMatch(unittest.TestCase):
    """BASICのアンプ最大電流(N2165)と号機マスタの容量が合っているか。

    実機BASIC 10台・34軸を突き合わせた結果、容量と1対1に対応していたのが
    N2165 だった（30軸一致／F25・F31 の4軸だけ食い違い）。
    """

    # 軸は 1020（軸名のASCIIコード X=88 Y=89 Z=90 A=65）で決まる。
    # 位置で決め打ちしないので、見本にも実ファイルと同じく 1020 を入れる。
    def _text(self, a1, a2=None, a3=None, a4=None):
        vals = [v for v in (a1, a2, a3, a4) if v is not None]
        codes = "".join(f"A{i}P{c}" for i, c in
                        enumerate((88, 89, 90, 65)[:len(vals)], 1))
        seg = "".join(f"A{i}P{v}" for i, v in enumerate(vals, 1))
        return f"%\nN01020Q1{codes}\nN02165Q1{seg}\n%\n"

    def _ctl(self, **caps):
        return C.Controller("23", caps=caps)

    def test_all_match(self):
        t = self._text("25", "45", "85", "165")
        ctl = self._ctl(X="20A", Y="40A", Z="80A", A="160A")
        self.assertEqual(C.check_capacity_match(t, ctl), [])

    def test_reads_capacity_for_every_axis(self):
        t = self._text("25", "45", "85", "165")
        self.assertEqual(C.capacity_from_basic(t),
                         {"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})

    def test_mismatch_reported_per_axis(self):
        # 実データの F25 と同じ形: マスタ80Aなのにファイルは25/45
        t = self._text("25", "45")
        ctl = self._ctl(X="80A", Y="80A")
        ng = C.check_capacity_match(t, ctl)
        self.assertEqual(len(ng), 2)
        self.assertIn("X軸", ng[0])
        self.assertIn("80A", ng[0])

    def test_axes_without_capacity_are_skipped(self):
        t = self._text("25", "45")
        ctl = self._ctl(X="20A")          # Y の容量がマスタに無い
        self.assertEqual(C.check_capacity_match(t, ctl), [])

    def test_missing_parameter_is_skipped(self):
        ctl = self._ctl(X="20A")
        self.assertEqual(C.check_capacity_match("%\nN01825Q1A1P3000\n%\n", ctl), [])

    def test_master_with_odd_capacity_is_reported(self):
        # マスタ側が見慣れない値でも、ファイルから読めたなら食い違いとして出す
        # （BASICが正・マスタが古い、という向きで見る）
        t = self._text("25")
        ng = C.check_capacity_match(t, self._ctl(X="999A"))
        self.assertEqual(len(ng), 1)
        self.assertIn("999A", ng[0])
        self.assertIn("20A", ng[0])

    def test_no_controller(self):
        self.assertEqual(C.check_capacity_match(self._text("25"), None), [])

    def test_map_is_overridable(self):
        # AMR=30 は既定の規則(AMR=容量+5)だと 25A で標準容量に無く判定不能。
        # 設定で対応表を渡せば、その機種の流儀で読める。
        t = self._text("30")
        ctl = self._ctl(X="20A")
        self.assertEqual(C.check_capacity_match(t, ctl, {"20A": "30"}), [])

    def test_unresolvable_amr_is_not_reported(self):
        # 判定できない値で「食い違い」と言わない（実データ F35 の AMR=10 など）
        t = self._text("10")
        self.assertEqual(C.check_capacity_match(t, self._ctl(X="20A")), [])

    def test_axis_order_from_1020_not_position(self):
        # スロットの位置ではなく 1020 の軸名で判定する（B/C軸も見る）
        t = ("%\nN01020Q1A1P90A2P66\nN02165Q1A1P25A2P85\n%\n")   # A1=Z, A2=B
        caps = C.capacity_from_basic(t)
        self.assertEqual(caps, {"Z": "20A", "B": "80A"})


class TestMasterFixFromBasic(unittest.TestCase):
    """実機のBASICが正で、号機マスタが古いときにマスタを直す。

    容量→N2165 の対応（20A=25 / 40A=45 / 80A=85 / 160A=165）を逆に使う。
    """

    MACHINE = (b"%\n\r\rN01020Q1A1P88A2P89A3P90A4P65\n\r\r"
               b"N02165Q1A1P25A2P45A3P85A4P165\n\r\r%\n\r\r")
    PC = b"%\nN01020Q1A1P88A2P89\nN02165Q1A1P25A2P45\n%\n"

    def _dir(self, files):
        d = tempfile.mkdtemp()
        for name, data in files.items():
            (Path(d) / name).write_bytes(data)
        return d

    def test_capacity_read_from_basic(self):
        caps = C.capacity_from_basic(self.MACHINE.decode("cp932"))
        self.assertEqual(caps, {"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})

    def test_fix_rows_lists_differences(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE})
        ctl = C.Controller("23", caps={"X": "80A", "Y": "40A"})
        fixes = C.master_fix_rows(d, [ctl])
        got = {(ax, cur, cap) for _u, ax, cur, cap, _n in fixes}
        self.assertIn(("X", "80A", "20A"), got)      # 違う → 直す
        self.assertNotIn(("Y", "40A", "40A"), got)   # 同じ → 出さない
        self.assertIn(("Z", "", "80A"), got)         # マスタが空 → 埋める

    def test_pc_made_file_is_not_used_as_evidence(self):
        d = self._dir({"F23BASIC.prm": self.PC})
        ctl = C.Controller("23", caps={"X": "80A"})
        self.assertEqual(C.master_fix_rows(d, [ctl]), [])

    def test_unknown_unit_skipped(self):
        d = self._dir({"F99BASIC.DAT": self.MACHINE})
        self.assertEqual(C.master_fix_rows(d, [C.Controller("23")]), [])

    def test_apply_writes_master(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE})
        path = str(Path(d) / "master.csv")
        ctl = C.Controller("23", cnc="31i-MA", caps={"X": "80A", "Y": "40A"})
        C.upsert_controller(path, ctl)
        fixes = C.master_fix_rows(d, [ctl])
        n = C.apply_master_fixes(path, [ctl], fixes)
        self.assertEqual(n, 1)
        again = {str(c.unit): c for c in C.load_controllers(path)}["23"]
        self.assertEqual(again.caps["X"], "20A")
        self.assertEqual(again.caps["Z"], "80A")
        self.assertEqual(again.cnc, "31i-MA")       # 他の項目は保持

    def test_apply_is_idempotent(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE})
        path = str(Path(d) / "master.csv")
        ctl = C.Controller("23", caps={"X": "80A"})
        C.upsert_controller(path, ctl)
        C.apply_master_fixes(path, [ctl], C.master_fix_rows(d, [ctl]))
        ctls2 = C.load_controllers(path)
        self.assertEqual(C.master_fix_rows(d, ctls2), [])

    def test_no_fixes_when_already_matching(self):
        d = self._dir({"F23BASIC.DAT": self.MACHINE})
        ctl = C.Controller("23", caps={"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})
        self.assertEqual(C.master_fix_rows(d, [ctl]), [])


class TestDuplicateSpecJudgement(unittest.TestCase):
    """中身が同じBASICは、仕様が同じなら正常。

    BASICは「基本パラメータ」なので、同じ仕様の号機は同じ中身になる。
    問題になるのは「中身は同じなのにハード仕様が違う」場合だけ。
    Ver や SERVO の版数はソフトの版なので見ない（F27/F28 は枝番だけ違う）。
    """

    def _ctl(self, unit, cnc="Oi-MD", voltage="AC200V", ver="", servo="", **caps):
        return C.Controller(unit, cnc=cnc, voltage=voltage, ver=ver,
                            servo=servo, caps=caps)

    def test_same_spec_is_normal(self):
        ctls = [self._ctl("24", X="80A", Y="80A"), self._ctl("80", X="80A", Y="80A")]
        got = C.classify_duplicate_groups([["F24BASIC.prm", "F80BASIC.prm"]], ctls)
        self.assertEqual(got[0][1], "same_spec")

    def test_servo_edition_alone_is_not_flagged(self):
        ctls = [self._ctl("27", servo="90C5/0006 90C8/0004", X="20A"),
                self._ctl("28", servo="90C5/0006 90C8/0003", X="20A")]
        got = C.classify_duplicate_groups([["F27BASIC.PRM", "F28BASIC.PRM"]], ctls)
        self.assertEqual(got[0][1], "same_spec")

    def test_capacity_difference_is_flagged(self):
        ctls = [self._ctl("20", Y="160A", Z="80A"), self._ctl("21", Y="80A", Z="160A")]
        got = C.classify_duplicate_groups([["F20BASIC.DAT", "F21BASIC.DAT"]], ctls)
        self.assertEqual(got[0][1], "diff_spec")
        self.assertIn("Y容量", got[0][2])

    def test_cnc_difference_is_flagged(self):
        ctls = [self._ctl("20", cnc="Oi-MD", X="20A"), self._ctl("21", cnc="31i-MA", X="20A")]
        got = C.classify_duplicate_groups([["F20BASIC.DAT", "F21BASIC.DAT"]], ctls)
        self.assertEqual(got[0][1], "diff_spec")

    def test_unknown_unit(self):
        got = C.classify_duplicate_groups([["F33BASIC", "F34BASIC"]], [])
        self.assertEqual(got[0][1], "unknown")
        self.assertIn("F33", got[0][2])

    def test_same_spec_helper(self):
        a = self._ctl("1", X="20A")
        self.assertEqual(C.same_spec(a, self._ctl("2", X="20A")), [])
        self.assertEqual(C.same_spec(a, self._ctl("2", X="40A")), ["X容量"])
        self.assertEqual(C.same_spec(a, None), [])
