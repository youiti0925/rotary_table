# -*- coding: utf-8 -*-
"""パラメータ/プログラムのファイルが「実機が出したもの」か「PCで作られたもの」かを判定する。

BASICフォルダには実機のバックアップと、PC側で作られたマスタが混ざっている。
どちらを元に製品ファイルを作るかで、制御装置が読み込めるかどうかが変わる
（実機の F23BASIC.DAT は読めたが、PC側の F23BASIC.prm は読めなかった）。
拡張子は当てにならない（.PRM でも実機のものがあり得る）ので、中身のバイトで見る。

決め手はブロックの区切り（EOB）:
    実機（FANUCのパンチアウト）   … LF CR CR
    実機のものをPCで写したもの     … CRLF×3（LFとCRがそれぞれCRLFへ膨らんだ形）
    PCのツールが作ったもの         … LF のみ、または CRLF
補助として、実機は ISOコードに無い文字（";"・小文字・ASCII外）を出さない。

使い方:
    from .param_origin import classify, scan_folder
    info = classify(Path("F23BASIC.DAT").read_bytes())
    info["verdict"]  # "machine" / "pc" / "unknown"
"""

import re
from pathlib import Path

PUNCH = b"\n\r\r"            # 実機のEOB
PUNCH_CONVERTED = b"\r\n\r\n\r\n"  # 実機のEOBをWindowsのテキストモードで写したもの
VERDICT_LABEL = {
    "machine": "実機（制御装置）が出したもの",
    "converted": "実機のものだがPC経由で改行が変わっている",
    "pc": "PCで作られたもの",
    "unknown": "判定できない",
}


def _eob_counts(data: bytes) -> tuple:
    """(実機形式, 実機→PC変換, CRLF, LFのみ) の行数。"""
    conv = data.count(PUNCH_CONVERTED)
    # 変換済み（CRLF×3）のファイルに LF CR CR は現れないので、両方は数えない
    punch = 0 if conv else data.count(PUNCH)
    crlf = data.count(b"\r\n") - punch - conv * 3
    lf = data.count(b"\n") - punch - conv * 3 - max(crlf, 0)
    return punch, conv, max(crlf, 0), max(lf, 0)


def file_kind(data: bytes) -> str:
    """ファイルの種類をざっくり見る（パラメータ / プログラム / 不明）。

    パラメータの書式は2世代ある:
        新 "N01825Q1A1P3000"      … Q1あり・空白なし
        旧 "N01825 A1 P 3000"     … Q1なし・空白区切り
    """
    head = data[:3000]
    if re.search(rb"N\d{4,5}Q\d", head):
        return "パラメータ"
    if re.search(rb"N\d{3,5}\s+(?:[LAST]\d+\s+)?[PM]\s*[-+]?\d", head):
        return "パラメータ"
    if re.search(rb"O\d{3,5}", head) or re.search(rb"G0\d", head):
        return "プログラム"
    return "不明"


def classify(data: bytes) -> dict:
    """バイト列から出どころを判定する。

    戻り値: {"verdict", "label", "score", "reasons", "kind", "eob", "lines"}
    score は大きいほど実機らしい。+3以上で実機、-2以下でPC、その間は判定保留。
    """
    data = bytes(data or b"")
    if not data.strip():
        return {"verdict": "unknown", "label": VERDICT_LABEL["unknown"], "score": 0,
                "reasons": ["中身が空"], "kind": "不明", "eob": "", "lines": 0}

    punch, conv, crlf, lf = _eob_counts(data)
    total = max(punch + conv + crlf + lf, 1)
    reasons = []
    score = 0
    converted = False

    if punch >= total * 0.9:
        score += 3
        reasons.append(f"区切りが LF CR CR（{punch}行）＝実機のパンチ形式")
        eob = "LF CR CR"
    elif conv >= total * 0.9:
        score += 3
        converted = True
        reasons.append(f"区切りが CRLF×3（{conv}行）＝実機の LF CR CR を"
                       "Windowsのテキストモードで写したもの")
        eob = "CRLF×3"
    elif crlf >= total * 0.9:
        score -= 2
        reasons.append(f"区切りが CRLF（{crlf}行）＝Windowsのツール")
        eob = "CRLF"
    elif lf >= total * 0.9:
        score -= 2
        reasons.append(f"区切りが LF のみ（{lf}行）＝PC/Unix側")
        eob = "LF"
    else:
        reasons.append(f"区切りが混在（実機{punch} / CRLF{crlf} / LF{lf}）")
        eob = "混在"

    # 実機は ISOコードに無い文字を出さない
    if b";" in data:
        score -= 3
        reasons.append('";" が入っている（実機はEOBを改行で出すので入らない）')
    non_ascii = sum(1 for b in data if b > 127)
    if non_ascii:
        score -= 2
        reasons.append(f"ASCII外の文字が{non_ascii}個")
    lower = sum(1 for b in data if 0x61 <= b <= 0x7A)
    if lower:
        score -= 1
        reasons.append(f"小文字が{lower}個（実機の出力は大文字のみ）")
    if not data.lstrip().startswith(b"%"):
        score -= 1
        reasons.append("先頭が % でない")

    verdict = ("converted" if (score >= 3 and converted) else
               "machine" if score >= 3 else "pc" if score <= -2 else "unknown")
    return {"verdict": verdict, "label": VERDICT_LABEL[verdict], "score": score,
            "reasons": reasons, "kind": file_kind(data), "eob": eob, "lines": total}


def scan_folder(folder, exts=None) -> list:
    """フォルダ内のファイルを判定して [(名前, info, Path)] を返す（名前順）。

    exts=None なら拡張子で絞らない（BASICは拡張子なしで置かれていることがあるため）。
    """
    base = Path(folder) if folder else None
    if not base or not base.is_dir():
        return []
    out = []
    for p in sorted(base.iterdir()):
        if not p.is_file():
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        try:
            info = classify(p.read_bytes())
        except Exception as e:            # 読めないファイルは飛ばさず理由を出す
            info = {"verdict": "unknown", "label": VERDICT_LABEL["unknown"],
                    "score": 0, "reasons": [f"読めません: {e}"], "kind": "不明",
                    "eob": "", "lines": 0}
        out.append((p.name, info, p))
    return out


def summarize(rows) -> str:
    """判定結果の件数まとめ（1行）。"""
    n = {"machine": 0, "converted": 0, "pc": 0, "unknown": 0}
    for _name, info, _p in rows:
        n[info["verdict"]] = n.get(info["verdict"], 0) + 1
    return (f"{len(rows)}件: 実機 {n['machine']} / 実機(改行変換済) {n['converted']}"
            f" / PC {n['pc']} / 保留 {n['unknown']}")
