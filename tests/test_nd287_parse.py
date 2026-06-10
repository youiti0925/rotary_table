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
