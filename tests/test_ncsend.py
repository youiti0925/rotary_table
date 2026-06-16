# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from nd287_app import ncsend


class TestNcBytes(unittest.TestCase):
    def test_crlf_normalized(self):
        # LF も CR も CRLF に統一される（FANUC前提）
        self.assertEqual(ncsend.nc_bytes("A\nB\r\nC\rD"),
                         b"A\r\nB\r\nC\r\nD")

    def test_non_ascii_replaced(self):
        # 日本語コメントが混じっても落ちず "?" に置換される
        out = ncsend.nc_bytes("(コメント)\nM30")
        self.assertTrue(out.endswith(b"M30"))
        self.assertNotIn("コ".encode("utf-8"), out)


class TestFilename(unittest.TestCase):
    def test_machine_name(self):
        self.assertEqual(ncsend.default_filename("261942"), "261942.NC")

    def test_fallback_to_o_number(self):
        self.assertEqual(ncsend.default_filename("", main_number=100), "O0100.NC")

    def test_fallback_default(self):
        self.assertEqual(ncsend.default_filename("", None), "program.NC")

    def test_path_separators_sanitized(self):
        # 機番に / や \\ が混ざってもサブフォルダ扱いにならない
        self.assertEqual(ncsend.default_filename("AB/CD"), "AB_CD.NC")
        self.assertEqual(ncsend.default_filename("AB\\CD"), "AB_CD.NC")

    def test_path_traversal_neutralized(self):
        name = ncsend.default_filename("../../etc/passwd")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertTrue(name.endswith(".NC"))

    def test_dotdot_falls_back(self):
        # ".." はサニタイズで空になり O番号 へフォールバック（..NC を作らない）
        self.assertEqual(ncsend.default_filename("..", main_number=100), "O0100.NC")


class TestSendToFolder(unittest.TestCase):
    def test_writes_file_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            path = ncsend.send_to_folder("%\nO0100\nM30\n%", d, "261942.NC")
            self.assertTrue(Path(path).exists())
            data = Path(path).read_bytes()
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"\n\n", data.replace(b"\r\n", b""))  # 生のLF残らない

    def test_missing_folder_raises_clear_error(self):
        missing = str(Path(tempfile.gettempdir()) / "no_such_dir_xyz_123")
        with self.assertRaises(FileNotFoundError):
            ncsend.send_to_folder("x", missing, "a.NC")

    def test_empty_folder_raises(self):
        with self.assertRaises(ValueError):
            ncsend.send_to_folder("x", "", "a.NC")


class TestSendDispatch(unittest.TestCase):
    def test_folder_method_routes_to_folder(self):
        with tempfile.TemporaryDirectory() as d:
            settings = dict(nc_send_method="folder", nc_send_folder=d)
            dest = ncsend.send("%\nM30\n%", settings, machine="261942")
            self.assertTrue(dest.endswith("261942.NC"))
            self.assertTrue(Path(dest).exists())

    def test_ftp_method_without_host_raises(self):
        settings = dict(nc_send_method="ftp", nc_ftp_host="")
        with self.assertRaises(ValueError):
            ncsend.send("x", settings, machine="261942")


if __name__ == "__main__":
    unittest.main()
