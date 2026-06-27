# -*- coding: utf-8 -*-
"""制御装置マスタ（号機ごとの CNC・電圧・SERVO版・軸ごとの容量/アンプ型式）の読み込みと、
製品の必要容量から「使える制御装置・軸」を絞り込むユーティリティ。

CSV列（マスタ/制御装置マスタ.csv）:
  号機, CNCユニット, Ver, 制御電圧, SERVO, X容量..C容量, Xアンプ..Cアンプ,
  -Bなめらか補正, -D駆動
容量は "20A"/"40A"/"80A"/"160A" の文字列。軸は X/Y/Z/A/B/C（第1〜6軸）。
"""

import csv
import io
import re

AXES = ["X", "Y", "Z", "A", "B", "C"]


def _norm_cap(cap) -> str:
    """容量表記を正規化（'40a'/' 40A '/'40' → '40A'）。空なら ''。"""
    s = str(cap or "").strip().upper().replace("Ａ", "A")
    if not s:
        return ""
    if s.endswith("A"):
        s = s[:-1]
    s = s.strip()
    return f"{s}A" if s else ""


class Controller:
    __slots__ = ("unit", "cnc", "ver", "voltage", "servo", "caps", "amps",
                 "b_corr", "d_drive")

    def __init__(self, unit, cnc="", ver="", voltage="", servo="",
                 caps=None, amps=None, b_corr="", d_drive=""):
        self.unit = str(unit or "").strip()
        self.cnc = str(cnc or "").strip()
        self.ver = str(ver or "").strip()
        self.voltage = str(voltage or "").strip()
        self.servo = str(servo or "").strip()
        self.caps = {a: _norm_cap((caps or {}).get(a)) for a in AXES}
        self.amps = {a: str((amps or {}).get(a) or "").strip() for a in AXES}
        self.b_corr = str(b_corr or "").strip()
        self.d_drive = str(d_drive or "").strip()

    def axes(self):
        """容量が入っている（実装されている）軸の文字 [X,Y,...]。"""
        return [a for a in AXES if self.caps.get(a)]

    def axes_with_capacity(self, cap):
        """指定容量に一致する軸の文字リスト。"""
        c = _norm_cap(cap)
        return [a for a in AXES if self.caps.get(a) == c] if c else []

    def has_capacity(self, cap):
        return bool(self.axes_with_capacity(cap))

    def caps_text(self):
        """'X:20A Y:40A Z:80A A:160A' 形式（実装軸のみ）。"""
        return " ".join(f"{a}:{self.caps[a]}" for a in self.axes())

    def label(self):
        """プルダウン表示用の1行ラベル。"""
        return f"{self.unit}（{self.cnc}） {self.caps_text()}"


_COLMAP = {
    "unit": ("号機", "機番", "unit"),
    "cnc": ("cncユニット", "cnc", "制御", "cncユニット"),
    "ver": ("ver", "バージョン", "version"),
    "voltage": ("制御電圧", "電圧", "voltage"),
    "servo": ("servo", "servo series/edition", "サーボ"),
    "b_corr": ("-bなめらか補正", "なめらか補正", "-b"),
    "d_drive": ("-d駆動", "駆動可否", "-d"),
}


def _norm_header(name):
    key = (name or "").strip().lower()
    for canon, aliases in _COLMAP.items():
        if key in (a.lower() for a in aliases):
            return canon
    return key


def parse_controllers(text):
    """CSVテキスト → [Controller]。号機が空の行は読み飛ばす。"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = rows[0]
    idx = {}
    for i, h in enumerate(header):
        idx[_norm_header(h)] = i
        idx[(h or "").strip()] = i  # 容量/アンプ列は元の見出しで引く
    out = []
    for row in rows[1:]:
        def cell(key):
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""
        unit = cell("unit")
        if not unit:
            continue
        caps = {a: cell(f"{a}容量") for a in AXES}
        amps = {a: cell(f"{a}アンプ") for a in AXES}
        out.append(Controller(
            unit, cnc=cell("cnc"), ver=cell("ver"), voltage=cell("voltage"),
            servo=cell("servo"), caps=caps, amps=amps,
            b_corr=cell("b_corr"), d_drive=cell("d_drive")))
    return out


def load_controllers(path):
    """制御装置マスタCSVを読む。cp932（Excel/Windows）でもUTF-8でも読めるよう、
    号機が取れたほうの文字コードを採用する。"""
    from pathlib import Path
    if not path or not Path(path).exists():
        return []
    raw = Path(path).read_bytes()
    best = []
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            ctls = parse_controllers(raw.decode(enc, errors="strict"))
        except Exception:
            continue
        if ctls:
            return ctls
        best = best or ctls
    # どれもダメなら replace で最後の手段
    return parse_controllers(raw.decode("cp932", errors="replace"))


def all_capacities(controllers):
    """マスタに出てくる容量の一覧（数値順）。プルダウンの選択肢用。"""
    seen = set()
    for c in controllers:
        for a in AXES:
            if c.caps.get(a):
                seen.add(c.caps[a])
    def keyfn(cap):
        m = re.match(r"(\d+)", cap)
        return int(m.group(1)) if m else 9999
    return sorted(seen, key=keyfn)


def filter_by_capacity(controllers, cap):
    """指定容量の軸を持つ制御装置だけ返す（容量空なら全件）。"""
    c = _norm_cap(cap)
    if not c:
        return list(controllers)
    return [ctl for ctl in controllers if ctl.has_capacity(c)]


def basic_file_for_unit(folder, unit):
    """BASICの場所フォルダから、号機に対応するBASICファイルのパスを探す。

    ファイル名は `F<号機>BASIC`（拡張子は .PRM/.prm/.DAT/.dat/.txt や無しなど様々）。
    号機番号がちょうど一致するものを返す（'1' が 'F10'/'F100' に誤一致しない）。
    複数あれば .prm → .txt → .dat → 拡張子なし の順で優先。見つからなければ None。
    """
    from pathlib import Path
    u = str(unit or "").strip()
    if not u or not folder or not Path(folder).is_dir():
        return None
    cands = []
    for p in sorted(Path(folder).iterdir()):
        if not p.is_file():
            continue
        m = re.match(r"^F(\d+)BASIC", p.name, re.IGNORECASE)
        if m and u.isdigit() and m.group(1).isdigit() and int(m.group(1)) == int(u):
            cands.append(p)
    if not cands:
        return None

    def rank(p):
        return {".prm": 0, ".txt": 1, ".dat": 2, "": 3}.get(p.suffix.lower(), 4)

    cands.sort(key=rank)
    return str(cands[0])
