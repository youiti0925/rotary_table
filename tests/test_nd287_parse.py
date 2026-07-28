# -*- coding: utf-8 -*-
import unittest

from nd287_app.nd287 import parse_angle, deg_to_dms, extract_angles, ACK


class TestParseAngle(unittest.TestCase):
    def test_decimal_degrees(self):
        self.assertAlmostEqual(parse_angle(b"123.4567\r\n"), 123.4567)

    def test_negative_decimal(self):
        self.assertAlmostEqual(parse_angle(b"-0.0012\r\n"), -0.0012)

    def test_dms_with_symbols(self):
        # 123°45'06.7" = 123 + 45/60 + 6.7/3600（度記号は0xB0のバイトで届く想定）
        v = parse_angle("123°45'06.7\"\r\n".encode("latin-1"))
        self.assertAlmostEqual(v, 123 + 45 / 60 + 6.7 / 3600, places=9)

    def test_dms_negative(self):
        v = parse_angle(b"-10 30 00.0\r\n")
        self.assertAlmostEqual(v, -10.5, places=9)

    def test_three_blocks_without_symbols(self):
        v = parse_angle(b"45 00 36.0\r\n")
        self.assertAlmostEqual(v, 45.01, places=9)

    def test_ack_is_stripped(self):
        self.assertAlmostEqual(parse_angle(ACK + b"90.0\r\n"), 90.0)

    def test_garbage_with_unit(self):
        self.assertAlmostEqual(parse_angle(b"  +12.5 deg \r\n"), 12.5)

    def test_empty_and_no_number(self):
        self.assertIsNone(parse_angle(b""))
        self.assertIsNone(parse_angle(b"\r\n"))
        self.assertIsNone(parse_angle(b"ERROR\r\n"))

    def test_seven_bit_even_parity_contamination(self):
        # 実機報告: 8ビットで受けると7ビット偶数パリティがbit7に紛れ、'4'(0x34)が
        # 0xB4 になって先頭桁が落ち 0.000000 と誤読された。bit7を外して復元する。
        raw = bytes([0x2B, 0xA0, 0xA0, 0xB4, 0x30, 0x2E, 0x30, 0x30, 0x2E,
                     0x30, 0x30, 0x2E, 0x30, 0xA0, 0x4D, 0xA0, 0xA0, 0x8D])
        self.assertAlmostEqual(parse_angle(raw), 40.0, places=6)  # 0ではなく40.00.00.0

    def test_parity_contaminated_ten_degrees(self):
        # 「+ 10.00.00.0」を7ビット偶数パリティで送った場合（'1'=0x31→0xB1 も復元）
        clean = b"+ 10.00.00.0 M\r"
        raw = bytes(b | 0x80 if bin(b).count("1") % 2 else b for b in clean)
        self.assertAlmostEqual(parse_angle(raw), 10.0, places=6)

    def test_dotted_dms_seconds_not_dropped(self):
        # ドット区切り DD.MM.SS を十進度と誤読して秒を落とさない
        self.assertAlmostEqual(parse_angle(b"40.00.05\r\n"), 40 + 5 / 3600, places=9)

    def test_dotted_dms_with_tenths(self):
        # DD.MM.SS.t（秒の小数）も読む: 50°00'10.0" = 50.002778°
        self.assertAlmostEqual(parse_angle(b"50.00.10.0\r\n"),
                               50 + 10 / 3600, places=9)

    def test_real_50deg_sample_with_parity(self):
        # 実機の生バイト（7bit偶数パリティ混入）。'5' はパリティ0で0x35のまま、'1'は
        # 0x31→0xB1 に化ける。両修正で 50°00'10.0" = 50.0028° に復元される。
        raw = bytes([0x2B, 0xA0, 0xA0, 0x35, 0x30, 0x2E, 0x30, 0x30, 0x2E, 0xB1,
                     0x30, 0x2E, 0x30, 0xA0, 0x4D, 0xA0, 0xA0, 0x8D])
        self.assertAlmostEqual(parse_angle(raw), 50 + 10 / 3600, places=6)

    def test_dotted_dms_negative_detached_sign(self):
        # 符号が数字から離れて出る負値（"-  40.00.05"）も負として読む
        self.assertAlmostEqual(parse_angle(b"-  40.00.05\r\n"),
                               -(40 + 5 / 3600), places=9)

    def test_degree_symbol_preserved_not_masked(self):
        # 正常な8ビットの度記号 '°'(0xB0) はマスクで '0' に化けさせない
        v = parse_angle("12°30'00.0\"\r\n".encode("latin-1"))
        self.assertAlmostEqual(v, 12.5, places=9)

    def test_clean_ascii_unchanged_by_mask(self):
        # 正常時（bit7が立たない）は変換が無害＝従来どおり
        self.assertAlmostEqual(parse_angle(b"123.4567\r\n"), 123.4567)
        self.assertAlmostEqual(parse_angle(b"-10 30 00.0\r\n"), -10.5, places=9)


class TestExtractAngles(unittest.TestCase):
    def test_multiple_complete_lines(self):
        angles, rest = extract_angles(b"10.0\r\n20.0\r\n")
        self.assertEqual(angles, [10.0, 20.0])
        self.assertEqual(rest, b"")

    def test_incomplete_tail_kept_in_buffer(self):
        angles, rest = extract_angles(b"10.0\r\n20.")
        self.assertEqual(angles, [10.0])
        self.assertEqual(rest, b"20.")

    def test_garbage_lines_skipped(self):
        angles, rest = extract_angles(b"\r\nERR\r\n30.5\r\n")
        self.assertEqual(angles, [30.5])
        self.assertEqual(rest, b"")

    def test_lf_only_terminator(self):
        angles, rest = extract_angles(b"45.0\n")
        self.assertEqual(angles, [45.0])


class TestDegToDms(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(deg_to_dms(123.7518611), "+123°45'06.7\"")

    def test_negative(self):
        self.assertEqual(deg_to_dms(-10.5), "-10°30'00.0\"")

    def test_zero(self):
        self.assertEqual(deg_to_dms(0.0), "+0°00'00.0\"")

    def test_rounding_does_not_show_60_seconds(self):
        # 59.99" は繰り上がって 0.0" + 1分 になる
        s = deg_to_dms(10 + 59.99 / 3600)
        self.assertNotIn("60.0", s)
        self.assertEqual(s, "+10°01'00.0\"")


if __name__ == "__main__":
    unittest.main()
