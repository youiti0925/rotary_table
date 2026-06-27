# -*- coding: utf-8 -*-
"""FANUC ネイティブ・パラメータファイル（.PRM、実機が読み込む形式）の読み取りと
「マスタを元に一部の値だけ差し替える」バイト保存型の編集。

形式（実物 F30BASIC.PRM / T<Seiban>.prm から解読）:
    %                       開始
    N<番号>Q1<セグメント…>   1パラメータ1行
        L1P<値>             単一値（系統共通）。P=整数/ビット列、M=符号付小数
        A1P<>A2P<>A3P<>A4P<> 軸1〜4の値（A4='A'軸＝回転/傾斜軸）
        S1P<>               主軸 / T1P<> / P<>(番号なし) もある
    %                       終了
    メタ情報（Seiban/Model等）は無い。Seibanはファイル名のみ。

方針（安全第一）:
    マスタ.PRM のテキストはそのまま保持し、指定した番号の指定軸の「値」だけを
    その場で置換する（他のバイト＝空白・空行・改行・他パラメータは一切触らない）。
    これでマスタとバイト単位で同一、変更したパラメータの値だけが変わる。
    値の捏造はしない。機械への入力は人が実施（PWE/電源再投入に注意）。
"""

import re

# 1パラメータ行（行頭が N<digits>Q1）。Q1 以降のセグメント部を group(2) に取る
_LINE = re.compile(r"^(N(\d+)Q1)(.*)$")
# セグメント: 任意のグループ(L1/A1..A4/S1/T1 等) + 型(P/M) + 値
_SEG = re.compile(r"([LASTlast]\d+)?([PM])([-+]?\d+(?:\.\d+)?)")
_VALUE = r"[-+]?\d+(?:\.\d+)?"


def segments(line: str):
    """1行のセグメント [(ラベル, 型, 値)] を返す。ラベルは 'L1'/'A4'/'S1'/'' など。"""
    m = _LINE.match(line)
    if not m:
        return None
    return [(g or "", t, v) for (g, t, v) in _SEG.findall(m.group(3))]


def param_number(line: str):
    m = _LINE.match(line)
    return m.group(2) if m else None


def _norm_num(number) -> str:
    """番号を5桁ゼロ詰めに正規化（'1825'→'01825'、'01825'→'01825'）。"""
    s = str(number).strip().lstrip("Nn")
    return s.zfill(5) if s.isdigit() else s


def get_value(text: str, number, label: str = None):
    """番号(・ラベル)の値を返す。label 省略時は最初の値。無ければ None。

    label 例: 'A4'（回転軸）, 'L1', 'S1'。'' で無ラベル（番号なしP）。
    """
    num = _norm_num(number)
    for line in text.splitlines():
        if param_number(line) == num:
            for (g, t, v) in segments(line):
                if label is None or g.upper() == label.upper():
                    return v
            return None
    return None


def set_value(text: str, number, value, label: str = None) -> tuple:
    """番号(・ラベル)の値だけをその場で置換する（他のバイトは不変）。

    戻り値: (新テキスト, 置換できたか bool)。label 省略時は最初の値を置換。
    """
    num = _norm_num(number)
    value = str(value)
    lines = text.split("\n")
    done = False
    for i, line in enumerate(lines):
        if done or param_number(line) != num:
            continue
        m = _LINE.match(line)
        head, body = m.group(1), m.group(3)  # head='N#####Q1', body=セグメント部
        # body 内で、目的のセグメントの「値」だけを置換する
        out, pos, replaced = [], 0, False
        for sm in _SEG.finditer(body):
            g = (sm.group(1) or "")
            out.append(body[pos:sm.start()])
            if (not replaced) and (label is None or g.upper() == label.upper()):
                out.append(f"{g}{sm.group(2)}{value}")  # ラベル・型は保持、値だけ差替
                replaced = True
            else:
                out.append(sm.group(0))
            pos = sm.end()
        out.append(body[pos:])
        if replaced:
            lines[i] = head + "".join(out)
            done = True
    return "\n".join(lines), done


def apply_changes(text: str, changes) -> tuple:
    """changes: [(番号, 値, ラベル), ...] を順に適用。

    戻り値: (新テキスト, 反映できなかった [(番号,ラベル), ...])。
    """
    missing = []
    for item in changes:
        number, value = item[0], item[1]
        label = item[2] if len(item) > 2 else None
        text, ok = set_value(text, number, value, label)
        if not ok:
            missing.append((str(number), label or ""))
    return text, missing


def set_on_axis(text: str, number, value, axis_num) -> tuple:
    """指定軸(A<axis_num>)の値を差し替える。軸が無いパラメータは単一値(L1/S1/T1/番号なし)へ。

    工程: BASICの「その軸だけ」を製品値に変える、を実現する。戻り値 (新text, 成否)。
    """
    for label in (f"A{axis_num}", "L1", "S1", "T1", None):
        new, ok = set_value(text, number, value, label)
        if ok:
            return new, True
    return text, False


def apply_product_values(text: str, values: dict, axis_num) -> tuple:
    """製品の値 {番号: 値} を BASIC の指定軸へ一括反映する。

    戻り値: (新テキスト, 反映できなかった番号のリスト)。番号は BASIC に無いもの。
    """
    missing = []
    for number, value in values.items():
        if value is None or str(value) == "":
            continue
        text, ok = set_on_axis(text, number, value, axis_num)
        if not ok:
            missing.append(str(number))
    return text, missing


def set_common(text: str, number, value) -> tuple:
    """軸に属さない値(L1/S1/T1/無ラベル)だけを差し替える（A軸は触らない）。

    2軸テーブルで「共通(系統)パラメータ」を、誤って片方の軸へ入れないための専用版。
    戻り値 (新テキスト, 成否)。
    """
    for label in ("L1", "S1", "T1", None):
        new, ok = set_value(text, number, value, label)
        if ok:
            return new, True
    return text, False


def apply_common_values(text: str, values: dict) -> tuple:
    """共通値 {番号: 値} を BASIC の共通スロット(L1/S1/T1/無ラベル)へ反映する。

    戻り値 (新テキスト, 反映できなかった番号のリスト)。
    """
    missing = []
    for number, value in values.items():
        if value is None or str(value) == "":
            continue
        text, ok = set_common(text, number, value)
        if not ok:
            missing.append(str(number))
    return text, missing


def axis_of_label(label) -> int:
    """ラベル 'A4'/'a2' → 軸番号 4/2。A軸でなければ None（L1/S1/T1/'' 等）。"""
    m = re.fullmatch(r"[Aa](\d+)", str(label or "").strip())
    return int(m.group(1)) if m else None


def diff_by_axis(master_text: str, product_text: str) -> tuple:
    """master(BASIC) と完成製品 .PRM の差分を「軸ごと」に分ける。

    2軸テーブルでは完成製品ファイルが傾斜軸・回転軸の両方を変更している。
    diff() は軸ラベルを保持するので、それを軸番号で振り分けて返す。
    戻り値: (per_axis: {軸番号: {番号: 値}}, common: {番号: 値})。
    値が None（製品側に無い番号）は除外する。
    """
    per_axis, common = {}, {}
    for (number, label, _mv, ov) in diff(master_text, product_text):
        if ov is None:
            continue
        ax = axis_of_label(label)
        if ax is None:
            common[number] = ov
        else:
            per_axis.setdefault(ax, {})[number] = ov
    return per_axis, common


def looks_like_fanuc_prm(text: str) -> bool:
    """先頭付近に N#####Q1… 形式があれば FANUCネイティブ.PRM とみなす。"""
    head = text.lstrip()
    if not head.startswith("%"):
        # %が無くてもN行が並んでいれば許容
        pass
    return bool(re.search(r"^N\d+Q1", head, re.MULTILINE))


def values_map(text: str) -> dict:
    """{(番号, ラベル): 値} の辞書にする。ラベルは 'L1'/'A4'/'S1'/'' など。"""
    out = {}
    for line in text.splitlines():
        num = param_number(line)
        if num is None:
            continue
        for (g, t, v) in segments(line):
            out[(num, g)] = v
    return out


def diff(master_text: str, other_text: str) -> list:
    """マスタと製品別ファイルを比較し、値が違う箇所を返す。

    戻り値: [(番号, ラベル, マスタ値, 製品値), ...]（番号・ラベル順）。
    これで「その製品で何番をどう変えているか」を実物から自動抽出できる
    （紙のパラメータ表を打ち直さなくてよい）。マスタに無い/製品に無い番号は
    値 None で示す。
    """
    m = values_map(master_text)
    o = values_map(other_text)
    changed = []
    for key in sorted(set(m) | set(o)):
        mv, ov = m.get(key), o.get(key)
        if mv != ov:
            number, label = key
            changed.append((number, label, mv, ov))
    return changed


def effective_value(master_text: str, values: dict, number, label: str = None):
    """変更後の実効値を返す: values に number があればその値、無ければマスタ(BASIC)の値。

    番号は5桁ゼロ詰めゆらぎを吸収して照合する（'1815'と'01815'を同一視）。
    """
    num = _norm_num(number)
    for k, v in values.items():
        if _norm_num(k) == num:
            return v
    return get_value(master_text, number, label)


def _bit_of(value, bit: int):
    """ビットパラメータ value の bit 番ビット(0/1)を返す。判別不能なら None。

    value は '00100000' のようなビット列（FANUC表示=左がMSB #7…#0）でも、
    10進整数の文字列でもよい。先頭の ' や " 空白は除去する。
    """
    s = str(value).strip().lstrip("'\"").strip()
    if not s:
        return None
    if set(s) <= {"0", "1"} and len(s) > 1:   # ビット列（左がMSB）
        s = s.zfill(bit + 1)
        return 1 if s[len(s) - 1 - bit] == "1" else 0
    try:
        n = int(s, 10)
    except ValueError:
        return None
    return (n >> bit) & 1


def closed_loop_mode(value, bit: int = 1, full_when: int = 1) -> str:
    """クローズドループ種別を返す: 'フル' / 'セミ' / ''（判別不能）。

    既定は FANUC 1815 の #1(OPTx＝別置検出器)。1ならフルクローズ（別置スケール）、
    0ならセミクローズ（モータ内蔵検出器）。機種で番号/ビットが違う場合に備えて
    bit・full_when を引数化（設定から渡せる）。値の生表示と併用して人が確認する前提。
    """
    b = _bit_of(value, bit)
    if b is None:
        return ""
    return "フル" if b == full_when else "セミ"

