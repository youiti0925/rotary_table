# -*- coding: utf-8 -*-
import unittest

from nd287_app.nd287 import ND287Device, find_nd287_port, list_serial_ports, probe_port


class TestAutoDetect(unittest.TestCase):
    def test_list_serial_ports_returns_list(self):
        self.assertIsInstance(list_serial_ports(), list)

    def test_probe_nonexistent_port_is_false(self):
        self.assertFalse(probe_port("/dev/ttyNOSUCHPORT99"))

    def test_find_with_no_ports_returns_none(self):
        self.assertIsNone(find_nd287_port(ports=[]))

    def test_find_skips_dead_ports(self):
        self.assertIsNone(find_nd287_port(ports=["/dev/ttyNOSUCHPORT98", "/dev/ttyNOSUCHPORT99"]))

    def test_auto_flag(self):
        self.assertTrue(ND287Device()._auto)
        self.assertTrue(ND287Device("auto")._auto)
        self.assertFalse(ND287Device("COM3")._auto)

    def test_open_auto_without_device_raises(self):
        dev = ND287Device()
        with self.assertRaises(RuntimeError):
            dev.open()


if __name__ == "__main__":
    unittest.main()
