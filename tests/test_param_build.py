# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import fanuc_param as F_PRM
from nd287_app import param_build as B


def _logical(data):
    """区切り(EOB)の違いを無視して中身だけ比べる。"""
    if isinstance(data, bytes):
        data = data.decode("cp932")
    return [l for l in data.replace("\r", "\n").split("\n") if l != ""]
from nd287_app import fanuc_param as F

# N形式（実機ネイティブ）BASIC。1815 を各軸に持たせ、A4 をフル(#1=1)・他をセミにする。
BASIC_N = (
    "%\r\n"
    "\r\n"
    "N01815Q1A1P00000000A2P00000000A3P00000000A4P00000010 \r\n"
    "\r\n"
    "N01825Q1A1P3000A2P3000A3P3000A4P3000 \r\n"
    "\r\n"
    "N02020Q1A1P255A2P273A3P293A4P303 \r\n"
    "\r\n"
    "%\r\n"
)


class TestBuild(unittest.TestCase):
    def test_build_text_fanuc_axis_only(self):
        values = {"01825": "2500", "02020": "300"}
        newtext, missing, fmt = B.build_text(BASIC_N, values, axis=4)
        self.assertEqual(fmt, "fanuc")
        self.assertEqual(missing, [])
        self.assertEqual(F.get_value(newtext, "1825", "A4"), "2500")
        self.assertEqual(F.get_value(newtext, "1825", "A1"), "3000")  # 他軸不変

    def test_create_file_byte_roundtrip(self):
        # BASIC の A4 を変えた製品ファイルを作り、create_file の出力が一致するか
        d = tempfile.mkdtemp()
        master = os.path.join(d, "F30BASIC.PRM")
        with open(master, "w", encoding="cp932", newline="") as f:
            f.write(BASIC_N)
        product, _ = F.set_value(BASIC_N, "1825", "2500", "A4")
        values = {"01825": "2500"}
        out, missing, fmt = B.create_file(master, d, values, axis=4,
                                          prefix="T", seiban="50013078")
        self.assertEqual(out.name, "T50013078.DAT")
        # 中身は製品と一致し、区切りだけ実機の形式(LF CR CR)に統一される
        self.assertEqual(out.read_bytes(), F_PRM.prm_bytes(product))
        self.assertEqual(_logical(out.read_bytes()), _logical(product))
        self.assertIn(b"\n\r\r", out.read_bytes())

    def test_filename(self):
        # 既定は .DAT（実機が出力する形式。.prm は読めなかった）
        self.assertEqual(B.filename("R", "12345"), "R12345.DAT")
        self.assertEqual(B.filename("R", "12345", ".prm"), "R12345.prm")
        self.assertEqual(B.filename("R", "12345", "TXT"), "R12345.TXT")


class TestPreviewAndHeader(unittest.TestCase):
    def test_preview_rows_old_new(self):
        # 1825/A4 を 2500 に。旧値はBASICの A4=3000... ここでは BASIC_N の A4 値
        rows = B.preview_rows(BASIC_N, {"01825": "2500", "02020": "300"}, axis=4)
        d = {n: (o, nw) for (n, o, nw) in rows}
        self.assertEqual(d["01825"], ("3000", "2500"))
        self.assertEqual(d["02020"], ("303", "300"))

    def test_header_info_empty_for_fanuc(self):
        self.assertEqual(B.header_info(BASIC_N), {})

    def test_header_info_from_header_csv(self):
        from tests.test_prm_format import SAMPLE_PRM
        h = B.header_info(SAMPLE_PRM)
        self.assertEqual(h.get("motor"), "αiS4/5000-B")
        self.assertEqual(h.get("motor_no"), "A06B-2215-B000")
        self.assertEqual(h.get("direction"), "CCW")
        self.assertEqual(h.get("gear"), "1/36")   # 先頭の ' は除去


class TestMultiAxis(unittest.TestCase):
    """2軸テーブル: 1つのBASICへ傾斜軸・回転軸の両方を入れて1ファイルにする。"""

    def _product_two_axes(self):
        # 完成製品: A2(傾斜)と A4(回転)を別々に変えた .prm を作る
        p, _ = F.set_value(BASIC_N, "1825", "2500", "A2")
        p, _ = F.set_value(p, "2020", "260", "A2")
        p, _ = F.set_value(p, "1825", "1500", "A4")
        return p

    def test_product_axis_values_splits_by_axis(self):
        product = self._product_two_axes()
        per_axis, common = B.product_axis_values(BASIC_N, product)
        self.assertEqual(set(per_axis), {2, 4})
        self.assertEqual(per_axis[2], {"01825": "2500", "02020": "260"})
        self.assertEqual(per_axis[4], {"01825": "1500"})
        self.assertEqual(common, {})

    def test_build_text_multi_writes_both_axes(self):
        per_axis = {2: {"01825": "2500"}, 4: {"01825": "1500"}}
        newtext, missing, fmt = B.build_text_multi(BASIC_N, per_axis)
        self.assertEqual(fmt, "fanuc")
        self.assertEqual(missing, [])
        self.assertEqual(F.get_value(newtext, "1825", "A2"), "2500")
        self.assertEqual(F.get_value(newtext, "1825", "A4"), "1500")
        self.assertEqual(F.get_value(newtext, "1825", "A1"), "3000")  # 他軸不変
        self.assertEqual(F.get_value(newtext, "1825", "A3"), "3000")

    def test_multi_byte_roundtrip_reconstructs_product(self):
        # 完成製品(2軸)→ 差分を軸ごとに取り → BASICへ両軸入れると製品とバイト一致
        d = tempfile.mkdtemp()
        master = os.path.join(d, "F30BASIC.PRM")
        with open(master, "w", encoding="cp932", newline="") as f:
            f.write(BASIC_N)
        product = self._product_two_axes()
        per_axis, common = B.product_axis_values(BASIC_N, product)
        out, missing, fmt = B.create_file_multi(
            master, d, per_axis, common, prefix="TR", seiban="50013078")
        self.assertEqual(out.name, "TR50013078.DAT")
        self.assertEqual(missing, [])
        self.assertEqual(_logical(out.read_bytes()), _logical(product))

    def test_preview_rows_multi(self):
        per_axis = {2: {"01825": "2500"}, 4: {"01825": "1500"}}
        rows = B.preview_rows_multi(BASIC_N, per_axis)
        d = {(n, ax): (o, nw) for (n, ax, o, nw) in rows}
        self.assertEqual(d[("01825", 2)], ("3000", "2500"))
        self.assertEqual(d[("01825", 4)], ("3000", "1500"))

    def test_common_param_not_forced_onto_axis(self):
        # 系統共通(L1)パラメータを足したBASICで、共通変更が軸を汚さないこと
        basic = BASIC_N.replace(
            "N02020Q1A1P255A2P273A3P293A4P303 \r\n",
            "N02020Q1A1P255A2P273A3P293A4P303 \r\nN08130Q1L1P3 \r\n")
        product, _ = F.set_value(basic, "8130", "4", "L1")
        per_axis, common = B.product_axis_values(basic, product)
        self.assertEqual(per_axis, {})
        self.assertEqual(common, {"08130": "4"})
        newtext, missing, _ = B.build_text_multi(basic, per_axis, common)
        self.assertEqual(missing, [])
        self.assertEqual(F.get_value(newtext, "8130", "L1"), "4")


class TestResolveProductValues(unittest.TestCase):
    """ヘッダ＋CSV製品の表示値(' と *)をネイティブBASICへ書ける実値へ解決する。"""

    def test_strip_quote_plain(self):
        r = B.resolve_product_values(BASIC_N, {"01825": "3000", "02020": "'262"}, axis=4)
        self.assertEqual(r["01825"], "3000")
        self.assertEqual(r["02020"], "262")          # ' を除去

    def test_star_keeps_basic_bit(self):
        # BASIC_N の 1815/A4 = 00000010。'*' の桁は BASIC のビットを残す
        r = B.resolve_product_values(BASIC_N, {"01815": "'001*0000"}, axis=4)
        self.assertEqual(r["01815"], "00100000")     # index3の*→BASICの0
        r2 = B.resolve_product_values(BASIC_N, {"01815": "1*******"}, axis=4)
        self.assertEqual(r2["01815"], "10000010")    # MSBだけ1、他はBASIC(00000010)

    def test_build_text_writes_resolved_value(self):
        newtext, missing, fmt = B.build_text(BASIC_N, {"01815": "1*******"}, axis=4)
        self.assertEqual(fmt, "fanuc")
        self.assertEqual(F.get_value(newtext, "1815", "A4"), "10000010")
        self.assertEqual(F.get_value(newtext, "1815", "A1"), "00000000")  # 他軸不変

    def test_preview_shows_resolved(self):
        rows = B.preview_rows(BASIC_N, {"01815": "'001*0000"}, axis=4)
        d = {n: (o, nw) for (n, o, nw) in rows}
        self.assertEqual(d["01815"], ("00000010", "00100000"))  # 旧→解決後の新


class TestServoOptions(unittest.TestCase):
    """作成時オプション: 2000を0／1815原点ビットON-OFF。"""

    def test_set_bit_value(self):
        self.assertEqual(F.set_bit_value("00000000", 5, True), "00100000")
        self.assertEqual(F.set_bit_value("00100000", 5, False), "00000000")
        self.assertEqual(F.set_bit_value("'00000010", 5, True), "00100010")  # ' 除去
        self.assertEqual(F.set_bit_value("0", 1, True), "00000010")          # ビット列扱い
        self.assertEqual(F.set_bit_value("12", 1, True), "14")               # 非ビット=整数

    def test_zero_motor_dgpr_bit_only(self):
        # 2000 #1(DGPR) だけを0にする（他ビットは保持）。BASIC_Nに2000を足して検証
        basic = BASIC_N.replace(
            "N02020Q1A1P255A2P273A3P293A4P303 \r\n",
            "N02020Q1A1P255A2P273A3P293A4P303 \r\n"
            "N02000Q1A1P00000010A2P00000010A3P00000010A4P00000010 \r\n")
        v = B.with_servo_options(basic, 4, {"01825": "2500"}, zero_motor=True,
                                 zero_param="2000", zero_bit=1)
        self.assertEqual(v["2000"], "00000000")  # #1(bit1)=0、元00000010→00000000
        self.assertEqual(v["01825"], "2500")     # 他は不変
        # 他ビットは保持される（例 #4を立てた値なら#4は残る）
        v2 = B.with_servo_options(
            basic.replace("A4P00000010 \r\n", "A4P00010010 \r\n"), 4, {},
            zero_motor=True, zero_param="2000", zero_bit=1)
        self.assertEqual(v2["2000"], "00010000")  # #4は保持・#1だけ0

    def test_origin_on_off_from_basic(self):
        # BASIC_N の 1815/A4 = 00000010。原点(#5)ON → 00100010、OFF → 00000010
        on = B.with_servo_options(BASIC_N, 4, {}, origin="on",
                                  origin_param="1815", origin_bit=5)
        self.assertEqual(on["1815"], "00100010")
        off = B.with_servo_options(BASIC_N, 4, {}, origin="off",
                                   origin_param="1815", origin_bit=5)
        self.assertEqual(off["1815"], "00000010")

    def test_origin_uses_product_value_when_present(self):
        # 製品が 1815='001*0000' を持つ → 解決(A4=...0010で*埋め)後に #5 を立てる
        v = B.with_servo_options(BASIC_N, 4, {"1815": "'001*0000"}, origin="on",
                                 origin_param="1815", origin_bit=5)
        # '001*0000' を A4(00000010)へ解決 → 00100000、#5は既に1なのでそのまま
        self.assertEqual(v["1815"], "00100000")

    def test_origin_none_leaves_alone(self):
        v = B.with_servo_options(BASIC_N, 4, {"01825": "2500"}, origin=None)
        self.assertNotIn("1815", v)


class TestDetectMode(unittest.TestCase):
    def test_full_on_axis4(self):
        # A4 は #1=1 → フル、A1 は #1=0 → セミ（軸で違う）
        mode, eff = B.detect_mode(BASIC_N, {}, axis=4)
        self.assertEqual(mode, "フル")
        self.assertEqual(eff, "00000010")
        mode1, _ = B.detect_mode(BASIC_N, {}, axis=1)
        self.assertEqual(mode1, "セミ")

    def test_change_overrides_basic(self):
        # 変更で 1815/A4 をセミ(#1=0)へ → 判定もセミ
        mode, eff = B.detect_mode(BASIC_N, {"01815": "00000000"}, axis=4)
        self.assertEqual(mode, "セミ")

    def test_quoted_star_value_resolves_before_detect(self):
        # ヘッダ＋CSV由来の "'0000001*"（表示クォート＋不問ビット）でも、
        # 実際に書く値へ解決してからモード判定する（以前は判定不能で""）
        mode, eff = B.detect_mode(BASIC_N, {"1815": "'0000001*"}, axis=4)
        self.assertEqual(mode, "フル")


class TestHeaderCsvMissing(unittest.TestCase):
    def test_short_row_reported_missing_not_silently_kept(self):
        # 5列しかない行は書き換えられない → missing に出す（黙って旧値のままにしない）
        raw = ('System Version=1\nModel=X\nSeiban=1\n'
               '"番号","軸","和名","英名","値","説明"\n'
               '"1815","---","","","00000010"\n')
        new, missing, fmt = B.build_text(raw, {"1815": "00000000"}, 0, "")
        self.assertEqual(fmt, "headercsv")
        self.assertEqual(missing, ["1815"])
        self.assertIn("00000010", new)     # 旧値のまま＝ただし missing で報告済み


if __name__ == "__main__":
    unittest.main()


class TestMachineReadableParamFile(unittest.TestCase):
    """制御装置が取り込める形になっているか（.prm が読めなかった件の再発防止）

    実機が自分で出力したバックアップ（F23BASIC.DAT）と同じ形にそろえる:
    ・ブロックの区切りは LF CR CR（PCの改行のままだと読み込めなかった）
    ・行の中身は1バイトも変えない（値の桁・末尾の空白も形式の一部）
    """

    RAW = "%\r\nN01825Q1A1P3000A4P3000\r\nN02020Q1A1P303 \r\n%\r\n"

    def test_eob_is_machine_format(self):
        self.assertEqual(F_PRM.prm_bytes("%\nN01825Q1A1P3000\n%"),
                         b"%\n\r\rN01825Q1A1P3000\n\r\r%\n\r\r")

    def test_eob_selectable(self):
        from nd287_app import fanuc
        self.assertEqual(F_PRM.prm_bytes("%\nM\n%", fanuc.EOB_CRLF),
                         b"%\r\nM\r\n%\r\n")

    def test_line_content_untouched(self):
        # 末尾の空白も実機の形式の一部なので落とさない
        self.assertIn(b"N02020Q1A1P303 \n\r\r", F_PRM.prm_bytes(self.RAW))

    def test_any_input_newline_normalizes(self):
        for raw in ("%\nA\n%", "%\r\nA\r\n%", "%\n\r\rA\n\r\r%"):
            self.assertEqual(F_PRM.prm_bytes(raw), b"%\n\r\rA\n\r\r%\n\r\r")

    def test_idempotent(self):
        # 一度書いたファイルを読み直して書いても同じ（区切りが増殖しない）
        once = F_PRM.prm_bytes(self.RAW)
        self.assertEqual(F_PRM.prm_bytes(once.decode("ascii")), once)

    def test_write_text_uses_machine_eob_for_native(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "x.DAT")
        B.write_text(out, self.RAW)
        self.assertIn(b"\n\r\r", open(out, "rb").read())

    def test_write_text_keeps_headercsv_as_is(self):
        # 社内で読むヘッダ＋CSV形式(.prm)は従来どおり cp932・改行そのまま
        d = tempfile.mkdtemp()
        out = os.path.join(d, "y.prm")
        text = "Seiban,50013078\r\n名称,値\r\n"
        B.write_text(out, text, "headercsv")
        self.assertEqual(open(out, "rb").read(), text.encode("cp932"))


class TestParamValidate(unittest.TestCase):
    OK = "%\nN01825Q1A1P3000\nN02020Q1A1P303 \n%"

    def _has(self, problems, word):
        return any(word in p for p in problems)

    def test_clean_file_has_no_problems(self):
        self.assertEqual(F_PRM.validate_prm(self.OK), [])

    def test_flags_missing_percent(self):
        self.assertTrue(self._has(F_PRM.validate_prm("N01825Q1A1P3000"), "%"))

    def test_cnc_id_header_is_accepted(self):
        # 実機は "%(CNCID=…)" と、% の後ろに制御装置IDを付けて出すことがある
        ok = "%(CNCID=3C7B5D01,F6914B1A)\nN01825Q1A1P3000\n%"
        self.assertEqual(F_PRM.validate_prm(ok), [])

    def test_flags_semicolon(self):
        self.assertTrue(self._has(
            F_PRM.validate_prm("%\nN01825Q1A1P3000 ;\n%"), '";"'))

    def test_flags_non_ascii(self):
        self.assertTrue(self._has(
            F_PRM.validate_prm("%\nN01825Q1A1P3000\n(コメント)\n%"), "ASCII"))

    def test_flags_numbers_the_machine_does_not_have(self):
        # 実機のバックアップに無い番号は取込が止まる原因になる
        ref = "%\nN01825Q1A1P3000\n%"
        text = "%\nN01825Q1A1P3000\nN09999Q1L1P00000000\n%"
        self.assertTrue(self._has(F_PRM.validate_prm(text, ref), "実機のバックアップに無い"))
        self.assertEqual(F_PRM.unknown_numbers(text, ref), [9999])

    def test_no_reference_means_no_number_check(self):
        text = "%\nN09999Q1L1P00000000\n%"
        self.assertEqual(F_PRM.validate_prm(text, ""), [])

    def test_drop_numbers(self):
        text = "%\nN01825Q1A1P3000\nN09999Q1L1P0\n%"
        kept, removed = F_PRM.drop_numbers(text, [9999])
        self.assertEqual(removed, [9999])
        self.assertNotIn("N09999", kept)
        self.assertIn("N01825", kept)

    def test_numbers_in(self):
        self.assertEqual(F_PRM.numbers_in(self.OK), {1825, 2020})


class TestOldFormatBasic(unittest.TestCase):
    """旧書式のBASIC（Q1なし・空白区切り）。F10〜F17 の8台がこの形。

    以前はN形式として認識できず、値が入らないままマスタと違うファイルが
    出力されていた（実機のBASIC 31本を調べて発覚）。
    """

    OLD = ("%\n"
           "N00000 P 00000010\n"
           "N01320 A1 P-1 A2 P-1 A3 P-1 A4 P-1\n"
           "N01825 A1 P 3000 A2 P 3000 A3 P 3000 A4 P 3000\n"
           "N04000 A1 P 00000000\n"
           "%\n")

    def test_recognized_as_native(self):
        self.assertTrue(F_PRM.looks_like_fanuc_prm(self.OLD))

    def test_reads_values(self):
        self.assertEqual(F_PRM.get_value(self.OLD, "1825", "A4"), "3000")
        self.assertEqual(F_PRM.get_value(self.OLD, "1320", "A2"), "-1")
        self.assertEqual(F_PRM.get_value(self.OLD, "0"), "00000010")

    def test_replacement_keeps_spacing_byte_for_byte(self):
        new, ok = F_PRM.set_value(self.OLD, "1825", "2500", "A4")
        self.assertTrue(ok)
        self.assertIn("N01825 A1 P 3000 A2 P 3000 A3 P 3000 A4 P 2500", new)

    def test_same_value_write_changes_nothing(self):
        new, ok = F_PRM.set_value(self.OLD, "1825", "3000", "A4")
        self.assertTrue(ok)
        self.assertEqual(new, self.OLD)

    def test_only_one_line_changes(self):
        new, _ = F_PRM.set_value(self.OLD, "1825", "2500", "A4")
        a, b = self.OLD.split("\n"), new.split("\n")
        self.assertEqual(len(a), len(b))
        self.assertEqual(sum(1 for x, y in zip(a, b) if x != y), 1)

    def test_negative_value_preserved(self):
        new, ok = F_PRM.set_value(self.OLD, "1320", "-5", "A3")
        self.assertTrue(ok)
        self.assertIn("N01320 A1 P-1 A2 P-1 A3 P-5 A4 P-1", new)

    def test_build_text_takes_the_native_path(self):
        newtext, missing, fmt = B.build_text(self.OLD, {"01825": "2500"}, 4, "50013078")
        self.assertEqual(fmt, "fanuc")
        self.assertEqual(missing, [])
        self.assertEqual(F_PRM.get_value(newtext, "1825", "A4"), "2500")

    def test_headercsv_still_not_treated_as_native(self):
        csv_like = 'Seiban,50013078\r\n"1815","値",""\r\n"1825","3000",""\r\n'
        self.assertFalse(F_PRM.looks_like_fanuc_prm(csv_like))


class TestMachineReference(unittest.TestCase):
    """実機のバックアップが手元に無いとき（PC側のBASICしか無いとき）の照合。

    1本と突き合わせるのではなく、フォルダの実機BASIC全部の番号の和集合と比べる。
    実データ: 実機BASIC 16本の和集合に N09802・N09820〜N09999 は1つも無く、
    PC製7本には全部入っていた（PC側ツールが足したもの）。
    """

    MACHINE_A = "%\n\r\rN00000Q1L1P0\n\r\rN01825Q1A1P3000\n\r\r%\n\r\r"
    MACHINE_B = "%\n\r\rN00000Q1L1P0\n\r\rN02020Q1A1P303\n\r\r%\n\r\r"
    PC = "%\nN00000Q1L1P0\nN01825Q1A1P3000\nN02020Q1A1P303\nN09802Q1P0\n%\n"
    OLD_MACHINE = "%\n\r\rN00000 P 0\n\r\rN01825 A1 P 3000\n\r\r%\n\r\r"

    def _dir(self, files):
        d = tempfile.mkdtemp()
        for name, text in files.items():
            (Path(d) / name).write_bytes(text.encode("cp932"))
        return d

    def test_union_of_machine_files(self):
        d = self._dir({"F22BASIC.DAT": self.MACHINE_A,
                       "F25BASIC.PRM": self.MACHINE_B,
                       "F23BASIC.prm": self.PC})
        ref, used = B.machine_reference(d, like=self.PC)
        self.assertEqual(sorted(used), ["F22BASIC.DAT", "F25BASIC.PRM"])
        self.assertEqual(F_PRM.numbers_in(ref), {0, 1825, 2020})
        # どの実機も持っていない N09802 だけが引っかかる
        self.assertEqual(F_PRM.unknown_numbers(self.PC, ref), [9802])

    def test_ignores_pc_made_files(self):
        d = self._dir({"F22BASIC.DAT": self.MACHINE_A, "F23BASIC.prm": self.PC})
        ref, used = B.machine_reference(d, like=self.PC)
        self.assertEqual(used, ["F22BASIC.DAT"])
        self.assertNotIn(9802, F_PRM.numbers_in(ref))

    def test_format_generations_are_not_mixed(self):
        # 旧書式の実機を、新書式のBASICの照合に混ぜない（番号体系が違う）
        d = self._dir({"F12BASIC.DAT": self.OLD_MACHINE, "F23BASIC.prm": self.PC})
        ref, used = B.machine_reference(d, like=self.PC)
        self.assertEqual(used, [])
        self.assertEqual(ref, "")

    def test_excludes_itself(self):
        d = self._dir({"F22BASIC.DAT": self.MACHINE_A})
        ref, used = B.machine_reference(d, like=self.MACHINE_A,
                                        exclude=Path(d) / "F22BASIC.DAT")
        self.assertEqual(used, [])

    def test_no_machine_files_returns_empty(self):
        d = self._dir({"F23BASIC.prm": self.PC})
        self.assertEqual(B.machine_reference(d, like=self.PC), ("", []))

    def test_missing_folder(self):
        self.assertEqual(B.machine_reference("/no/such/dir"), ("", []))


class TestIndividualData(unittest.TestCase):
    """機械の個体データ（原点・グリッドシフト）が入っていないか。

    仕様（CNCユニット・容量・アンプ）が同じ号機どうしでも、原点は据付けごとに
    違う。実データ: F24 と F80 はマスタ上まったく同じ仕様だが、そのBASICには
    グリッドシフト A1=5420/A2=9400 と APZ=1（原点確立済み）が入っていた。
    「容量が同じなら流用できる」が成り立たない理由。
    """

    CLEAN = ("%\nN01815Q1A1P00000000A2P00000000\n"
             "N01850Q1A1P0A2P0\nN01825Q1A1P3000\n%\n")
    WITH_ORIGIN = ("%\nN01815Q1A1P00110010A2P00110010\n"
                   "N01850Q1A1P5420A2P9400\nN01825Q1A1P3000\n%\n")

    def test_clean_basic_has_none(self):
        self.assertEqual(F_PRM.individual_data(self.CLEAN), [])

    def test_grid_shift_detected(self):
        found = F_PRM.individual_data(self.WITH_ORIGIN)
        self.assertTrue(any("グリッドシフト" in f for f in found))
        self.assertTrue(any("5420" in f for f in found))

    def test_origin_established_detected(self):
        found = F_PRM.individual_data(self.WITH_ORIGIN)
        self.assertTrue(any("APZ" in f for f in found))
        self.assertTrue(any("A1・A2" in f for f in found))

    def test_origin_only(self):
        text = "%\nN01815Q1A1P00100000\nN01850Q1A1P0\n%\n"
        found = F_PRM.individual_data(text)
        self.assertEqual(len(found), 1)
        self.assertIn("APZ", found[0])

    def test_shift_only(self):
        text = "%\nN01815Q1A1P00000000\nN01850Q1A1P-94\n%\n"
        found = F_PRM.individual_data(text)
        self.assertEqual(len(found), 1)
        self.assertIn("-94", found[0])

    def test_bit_and_number_are_configurable(self):
        # 番号・ビットは機種で違うことがあるので変えられる
        text = "%\nN01815Q1A1P00000010\n%\n"
        self.assertEqual(F_PRM.individual_data(text), [])
        found = F_PRM.individual_data(text, origin_bit=1)
        self.assertTrue(any("#1" in f for f in found))

    def test_unlabelled_origin(self):
        text = "%\nN01815Q1P00100000\n%\n"
        self.assertTrue(any("軸指定なし" in f for f in F_PRM.individual_data(text)))

    def test_empty_text(self):
        self.assertEqual(F_PRM.individual_data("%\n%\n"), [])


class TestZeroIndividual(unittest.TestCase):
    """グリッドシフト・バックラッシ補正は出荷ファイルでは0にする。

    機械個体の実測値なので、前の機械の値を持ち込まない。作る軸だけでなく
    全軸を0にする（他軸に値が残ったまま出荷されるのを防ぐ）。
    """

    RAW = ("%\nN01825Q1A1P3000A2P3000\n"
           "N01850Q1A1P5420A2P9400\n"
           "N01851Q1A1P4A2P0\n"
           "N01852Q1A1P4A2P0\n%\n")

    def test_finds_non_zero_only(self):
        rows = B.individual_zero_rows(self.RAW)
        self.assertEqual(rows, [("1850", "A1", "5420"), ("1850", "A2", "9400"),
                                ("1851", "A1", "4"), ("1852", "A1", "4")])

    def test_zeroes_all_axes(self):
        new, rows = B.zero_individual(self.RAW)
        self.assertEqual(len(rows), 4)
        self.assertIn("N01850Q1A1P0A2P0", new)
        self.assertIn("N01851Q1A1P0A2P0", new)

    def test_other_lines_untouched(self):
        new, _ = B.zero_individual(self.RAW)
        self.assertIn("N01825Q1A1P3000A2P3000", new)
        a, b = self.RAW.split("\n"), new.split("\n")
        self.assertEqual(len(a), len(b))
        self.assertEqual(sum(1 for x, y in zip(a, b) if x != y), 3)

    def test_idempotent(self):
        once, _ = B.zero_individual(self.RAW)
        twice, rows = B.zero_individual(once)
        self.assertEqual(rows, [])
        self.assertEqual(once, twice)

    def test_clean_file_unchanged(self):
        clean = "%\nN01850Q1A1P0A2P0\n%\n"
        new, rows = B.zero_individual(clean)
        self.assertEqual(rows, [])
        self.assertEqual(new, clean)

    def test_params_configurable(self):
        rows = B.individual_zero_rows(self.RAW, params=("1851",))
        self.assertEqual(rows, [("1851", "A1", "4")])

    def test_build_text_applies_it(self):
        new, missing, fmt = B.build_text(self.RAW, {"01825": "2500"}, 1, "",
                                         B.ZERO_INDIVIDUAL_PARAMS)
        self.assertEqual(F_PRM.get_value(new, "1825", "A1"), "2500")
        self.assertEqual(F_PRM.get_value(new, "1850", "A1"), "0")
        self.assertEqual(F_PRM.get_value(new, "1850", "A2"), "0")

    def test_build_text_skips_when_off(self):
        new, _m, _f = B.build_text(self.RAW, {"01825": "2500"}, 1, "", None)
        self.assertEqual(F_PRM.get_value(new, "1850", "A1"), "5420")

    def test_preview_rows(self):
        rows = B.preview_zero_rows(self.RAW)
        self.assertIn(("1850(A1)", "5420", "0"), rows)
        self.assertIn(("1851(A1)", "4", "0"), rows)

    def test_headercsv_is_not_touched(self):
        csv_like = 'Seiban,50013078\r\n"1850","5420",""\r\n'
        self.assertEqual(B.individual_zero_rows(csv_like), [])

    def test_old_format_supported(self):
        # 旧書式（Q1なし・空白区切り）。looks_like_fanuc_prm は3行以上で判定するので
        # 実ファイルと同じく複数行の見本を使う
        old = ("%\nN01825 A1 P 3000\nN01850 A1 P 5420 A2 P 9400\n"
               "N01851 A1 P 4\nN01852 A1 P 0\n%\n")
        new, rows = B.zero_individual(old)
        self.assertEqual(rows, [("1850", "A1", "5420"), ("1850", "A2", "9400"),
                                ("1851", "A1", "4")])
        self.assertIn("N01850 A1 P 0 A2 P 0", new)   # 空白の形はそのまま
        self.assertIn("N01825 A1 P 3000", new)

    def test_product_value_wins_over_zeroing(self):
        # 製品データがグリッドシフトを指定していたら、0化で潰さない
        new, _m, _f = B.build_text(self.RAW, {"01850": "1234"}, 1,
                                   "", B.ZERO_INDIVIDUAL_PARAMS)
        self.assertEqual(F_PRM.get_value(new, "1850", "A1"), "1234")
        self.assertEqual(F_PRM.get_value(new, "1850", "A2"), "0")   # 指定外の軸は0

    def test_multi_axis_zeroing_also_lets_product_win(self):
        new, _m, _f = B.build_text_multi(
            self.RAW, {1: {"01850": "1234"}}, None, "", B.ZERO_INDIVIDUAL_PARAMS)
        self.assertEqual(F_PRM.get_value(new, "1850", "A1"), "1234")
        self.assertEqual(F_PRM.get_value(new, "1850", "A2"), "0")


class TestDropNumbersRobustness(unittest.TestCase):
    """番号リストに変な要素が混ざっても落ちない（呼び側のリストをそのまま渡せる）"""

    TEXT = "%\nN01825Q1A1P3000\nN09999Q1L1P0\n%\n"

    def test_ignores_unreadable_entries(self):
        kept, removed = F_PRM.drop_numbers(self.TEXT, [None, "", "あ", 9999, "N01825"])
        self.assertEqual(sorted(removed), [1825, 9999])

    def test_accepts_string_and_prefixed_numbers(self):
        _kept, removed = F_PRM.drop_numbers(self.TEXT, ["09999"])
        self.assertEqual(removed, [9999])

    def test_empty_list(self):
        kept, removed = F_PRM.drop_numbers(self.TEXT, [])
        self.assertEqual(removed, [])
        self.assertEqual(kept, self.TEXT)   # 何も指定しなければ中身は変わらない

    def test_none_list(self):
        _kept, removed = F_PRM.drop_numbers(self.TEXT, None)
        self.assertEqual(removed, [])
