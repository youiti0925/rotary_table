# -*- coding: utf-8 -*-
"""FOCAS の自動接続: 機械を LAN の中から探す・台帳（CNC ID → 号機）。

LAN には出ない（探す部分は「窓口が開いているか」を偽物に差し替える）。
画面の試験は偽物のヘルパー（tests/fake_focas_helper.py）を使う。
"""
import os
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from nd287_app import focas_find as F

CID = "3C7B5D01-49C1F435-5E28C389-6592C486"


class TestCandidates(unittest.TestCase):
    def test_order_first_then_dot1_then_rest(self):
        hosts = F.candidate_hosts(["10.0.0.5"], local_ips=["192.168.1.2"])
        self.assertEqual(hosts[:3], ["10.0.0.5", "192.168.1.1", "192.168.1.3"])
        self.assertNotIn("192.168.1.2", hosts)          # 自分は探さない
        self.assertEqual(len(hosts), 1 + 253)

    def test_no_duplicates(self):
        hosts = F.candidate_hosts(["192.168.1.1", "192.168.1.1"], local_ips=["192.168.1.2"])
        self.assertEqual(hosts.count("192.168.1.1"), 1)

    def test_no_lan(self):
        self.assertEqual(F.candidate_hosts(["192.168.1.1"], local_ips=[]), ["192.168.1.1"])

    def test_local_ips_skip_loopback_and_unconnected(self):
        fake = [(2, 1, 6, "", ("127.0.0.1", 0)), (2, 1, 6, "", ("169.254.3.4", 0)),
                (2, 1, 6, "", ("192.168.1.2", 0))]
        with mock.patch.object(F.socket, "getaddrinfo", return_value=fake):
            self.assertEqual(F.local_ipv4s(), ["192.168.1.2"])


class TestFind(unittest.TestCase):
    def test_first_only_stops_at_known_host(self):
        seen = []

        def probe(h, port, timeout):
            seen.append(h)
            return h == "192.168.1.1"
        hosts = F.candidate_hosts(["192.168.1.1"], local_ips=["192.168.1.2"])
        self.assertEqual(F.find_machines(hosts, probe=probe, first_only=True), ["192.168.1.1"])
        self.assertEqual(seen, ["192.168.1.1"])          # 1件目で見つかれば LAN 全体は探さない

    def test_scan_keeps_order(self):
        open_ = {"192.168.1.50", "192.168.1.7"}
        hosts = F.candidate_hosts(["10.9.9.9"], local_ips=["192.168.1.2"])
        found = F.find_machines(hosts, probe=lambda h, p, t: h in open_)
        self.assertEqual(found, ["192.168.1.7", "192.168.1.50"])
        self.assertEqual(F.find_machines(hosts, probe=lambda h, p, t: h in open_, first_only=True),
                         ["192.168.1.7"])

    def test_nothing(self):
        self.assertEqual(F.find_machines([], probe=lambda *a: True), [])
        self.assertEqual(F.find_machines(["1.2.3.4"], probe=lambda *a: False, first_only=True), [])

    def test_port_open_on_closed_port(self):
        self.assertFalse(F.port_open("127.0.0.1", 1, timeout=0.2))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "マスタ" / "FOCAS機械台帳.csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty(self):
        self.assertEqual(F.load_registry(self.path), {})
        self.assertEqual(F.unit_for(self.path, CID), "")
        self.assertEqual(F.known_ips(self.path), [])

    def test_ip_only_does_not_set_unit(self):
        F.remember(self.path, CID, ip="192.168.1.1", now="2026-11-23 10:00:00")
        self.assertEqual(F.unit_for(self.path, CID), "")   # 号機は人が選ぶまで空
        self.assertEqual(F.known_ips(self.path), ["192.168.1.1"])

    def test_unit_kept_when_only_ip_changes(self):
        F.remember(self.path, CID, ip="192.168.1.1", unit="23", now="2026-11-23 10:00:00")
        F.remember(self.path, CID, ip="192.168.1.9", now="2026-11-23 11:00:00")
        self.assertEqual(F.unit_for(self.path, CID), "23")
        self.assertEqual(F.unit_for_ip(self.path, "192.168.1.9"), "23")

    def test_newest_ip_first(self):
        F.remember(self.path, "A", ip="192.168.1.1", now="2026-11-01 00:00:00")
        F.remember(self.path, "B", ip="192.168.2.1", now="2026-11-20 00:00:00")
        self.assertEqual(F.known_ips(self.path), ["192.168.2.1", "192.168.1.1"])

    def test_no_cnc_id_is_not_recorded(self):
        self.assertIsNone(F.remember(self.path, None, ip="192.168.1.1", unit="23"))
        self.assertFalse(self.path.exists())

    def test_readable_as_excel_csv(self):
        F.remember(self.path, CID, ip="192.168.1.1", unit="23")
        head = self.path.read_bytes()[:3]
        self.assertEqual(head, b"\xef\xbb\xbf")             # Excel で文字化けしない（BOM 付き）

    def test_relative_path_is_under_app_dir(self):
        p = F.registry_path({"focas_registry_csv": r"マスタ/FOCAS機械台帳.csv"}, app_dir=self.tmp)
        self.assertEqual(p, Path(self.tmp) / "マスタ" / "FOCAS機械台帳.csv")


class TestPickHost(unittest.TestCase):
    def test_not_auto_keeps_host(self):
        import nd287_app.gui as G
        with mock.patch.object(F, "port_open", side_effect=AssertionError("探してはいけない")):
            self.assertEqual(G._focas_pick_host({}, "1.2.3.4", 8193, lambda *a: None, auto=False),
                             "1.2.3.4")

    def test_auto_find_off_keeps_host(self):
        import nd287_app.gui as G
        with mock.patch.object(F, "port_open", side_effect=AssertionError("探してはいけない")):
            self.assertEqual(G._focas_pick_host({"focas_auto_find": False}, "1.2.3.4", 8193,
                                                lambda *a: None, auto=True), "1.2.3.4")

    def test_auto_finds_another_host(self):
        import nd287_app.gui as G
        tmp = tempfile.mkdtemp()
        try:
            st = {"focas_registry_csv": str(Path(tmp) / "台帳.csv")}
            with mock.patch.object(F, "port_open", side_effect=lambda h, p, timeout=0.3: h == "192.168.1.1"), \
                    mock.patch.object(F, "local_ipv4s", return_value=["192.168.1.2"]):
                self.assertEqual(G._focas_pick_host(st, "192.168.0.10", 8193, lambda *a: None, auto=True),
                                 "192.168.1.1")
                with mock.patch.object(F, "port_open", return_value=False):
                    from nd287_app import focas_client
                    with self.assertRaises(focas_client.FocasError) as cm:
                        G._focas_pick_host(st, "192.168.0.10", 8193, lambda *a: None, auto=True)
                    self.assertEqual(cm.exception.kind, "connect")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


try:
    from test_focas_dialog import DialogBase
except ImportError:                     # unittest discover の外から読まれたとき
    from tests.test_focas_dialog import DialogBase


class TestDialogAuto(DialogBase):
    """画面: 開いたら自動でつなぐ・台帳の号機を選んでおく・人が選んだ号機を台帳に残す。"""

    def _auto_dialog(self, **kw):
        import nd287_app.gui as G
        self.reg = Path(self.work) / "台帳.csv"
        self.settings.update(focas_auto_find=False, focas_registry_csv=str(self.reg), **kw)
        d = G.FocasDialog(None, self.settings, auto=True)
        d._timer.setInterval(600000)
        d.warnings = []
        d._warn = lambda title, text: d.warnings.append((title, text))
        d._info = lambda title, text: d.warnings.append((title, text))
        d.show()
        self.dialogs.append(d)
        for _ in range(5):
            self.app.processEvents()
        self.wait(d)
        return d

    def test_opens_and_connects_by_itself(self):
        d = self._auto_dialog()
        self.assertTrue(d._connected)
        self.assertEqual(F.load_registry(self.reg)[CID]["ip"], "127.0.0.1")
        self.assertEqual(F.unit_for(self.reg, CID), "")       # 号機は人が選ぶまで決めない
        self.assertEqual(d.cmb_unit.currentData(), "")

    def test_auto_connect_can_be_turned_off(self):
        d = self._auto_dialog(focas_auto_connect=False)
        self.assertFalse(d._connected)

    def test_chosen_unit_is_remembered_and_used_next_time(self):
        d = self._auto_dialog()
        i = d.cmb_unit.findData("23")
        self.assertGreaterEqual(i, 0)
        d.cmb_unit.setCurrentIndex(i)
        d.cmb_unit.activated.emit(i)                           # 人が選んだ
        self.assertEqual(F.unit_for(self.reg, CID), "23")
        d.close()
        d2 = self._auto_dialog()
        self.assertEqual(d2.cmb_unit.currentData(), "23")
        self.assertIn("台帳", d2.lbl_unit.text())

    def test_not_auto_does_not_touch_registry(self):
        self.reg = Path(self.work) / "台帳.csv"
        self.settings.update(focas_registry_csv=str(self.reg))
        d = self.dialog()
        self.connect(d)
        self.assertTrue(d._connected)
        self.assertFalse(self.reg.exists())


if __name__ == "__main__":
    unittest.main()
