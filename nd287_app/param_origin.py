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

★この判定の限界（重要）
    アプリ自身も実機と同じ区切り（LF CR CR）で書き出すので、
    「アプリが出力したファイル」と「実機が出したファイル」は区別できない。
    判定は「実機と同じ形式か」であって「実機から出てきたか」ではない。
    したがって BASICフォルダには<b>アプリの出力を置かないこと</b>。
    置くと machine_reference（実機の番号の和集合）や master_fix_rows
    （PC製は根拠にしない）の前提が崩れ、PC側ツールが足した番号が
    「実機にある番号」として通ってしまう。

使い方:
    from .param_origin import classify, scan_folder
    info = classify(Path("F23BASIC.DAT").read_bytes())
    info["verdict"]  # "machine" / "pc" / "unknown"
"""

import hashlib
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


def cnc_id(data: bytes) -> str:
    """先頭に書かれた制御装置ID（CNC ID）。無ければ空。

    新しい制御装置は、パラメータを出力するとき
        %(CNCID=3C7B5D01,F6914B1A,5E32C389,4E92C4C8)
    のように % の直後へ自分のIDをコメントで書く。パラメータの値ではないので
    読み込んでも設定は変わらないが、「そのファイルがどの制御装置から出たか」を
    示す唯一の手がかりになる。別の号機のファイルに同じIDが入っていたら、
    どちらかが取り違え（コピー）である。
    """
    m = re.search(rb"CNCID\s*=\s*([0-9A-Fa-f,]+)", bytes(data or b"")[:400])
    return m.group(1).decode("ascii", "ignore").upper() if m else ""


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

    ★判定できるのは「実機と同じ形式か」まで。アプリ自身も同じ形式で書くので、
      アプリの出力は machine と判定される（モジュール冒頭の注意を参照）。

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
            "reasons": reasons, "kind": file_kind(data), "eob": eob, "lines": total,
            "cnc_id": cnc_id(data), "digest": hashlib.sha256(data).hexdigest()}


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
                    "eob": "", "lines": 0, "cnc_id": "", "digest": ""}
        out.append((p.name, info, p))
    return out


def summarize(rows) -> str:
    """判定結果の件数まとめ（1行）。"""
    n = {"machine": 0, "converted": 0, "pc": 0, "unknown": 0}
    for _name, info, _p in rows:
        n[info["verdict"]] = n.get(info["verdict"], 0) + 1
    return (f"{len(rows)}件: 実機 {n['machine']} / 実機(改行変換済) {n['converted']}"
            f" / PC {n['pc']} / 保留 {n['unknown']}")


def duplicate_groups(rows) -> list:
    """中身が完全に同じファイルの組を返す（scan_folder の結果を渡す）。

    別の号機の名前で置いてあるのに中身が同じなら、どちらかは取り違え（コピー）。
    そのまま使うと、別の機械のサーボ設定を書き込むことになる。
    戻り値: [[名前, 名前, ...], ...]（2本以上の組だけ、名前順）
    """
    by_digest = {}
    for name, info, _p in rows:
        d = info.get("digest")
        if d:
            by_digest.setdefault(d, []).append(name)
    return sorted((sorted(v) for v in by_digest.values() if len(v) > 1),
                  key=lambda g: g[0])


def unit_of(name: str) -> str:
    """ファイル名から号機番号を取り出す（'F23BASIC.DAT' → '23'）。無ければ空。"""
    m = re.match(r"F(\d+)BASIC", str(name), re.IGNORECASE)
    return str(int(m.group(1))) if m else ""


def cross_unit_duplicates(rows) -> list:
    """「別の号機どうしで中身が同じ」組だけを返す（同じ号機の重複は除く）。"""
    return [g for g in duplicate_groups(rows)
            if len({unit_of(n) for n in g if unit_of(n)}) > 1]


def shared_cnc_ids(rows) -> list:
    """同じCNC IDが別の号機に付いている組を返す [(ID, [名前...])]。"""
    by_id = {}
    for name, info, _p in rows:
        i = info.get("cnc_id")
        if i:
            by_id.setdefault(i, []).append(name)
    return [(i, sorted(v)) for i, v in sorted(by_id.items())
            if len({unit_of(n) for n in v if unit_of(n)}) > 1]
