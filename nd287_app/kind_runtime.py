# -*- coding: utf-8 -*-
"""測定の種類（モーター／駆動装置／フルクロ）を、測定の流れで使うための部品（Qt なし）。

種類別の表（kind_conditions.py の専用 Excel）を、設定 kind_conditions_xlsx の場所から読む。
無ければ、今までの登録を写して1回だけ作る（ある表は絶対に上書きしない）。
セミの種類（モーター・駆動装置）の結果は、検査表（.BS/.KS/.RS）にも Web にも出さず、
ファイル名に種類を付けて別名で保存する。
"""
from pathlib import Path

from . import kind_conditions as K

SETTING_KEY = "kind_conditions_xlsx"
DEFAULT_PATH = "マスタ/" + K.FILE_NAME

LABEL = {"セミ・モーター": "モーター", "セミ・駆動装置": "駆動装置", "フル・スケール付き": "フルクロ"}
HINT = {"セミ・モーター": "歯1枚を10等分で測ります（セミクロ）",
        "セミ・駆動装置": "歯1枚飛ばしで測ります（セミクロ）",
        "フル・スケール付き": "スケールで決めた測り方です（フルクロ）"}
SEMI_KINDS = ("セミ・モーター", "セミ・駆動装置")


def label(kind):
    return LABEL.get(kind, kind or "")


def is_semi(kind):
    return kind in SEMI_KINDS


def resolve_path(settings, app_dir=None):
    p = Path(str((settings or {}).get(SETTING_KEY) or DEFAULT_PATH))
    if p.is_absolute():
        return p
    if app_dir is None:
        from .settings import app_dir as _ad
        app_dir = _ad()
    return Path(app_dir) / p


def ensure_file(path, masters=None):
    """表が無ければ、今までの登録を写して1回だけ作る。戻り値 (作った: bool, 問題の文 or "")。ある表には触らない。"""
    p = Path(path)
    if p.exists():
        return False, ""
    try:
        rot, tilt = K.rows_from_existing(masters or {})
        p.parent.mkdir(parents=True, exist_ok=True)
        K.build_template(p, rot, tilt)
        return True, ""
    except Exception as e:
        if p.is_file():                                   # 別のところが先に作った: そのまま使う（上書きしない）
            return False, ""                               # 作れなくても、今までの登録で続けられる（理由は画面に出す）
        return False, f"測定条件（種類別）の表を作れませんでした: {e}"


def load_table(settings, masters=None, app_dir=None, create=True):
    """種類別の表を読む。{"rotary","tilt","plan","problems",…}。読めない・作れないときの理由は problems に入る。"""
    path = resolve_path(settings, app_dir)
    extra = []
    if create:
        _created, why = ensure_file(path, masters)
        if why:
            extra.append(why)
    table = K.load(path)
    table["problems"] = list(table.get("problems") or []) + extra
    table["path"] = str(path)
    return table


def kinds_for(table, model, tilt):
    if not table or not str(model or "").strip():
        return []
    return K.kinds_of(table, str(model).strip(), bool(tilt))


def pick_kind(kinds, machine_loop=None, last=""):
    """型式を手で入れたとき、登録済みの種類（kinds）から先に選んでおくもの。決まらなければ ""（人が押す）。
    1つだけ → それ。機械の読み（"フル"/"セミ"）があれば、それに合う種類（セミで2つ以上あれば前回、無ければ決めない）。
    機械につながっていなければ前回使った種類。"""
    kinds = list(kinds or [])
    if not kinds:
        return ""
    if len(kinds) == 1:
        return kinds[0]
    if machine_loop in ("フル", "セミ"):
        cand = [k for k in kinds if is_semi(k) == (machine_loop == "セミ")]
        if len(cand) == 1:
            return cand[0]
        if last in cand:
            return last
        return ""
    return last if last in kinds else ""


def problems_text(table, model=None):
    """画面に出す、種類別の表の問題（無ければ ""）。model を渡すと、その型式に関係するものだけ。"""
    ps = [str(x).replace("rotary:", "回転:").replace("tilt:", "傾斜:") for x in K.problems_for(table, model)]
    if not ps:
        return ""
    head = "種類別の表に問題: "                  # 短く（状態欄で、肝心の「何が」が「…」に隠れないように。11/26）
    return head + " ／ ".join(ps[:3]) + (f" ほか{len(ps) - 3}件" if len(ps) > 3 else "")


def resolve(table, model, tilt, kind, scale=""):
    """型式・種類から、条件の1行を引き、(rec, params, error) を返す。
    kind が空、またはこの型式に種類の登録が無いとき: (None, None, "")（今までどおり）。
    刻みが決まらないとき: error に理由（10° などへ黙って落とさない）。"""
    if not kind or not kinds_for(table, model, tilt):
        return None, None, ""
    rec = K.find(table, str(model).strip(), bool(tilt), kind, scale)
    if rec is not None and rec.get("測定の種類") != K.normalize_kind(kind):   # 種類なしの行へ落ちた: 黙って使わない
        rec = None
    if rec is None:
        return None, None, f"{label(kind)}の測り方が登録されていません"
    p = K.params(rec, bool(tilt))
    if p.get("_bad"):
        return rec, p, f"{label(kind)}の「{'・'.join(p['_bad'])}」が数字として読めません。Excelを直してください"
    if not p.get("_ok"):
        need = "歯数か刻み" if kind == "セミ・モーター" else "刻み"
        return rec, p, f"{label(kind)}の{need}が、登録に入っていないので決められません"
    return rec, p, ""


NAME_NOTE = "名前の区別は仮です（検査表のシステムでの読み方は確かめ中）"
