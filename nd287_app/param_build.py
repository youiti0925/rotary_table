# -*- coding: utf-8 -*-
"""BASIC(.prm) を元に、製品の変更値を指定軸へ入れて 1 ファイル作る処理の共通部分。

GUI（パラメータ画面・パラメータDB画面）から共通で使う。Qt 非依存なので単体試験可。
N形式（実機ネイティブ）はバイト保存編集、ヘッダ＋CSV形式は構造を保ったまま差替え。
"""

from pathlib import Path

from . import fanuc_param, prm_format


def read_master(master_path) -> str:
    """BASIC を cp932 で読む。改行 CRLF を文字どおり保持（バイト一致のため bytes 経由）。"""
    return Path(master_path).read_bytes().decode("cp932", errors="replace")


def build_text(raw: str, values: dict, axis: int, seiban: str = "") -> tuple:
    """BASIC テキスト raw に values({番号:値}) を入れた新テキストを返す。

    戻り値: (新テキスト, 反映できなかった番号リスト, 形式 'fanuc'/'headercsv')。
    N形式は指定軸だけ差替え（他軸・他バイトは不変）。ヘッダ＋CSV形式は Seiban も差替え。
    """
    if fanuc_param.looks_like_fanuc_prm(raw):
        newtext, missing = fanuc_param.apply_product_values(raw, values, axis)
        return newtext, missing, "fanuc"
    doc = prm_format.parse_prm(raw)
    if seiban:
        prm_format.header_set(doc, "Seiban", seiban)
    missing = [n for n in values if prm_format.param_value(doc, n) is None]
    prm_format.apply_values(doc, values)
    return prm_format.format_prm(doc), missing, "headercsv"


def write_text(path, newtext: str):
    """cp932・改行そのまま（newline=""）で書く。N形式の CRLF をそのまま残す。"""
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(newtext)


def filename(prefix: str, seiban: str) -> str:
    """出力ファイル名 <頭文字><Seiban>.prm（例 T50013078.prm / R… ）。"""
    return f"{prefix}{seiban}.prm"


def create_file(master_path, out_dir, values: dict, *, axis: int, prefix: str,
                seiban: str) -> tuple:
    """BASIC を元に <頭文字><Seiban>.prm を out_dir に作成する。

    戻り値: (出力Path, 反映できなかった番号リスト, 形式)。
    """
    raw = read_master(master_path)
    newtext, missing, fmt = build_text(raw, values, axis, seiban)
    out = Path(out_dir) / filename(prefix, seiban)
    write_text(out, newtext)
    return out, missing, fmt


def header_info(text: str) -> dict:
    """ヘッダ＋CSV形式(.prm)からヘッダ情報を取り出す。N形式や非対応なら空 dict。

    返すキー: motor(Motor Model), motor_no(Motor Number), direction(Direction),
    gear(Gear Rate)。DBへ保存する付加情報。
    """
    if fanuc_param.looks_like_fanuc_prm(text):
        return {}
    try:
        doc = prm_format.parse_prm(text)
    except Exception:
        return {}
    out = {
        "motor": prm_format.header_get(doc, "Motor Model", ""),
        "motor_no": prm_format.header_get(doc, "Motor Number", ""),
        "direction": prm_format.header_get(doc, "Direction", ""),
        "gear": prm_format.header_get(doc, "Gear Rate", "").lstrip("'"),
    }
    return {k: v for k, v in out.items() if v}


def preview_rows(raw: str, values: dict, axis: int) -> list:
    """作成前プレビュー用の [(番号, 旧値, 新値)]。旧値は BASIC(raw) のその軸の値。

    N形式は指定軸(A<axis>)の値、ヘッダ＋CSV形式は番号の値を「旧値」とする。
    """
    is_fanuc = fanuc_param.looks_like_fanuc_prm(raw)
    doc = None
    if not is_fanuc:
        try:
            doc = prm_format.parse_prm(raw)
        except Exception:
            doc = None
    rows = []
    for num, newv in values.items():
        if newv == "":
            continue
        if is_fanuc:
            old = fanuc_param.get_value(raw, num, f"A{axis}")
            if old is None:
                old = fanuc_param.get_value(raw, num)
        else:
            old = prm_format.param_value(doc, num) if doc else None
        rows.append((str(num), "" if old is None else str(old), str(newv)))
    return rows


def detect_mode(raw: str, values: dict, axis: int, *, number="1815", bit=1,
                full_when=1) -> tuple:
    """変更後の実効 1815 値からクローズドループ種別を判定する。

    戻り値: (モード 'フル'/'セミ'/'', 実効値文字列)。実効値は生表示・確認用。
    N形式は指定軸の値、ヘッダ＋CSV形式は番号の値を見る。
    """
    if fanuc_param.looks_like_fanuc_prm(raw):
        eff = fanuc_param.effective_value(raw, values, number, f"A{axis}")
    else:
        eff = None
        for k, v in values.items():
            if str(k).lstrip("0") == str(number).lstrip("0"):
                eff = v
                break
        if eff is None:
            try:
                doc = prm_format.parse_prm(raw)
                eff = prm_format.param_value(doc, number)
            except Exception:
                eff = None
    return fanuc_param.closed_loop_mode(eff, bit, full_when), (eff or "")
