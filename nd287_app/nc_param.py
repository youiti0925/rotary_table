# -*- coding: utf-8 -*-
"""FANUCパラメータの「製品ごとの変更（差分）」を管理し、確認表と差分パラメータ
ファイルを作る。

運用イメージ:
  マスタパラメータ（共通の基準）に対し、製品/仕様ごとに一部のパラメータだけを
  変更する。従来は紙の「パラメータ表」を見て手入力していた。これを:
    1. 紙の表を CSV（型式, 番号, 軸, 変更値, メモ）に一度だけ起こす。
    2. 型式を選ぶと、その製品の変更分だけを
        - 確認表（番号 / 旧値→新値 / メモ）と
        - 差分パラメータファイル（変更する番号だけ）
       として出力する。
    3. カード/LANで機械へ渡し、機械側で PWE=1 にして「入力」する（人が実施）。

安全方針:
  - このモジュールは「ファイルを作るだけ」。機械へ直接書き込みはしない。
  - 差分方式: 変更する番号だけを書く。FANUCは入力したファイルに載っている
    パラメータだけ書き換わる（載っていないものは現状維持）ので、他を壊さない。
  - ★パラメータファイルの厳密なバイト書式は機種で異なる。実機のバックアップ
    1個で必ず検証してから機械へ投入すること（未検証のまま投入しない）。
"""

import csv
import io
import re

# 取り込みCSVの列名ゆらぎを吸収する（紙の表→CSV化を人がやる前提）
_COL_ALIASES = {
    "model": ("型式", "機種", "品種", "製品", "model"),
    "controller": ("制御", "制御装置", "nc", "版", "cnc", "controller"),
    "number": ("番号", "パラメータ", "パラメータ番号", "param", "no", "number"),
    "axis": ("軸", "軸番号", "axis"),
    "value": ("変更値", "値", "設定値", "value", "data"),
    "note": ("メモ", "備考", "説明", "項目", "note", "memo"),
}


def _norm_header(name: str) -> str:
    key = (name or "").strip().lower()
    for canon, aliases in _COL_ALIASES.items():
        if key in (a.lower() for a in aliases):
            return canon
    return key


def normalize_model(text: str) -> str:
    """型式照合用に正規化（大文字化・記号/空白除去）。masters.find_entry と同方針。"""
    return re.sub(r"[\s\-_./]", "", str(text or "")).upper()


class ParamChange:
    """1件の変更。controller=制御(MELDAS-60/FANUC等。無ければ全制御共通),
    number=パラメータ番号, axis=軸(無ければ ""), value=変更後の値。"""

    __slots__ = ("controller", "number", "axis", "value", "note")

    def __init__(self, number, axis="", value="", note="", controller=""):
        self.controller = str(controller or "").strip()
        self.number = str(number).strip()
        self.axis = str(axis or "").strip()
        self.value = str(value).strip()
        self.note = str(note or "").strip()

    def __eq__(self, other):
        return isinstance(other, ParamChange) and (
            self.controller, self.number, self.axis, self.value, self.note
        ) == (other.controller, other.number, other.axis, other.value, other.note)

    def __repr__(self):
        return (f"ParamChange({self.number!r},{self.axis!r},{self.value!r},"
                f"{self.note!r},ctrl={self.controller!r})")

    def key(self):
        """同一パラメータ判定キー（制御＋番号＋軸）。"""
        return (self.controller, self.number, self.axis)


def parse_changes(text: str) -> dict:
    """CSVテキスト →  {型式: [ParamChange, ...]}。列名はゆらぎを吸収する。

    番号が空の行（区切り・見出し）は読み飛ばす。同じ型式・同じ番号(＋軸)が複数
    あれば後勝ち（最後の値を採用）。
    """
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}
    header = [_norm_header(c) for c in rows[0]]
    idx = {name: header.index(name) for name in set(header) if name in _COL_ALIASES}

    def cell(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = {}
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        number = cell(row, "number")
        if not number:
            continue
        model = cell(row, "model")
        change = ParamChange(number, cell(row, "axis"),
                             cell(row, "value"), cell(row, "note"),
                             controller=cell(row, "controller"))
        bucket = out.setdefault(model, [])
        # 同じ番号(＋軸)は後勝ちで置き換え
        for i, ex in enumerate(bucket):
            if ex.key() == change.key():
                bucket[i] = change
                break
        else:
            bucket.append(change)
    return out


def load_changes(path) -> dict:
    with open(path, "r", encoding="cp932", errors="replace", newline="") as f:
        return parse_changes(f.read())


def write_changes(path, changes: dict):
    """{型式: [ParamChange]} を CSV に保存（編集UIからの書き戻し用）。"""
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["型式", "制御", "番号", "軸", "変更値", "メモ"])
        for model, items in changes.items():
            for c in items:
                w.writerow([model, c.controller, c.number, c.axis, c.value, c.note])


def _model_items(changes: dict, model: str) -> list:
    """型式（ゆらぎ吸収して照合）に対応する変更リスト。無ければ空。"""
    target = normalize_model(model)
    if not target:
        return []
    for key, items in changes.items():       # 完全一致（正規化）優先
        if normalize_model(key) == target:
            return list(items)
    for key, items in changes.items():       # 次に前方一致
        nk = normalize_model(key)
        if nk and (target.startswith(nk) or nk.startswith(target)):
            return list(items)
    return []


def changes_for_model(changes: dict, model: str, controller=None) -> list:
    """型式（＋指定があれば制御）に対応する変更リストを返す。

    controller を指定した場合、制御が一致するもの＋制御欄が空（全制御共通）のものを
    返す。指定なしなら全制御ぶんを返す。
    """
    items = _model_items(changes, model)
    if controller:
        c = normalize_model(controller)
        items = [x for x in items if not x.controller or normalize_model(x.controller) == c]
    return items


def controllers_for_model(changes: dict, model: str) -> list:
    """その型式で使われている制御名（重複なし・出現順）。UIの選択肢用。"""
    seen = []
    for x in _model_items(changes, model):
        if x.controller and x.controller not in seen:
            seen.append(x.controller)
    return seen


def parse_param_backup(text: str) -> dict:
    """マスタ/バックアップのパラメータ値を {(番号,軸): 値} に読む（旧値表示用）。

    機種で書式が違うので“緩く”読む: 各行から「番号」と「値」を見つける。
    軸つきは number/axis を拾えた範囲で。読めない行は無視する。
    ★この読み取りも実サンプルで要検証。
    """
    table = {}
    for line in text.splitlines():
        s = line.strip().strip(";")
        if not s or s in ("%",):
            continue
        m = re.match(r"[Nn]?\s*(\d{1,5})\s*(?:[Aa]\s*(\d+))?\s+([-+]?\d+)", s)
        if not m:
            continue
        number, axis, value = m.group(1), (m.group(2) or ""), m.group(3)
        table[(number, axis)] = value
    return table


def checklist_rows(changes: list, master: dict = None) -> list:
    """確認表の行 (番号, 軸, 旧値, 新値, メモ) を返す。master があれば旧値を埋める。"""
    master = master or {}
    rows = []
    for c in changes:
        old = master.get((c.number, c.axis), master.get((c.number, ""), ""))
        rows.append((c.number, c.axis, old, c.value, c.note))
    return rows


def format_checklist(model: str, changes: list, master: dict = None,
                     date: str = "", operator: str = "", controller: str = "") -> str:
    """機械側での照合・入力用の確認表（テキスト）。これは書式リスクなし＝確実。"""
    rows = checklist_rows(changes, master)
    lines = [
        "パラメータ変更 確認表",
        f"型式: {model}    制御: {controller or '―'}    日付: {date}    担当: {operator}",
        "※ 機械側で書込許可にして該当番号だけ入力。一部は電源再投入が必要。",
        "-" * 56,
        f'{"番号":<8}{"軸":<4}{"旧値":>10}  →{"新値":>10}   メモ',
        "-" * 56,
    ]
    for number, axis, old, new, note in rows:
        lines.append(f'{number:<8}{axis:<4}{old:>10}  →{new:>10}   {note}')
    lines.append("-" * 56)
    lines.append(f"変更点数: {len(rows)}")
    return "\n".join(lines)


# ★ここから下（機械が読み込むファイルの書式）は制御(FANUC/MELDAS)・機種で異なる。
#   実サンプルで要検証。確定するまでは確認表（人が手入力）を使うこと。
def format_param_file(changes: list, *, controller: str = "",
                      newline: str = "\r\n") -> str:
    """差分パラメータファイル（変更する番号だけ）。汎用テキスト形（要検証）。

    現状は「N<番号> P<値>」の一般形＋制御名のコメントで出力する。FANUC と MELDAS
    で受け付ける厳密な書式は異なるため、各制御の実機バックアップ1個で確認してから
    投入すること（未確認のまま投入しない）。
    """
    out = ["%"]
    if controller:
        out.append(f"(CONTROLLER {controller} - VERIFY FORMAT WITH REAL BACKUP)")
    for c in changes:
        axis = f" A{c.axis}" if c.axis else ""
        out.append(f"N{c.number}{axis} P{c.value}")
    out.append("%")
    return newline.join(out) + newline


def save_param_file(path, changes: list, controller: str = ""):
    with open(path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(format_param_file(changes, controller=controller))


def save_checklist(path, text: str):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(text)
