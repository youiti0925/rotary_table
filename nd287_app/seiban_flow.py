# -*- coding: utf-8 -*-
"""受注伝票番号(Seiban)起点の「かんたん作成」ロジック（GUI非依存・単体試験可）。

流れ:
  1. Seiban から製品データフォルダの傾斜(T)・回転(R)ファイルを探す。
  2. 見つかった製品.prm の中身から、型式・必要容量・モーター等を読む。
  3. 必要容量から「作れる制御装置（号機）」を絞り、各軸の割り当てを提案する。
これを ParamWizardDialog から呼んで、Seiban→何を作るか→どの制御装置か→作成、を導く。
"""

import csv
import io
import re
from pathlib import Path

from . import prm_format, fanuc_param

# 頭文字 → 種別（出力ファイル名と同じ規則: T=傾斜, R=回転）
PREFIX_KIND = {"T": "傾斜", "R": "回転"}
KIND_PREFIX = {"傾斜": "T", "回転": "R"}
_CAP_NUMS = ("20", "40", "80", "160")


def find_seiban_files(folder, seiban) -> list:
    """製品データフォルダから Seiban に対応する傾斜(T)/回転(R)ファイルを探す。

    ファイル名の頭文字 T/R＋Seiban で判別する（出力名 <頭文字><Seiban>.prm と同じ規則）。
    戻り値: [{"kind": 傾斜/回転, "prefix": T/R, "path": str, "name": str}, ...]（T,Rの順）。
    """
    s = str(seiban or "").strip()
    if not s or not folder or not Path(folder).is_dir():
        return []
    found = {}
    for p in sorted(Path(folder).iterdir()):
        if not (p.is_file() and p.suffix.lower() in (".prm", ".txt")):
            continue
        stem = p.stem.upper()
        m = re.match(r"^([TR])", stem)
        if not m:
            continue
        prefix = m.group(1)
        # 頭文字の直後（または名前のどこか）に Seiban を含む
        rest = stem[1:]
        if s.upper() in rest and prefix not in found:
            found[prefix] = {"kind": PREFIX_KIND[prefix], "prefix": prefix,
                             "path": str(p), "name": p.name}
    return [found[k] for k in ("T", "R") if k in found]


def derive_capacity(amp_model: str = "", motor_model: str = "") -> str:
    """アンプ型式（必要ならモーター型式）から必要容量(20A/40A/80A/160A)を推定する。

    確実に読めたときだけ容量を返す（読めなければ "" ＝ 手で選ぶフォールバック）。
    例: 'αiSV 40' / 'βiSV 80' / 'SVM 160' / 単独の '160' → 容量。
    """
    for src in (amp_model, motor_model):
        s = str(src or "")
        if not s:
            continue
        # 単独トークンの 20/40/80/160（語境界）を最優先で拾う
        for n in sorted(_CAP_NUMS, key=len, reverse=True):
            if re.search(rf"(?<!\d){n}(?!\d)", s):
                return f"{n}A"
    return ""


def _normkey(s) -> str:
    """対応表の照合キー正規化（大文字・記号/空白除去）。"""
    return re.sub(r"[\s\-_/.]", "", str(s or "")).upper()


_MCAP_ALIASES = {
    "model": ("モーター型式", "モータ型式", "モーター", "モータ", "motor model", "motor"),
    "number": ("モーター番号", "モータ番号", "motor number", "motor no", "number"),
    "motor_id": ("モーターid", "モータid", "2020", "motor id", "id"),
    "capacity": ("容量", "アンプ容量", "必要容量", "capacity", "amp"),
}


def _mcap_header(name):
    key = (name or "").strip().lower()
    for canon, aliases in _MCAP_ALIASES.items():
        if key in (a.lower() for a in aliases):
            return canon
    return key


def parse_motor_caps(text: str) -> dict:
    """モーター→容量 対応表CSV → {"by_no":{}, "by_model":{}, "by_id":{}}（正規化キー）。

    列: モーター型式 / モーター番号 / モーターID(2020) / 容量。空容量の行は無視。
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"by_no": {}, "by_model": {}, "by_id": {}}
    header = [_mcap_header(c) for c in rows[0]]
    idx = {n: header.index(n) for n in set(header) if n in _MCAP_ALIASES}
    out = {"by_no": {}, "by_model": {}, "by_id": {}}

    def cell(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        cap = _norm_cap_text(cell(row, "capacity"))
        if not cap:
            continue
        if cell(row, "number"):
            out["by_no"][_normkey(cell(row, "number"))] = cap
        if cell(row, "model"):
            out["by_model"][_normkey(cell(row, "model"))] = cap
        if cell(row, "motor_id"):
            out["by_id"][_normkey(cell(row, "motor_id"))] = cap
    return out


def _norm_cap_text(cap) -> str:
    s = str(cap or "").strip().upper().replace("Ａ", "A")
    if not s:
        return ""
    if s.endswith("A"):
        s = s[:-1]
    s = s.strip()
    return f"{s}A" if s else ""


def load_motor_caps(path) -> dict:
    """モーター→容量 対応表を読む（cp932/UTF-8両対応）。無ければ空の辞書。"""
    if not path or not Path(path).exists():
        return {"by_no": {}, "by_model": {}, "by_id": {}}
    raw = Path(path).read_bytes()
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            t = parse_motor_caps(raw.decode(enc, errors="strict"))
        except Exception:
            continue
        if any(t.values()):
            return t
    return parse_motor_caps(raw.decode("cp932", errors="replace"))


def capacity_for_motor(table: dict, motor_no="", motor_model="", motor_id="") -> str:
    """モーター→容量 対応表から容量を引く（番号→ID→型式の順で照合）。無ければ ""。"""
    if not table:
        return ""
    for key, val in (("by_no", motor_no), ("by_id", motor_id), ("by_model", motor_model)):
        if val:
            cap = table.get(key, {}).get(_normkey(val))
            if cap:
                return cap
    return ""


def read_product_meta(text: str, *, kind="", motor_caps=None) -> dict:
    """製品データ(.prm)の付加情報を読む。ヘッダ＋CSV形式（System Version=…）専用。

    戻り値 dict: model, kind(傾斜/回転), capacity(推定), amp_model, motor, motor_no,
    gear, direction, sep_detector, mode_hint(フル/セミ/'')。
    N形式（実機ネイティブ）はヘッダが無いので最小限（kind は引数のものを使う）。
    """
    meta = {"model": "", "kind": kind, "capacity": "", "capacity_src": "",
            "amp_model": "", "motor": "", "motor_no": "", "motor_id": "",
            "gear": "", "direction": "", "sep_detector": "", "mode_hint": ""}
    if not text or fanuc_param.looks_like_fanuc_prm(text):
        return meta
    try:
        doc = prm_format.parse_prm(text)
    except Exception:
        return meta
    g = lambda k: prm_format.header_get(doc, k, "").strip()
    meta["model"] = g("Model")
    axis = g("Axis").upper()
    if axis in PREFIX_KIND:
        meta["kind"] = PREFIX_KIND[axis]      # 中身の Axis 欄を優先
    meta["amp_model"] = g("Servo Amp Model")
    meta["motor"] = g("Motor Model")
    meta["motor_no"] = g("Motor Number")
    meta["gear"] = g("Gear Rate").lstrip("'")
    meta["direction"] = g("Direction")
    meta["sep_detector"] = g("Separate Detector")
    try:                                       # パラメータ2020＝モーター型式ID
        meta["motor_id"] = (prm_format.param_value(doc, "2020") or "").strip()
    except Exception:
        meta["motor_id"] = ""
    # 容量: まず Servo Amp Model から、無ければ モーター→容量 対応表から引く
    cap = derive_capacity(meta["amp_model"])
    if cap:
        meta["capacity_src"] = "アンプ"
    elif motor_caps:
        cap = capacity_for_motor(motor_caps, meta["motor_no"], meta["motor"],
                                 meta["motor_id"])
        if cap:
            meta["capacity_src"] = "対応表"
    meta["capacity"] = cap
    # 別置検出器が入っていればフルクロの手がかり（最終判定は 1815）
    meta["mode_hint"] = "フル" if meta["sep_detector"] else ""
    return meta


def assign_axes(controller, needs: list):
    """1つの制御装置で needs（容量のリスト）に軸を割り当てられるか試す。

    needs: 作りたい軸ぶんの容量文字列 ["160A","160A"] 等（不明は ""）。
    戻り値: 割り当て [軸文字, ...]（needs と同順）。割り当て不可なら None。
    容量指定のある need を先に確定し、残り（"")は空いている軸へ。
    """
    avail = list(controller.axes())          # 実装されている軸 [X,Y,...]
    order = sorted(range(len(needs)), key=lambda i: 0 if needs[i] else 1)
    out = [None] * len(needs)
    for i in order:
        cap = needs[i]
        pick = None
        for a in avail:
            if (not cap) or controller.caps.get(a) == cap:
                pick = a
                break
        if pick is None:
            return None
        out[i] = pick
        avail.remove(pick)
    return out


def capable_controllers(controllers, needs: list) -> list:
    """needs（作りたい軸ぶんの容量）を満たせる制御装置と軸割り当てを返す。

    戻り値: [(controller, [軸文字, ...]), ...]（号機番号順）。needs が空なら全件（割当 []）。
    """
    if not needs:
        return [(c, []) for c in controllers]
    out = []
    for c in controllers:
        asg = assign_axes(c, needs)
        if asg is not None:
            out.append((c, asg))
    return out
