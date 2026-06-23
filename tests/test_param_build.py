# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from nd287_app import param_build as B
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
        self.assertEqual(out.name, "T50013078.prm")
        self.assertEqual(out.read_bytes(),
                         product.encode("cp932"))  # CRLF含めバイト一致

    def test_filename(self):
        self.assertEqual(B.filename("R", "12345"), "R12345.prm")


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


if __name__ == "__main__":
    unittest.main()
