# -*- coding: utf-8 -*-
"""focas_helper（構造体と値の変換）。偽物の機械（tests/fake_fwlib.py）を直接つなぐ。

実機にはつながない。本物の DLL も 32bit Python も呼ばない。
設計書 §11.2 の 1〜12・18・22・23 と、読む命令まわりの確認。
"""

import base64
import contextlib
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

# 書く関数で一覧に入れてよいのは cnc_wrparam（パラメータ1件）だけ。
# 削除・プログラム書込・運転開始・PMC・マクロ書込・サーチ系は、一覧に入れない
FORBIDDEN = ("cnc_start", "cnc_search", "cnc_wrmacro", "cnc_dwnstart4",
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

    def test_allowed_list_is_read_functions_plus_cnc_wrparam_only(self):
        read_funcs = {
            "cnc_allclibhndl3", "cnc_freelibhndl", "cnc_sysinfo", "cnc_rdcncid", "cnc_rdaxisname",
            "cnc_statinfo", "cnc_rdalmmsg", "cnc_rdproginfo", "cnc_rdpdf_subdir",
            "cnc_rdpdf_alldir", "cnc_upstart4", "cnc_upload4", "cnc_upend4", "cnc_rdparainfo",
            "cnc_rdparam"}
        # 許可一覧に足してよい書込系は cnc_wrparam だけ（増やすなら、この試験を直す前に利用者へ聞く）
        self.assertEqual(set(H.ALLOWED_FUNCS) - read_funcs, {"cnc_wrparam"})
        for bad in FORBIDDEN:
            self.assertNotIn(bad, H.ALLOWED_FUNCS)
        for bad in ("cnc_wrparam2", "cnc_wrpmcrng", "cnc_pdf_del", "cnc_download4",
                    "cnc_wrmacro", "cnc_start", "pmc_wrpmcrng"):
            self.assertNotIn(bad, H.ALLOWED_FUNCS)
        self.assertEqual(sorted(H.ALLOWED_FUNCS), sorted(set(H.ALLOWED_FUNCS)))
        import re
        src = Path(H.__file__).read_text(encoding="utf-8")
        # 一覧にない名前がコード側に出てくるなら、ここで気づく（コメント内の名前は除く）
        code_only = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        doc_free = re.sub(r'"""[\s\S]*?"""', "", code_only)
        used = set(re.findall(r'(?:read_rc\(|call_retry\(|check\(|_fail\(|api\.)"?((?:cnc|pmc)_\w+)',
                              doc_free))
        self.assertGreater(len(used), 10)
        self.assertEqual(used - set(H.ALLOWED_FUNCS), set())

    def test_unlisted_function_cannot_be_fetched(self):
        lib = FakeFwlib()
        api = H.Api(lib)
        for name in ("cnc_start", "pmc_wrpmcrng", "cnc_search", "cnc_wrmacro", "cnc_pdf_del",
                     "cnc_dwnstart4"):
            with self.assertRaises(AttributeError):
                getattr(api, name)
        self.assertEqual(lib.called("cnc_start"), 0)
        self.assertEqual(lib.called("cnc_wrparam"), 0)          # 取り出しただけでは呼ばれない

    def test_fake_has_write_functions_that_helper_never_calls(self):
        lib, hp, run = connected()
        for cmd, args in (("identify", {}), ("status", {}), ("alarms", {}), ("prog_info", {}),
                          ("prog_list", {"recursive": True}), ("param_info", {}),
                          ("param_read", {"no": 20}), ("param_dump", {"kind": "param"}),
                          ("prog_read", {"path": "//CNC_MEM/USER/PATH1/O901"})):
            run(cmd, **args)
        for bad in FORBIDDEN + ("cnc_wrparam",):
            self.assertEqual(lib.called(bad), 0, bad)            # 読む命令は、書く関数を呼ばない

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
        # 登録本数は「プログラム＋フォルダ」の数（実機は 62本＋フォルダ10個で 72 だった）。
        # 偽物は プログラム4本 + フォルダ4個（USER・PATH1・PATH2・LIBRARY）
        self.assertEqual(r["registered"], 8)

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
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run("status")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "internal")
        # B-3: トレースバックは標準エラーへ（返事には載せない）
        self.assertIn("Traceback", err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())
        self.assertIn("cmd=status", err.getvalue())
        self.assertNotIn("Traceback", json.dumps(r))
        self.assertIn("RuntimeError", r["error"]["message"])
        self.assertTrue(run("alarms")["ok"])                       # そのあとも動く

    def test_bad_messages(self):
        lib, hp = make()
        for msg in ({}, {"cmd": 5}, {"cmd": "なにか"}, {"cmd": "status", "args": [1]}, [1, 2]):
            r = hp.handle(msg, lambda x: None)
            self.assertFalse(r["ok"], msg)
        # 書く命令は param_write だけ。プログラム書込・削除・運転開始の命令名は存在しない
        for cmd in ("prog_write", "prog_delete", "start", "cycle_start", "macro_write",
                    "pmc_write", "param_write_file"):
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
                                 "sys", "time", "platform", "traceback", "re", "decimal"}, set())
        self.assertNotIn("nd287_app", mods)


class TestMain(unittest.TestCase):
    """本番の入口 main()。本物の DLL は読まない（WinDLL を偽物に差し替える）。"""

    def _run(self, files, stdin_bytes=b"", search=()):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            for f in files:
                (Path(d) / f).write_bytes(b"")
            out = io.BytesIO()
            fake_stdout = mock.Mock(buffer=out)
            fake_stdin = mock.Mock(buffer=io.BytesIO(stdin_bytes))
            lib = FakeFwlib()
            old_stdout = sys.stdout
            try:
                with mock.patch.object(H, "DLL_SEARCH", search),                         mock.patch.object(sys, "stdout", fake_stdout),                         mock.patch.object(sys, "stdin", fake_stdin),                         mock.patch.object(ctypes, "WinDLL", create=True, side_effect=lambda p: lib):
                    rc = H.main(["--dll-dir", d])
                    redirected = sys.stdout
            finally:
                sys.stdout = old_stdout
            return rc, [json.loads(l) for l in out.getvalue().decode("ascii").splitlines()],                 redirected, lib

    def test_no_dll_says_so_in_hello_and_ends(self):
        rc, lines, _, lib = self._run([])
        self.assertEqual(rc, 3)
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]["hello"], lines[0]["proto"]), (1, H.PROTO))
        self.assertFalse(lines[0]["dll_ok"])
        self.assertIn("dll_error", lines[0])
        self.assertIn(lines[0]["bits"], (32, 64))

    def test_hello_then_serve_and_stdout_is_redirected(self):
        stdin = json.dumps({"id": 1, "cmd": "quit"}).encode() + b'\n'
        rc, lines, redirected, lib = self._run(["Fwlib32.dll"], stdin)
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0]["dll_ok"])
        self.assertTrue(lines[0]["dll"].endswith("Fwlib32.dll"))
        self.assertEqual(lines[1]["id"], 1)
        # print() が JSON の流れに紛れ込まないよう、標準出力は標準エラーへ向け直してある
        self.assertIs(redirected, sys.stderr)

    def test_find_dll_order(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(a) / "Fwlib32.dll").write_bytes(b"")
            (Path(b) / "Fwlib32.dll").write_bytes(b"")
            with mock.patch.object(H, "DLL_SEARCH", (b,)):
                self.assertEqual(H.find_dll(a), str(Path(a) / "Fwlib32.dll"))
                self.assertEqual(H.find_dll(""), str(Path(b) / "Fwlib32.dll"))
            with mock.patch.object(H, "DLL_SEARCH", ()):
                self.assertIsNone(H.find_dll(str(Path(a) / "ない")))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 以下はレビューの指摘（A-1・B-1〜B-8）への対応
# ---------------------------------------------------------------------------

class TestDirectoryEnd(unittest.TestCase):
    """A-1: 実機（31i）では alldir は最後のページの後に 3、subdir は 0・件数 0 で終わる。
    それ以外の戻り値は「読み切れていない」。"""

    def test_normal_end_is_ok(self):
        lib, hp, run = connected()
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertEqual((r["rc_sub"], r["rc_all"]), (0, 3))
        self.assertTrue(r["ok"])
        t = run.ok("prog_list", path="//CNC_MEM/", recursive=True)["tree"]
        self.assertTrue(all(x["ok"] for x in t))
        self.assertTrue(all(x["rc_all"] == 3 and x["rc_sub"] == 0 for x in t))

    def test_alldir_ending_with_zero_is_not_the_normal_end(self):
        lib, hp, run = connected({"alldir_end_rc": 0})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertFalse(r["ok"])
        self.assertEqual(r["rc_all"], 0)

    def test_other_nonzero_is_incomplete_and_rc_is_returned(self):
        for rc in (5, 6, 7, -1):
            lib, hp, run = connected({"force_rc": {"cnc_rdpdf_alldir": rc}})
            r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
            self.assertFalse(r["ok"], rc)
            self.assertEqual(r["rc_all"], rc)
        lib, hp, run = connected({"force_rc": {"cnc_rdpdf_subdir": 5}})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertFalse(r["ok"])
        self.assertEqual(r["rc_sub"], 5)

    def test_busy_is_retried_a_few_times_then_succeeds(self):
        lib, hp, run = connected({"busy_first": {"cnc_rdpdf_alldir": 3}})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["programs"]), 3)

    def test_busy_forever_gives_up_as_incomplete(self):
        lib, hp, run = connected({"force_rc": {"cnc_rdpdf_alldir": -1}})
        r = run.ok("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertFalse(r["ok"])
        self.assertEqual(r["rc_all"], -1)
        self.assertEqual(lib.called("cnc_rdpdf_alldir"), H.DIR_BUSY_TRIES)

    def test_lost_still_raises(self):
        lib, hp, run = connected({"force_rc": {"cnc_rdpdf_alldir": -16}})
        r = run("prog_list", path="//CNC_MEM/USER/PATH1/")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "lost")


class TestRealBehaviourFixes(unittest.TestCase):
    def test_rdparam_length_is_at_least_8_and_real_is_12(self):
        lib, hp, run = connected()
        run.ok("param_read", no=20)                         # バイト型 → 4+1=5 ではなく 8
        run.ok("param_read", no=1420, label="A1", axis=1)   # 実数型 → 12
        run.ok("param_read", no=0, label="L1", axis=0)      # ビット型 → 8
        by = {}
        for no, ln in lib.rdparam_lengths:
            by.setdefault(no, ln)
        self.assertEqual(by[20], 8)
        self.assertEqual(by[1420], 12)
        self.assertEqual(by[0], 8)
        self.assertGreaterEqual(min(ln for _, ln in lib.rdparam_lengths), 8)

    def test_param_length_rule_for_each_kind(self):
        for kind, size in H.DATA_SIZE.items():
            self.assertEqual(max(4 + size, H.PARAM_MIN_LENGTH), {"real": 12, "dword": 8}.get(kind, 8))

    def test_rdparainfo_failure_midway_is_reported(self):
        lib, hp, run = connected({"rdparainfo_fail_at": 3, "rdparainfo_fail_rc": 5})
        r = run.ok("param_info", count=5)
        self.assertFalse(r["complete"])
        self.assertEqual(r["last_rc"], 5)
        self.assertGreater(r["next_no"], 0)
        self.assertEqual(len(r["types"]), 10)               # 取れた分は残る

    def test_rdparainfo_normal_end_is_complete(self):
        lib, hp, run = connected()
        r = run.ok("param_info", count=5)
        self.assertTrue(r["complete"])
        self.assertEqual(r["last_rc"], 0)

    def test_rdaxisname_buffer_is_4x32_and_reads_names(self):
        lib, hp, run = connected()
        i = run.ok("identify")
        self.assertEqual(lib.axisname_buf_size, 4 * 32)
        self.assertEqual(i["axis_names"], ["X", "Y", "Z", "A", "B", "C"])

    def test_status_on_unsupported_family_is_raw_only(self):
        lib, hp = make()
        lib.m.cnc_type = b"0 "
        lib.m.status["aut"] = 1
        run = Run(hp)
        run.ok("connect", host="127.0.0.1")
        r = run.ok("status")
        self.assertFalse(r["interpreted"])
        self.assertEqual(r["text"], {})                     # 31i の並びで日本語にしない
        self.assertEqual(r["values"]["aut"], 1)

    def test_status_on_31i_is_interpreted(self):
        lib, hp, run = connected({"status": {"aut": 1}})
        r = run.ok("status")
        self.assertTrue(r["interpreted"])
        self.assertEqual(r["text"]["aut"], "メモリ")

    def test_timeouts_of_helper_are_shorter_than_clients(self):
        from nd287_app import focas_client as C
        self.assertGreater(C.DEFAULT_TIMEOUTS["prog_read"], H.PROG_READ_LIMIT)
        self.assertGreater(C.DEFAULT_TIMEOUTS["param_dump"], H.PARAM_DUMP_LIMIT)


class TestAllowListWatch(unittest.TestCase):
    """B-8: 許可一覧の見張りを強める。"""

    @classmethod
    def setUpClass(cls):
        import ast
        cls.src = Path(H.__file__).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)

    def test_lib_appears_only_in_api_init(self):
        import ast
        api = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
        init = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        inside = {id(x) for x in ast.walk(init)}
        hits = [n for n in ast.walk(self.tree)
                if (isinstance(n, ast.Attribute) and n.attr == "_lib")
                or (isinstance(n, ast.Name) and n.id == "_lib")]
        self.assertTrue(hits)                                # 見張りが空振りでないこと
        for n in hits:
            self.assertIn(id(n), inside, f"_lib が Api.__init__ の外にあります（{n.lineno} 行）")

    def test_every_cnc_pmc_name_in_code_is_allowed(self):
        import ast
        import re
        pat = re.compile(r"\b(?:cnc|pmc)_\w+")
        doc_ids = set()
        for n in ast.walk(self.tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body \
                    and isinstance(n.body[0], ast.Expr) \
                    and isinstance(getattr(n.body[0], "value", None), ast.Constant) \
                    and isinstance(n.body[0].value.value, str):
                doc_ids.add(id(n.body[0].value))
        names = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids:
                names |= set(pat.findall(n.value))
            elif isinstance(n, ast.Name):
                names |= set(pat.findall(n.id))
            elif isinstance(n, ast.Attribute):
                names |= set(pat.findall(n.attr))
        # 返事の項目名（DLL の関数ではない）。増えたときは、関数でないことを確かめてここへ足す
        data_keys = {"cnc_id", "cnc_id_rc", "cnc_type"}
        names -= data_keys
        self.assertGreater(len(names), 10)
        self.assertEqual(names - set(H.ALLOWED_FUNCS), set())
        self.assertEqual(names & set(FORBIDDEN), set())



VERIFIED = ["byte/none"]


class TestParamWrite(unittest.TestCase):
    """段4: cnc_wrparam の詰め方（設計 §11.2 の 13〜17）。偽物の機械だけ。実機にはつながない。"""

    def write(self, run, no, label, value, verified=VERIFIED):
        return run("param_write", no=no, label=label, value=value, verified=verified)

    def test_byte_is_packed_like_the_script_that_worked_on_the_machine(self):
        lib, hp, run = connected()
        r = self.write(run, 20, "", "4")
        self.assertTrue(r["ok"], r)
        # focas_wr0020.py（実機で成功）は struct.pack("<hhB", 20, 0, 値)・長さ 5
        self.assertEqual(bytes.fromhex(r["result"]["data_hex"]), struct.pack("<hhB", 20, 0, 4))
        self.assertEqual(r["result"]["length"], 5)
        self.assertEqual(lib.wr_lengths, [5])
        self.assertEqual(lib.m.writes, [(20, "", "4")])
        self.assertEqual(run.ok("param_read", no=20, label="")["value"], "4")

    def test_negative_byte_reads_back_the_same(self):
        lib, hp, run = connected()
        self.assertTrue(self.write(run, 20, "", "-49")["ok"])
        self.assertEqual(run.ok("param_read", no=20, label="")["value"], "-49")

    def test_other_types_are_packed_as_designed_when_verified(self):
        v = ["byte/none", "bit/A", "dword/A", "word/none", "real/A"]
        lib, hp, run = connected()
        r = self.write(run, 1815, "A4", "00100000", v)["result"]            # ビット 8桁 → 1バイト
        self.assertEqual(bytes.fromhex(r["data_hex"]), struct.pack("<hhB", 1815, 4, 0x20))
        r = self.write(run, 1821, "A4", "360000", v)["result"]              # 2ワード
        self.assertEqual(bytes.fromhex(r["data_hex"]), struct.pack("<hhi", 1821, 4, 360000))
        r = self.write(run, 1420, "A4", "8000.0", v)["result"]              # 実数: 値の整数と桁数
        self.assertEqual(bytes.fromhex(r["data_hex"]), struct.pack("<hhii", 1420, 4, 80000, 1))
        self.assertEqual(r["length"], 12)
        self.assertEqual(run.ok("param_read", no=1420, label="A4")["value"], "8000.0")
        self.assertEqual(run.ok("param_read", no=1815, label="A4")["value"], "00100000")

    def test_word_is_packed_as_signed_short(self):
        d = H.encode_param_value("word", "-2")
        self.assertEqual(d, struct.pack("<h", -2))

    def test_real_does_not_use_floating_point(self):
        self.assertEqual(H.encode_param_value("real", "0.1", 1), struct.pack("<ii", 1, 1))
        self.assertEqual(H.encode_param_value("real", "108.125", 3), struct.pack("<ii", 108125, 3))
        self.assertEqual(H.encode_param_value("real", "-1.0", 1), struct.pack("<ii", -10, 1))
        self.assertEqual(H.encode_param_value("real", "20000", 1), struct.pack("<ii", 200000, 1))

    def test_real_that_cannot_be_represented_is_refused_not_rounded(self):
        with self.assertRaises(H.FocasError) as cm:
            H.encode_param_value("real", "0.0005", 3)
        self.assertEqual(cm.exception.kind, "refused")
        self.assertIn("丸めて", cm.exception.detail["message"])
        lib, hp, run = connected()
        r = self.write(run, 1420, "A4", "8000.05", ["real/A"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "refused")
        self.assertEqual(lib.m.writes, [])                    # 1回も書いていない

    def test_out_of_range_values_are_refused(self):
        for kind, text in (("byte", "300"), ("byte", "128"), ("byte", "-129"), ("word", "40000"),
                           ("dword", "3000000000"), ("bit", "123456789"), ("bit", "0010000"),
                           ("bit", "0010000x"), ("byte", "1.5"), ("real", "abc")):
            with self.assertRaises(H.FocasError, msg=(kind, text)):
                H.encode_param_value(kind, text, 1)
        lib, hp, run = connected()
        r = self.write(run, 20, "", "300")
        self.assertFalse(r["ok"])
        self.assertEqual(lib.m.writes, [])

    def test_label_to_axis_field(self):
        self.assertEqual(H.axis_field(""), 0)
        self.assertEqual(H.axis_field("A4"), 4)
        self.assertEqual(H.axis_field("S1"), 1)
        for label in ("L1", "L2", "T1"):
            with self.assertRaises(H.FocasError) as cm:
                H.axis_field(label)
            self.assertEqual(cm.exception.kind, "refused")

    def test_axis_label_is_passed_as_the_axis_number(self):
        lib, hp, run = connected()
        r = self.write(run, 1020, "A4", "65", ["byte/A"])["result"]
        self.assertEqual(bytes.fromhex(r["data_hex"])[:4], struct.pack("<hh", 1020, 4))

    def test_l_and_t_labels_are_not_written(self):
        lib, hp, run = connected()
        r = self.write(run, 3202, "L1", "01000000", ["bit/L"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["reason"], "unconfirmed_axis")
        self.assertEqual(lib.m.writes, [])

    def test_unverified_type_is_never_written(self):
        lib, hp, run = connected()
        r = self.write(run, 1815, "A4", "00100000")         # bit/A は確認済みの表に無い
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "refused")
        self.assertEqual(r["error"]["reason"], "unverified_kind")
        self.assertEqual(r["error"]["key"], "bit/A")
        self.assertEqual(lib.called("cnc_wrparam"), 0)
        r = run("param_write", no=20, label="", value="4")   # verified を渡さない → 断る
        self.assertFalse(r["ok"])
        self.assertEqual(lib.called("cnc_wrparam"), 0)

    def test_kind_key_combines_basic_type_and_label_kind(self):
        self.assertEqual(H.kind_key(17, ""), "byte/none")
        self.assertEqual(H.kind_key(68, "A4"), "bit/A")
        self.assertEqual(H.kind_key(4103, "A4"), "real/A")
        self.assertEqual(H.kind_key(260, "S1"), "bit/S")        # 未解読ビット付きでも基本の型で決まる
        self.assertEqual(H.kind_key(325, "S1"), "byte/S")
        self.assertEqual(H.kind_key(0, "L1"), "bit/L")
        self.assertEqual(H.label_kind("T2"), "T")

    def test_nonzero_return_is_an_error_and_ew_prot_is_named(self):
        lib, hp, run = connected({"force_rc": {"cnc_wrparam": 7}})
        r = self.write(run, 20, "", "4")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "focas")
        self.assertEqual(r["error"]["rc"], 7)
        self.assertEqual(r["error"]["name"], "EW_PROT")
        self.assertEqual(r["error"]["func"], "cnc_wrparam")

    def test_pwe_off_returns_7_like_the_machine(self):
        lib, hp, run = connected({"pwe_required": True})
        r = self.write(run, 20, "", "4")
        self.assertEqual(r["error"]["rc"], 7)
        lib2, hp2, run2 = connected({"pwe_required": True,
                                     "alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        self.assertTrue(self.write(run2, 20, "", "4")["ok"])

    def test_ew_buffer_is_retried_but_ends_by_time_limit(self):
        lib, hp, run = connected({"write_rc_at": {"1": 10, "2": 10}})
        r = self.write(run, 20, "", "4")
        self.assertTrue(r["ok"], r)                     # 10 を2回返されても、待って送り直して通る
        self.assertEqual(lib.called("cnc_wrparam"), 3)
        # ずっと 10 を返す機械でも、上限時間で必ず抜ける
        t = [0.0]

        def clock():
            t[0] += 0.5
            return t[0]
        lib2 = FakeFwlib(scenario={"force_rc": {"cnc_wrparam": 10}})
        hp2 = H.Helper(lib2, sleep=lambda s: None, clock=clock)
        run2 = Run(hp2)
        run2.ok("connect", host="127.0.0.1", port=8193, timeout=1)
        r = run2("param_write", no=20, label="", value="4", verified=VERIFIED)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "timeout")
        self.assertLess(lib2.called("cnc_wrparam"), 100)

    def test_ew_busy_is_not_retried(self):
        lib, hp, run = connected({"force_rc": {"cnc_wrparam": -1}})
        r = self.write(run, 20, "", "4")
        self.assertEqual(r["error"]["rc"], -1)
        self.assertEqual(lib.called("cnc_wrparam"), 1)  # 書込は繰り返さない

    def test_lost_connection_is_lost(self):
        lib, hp, run = connected({"force_rc": {"cnc_wrparam": -16}})
        r = self.write(run, 20, "", "4")
        self.assertEqual(r["error"]["kind"], "lost")

    def test_unsupported_family_cannot_write(self):
        lib, hp, run = connected({"cnc_type": "0"})
        r = self.write(run, 20, "", "4")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["kind"], "unsupported")
        self.assertEqual(lib.called("cnc_wrparam"), 0)

    def test_param_write_needs_a_connection(self):
        lib, hp = make()
        r = Run(hp)("param_write", no=20, label="", value="4", verified=VERIFIED)
        self.assertEqual(r["error"]["kind"], "not_connected")

    def test_wrparam_is_only_called_by_param_write(self):
        import re
        src = Path(H.__file__).read_text(encoding="utf-8")
        calls = [m.start() for m in re.finditer(r"cnc_wrparam", src)]
        code_calls = [l for l in src.splitlines()
                      if "api.cnc_wrparam(" in l and not l.lstrip().startswith("#")]
        self.assertEqual(len(code_calls), 1)
        self.assertGreater(len(calls), 1)
