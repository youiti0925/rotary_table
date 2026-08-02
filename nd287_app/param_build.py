# -*- coding: utf-8 -*-
"""BASIC(.prm) を元に、製品の変更値を指定軸へ入れて 1 ファイル作る処理の共通部分。

GUI（パラメータ画面・パラメータDB画面）から共通で使う。Qt 非依存なので単体試験可。
N形式（実機ネイティブ）はバイト保存編集、ヘッダ＋CSV形式は構造を保ったまま差替え。
"""

from pathlib import Path

from . import fanuc_param, param_origin, prm_format


def _is_new_format(text: str) -> bool:
    """パラメータの書式世代（新 "N01825Q1…" / 旧 "N01825 A1 P …"）。"""
    import re
    return bool(re.search(r"N\d+Q\d", (text or "")[:3000]))


def machine_reference(folder, like: str = "", exclude=None) -> tuple:
    """フォルダにある「実機が出したBASIC」を集めて、番号照合用の参照を作る。

    同じ号機の実機バックアップが無いとき（PC側のBASICしか無いとき）に使う。
    1本と突き合わせるのではなく、フォルダ内の実機BASIC全部の「番号の和集合」と
    比べる。どの実機も持っていない番号だけが引っかかるので、
    号機ごとの差でむやみに警告しない。

    実データでの裏づけ: 実機BASIC 16本の和集合に N09802・N09820〜N09999 は
    1つも無く、PC製7本には全部入っていた（PC側ツールが足したもの）。

    like  … 書式世代をそろえるための見本テキスト（空なら世代を問わない）
    戻り値: (参照テキスト, 使った実機ファイル名のリスト)
    """
    base = Path(folder) if folder else None
    if not base or not base.is_dir():
        return "", []
    want_new = _is_new_format(like) if like else None
    numbers, used = set(), []
    for p in sorted(base.iterdir()):
        if not p.is_file() or (exclude and Path(exclude).resolve() == p.resolve()):
            continue
        try:
            data = p.read_bytes()
        except Exception:
            continue
        if param_origin.classify(data)["verdict"] not in ("machine", "converted"):
            continue
        text = data.decode("cp932", errors="replace")
        if want_new is not None and _is_new_format(text) != want_new:
            continue
        nums = fanuc_param.numbers_in(text)
        if not nums:
            continue
        numbers |= nums
        used.append(p.name)
    if not used:
        return "", []
    # numbers_in が読める最小の形で参照テキストを組む（値は照合に使わない）
    ref = "%\n" + "\n".join(f"N{n:05d}Q1P0" for n in sorted(numbers)) + "\n%\n"
    return ref, used


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
    - 数値は実機が出す書き方へそろえる（"+108.000" → "108.0"。値は変えない）。
    BASIC がネイティブでない場合はそのまま返す（実害なし）。
    """
    if not fanuc_param.looks_like_fanuc_prm(basic_text):
        return dict(values)
    # 旧書式(8台)のファイルは小数を1つも使わない＝値は最小設定単位の整数。
    # そこへ小数を書くと書式違反になるが、勝手に落とすと1000倍ずれる恐れがある。
    # その世代では数値に触らず、書く前の点検で気づかせる。
    allow_decimal = _is_new_format(basic_text)
    out = {}
    for key, val in values.items():
        # キーは 番号 でも (番号, ラベル) でもよい。共通値はラベル付きで来る
        num, label = fanuc_param._split_common_key(key)
        v = str(val).strip()
        if v.startswith("'"):
            v = v[1:].strip()
        if label:
            cur = fanuc_param.get_value(basic_text, num, label)
        else:                          # 実際に書き込むスロットの現在値を基準にする
            cur = fanuc_param.value_on_axis(basic_text, num, axis) if axis else None
            if cur is None:
                cur = fanuc_param.get_value(basic_text, num)
        if "*" in v:
            v = _merge_bits(v, cur or "")
        else:
            v = fanuc_param.normalize_number(v, cur, allow_decimal)
        out[key] = v
    return out


def drop_params(values: dict, params) -> tuple:
    """製品値から指定番号を取り除く。戻り値 (残った値, 取り除いた {キー:値})。

    キーは 番号 でも (番号, ラベル) でもよい（軸ラベル付きの共通値も落とす）。
    """
    if not params:
        return dict(values or {}), {}
    want = {fanuc_param._norm_num(x) for x in params}
    keep, dropped = {}, {}
    for key, v in (values or {}).items():
        num, _label = fanuc_param._split_common_key(key)
        if fanuc_param._norm_num(num) in want:
            dropped[key] = v          # キーの形（番号 / (番号,ラベル)）を保つ
        else:
            keep[key] = v
    return keep, dropped


def drop_zero_params(values: dict, zero_params) -> tuple:
    """製品値から「0にする番号」を取り除く。戻り値 (残った値, 取り除いた {番号:値})。

    製品データは、別の号機の完成品と BASIC の差分から作られる。つまり
    その号機の 1850(グリッドシフト)・1851/1852(バックラッシ補正) が
    そのまま混ざってくる。0にしてから製品値を入れると、0にした直後に
    別の機械の実測値で上書きされてしまう（しかも軸も別のところへ入る）。
    出荷ファイルではこれらは0にするので、製品値の側から先に外す。
    """
    return drop_params(values, zero_params)


def build_text(raw: str, values: dict, axis: int, seiban: str = "",
               zero_params=None, soft_limit=None, soft_params=None) -> tuple:
    """BASIC テキスト raw に values({番号:値}) を入れた新テキストを返す。

    戻り値: (新テキスト, 反映できなかった番号リスト, 形式 'fanuc'/'headercsv')。
    N形式は指定軸だけ差替え（他軸・他バイトは不変）。ヘッダ＋CSV形式は Seiban も差替え。
    zero_params を渡すと、その番号（既定はグリッドシフト・バックラッシ補正）を
    全軸0にする。出荷するファイルに前の機械の実測値を残さないため。
    soft_limit は 'apply'(既定・客先どおり) / 'skip'(入れない) / 'disable'(無効化)。
    """
    soft_params = _soft_params(soft_params)
    if soft_mode(soft_limit) != "apply":
        # 客先パラメータのソフトリミットを入れない（検査中は邪魔になることがある）
        values, _s = drop_params(values, soft_params)
    if fanuc_param.looks_like_fanuc_prm(raw):
        values = resolve_product_values(raw, values, axis)   # '除去・*マージ（元のBASICを見る）
        # 製品データが持ち込む個体データ（別の号機の実測値）を先に外してから0にする
        values, _dropped = drop_zero_params(values, zero_params)
        text = raw
        if zero_params:
            text, _z = zero_individual(text, zero_params)
        if soft_mode(soft_limit) == "disable":
            text, _r = disable_soft_limit(text, [axis], soft_params)
        newtext, missing = fanuc_param.apply_product_values(text, values, axis)
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


# ファイル名に入れる種別の記号（制御装置側で化けないよう半角英数にする）
KIND_TAG = {"傾斜": "TILT", "回転": "ROT", "TILT": "TILT", "ROT": "ROT"}
_NAME_OK = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"


def name_tag(text) -> str:
    """ファイル名に入れられる形（半角大文字・英数と-）にする。使えない字は落とす。

    メモリカード経由で制御装置に見せるので、全角や記号は入れない。
    """
    import unicodedata
    # 全角で入力された型式（ＲＴＴ－１３５）も半角へ直してから使う
    s = unicodedata.normalize("NFKC", str(text or "")).strip().upper()
    return "".join(c if c in _NAME_OK else ("-" if c in " _/." else "")
                   for c in s).strip("-")


def axis_tag(axis, kind: str = "") -> str:
    """軸と種別をファイル名用の記号にする（('Z','傾斜') → 'Z-TILT'）。"""
    ax = name_tag(axis)
    k = KIND_TAG.get(str(kind or "").strip(), name_tag(kind))
    return "-".join(x for x in (ax, k) if x)


def filename(prefix: str, seiban: str, ext: str = ".DAT", *, model: str = "",
             axes=(), detail: bool = True, sep: str = "_") -> str:
    """出力ファイル名 <頭文字><Seiban>[_型式][_軸-種別...]<拡張子>。

    例: filename("T", "50013078", model="RTT-135", axes=[("Z", "傾斜")])
        → 'T50013078_RTT-135_Z-TILT.DAT'
    型式・軸・種別を入れておくと、フォルダにまとめて作ったときに中身が分かる。
    detail=False（設定 param_name_detail=false）で従来の <頭文字><Seiban> に戻せる。
    拡張子の既定が .DAT なのは、実機が自分で出力するのが .DAT で、そちらは
    読み込めることが確認できているため（.prm は読めなかった）。設定で変えられる。
    """
    ext = str(ext or ".DAT")
    if not ext.startswith("."):
        ext = "." + ext
    stem = f"{prefix}{seiban}"
    if detail:
        parts = [name_tag(model)]
        for a in (axes or ()):
            parts.append(axis_tag(*a) if isinstance(a, (tuple, list)) else axis_tag(a))
        stem = str(sep or "_").join([stem] + [p for p in parts if p])
    return f"{stem}{ext}"


# 出力先（メモリカード）の目安。制御装置の画面にファイルが出ないことがあるため。
#   実例: カードに入っていたファイルを全部消して1本だけにしたら、そこで初めて
#   制御装置の画面に出てきた（長い名前でも出た）。空き容量そのものだけでなく、
#   ファイルの数（FATのディレクトリ枠）も効くので、両方を作る前に見せる。
CARD_FILE_WARN = 100          # このフォルダにこれ以上あったら知らせる
CARD_FREE_WARN = 2 * 1024 * 1024   # 空きがこれ未満なら知らせる


def _fmt_size(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        n = 0.0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def folder_status(out_dir, need: int = 0, *, file_warn: int = CARD_FILE_WARN,
                  free_warn: int = CARD_FREE_WARN) -> tuple:
    """出力先の空き容量とファイル数を調べる。戻り値 (説明文, 警告リスト)。

    メモリカードが一杯だったり、ファイルが多すぎたりすると、書けてはいても
    <b>制御装置の画面にファイルが出てこない</b>（実機で確認）。作る前に知らせる。
    """
    import shutil
    try:
        need = int(need or 0)
    except (TypeError, ValueError):
        need = 0            # 呼び側が変な値を渡しても止まらない
    try:
        p = Path(out_dir) if out_dir else None
    except TypeError:
        return "", []
    if not p or not p.is_dir():
        return "", []
    try:
        files = [f for f in p.iterdir() if f.is_file()]
    except OSError as e:
        return "", [f"出力先を読めません: {e}"]
    try:
        usage = shutil.disk_usage(str(p))
        free, total = usage.free, usage.total
    except OSError:
        free = total = None
    note = f"出力先: ファイル {len(files)}個"
    if free is not None:
        note += f" / 空き {_fmt_size(free)}（全体 {_fmt_size(total)}）"
    warn = []
    if free is not None and need and free < need:
        warn.append(f"空き容量が足りません（必要 {_fmt_size(need)} / 空き "
                    f"{_fmt_size(free)}）。カードのファイルを減らしてください")
    elif free is not None and free < free_warn:
        warn.append(f"空き容量が少ないです（{_fmt_size(free)}）。"
                    "書けても制御装置の画面に出てこないことがあります")
    if len(files) >= file_warn:
        warn.append(f"このフォルダにファイルが {len(files)}個 あります。"
                    "多いと制御装置の画面に出てこないことがあります"
                    "（実機で、全部消して1本だけにしたら出てきた例があります）。"
                    "使わないファイルは減らすか、フォルダに分けてください")
    return note, warn


def check_received(path, reference: str = "") -> tuple:
    """機械から受け取ったファイルをその場で点検する。戻り値 (説明, 問題リスト)。

    バックアップは<b>取った直後に見ないと壊れていても気づけない</b>。
    実データの F35BASIC は途中に % があって258行が読まれない状態だったが、
    誰も気づかないまま置かれていた。FTPで受け取った瞬間にここで知らせる。
    """
    try:
        p = Path(path)
    except TypeError:
        return "", ["ファイルの場所が正しくありません"]
    try:
        data = p.read_bytes()
    except (OSError, TypeError, ValueError) as e:
        return "", [f"読めません: {e}"]
    if not data.strip():
        return "", ["中身が空です"]
    info = param_origin.classify(data)
    text = data.decode("cp932", errors="replace")
    kind = info.get("kind") or ""
    note = (f"{p.name}｜{info.get('label', '')}"
            f"｜区切り {info.get('eob', '?')}｜{len(data):,}バイト")
    if kind == "パラメータ" or fanuc_param.looks_like_fanuc_prm(text):
        problems = fanuc_param.validate_prm(text, reference)
        nums = fanuc_param.numbers_in(text)
        if nums:
            note += f"｜番号 {len(nums)}個"
    else:
        from . import fanuc
        problems = fanuc.validate(text)
    return note, problems


def create_file(master_path, out_dir, values: dict, *, axis: int, prefix: str,
                seiban: str, ext: str = ".DAT", eob: str = None,
                zero_params=None, soft_limit=None, soft_params=None,
                name: str = "") -> tuple:
    """BASIC を元に <頭文字><Seiban><拡張子> を out_dir に作成する。

    name を渡すとそのファイル名で作る（画面のプレビューに出した名前と必ず一致させる）。
    戻り値: (出力Path, 反映できなかった番号リスト, 形式)。
    """
    raw = read_master(master_path)
    newtext, missing, fmt = build_text(raw, values, axis, seiban, zero_params,
                                       soft_limit, soft_params)
    out = Path(out_dir) / (name or filename(prefix, seiban, ext))
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


# 出荷するファイルでは0にしておく項目（実機で「ゼロで大丈夫」と確認）。
#   1850 グリッドシフト     … 原点の実測補正。据付けごとに違うので持ち込まない
#   1851 バックラッシ補正量  … 機械個体の測定値
#   1852 早送り時のバックラッシ補正量
# 番号は機種で違うことがあるので settings の "zero_individual_params" で変更できる。
ZERO_INDIVIDUAL_PARAMS = ("1850", "1851", "1852")


def individual_zero_rows(raw: str, params=ZERO_INDIVIDUAL_PARAMS) -> list:
    """0にすべき項目のうち、いま0でないものを [(番号, ラベル, 旧値)] で返す。

    軸を限らず、そのファイルに入っている全軸を対象にする。作る軸だけ0にしても、
    他の軸に前の機械の値が残ったまま出荷されてしまうため。
    """
    params = params or ()          # None/空 は「0にしない」（既定へ戻さない）
    if not fanuc_param.looks_like_fanuc_prm(raw):
        return []
    out = []
    for num in params:
        for line in str(raw).replace("\r", "\n").split("\n"):
            if fanuc_param._norm_num(fanuc_param.param_number(line)) \
                    != fanuc_param._norm_num(num):
                continue
            for (label, _t, value) in (fanuc_param.segments(line) or []):
                try:
                    if float(value) == 0.0:
                        continue
                except ValueError:
                    continue
                out.append((str(num), label, value))
            break
    return out


def zero_individual(raw: str, params=ZERO_INDIVIDUAL_PARAMS) -> tuple:
    """グリッドシフト・バックラッシ補正を全軸0にする。

    戻り値: (新テキスト, [(番号, ラベル, 旧値)])。0にした所だけが変わる。
    """
    rows = individual_zero_rows(raw, params)
    text = raw
    for num, label, _old in rows:
        text, _ok = fanuc_param.set_value(text, num, "0", label or None)
    return text, rows


# --- ソフトリミット（記憶式ストロークリミット1）------------------------------
#   1320 … 各軸のストロークリミット＋側 / 1321 … −側
# 客先パラメータには機械の可動範囲が入っているが、社内の検査では入れたくないことが
# ある（検査の割出しが範囲外になって動かせない）。そこで作成時に選べるようにした。
#   apply   … 客先指定どおり入れる（既定）
#   skip    … 入れない。BASICの値をそのまま残す
#   disable … 無効化する。BASICが持っている値（＋側 -1 / −側 +1）を書く
# 実データの裏づけ: 手元のBASIC 31本すべてが 1320=-1 / 1321=+1（未使用軸は 0.0）で、
# それ以外の値は1つも無かった。つまり BASIC の出荷時状態＝この値。
SOFT_LIMIT_PARAMS = ("1320", "1321")
SOFT_LIMIT_OFF = {"1320": "-1", "1321": "1"}
SOFT_LIMIT_MODES = ("apply", "skip", "disable")


def _soft_params(params):
    """ソフトリミットの番号。None は既定、空リストは「対象なし」。

    zero_individual_params と同じ扱いにそろえる（空＝無効。空で既定へ戻さない）。
    """
    return SOFT_LIMIT_PARAMS if params is None else tuple(params)


def soft_mode(mode) -> str:
    """ソフトリミットの指定を 'apply'/'skip'/'disable' に正規化する（不明は apply）。"""
    m = str(mode or "").strip().lower()
    return m if m in SOFT_LIMIT_MODES else "apply"


def _decimal_style(raw: str, number, label: str) -> bool:
    """その行が小数付きで書かれているか（-1.0 か -1 か）。

    書き込む所の今の値では判断しない。そこには客先値（-100.0 など）が入っている
    ことがあり、元が整数書式のファイルに -1.0 を書いてしまう。同じ行の他の軸に
    合わせ、決められなければ書式世代（新＝小数付き / 旧＝整数）で決める。
    実データ: 旧書式8本は "A1 P-1"、新書式は "A1P-1.0" で、混ざった例は無かった。
    """
    num = fanuc_param._norm_num(number)
    for line in str(raw).replace("\r", "\n").split("\n"):
        if fanuc_param._norm_num(fanuc_param.param_number(line)) != num:
            continue
        others = [v for (g, _t, v) in (fanuc_param.segments(line) or [])
                  if g.upper() != str(label or "").upper()]
        dec = sum(1 for v in others if "." in v)
        if others and dec * 2 != len(others):
            return dec * 2 > len(others)
        break
    return _is_new_format(raw)


def soft_limit_off_value(raw: str, number, label: str) -> str:
    """その番号の「制限なし」値（-1 / -1.0）。小数点の有無はファイルに合わせる。"""
    base = SOFT_LIMIT_OFF.get(str(fanuc_param._norm_num(number)).lstrip("0"))
    if base is None:
        return None
    return base + ".0" if _decimal_style(raw, number, label) else base


def _same_number(a, b) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def soft_limit_rows(raw: str, axes, params=SOFT_LIMIT_PARAMS) -> list:
    """無効化で書き換わる所を [(番号, ラベル, 旧値, 新値)] で返す（既に無効なら出ない）。

    対象は作成する軸だけ。全軸へ書くと、同じファイルに入っている別軸（2軸テーブルの
    相手軸など）の設定まで消してしまうため。軸が無い番号は対象外。
    """
    if not axes or not fanuc_param.looks_like_fanuc_prm(raw):
        return []
    out = []
    for num in _soft_params(params):
        for ax in axes:
            label = f"A{ax}"
            cur = fanuc_param.get_value(raw, num, label)
            if cur is None:
                continue                       # その軸のスロットが無い
            new = soft_limit_off_value(raw, num, label)
            if new is None or _same_number(cur, new):
                continue                       # 既に「制限なし」
            out.append((str(num), label, str(cur), new))
    return out


def disable_soft_limit(raw: str, axes, params=SOFT_LIMIT_PARAMS) -> tuple:
    """指定軸のソフトリミットを「制限なし」にする。戻り値 (新テキスト, 変更行)。"""
    rows = soft_limit_rows(raw, axes, params)
    text = raw
    for num, label, _old, new in rows:
        text, _ok = fanuc_param.set_value(text, num, new, label)
    return text, rows


def soft_limit_preview(raw: str, values: dict, axes, mode,
                       params=SOFT_LIMIT_PARAMS) -> list:
    """作成前プレビューに足す行 [(番号, ラベル, 旧値, 新値, 変わるか)]。

    skip  … 客先値を入れないので「そのまま」と出す（黙って落とさない）
    disable … 実際に書く値を出す

    5つ目の「変わるか」を返すのは、画面が<b>新値の文字列を見て</b>件数を数えて
    いたため。「そのまま（入れない）」も「変わった」と数えられ、全5件中5件が
    変更と出ていた（実際に変わるのは1件）。文言を変えるたびに壊れる作りなので、
    数えるための旗をこちらで持つ。
    """
    mode = soft_mode(mode)
    if mode == "apply":
        return []
    if mode == "disable":
        return [(n, l, o, v, True) for (n, l, o, v) in soft_limit_rows(raw, axes, params)]
    _keep, dropped = drop_params(values, _soft_params(params))
    rows = []
    for key in sorted(dropped, key=str):
        num, label = fanuc_param._split_common_key(key)
        for ax in (axes or [None]):
            lab = label or (f"A{ax}" if ax else None)
            cur = fanuc_param.get_value(raw, num, lab) if lab else None
            rows.append((str(num), lab or "", "" if cur is None else str(cur),
                         "そのまま（入れない）", False))
    return rows


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

    軸へ再ターゲット（別の軸へ入れ直す）できるよう、<b>軸ラベルだけ</b>を落とす。
    L2/L3/S2… のような軸でないラベルは (番号, ラベル) のまま残す。落とすと
    第2系統向けの値が第1系統(L1)へ入り、元の設定を壊す。
    FANUC N形式（完成製品）は BASIC との差分、ヘッダ＋CSV形式は全パラメータ値。
    かんたん作成（Seiban起点）で、製品の傾斜/回転ファイルを号機の割当軸へ入れるのに使う。
    """
    if fanuc_param.looks_like_fanuc_prm(product_text):
        if fanuc_param.looks_like_fanuc_prm(basic_text):
            out = {}
            for (num, lab, _m, ov) in fanuc_param.diff(basic_text, product_text):
                if ov is None:
                    continue
                if lab and fanuc_param.axis_of_label(lab) is None:
                    out[(num, lab)] = ov   # L2/S2 等はラベルを残す（別系統を壊さない）
                else:
                    out[num] = ov          # 軸は再ターゲットするので落とす
            return out
        out = {}
        for (num, lab), v in fanuc_param.values_map(product_text).items():
            if lab and fanuc_param.axis_of_label(lab) is None:
                out[(num, lab)] = v
            else:
                out[num] = v
        return out
    try:
        doc = prm_format.parse_prm(product_text)
        return {num: v for (num, v, _jp, _en) in prm_format.iter_params(doc) if v != ""}
    except Exception:
        return {}


def build_text_multi(raw: str, axis_values: dict, common: dict = None,
                     seiban: str = "", zero_params=None,
                     soft_limit=None, soft_params=None) -> tuple:
    """BASIC raw に「複数軸ぶんの値」を入れた新テキストを返す（2軸テーブル用）。

    axis_values={軸番号: {番号:値}} を各軸へ、common={番号:値} を共通スロットへ適用。
    1つの BASIC（=1つの制御装置）に傾斜軸・回転軸の両方を入れて 1ファイルにする。
    （フル backup を軸ごとに2ファイル作ると、片方が他軸を BASIC 値へ戻してしまうため、
    1ファイルに両軸を入れるのが正しい。）
    戻り値: (新テキスト, 反映できなかった [(番号, 軸), ...], 形式 'fanuc'/'headercsv')。
    """
    axis_values = axis_values or {}
    common = common or {}
    soft_params = _soft_params(soft_params)
    skip_soft = soft_mode(soft_limit) != "apply"
    if not fanuc_param.looks_like_fanuc_prm(raw):
        # ヘッダ＋CSV形式は軸が無い。全値を束ねて従来処理（軸=0）にフォールバック
        merged = dict(common)
        for vals in axis_values.values():
            merged.update(vals)
        newtext, missing, fmt = build_text(raw, merged, 0, seiban, None,
                                           soft_limit, soft_params)
        return newtext, [(m, "") for m in missing], fmt
    text = raw
    if zero_params:
        text, _z = zero_individual(text, zero_params)
    if soft_mode(soft_limit) == "disable":
        text, _r = disable_soft_limit(text, sorted(axis_values), soft_params)
    missing = []
    for ax in sorted(axis_values):
        vals = resolve_product_values(raw, axis_values[ax], ax)   # '除去・*マージ
        vals, _d = drop_zero_params(vals, zero_params)   # 個体データは持ち込まない
        if skip_soft:
            vals, _s = drop_params(vals, soft_params)    # 客先ソフトリミットを入れない
        text, miss = fanuc_param.apply_product_values(text, vals, ax)
        missing += [(m, ax) for m in miss]
    cvals = resolve_product_values(raw, common, None)
    cvals, _d = drop_zero_params(cvals, zero_params)
    if skip_soft:
        cvals, _s = drop_params(cvals, soft_params)
    text, miss = fanuc_param.apply_common_values(text, cvals)
    missing += [(m, "") for m in miss]
    return text, missing, "fanuc"


def create_file_multi(master_path, out_dir, axis_values: dict, common: dict = None,
                      *, prefix: str, seiban: str, ext: str = ".DAT",
                      eob: str = None, zero_params=None, soft_limit=None,
                      soft_params=None, name: str = "") -> tuple:
    """BASIC を元に、複数軸ぶんを入れた <頭文字><Seiban><拡張子> を作成する（2軸用）。

    name を渡すとそのファイル名で作る（画面のプレビューに出した名前と必ず一致させる）。
    戻り値: (出力Path, 反映できなかった [(番号, 軸), ...], 形式)。
    """
    raw = read_master(master_path)
    newtext, missing, fmt = build_text_multi(
        raw, axis_values, common, seiban, zero_params, soft_limit, soft_params)
    out = Path(out_dir) / (name or filename(prefix, seiban, ext))
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
        for key, newv in vals.items():
            if newv == "":
                continue
            # キーは 番号 でも (番号, ラベル) でもよい。ほどかずに使うと
            # 番号欄に "('01800', 'L1')" と出て旧値が空欄になる（実データで38%）
            num, label = fanuc_param._split_common_key(key)
            if label:
                old = fanuc_param.common_value(raw, num, label)
                shown = f"{num}({label})"
            else:
                # 実際に書き込むスロットの現在値（別軸の値を旧値として出さない）
                old = fanuc_param.value_on_axis(raw, num, ax)
                shown = str(num)
            rows.append((shown, ax, "" if old is None else str(old), str(newv)))
    for key, newv in resolve_product_values(raw, common, None).items():
        if newv == "":
            continue
        num, label = fanuc_param._split_common_key(key)
        old = fanuc_param.common_value(raw, num, label)
        rows.append((f"{num}({label})" if label else str(num), "",
                     "" if old is None else str(old), str(newv)))
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
    for key, newv in values.items():
        if newv == "":
            continue
        num, label = fanuc_param._split_common_key(key)   # ラベル付きキーもほどく
        if is_fanuc and label:
            old = fanuc_param.common_value(raw, num, label)
            shown = f"{num}({label})"
        elif is_fanuc:
            # 実際に書き込むスロットの現在値（別軸の値を旧値として出さない）
            old = fanuc_param.value_on_axis(raw, num, axis)
            shown = str(num)
        else:
            old = prm_format.param_value(doc, num) if doc else None
            shown = str(num)
        rows.append((shown, "" if old is None else str(old), str(newv)))
    return rows


def preview_zero_rows(raw: str, params=ZERO_INDIVIDUAL_PARAMS) -> list:
    """0にする項目のプレビュー行 [(番号(軸), 旧値, "0")]。"""
    return [(f"{num}({label})" if label else str(num), old, "0")
            for num, label, old in individual_zero_rows(raw, params)]


# プレビュー行の区分（画面で色分け・件数の数え方を変えるために使う）
KIND_PRODUCT = "製品値"
KIND_ZERO = "0にする"
KIND_SOFT = "ソフトリミット"
KIND_MISSING = "BASICに無い"


def prepare(raw: str, values: dict = None, *, axis=None, axis_values: dict = None,
            common: dict = None, seiban: str = "", zero_params=None,
            soft_limit=None, soft_params=None, eob=None, reference: str = "") -> dict:
    """作成の中身を1回だけ決める。<b>プレビューと書き込みで必ず同じものを使う</b>。

    これを作った理由: 同じ処理が作成画面5経路にコピーされていて、
    片方だけ直す事故が実際に2回起きた（0化のプレビューがDBの2経路だけ抜ける、
    ソフトリミットの除外が5経路すべてで抜ける）。プレビューが「108.0を書く」と
    言いながら1バイトも書かない、という一番まずい状態になっていた。

    戻り値 dict:
      rows     … プレビュー行 [(番号, 軸, 旧値, 新値, 区分, 変わるか)]
      newtext / missing / fmt … 書き込む内容
      applied / total … 反映できた数（実際に書く値で数える）
      need     … 出力の実バイト数（カードの空き確認に使う）
      problems … 書く前の点検（validate_prm）
      values / axis_values / common … 実際に書く値（除外・解決済み）
    """
    single = axis_values is None
    axis_values = dict(axis_values or ({} if values is None else {axis: values}))
    common = dict(common or {})
    zero_params = tuple(zero_params or ())
    sparams = _soft_params(soft_params)
    mode = soft_mode(soft_limit)
    axes = sorted(a for a in axis_values if a)
    is_fanuc = fanuc_param.looks_like_fanuc_prm(raw)

    rows, out_axis, dropped_soft = [], {}, {}
    for ax, vals in axis_values.items():
        vals = resolve_product_values(raw, vals, ax) if is_fanuc else dict(vals or {})
        if mode != "apply":
            vals, dsoft = drop_params(vals, sparams)
            dropped_soft.update(dsoft)
        if is_fanuc:
            vals, _dz = drop_zero_params(vals, zero_params)
        out_axis[ax] = vals
    out_common = resolve_product_values(raw, common, None) if is_fanuc else dict(common)
    if mode != "apply":
        out_common, dsoft = drop_params(out_common, sparams)
        dropped_soft.update(dsoft)
    if is_fanuc:
        out_common, _dz = drop_zero_params(out_common, zero_params)

    # --- ここから下は「実際に書く値」だけを見る（画面と中身がずれない） ---
    if single:
        ax = next(iter(out_axis), axis)
        for (num, old, new) in preview_rows(raw, out_axis.get(ax, {}), ax):
            kind = KIND_MISSING if (is_fanuc and old == "") else KIND_PRODUCT
            rows.append((num, "", old, new, kind, old != new))
    else:
        for (num, a, old, new) in preview_rows_multi(raw, out_axis, out_common):
            kind = KIND_MISSING if (is_fanuc and old == "") else KIND_PRODUCT
            rows.append((num, a, old, new, kind, old != new))
    for (num, old, new) in preview_zero_rows(raw, zero_params):
        rows.append((num, "全軸", old, new, KIND_ZERO, old != new))
    for (num, lab, old, new, changed) in soft_limit_preview(
            raw, dropped_soft, axes or ([axis] if axis else []), mode, sparams):
        rows.append((f"{num}({lab})" if lab else num, lab, old, new,
                     KIND_SOFT, changed))

    if single:
        ax = next(iter(out_axis), axis)
        newtext, missing, fmt = build_text(raw, out_axis.get(ax, {}), ax, seiban,
                                           zero_params, mode, sparams)
        total = len(out_axis.get(ax, {}))
        miss_n = len(missing)
    else:
        newtext, missing, fmt = build_text_multi(raw, out_axis, out_common, seiban,
                                                 zero_params, mode, sparams)
        total = sum(len(v) for v in out_axis.values()) + len(out_common)
        miss_n = len(missing)
    need = (len(fanuc_param.prm_bytes(newtext, eob)) if fmt == "fanuc"
            else len(newtext.encode("cp932", "replace")))
    problems = fanuc_param.validate_prm(newtext, reference) if fmt == "fanuc" else []
    return {"rows": rows, "newtext": newtext, "missing": missing, "fmt": fmt,
            "applied": total - miss_n, "total": total, "need": need,
            "problems": problems, "values": out_axis.get(axis, {}),
            "axis_values": out_axis, "common": out_common,
            "dropped_soft": dropped_soft}


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
