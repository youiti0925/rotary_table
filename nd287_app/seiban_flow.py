# -*- coding: utf-8 -*-
"""受注伝票番号(Seiban)起点の「かんたん作成」ロジック（GUI非依存・単体試験可）。

流れ:
  1. Seiban から製品データフォルダの傾斜(T)・回転(R)ファイルを探す。
  2. 見つかった製品.prm の中身から、型式・必要容量・モーター等を読む。
  3. 必要容量から「作れる制御装置（号機）」を絞り、各軸の割り当てを提案する。
これを ParamWizardDialog から呼んで、Seiban→何を作るか→どの制御装置か→作成、を導く。
"""

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


def read_product_meta(text: str, *, kind="") -> dict:
    """製品データ(.prm)の付加情報を読む。ヘッダ＋CSV形式（System Version=…）専用。

    戻り値 dict: model, kind(傾斜/回転), capacity(推定), amp_model, motor, motor_no,
    gear, direction, sep_detector, mode_hint(フル/セミ/'')。
    N形式（実機ネイティブ）はヘッダが無いので最小限（kind は引数のものを使う）。
    """
    meta = {"model": "", "kind": kind, "capacity": "", "amp_model": "",
            "motor": "", "motor_no": "", "gear": "", "direction": "",
            "sep_detector": "", "mode_hint": ""}
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
    meta["capacity"] = derive_capacity(meta["amp_model"], meta["motor"])
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
