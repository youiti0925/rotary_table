# -*- coding: utf-8 -*-
"""偽物の機械（Fwlib32.dll の代わり）。試験専用。実機にはつながない。

本物と同じ名前の関数を持ち、ctypes の引数をそのまま受けて、**本物と同じバイトの並びで**
バッファに書く。並びは実機で動いた試験スクリプト（機械バックアップ_20261122/スクリプト）から
写したもの。だから構造体の詰め方・取り出し方まで 64bit の試験で確かめられる。

偽物の機械が持つもの: パラメータ（番号×ラベル→値、型コード）・プログラムの木・状態・
アラーム・登録の残り本数。起こせること（scenario）:
  lost_after       N 回呼んだあとは全部 -16（接続切れ）
  buffer_every     cnc_upload4 を N 回に 1 回 EW_BUFFER(10) にする
  hang_in          その関数で固まる（返事をしない）
  raise_in         その関数で例外を起こす（ヘルパーの想定外の例外の試験）
  connect_fail_count  最初の N 回は接続に失敗（-16）
  force_rc         {関数名: 戻り値} その関数は常にその戻り値
  dump_pad_lines   パラメータの控えに詰め物の行を足して大きくする
  extra_programs   PATH1 にプログラムを N 本足す
  alldir_end_rc    cnc_rdpdf_alldir が全部読み終えた後に返す戻り値（既定 3。実機の動き）
  busy_first       {関数名: N} その関数は最初の N 回だけ -1（処理中）を返す
  rdparainfo_fail_at  N 回目の cnc_rdparainfo から戻り値 {rdparainfo_fail_rc}（既定 5）
  pwe_required / write_ignore / write_store / write_alarms / write_side / write_rc_at
                   cnc_wrparam の振る舞い（_cnc_wrparam の説明を見る）。書いた値は丸ごと吸い出し（param_dat）にも出る
  dwn_rc / download_rc / pdf_del_rc / download_buffer_every  プログラム書込・削除の失敗（戻り値）の模擬
  alarms / status / cnc_id / remaining / cnc_type など
実機の動き（2026-11-22 の focas_dirs.py の記録）: cnc_rdpdf_alldir は最後のページの後に戻り値 3 で終わり、
cnc_rdpdf_subdir は戻り値 0・件数 0 で終わる。登録本数（cnc_rdproginfo）は「プログラム＋フォルダ」の数。
偽物の仮定（実機では未確認）: L1・L2 は 軸の欄 0/1→L1、2→L2 で読める。
"""

import base64
import ctypes
import os
import re
import struct
import threading
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EW_BUFFER = 10

DEFAULT_ALARMS = []


def _val(x):
    if hasattr(x, "_obj"):          # ctypes.byref(...)
        x = x._obj
    if hasattr(x, "value"):
        return x.value
    return x


def _target(x):
    return x._obj if hasattr(x, "_obj") else x


def _put(buf, data):
    ctypes.memmove(buf, data, len(data))


def split_param_text(text):
    """パラメータの控え（テキスト）→ {(番号, ラベル): 値の文字}"""
    out = {}
    for line in text.replace("\r", "").split("\n"):
        m = re.match(r"N(\d+)Q1(.*)", line)
        if not m:
            continue
        for g, _pm, v in re.findall(r"([A-Z]+\d*)?([PM])([-+0-9.]+)", m.group(2)):
            out[(int(m.group(1)), g or "")] = v
    return out


# cnc_rdsyssoft3 の生のバイト（2026-11-25 23号機から読んだもの、そのまま。14件×12バイト）
SYSSOFT_23 = bytes.fromhex(
    "0101473130330031382e30000201473130330031382e30000301473130330031382e3000"
    "1101473130330031382e30001201473130330031382e30002301473130330031382e3000"
    "2401473130330031382e30005101363057320030303032000102343041300031392e3000"
    "1102312e3030002020303100010339304530003030313600010636305638003030323100"
    "010936353646003030313400110936353645003030313100")


class FakeMachine:
    def __init__(self, scenario=None):
        sc = dict(scenario or {})
        self.scenario = sc
        txt = (FIXTURES / "focas_param_31i.DAT").read_bytes()
        self.param_dat = txt
        self.params = split_param_text(txt.decode("latin-1"))
        self.types = {}
        for i, line in enumerate((FIXTURES / "focas_types_31i.csv").read_text(
                encoding="utf-8").splitlines()):
            if i == 0 or not line.strip():
                continue
            a, b = line.split(",")
            self.types[int(a)] = int(b)
        self.cnc_type, self.mt_type = sc.get("cnc_type", "31").encode().ljust(2), b"M "
        self.series, self.version, self.axes_txt = b"G103", b"18.0", b"6 "
        self.max_axis = 6
        self.axis_names = [b"X", b"Y", b"Z", b"A", b"B", b"C"]
        self.syssoft = sc.get("syssoft", SYSSOFT_23)
        self.cnc_id = sc.get("cnc_id") or [0x3C7B5D01, 0x49C1F435, 0x5E28C389, 0x6592C486]
        self.status = {"hdck": 0, "tmmode": 0, "aut": 0, "run": 0, "motion": 0,
                       "mstb": 0, "emergency": 0, "alarm": 0, "edit": 0}
        self.status.update(sc.get("status") or {})
        # アラーム: (番号, 種類, 軸, 文言)
        self.alarms = [tuple(a) for a in sc.get("alarms", DEFAULT_ALARMS)]
        self.remaining = sc.get("remaining", 33)
        self.used, self.unused = 32500, 105000
        self.pitch = (b"%\nN11000Q0P0 \nN11001Q0P0 \nN11002Q0P0 \n%")
        self.macro = (b"%\nG10L85P500(00000000000000000) \nG10L85P501(00000000000000000) \n"
                      b"SETVN549[] \nM02\n%")
        self.tool = b"%\nG10G90P1R0.000 \nG10G90P2R0.000 \n%"
        self.writes = []                # cnc_wrparam で受けた (番号, ラベル, 値の文字)
        self.programs = {}
        self.extra_folders = {"//CNC_MEM/USER/PATH2/"}
        base = "//CNC_MEM/USER/PATH1/"
        self.add_program(base + "O901", b"%\nO0901(RT-135 TEST) \nG04X0. \nM30 \n%", "RT-135 TEST")
        self.add_program(base + "O1000", b"%\nO1000(MAIN) \nM98P901 \nM30 \n%", "MAIN")
        self.add_program(base + "O2100", b"%\nO2100(SUB) \nM99 \n%", "SUB")
        self.add_program("//CNC_MEM/USER/LIBRARY/O9001", b"%\nO9001(LIB) \nM99 \n%", "LIB")
        for i in range(int(sc.get("extra_programs", 0))):
            n = 3000 + i
            self.add_program(f"{base}O{n}", f"%\nO{n}(X) \nM99 \n%".encode(), "X")
        if sc.get("long_name"):
            self.add_program(base + "O" + "1" * 35, b"%\nO1(LONG) \n%", "L")
        for path, b64 in (sc.get("program_bytes") or {}).items():
            self.add_program(path, base64.b64decode(b64), "BIN")
        # 実機は 62本＋フォルダ10個で 72 だった。ルート（//CNC_MEM/）は数えない
        self.registered = sc.get("registered", len(self.programs) + len(
            self.all_folders() - {"//CNC_MEM/"}))

    def set_param(self, no, lab, text):
        """値を変える。丸ごと吸い出し（param_dat）の該当行の値も書き換える（行のほかの文字は保つ）。"""
        self.params[(no, lab)] = text
        key = f"N{no:05d}Q1"
        lines = self.param_dat.decode("latin-1").split(chr(10))
        for i, line in enumerate(lines):
            if not line.startswith(key):
                continue

            def rep(m):
                if (m.group(1) or "") == lab:
                    return f"{m.group(1) or ''}{m.group(2)}{text}"
                return m.group(0)
            lines[i] = key + re.sub(r"([A-Z]+[0-9]*)?([PM])([-+0-9.]+)", rep, line[len(key):])
            break
        self.param_dat = chr(10).join(lines).encode("latin-1")

    def add_program(self, path, data, comment=""):
        self.programs[path] = (data, comment)

    def all_folders(self):
        fs = set(self.extra_folders)
        for p in self.programs:
            parts = p.split("/")
            for i in range(3, len(parts)):
                fs.add("/".join(parts[:i]) + "/")
        return fs

    def folders_under(self, path):
        return sorted({f[len(path):].split("/")[0] for f in self.all_folders()
                       if f.startswith(path) and f != path})

    def programs_in(self, path):
        out = []
        for p, (data, comment) in sorted(self.programs.items()):
            if p.startswith(path) and "/" not in p[len(path):]:
                out.append((p[len(path):], data, comment))
        return out

    def dump(self, code):
        if code == 2:
            data = self.param_dat
            pad = int(self.scenario.get("dump_pad_lines", 0))
            if pad:
                body = data.rstrip(b"%").rstrip(b"\n")
                filler = b"".join(b"N09999Q1P0 \n" for _ in range(pad))
                data = body + b"\n" + filler + b"%"
            return data
        return {3: self.pitch, 4: self.macro, 1: self.tool}.get(code)


class _Fn:
    """ctypes の関数のように restype を付けられる呼び出し口。"""

    def __init__(self, owner, name, impl):
        self.owner, self.name, self.impl = owner, name, impl
        self.restype = None

    def __call__(self, *args):
        return self.owner._dispatch(self, args)


class FakeFwlib:
    FUNCS = ("cnc_allclibhndl3", "cnc_freelibhndl", "cnc_sysinfo", "cnc_rdcncid",
             "cnc_rdaxisname", "cnc_statinfo", "cnc_rdalmmsg", "cnc_rdproginfo",
             "cnc_rdsyssoft3",
             "cnc_rdpdf_subdir", "cnc_rdpdf_alldir", "cnc_upstart4", "cnc_upload4",
             "cnc_upend4", "cnc_rdparainfo", "cnc_rdparam",
             "cnc_wrparam",     # 書く関数はこれだけ（ヘルパーが呼べる）
             # 本物の DLL にはあるが、ヘルパーは呼べない（呼ばれたら試験が気づく）
             # プログラム書込・削除（prog_write だけが使う。2026-11-22 実機で確認した手順）
             "cnc_dwnstart4", "cnc_download4", "cnc_dwnend4", "cnc_pdf_del",
             "cnc_start", "cnc_wrmacro",
             "cnc_search", "pmc_wrpmcrng")

    def __init__(self, machine=None, scenario=None, calls_log=None):
        self.m = machine or FakeMachine(scenario)
        self.sc = self.m.scenario
        self.calls = []                 # (関数名, 要約)
        self.count = 0
        self.handle = None
        self.connect_tries = 0
        self.upload_pos = None
        self.upload_n = 0
        self.busy_left = dict(self.sc.get("busy_first") or {})
        self.rdparainfo_calls = 0
        self.rdparam_lengths = []
        self.axisname_buf_size = None
        self.calls_log = calls_log
        self.dwn = None                 # 書込中 [フォルダ, 受けたバイト]
        self.dwn_n = 0
        self.host = None
        self._hang = threading.Event()
        for name in self.FUNCS:
            impl = getattr(self, "_" + name, None)
            setattr(self, name, _Fn(self, name, impl))

    # --- 呼び出しの共通処理 --------------------------------------------------

    def _dispatch(self, fn, args):
        self.count += 1
        self.calls.append(fn.name)
        if self.calls_log:
            with open(self.calls_log, "a", encoding="ascii") as f:
                f.write(fn.name + "\n")
        sc = self.sc
        if sc.get("hang_in") == fn.name:
            self._hang.wait(3600)
        if sc.get("raise_in") == fn.name:
            raise RuntimeError("偽物の DLL が壊れた")
        if fn.impl is None:
            return 1                    # EW_FUNC: 実装していない
        if fn.name != "cnc_freelibhndl" and "lost_after" in sc and self.count > sc["lost_after"]:
            return -16
        if fn.name in (sc.get("force_rc") or {}):
            return sc["force_rc"][fn.name]
        if self.busy_left.get(fn.name, 0) > 0:
            self.busy_left[fn.name] -= 1
            return -1
        if fn.name not in ("cnc_allclibhndl3", "cnc_freelibhndl") and self.handle is None:
            return -8
        return fn.impl(*args)

    def called(self, name):
        return self.calls.count(name)

    # --- 接続 -----------------------------------------------------------------

    def _cnc_allclibhndl3(self, ip, port, timeout, h):
        self.connect_tries += 1
        self.host = bytes(ip).decode("ascii") if isinstance(ip, bytes) else str(ip)
        if self.connect_tries <= int(self.sc.get("connect_fail_count", 0)):
            return -16
        if "connect_rc" in self.sc:
            return self.sc["connect_rc"]
        _target(h).value = 1
        self.handle = 1
        return 0

    def _cnc_freelibhndl(self, h):
        self.handle = None
        return 0

    # --- 識別・状態 ---------------------------------------------------------------

    def _cnc_sysinfo(self, h, buf):
        m = self.m
        _put(buf, struct.pack("<hh2s2s4s4s2s", 0, m.max_axis, m.cnc_type, m.mt_type,
                              m.series, m.version, m.axes_txt))
        return 0

    def _cnc_rdcncid(self, h, arr):
        for i, w in enumerate(self.m.cnc_id):
            arr[i] = w
        return 0

    def _cnc_rdaxisname(self, h, n, buf):
        self.axisname_buf_size = ctypes.sizeof(buf)
        names = self.m.axis_names
        _put(buf, b"".join(nm + b"\0" for nm in names))
        _target(n).value = len(names)
        return 0

    def _cnc_rdsyssoft3(self, h, path, num, start, buf):
        rc = self.sc.get("syssoft_rc", 0)
        if rc:
            return rc
        raw = self.m.syssoft
        n = min(_val(num), len(raw) // 12)
        _put(buf, raw[:n * 12])
        _target(num).value = n
        return 0

    def _cnc_statinfo(self, h, arr):
        order = ("hdck", "tmmode", "aut", "run", "motion", "mstb", "emergency", "alarm", "edit")
        for i, k in enumerate(order):
            arr[i] = self.m.status[k]
        return 0

    def _cnc_rdalmmsg(self, h, kind, num, buf):
        want = _val(num)
        out = b""
        shown = self.m.alarms[:max(0, want)]
        for no, typ, axis, text in shown:
            raw = text.encode("cp932")[:32]
            out += (struct.pack("<ihhhh", no, typ, axis, 0, len(raw)) + raw.ljust(32, b"\0"))
        _put(buf, out)
        _target(num).value = len(shown)
        return 0

    def _cnc_rdproginfo(self, h, kind, size, buf):
        m = self.m
        _put(buf, struct.pack("<hhii", m.registered, m.remaining, m.used, m.unused))
        return 0

    # --- プログラム一覧 --------------------------------------------------------------

    @staticmethod
    def _req_path(req):
        raw = req.raw
        return raw[:212].split(b"\0")[0].decode("ascii"), raw[212:]

    def _cnc_rdpdf_subdir(self, h, num, req, res):
        path, tail = self._req_path(req)
        start, _ = struct.unpack("<hh", tail[:4])
        subs = self.m.folders_under(path)[start:start + 10]
        out = b""
        for s in subs:
            out += struct.pack("<i", 0) + s.encode("ascii")[:36].ljust(36, b"\0")
        _put(res, out)
        _target(num).value = len(subs)
        return 0

    def _cnc_rdpdf_alldir(self, h, num, req, res):
        path, tail = self._req_path(req)
        start = struct.unpack("<h", tail[:2])[0]
        items = self.m.programs_in(path)[start:start + 10]
        out = b""
        for name, data, comment in items:
            e = bytearray(128)
            struct.pack_into("<h", e, 0, 1)
            struct.pack_into("<5h", e, 2, 2026, 11, 22, 16, 4)
            struct.pack_into("<i", e, 20, len(data))
            e[28:64] = name.encode("ascii")[:36].ljust(36, b"\0")
            e[64:116] = comment.encode("cp932")[:52].ljust(52, b"\0")
            out += bytes(e)
        _put(res, out)
        _target(num).value = len(items)
        if not items:
            return int(self.sc.get("alldir_end_rc", 3))     # 実機: 最後のページの後は 3
        return 0

    # --- 吸い出し ---------------------------------------------------------------------

    def _cnc_upstart4(self, h, code, path):
        code = _val(code)
        if code == 0:
            p = bytes(path).decode("ascii")
            if p not in self.m.programs:
                return 5
            data = self.m.programs[p][0]
        else:
            data = self.m.dump(code)
            if data is None:
                return 6                # EW_NOOPT（ワーク原点オフセットで出た【事実】）
        self.upload_pos = [data, 0]
        return 0

    def _cnc_upload4(self, h, ln, buf):
        self.upload_n += 1
        be = int(self.sc.get("buffer_every", 0))
        if be and self.upload_n % be == 0:
            return EW_BUFFER
        if self.upload_pos is None:
            return 5
        data, pos = self.upload_pos
        want = _val(ln)
        chunk = data[pos:pos + want]
        self.upload_pos[1] = pos + len(chunk)
        _put(buf, chunk)
        _target(ln).value = len(chunk)
        return 0

    def _cnc_upend4(self, h):
        self.upload_pos = None
        return 0

    # --- パラメータ -----------------------------------------------------------------------

    def _cnc_rdparainfo(self, h, start, count, buf):
        self.rdparainfo_calls += 1
        fail_at = self.sc.get("rdparainfo_fail_at")
        if fail_at and self.rdparainfo_calls >= fail_at:
            return int(self.sc.get("rdparainfo_fail_rc", 5))
        s = _val(start)
        n = _val(count)
        nos = sorted(no for no in self.m.types if no >= s)
        mine = nos[:n]
        nxt = nos[n] if len(nos) > n else 0
        out = struct.pack("<hhh", len(mine), 0, nxt)
        for no in mine:
            out += struct.pack("<hh", no, self.m.types[no])
        _put(buf, out)
        return 0

    def _label_for(self, no, axis):
        ty = self.m.types.get(no)
        if ty is None:
            return None
        if (no, "") in self.m.params and axis == 0:
            return ""
        for lab, k in (("A", axis), ("S", axis)):
            if (no, f"{lab}{k}") in self.m.params and k > 0:
                return f"{lab}{k}"
        if axis in (0, 1) and (no, "L1") in self.m.params:
            return "L1"
        if axis == 2 and (no, "L2") in self.m.params:
            return "L2"
        return None

    def _cnc_rdparam(self, h, no, axis, length, buf):
        no, axis, length = _val(no), _val(axis), _val(length)
        self.rdparam_lengths.append((no, length))
        ty = self.m.types.get(no)
        lab = self._label_for(no, axis)
        if ty is None or lab is None:
            return 3
        code = ty
        base = ("bit", "byte", "word", "dword")[code & 3]
        kind = "real" if code & 0x1000 else base
        size = {"bit": 1, "byte": 1, "word": 2, "dword": 4, "real": 8}[kind]
        if length < 4 + size:
            return 2
        text = self.m.params[(no, lab)]
        if kind == "bit":
            data = struct.pack("<B", int(text, 2))
        elif kind == "byte":
            data = struct.pack("<b", int(text))
        elif kind == "word":
            data = struct.pack("<h", int(text))
        elif kind == "dword":
            data = struct.pack("<i", int(text))
        else:
            dec = len(text.split(".")[1]) if "." in text else 0
            data = struct.pack("<ii", int(text.replace(".", "")), dec)
        _put(buf, struct.pack("<hh", no, axis) + data)
        return 0

    # --- パラメータの書込（cnc_wrparam。実機で動いた形: 先頭 <hh(番号, 軸) ＋ データ）--------------

    def _cnc_wrparam(self, h, length, buf):
        """本物と同じバイトの並びで受ける。長さは 4 ＋ データの大きさ（バイト型は 5。実機で成功した形）。
        scenario:
          pwe_required   True なら、アラームに SW0100（種類0・番号100）が無いとき戻り値 7
          write_ignore   [番号…] 戻り値 0 を返すが値は変えない（書けたふりをする機械）
          write_store    {番号: 値の文字} 書いた値と違う値が入る
          write_alarms   {番号: [[番号, 種類, 軸, 文言]…]} その番号を書くとアラームが増える（PW0000 など）
          write_side     {番号: [[番号, ラベル, 値の文字]…]} その番号を書くと別の番号も変わる
          write_rc_at    {N: 戻り値} N 回目の書込はその戻り値（書き込まない）
        """
        sc = self.sc
        length = _val(length)
        raw = buf.raw[:length]
        self.write_n = getattr(self, "write_n", 0) + 1
        self.wr_lengths = getattr(self, "wr_lengths", [])
        self.wr_lengths.append(length)
        rc_at = {int(k): v for k, v in (sc.get("write_rc_at") or {}).items()}
        if self.write_n in rc_at:
            return rc_at[self.write_n]
        if sc.get("pwe_required") and not any(a[0] == 100 and a[1] == 0 for a in self.m.alarms):
            return 7
        no, axis = struct.unpack("<hh", raw[:4])
        ty = self.m.types.get(no)
        lab = self._label_for(no, axis)
        if ty is None or lab is None:
            return 3
        base = ("bit", "byte", "word", "dword")[ty & 3]
        kind = "real" if ty & 0x1000 else base
        size = {"bit": 1, "byte": 1, "word": 2, "dword": 4, "real": 8}[kind]
        if length != 4 + size:
            return 2                        # EW_LENGTH
        d = raw[4:]
        if kind == "bit":
            text = format(d[0], "08b")
        elif kind == "byte":
            text = str(struct.unpack("<b", d)[0])
        elif kind == "word":
            text = str(struct.unpack("<h", d)[0])
        elif kind == "dword":
            text = str(struct.unpack("<i", d)[0])
        else:
            prm, dec = struct.unpack("<ii", d)
            n = str(abs(prm)).rjust(dec + 1, "0") if dec else str(abs(prm))
            text = ("-" if prm < 0 else "") + (f"{n[:-dec]}.{n[-dec:]}" if dec else n)
        self.m.writes.append((no, lab, text))
        if no in (sc.get("write_ignore") or []):
            return 0
        store = {int(k): v for k, v in (sc.get("write_store") or {}).items()}
        self.m.set_param(no, lab, store.get(no, text))
        for k, side in (sc.get("write_side") or {}).items():
            if int(k) == no:
                for n2, l2, v2 in side:
                    self.m.set_param(int(n2), l2, v2)
        for k, al in (sc.get("write_alarms") or {}).items():
            if int(k) == no:
                self.m.alarms += [tuple(a) for a in al]
        return 0

    # --- プログラムの書込・削除（実機で動いた形: フォルダを渡して流し込む。2026-11-22）---------------
    # 機械の中は「%」＋送ったもの（読み戻しが 1 バイト一致した【事実】）。名前は O<数字>（先頭の 0 なし）。
    # 同じ名前があるときの戻り値は 5（偽物の仮定。実機では未確認。アプリは先に一覧で調べて書かない）。

    def _cnc_dwnstart4(self, h, code, path):
        if "dwn_rc" in self.sc:
            return self.sc["dwn_rc"]
        self.dwn = [bytes(path).decode("ascii"), bytearray()]
        return 0

    def _cnc_download4(self, h, ln, buf):
        self.dwn_n += 1
        if "download_rc" in self.sc:
            return self.sc["download_rc"]
        be = int(self.sc.get("download_buffer_every", 0))
        if be and self.dwn_n % be == 0:
            return EW_BUFFER
        if self.dwn is None:
            return 5
        n = _val(ln)
        self.dwn[1] += buf.raw[:n]
        return 0

    def _cnc_dwnend4(self, h):
        if self.dwn is None:
            return 0
        folder, data = self.dwn
        self.dwn = None
        m = re.search(rb"O0*(\d+)", bytes(data))
        if not m:
            return 5
        path = f"{folder}O{int(m.group(1))}"
        if path in self.m.programs:
            return 5
        self.m.add_program(path, b"%" + bytes(data), "")
        self.m.registered += 1
        self.m.remaining = max(0, self.m.remaining - 1)
        self.m.written_programs = getattr(self.m, "written_programs", []) + [path]
        return 0

    def _cnc_pdf_del(self, h, path):
        if "pdf_del_rc" in self.sc:
            return self.sc["pdf_del_rc"]
        p = bytes(path).decode("ascii")
        if p not in self.m.programs:
            return 5
        del self.m.programs[p]
        self.m.registered -= 1
        self.m.remaining += 1
        self.m.deleted_programs = getattr(self.m, "deleted_programs", []) + [p]
        return 0
