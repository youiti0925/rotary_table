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

from . import fanuc

def numbers_in(text: str) -> set:
    """テキストに入っているパラメータ番号の集合。"""
    out = set()
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        num = param_number(line)
        if num is not None:
            out.add(int(num))
    return out


def unknown_numbers(text: str, reference: str) -> list:
    """実機のバックアップ(reference)に無いパラメータ番号を返す。

    「何番から先がメーカ用」という固定の線引きはしない。制御装置が自分で出力した
    バックアップに入っていない番号は、その制御装置が持っていない（または出力しない
    保護領域の）番号なので、書き戻すと取込が止まる可能性がある。実機どうしを
    突き合わせて決めるのが確実。
    """
    ref = numbers_in(reference)
    if not ref:
        return []
    return sorted(n for n in numbers_in(text) if n not in ref)


def drop_numbers(text: str, numbers) -> tuple:
    """指定した番号の行を取り除いたテキストと、取り除いた番号を返す。

    番号として読めない要素（None・空・文字）は黙って無視する。呼び側が
    別のところで作ったリストをそのまま渡せるようにするため。
    """
    drop = set()
    for n in (numbers or []):
        try:
            drop.add(int(str(n).strip().lstrip("Nn")))
        except (TypeError, ValueError):
            continue
    kept, removed = [], []
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        num = param_number(line)
        if num is not None and int(num) in drop:
            removed.append(int(num))
            continue
        kept.append(line)
    return "\n".join(kept), removed


def prm_bytes(text: str, eob: str = None) -> bytes:
    """パラメータファイルを、実機が読める形のバイト列にする。

    行の中身は1バイトも変えない（値の桁・末尾の空白まで実機の形式の一部なので）。
    変えるのは行の区切り（EOB）だけ。実機が出力したファイルは LF CR CR だった。
    """
    eob = eob or fanuc.DEFAULT_EOB
    norm = str(text).replace("\r\n", "\n").replace("\r", "\n")
    # 空行は落とす。実機形式(LF CR CR)を読み直すと CR が空行に化けるので、
    # 落とさないと通すたびに区切りが増えていく（パラメータ行に空行は無い）。
    lines = [l for l in norm.split("\n") if l != ""]
    return "".join(l + eob for l in lines).encode("ascii", "ignore")


# 機械の個体ごとに決まる（＝他の号機へ持ち込んではいけない）パラメータ。
# 番号・ビットは機種で違うことがあるので、設定で変えられるようにしてある。
INDIVIDUAL_ORIGIN_PARAM = "1815"
INDIVIDUAL_ORIGIN_BIT = 5        # APZ = 原点確立済み
INDIVIDUAL_SHIFT_PARAM = "1850"  # グリッドシフト（原点の実測補正）
AXIS_LABELS = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")


def individual_data(text: str, *, origin_param=INDIVIDUAL_ORIGIN_PARAM,
                    origin_bit=INDIVIDUAL_ORIGIN_BIT,
                    shift_param=INDIVIDUAL_SHIFT_PARAM) -> list:
    """その機械の個体データ（原点・グリッドシフト）が入っていないかを見る。

    仕様（CNCユニット・モーター容量・アンプ）が同じ号機どうしでも、
    原点の位置は据付けごとに違う。これが入ったファイルを別の号機へ入れると、
    ・グリッドシフト … その機械で実測した原点補正が別の機械に入る
    ・原点確立済み(APZ=1) … 実際は確立していないのに確立済みとして扱われる
    ことになる。「容量が同じなら流用できる」が成り立たない理由がここ。

    戻り値: 見つかった項目の説明リスト（空なら個体データ無し）。
    """
    found = []
    hit_axes = [a for a in AXIS_LABELS
                if _bit_of(get_value(text, origin_param, a), origin_bit) == 1]
    if not hit_axes and _bit_of(get_value(text, origin_param), origin_bit) == 1:
        hit_axes = ["(軸指定なし)"]
    if hit_axes:
        found.append(f"原点確立済み（N{_norm_num(origin_param)} #{origin_bit} APZ=1）: "
                     + "・".join(hit_axes))
    shifts = []
    for a in AXIS_LABELS:
        v = get_value(text, shift_param, a)
        if v is None:
            continue
        try:
            if float(v) != 0.0:
                shifts.append(f"{a}={v}")
        except ValueError:
            continue
    if shifts:
        found.append(f"グリッドシフト（N{_norm_num(shift_param)}）: " + "・".join(shifts))
    return found


def validate_prm(text: str, reference: str = "") -> list:
    """機械が取込に失敗しそうな点を洗い出す（読ませる前の自己点検）。

    reference に実機のバックアップを渡すと、実機に無い番号も指摘する。
    """
    problems = []
    lines = [l for l in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
             if l.strip()]
    if not lines:
        return ["ファイルが空です"]
    # 先頭・末尾は % で始まっていればよい。実機は "%(CNCID=3C7B5D01,…)" のように
    # % の後ろに制御装置IDのコメントを付けて出すことがある（F24/F80 で確認）。
    if not lines[0].strip().startswith("%") or not lines[-1].strip().startswith("%"):
        problems.append("先頭と末尾が % になっていません（FANUCの読取開始/終了記号）")
    # 途中の % は「読取終わり」。そこから先が丸ごと無視される（実データで発見）。
    mid = [i for i, l in enumerate(lines[1:-1], start=2) if l.strip().startswith("%")]
    if mid:
        problems.append(
            f"途中に % があります（{len(mid)}箇所・最初は{mid[0]}行目 "
            f"{lines[mid[0] - 1].strip()[:24]!r}）。% は読取終わりの記号なので、"
            "そこから先が読み込まれません")
    if ";" in text:
        problems.append('";" は文字として書けません（EOBは改行）')
    bad = sorted({c for c in text if ord(c) > 127})
    if bad:
        problems.append(f"ASCIIでない文字が入っています {''.join(bad)[:8]!r}")
    if not any(param_number(l) is not None for l in lines):
        problems.append("N<番号>Q1… のパラメータ行が1つもありません")
    dup = duplicate_numbers(text)
    if dup:
        problems.append(
            f"同じ番号が2回以上出てくる行が {len(dup)}個 あります"
            f"（N{dup[0]}〜N{dup[-1]}）。アプリは最初の1つだけを読み書きするので、"
            "2つ目以降には製品値が入りません")
    if reference:
        extra = unknown_numbers(text, reference)
        if extra:
            problems.append(
                f"実機のバックアップに無い番号が {len(extra)}個 あります"
                f"（N{extra[0]:05d}〜N{extra[-1]:05d}）。"
                "その制御装置が持っていない番号は取込が止まる原因になります")
    return problems


# 1パラメータ行。行頭の余白(\r や空白)を group(1) に保持してから N<digits>[Q1]。
# 実機BASICは改行が \n\r\r 等まちまちで、split("\n") すると行頭に \r が残るため、
# 先頭余白を許容しつつ保存（出力のバイト一致のため余白はそのまま戻す）。
#
# 書式は2世代ある（実機のBASICを31本調べて確認）:
#   新 "N01825Q1A1P3000A2P3000"      … Q1 あり・空白なし
#   旧 "N01825 A1 P 3000 A2 P 3000"  … Q1 なし・空白区切り（F10〜F17 の8台）
# 旧書式を弾いていたため、その制御装置ではBASICを読めず、値が入らないまま
# マスタと違うファイルが出力されていた。両方を同じ仕組みで扱う。
_LINE = re.compile(r"^(\s*)(N(\d+)(?:Q\d+)?)(.*)$")
# セグメント: 任意のグループ(L1/A1..A4/S1/T1 等) + 型(P/M) + 値。
# 旧書式の空白（"A1 P 3000" の2か所）も捕まえて、書き戻すときにそのまま復元する。
_SEG = re.compile(r"([LASTlast]\d+)?(\s*)([PM])(\s*)([-+]?\d+(?:\.\d+)?)")
_VALUE = r"[-+]?\d+(?:\.\d+)?"


def segments(line: str):
    """1行のセグメント [(ラベル, 型, 値)] を返す。ラベルは 'L1'/'A4'/'S1'/'' など。"""
    m = _LINE.match(line)
    if not m:
        return None
    return [(g or "", t, v) for (g, _s1, t, _s2, v) in _SEG.findall(m.group(4))]


def param_number(line: str):
    m = _LINE.match(line)
    return m.group(3) if m else None


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
        if _norm_num(param_number(line)) == num:   # 行側のゼロ詰めゆらぎも吸収
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
        if done or _norm_num(param_number(line)) != num:
            continue
        m = _LINE.match(line)
        prefix, head, body = m.group(1), m.group(2), m.group(4)  # 先頭余白/N#####Q1/本体
        # body 内で、目的のセグメントの「値」だけを置換する
        out, pos, replaced = [], 0, False
        for sm in _SEG.finditer(body):
            g = (sm.group(1) or "")
            out.append(body[pos:sm.start()])
            if (not replaced) and (label is None or g.upper() == label.upper()):
                # ラベル・空白・型はそのまま、値だけ差替（旧書式の空白もバイト一致で戻す）
                out.append(f"{g}{sm.group(2)}{sm.group(3)}{sm.group(4)}{value}")
                replaced = True
            else:
                out.append(sm.group(0))
            pos = sm.end()
        out.append(body[pos:])
        if replaced:
            lines[i] = prefix + head + "".join(out)  # 先頭余白も復元（バイト一致）
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
    最後のフォールバックは「無ラベル」限定（""）。None（=先頭セグメント何でも）に
    すると、行に A1..A4 しか無いのに軸5を要求されたとき A1（X軸）へ黙って書いて
    しまう。該当スロットが無ければ失敗を返し、呼び側が「未反映」として報告する。
    """
    for label in (f"A{axis_num}", "L1", "S1", "T1", ""):
        new, ok = set_value(text, number, value, label)
        if ok:
            return new, True
    return text, False


def value_on_axis(text: str, number, axis_num):
    """set_on_axis が実際に書き込むスロットの現在値を返す（同じ探索順。無ければ None）。

    プレビューの「旧値」表示に使う。単純に「最初の値」へフォールバックすると、
    書き込み先が無いのに別軸(A1)の値が旧値として出て誤りを隠してしまう。
    """
    for label in (f"A{axis_num}", "L1", "S1", "T1", ""):
        v = get_value(text, number, label)
        if v is not None:
            return v
    return None


def _split_common_key(key) -> tuple:
    """共通値の辞書キーを (番号, ラベル) にほどく。

    キーは (番号, ラベル) でも 番号 だけでもよい。後者はラベル不明として扱う。
    """
    if isinstance(key, tuple):
        return key[0], (key[1] if len(key) > 1 else None)
    return key, None


def apply_product_values(text: str, values: dict, axis_num) -> tuple:
    """製品の値を BASIC の指定軸へ一括反映する。

    キーは 番号 でも (番号, ラベル) でもよい。ラベル付き（L2/S2 など軸でない
    スロット）は、指定軸ではなく<b>そのラベルのスロット</b>へ書く。
    軸ラベルは製品データの側で落としてある（別の軸へ入れ直せるようにするため）。
    戻り値: (新テキスト, 反映できなかった番号のリスト)。
    """
    missing = []
    for key, value in values.items():
        if value is None or str(value) == "":
            continue
        number, label = _split_common_key(key)
        if label:
            text, ok = set_common(text, number, value, label)
        else:
            text, ok = set_on_axis(text, number, value, axis_num)
        if not ok:
            missing.append(f"{number}({label})" if label else str(number))
    return text, missing


# 軸でないスロットの探索順。ラベルが分からないときだけ使う。
COMMON_LABELS = ("L1", "S1", "T1", "")


def set_common(text: str, number, value, label=None) -> tuple:
    """軸に属さない値(L1/L2…/S1/S2…/T1/無ラベル)を差し替える（A軸は触らない）。

    label を渡したら、そのスロットだけに書く。無ければ失敗を返す。
    多系統・複数主軸の機械では L2/L3/L4・S2〜S6 に別の値が入っており、
    ラベルを見ずに「最初に見つかったスロット」へ書くと、
    第2系統向けの値が第1系統(L1)に入って元の設定を壊す。
    label 省略時だけ従来どおり L1→S1→T1→無ラベル の順で探す。
    戻り値 (新テキスト, 成否)。
    """
    if label:
        return set_value(text, number, value, label)
    for lab in COMMON_LABELS:
        new, ok = set_value(text, number, value, lab)
        if ok:
            return new, True
    return text, False


def common_value(text: str, number, label=None):
    """set_common が実際に書き込むスロットの現在値（同じ探索順。無ければ None）。"""
    if label:
        return get_value(text, number, label)
    for lab in COMMON_LABELS:
        v = get_value(text, number, lab)
        if v is not None:
            return v
    return None


def apply_common_values(text: str, values: dict) -> tuple:
    """共通値を BASIC の共通スロットへ反映する。

    values のキーは {(番号, ラベル): 値}（推奨）または {番号: 値}。
    ラベルがあればそのスロットだけに書く（第2系統の値を第1系統に入れない）。
    戻り値 (新テキスト, 反映できなかった [番号 または "番号(ラベル)"] のリスト)。
    """
    missing = []
    for key, value in values.items():
        if value is None or str(value) == "":
            continue
        number, label = _split_common_key(key)
        text, ok = set_common(text, number, value, label)
        if not ok:
            missing.append(f"{number}({label})" if label else str(number))
    return text, missing


def axis_of_label(label) -> int:
    """ラベル 'A4'/'a2' → 軸番号 4/2。A軸でなければ None（L1/S1/T1/'' 等）。"""
    m = re.fullmatch(r"[Aa](\d+)", str(label or "").strip())
    return int(m.group(1)) if m else None


def diff_by_axis(master_text: str, product_text: str) -> tuple:
    """master(BASIC) と完成製品 .PRM の差分を「軸ごと」に分ける。

    2軸テーブルでは完成製品ファイルが傾斜軸・回転軸の両方を変更している。
    diff() は軸ラベルを保持するので、それを軸番号で振り分けて返す。
    戻り値: (per_axis: {軸番号: {番号: 値}}, common: {(番号, ラベル): 値})。
    common のキーにラベル(L1/L2/S1/S2…)を残すのは、多系統・複数主軸の機械で
    第2系統向けの値を第1系統(L1)へ入れないため。値が None（製品側に無い番号）は除外。
    """
    per_axis, common = {}, {}
    for (number, label, _mv, ov) in diff(master_text, product_text):
        if ov is None:
            continue
        ax = axis_of_label(label)
        if ax is None:
            # ラベル(L1/L2/S1/S2…)を残す。落とすと第2系統の値が第1系統へ入る。
            common[(number, label or "")] = ov
        else:
            per_axis.setdefault(ax, {})[number] = ov
    return per_axis, common


def looks_like_fanuc_prm(text: str) -> bool:
    """FANUCネイティブ形式（実機が読み書きする形）かどうか。

    新書式 "N01825Q1A1P3000" と、旧書式 "N01825 A1 P 3000"（Q1なし・空白区切り）の
    両方を見る。実機BASICは改行が \\n\\r\\r 等まちまちで行頭に \\r が残ることがあるため、
    行頭アンカーに頼らずパターンの有無で判定する。
    ヘッダ＋CSV形式（社内で読む .prm）はカンマ区切りなのでどちらにも当たらない。
    """
    text = text or ""
    if re.search(r"N\d+Q\d", text):
        return True
    # 旧書式。カンマ区切りのCSVを誤って拾わないよう、複数行あることを条件にする
    return len(re.findall(r"(?m)^\s*N\d{3,5}\s+(?:[LAST]\d+\s+)?[PM]\s*[-+]?\d", text)) >= 3


def duplicate_numbers(text: str) -> list:
    """同じ番号が2回以上出てくる場合、その番号を返す（多系統の制御装置など）。

    アプリは get_value/set_value とも「最初に出てきた行」を読み書きするので、
    重複があると2つ目以降は読まれず、書き換えもされない。実データでは
    F35BASIC（6軸）だけが該当し、2000番台が2回ずつ出ていた。
    """
    seen, dup = set(), set()
    for line in str(text).replace("\r", "\n").split("\n"):
        n = param_number(line)
        if n is None:
            continue
        n = _norm_num(n)
        if n in seen:
            dup.add(n)
        seen.add(n)
    return sorted(dup)


def values_map(text: str) -> dict:
    """{(番号, ラベル): 値} の辞書にする。ラベルは 'L1'/'A4'/'S1'/'' など。

    同じ番号が2回以上あるときは「最初の行」を採る。get_value/set_value と
    同じ行を指すようにするため（以前は最後が残り、読む場所によって値が
    食い違っていた）。重複そのものは duplicate_numbers で知らせる。
    """
    out = {}
    for line in text.splitlines():
        num = param_number(line)
        if num is None:
            continue
        for (g, t, v) in segments(line):
            out.setdefault((num, g), v)     # 先勝ち（get_value と同じ行を指す）
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


def set_bit_value(value, bit: int, on: bool) -> str:
    """ビットパラメータ value の bit 番ビットを on(True=1/False=0) にした値を返す。

    value がビット列('00000000' 左がMSB #7…#0)ならビット列のまま該当桁を変更（8桁未満は
    8桁にそろえる）。10進整数文字列なら整数として ON/OFF。先頭の ' は除去して扱う。
    """
    s = str(value).strip().lstrip("'\"").strip()
    if s and set(s) <= {"0", "1"}:                 # ビット列
        s = s.zfill(max(8, bit + 1))
        idx = len(s) - 1 - bit
        s = s[:idx] + ("1" if on else "0") + s[idx + 1:]
        return s
    try:
        n = int(s or "0", 10)
    except ValueError:
        # 数値でもビット列でもない（'*'残り等）→ 0基準で立てる
        n = 0
    n = (n | (1 << bit)) if on else (n & ~(1 << bit))
    return str(n)


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

