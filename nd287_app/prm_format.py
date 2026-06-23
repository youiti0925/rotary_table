# -*- coding: utf-8 -*-
"""FANUC 用 .prm（津田駒 NC回転テーブルのパラメータファイル）の読み書き。

実物 T50013078.prm から解読。構造:

  ヘッダ部（各項目: コメント行 ";  <ラベル>" ＋ "Key=Value"）
    System Version / PrintForm / Model / Seiban / Motor RPM / Gear Rate / Axis /
    Direction / ZRN Direction / Motor Number / Motor Model / Motor Detector /
    Separate Detector / Separate Convert. / Servo Amp Model / DATE / CHECK /
    Note1 / Note2
  パラメータ部（CSV・6列をダブルクォートで囲む）:
    "番号","---","","","値","和名(改行)英名"
    ・最後の説明欄は和名と英名が改行で2行に分かれてクォート内に入る
    ・区切りの空行は "","","","","",""
  ファイル名 = <Axis><Seiban>.prm （Axis=T は傾斜、Seiban=受注伝票番号）
  文字コード cp932、改行 CRLF。

方針:
  マスタ.prm を読み込み、Seiban/Model/Axis などのヘッダと一部パラメータ値だけを
  差し替えて書き戻す（構造はマスタのまま＝バイト一致を保ちやすい）。値の捏造はしない。
"""

import csv
import io
import copy

ENCODING = "cp932"
NEWLINE = "\r\n"


def parse_prm(text: str) -> dict:
    """ .prm テキスト → doc。

    doc = {
      "header": [ {"comments": [行...], "key": str, "value": str}, ... ],
      "pre_csv": [CSV直前の余り行...],   # 通常は空
      "rows":   [ [f0,f1,f2,f3,f4,f5], ... ],  # パラメータ行（説明は改行入り）
    }
    """
    lines = text.splitlines()
    # 最初に " で始まる行＝CSVの開始
    csv_start = len(lines)
    for i, ln in enumerate(lines):
        if ln.startswith('"'):
            csv_start = i
            break
    header, pending = [], []
    for ln in lines[:csv_start]:
        if ln.startswith(";"):
            pending.append(ln)
        elif "=" in ln:
            key, _, val = ln.partition("=")
            header.append({"comments": pending, "key": key, "value": val})
            pending = []
        else:
            pending.append(ln)          # 想定外の行も保持して往復維持
    body = "\n".join(lines[csv_start:])
    rows = list(csv.reader(io.StringIO(body))) if body.strip() else []
    return {"header": header, "pre_csv": pending, "rows": rows}


def _csv_field(value: str, newline: str) -> str:
    v = (value or "").replace('"', '""').replace("\r\n", "\n").replace("\n", newline)
    return f'"{v}"'


def format_prm(doc: dict, newline: str = NEWLINE) -> str:
    out = []
    for entry in doc.get("header", []):
        for c in entry.get("comments", []):
            out.append(c)
        out.append(f'{entry["key"]}={entry.get("value", "")}')
    out.extend(doc.get("pre_csv", []))
    text = newline.join(out)
    if out:
        text += newline
    rows = doc.get("rows", [])
    if rows:
        text += newline.join(
            ",".join(_csv_field(f, newline) for f in row) for row in rows
        ) + newline
    return text


def load_prm(path) -> dict:
    with open(path, "r", encoding=ENCODING, errors="replace", newline="") as f:
        return parse_prm(f.read())


def save_prm(path, doc: dict):
    with open(path, "w", encoding=ENCODING, errors="replace", newline="") as f:
        f.write(format_prm(doc))


# ---- ヘッダ操作 ----
def header_get(doc: dict, key: str, default: str = "") -> str:
    for e in doc.get("header", []):
        if e["key"] == key:
            return e.get("value", "")
    return default


def header_set(doc: dict, key: str, value: str) -> bool:
    for e in doc.get("header", []):
        if e["key"] == key:
            e["value"] = str(value)
            return True
    return False


# ---- パラメータ操作 ----
def param_value(doc: dict, number) -> str:
    number = str(number)
    for row in doc.get("rows", []):
        if row and row[0] == number:
            return row[4] if len(row) > 4 else ""
    return None


def set_param_value(doc: dict, number, value) -> bool:
    """番号のパラメータ値（第5列）を差し替える。見つかれば True。"""
    number = str(number)
    for row in doc.get("rows", []):
        if row and row[0] == number and len(row) >= 6:
            row[4] = str(value)
            return True
    return False


def apply_values(doc: dict, values: dict):
    """{番号: 値} を一括反映。反映できなかった番号のリストを返す。"""
    missing = []
    for number, value in values.items():
        if not set_param_value(doc, number, value):
            missing.append(str(number))
    return missing


def iter_params(doc: dict):
    """(番号, 値, 和名, 英名) を返す（説明は改行で和名/英名に分割）。空行は除く。"""
    for row in doc.get("rows", []):
        if not row or not row[0]:
            continue
        desc = row[5] if len(row) > 5 else ""
        jp, _, en = desc.partition("\n")
        yield row[0], (row[4] if len(row) > 4 else ""), jp, en


def default_filename(doc: dict) -> str:
    """<Axis><Seiban>.prm。Axis=T は傾斜。"""
    axis = header_get(doc, "Axis", "").strip()
    seiban = header_get(doc, "Seiban", "").strip()
    stem = f"{axis}{seiban}" or "param"
    return f"{stem}.prm"


def generate(master_doc: dict, header_overrides: dict = None,
             value_overrides: dict = None):
    """マスタ.prm をコピーし、ヘッダ（Seiban/Model等）と一部パラメータ値だけ差し替える。

    マスタの構造（コメント・列・順序）はそのまま保つので、書式はマスタと完全一致。
    戻り値: (新doc, 反映できなかった番号のリスト)。
    """
    doc = copy.deepcopy(master_doc)
    for key, value in (header_overrides or {}).items():
        header_set(doc, key, value)
    missing = apply_values(doc, value_overrides or {})
    return doc, missing
