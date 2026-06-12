# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import unittest

from nd287_app.switchbot import fetch_temperature, make_headers


class TestSwitchBot(unittest.TestCase):
    def test_signature(self):
        # SwitchBot API v1.1 の署名仕様: HMAC-SHA256(secret, token+t+nonce) をBase64
        headers = make_headers("TOKEN", "SECRET", t="1700000000000", nonce="NONCE")
        expected = base64.b64encode(
            hmac.new(b"SECRET", b"TOKEN1700000000000NONCE", hashlib.sha256).digest()
        ).decode()
        self.assertEqual(headers["sign"], expected)
        self.assertEqual(headers["Authorization"], "TOKEN")
        self.assertEqual(headers["t"], "1700000000000")
        self.assertEqual(headers["nonce"], "NONCE")

    def test_auto_timestamp_and_nonce(self):
        headers = make_headers("TOKEN", "SECRET")
        self.assertTrue(headers["t"].isdigit())
        self.assertTrue(len(headers["nonce"]) > 10)

    def test_unconfigured_raises(self):
        with self.assertRaises(ValueError):
            fetch_temperature("", "", "")




class TestBotControl(unittest.TestCase):
    def test_ble_payload_without_password(self):
        from nd287_app.switchbot import ble_press_payload
        self.assertEqual(ble_press_payload(""), bytes([0x57, 0x01, 0x00]))

    def test_ble_payload_with_password(self):
        import binascii
        from nd287_app.switchbot import ble_press_payload
        payload = ble_press_payload("pass123")
        crc = binascii.crc32(b"pass123") & 0xFFFFFFFF
        self.assertEqual(payload, bytes([0x57, 0x11]) + crc.to_bytes(4, "big") + b"\x00")

    def test_bot_configured(self):
        from nd287_app.switchbot import bot_configured
        self.assertFalse(bot_configured({}))
        self.assertTrue(bot_configured({"switchbot_token": "t", "switchbot_secret": "s",
                                        "switchbot_device": "d"}))
        self.assertFalse(bot_configured({"switchbot_token": "t"}))
        self.assertTrue(bot_configured({"switchbot_use_ble": True,
                                        "switchbot_ble_mac": "AA:BB"}))
        self.assertFalse(bot_configured({"switchbot_use_ble": True}))

    def test_cloud_unconfigured(self):
        from nd287_app.switchbot import press_bot_cloud
        ok, message = press_bot_cloud("", "", "")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
