# -*- coding: utf-8 -*-
"""FOCAS ヘルパー（32bit Python の別プロセスで動かす。DLL を呼ぶ唯一の場所）。

設計: docs/設計_FOCAS基本.md §4〜§6。

- アプリ本体（64bit）とは標準入出力に JSON を1行ずつ流してやり取りする。
- 標準ライブラリだけ。**nd287_app のほかのファイルを import しない**
  （exe にしたとき、32bit Python から nd287_app は見えないため）。
- import しただけでは DLL を読まない（試験が普通のモジュールとして読み込む）。
- 書く DLL 関数は **cnc_wrparam（パラメータ1件の書込）だけ**。削除・プログラム書込・
  運転開始・PMC・マクロ書込は ALLOWED_FUNCS に入れていない（一覧に無い名前は Api から
  取り出せない）。cnc_wrparam は「確認済みの型」（verified）の番号にしか使わない。
- 本番のヘルパーに試験用の抜け道（偽物を読む引数）は無い。偽物を差し込むのは
  tests/fake_focas_helper.py だけで、serve(lib=...) を直接呼ぶ。

構造体の大きさとオフセットは、実機で動いた試験スクリプト
（機械バックアップ_20261122/スクリプト/ focas_*.py, param_csv.py, ptype_probe.py）から写した。
"""

import base64
import ctypes
import hashlib
import json
import os
import re
import struct
import sys
import time
import traceback
from decimal import Decimal

PROTO = 1

# 呼べる DLL 関数の一覧。ここに無い名前は取り出せない。
# 書く関数は cnc_wrparam だけ。cnc_start・pmc_wrpmcrng・cnc_search・cnc_wrmacro・
# cnc_dwn*・cnc_pdf_del などは入れない（試験が見張る）。
ALLOWED_FUNCS = (
    "cnc_allclibhndl3", "cnc_freelibhndl",
    "cnc_sysinfo", "cnc_rdcncid", "cnc_rdaxisname",
    "cnc_statinfo", "cnc_rdalmmsg",
    "cnc_rdproginfo", "cnc_rdpdf_subdir", "cnc_rdpdf_alldir",
    "cnc_upstart4", "cnc_upload4", "cnc_upend4",
    "cnc_rdparainfo", "cnc_rdparam",
    "cnc_wrparam",
)

# 戻り値の名前。実機で見たのは 0・6・10 だけで、残りは仕様の知識（未確認）。
RC_NAMES = {
    0: "EW_OK", 3: "EW_NUMBER", 4: "EW_ATTRIB", 5: "EW_DATA", 6: "EW_NOOPT",
    7: "EW_PROT", 10: "EW_BUFFER", 12: "EW_MODE", 13: "EW_REJECT",
    15: "EW_ALARM", -1: "EW_BUSY", -8: "EW_HANDLE", -16: "EW_SOCKET",
}
RC_LOST = (-8, -16)
RC_BUFFER = 10
RC_BUSY = -1
# フォルダの読み終わり。実機（31i）の実行記録（2026-11-22 focas_dirs.py）:
#   cnc_rdpdf_alldir は最後のページの後に戻り値 3 を返して終わる。
#   cnc_rdpdf_subdir は戻り値 0・件数 0 で終わる。
# これ以外の戻り値で止まったら「読み切れていない」（不完全）。
ALLDIR_END_RC = 3
SUBDIR_END_RC = 0
DIR_BUSY_TRIES = 5          # 処理中(-1)のとき、短くあけて読み直す回数
PROG_READ_LIMIT = 60        # プログラム1本を読む時間の上限[秒]（クライアントはこれより長く待つ）
PARAM_DUMP_LIMIT = 150
PARAM_WRITE_LIMIT = 5       # 書込が EW_BUFFER（待って再送）を返し続けたとき、この秒数で抜ける

DUMP_KINDS = {"param": 2, "pitch": 3, "macro": 4, "tool": 1, "workofs": 5}

STATUS_FIELDS = ("hdck", "tmmode", "aut", "run", "motion", "mstb",
                 "emergency", "alarm", "edit")
# 値の読み。仕様の知識で、実機で見たのは「MDI・リセット・アラーム1」だけ（設計 §6.2）。
STATUS_TEXT = {
    "aut": {0: "MDI", 1: "メモリ", 3: "編集", 4: "ハンドル", 5: "ジョグ",
            9: "原点復帰", 10: "リモート"},
    "run": {0: "リセット", 1: "停止", 2: "一時停止", 3: "運転中", 4: "MSTR"},
    "motion": {0: "なし", 1: "移動中", 2: "ドウェル"},
    "emergency": {0: "なし", 1: "非常停止", 2: "リセット中"},
    "alarm": {0: "なし", 1: "アラーム", 2: "電池低下"},
}
# アラームの種類の番号（仕様の知識。実機で見たのは 種類0・番号100＝SW0100 だけ）
ALARM_PREFIX = {0: "SW", 1: "PW", 2: "IO", 3: "PS", 4: "OT", 5: "OH",
                6: "SV", 7: "SR", 8: "MC", 9: "SP", 10: "DS"}

DEFAULT_FAMILIES = {"31": "31i"}
DLL_NAME = "Fwlib32.dll"
DLL_SEARCH = (r"C:\Program Files (x86)\FANUC SERVO GUIDE", r"C:\Windows\SysWOW64")


class FocasError(Exception):
    """命令の失敗。kind と詳細がそのまま返事の error になる。"""

    def __init__(self, kind, **detail):
        super().__init__(kind)
        self.kind = kind
        self.detail = detail

    def as_dict(self):
        d = {"kind": self.kind}
        d.update(self.detail)
        return d


def rc_name(rc):
    return RC_NAMES.get(rc, "?")


def _fail(func, rc):
    """戻り値が 0 以外のときのエラー。接続が切れた戻り値は lost にする。"""
    kind = "lost" if rc in RC_LOST else "focas"
    return FocasError(kind, func=func, rc=rc, name=rc_name(rc))


# ---------------------------------------------------------------------------
# DLL 関数の窓口
# ---------------------------------------------------------------------------

class Api:
    """ALLOWED_FUNCS の関数だけを取り出せる窓口。全関数に restype=c_short を付ける
    （付けないと成功 0 が大きな数に見える【事実】）。"""

    def __init__(self, lib):
        self._lib = lib
        self._fn = {}
        self.missing = []
        for name in ALLOWED_FUNCS:
            try:
                fn = getattr(lib, name)
            except AttributeError:
                self.missing.append(name)
                continue
            fn.restype = ctypes.c_short
            self._fn[name] = fn

    def __getattr__(self, name):
        if name.startswith("_") or name not in ALLOWED_FUNCS:
            raise AttributeError(f"{name} は呼べる関数の一覧にありません")
        try:
            return self._fn[name]
        except KeyError:
            raise AttributeError(f"{name} はこの DLL にありません") from None


# ---------------------------------------------------------------------------
# 構造体の読み取り（試験が直接呼ぶ）
# ---------------------------------------------------------------------------

def _txt(b):
    return bytes(b).split(b"\0")[0].decode("ascii", "replace").strip()


def parse_sysinfo(b):
    """cnc_sysinfo の18バイト。[2:4]最大軸数 [4:6]機種 [6:8]M/T [8:12]系列 [12:16]版 [16:18]軸数（文字）"""
    b = bytes(b)
    axes_txt = _txt(b[16:18])
    return {
        "max_axis": struct.unpack("<h", b[2:4])[0],
        "cnc_type": _txt(b[4:6]),
        "mt_type": _txt(b[6:8]),
        "series": _txt(b[8:12]),
        "version": _txt(b[12:16]),
        "axes": int(axes_txt) if axes_txt.isdigit() else None,
        "axes_text": axes_txt,
    }


def parse_statinfo(shorts):
    """cnc_statinfo の short の並び。値は生の数値と日本語の読みを両方返す。"""
    values = {name: int(shorts[i]) for i, name in enumerate(STATUS_FIELDS)}
    text = {}
    for name, table in STATUS_TEXT.items():
        v = values[name]
        text[name] = table.get(v, f"不明({v})")
    return {"values": values, "text": text}


def alarm_code(typ, no):
    prefix = ALARM_PREFIX.get(typ)
    return f"{prefix}{no:04d}" if prefix else f"種類{typ}-{no:04d}"


def parse_alarms(buf, count):
    """cnc_rdalmmsg: 1件44B。[0:4]番号 [4:6]種類 [6:8]軸 [10:12]文字数 [12:44]文言（cp932）"""
    out = []
    raw = bytes(buf)
    for i in range(max(0, min(count, len(raw) // 44))):
        e = raw[i * 44:(i + 1) * 44]
        no = struct.unpack("<i", e[0:4])[0]
        typ = struct.unpack("<h", e[4:6])[0]
        axis = struct.unpack("<h", e[6:8])[0]
        ln = struct.unpack("<h", e[10:12])[0]
        msg = e[12:12 + max(0, min(32, ln))].decode("cp932", "replace")
        out.append({"no": no, "type": typ, "axis": axis, "text": msg,
                    "code": alarm_code(typ, no)})
    return out


def decode_type(code):
    """パラメータの型コード。基本の型は下位2ビットと 0x1000（実数）から決める。
    0x10 0x20 0x40 は意味が未解読なので、付いていることだけ返す（禁止の理由にはしない）。"""
    code = int(code) & 0xFFFF
    base = ("bit", "byte", "word", "dword")[code & 3]
    real = bool(code & 0x1000)
    return {
        "code": code,
        "base": base,
        "kind": "real" if real else base,
        "real": real,
        "axis": bool(code & 0x4),
        "spindle": bool(code & 0x100),
        "unknown_bits": [b for b in (0x10, 0x20, 0x40) if code & b],
    }


DATA_SIZE = {"bit": 1, "byte": 1, "word": 2, "dword": 4, "real": 8}
PARAM_MIN_LENGTH = 8        # cnc_rdparam の長さの下限（実機で通った値）


def fmt_real(prm, dec):
    """値の整数と小数点以下の桁数 → 10進の文字（浮動小数点を使わない）。"""
    dec = max(0, int(dec))
    sign = "-" if prm < 0 else ""
    n = abs(int(prm))
    if dec == 0:
        return f"{sign}{n}"
    s = str(n).rjust(dec + 1, "0")
    return f"{sign}{s[:-dec]}.{s[-dec:]}"


def parse_param_data(kind, buf):
    """cnc_rdparam の返り。[0:2]番号 [2:4]軸 [4:]データ。"""
    b = bytes(buf)
    if kind == "bit":
        return format(b[4], "08b")
    if kind == "byte":
        return str(struct.unpack("<b", b[4:5])[0])        # 符号つき（-49 などが実在する）
    if kind == "word":
        return str(struct.unpack("<h", b[4:6])[0])
    if kind == "dword":
        return str(struct.unpack("<i", b[4:8])[0])
    if kind == "real":
        prm, dec = struct.unpack("<ii", b[4:12])
        return fmt_real(prm, dec)
    raise FocasError("bad_args", message=f"知らない型です: {kind}")


def axis_field(label):
    """ラベル → cnc_rdparam の軸の欄。なし→0、A4→4、S1→1。
    L・T（系統型）の指し方は未確認なので断る（読むときは axis を明示して試す）。"""
    label = (label or "").strip().upper()
    if label in ("", "-"):
        return 0
    if label[0] in "AS" and label[1:].isdigit():
        return int(label[1:])
    raise FocasError("refused", reason="unconfirmed_axis", label=label,
                     message=f"ラベル {label} の軸の指し方はまだ確かめていません")


LABEL_KINDS = ("L", "A", "S", "T")


def label_kind(label):
    """ラベル → ラベルの種類。なし→"none"、L1→"L"、A4→"A"、S1→"S"、T1→"T"。それ以外は "?"。"""
    label = (label or "").strip().upper()
    if label in ("", "-"):
        return "none"
    if label[0] in LABEL_KINDS and label[1:].isdigit():
        return label[0]
    return "?"


def kind_key(type_code, label):
    """「確認済みの型」の表で使う名前。基本の型（bit/byte/word/dword/real）＋ "/" ＋ ラベルの種類。
    例: byte/none（実機で書いたことがあるのはこれだけ【事実】）、real/A、bit/A。"""
    return f"{decode_type(type_code)['kind']}/{label_kind(label)}"


KIND_JA = {"bit": "ビット", "byte": "バイト", "word": "ワード", "dword": "2ワード", "real": "実数"}
LABEL_JA = {"none": "", "A": "軸型", "S": "主軸型", "L": "系統型", "T": "T型"}


def kind_label(key):
    """「確認済みの型」の名前（byte/none など）→ 利用者に見せる日本語。
    byte/none→「バイト」、dword/A→「2ワード・軸型」、bit/A→「ビット・軸型」、byte/S→「バイト・主軸型」。"""
    k, _, lab = str(key or "").partition("/")
    base = KIND_JA.get(k)
    if base is None:
        return str(key or "?")
    extra = LABEL_JA.get(lab, lab)
    return f"{base}・{extra}" if extra else base


def label_type_problem(type_code, label):
    """型コードの軸ビット（0x4）・主軸ビット（0x100）とラベル（A・S・なし）の食い違い。
    食い違いなら断る理由の文、合っていれば None。
    実機の 24,664 行で、A⇔0x4（主軸ビットなし）・S⇔0x104 は例外なく一致している【事実】。
    ラベルなしは軸ビットが付いていないこと。L・T（系統型）は型コードから判定できないので、
    ここでは見ない（ラベルを頼りに、軸の指し方が未確認として別に断る）。"""
    ty = decode_type(type_code)
    lk = label_kind(label)
    ax, sp = ty["axis"], ty["spindle"]
    if lk == "A" and not (ax and not sp):
        why = "軸型（A）のラベルなのに、型コードは軸型ではありません"
    elif lk == "S" and not (ax and sp):
        why = "主軸型（S）のラベルなのに、型コードは主軸型ではありません"
    elif lk == "none" and ax:
        why = "ラベルが無いのに、型コードは軸ごとの値（軸型・主軸型）です"
    else:
        return None
    return f"{why}（型コード 0x{ty['code']:X}・ラベル {label or '-'}）。番号とラベルの組が機械の型と食い違うので書きません"


def real_decimals(text):
    """実数の文字の小数点以下の桁数（"8000.000"→3、"8000"→0）。"""
    t = str(text).strip()
    return len(t.split(".", 1)[1]) if "." in t else 0


_INT_RE = re.compile(r"[+-]?[0-9]+")
_REAL_RE = re.compile(r"[+-]?[0-9]+([.][0-9]+)?")
INT_RANGE = {"byte": (-128, 127), "word": (-32768, 32767), "dword": (-2147483648, 2147483647)}


def encode_param_value(kind, text, dec=None):
    """書く値の文字 → cnc_wrparam のデータ部（番号と軸の4バイトは含まない）。

    浮動小数点を使わない。実数は「整数 = 値 × 10^桁数」にして、割り切れなければ断る
    （丸めて書かない。調査記録 §14 の 1000 倍ずれの事故と同じ種類の危険）。
    バイト型は読み戻しが符号つき（-128〜127）なので、その範囲だけ。128 以上の扱いは未確認。
    """
    t = str(text).strip()
    if kind == "bit":
        if not re.fullmatch(r"[01]{8}", t):
            raise FocasError("bad_args", message=f"ビット型は 0 と 1 の8桁で指定してください: {t!r}")
        return struct.pack("<B", int(t, 2))
    if kind in INT_RANGE:
        if not _INT_RE.fullmatch(t):
            raise FocasError("bad_args", message=f"整数で指定してください: {t!r}")
        v = int(t)
        lo, hi = INT_RANGE[kind]
        if not lo <= v <= hi:
            extra = "（128 以上の扱いは未確認です）" if kind == "byte" else ""
            raise FocasError("bad_args", message=f"{kind} 型に書ける範囲は {lo}〜{hi} です: {v}{extra}")
        return struct.pack({"byte": "<b", "word": "<h", "dword": "<i"}[kind], v)
    if kind == "real":
        if not _REAL_RE.fullmatch(t):
            raise FocasError("bad_args", message=f"数で指定してください: {t!r}")
        if dec is None:
            raise FocasError("bad_args", message="小数点以下の桁数が分かりません")
        if int(dec) < 0:
            raise FocasError("bad_args", reason="bad_decimals",
                             message=f"小数点以下の桁数が負です（{dec}）。この番号は書きません")
        scaled = Decimal(t) * (Decimal(10) ** int(dec))
        if scaled != scaled.to_integral_value():
            raise FocasError(
                "refused", reason="not_representable",
                message=f"{t} は小数点以下 {dec} 桁では書けません（丸めて書くことはしません）")
        n = int(scaled)
        if not -2147483648 <= n <= 2147483647:
            raise FocasError("bad_args", message=f"値が大きすぎます: {t}")
        return struct.pack("<ii", n, int(dec))
    raise FocasError("bad_args", message=f"知らない型です: {kind}")


def _check_path(p):
    if not isinstance(p, str) or not p.startswith("//") or len(p) > 200:
        raise FocasError("bad_args", message="プログラムの場所は //CNC_MEM/... の形で指定してください")
    if "\0" in p or ".." in p.split("/") or not p.isascii():
        raise FocasError("bad_args", message="プログラムの場所に使えない文字があります")
    return p


# ---------------------------------------------------------------------------
# 機種ごとの手順
# ---------------------------------------------------------------------------

class Family:
    name = ""
    implemented = False


class Family31i(Family):
    """31i（実機確認済み）。フォルダで管理する。*4 と cnc_rdpdf_* を使う。"""
    name = "31i"
    implemented = True

    def prog_info(self, hp):
        buf = ctypes.create_string_buffer(12)
        hp.check("cnc_rdproginfo", hp.read_rc("cnc_rdproginfo", hp.h, ctypes.c_short(0),
                                              ctypes.c_short(12), buf))
        reg, unreg, used, unused = struct.unpack("<hhii", buf.raw)
        return {"registered": reg, "remaining": unreg, "used": used, "unused": unused}

    def _subdirs(self, hp, path):
        out, start, rc = [], 0, 0
        while True:
            def make():
                num = ctypes.c_short(10)
                req = ctypes.create_string_buffer(
                    path.encode().ljust(212, b"\0")[:212] + struct.pack("<hh", start, 0), 216)
                res = ctypes.create_string_buffer(40 * 10)
                return (hp.h, ctypes.byref(num), req, res), (num, res)
            rc, (num, res) = hp.call_retry("cnc_rdpdf_subdir", make)
            if rc in RC_LOST:
                raise _fail("cnc_rdpdf_subdir", rc)
            if rc != 0 or num.value <= 0:
                return out, rc
            for i in range(min(num.value, 10)):
                e = res.raw[i * 40:(i + 1) * 40]
                out.append(e[4:40].split(b"\0")[0].decode("ascii", "replace"))
            start += num.value
            hp.guard(len(out))

    def _entries(self, hp, path):
        out, start, rc = [], 0, 0
        while True:
            def make():
                num = ctypes.c_short(10)
                req = ctypes.create_string_buffer(
                    path.encode().ljust(212, b"\0")[:212] + struct.pack("<hhhh", start, 1, 0, 0), 220)
                res = ctypes.create_string_buffer(128 * 10)
                return (hp.h, ctypes.byref(num), req, res), (num, res)
            rc, (num, res) = hp.call_retry("cnc_rdpdf_alldir", make)
            if rc in RC_LOST:
                raise _fail("cnc_rdpdf_alldir", rc)
            if rc != 0 or num.value <= 0:
                return out, rc
            for i in range(min(num.value, 10)):
                e = res.raw[i * 128:(i + 1) * 128]
                kind = struct.unpack("<h", e[0:2])[0]
                y, mo, d, hh, mi = struct.unpack("<5h", e[2:12])
                size = struct.unpack("<i", e[20:24])[0]
                out.append({
                    "kind": kind,
                    "name": e[28:64].split(b"\0")[0].decode("ascii", "replace"),
                    "size": size,
                    "date": f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mi:02d}",
                    "comment": e[64:116].split(b"\0")[0].decode("cp932", "replace"),
                })
            start += num.value
            hp.guard(len(out))

    def prog_list(self, hp, path, recursive):
        todo, tree = [path], []
        while todo:
            p = todo.pop(0)
            subs, rs = self._subdirs(hp, p)
            items, ra = self._entries(hp, p)
            tree.append({
                "path": p, "folders": subs,
                "programs": [x for x in items if x["kind"] == 1],
                "rc_sub": rs, "rc_all": ra,
                # 正常な終わり: subdir は 0（件数 0）、alldir は 3。それ以外は読み切れていない
                "ok": rs == SUBDIR_END_RC and ra == ALLDIR_END_RC,
            })
            if not recursive:
                break
            todo += [p + s + "/" for s in subs]
            if len(tree) > 500:
                raise FocasError("refused", message="フォルダが多すぎます（500個で打ち切りました）")
        return tree

    def param_info(self, hp, start, count, emit):
        """型の表を読む。戻り値は (型の並び, 終わり方)。終わり方は
        {"complete", "last_rc", "next_no"}。途中で 0 以外が返ったら complete=False。"""
        types, s, batches = [], int(start), 0
        seen = set()
        last_rc, next_no = 0, 0
        while True:
            n = ctypes.c_ushort(count)
            buf = ctypes.create_string_buffer(6 + 4 * count)
            rc = hp.read_rc("cnc_rdparainfo", hp.h, ctypes.c_short(s), n, buf)
            if rc in RC_LOST or (rc != 0 and batches == 0):
                raise _fail("cnc_rdparainfo", rc)
            if rc != 0:
                last_rc, next_no = rc, s       # s は読もうとして失敗した先頭の番号
                break
            info_no, prev_no, next_no = struct.unpack("<hhh", buf.raw[:6])
            added, last = 0, -1
            for i in range(count):
                no, ty = struct.unpack("<hh", buf.raw[6 + 4 * i:10 + 4 * i])
                # 実際より多く読んだ分は 0 で埋まる。番号が増えない所で打ち切る
                if i > 0 and no <= last:
                    break
                last = no
                if no in seen:
                    continue
                seen.add(no)
                types.append([no, ty & 0xFFFF])
                added += 1
            batches += 1
            if added == 0 or next_no <= 0 or next_no <= s:
                break
            s = next_no
            hp.guard(batches)
            emit({"event": "progress", "done": len(types), "total": 0, "note": "型情報"})
        for no, ty in types:
            hp.types[no] = ty
        return types, {"complete": last_rc == 0, "last_rc": last_rc, "next_no": next_no}

    def type_of(self, hp, no):
        """1番号の型コード。表に無ければ 1件だけ読んで調べる（ptype_probe と同じ呼び方）。"""
        if no in hp.types:
            return hp.types[no]
        n = ctypes.c_ushort(1)
        buf = ctypes.create_string_buffer(10)
        rc = hp.read_rc("cnc_rdparainfo", hp.h, ctypes.c_short(no), n, buf)
        hp.check("cnc_rdparainfo", rc)
        rno, ty = struct.unpack("<hh", buf.raw[6:10])
        if rno != no:
            raise FocasError("focas", func="cnc_rdparainfo", rc=3, name=rc_name(3),
                             message=f"番号 {no} の型が取れませんでした")
        hp.types[no] = ty & 0xFFFF
        return hp.types[no]

    def param_read(self, hp, no, label, axis):
        ty = decode_type(self.type_of(hp, no))
        kind = ty["kind"]
        ax = axis_field(label) if axis is None else int(axis)
        buf = ctypes.create_string_buffer(16)
        # 長さは実機で通った値に合わせる（バイト型を 8 で読んだ呼び方）。4+型の大きさが 8 に
        # 届かない型は 8 にする。5 で通るかは未確認。実数は 12。
        rc = hp.read_rc("cnc_rdparam", hp.h, ctypes.c_short(no), ctypes.c_short(ax),
                        ctypes.c_short(max(4 + DATA_SIZE[kind], PARAM_MIN_LENGTH)), buf)
        hp.check("cnc_rdparam", rc)
        return {"no": no, "label": label or "", "axis": ax, "kind": kind,
                "value": parse_param_data(kind, buf.raw), "type_code": ty["code"]}

    def param_write(self, hp, no, label, value, verified):
        """パラメータ1件を cnc_wrparam で書く。書いたかどうかの確かめ（前後の読み）は呼ぶ側がする。

        - 「確認済みの型」（verified）に無い型は書かずに断る（実機で書いたことがあるのは
          byte/none だけ【事実】）。
        - 系統型・T 型（L1 T1 など）は軸の指し方が未確認なので断る（axis_field）。
        - EW_BUFFER（10）は待って同じ分を送り直す【仕様】。cnc_wrparam では実機で確かめていない
          （未確認）。上限時間で必ず抜ける。
          EW_BUSY（-1）は書込をやり直さない（書けたかどうかは呼ぶ側が読み戻して決める）。
        """
        ty = decode_type(self.type_of(hp, no))
        kind = ty["kind"]
        key = f"{kind}/{label_kind(label)}"
        mis = label_type_problem(ty["code"], label)
        if mis:
            raise FocasError("refused", reason="label_type_mismatch", key=key, message=mis)
        if key not in verified:
            raise FocasError(
                "refused", reason="unverified_kind", key=key,
                message=f"この型（{kind_label(key)}）は、この機種ではまだ書いたことがありません。"
                        "先に『同じ値を書く確認』をしてください")
        ax = axis_field(label)
        dec = None
        if kind == "real":                       # 書く前に読んで、機械の小数点以下の桁数を得る
            buf = ctypes.create_string_buffer(16)
            rc = hp.read_rc("cnc_rdparam", hp.h, ctypes.c_short(no), ctypes.c_short(ax),
                            ctypes.c_short(12), buf)
            hp.check("cnc_rdparam", rc)
            dec = struct.unpack("<ii", buf.raw[4:12])[1]
        data = struct.pack("<hh", no, ax) + encode_param_value(kind, value, dec)
        t0 = hp.clock()
        while True:
            rc = hp.api.cnc_wrparam(hp.h, ctypes.c_short(len(data)),
                                    ctypes.create_string_buffer(data, len(data)))
            if rc != RC_BUFFER:
                break
            if hp.clock() - t0 > PARAM_WRITE_LIMIT:
                raise FocasError("timeout", func="cnc_wrparam", seconds=PARAM_WRITE_LIMIT)
            hp.sleep(0.02)                        # 待って同じ分を送り直す【仕様。cnc_wrparam では未確認】
        hp.check("cnc_wrparam", rc)
        return {"no": no, "label": label or "", "axis": ax, "kind": kind, "key": key,
                "value": str(value), "length": len(data), "data_hex": data.hex(), "rc": rc}

    def upload(self, hp, code, path, limit, emit, note):
        """cnc_upstart4 / cnc_upload4 / cnc_upend4 で読み切る。末尾 % で終わる。"""
        rc = hp.api.cnc_upstart4(hp.h, ctypes.c_short(code), path.encode("ascii"))
        if rc != 0:
            raise _fail("cnc_upstart4", rc)
        data, last, complete, empties = bytearray(), 0, False, 0
        t0 = hp.clock()
        chunks = 0
        try:
            while True:
                if hp.clock() - t0 > limit:
                    raise FocasError("timeout", func="cnc_upload4", seconds=limit)
                ln = ctypes.c_long(1280)
                buf = ctypes.create_string_buffer(1281)
                last = hp.api.cnc_upload4(hp.h, ctypes.byref(ln), buf)
                if last == RC_BUFFER:                # 待って同じ所を読み直す【事実】
                    hp.sleep(0.02)
                    continue
                if last != 0:
                    break
                chunk = buf.raw[:max(0, min(ln.value, 1280))]
                data += chunk
                chunks += 1
                empties = empties + 1 if not chunk else 0
                if empties > 1000:
                    break
                if len(data) > 1 and chunk.rstrip(b"\0").endswith(b"%"):
                    complete = True
                    break
                if chunks % 20 == 0:
                    emit({"event": "progress", "done": len(data), "total": 0, "note": note})
        finally:
            end_rc = hp.api.cnc_upend4(hp.h)
        if not complete:
            raise _fail("cnc_upload4", last if last != 0 else -99)
        return bytes(data), last, end_rc


class Family0iD(Family):
    name = "0i-D"


class Family18i(Family):
    name = "18i"


FAMILY_CLASSES = {"31i": Family31i, "0i-D": Family0iD, "18i": Family18i}


# ---------------------------------------------------------------------------
# ヘルパー本体
# ---------------------------------------------------------------------------

class Helper:
    """1本のスレッドで動く。機械との接続（ハンドル）は取ったスレッドで使う。"""

    def __init__(self, lib, sleep=time.sleep, clock=time.monotonic, families=None):
        self.api = Api(lib)
        self.sleep = sleep
        self.clock = clock
        self.families = dict(families or DEFAULT_FAMILIES)
        self.h = None
        self.family = None
        self.info = None
        self.types = {}
        self._guard_limit = 100000

    # --- 呼び出しの下請け -------------------------------------------------

    def read_rc(self, name, *args):
        """読む関数の呼び出し。EW_BUSY（処理中）は少し待って1回だけ読み直す。"""
        fn = getattr(self.api, name)
        rc = fn(*args)
        if rc == RC_BUSY:
            self.sleep(0.2)
            rc = fn(*args)
        return rc

    def call_retry(self, name, make_args, tries=DIR_BUSY_TRIES):
        """処理中（-1）なら短くあけて読み直す。読み直すたびに make_args() で引数を作り直す
        （前の呼び出しが書き換えた件数の欄を持ち越さない）。戻り値は (rc, make_args の2つ目)。"""
        fn = getattr(self.api, name)
        for i in range(max(1, tries)):
            args, extra = make_args()
            rc = fn(*args)
            if rc != RC_BUSY or i == tries - 1:
                return rc, extra
            self.sleep(0.2)

    def check(self, func, rc):
        if rc != 0:
            raise _fail(func, rc)

    def guard(self, n):
        if n > self._guard_limit:
            raise FocasError("refused", message="読み取りが終わらないので打ち切りました")

    def need_handle(self):
        if self.h is None:
            raise FocasError("not_connected", message="機械につながっていません")

    def need_family(self):
        self.need_handle()
        if self.family is None or not FAMILY_CLASSES[self.family].implemented:
            t = (self.info or {}).get("cnc_type", "?")
            raise FocasError("unsupported", cnc_type=t,
                             message=f"この機種（{t}i）はまだ対応していません")
        return FAMILY_CLASSES[self.family]()

    def close(self):
        if self.h is not None:
            try:
                self.api.cnc_freelibhndl(self.h)
            except Exception:
                pass
            self.h = None
            self.family = None
            self.info = None
            self.types = {}

    # --- 命令 ------------------------------------------------------------

    def cmd_connect(self, a, emit):
        host = a.get("host")
        if not isinstance(host, str) or not host or not host.isascii() or len(host) > 100:
            raise FocasError("bad_args", message="機械の IP アドレスが正しくありません")
        port = int(a.get("port", 8193))
        timeout = int(a.get("timeout", 10))
        if timeout < 1:                 # 0 以下は既定の 10 秒（アプリ側 focas_client.connect_timeout と同じ）
            timeout = 10
        if a.get("families"):
            self.families = {str(k): str(v) for k, v in a["families"].items()}
        self.close()
        h = ctypes.c_ushort(0)
        rc = self.api.cnc_allclibhndl3(host.encode("ascii"), ctypes.c_ushort(port),
                                       ctypes.c_long(timeout), ctypes.byref(h))
        if rc != 0:
            raise FocasError("connect", func="cnc_allclibhndl3", rc=rc, name=rc_name(rc))
        self.h = h
        try:
            info = self._sysinfo()
        except FocasError:
            self.close()
            raise
        self.info = info
        fam = self.families.get(info["cnc_type"])
        self.family = fam if fam in FAMILY_CLASSES else None
        return {"connected": True, "cnc_type": info["cnc_type"], "family": self.family,
                "supported": bool(self.family and FAMILY_CLASSES[self.family].implemented)}

    def cmd_disconnect(self, a, emit):
        self.close()
        return {"connected": False}

    def _sysinfo(self):
        buf = ctypes.create_string_buffer(64)
        self.check("cnc_sysinfo", self.read_rc("cnc_sysinfo", self.h, buf))
        return parse_sysinfo(buf.raw[:18])

    def cmd_identify(self, a, emit):
        self.need_handle()
        info = self._sysinfo()
        self.info = info
        out = dict(info)
        cid = (ctypes.c_ulong * 4)()
        rc = self.read_rc("cnc_rdcncid", self.h, cid)
        if rc in RC_LOST:
            raise _fail("cnc_rdcncid", rc)
        out["cnc_id"] = "-".join(f"{int(x) & 0xFFFFFFFF:08X}" for x in cid) if rc == 0 else None
        out["cnc_id_rc"] = rc
        n = ctypes.c_short(32)
        buf = ctypes.create_string_buffer(4 * 32)       # 実機で動いた大きさ（focas_exec.py）
        rc = self.read_rc("cnc_rdaxisname", self.h, ctypes.byref(n), buf)
        names = None
        if rc == 0:
            names = []
            for i in range(max(0, min(n.value, 32))):
                c, s = buf.raw[2 * i], buf.raw[2 * i + 1]
                nm = chr(c) if 32 < c < 127 else "?"
                names.append(nm + (chr(s) if 32 < s < 127 else ""))
        out["axis_names"] = names
        out["axis_names_rc"] = rc
        out["family"] = self.family
        out["supported"] = bool(self.family and FAMILY_CLASSES[self.family].implemented)
        return out

    def cmd_status(self, a, emit):
        self.need_handle()
        st = (ctypes.c_short * 16)()
        self.check("cnc_statinfo", self.read_rc("cnc_statinfo", self.h, st))
        out = parse_statinfo(list(st))
        # 数値の読み（MDI・リセット等）は 31i の並びで確かめたもの。ほかの機種では
        # 並びが違うかもしれないので、生の数値だけ返す。
        out["interpreted"] = bool(self.family and FAMILY_CLASSES[self.family].implemented)
        if not out["interpreted"]:
            out["text"] = {}
        return out

    def cmd_alarms(self, a, emit):
        self.need_handle()
        want = 10
        num = ctypes.c_short(want)
        buf = ctypes.create_string_buffer(44 * want)
        self.check("cnc_rdalmmsg", self.read_rc("cnc_rdalmmsg", self.h, ctypes.c_short(-1),
                                                ctypes.byref(num), buf))
        items = parse_alarms(buf.raw, num.value)
        return {"count": len(items), "alarms": items, "truncated": num.value >= want}

    def cmd_prog_info(self, a, emit):
        return self.need_family().prog_info(self)

    def cmd_prog_list(self, a, emit):
        fam = self.need_family()
        path = _check_path(a.get("path", "//CNC_MEM/"))
        tree = fam.prog_list(self, path, bool(a.get("recursive")))
        return {"tree": tree} if a.get("recursive") else tree[0]

    def cmd_prog_read(self, a, emit):
        fam = self.need_family()
        path = _check_path(a.get("path"))
        data, last, end_rc = fam.upload(self, 0, path, float(a.get("limit", PROG_READ_LIMIT)), emit,
                                        path.rsplit("/", 1)[-1])
        return _data_result(data, last, end_rc)

    def cmd_param_info(self, a, emit):
        fam = self.need_family()
        count = max(1, min(int(a.get("count", 100)), 100))
        types, end = fam.param_info(self, int(a.get("start", 0)), count, emit)
        return {"count": len(types), "types": types, **end}

    def cmd_param_read(self, a, emit):
        fam = self.need_family()
        return fam.param_read(self, int(a["no"]), a.get("label", ""), a.get("axis"))

    def cmd_param_read_many(self, a, emit):
        fam = self.need_family()
        items = a.get("items")
        if not isinstance(items, list) or len(items) > 500:
            raise FocasError("bad_args", message="一度に読めるのは 500 件までです")
        out = []
        for it in items:
            try:
                out.append(dict(ok=True, **fam.param_read(
                    self, int(it["no"]), it.get("label", ""), it.get("axis"))))
            except FocasError as e:
                if e.kind in ("lost", "not_connected", "unsupported"):
                    raise
                out.append({"ok": False, "no": it.get("no"), "label": it.get("label", ""),
                            "error": e.as_dict()})
        return {"items": out}

    def cmd_param_write(self, a, emit):
        fam = self.need_family()
        verified = a.get("verified")
        if not isinstance(verified, list) or not all(isinstance(x, str) for x in verified):
            raise FocasError("bad_args", message="verified（確認済みの型の一覧）を渡してください")
        if not isinstance(a.get("value"), str):
            raise FocasError("bad_args", message="value は文字で渡してください")
        return fam.param_write(self, int(a["no"]), a.get("label", ""), a["value"], verified)

    def cmd_param_dump(self, a, emit):
        fam = self.need_family()
        kind = a.get("kind")
        if kind not in DUMP_KINDS:
            raise FocasError("bad_args", message=f"知らない種別です: {kind}")
        data, last, end_rc = fam.upload(self, DUMP_KINDS[kind], "", float(a.get("limit", PARAM_DUMP_LIMIT)),
                                        emit, kind)
        out = _data_result(data, last, end_rc)
        out["kind"] = kind
        return out

    def cmd_quit(self, a, emit):
        self.close()
        return {"bye": True}

    COMMANDS = {
        "connect": cmd_connect, "disconnect": cmd_disconnect, "identify": cmd_identify,
        "status": cmd_status, "alarms": cmd_alarms, "prog_info": cmd_prog_info,
        "prog_list": cmd_prog_list, "prog_read": cmd_prog_read,
        "param_info": cmd_param_info, "param_read": cmd_param_read,
        "param_read_many": cmd_param_read_many, "param_write": cmd_param_write,
        "param_dump": cmd_param_dump,
        "quit": cmd_quit,
    }

    @staticmethod
    def _trace(msg):
        """想定外の例外のトレースバックを標準エラーに出す（アプリが focas_helper.log に残す）。
        返事には載せない。命令の中身（args）は書かない。"""
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        try:
            sys.stderr.write(f"[helper] 想定外の例外 cmd={cmd}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        except Exception:
            pass

    def handle(self, msg, write):
        """1つの命令を処理して、返事（dict）を返す。どんな例外でも返事にする。"""
        mid = msg.get("id") if isinstance(msg, dict) else None
        try:
            if not isinstance(msg, dict) or not isinstance(msg.get("cmd"), str):
                raise FocasError("protocol", message="命令の形が正しくありません")
            fn = self.COMMANDS.get(msg["cmd"])
            if fn is None:
                raise FocasError("bad_args", message=f"知らない命令です: {msg['cmd']}")
            args = msg.get("args") or {}
            if not isinstance(args, dict):
                raise FocasError("bad_args", message="args は辞書で渡してください")

            def emit(ev):
                ev = dict(ev)
                ev["id"] = mid
                write(ev)

            return {"id": mid, "ok": True, "result": fn(self, args, emit)}
        except FocasError as e:
            return {"id": mid, "ok": False, "error": e.as_dict()}
        except Exception as e:                      # ヘルパーが黙って死なない
            self._trace(msg)
            return {"id": mid, "ok": False,
                    "error": {"kind": "internal", "message": f"{type(e).__name__}: {e}"}}


def _data_result(data, last_rc, end_rc):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii"),
            "last_rc": last_rc, "end_rc": end_rc}


# ---------------------------------------------------------------------------
# 入出力
# ---------------------------------------------------------------------------

def emit_line(out, obj):
    """1行の JSON（ASCII だけ）を出す。"""
    out.write((json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
    out.flush()


def serve(lib, stdin, stdout, hello=None, families=None, sleep=time.sleep,
          clock=time.monotonic):
    """標準入力から1行ずつ命令を読み、返事を1行ずつ出す。
    lib は「DLL の持ち主」。本番は ctypes.WinDLL、試験は tests/fake_fwlib.py。
    標準入力が閉じたら（アプリが落ちたら）接続を切って終わる。"""
    hp = Helper(lib, sleep=sleep, clock=clock, families=families)

    def write(obj):
        emit_line(stdout, obj)

    if hello is not None:
        write(hello)
    try:
        while True:
            line = stdin.readline()
            if not line:
                break                                # 入力が閉じた
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                write({"id": None, "ok": False,
                       "error": {"kind": "protocol", "message": "JSON ではない行を受け取りました"}})
                continue
            reply = hp.handle(msg, write)
            write(reply)
            if isinstance(msg, dict) and msg.get("cmd") == "quit":
                break
    finally:
        hp.close()
    return 0


def find_dll(dll_dir=""):
    """Fwlib32.dll のフルパス。設定のフォルダ → SERVO GUIDE → SysWOW64 の順。"""
    dirs = ([dll_dir] if dll_dir else []) + list(DLL_SEARCH)
    for d in dirs:
        p = os.path.join(d, DLL_NAME)
        if os.path.isfile(p):
            return p
    return None


def main(argv=None):
    import platform
    argv = list(sys.argv[1:] if argv is None else argv)
    dll_dir = ""
    if "--dll-dir" in argv:
        i = argv.index("--dll-dir")
        dll_dir = argv[i + 1] if i + 1 < len(argv) else ""
    # 本物の標準出力を自分だけで握り、print などが紛れ込まないよう向け直す
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    hello = {"hello": 1, "proto": PROTO, "bits": struct.calcsize("P") * 8,
             "python": platform.python_version(), "dll": None, "dll_ok": False}
    lib = None
    path = find_dll(dll_dir)
    hello["dll"] = path
    if path is None:
        hello["dll_error"] = f"{DLL_NAME} が見つかりません"
    else:
        try:
            lib = ctypes.WinDLL(path)           # フルパスで読む（試験スクリプトと同じ）
            hello["dll_ok"] = True
        except (OSError, AttributeError) as e:
            hello["dll_error"] = str(e)
    if lib is None:
        emit_line(out, hello)
        return 3
    return serve(lib, sys.stdin.buffer, out, hello=hello)


if __name__ == "__main__":
    sys.exit(main())
