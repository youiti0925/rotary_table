# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

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
