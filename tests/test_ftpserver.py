# -*- coding: utf-8 -*-
"""アプリ内FTPサーバーの試験。

本物の ftplib（標準ライブラリのFTPクライアント）で実際に接続して確かめる。
一番大事なのは「中身を1バイトも変えずに渡すこと」。測定プログラムと
パラメータの区切りは LF CR CR で、普通のFTPサーバーのようにASCIIモードで
改行を直すと制御装置が読めないファイルになる。
"""

import ftplib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from nd287_app import ftpserver as F

# 実機と同じ区切り（LF CR CR）。ここが化けたら制御装置は読めない
MACHINE_BYTES = b"%\n\r\rO1000(TEST)\n\r\rG90\n\r\r%\n\r\r"


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.srv = F.FtpServer(self.dir, host="127.0.0.1", port=0,
                               user="cnc", password="cnc")
        self.srv.start()
        self.ftp = ftplib.FTP()
        self.ftp.connect("127.0.0.1", self.srv.port, timeout=10)
        self.ftp.login("cnc", "cnc")

    def tearDown(self):
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestTransfer(_Base):
    def test_downloads_bytes_exactly(self):
        (self.dir / "T50014175.DAT").write_bytes(MACHINE_BYTES)
        got = io.BytesIO()
        self.ftp.retrbinary("RETR T50014175.DAT", got.write)
        self.assertEqual(got.getvalue(), MACHINE_BYTES)

    def test_ascii_mode_does_not_touch_line_ends(self):
        """TYPE A でも改行を書き換えない（LF CR CR を守る）。"""
        (self.dir / "O1000.NC").write_bytes(MACHINE_BYTES)
        self.ftp.voidcmd("TYPE A")
        got = io.BytesIO()
        self.ftp.retrbinary("RETR O1000.NC", got.write)
        self.assertEqual(got.getvalue(), MACHINE_BYTES)

    def test_upload_keeps_bytes(self):
        """機械からPCへ書き戻す（バックアップ）ときも中身はそのまま。"""
        self.ftp.storbinary("STOR F23BASIC.DAT", io.BytesIO(MACHINE_BYTES))
        self.assertEqual((self.dir / "F23BASIC.DAT").read_bytes(), MACHINE_BYTES)

    def test_size_matches(self):
        (self.dir / "a.NC").write_bytes(MACHINE_BYTES)
        self.assertEqual(self.ftp.size("a.NC"), len(MACHINE_BYTES))

    def test_delete_and_rename(self):
        (self.dir / "old.NC").write_bytes(b"x")
        self.ftp.rename("old.NC", "new.NC")
        self.assertTrue((self.dir / "new.NC").exists())
        self.ftp.delete("new.NC")
        self.assertFalse((self.dir / "new.NC").exists())


class TestListing(_Base):
    def test_nlst_shows_files(self):
        for n in ("T1.DAT", "R2.NC"):
            (self.dir / n).write_bytes(b"x")
        self.assertEqual(sorted(self.ftp.nlst()), ["R2.NC", "T1.DAT"])

    def test_list_unix_style(self):
        (self.dir / "T1.DAT").write_bytes(b"12345")
        lines = []
        self.ftp.retrlines("LIST", lines.append)
        self.assertTrue(any(l.startswith("-rw-") and "T1.DAT" in l for l in lines))
        self.assertTrue(any(" 5 " in l for l in lines))

    def test_list_dos_style(self):
        self.srv.list_style = "dos"
        (self.dir / "T1.DAT").write_bytes(b"12345")
        lines = []
        self.ftp.retrlines("LIST", lines.append)
        self.assertTrue(any("T1.DAT" in l and "-" in l for l in lines))

    def test_list_ignores_dash_l(self):
        (self.dir / "T1.DAT").write_bytes(b"x")
        lines = []
        self.ftp.retrlines("LIST -l", lines.append)     # 一部の機器は -l を付ける
        self.assertTrue(any("T1.DAT" in l for l in lines))

    def test_subfolder_navigation(self):
        (self.dir / "RTT135").mkdir()
        (self.dir / "RTT135" / "T1.DAT").write_bytes(b"x")
        self.ftp.cwd("RTT135")
        self.assertEqual(self.ftp.pwd(), "/RTT135")
        self.assertEqual(self.ftp.nlst(), ["T1.DAT"])
        self.ftp.cwd("..")
        self.assertEqual(self.ftp.pwd(), "/")


class TestSafety(_Base):
    def test_cannot_escape_the_shared_folder(self):
        """.. をいくつ並べても公開フォルダの外は見せない。"""
        outside = self.dir.parent / "himitsu.txt"
        outside.write_bytes(b"secret")
        try:
            self.ftp.cwd("../../..")
            self.assertEqual(self.ftp.pwd(), "/")       # / から動かない
            self.assertNotIn("himitsu.txt", self.ftp.nlst())
            with self.assertRaises(ftplib.error_perm):
                self.ftp.size("../himitsu.txt")
        finally:
            outside.unlink(missing_ok=True)

    def test_absolute_path_is_relative_to_the_shared_folder(self):
        (self.dir / "T1.DAT").write_bytes(b"x")
        self.assertEqual(self.ftp.size("/T1.DAT"), 1)


class TestLogin(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_wrong_password_is_refused(self):
        srv = F.FtpServer(self.dir, host="127.0.0.1", port=0,
                          user="cnc", password="cnc")
        srv.start()
        try:
            ftp = ftplib.FTP()
            ftp.connect("127.0.0.1", srv.port, timeout=10)
            with self.assertRaises(ftplib.error_perm):
                ftp.login("cnc", "chigau")
            ftp.close()
        finally:
            srv.stop()

    def test_no_password_means_anyone_can_read(self):
        (self.dir / "T1.DAT").write_bytes(b"x")
        srv = F.FtpServer(self.dir, host="127.0.0.1", port=0, user="", password="")
        srv.start()
        try:
            ftp = ftplib.FTP()
            ftp.connect("127.0.0.1", srv.port, timeout=10)
            ftp.login()                       # anonymous
            self.assertEqual(ftp.nlst(), ["T1.DAT"])
            ftp.close()
        finally:
            srv.stop()

    def test_missing_folder_is_reported(self):
        srv = F.FtpServer(self.dir / "ない", host="127.0.0.1", port=0)
        with self.assertRaises(OSError):
            srv.start()

    def test_start_stop(self):
        srv = F.FtpServer(self.dir, host="127.0.0.1", port=0)
        self.assertFalse(srv.running)
        srv.start()
        self.assertTrue(srv.running)
        srv.stop()
        self.assertFalse(srv.running)

    def test_cnc_settings_table(self):
        srv = F.FtpServer(self.dir, host="127.0.0.1", port=0,
                          user="cnc", password="himitsu")
        srv.start()
        try:
            rows = dict(srv.cnc_settings("192.168.1.10"))
            self.assertEqual(rows["ホスト名(IPアドレス)"], "192.168.1.10")
            self.assertEqual(rows["ポート番号"], str(srv.port))
            self.assertEqual(rows["ユーザー名"], "cnc")
        finally:
            srv.stop()


class TestActiveMode(_Base):
    """PORT（アクティブモード）でも渡せる。制御装置がこちらを使うことがある。"""

    def test_active_mode_download(self):
        (self.dir / "T1.DAT").write_bytes(MACHINE_BYTES)
        self.ftp.set_pasv(False)
        got = io.BytesIO()
        self.ftp.retrbinary("RETR T1.DAT", got.write)
        self.assertEqual(got.getvalue(), MACHINE_BYTES)


class TestReceivedCheck(_Base):
    """機械から受け取ったバックアップをその場で点検する。

    壊れたバックアップは取った直後に見ないと気づけない（実データの F35BASIC は
    途中の % で258行が読まれない状態のまま置かれていた）。
    """

    def setUp(self):
        super().setUp()
        self.seen = []
        self.srv._on_stored = self.seen.append

    def test_calls_the_check_after_receiving(self):
        self.ftp.storbinary("STOR F23BASIC.DAT", io.BytesIO(MACHINE_BYTES))
        self.assertEqual([p.name for p in self.seen], ["F23BASIC.DAT"])

    def test_a_broken_backup_is_reported(self):
        from nd287_app import param_build as B
        broken = ("%\n" + "\n".join(f"N{i:05d}Q1A1P0" for i in range(5))
                  + "\n%1P00000000\nN27124Q1A1P0\n%\n")   # 途中の % （F35と同じ形）
        self.ftp.storbinary("STOR F35BASIC.DAT",
                            io.BytesIO(broken.encode("cp932")))
        note, problems = B.check_received(self.dir / "F35BASIC.DAT")
        self.assertIn("F35BASIC.DAT", note)
        self.assertTrue(any("途中に %" in p for p in problems))

    def test_a_good_backup_has_no_complaint(self):
        from nd287_app import param_build as B
        good = "%\n" + "\n".join(f"N{i:05d}Q1A1P0" for i in range(5)) + "\n%\n"
        self.ftp.storbinary("STOR F23BASIC.DAT", io.BytesIO(good.encode("cp932")))
        note, problems = B.check_received(self.dir / "F23BASIC.DAT")
        self.assertEqual(problems, [])
        self.assertIn("番号 5個", note)

    def test_check_failure_does_not_break_the_transfer(self):
        def boom(_p):
            raise RuntimeError("わざと失敗")
        self.srv._on_stored = boom
        self.ftp.storbinary("STOR X.DAT", io.BytesIO(MACHINE_BYTES))
        self.assertEqual((self.dir / "X.DAT").read_bytes(), MACHINE_BYTES)
        self.assertTrue(any("点検できません" in l for l in self.srv.lines))

    def test_empty_and_missing_files(self):
        from nd287_app import param_build as B
        (self.dir / "empty.DAT").write_bytes(b"")
        self.assertEqual(B.check_received(self.dir / "empty.DAT")[1],
                         ["中身が空です"])
        note, problems = B.check_received(self.dir / "ない.DAT")
        self.assertEqual(note, "")
        self.assertTrue(problems)


class TestListLine(unittest.TestCase):
    def test_unix_line_has_name_and_size(self):
        d = Path(tempfile.mkdtemp())
        try:
            p = d / "T50014175.DAT"
            p.write_bytes(b"a" * 1234)
            line = F._list_line(p, "unix")
            self.assertIn("T50014175.DAT", line)
            self.assertIn("1234", line)
            self.assertTrue(line.startswith("-rw-"))
            dos = F._list_line(p, "dos")
            self.assertIn("T50014175.DAT", dos)
            self.assertIn("1234", dos)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_folder_is_marked(self):
        d = Path(tempfile.mkdtemp())
        try:
            (d / "sub").mkdir()
            self.assertTrue(F._list_line(d / "sub", "unix").startswith("d"))
            self.assertIn("<DIR>", F._list_line(d / "sub", "dos"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class TestSelfTest(_Base):
    """「開始を押したけど合っているのか分からない」を無くす自己診断。"""

    def test_reports_each_step_when_it_works(self):
        (self.dir / "T1.DAT").write_bytes(MACHINE_BYTES)
        ok, lines = F.self_test("127.0.0.1", self.srv.port, "cnc", "cnc",
                                root=self.dir)
        self.assertTrue(ok, lines)
        self.assertEqual(len(lines), 4)          # つながる/ログイン/一覧/1本読む
        self.assertIn("つながった", lines[0])
        self.assertIn("ログインできた", lines[1])
        self.assertIn("フォルダが見えた", lines[2])
        self.assertIn("1バイトも変わっていない", lines[3])

    def test_cleans_up_its_own_test_file(self):
        before = sorted(p.name for p in self.dir.iterdir())
        F.self_test("127.0.0.1", self.srv.port, "cnc", "cnc", root=self.dir)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), before)

    def test_wrong_password_stops_at_login(self):
        ok, lines = F.self_test("127.0.0.1", self.srv.port, "cnc", "chigau",
                                root=self.dir)
        self.assertFalse(ok)
        self.assertIn("つながった", lines[0])      # ①は通る
        self.assertTrue(lines[-1].startswith("★"))  # どこで止まったか分かる

    def test_nothing_listening_stops_at_the_first_step(self):
        ok, lines = F.self_test("127.0.0.1", 1, "cnc", "cnc", timeout=1.0)
        self.assertFalse(ok)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("★"))

    def test_works_without_a_folder_to_read(self):
        ok, lines = F.self_test("127.0.0.1", self.srv.port, "cnc", "cnc", root=None)
        self.assertTrue(ok, lines)
        self.assertEqual(len(lines), 3)          # 1本読む段は飛ばす


class TestUppercaseLogin(unittest.TestCase):
    """制御装置は小文字を送れない（FANUCのISOコードに小文字が無い）。

    画面で cnc と打っても回線には USER CNC と大文字で出てくる。実機のログで
    「← USER CNC → 530 Login incorrect」となり、ファイル一覧が出なかった。
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "T1.DAT").write_bytes(MACHINE_BYTES)
        self.srv = F.FtpServer(self.dir, host="127.0.0.1", port=0,
                               user="cnc", password="cnc")
        self.srv.start()

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _login(self, user, password):
        ftp = ftplib.FTP()
        ftp.connect("127.0.0.1", self.srv.port, timeout=10)
        try:
            ftp.login(user, password)
            return ftp.nlst()
        finally:
            try:
                ftp.close()
            except Exception:
                pass

    def test_uppercase_from_the_machine_is_accepted(self):
        self.assertEqual(self._login("CNC", "CNC"), ["T1.DAT"])

    def test_lowercase_still_works(self):
        self.assertEqual(self._login("cnc", "cnc"), ["T1.DAT"])

    def test_mixed_case_works(self):
        self.assertEqual(self._login("Cnc", "cNC"), ["T1.DAT"])

    def test_a_different_word_is_still_refused(self):
        with self.assertRaises(ftplib.error_perm):
            self._login("CNC", "CHIGAU")

    def test_counts_are_kept_for_the_screen(self):
        """画面が「つないできた」と「ログインできた」を別に出せるように。"""
        self._login("CNC", "CNC")
        self.assertEqual(self.srv.logged_in, 1)
        self.assertEqual(self.srv.login_ng, 0)
        with self.assertRaises(ftplib.error_perm):
            self._login("CNC", "CHIGAU")
        self.assertEqual(self.srv.logged_in, 1)
        self.assertEqual(self.srv.login_ng, 1)
        self.assertTrue(any("ログインを断りました" in l for l in self.srv.lines))
