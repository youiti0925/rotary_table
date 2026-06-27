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

# 軸名パラメータ(1020)の値は軸名のASCIIコード。88=X,89=Y,90=Z,65=A,66=B,67=C…
_AXIS_CODE = {88: "X", 89: "Y", 90: "Z", 65: "A", 66: "B", 67: "C",
              85: "U", 86: "V", 87: "W"}
# アンプ最大電流(2165=AMR)→容量。実機(F30/F84)で 25→20A,45→40A,85→80A,165→160A を確認。
_AMR_CAP = {25: "20A", 45: "40A", 85: "80A", 165: "160A"}
_STD_CAP_NUMS = (10, 20, 40, 80, 160, 360)


def amr_to_capacity(amr) -> str:
    """アンプ最大電流(パラメータ2165=AMR)値 → 容量(20A等)。判定不能なら ""。

    確認済み: 25→20A,45→40A,85→80A,165→160A（AMR = 容量+5）。未知値は AMR-5 を
    標準容量(10/20/40/80/160/360)へ寄せる。離れていれば "" としてユーザー確認に回す。
    """
    s = str(amr if amr is not None else "").strip()
    if not s.lstrip("-").isdigit():
        return ""
    n = int(s)
    if n in _AMR_CAP:
        return _AMR_CAP[n]
    cand = n - 5
    for std in _STD_CAP_NUMS:
        if abs(cand - std) <= 2:
            return f"{std}A"
    return ""


def controller_from_basic_text(text, unit):
    """BASIC(.PRM ネイティブ)テキストから Controller を作る。

    軸名は 1020(軸名のASCIIコード)、容量は 2165(アンプ最大電流AMR)、参考に 2020
    (モーターID)を読む。電圧(200/400V)は BASIC からは確実に読めないので空のまま。
    """
    from . import fanuc_param
    vm = {}                                    # {(5桁番号, ラベル): 値} に一度で集約
    for (num, label), v in fanuc_param.values_map(text).items():
        vm[(fanuc_param._norm_num(num), label)] = v

    def g(num, lab):
        return vm.get((fanuc_param._norm_num(num), lab))

    caps, amps = {}, {}
    for i in range(1, 13):                      # 軸スロット A1〜A12
        lab = f"A{i}"
        code = g("1020", lab)
        if code is None or not code.strip().lstrip("-").isdigit():
            continue
        letter = _AXIS_CODE.get(int(code))
        if not letter:
            continue
        amr = g("2165", lab)
        caps[letter] = amr_to_capacity(amr)
        mid = g("2020", lab)
        amps[letter] = f"ID{mid}/AMR{amr}" if (mid or amr) else ""
    return Controller(unit, caps=caps, amps=amps)


def scan_basic_folder(folder):
    """BASICフォルダを走査し、各号機の Controller を BASIC から自動抽出する。

    ファイル名 F<号機>BASIC.*（拡張子ゆらぎ可）。同一号機に複数あれば
    basic_file_for_unit と同じ優先順(.prm>.txt>.dat>無)で1つ選ぶ。
    戻り値: [(Controller, BASICファイル名)]（号機番号順）。
    """
    from pathlib import Path
    from . import fanuc_param
    if not folder or not Path(folder).is_dir():
        return []
    units = {}
    for p in sorted(Path(folder).iterdir()):
        if not p.is_file():
            continue
        m = re.match(r"^F(\d+)BASIC", p.name, re.IGNORECASE)
        if m and m.group(1).isdigit():
            units.setdefault(str(int(m.group(1))), []).append(p)
    out = []
    for unit in sorted(units, key=lambda u: int(u)):
        path = basic_file_for_unit(folder, unit)
        if not path:
            continue
        try:
            text = Path(path).read_bytes().decode("cp932", errors="replace")
        except Exception:
            continue
        if not fanuc_param.looks_like_fanuc_prm(text):
            continue
        ctl = controller_from_basic_text(text, unit)
        if ctl.axes():
            out.append((ctl, Path(path).name))
    return out


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


# 制御装置マスタCSVの列順（書き出し・追記で使う）
MASTER_HEADER = ["号機", "CNCユニット", "Ver", "制御電圧", "SERVO",
                 "X容量", "Y容量", "Z容量", "A容量", "B容量", "C容量",
                 "Xアンプ", "Yアンプ", "Zアンプ", "Aアンプ", "Bアンプ", "Cアンプ",
                 "-Bなめらか補正", "-D駆動"]


def _master_row(ctl) -> list:
    """Controller → マスタCSV 1行（MASTER_HEADER 順）。Controller の持つ値を全部書く。"""
    row = {h: "" for h in MASTER_HEADER}
    row["号機"] = ctl.unit
    row["CNCユニット"] = ctl.cnc
    row["Ver"] = ctl.ver
    row["制御電圧"] = ctl.voltage
    row["SERVO"] = ctl.servo
    row["-Bなめらか補正"] = ctl.b_corr
    row["-D駆動"] = ctl.d_drive
    for a in AXES:
        if ctl.caps.get(a):
            row[f"{a}容量"] = ctl.caps[a]
        if ctl.amps.get(a):
            row[f"{a}アンプ"] = ctl.amps[a]
    return [row[h] for h in MASTER_HEADER]


def _read_master_rows(path):
    """マスタCSVを (ヘッダ有無, [行リスト]) で読む。cp932/UTF-8どちらでも。"""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return False, []
    raw = p.read_bytes()
    text = raw.decode("cp932", errors="replace")
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    rows = list(csv.reader(io.StringIO(text)))
    has_header = bool(rows) and rows[0] and "号機" in rows[0][0]
    return has_header, rows


def _write_master_rows(path, body_rows):
    """ヘッダ＋本文行をマスタCSVへ書き出す（cp932）。"""
    from pathlib import Path
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(MASTER_HEADER)
    for r in body_rows:
        w.writerow(r)
    Path(path).write_bytes(buf.getvalue().encode("cp932", errors="replace"))


def upsert_controller(path, controller) -> str:
    """1台の制御装置を手入力で登録/更新する。号機が既にあれば更新、無ければ追加。

    戻り値: 'added' / 'updated'。他の号機の行はそのまま保持。
    """
    has_header, rows = _read_master_rows(path)
    body = rows[1:] if has_header else rows
    new_row = _master_row(controller)
    u = str(controller.unit).strip()
    updated = False
    for i, r in enumerate(body):
        if r and str(r[0]).strip() == u:
            body[i] = new_row
            updated = True
            break
    if not updated:
        body.append(new_row)
    _write_master_rows(path, body)
    return "updated" if updated else "added"


def delete_controller(path, unit) -> bool:
    """号機を1台削除する。削除できたら True。"""
    has_header, rows = _read_master_rows(path)
    body = rows[1:] if has_header else rows
    u = str(unit).strip()
    kept = [r for r in body if not (r and str(r[0]).strip() == u)]
    if len(kept) == len(body):
        return False
    _write_master_rows(path, kept)
    return True


def diff_scanned_vs_master(scanned, existing) -> list:
    """BASICから読んだ scanned[(Controller,ファイル名)] と既存マスタ existing を突き合わせる。

    戻り値: [{"unit","caps","file","status","master_caps"}]。
    status: 'new'(マスタ未登録) / 'same'(容量一致) / 'diff'(容量が違う)。
    """
    by_unit = {str(c.unit): c for c in existing}
    out = []
    for ctl, fname in scanned:
        ex = by_unit.get(str(ctl.unit))
        if ex is None:
            status, mcaps = "new", ""
        else:
            mcaps = ex.caps_text()
            status = "same" if mcaps == ctl.caps_text() else "diff"
        out.append({"unit": ctl.unit, "caps": ctl.caps_text(), "file": fname,
                    "status": status, "master_caps": mcaps})
    return out


def append_units_to_master(path, controllers_to_add) -> int:
    """マスタCSVに新規号機の行を追記する（既存行はそのまま）。追記件数を返す。

    既存ファイルが無ければヘッダ付きで新規作成。文字コードは cp932。
    """
    from pathlib import Path
    if not path or not controllers_to_add:
        return 0
    p = Path(path)
    lines = []
    if p.exists():
        raw = p.read_bytes()
        for enc in ("cp932", "utf-8-sig", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                text = raw.decode("cp932", errors="replace")
        existing_rows = list(csv.reader(io.StringIO(text)))
        has_header = bool(existing_rows) and existing_rows[0] and "号機" in existing_rows[0][0]
    else:
        existing_rows, has_header = [], False
    buf = io.StringIO()
    w = csv.writer(buf)
    if not has_header:
        w.writerow(MASTER_HEADER)
    for row in existing_rows:
        w.writerow(row)
    n = 0
    for ctl in controllers_to_add:
        w.writerow(_master_row(ctl))
        n += 1
    p.write_bytes(buf.getvalue().encode("cp932", errors="replace"))
    return n
