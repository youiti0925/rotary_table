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


def _merge_bits(product_val: str, basic_val: str) -> str:
    """ビット表示値の '*'(不問) を BASIC の現在ビットで埋める（MSB先頭で桁合わせ）。

    例: product '001*0000' / basic '00000000' → '00100000'（*の桁はBASICの0を残す）。
    """
    p, b = str(product_val), str(basic_val or "")
    n = max(len(p), len(b))
    p = p.rjust(n, "*")            # 足りない上位桁は不問＝BASICを残す
    b = b.rjust(n, "0")
    return "".join(b[i] if p[i] == "*" else p[i] for i in range(n))


def resolve_product_values(basic_text: str, values: dict, axis) -> dict:
    """ヘッダ＋CSV形式の表示値を、ネイティブBASICへ書ける実値へ解決する。

    - 先頭の ' (表示用クォート)を除去。
    - ビット値の '*'(不問) は BASIC の当該軸(無ければ共通)の現在ビットを残す（マージ）。
    BASIC がネイティブでない、または値に '/'* が無ければそのまま返す（実害なし）。
    """
    if not fanuc_param.looks_like_fanuc_prm(basic_text):
        return dict(values)
    out = {}
    for num, val in values.items():
        v = str(val).strip()
        if v.startswith("'"):
            v = v[1:].strip()
        if "*" in v:
            cur = fanuc_param.get_value(basic_text, num, f"A{axis}") if axis else None
            if cur is None:
                cur = fanuc_param.get_value(basic_text, num)
            v = _merge_bits(v, cur or "")
        out[num] = v
    return out


def build_text(raw: str, values: dict, axis: int, seiban: str = "") -> tuple:
    """BASIC テキスト raw に values({番号:値}) を入れた新テキストを返す。

    戻り値: (新テキスト, 反映できなかった番号リスト, 形式 'fanuc'/'headercsv')。
    N形式は指定軸だけ差替え（他軸・他バイトは不変）。ヘッダ＋CSV形式は Seiban も差替え。
    """
    if fanuc_param.looks_like_fanuc_prm(raw):
        values = resolve_product_values(raw, values, axis)   # '除去・*マージ
        newtext, missing = fanuc_param.apply_product_values(raw, values, axis)
        return newtext, missing, "fanuc"
    doc = prm_format.parse_prm(raw)
    if seiban:
        prm_format.header_set(doc, "Seiban", seiban)
    # missing は「実際に書けたか」で決める（列数が足りない行を書けたことにしない）
    missing = prm_format.apply_values(doc, values)
    return prm_format.format_prm(doc), missing, "headercsv"


def write_text(path, newtext: str, fmt: str = None, eob: str = None):
    """パラメータファイルを書く。

    N形式（実機ネイティブ）は、実機が自分で出力したファイルと同じ区切り（既定
    LF CR CR）に統一して書く。元にした BASIC が PC の改行（LFのみ等）だと、
    そのまま引き継ぐと制御装置が読み込めないため（実機で確認）。
    ヘッダ＋CSV形式（社内で読む .prm）は従来どおり cp932・改行そのまま。
    """
    if fmt is None:
        fmt = "fanuc" if fanuc_param.looks_like_fanuc_prm(newtext) else "headercsv"
    if fmt == "fanuc":
        Path(path).write_bytes(fanuc_param.prm_bytes(newtext, eob))
        return
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(newtext)


def filename(prefix: str, seiban: str, ext: str = ".DAT") -> str:
    """出力ファイル名 <頭文字><Seiban><拡張子>（例 T50013078.DAT）。

    拡張子の既定が .DAT なのは、実機が自分で出力するのが .DAT で、そちらは
    読み込めることが確認できているため（.prm は読めなかった）。設定で変えられる。
    """
    ext = str(ext or ".DAT")
    if not ext.startswith("."):
        ext = "." + ext
    return f"{prefix}{seiban}{ext}"


def create_file(master_path, out_dir, values: dict, *, axis: int, prefix: str,
                seiban: str, ext: str = ".DAT", eob: str = None) -> tuple:
    """BASIC を元に <頭文字><Seiban><拡張子> を out_dir に作成する。

    戻り値: (出力Path, 反映できなかった番号リスト, 形式)。
    """
    raw = read_master(master_path)
    newtext, missing, fmt = build_text(raw, values, axis, seiban)
    out = Path(out_dir) / filename(prefix, seiban, ext)
    write_text(out, newtext, fmt, eob)
    return out, missing, fmt


def product_axis_values(basic_text: str, product_text: str) -> tuple:
    """完成製品 .prm と BASIC の差分を「軸ごと」に返す（2軸テーブル用）。

    戻り値: (per_axis: {軸番号: {番号: 値}}, common: {番号: 値})。
    FANUC N形式（実機ネイティブ）専用。ヘッダ＋CSV形式は軸概念が無いので空を返す。
    """
    if not (fanuc_param.looks_like_fanuc_prm(basic_text)
            and fanuc_param.looks_like_fanuc_prm(product_text)):
        return {}, {}
    return fanuc_param.diff_by_axis(basic_text, product_text)


def with_servo_options(raw: str, axis, values: dict, *, zero_motor=False,
                       zero_param="2000", zero_bit=1, origin=None,
                       origin_param="1815", origin_bit=5) -> dict:
    """作成時のサーボ設定オプションを values({番号:値}) に上書きして返す（対象軸ぶん）。

    zero_motor=True → zero_param(既定2000)の zero_bit(既定#1 DGPR)を 0 にする
      （モーター番号変更時のデジタルサーボ再初期化。FANUC: DGPR=0→電源再投入→自動で1）。
    origin='on'/'off' → origin_param(既定1815)の origin_bit(既定#5 APZ＝原点確立)を 1/0 に。
    いずれも基準値は「製品が持つその番号の値（'/* を解決後）」、無ければ BASIC の当該軸値。
    戻り値は新しい dict（元は変更しない）。該当ビットだけ変え、他ビットは保持する。
    """
    out = dict(values)

    def _apply_bit(num, bit, on):
        key = None
        for k in out:                              # 既に同番号があればそれを基準に
            if fanuc_param._norm_num(k) == fanuc_param._norm_num(num):
                key = k
                break
        if key is not None:
            base = resolve_product_values(raw, {key: out[key]}, axis).get(key, out[key])
        else:
            # 実際に書き込むスロットの値を基準に（別軸の値をビット基準にしない）
            base = fanuc_param.value_on_axis(raw, num, axis) or "00000000"
            key = str(num)
        out[key] = fanuc_param.set_bit_value(base, int(bit), on)

    if zero_motor and zero_param:
        _apply_bit(zero_param, zero_bit, False)    # DGPR(#1)=0（他ビット保持）
    if origin in ("on", "off") and origin_param:
        _apply_bit(origin_param, origin_bit, origin == "on")
    return out


def product_change_values(basic_text: str, product_text: str) -> dict:
    """完成製品 .prm から「BASICへ入れる変更値 {番号:値}」を平坦に取り出す。

    軸へ再ターゲット（別の軸へ入れ直す）できるよう、軸ラベルを落として番号→値で返す。
    FANUC N形式（完成製品）は BASIC との差分、ヘッダ＋CSV形式は全パラメータ値。
    かんたん作成（Seiban起点）で、製品の傾斜/回転ファイルを号機の割当軸へ入れるのに使う。
    """
    if fanuc_param.looks_like_fanuc_prm(product_text):
        if fanuc_param.looks_like_fanuc_prm(basic_text):
            return {num: ov for (num, _l, _m, ov)
                    in fanuc_param.diff(basic_text, product_text) if ov is not None}
        return {num: v for (num, _l), v in fanuc_param.values_map(product_text).items()}
    try:
        doc = prm_format.parse_prm(product_text)
        return {num: v for (num, v, _jp, _en) in prm_format.iter_params(doc) if v != ""}
    except Exception:
        return {}


def build_text_multi(raw: str, axis_values: dict, common: dict = None,
                     seiban: str = "") -> tuple:
    """BASIC raw に「複数軸ぶんの値」を入れた新テキストを返す（2軸テーブル用）。

    axis_values={軸番号: {番号:値}} を各軸へ、common={番号:値} を共通スロットへ適用。
    1つの BASIC（=1つの制御装置）に傾斜軸・回転軸の両方を入れて 1ファイルにする。
    （フル backup を軸ごとに2ファイル作ると、片方が他軸を BASIC 値へ戻してしまうため、
    1ファイルに両軸を入れるのが正しい。）
    戻り値: (新テキスト, 反映できなかった [(番号, 軸), ...], 形式 'fanuc'/'headercsv')。
    """
    axis_values = axis_values or {}
    common = common or {}
    if not fanuc_param.looks_like_fanuc_prm(raw):
        # ヘッダ＋CSV形式は軸が無い。全値を束ねて従来処理（軸=0）にフォールバック
        merged = dict(common)
        for vals in axis_values.values():
            merged.update(vals)
        newtext, missing, fmt = build_text(raw, merged, 0, seiban)
        return newtext, [(m, "") for m in missing], fmt
    text = raw
    missing = []
    for ax in sorted(axis_values):
        vals = resolve_product_values(raw, axis_values[ax], ax)   # '除去・*マージ
        text, miss = fanuc_param.apply_product_values(text, vals, ax)
        missing += [(m, ax) for m in miss]
    cvals = resolve_product_values(raw, common, None)
    text, miss = fanuc_param.apply_common_values(text, cvals)
    missing += [(m, "") for m in miss]
    return text, missing, "fanuc"


def create_file_multi(master_path, out_dir, axis_values: dict, common: dict = None,
                      *, prefix: str, seiban: str, ext: str = ".DAT",
                      eob: str = None) -> tuple:
    """BASIC を元に、複数軸ぶんを入れた <頭文字><Seiban><拡張子> を作成する（2軸用）。

    戻り値: (出力Path, 反映できなかった [(番号, 軸), ...], 形式)。
    """
    raw = read_master(master_path)
    newtext, missing, fmt = build_text_multi(raw, axis_values, common, seiban)
    out = Path(out_dir) / filename(prefix, seiban, ext)
    write_text(out, newtext, fmt, eob)
    return out, missing, fmt


def preview_rows_multi(raw: str, axis_values: dict, common: dict = None) -> list:
    """2軸テーブルの作成前プレビュー [(番号, 軸名, 旧値, 新値)]。

    旧値は BASIC(raw) の各軸の値。軸名は呼び側で表示名へ変換できるよう軸番号で返す
    （common は軸="")。N形式専用。
    """
    axis_values = axis_values or {}
    common = common or {}
    rows = []
    for ax in sorted(axis_values):
        vals = resolve_product_values(raw, axis_values[ax], ax)  # 表示も実際に書く値で
        for num, newv in vals.items():
            if newv == "":
                continue
            # 実際に書き込むスロットの現在値（別軸の値を旧値として出さない）
            old = fanuc_param.value_on_axis(raw, num, ax)
            rows.append((str(num), ax, "" if old is None else str(old), str(newv)))
    for num, newv in resolve_product_values(raw, common, None).items():
        if newv == "":
            continue
        old = fanuc_param.common_value(raw, num)
        rows.append((str(num), "", "" if old is None else str(old), str(newv)))
    return rows


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
    if is_fanuc:
        values = resolve_product_values(raw, values, axis)   # 表示も実際に書く値で
    else:
        try:
            doc = prm_format.parse_prm(raw)
        except Exception:
            doc = None
    rows = []
    for num, newv in values.items():
        if newv == "":
            continue
        if is_fanuc:
            # 実際に書き込むスロットの現在値（別軸の値を旧値として出さない）
            old = fanuc_param.value_on_axis(raw, num, axis)
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
        # 表示用の ' や不問ビット * を実際に書く値へ解決してから判定する
        # （'0000001* のような製品値でもモードが読めるように）
        vals = resolve_product_values(raw, values, axis)
        eff = fanuc_param.effective_value(raw, vals, number, f"A{axis}")
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
