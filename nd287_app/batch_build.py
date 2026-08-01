# -*- coding: utf-8 -*-
"""型式ごとに測定プログラムをまとめて作る。

測定条件が型式で決まっている（マスタ/測定条件.csv）ので、型式を選べば
測定プログラムは一意に決まる。1件ずつ画面で作らなくても、まとめて出せる。

出力の並べ方（memory_card_layout）:
    "folder" … <出力先>/<型式>/<型式>.NC  … PC・共有サーバで整理するとき
    "flat"   … <出力先>/<型式>.NC          … メモリカードへ入れるとき

  ★カードのサブフォルダを開けない制御装置がある（機種による）。
    見えない場合は "flat" を使う。どちらでも同じ中身のファイルが出る。

ファイル名は型式そのまま（使えない文字は _ に置換）。同じ名前になる型式が
あれば連番を付ける。上書きはしない（既にあれば飛ばして報告する）。
"""

import re
from pathlib import Path

from . import fanuc
from .masters import condition_params

# ファイル名に使えない文字（パス区切り・予約文字・制御文字）
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
LAYOUTS = (("型式ごとのフォルダに入れる（PC・共有サーバ向け）", "folder"),
           ("1つのフォルダに並べる（メモリカード向け）", "flat"))


def safe_name(model: str) -> str:
    """型式をファイル名に使える形にする。空になったら "MODEL"。"""
    s = _BAD_NAME.sub("_", str(model or "")).strip().strip(".")
    return s or "MODEL"


def program_for_model(cond: dict, cfg, *, rotary=True, repeats=0,
                      blocks=None, title=None) -> str:
    """測定条件1件から測定プログラム（Gコード）を作る。

    分割のみが既定。再現も入れるなら blocks と repeats を渡す。
    条件に刻みが無い型式は ValueError（作れないものを黙って作らない）。
    """
    p = condition_params(cond)
    if not p.get("wheel_pitch"):
        raise ValueError("ホイール刻みが測定条件にありません")
    worm_pitch = p.get("worm_pitch") or 0.0
    worm_range = p.get("worm_range") or 0.0
    model = cond.get("model", "")
    close = (cond.get("close") or "").strip()
    return fanuc.generate(
        cfg, rotary=rotary,
        title=title if title is not None else f"{model} {close}".strip(),
        wheel_pitch=p["wheel_pitch"], wheel_start=0.0, wheel_end=360.0,
        worm_pitch=worm_pitch, worm_range=worm_range, worm_start=0.0,
        blocks=list(blocks or []), repeats=int(repeats or 0),
        include_division=True, include_repeat=bool(blocks and repeats))


def _out_path(base: Path, model: str, ext: str, layout: str, used: set) -> Path:
    name = safe_name(model)
    stem = name
    n = 2
    while True:
        p = (base / name / f"{stem}{ext}") if layout == "folder" \
            else (base / f"{stem}{ext}")
        if str(p).lower() not in used:
            used.add(str(p).lower())
            return p
        stem = f"{name}_{n}"       # 同名の型式（フル/セミ等）は連番で分ける
        n += 1


def batch_programs(entries, cfg, out_dir, *, layout="folder", ext=".NC",
                   eob=None, rotary=True, overwrite=False) -> list:
    """型式ごとの測定プログラムをまとめて書き出す。

    entries … load_conditions の値（dict の並び）。model/close を持つもの。
    戻り値: [(型式, close, 状態, パスまたは理由)]
        状態は "作成" / "既存のため飛ばした" / "作れない"
    """
    base = Path(out_dir)
    if not base.is_dir():
        raise NotADirectoryError(f"出力先フォルダがありません: {base}")
    used, report = set(), []
    for cond in entries:
        model = cond.get("model", "")
        close = (cond.get("close") or "").strip()
        try:
            text = program_for_model(cond, cfg, rotary=rotary)
        except Exception as e:
            report.append((model, close, "作れない", str(e)))
            continue
        path = _out_path(base, f"{model}{('_' + close) if close else ''}",
                         ext, layout, used)
        if path.exists() and not overwrite:
            report.append((model, close, "既存のため飛ばした", str(path)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fanuc.nc_bytes(text, eob or fanuc.DEFAULT_EOB))
        report.append((model, close, "作成", str(path)))
    return report


def summarize(report) -> str:
    """一括作成の結果を1行にまとめる。"""
    n = {}
    for _m, _c, state, _p in report:
        n[state] = n.get(state, 0) + 1
    return "　".join(f"{k} {v}件" for k, v in sorted(n.items())) or "0件"
