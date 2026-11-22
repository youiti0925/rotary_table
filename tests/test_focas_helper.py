# -*- coding: utf-8 -*-
"""focas_helper（構造体と値の変換）。偽物の機械（tests/fake_fwlib.py）を直接つなぐ。

実機にはつながない。本物の DLL も 32bit Python も呼ばない。
設計書 §11.2 の 1〜12・18・22・23 と、読む命令まわりの確認。
"""

import base64
import ctypes
import hashlib
import io
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_helper as H          # noqa: E402
from fake_fwlib import FakeFwlib, FakeMachine    # noqa: E402

# 書込・削除・運転開始・PMC・サーチ系の関数は、一覧に入れない（段4以降も運転系は入れない）
FORBIDDEN = ("cnc_start", "cnc_search", "cnc_wrmacro", "cnc_wrparam", "cnc_dwnstart4",
             "cnc_download4", "cnc_dwnend4", "cnc_pdf_del", "cnc_delete", "cnc_wrpmcrng",
             "pmc_wrpmcrng", "pmc_wrpmcrng2", "cnc_wrtofs", "cnc_pdf_rename",
             "cnc_setpath", "cnc_cyclestart", "cnc_reset", "cnc_wrpitchdata")


def make(scenario=None, **kw):
    lib = FakeFwlib(scenario=scenario)
    hp = H.Helper(lib, sleep=lambda s: None, **kw)
    return lib, hp


class Run:
    """命令を1つずつ流して返事を見る。"""

    def __init__(self, hp):
        self.hp = hp
        self.events = []
        self.n = 0

    def __call__(self, cmd, **args):
        self.n += 1
        return self.hp.handle({"id": self.n, "cmd": cmd, "args": args}, self.events.append)

    def ok(self, cmd, **args):
        r = self(cmd, **args)
        assert r["ok"], r
        return r["result"]


def connected(scenario=None):
    lib, hp = make(scenario)
    run = Run(hp)
    run.ok("connect", host="127.0.0.1", port=8193, timeout=1)
    return lib, hp, run


class TestApi(unittest.TestCase):
    def test_restype_is_c_short_for_every_function(self):
        lib = FakeFwlib()
        H.Api(lib)
        for name in H.ALLOWED_FUNCS:
            self.assertIs(getattr(lib, name).restype, ctypes.c_short, name)

    def test_allowed_list_is_only_read_functions(self):
        for bad in FORBIDDEN:
            self.assertNotIn(bad, H.ALLOWED_FUNCS)
        self.assertEqual(sorted(H.ALLOWED_FUNCS), sorted(set(H.ALLOWED_FUNCS)))
        import re
        src = Path(H.__file__).read_text(encoding="utf-8")
        # 一覧にない名前がコード側に出てくるなら、ここで気づく（コメント内の名前は除く）
        code_only = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        doc_free = re.sub(r'"""[\s\S]*?"""', "", code_only)
        used = set(re.findall(r'(?:read_rc\(|check\(|_fail\(|api\.)"?((?:cnc|pmc)_\w+)', doc_free))
        self.assertGreater(len(used), 10)
        self.assertEqual(used - set(H.ALLOWED_FUNCS), set())

    def test_unlisted_function_cannot_be_fetched(self):
        lib = FakeFwlib()
        api = H.Api(lib)
        for name in ("cnc_start", "cnc_wrparam", "pmc_wrpmcrng", "cnc_search"):
            with self.assertRaises(AttributeError):
                getattr(api, name)
        self.assertEqual(lib.called("cnc_wrparam"), 0)

    def test_fake_has_write_functions_that_helper_never_calls(self):
        lib, hp, run = connected()
        for cmd, args in (("identify", {}), ("status", {}), ("alarms", {}), ("prog_info", {}),
                          ("prog_list", {"recursive": True}), ("param_info", {}),
                          ("param_read", {"no": 20}), ("param_dump", {"kind": "param"}),
                          ("prog_read", {"path": "//CNC_MEM/USER/PATH1/O901"})):
            run(cmd, **args)
        for bad in FORBIDDEN:
            self.assertEqual(lib.called(bad), 0, bad)

    def test_missing_function_in_dll_is_not_fatal_until_used(self):
        lib = FakeFwlib()
        del lib.cnc_rdpdf_alldir
        api = H.Api(lib)
        self.assertIn("cnc_rdpdf_alldir", api.missing)
        with self.assertRaises(AttributeError):
            api.cnc_rdpdf_alldir


class TestParsers(unittest.TestCase):
    def test_sysinfo(self):
        b = struct.pack("<hh2s2s4s4s2s", 0, 6, b"31", b"M ", b"G103", b"18.0", b"6 ")
        self.assertEqual(len(b), 18)
        r = H.parse_sysinfo(b)
        self.assertEqual((r["cnc_type"], r["mt_type"], r["series"], r["version"], r["axes"]),
                         ("31", "M", "G103", "18.0", 6))
        self.assertEqual(r["max_axis"], 6)

    def test_statinfo_names_and_raw(self):
        r = H.parse_statinfo([0, 0, 0, 0, 0, 0, 0, 1, 0] + [0] * 7)
        self.assertEqual(len(r["values"]), 9)
        self.assertEqual(r["values"]["alarm"], 1)
        self.assertEqual(r["text"]["aut"], "MDI")
        self.assertEqual(r["text"]["run"], "リセット")
        self.assertEqual(r["text"]["alarm"], "アラーム")
        r = H.parse_statinfo([0, 0, 77, 3, 1, 0, 1, 0, 0] + [0] * 7)
        self.assertEqual(r["text"]["aut"], "不明(77)")       # 未確認の値は不明と出す
        self.assertEqual(r["text"]["run"], "運転中")
        self.assertEqual(r["text"]["emergency"], "非常停止")

    def test_alarm_entries(self):
        def ent(no, typ, text):
            raw = text.encode("cp932")[:32]
            return struct.pack("<ihhhh", no, typ, 0, 0, len(raw)) + raw.ljust(32, b"\0")
        buf = ent(100, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です") + ent(0, 1, "電源を切って下さい")
        r = H.parse_alarms(buf, 2)
        self.assertEqual(r[0]["code"], "SW0100")
        self.assertEqual(r[0]["text"], "ﾊﾟﾗﾒｰﾀ書き込み可能です")
        self.assertEqual(r[1]["code"], "PW0000")
        self.assertEqual((r[1]["type"], r[1]["no"]), (1, 0))
        self.assertEqual(H.parse_alarms(buf, 0), [])
        self.assertEqual(H.alarm_code(99, 5), "種類99-0005")

    def test_type_codes(self):
        dt = H.decode_type
        self.assertEqual(dt(0)["kind"], "bit")
        self.assertEqual(dt(16)["kind"], "bit")
        self.assertEqual(dt(17)["kind"], "byte")
        r = dt(5)
        self.assertEqual((r["kind"], r["axis"]), ("byte", True))
        r = dt(68)
        self.assertEqual((r["kind"], r["axis"]), ("bit", True))
        self.assertEqual(dt(6)["kind"], "word")
        self.assertEqual(dt(71)["kind"], "dword")
        r = dt(4103)
        self.assertEqual((r["kind"], r["axis"], r["real"]), ("real", True, True))
        self.assertEqual(dt(4099)["kind"], "real")

    def test_type_codes_with_undecoded_bits_are_reported_not_refused(self):
        r = H.decode_type(260)
        self.assertEqual((r["kind"], r["spindle"], r["axis"]), ("bit", True, True))
        r = H.decode_type(96)
        self.assertEqual((r["kind"], r["unknown_bits"]), ("bit", [0x20, 0x40]))
        r = H.decode_type(4179)
        self.assertEqual((r["kind"], r["unknown_bits"]), ("real", [0x10, 0x40]))

    def test_real_formatting_has_no_float_error(self):
        self.assertEqual(H.fmt_real(-10, 1), "-1.0")
        self.assertEqual(H.fmt_real(200000, 1), "20000.0")
        self.assertEqual(H.fmt_real(108125, 3), "108.125")
        self.assertEqual(H.fmt_real(1, 3), "0.001")
        self.assertEqual(H.fmt_real(-5, 3), "-0.005")
        self.assertEqual(H.fmt_real(7, 0), "7")

    def test_param_data_each_kind(self):
        hdr = struct.pack("<hh", 1, 0)
        self.assertEqual(H.parse_param_data("bit", hdr + b"\x20"), "00100000")
        self.assertEqual(H.parse_param_data("bit", hdr + b"\x09"), "00001001")
        self.assertEqual(H.parse_param_data("byte", hdr + b"\x09"), "9")
        self.assertEqual(H.parse_param_data("byte", hdr + struct.pack("<b", -49)), "-49")
        self.assertEqual(H.parse_param_data("word", hdr + struct.pack("<h", -300)), "-300")
        self.assertEqual(H.parse_param_data("dword", hdr + struct.pack("<i", -360000)), "-360000")
        self.assertEqual(H.parse_param_data("real", hdr + struct.pack("<ii", -10, 1)), "-1.0")
        self.assertEqual(H.parse_param_data("real", hdr + struct.pack("<ii", 200000, 1)), "20000.0")

    def test_axis_field(self):
        self.assertEqual(H.axis_field(""), 0)
        self.assertEqual(H.axis_field("-"), 0)
        self.assertEqual(H.axis_field("A4"), 4)
        self.assertEqual(H.axis_field("A12"), 12)
        self.assertEqual(H.axis_field("S1"), 1)
        for lab in ("L1", "L2", "T1"):
            with self.assertRaises(H.FocasError) as cm:
                H.axis_field(lab)
            self.assertEqual(cm.exception.kind, "refused")

    def test_path_check(self):
        for bad in ("O901", "/CNC_MEM/x", "//CNC_MEM/../x", None, "//" + "a" * 300):
            with self.assertRaises(H.FocasError):
                H._check_path(bad)
        self.assertEqual(H._check_path("//CNC_MEM/USER/PATH1/"), "//CNC_MEM/USER/PATH1/")


class TestCommands(unittest.TestCase):
    def test_connect_and_identify(self):
        lib, hp, run = connected()
        r = run.ok("identify")
        self.assertEqual((r["cnc_type"], r["mt_type"], r["series"], r["version"], r["axes"]),
                         ("31", "M", "G103", "18.0", 6))
        self.assertEqual(r["cnc_id"], "3C7B5D01-49C1F435-5E28C389-6592C486")
        self.assertEqual(r["axis_names"], ["X", "Y", "Z", "A", "B", "C"])
        self.assertTrue(r["supported"])
        self.assertEqual(r["family"], "31i")
        self.assertEqual(lib.host, "127.0.0.1")

    def test_status_returns_names_and_raw(self):
        lib, hp, run = connected({"status": {"aut": 0, "run": 0, "alarm": 1}})
        r = run.ok("status")
        self.assertEqual(sorted(r["values"]), sorted(H.STATUS_FIELDS))
        self.assertEqual(r["values"]["alarm"], 1)
        self.assertEqual(r["text"]["alarm"], "アラーム")

    def test_alarms_none_one_and_over_ten(self):
        lib, hp, run = connected()
        r = run.ok("alarms")
        self.assertEqual((r["count"], r["alarms"], r["truncated"]), (0, [], False))
        lib, hp, run = connected({"alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        r = run.ok("alarms")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["alarms"][0]["code"], "SW0100")
        self.assertIn("書き込み", r["alarms"][0]["text"])
        many = [[500 + i, 4, 1, "OT"] for i in range(12)]
        lib, hp, run = connected({"alarms": many})
        r = run.ok("alarms")
        self.assertEqual(r["count"], 10)
        self.assertTrue(r["truncated"])             # 10件を超えるかもしれないことを知らせる

    def test_prog_info(self):
        lib, hp, run = connected({"remaining": 0})
        r = run.ok("prog_info")
        self.assertEqual(r["remaining"], 0)
        self.assertEqual((r["used"], r["unused"]), (32500, 105000))
        self.assertEqual(r["registered"], 4)

    def test_prog_list_walks_folders(self):
        lib, hp, run = connected()
        r = run.ok("prog_list", path="//CNC_MEM/", recursive=True)
        paths = [t["path"] for t in r["tree"]]
        self.assertEqual(paths[0], "//CNC_MEM/")
        self.assertIn("//CNC_MEM/USER/PATH1/", paths)
        self.assertIn("//CNC_MEM/USER/PATH2/", paths)
        self.assertIn("//CNC_MEM/USER/LIBRARY/", paths)
        p1 = next(t for t in r["tree"] if t["path"] == "//CNC_MEM/USER/PATH1/")
        self.assertEqual([x["name"] for x in p1["programs"]], ["O1000", "O2100", "O901"])
        o901 = next(x for x in p1["programs"] if x["name"] == "O901")
        self.assertEqual(o901["comment"], "RT-135 TEST")
        self.assertEqual(o901["date"], "2026-11-22 16:04")
        self.assertGreater(o901["size"], 10)
        top = r["tree"][0]
        self.assertEqual(top["folders"], ["USER"])

    def test_prog_list_continues_in_tens(self):
        lib, hp, run = connected({"extra_programs": 23})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertEqual(len(r["programs"]), 26)
        self.assertEqual(len({x["name"] for x in r["programs"]}), 26)

    def test_prog_list_full_width_name(self):
        lib, hp, run = connected({"long_name": True})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertIn("O" + "1" * 35, [x["name"] for x in r["programs"]])

    def test_param_info_continues_and_stops(self):
        lib, hp, run = connected()
        r = run.ok("param_info", count=5)              # 5件ずつ。続き読みになる
        nos = [no for no, _ in r["types"]]
        self.assertEqual(nos, sorted(set(nos)))
        self.assertEqual(len(nos), 29)
        self.assertEqual(dict(map(tuple, r["types"]))[1420], 4103)
        self.assertGreater(lib.called("cnc_rdparainfo"), 3)

    def test_param_info_stops_when_next_does_not_advance(self):
        lib, hp, run = connected()
        orig = lib._cnc_rdparainfo

        def stuck(h, start, count, buf):
            rc = orig(h, start, count, buf)
            raw = bytearray(buf.raw)
            struct.pack_into("<h", raw, 4, 0)           # next_no を 0（進まない）にする
            ctypes.memmove(buf, bytes(raw), len(raw))
            return rc
        lib.cnc_rdparainfo.impl = stuck
        r = run.ok("param_info", count=5)
        self.assertEqual(len(r["types"]), 5)            # 無限に回らない
        self.assertEqual(lib.called("cnc_rdparainfo"), 1)

    def test_param_read_each_kind(self):
        lib, hp, run = connected()
        r = run.ok("param_read", no=0, label="L1", axis=0)
        self.assertEqual((r["kind"], r["value"]), ("bit", "00001001"))
        r = run.ok("param_read", no=20)
        self.assertEqual((r["kind"], r["value"], r["axis"]), ("byte", "9", 0))
        r = run.ok("param_read", no=1020, label="A4")
        self.assertEqual((r["kind"], r["value"], r["axis"]), ("byte", "65", 4))
        r = run.ok("param_read", no=7220, label="L1", axis=0)
        self.assertEqual(r["value"], "-49")                       # 符号つきバイト
        r = run.ok("param_read", no=1821, label="A1")
        self.assertEqual((r["kind"], r["value"]), ("dword", "3000"))
        r = run.ok("param_read", no=2165, label="A1")
        self.assertEqual((r["kind"], r["value"]), ("word", "25"))
        r = run.ok("param_read", no=1320, label="A1")
        self.assertEqual((r["kind"], r["value"]), ("real", "-1.0"))
        r = run.ok("param_read", no=1420, label="A4")
        self.assertEqual(r["value"], "20000.0")
        r = run.ok("param_read", no=982, label="S1")
        self.assertEqual(r["value"], "0")

    def test_param_read_type_is_looked_up_one_by_one_when_table_is_empty(self):
        lib, hp, run = connected()
        self.assertEqual(hp.types, {})
        run.ok("param_read", no=20)
        self.assertEqual(hp.types, {20: 17})
        self.assertEqual(lib.called("cnc_rdparainfo"), 1)
        run.ok("param_read", no=20)
        self.assertEqual(lib.called("cnc_rdparainfo"), 1)          # 2回目は表から

    def test_param_read_missing_number_and_l_label_without_axis(self):
        lib, hp, run = connected()
        r = run("param_read", no=12345, label="")
        self.assertFalse(r["ok"])
        r = run("param_read", no=1, label="L1")                    # 軸の指し方は未確認
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "refused")

    def test_param_read_many_continues_past_missing(self):
        lib, hp, run = connected()
        r = run.ok("param_read_many", items=[
            {"no": 20, "label": ""}, {"no": 12345, "label": ""}, {"no": 1020, "label": "A1"}])
        self.assertEqual([i["ok"] for i in r["items"]], [True, False, True])
        self.assertEqual(r["items"][1]["error"]["rc"], 3)
        self.assertEqual(r["items"][2]["value"], "88")

    def test_param_dump_matches_machine_and_ends_with_percent(self):
        lib, hp, run = connected({"dump_pad_lines": 2500})
        r = run.ok("param_dump", kind="param")
        data = base64.b64decode(r["data"])
        self.assertEqual(data, lib.m.dump(2))
        self.assertEqual(r["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(r["size"], len(data))
        self.assertGreater(len(data), 2560)                        # 1280バイトを2回以上
        self.assertTrue(data.endswith(b"%"))
        self.assertGreater(lib.called("cnc_upload4"), 2)
        self.assertEqual(lib.called("cnc_upend4"), 1)
        self.assertTrue(any(e.get("event") == "progress" for e in run.events))

    def test_param_dump_every_kind(self):
        lib, hp, run = connected()
        for kind in ("pitch", "macro", "tool"):
            r = run.ok("param_dump", kind=kind)
            self.assertTrue(base64.b64decode(r["data"]).endswith(b"%"), kind)

    def test_param_dump_survives_buffer_busy(self):
        lib, hp, run = connected({"dump_pad_lines": 200, "buffer_every": 2})
        r = run.ok("param_dump", kind="param")
        self.assertEqual(base64.b64decode(r["data"]), lib.m.dump(2))

    def test_param_dump_unreadable_kind_is_reported(self):
        lib, hp, run = connected()
        r = run("param_dump", kind="workofs")
        self.assertFalse(r["ok"])
        self.assertEqual((r["error"]["func"], r["error"]["rc"]), ("cnc_upstart4", 6))
        self.assertEqual(lib.called("cnc_upload4"), 0)

    def test_param_dump_lost_midway(self):
        lib, hp, run = connected({"dump_pad_lines": 500})
        lib.sc["lost_after"] = lib.count + 3
        r = run("param_dump", kind="param")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "lost")
        self.assertEqual(r["error"]["rc"], -16)
        self.assertGreaterEqual(lib.called("cnc_upend4"), 1)       # 途中で切れても終わりの呼び出しはする

    def test_param_dump_time_limit(self):
        lib, hp, run = connected({"dump_pad_lines": 200, "buffer_every": 1})
        t = [0.0]

        def clock():
            t[0] += 1.0
            return t[0]
        hp.clock = clock
        r = run("param_dump", kind="param", limit=5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "timeout")           # 上限時間で必ず抜ける

    def test_prog_read(self):
        lib, hp, run = connected()
        r = run.ok("prog_read", path="//CNC_MEM/USER/PATH1/O901")
        data = base64.b64decode(r["data"])
        self.assertEqual(data, lib.m.programs["//CNC_MEM/USER/PATH1/O901"][0])
        r = run("prog_read", path="//CNC_MEM/USER/PATH1/O7777")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["func"], "cnc_upstart4")

    def test_not_connected(self):
        lib, hp = make()
        run = Run(hp)
        for cmd in ("identify", "status", "alarms", "prog_info", "param_dump"):
            r = run(cmd, kind="param")
            self.assertEqual(r["error"]["kind"], "not_connected", cmd)

    def test_connect_failure_and_bad_host(self):
        lib, hp = make({"connect_fail_count": 1})
        run = Run(hp)
        r = run("connect", host="10.0.0.1", port=8193)
        self.assertEqual((r["error"]["kind"], r["error"]["rc"]), ("connect", -16))
        self.assertTrue(run("connect", host="10.0.0.1")["ok"])
        r = run("connect", host="")
        self.assertEqual(r["error"]["kind"], "bad_args")
        r = run("connect", host="日本語")
        self.assertEqual(r["error"]["kind"], "bad_args")

    def test_disconnect_frees_handle(self):
        lib, hp, run = connected()
        run.ok("disconnect")
        self.assertEqual(lib.called("cnc_freelibhndl"), 1)
        self.assertEqual(run("status")["error"]["kind"], "not_connected")

    def test_busy_is_retried_once_for_reads(self):
        lib, hp, run = connected()
        calls = []
        orig = lib._cnc_statinfo

        def busy_then_ok(h, arr):
            calls.append(1)
            return -1 if len(calls) == 1 else orig(h, arr)
        lib.cnc_statinfo.impl = busy_then_ok
        self.assertTrue(run("status")["ok"])
        self.assertEqual(len(calls), 2)
        lib.cnc_statinfo.impl = lambda h, arr: -1
        r = run("status")
        self.assertEqual((r["error"]["kind"], r["error"]["rc"]), ("focas", -1))


class TestFamilies(unittest.TestCase):
    def test_unsupported_family_still_identifies_and_reports_status(self):
        lib, hp = make()
        lib.m.cnc_type = b"0 "
        run = Run(hp)
        r = run.ok("connect", host="127.0.0.1")
        self.assertFalse(r["supported"])
        i = run.ok("identify")
        self.assertEqual(i["cnc_type"], "0")
        self.assertFalse(i["supported"])
        self.assertTrue(run("status")["ok"])
        self.assertTrue(run("alarms")["ok"])
        for cmd, args in (("prog_list", {}), ("prog_info", {}), ("param_info", {}),
                          ("param_read", {"no": 20}), ("param_dump", {"kind": "param"}),
                          ("prog_read", {"path": "//CNC_MEM/x"})):
            r = run(cmd, **args)
            self.assertFalse(r["ok"], cmd)
            self.assertEqual(r["error"]["kind"], "unsupported", cmd)
            self.assertIn("0i", r["error"]["message"])

    def test_35i_is_unsupported_until_assigned(self):
        lib, hp = make()
        lib.m.cnc_type = b"35"
        run = Run(hp)
        self.assertFalse(run.ok("connect", host="127.0.0.1")["supported"])
        self.assertEqual(run("prog_info")["error"]["kind"], "unsupported")
        # 設定で 31i と同じ手順に割り当てると使える（読むだけの確認が済んだあとの話）
        r = run.ok("connect", host="127.0.0.1", families={"35": "31i"})
        self.assertTrue(r["supported"])
        self.assertTrue(run("prog_info")["ok"])

    def test_family_names_exist(self):
        self.assertTrue(H.FAMILY_CLASSES["31i"].implemented)
        self.assertFalse(H.FAMILY_CLASSES["0i-D"].implemented)
        self.assertFalse(H.FAMILY_CLASSES["18i"].implemented)


class TestRobustness(unittest.TestCase):
    def test_any_exception_becomes_an_error_reply(self):
        lib, hp, run = connected()

        def boom(h, arr):
            raise RuntimeError("DLL が壊れた")
        lib.cnc_statinfo.impl = boom
        r = run("status")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "internal")
        self.assertIn("RuntimeError", r["error"]["message"])
        self.assertTrue(run("alarms")["ok"])                       # そのあとも動く

    def test_bad_messages(self):
        lib, hp = make()
        for msg in ({}, {"cmd": 5}, {"cmd": "なにか"}, {"cmd": "status", "args": [1]}, [1, 2]):
            r = hp.handle(msg, lambda x: None)
            self.assertFalse(r["ok"], msg)
        # 書込・運転開始の命令名は存在しない
        for cmd in ("param_write", "prog_write", "prog_delete", "start", "cycle_start"):
            r = hp.handle({"id": 1, "cmd": cmd}, lambda x: None)
            self.assertEqual(r["error"]["kind"], "bad_args", cmd)

    def test_serve_replies_one_json_line_each_and_survives_garbage(self):
        lib = FakeFwlib()
        stdin = io.BytesIO(b"\n".join([
            json.dumps({"id": 1, "cmd": "connect", "args": {"host": "127.0.0.1"}}).encode(),
            "これはJSONではない".encode(),
            b"{\"id\": 2, \"cmd\": \"alarms\"",                     # 途中で切れた行
            json.dumps({"id": 3, "cmd": "status"}).encode(),
            json.dumps({"id": 4, "cmd": "quit"}).encode(),
            json.dumps({"id": 5, "cmd": "status"}).encode(),        # quit のあとは読まない
        ]) + b"\n")
        out = io.BytesIO()
        H.serve(lib, stdin, out, hello={"hello": 1, "proto": H.PROTO}, sleep=lambda s: None)
        text = out.getvalue()
        self.assertTrue(text.isascii())                             # ASCII だけ
        lines = [json.loads(l) for l in text.decode("ascii").splitlines()]
        self.assertEqual(lines[0]["hello"], 1)
        self.assertEqual([l.get("id") for l in lines[1:]], [1, None, None, 3, 4])
        self.assertEqual(lines[2]["error"]["kind"], "protocol")
        self.assertEqual(lines[3]["error"]["kind"], "protocol")
        self.assertTrue(lines[4]["ok"])
        self.assertEqual(lib.called("cnc_freelibhndl"), 1)

    def test_serve_frees_handle_when_input_closes(self):
        lib = FakeFwlib()
        stdin = io.BytesIO(json.dumps({"id": 1, "cmd": "connect",
                                       "args": {"host": "127.0.0.1"}}).encode() + b"\n")
        H.serve(lib, stdin, io.BytesIO(), sleep=lambda s: None)
        self.assertEqual(lib.called("cnc_freelibhndl"), 1)

    def test_japanese_alarm_text_is_ascii_on_the_wire(self):
        lib = FakeFwlib(scenario={"alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        stdin = io.BytesIO(b"\n".join([
            json.dumps({"id": 1, "cmd": "connect", "args": {"host": "127.0.0.1"}}).encode(),
            json.dumps({"id": 2, "cmd": "alarms"}).encode()]) + b"\n")
        out = io.BytesIO()
        H.serve(lib, stdin, out, sleep=lambda s: None)
        self.assertTrue(out.getvalue().isascii())
        rep = json.loads(out.getvalue().decode("ascii").splitlines()[1])
        self.assertEqual(rep["result"]["alarms"][0]["text"], "ﾊﾟﾗﾒｰﾀ書き込み可能です")

    def test_importing_does_not_load_a_dll(self):
        # import しただけでは何も読まない（main を呼ぶまで ctypes.WinDLL は使われない）
        src = Path(H.__file__).read_text(encoding="utf-8")
        head = src.split("def main(")[0]
        self.assertNotIn("WinDLL(", head)

    def test_helper_imports_only_standard_library(self):
        import re
        src = Path(H.__file__).read_text(encoding="utf-8")
        mods = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*)", src, re.M))
        self.assertEqual(mods - {"base64", "ctypes", "hashlib", "json", "os", "struct",
                                 "sys", "time", "platform"}, set())
        self.assertNotIn("nd287_app", mods)


if __name__ == "__main__":
    unittest.main()
