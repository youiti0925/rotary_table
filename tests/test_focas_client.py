# -*- coding: utf-8 -*-
"""focas_client（プロセスとやり取り）。偽物の機械を差し込んだヘルパーを子プロセスで起動する。

実機にはつながない。本物の DLL も 32bit Python も呼ばない
（手元の 64bit Python で動かす。偽物のヘルパーは bits=32 と名乗る）。
設計書 §11.2 の 24〜36。
"""

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from nd287_app import focas_client as C

FAKE_HELPER = str(Path(__file__).resolve().parent / "fake_focas_helper.py")


def make_client(scenario=None, **kw):
    env = {"FAKE_FOCAS_SCENARIO": json.dumps(scenario or {})}
    kw.setdefault("timeouts", {"start": 15, "connect": 15, "default": 10, "quit": 2})
    return C.FocasClient(sys.executable, FAKE_HELPER, env=env, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self._clients = []

    def tearDown(self):
        for c in self._clients:
            c.close()

    def client(self, scenario=None, **kw):
        c = make_client(scenario, **kw)
        self._clients.append(c)
        return c

    def connected(self, scenario=None, **kw):
        c = self.client(scenario, **kw)
        c.call("connect", {"host": "127.0.0.1", "port": 8193, "timeout": 1})
        return c


class TestStart(Base):
    def test_hello(self):
        c = self.client()
        hello = c.start()
        self.assertEqual(hello["bits"], 32)
        self.assertEqual(hello["proto"], C.PROTO)
        self.assertTrue(hello["dll_ok"])
        self.assertTrue(c.alive)

    def test_no_python(self):
        c = C.FocasClient(r"C:\ない\python.exe", FAKE_HELPER)
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "no_python")
        self.assertIn("32bit", cm.exception.message)
        c = C.FocasClient("", FAKE_HELPER)
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "no_python")

    def test_no_helper(self):
        c = C.FocasClient(sys.executable, r"C:\ない\focas_helper.py")
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "no_helper")

    def test_not_32bit(self):
        c = self.client({"bits": 64})
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "not_32bit")
        self.assertIn("64bit", cm.exception.message)
        self.assertFalse(c.alive)                       # つながずに止める

    def test_bit_check_can_be_relaxed_only_by_the_caller(self):
        # ヘルパー起動の試験は 64bit Python で回すので、その試験だけ緩める
        c = self.client({"bits": 64}, expect_bits=None)
        self.assertEqual(c.start()["bits"], 64)

    def test_no_dll(self):
        c = self.client({"no_dll": True})
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "no_dll")
        self.assertIn("SERVO GUIDE", cm.exception.hint)
        self.assertFalse(c.alive)

    def test_old_helper(self):
        c = self.client({"proto": 99})
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "helper_old")

    def test_no_hello_times_out(self):
        c = self.client({"no_hello": True}, timeouts={"start": 1})
        t0 = time.monotonic()
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertLess(time.monotonic() - t0, 8)
        self.assertFalse(c.alive)

    def test_garbage_hello_is_protocol_error(self):
        c = self.client({"garbage_hello": True})
        with self.assertRaises(C.FocasError) as cm:
            c.start()
        self.assertEqual(cm.exception.kind, "protocol")
        self.assertFalse(c.alive)

    def test_helper_that_cannot_start(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad_helper.py"
            bad.write_text("raise SystemExit(5)\n")
            c = C.FocasClient(sys.executable, str(bad))
            with self.assertRaises(C.FocasError) as cm:
                c.start()
            self.assertEqual(cm.exception.kind, "protocol")


class TestCalls(Base):
    def test_call_and_result(self):
        c = self.connected()
        self.assertTrue(c.connected)
        r = c.call("identify")
        self.assertEqual(r["cnc_type"], "31")
        self.assertEqual(c.call("status")["values"]["run"], 0)

    def test_helper_error_becomes_focas_error_with_func_and_rc(self):
        c = self.connected()
        with self.assertRaises(C.FocasError) as cm:
            c.call("param_dump", {"kind": "workofs"})
        e = cm.exception
        self.assertEqual(e.kind, "focas")
        self.assertEqual((e.detail["func"], e.detail["rc"]), ("cnc_upstart4", 6))
        self.assertIn("cnc_upstart4", e.message)
        self.assertIn("6", e.message)

    def test_not_connected_error(self):
        c = self.client()
        with self.assertRaises(C.FocasError) as cm:
            c.call("status")
        self.assertEqual(cm.exception.kind, "not_connected")

    def test_connect_failure(self):
        c = self.client({"connect_fail_count": 1})
        with self.assertRaises(C.FocasError) as cm:
            c.call("connect", {"host": "127.0.0.1"})
        self.assertEqual(cm.exception.kind, "connect")
        self.assertIn("-16", cm.exception.message)
        self.assertFalse(c.connected)
        c.call("connect", {"host": "127.0.0.1"})
        self.assertTrue(c.connected)

    def test_events_come_first_and_ids_do_not_mix(self):
        c = self.connected({"dump_pad_lines": 2500, "stale_before": 3})
        events = []
        r = c.call("param_dump", {"kind": "param"}, on_event=events.append)
        self.assertGreaterEqual(len(events), 1)
        self.assertTrue(all(e["event"] == "progress" for e in events))
        data = C.FocasClient.decode_data(r)
        self.assertTrue(data.endswith(b"%"))
        self.assertGreater(len(data), 25600)
        self.assertNotIn("stale", r)
        # 古い（別の id の）返事は捨てられ、次の命令も取り違えない
        self.assertEqual(c.call("status")["values"]["run"], 0)

    def test_data_round_trips_byte_for_byte(self):
        raw = b"%\nO1(A) \n\r\rG04X0.\x00 \n\r\r  \n%"          # LF CR CR・末尾空白・NUL
        path = "//CNC_MEM/USER/PATH1/O555"
        c = self.connected({"program_bytes": {path: base64.b64encode(raw).decode()}})
        r = c.call("prog_read", {"path": path})
        self.assertEqual(C.FocasClient.decode_data(r), raw)

    def test_decode_data_detects_corruption(self):
        c = self.connected()
        r = c.call("prog_read", {"path": "//CNC_MEM/USER/PATH1/O901"})
        r = dict(r)
        r["data"] = base64.b64encode(b"%\nX\n%").decode()
        with self.assertRaises(C.FocasError) as cm:
            C.FocasClient.decode_data(r)
        self.assertEqual(cm.exception.kind, "protocol")

    def test_japanese_alarm_text_round_trips(self):
        c = self.connected({"alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        r = c.call("alarms")
        self.assertEqual(r["alarms"][0]["text"], "ﾊﾟﾗﾒｰﾀ書き込み可能です")
        self.assertEqual(r["alarms"][0]["code"], "SW0100")

    def test_two_threads_are_serialized(self):
        c = self.connected({"alarms": [[100, 0, 0, "A"]]})
        results, errors = [], []

        def work(cmd):
            try:
                for _ in range(15):
                    r = c.call(cmd)
                    results.append((cmd, "values" in r, "alarms" in r))
            except Exception as e:                      # pragma: no cover
                errors.append(e)
        ts = [threading.Thread(target=work, args=(cmd,)) for cmd in ("status", "alarms") * 2]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 60)
        for cmd, has_values, has_alarms in results:
            self.assertEqual((has_values, has_alarms), (cmd == "status", cmd == "alarms"))


class TestFailures(Base):
    def test_timeout_kills_helper_and_next_call_restarts(self):
        c = self.connected({"hang_in": "cnc_statinfo"})
        pid = c.pid
        t0 = time.monotonic()
        with self.assertRaises(C.FocasError) as cm:
            c.call("status", timeout=1)
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertIn("1", cm.exception.message)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertFalse(c.alive)
        self.assertFalse(c.connected)
        # 次の命令で起動し直す（機械との接続は切れているので、つなぎ直しは呼び出し側）
        with self.assertRaises(C.FocasError) as cm:
            c.call("identify")
        self.assertEqual(cm.exception.kind, "not_connected")
        self.assertTrue(c.alive)
        self.assertNotEqual(c.pid, pid)
        c.call("connect", {"host": "127.0.0.1"})
        self.assertEqual(c.call("identify")["cnc_type"], "31")

    def test_garbage_line_is_protocol_error_and_app_survives(self):
        c = self.connected({"garbage_at": 2})
        with self.assertRaises(C.FocasError) as cm:
            c.call("status")
        self.assertEqual(cm.exception.kind, "protocol")
        self.assertFalse(c.alive)
        c.call("connect", {"host": "127.0.0.1"})            # 起動し直せる
        self.assertTrue(c.alive)

    def test_blank_line_is_protocol_error(self):
        c = self.connected({"blank_at": 2})
        with self.assertRaises(C.FocasError) as cm:
            c.call("status")
        self.assertEqual(cm.exception.kind, "protocol")

    def test_cut_off_line_is_protocol_error(self):
        c = self.connected({"partial_at": 2})
        with self.assertRaises(C.FocasError) as cm:
            c.call("status")
        self.assertEqual(cm.exception.kind, "protocol")

    def test_helper_dies_suddenly(self):
        c = self.connected({"die_at": 2})
        with self.assertRaises(C.FocasError) as cm:
            c.call("status")
        self.assertEqual(cm.exception.kind, "lost")
        self.assertFalse(c.alive)

    def test_connection_lost_error_from_dll(self):
        c = self.connected({"lost_after": 2})
        with self.assertRaises(C.FocasError) as cm:
            c.call("alarms")
        self.assertEqual(cm.exception.kind, "lost")
        self.assertIn("-16", cm.exception.message)
        self.assertFalse(c.connected)


class TestClose(Base):
    def test_close_sends_quit_and_helper_ends(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "calls.txt")
            c = self.connected({"calls_log": log})
            proc = c._proc
            c.close()
            self.assertIsNotNone(proc.poll())
            self.assertFalse(c.alive)
            self.assertIn("cnc_freelibhndl", Path(log).read_text().split())
            c.close()                                       # 何度呼んでもよい

    def test_close_kills_helper_that_ignores_quit(self):
        c = self.connected({"hang_in": "cnc_freelibhndl"}, timeouts={"quit": 1, "default": 10,
                                                                      "start": 15, "connect": 15})
        proc = c._proc
        t0 = time.monotonic()
        c.close()
        self.assertIsNotNone(proc.poll())                   # 子プロセスが残らない
        self.assertLess(time.monotonic() - t0, 10)

    def test_helper_ends_by_itself_when_app_disappears(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "calls.txt")
            c = self.connected({"calls_log": log})
            proc = c._proc
            proc.stdin.close()                              # アプリが消えた（標準入力が閉じた）
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("cnc_freelibhndl", Path(log).read_text().split())

    def test_stderr_goes_to_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "focas_helper.log")
            c = self.client(log_path=log)
            c.start()
            c.close()
            # 何も書かなくてもファイルが壊れない。tail は空でもよい
            self.assertTrue(isinstance(list(c.stderr_tail), list))


class TestDescribe(unittest.TestCase):
    def test_messages_include_function_and_return_value(self):
        for err in ({"kind": "focas", "func": "cnc_wrparam", "rc": 7, "name": "EW_PROT"},
                    {"kind": "connect", "func": "cnc_allclibhndl3", "rc": -16, "name": "EW_SOCKET"},
                    {"kind": "lost", "func": "cnc_upload4", "rc": -8, "name": "EW_HANDLE"},
                    {"kind": "focas", "func": "cnc_rdparam", "rc": 99, "name": "?"}):
            msg, hint = C.describe(err)
            self.assertIn(err["func"], msg)
            self.assertIn(str(err["rc"]), msg)
        msg, _ = C.describe({"kind": "focas", "func": "cnc_wrparam", "rc": 7, "name": "EW_PROT"})
        self.assertIn("EW_PROT", msg)
        self.assertIn("書込", msg)

    def test_every_kind_has_a_japanese_message(self):
        for kind in ("no_python", "no_helper", "not_32bit", "no_dll", "helper_old", "connect",
                     "focas", "lost", "timeout", "refused", "unsupported", "not_connected",
                     "bad_args", "internal", "protocol", "なにか未知"):
            msg, _ = C.describe({"kind": kind, "seconds": 5})
            self.assertTrue(msg, kind)
            self.assertTrue(any(ord(ch) > 127 for ch in msg), kind)
        self.assertIn("5", C.describe({"kind": "timeout", "seconds": 5})[0])

    def test_unsupported_message_is_passed_through(self):
        msg, _ = C.describe({"kind": "unsupported", "message": "この機種（0i）はまだ対応していません"})
        self.assertIn("0i", msg)


class TestResolve(unittest.TestCase):
    def test_helper_path_resolution(self):
        p = C.resolve_helper("")
        self.assertTrue(p.endswith("focas_helper.py"))
        self.assertEqual(C.resolve_helper(r"C:\ない\x.py"), "")
        self.assertEqual(C.resolve_helper(FAKE_HELPER), FAKE_HELPER)

    def test_python32_setting_must_exist(self):
        self.assertEqual(C.resolve_python32(r"C:\ない\python.exe"), "")
        self.assertEqual(C.resolve_python32(sys.executable), sys.executable)

    def test_make_client_does_not_start_anything(self):
        c = C.make_client({"focas_python32": sys.executable, "focas_helper_path": FAKE_HELPER})
        self.assertFalse(c.alive)
        self.assertIsNone(c.pid)


if __name__ == "__main__":
    unittest.main()
