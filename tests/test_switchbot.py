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


if __name__ == "__main__":
    unittest.main()
