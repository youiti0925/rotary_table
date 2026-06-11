# -*- coding: utf-8 -*-
"""型式マスタ（測定条件.csv / 合否判定.csv）の読み込みと適用

測定条件.csv（1行目=回転/傾斜の見出し、2行目=列名、3行目以降データ）:
    型式, クローズ, 間隔H, 間隔W, 1/N_H, 1/N_W, 分割数1, 分割数2,
    測定順HR, 測定順WR, 測定順WL, 測定順HL
    - 間隔は0.0001°単位（例 300000=30°）。実測ピッチ = 間隔/N
    - 測定順は1〜4で取込の順番。0や空欄はその系列を測らない

合否判定.csv（1行目=列名、2行目以降データ）:
    型式, クローズ, 多軸, MINA, MINB, MAXA, MAXB, 歯数, …, 傾きH, 傾きW, コメント, 表示名
    - バックラッシ規格: MIN = MINA×温度 + MINB、MAX = MAXA×温度 + MAXB（A空欄=0）
    - 歯数があればウォーム1回転 = 360/歯数[°]（例 72 → 5°）
    - 傾きH/W: 傾きの規格[秒]（ホイール/ウォーム）

照合キーは「型式＋クローズ（＋多軸）」を連結した文字列（例 RB-250Ri, RN-100N）。
"""

import csv
import io
from pathlib import Path

from .settings import app_dir

CONDITION_ORDER_SECTIONS = ("HR", "WR", "WL", "HL")


def _read_text(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


def _num(row, index):
    try:
        text = row[index].strip()
    except IndexError:
        return None
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _key(*parts):
    return "".join(p.strip() for p in parts if p and p.strip()).upper()


def load_conditions(path) -> dict:
    """測定条件.csv → {照合キー: 条件dict}"""
    rows = list(csv.reader(io.StringIO(_read_text(path))))
    entries = {}
    for row in rows[2:]:
        if not row or not row[0].strip():
            continue
        model = row[0].strip()
        close = row[1].strip() if len(row) > 1 else ""
        order_positions = []
        for offset, section in enumerate(CONDITION_ORDER_SECTIONS):
            position = _num(row, 8 + offset)
            order_positions.append((section, int(position) if position else 0))
        entry = dict(
            model=model,
            close=close,
            interval_h=_num(row, 2),
            interval_w=_num(row, 3),
            n_h=int(_num(row, 4) or 1),
            n_w=int(_num(row, 5) or 1),
            div1=int(_num(row, 6) or 0),
            div2=int(_num(row, 7) or 0),
            order=[s for s, p in sorted(order_positions, key=lambda x: x[1]) if p > 0],
        )
        entries[_key(model, close)] = entry
    return entries


def load_judgement(path) -> dict:
    """合否判定.csv → {照合キー: 規格dict}"""
    rows = list(csv.reader(io.StringIO(_read_text(path))))
    entries = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        model = row[0].strip()
        close = row[1].strip() if len(row) > 1 else ""
        multi = row[2].strip() if len(row) > 2 else ""
        display = row[15].strip() if len(row) > 15 and row[15].strip() else ""
        entry = dict(
            model=model,
            close=close,
            multi=multi,
            min_a=_num(row, 3),
            min_b=_num(row, 4),
            max_a=_num(row, 5),
            max_b=_num(row, 6),
            teeth=_num(row, 7),
            slope_h=_num(row, 12),
            slope_w=_num(row, 13),
        )
        entries[_key(model, close, multi)] = entry
        if display:
            entries.setdefault(display.upper(), entry)
    return entries


def load_masters(settings) -> dict:
    """settings のパス設定から両マスタを読む。"""

    def resolve(key, default):
        path = Path(str(settings.get(key) or default))
        if not path.is_absolute():
            path = app_dir() / path
        return path

    return dict(
        conditions=load_conditions(resolve("conditions_csv", "マスタ/測定条件.csv")),
        judgement=load_judgement(resolve("judgement_csv", "マスタ/合否判定.csv")),
    )


def find_entry(entries: dict, model_text: str):
    """入力された型式（例 RWE-200, RB-250Ri）でマスタを引く。"""
    if not model_text:
        return None
    return entries.get(model_text.strip().upper())


def condition_params(cond: dict, judge: dict = None) -> dict:
    """測定条件エントリ → アプリの設定値（ホイール刻み・ウォーム刻み等）

    ウォームの刻み・範囲も測定条件（間隔W・分割数2）から決める。
    合否判定マスタの歯数はP補正用のデータで、測定条件には使わない。
    """
    params = {}
    if cond.get("interval_h") and cond.get("div1"):
        params["wheel_pitch"] = cond["interval_h"] * 1e-4 / cond["n_h"]
    if cond.get("interval_w") and cond.get("div2"):
        pitch = cond["interval_w"] * 1e-4 / cond["n_w"]
        params["worm_pitch"] = pitch
        params["worm_range"] = pitch * cond["div2"] * cond["n_w"]
    params["order"] = cond.get("order") or list(CONDITION_ORDER_SECTIONS)
    return params


def formula_minmax(judge: dict, temp_c: float):
    """温度式によるバックラッシ規格: MIN=MINA×温度+MINB, MAX=MAXA×温度+MAXB

    MAXB が無い型式は規格なし（None）。
    """
    if not judge or temp_c is None or judge.get("max_b") is None:
        return None
    spec_min = (judge.get("min_a") or 0.0) * temp_c + (judge.get("min_b") or 0.0)
    spec_max = (judge.get("max_a") or 0.0) * temp_c + judge["max_b"]
    return (spec_min, spec_max)
