# -*- coding: utf-8 -*-
"""ファイルの出どころ判定（実機が出したもの / PCで作られたもの）"""
import tempfile
import unittest
from pathlib import Path

from nd287_app.param_origin import classify, file_kind, scan_folder, summarize

# 実機のパンチ形式（EOB = LF CR CR）
MACHINE_PARAM = b"%\n\r\rN00000Q1L1P00000010L2P00000000 \n\r\rN01825Q1A1P3000\n\r\r%\n\r\r"
MACHINE_PROG = b"%\n\r\rO0001\n\r\rG91G00Z10.\n\r\rG04X1.\n\r\rM30\n\r\r%\n\r\r"
# PCで作られたもの
PC_PARAM_LF = b"%\nN00000Q1L1P00000010L2P00000000 \nN01825Q1A1P3000\n%\n"
PC_PARAM_CRLF = b"%\r\nN00000Q1L1P00000010\r\nN01825Q1A1P3000\r\n%\r\n"
PC_PROG = ("%\nO0100 (MEASURE ????)\n(--- reset (set 0) ---)\n"
           "G91 G00 X10. ;\nM30 ;\n%\n").encode("ascii")


class TestClassify(unittest.TestCase):
    def test_machine_parameter_file(self):
        info = classify(MACHINE_PARAM)
        self.assertEqual(info["verdict"], "machine")
        self.assertEqual(info["eob"], "LF CR CR")
        self.assertEqual(info["kind"], "パラメータ")

    def test_machine_program_file(self):
        info = classify(MACHINE_PROG)
        self.assertEqual(info["verdict"], "machine")
        self.assertEqual(info["kind"], "プログラム")

    def test_pc_lf_only(self):
        info = classify(PC_PARAM_LF)
        self.assertEqual(info["verdict"], "pc")
        self.assertEqual(info["eob"], "LF")

    def test_pc_crlf(self):
        info = classify(PC_PARAM_CRLF)
        self.assertEqual(info["verdict"], "pc")
        self.assertEqual(info["eob"], "CRLF")

    def test_pc_program_with_semicolons(self):
        info = classify(PC_PROG)
        self.assertEqual(info["verdict"], "pc")
        self.assertTrue(any('";"' in r for r in info["reasons"]))

    def test_extension_is_not_used(self):
        # 拡張子は判定に使わない（.PRM でも実機のものがあり得る）
        self.assertEqual(classify(MACHINE_PARAM)["verdict"], "machine")
        self.assertEqual(classify(PC_PARAM_LF)["verdict"], "pc")

    def test_non_ascii_counts_against_machine(self):
        info = classify("%\n\r\rN01825Q1A1P3000\n\r\r(コメント)\n\r\r%\n\r\r".encode("utf-8"))
        self.assertTrue(any("ASCII外" in r for r in info["reasons"]))

    def test_empty(self):
        self.assertEqual(classify(b"")["verdict"], "unknown")
        self.assertEqual(classify(None)["verdict"], "unknown")

    def test_reasons_are_never_empty(self):
        for data in (MACHINE_PARAM, PC_PARAM_LF, PC_PROG):
            self.assertTrue(classify(data)["reasons"])


class TestFileKind(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(file_kind(MACHINE_PARAM), "パラメータ")
        self.assertEqual(file_kind(MACHINE_PROG), "プログラム")
        self.assertEqual(file_kind(b"hello world"), "不明")


class TestScanFolder(unittest.TestCase):
    def test_scans_and_summarizes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "F23BASIC.DAT").write_bytes(MACHINE_PARAM)
            (Path(d) / "F23BASIC.prm").write_bytes(PC_PARAM_LF)
            (Path(d) / "F35BASIC").write_bytes(MACHINE_PARAM)   # 拡張子なしも見る
            rows = scan_folder(d)
        self.assertEqual([n for n, _i, _p in rows],
                         ["F23BASIC.DAT", "F23BASIC.prm", "F35BASIC"])
        verdicts = {n: i["verdict"] for n, i, _p in rows}
        self.assertEqual(verdicts["F23BASIC.DAT"], "machine")
        self.assertEqual(verdicts["F23BASIC.prm"], "pc")
        self.assertEqual(verdicts["F35BASIC"], "machine")
        self.assertIn("実機 2", summarize(rows))

    def test_missing_folder(self):
        self.assertEqual(scan_folder("/no/such/dir"), [])
        self.assertEqual(scan_folder(""), [])

    def test_ext_filter(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.DAT").write_bytes(MACHINE_PARAM)
            (Path(d) / "readme.txt").write_bytes(b"hello")
            rows = scan_folder(d, exts=(".dat",))
        self.assertEqual([n for n, _i, _p in rows], ["a.DAT"])


if __name__ == "__main__":
    unittest.main()


# 実機のEOBをWindowsのテキストモードで写すと、LFとCRがそれぞれCRLFに膨らむ
CONVERTED = MACHINE_PARAM.replace(b"\n\r\r", b"\r\n\r\n\r\n")
# 旧書式（Q1なし・空白区切り）。F10〜F17 の8台がこの形
OLD_MACHINE = b"%\n\r\rN00000 P 00000010\n\r\rN01825 A1 P 3000 A4 P 3000\n\r\r%\n\r\r"


class TestConvertedAndOldFormat(unittest.TestCase):
    def test_converted_is_recognized_as_machine_origin(self):
        info = classify(CONVERTED)
        self.assertEqual(info["verdict"], "converted")
        self.assertEqual(info["eob"], "CRLF×3")
        self.assertTrue(any("Windows" in r for r in info["reasons"]))

    def test_converted_is_not_confused_with_plain_crlf(self):
        self.assertEqual(classify(PC_PARAM_CRLF)["verdict"], "pc")

    def test_old_format_is_a_parameter_file(self):
        info = classify(OLD_MACHINE)
        self.assertEqual(info["kind"], "パラメータ")
        self.assertEqual(info["verdict"], "machine")

    def test_summary_counts_converted(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "F17BASIC.DAT").write_bytes(CONVERTED)
            (Path(d) / "F23BASIC.DAT").write_bytes(MACHINE_PARAM)
            rows = scan_folder(d)
        self.assertIn("実機(改行変換済) 1", summarize(rows))
