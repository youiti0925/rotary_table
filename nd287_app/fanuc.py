# -*- coding: utf-8 -*-
"""FANUC 測定プログラム（Gコード）生成

回転テーブルの割出精度・再現性を測定するためのプログラムを生成する。
カウンター（ND287等）へは、各測定点で位置決め・ドゥエル（静止待ち）後に
完了信号（既定 M80）を送り、その瞬間の値を取り込ませる。

＝＝＝ 出力形式について（実機で読めなかった件の対策）＝＝＝
実機（FANUC）が自分で出力したプログラムと同じ形にそろえてある。以前の出力は
PCのテキストとしては読めても、制御装置には読み込めなかった。原因は3つ:

  1. 行末の ";" を文字として書いていた
     画面に見える ";" は EOB（ブロック終わり）の表示であって、ファイルの中では
     改行そのもの。";" はFANUCのISOコード表に無い文字なので、書き込むと読取で
     エラーになる。実機が出力したファイルにも ";" は1つも入っていない。
  2. コメントに使えない文字が入っていた
     日本語をASCIIに落として "????" になっていた。"?" もISOコードに無い。
     さらに "(--- RESET (set 0) ---)" のようにカッコが入れ子だった。FANUCは
     最初の ")" でコメントを終わるので、残りが不正な指令として読まれる。
  3. 改行が実機の形式と違っていた
     実機は 1ブロックごとに LF CR CR で区切る。既定でこれに合わせる
     （EOB_STYLES で CRLF / LF にも変えられる）。

設定（FanucConfig）で変更できる項目:
    axis        … 割出軸のアドレス（既定 Z＝現在の実機）。うちの軸は X/Y/Z/A/B/C で、
                  X も正規の軸名（制御装置側はパラメータ1020で決まる）
    preswing    … 前振り量[°]（バックラッシュ消しの行き過ぎ量。既定 10）
    swing_dwell_sec … 振り後のドゥエル[秒]（バックラッシュ消しの振り後。測定とは
                      無関係なので小さめ＝速い。既定 1秒）
    dwell_sec   … 測定ドゥエル[秒]（測定点で静止・読取前。G04 X… 既定 1秒）
    mcode       … 完了信号Mコード（カウンターへ送る。既定 M80）
    clamp_enabled  … クランプ分割: 各測定点で クランプ→読取→アンクランプ する
    clamp_mcode / unclamp_mcode … クランプ/アンクランプ信号のMコード（軸ごとに
                      決まっているので設定で変える。例 4軸 M10/M11）
    clamp_dwell_sec / unclamp_dwell_sec … クランプ/アンクランプ信号後のドゥエル[秒]
                      （信号を出してもすぐ締まらない/緩まないので待つ）
    use_subprogram … True: 再現をサブプロ（M98 L呼び）/ False: 1本に展開
    return_to_start … 測定後に開始位置へ戻すか

分割の前振り: 各パス（CW一周/CCW一周）の先頭で1回だけ前振りし、以降は
ピッチ送り＋ドゥエル＋完了信号。CWは負側から、CCWは正側から接近する。
再現のサブプロ本体は現場の実物どおり（±前振りでCW読み・CCW読みの2点）。

ランタイムの完了信号発火数（= カウンターが取り込む点数）が、アプリ側の
測定シーケンスのステップ数と一致するよう設計している（test_fanuc で保証）。
"""

import re
from dataclasses import dataclass

# ブロックの区切り（EOB）。実機が出力したファイルは LF CR CR だった。
EOB_PUNCH = "\n\r\r"   # 実機と同じ（既定）
EOB_CRLF = "\r\n"      # 一般的なDNCソフト
EOB_LF = "\n"          # LFのみ
EOB_STYLES = (
    ("実機と同じ（LF CR CR）", EOB_PUNCH),
    ("CRLF（一般的なDNC）", EOB_CRLF),
    ("LF のみ", EOB_LF),
)
DEFAULT_EOB = EOB_PUNCH

# FANUCのISOコードでプログラムに書ける文字。ここに無い文字を入れると読取エラー。
LEGAL_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-+*/=%()")
# コメントの中で使える文字（カッコは入れ子にできないので除外）
_COMMENT_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-+*/=")
COMMENT_MAXLEN = 40


def sanitize_comment(text, maxlen: int = COMMENT_MAXLEN) -> str:
    """コメントをFANUCが読める形にする（カッコは付けずに中身だけ返す）。

    ・小文字は大文字へ（ISOコードは大文字のみ）
    ・日本語・"?" などコード表に無い文字は空白に置換して詰める
    ・入れ子のカッコは空白に置換（最初の ")" でコメントが終わってしまうため）
    """
    s = str(text or "").upper()
    out = [c if c in _COMMENT_CHARS else " " for c in s]
    return re.sub(r"\s+", " ", "".join(out)).strip()[:maxlen].strip()


@dataclass
class FanucConfig:
    axis: str = "Z"
    preswing: float = 10.0
    swing_dwell_sec: float = 1.0  # バックラッシュ消しの振り後（測定無関係＝小さめ）
    dwell_sec: float = 1.0        # 測定点での静止待ち（読取前。1.0〜5.0で調整）
    mcode: str = "M80"
    use_subprogram: bool = True
    main_number: int = 100
    # O8000〜O9999 は保護プログラム領域（パラメータ3202 NE8/NE9）で、既定では
    # 書き込めない機械が多い。ここに入れると転送そのものが弾かれるので 1000番台。
    rep_sub_number: int = 1000
    return_to_start: bool = True
    counter_reset: bool = True   # 先頭でバックラッシュ消し→M00（作業者がカウンターを0に）
    reset_swing: float = 10.0    # カウンターリセットの振り量[°]（測定の前振りとは別）
    # クランプ分割: 各測定点でクランプ→読取→アンクランプする（軸ロックして測る）
    clamp_enabled: bool = False
    clamp_mcode: str = "M10"        # クランプ信号Mコード（軸ごとに決まる。例 4軸 M10）
    unclamp_mcode: str = "M11"      # アンクランプ信号Mコード（例 4軸 M11）
    clamp_dwell_sec: float = 1.0    # クランプ信号後のドゥエル[秒]（締まり待ち）
    unclamp_dwell_sec: float = 1.0  # アンクランプ信号後のドゥエル[秒]（次の動き前の緩み待ち）

    @classmethod
    def from_settings(cls, settings: dict) -> "FanucConfig":
        s = settings or {}
        return cls(
            axis=str(s.get("fanuc_axis", "Z")),
            preswing=float(s.get("fanuc_preswing", 10.0)),
            swing_dwell_sec=float(s.get("fanuc_swing_dwell_sec", 1.0)),
            dwell_sec=float(s.get("fanuc_dwell_sec", 1.0)),
            mcode=str(s.get("fanuc_mcode", "M80")),
            use_subprogram=bool(s.get("fanuc_use_subprogram", True)),
            main_number=int(s.get("fanuc_main_number", 100)),
            rep_sub_number=int(s.get("fanuc_rep_sub_number", 1000)),
            return_to_start=bool(s.get("fanuc_return_to_start", True)),
            counter_reset=bool(s.get("fanuc_counter_reset", True)),
            reset_swing=float(s.get("fanuc_reset_swing", s.get("fanuc_preswing", 10.0))),
            clamp_enabled=bool(s.get("fanuc_clamp_enabled", False)),
            clamp_mcode=str(s.get("fanuc_clamp_mcode", "M10")),
            unclamp_mcode=str(s.get("fanuc_unclamp_mcode", "M11")),
            clamp_dwell_sec=float(s.get("fanuc_clamp_dwell_sec", 1.0)),
            unclamp_dwell_sec=float(s.get("fanuc_unclamp_dwell_sec", 1.0)),
        )


def fmt_num(value) -> str:
    """FANUC座標表記。整数は末尾ピリオド付き（10→"10."）、小数は余分な0を除去。"""
    value = round(float(value), 4)
    if value == int(value):
        return f"{int(value)}."
    text = f"{value:.4f}".rstrip("0")
    return text


def _g04(sec) -> str:
    # G04 X… は小数点付き = 秒指定（FANUC）。小数点なしは最小指令単位になり誤動作のもと。
    return f"G04 X{fmt_num(sec)}"


def _dwell(cfg: FanucConfig, swing: bool = False) -> str:
    # swing=True: バックラッシュ消しの振り後（測定無関係・小さめ）。
    # swing=False: 測定点での静止待ち（読取前）。
    return _g04(cfg.swing_dwell_sec if swing else cfg.dwell_sec)


def _read(cfg: FanucConfig) -> list:
    """測定点に着いてから完了信号を出すまでの手順。

    通常: 測定ドゥエル（静止待ち）→ 完了信号。
    クランプ分割（clamp_enabled）: クランプ信号 → クランプ後ドゥエル（締まり待ち）→
        完了信号 → アンクランプ信号 → アンクランプ後ドゥエル（次の動き前の緩み待ち）。
    どちらも完了信号（cfg.mcode）は1点につき1回だけ＝カウンターの取込点数は不変。
    """
    if cfg.clamp_enabled:
        return [
            cfg.clamp_mcode, _g04(cfg.clamp_dwell_sec),
            cfg.mcode,
            cfg.unclamp_mcode, _g04(cfg.unclamp_dwell_sec),
        ]
    return [_dwell(cfg), cfg.mcode]


def _point_with_preswing(cfg: FanucConfig, direction: int) -> list:
    """前振り付きで1点読む（パス先頭・再現で使う基本動作）。正味移動0。

    direction>0: 負側から接近（-preswing→戻り）。direction<0: 正側から接近。
    振り後は振りドゥエル（小）、測定点では _read（測定 or クランプ読取）。
    """
    p = cfg.preswing
    a = cfg.axis
    if direction > 0:
        moves = [f"G00 {a}{fmt_num(-p)}", _dwell(cfg, swing=True), f"{a}{fmt_num(p)}"]
    else:
        moves = [f"G00 {a}{fmt_num(p)}", _dwell(cfg, swing=True), f"{a}{fmt_num(-p)}"]
    return moves + _read(cfg)


def _division_pass(cfg: FanucConfig, n_points: int, pitch: float,
                   direction: int) -> list:
    """1パス（n_points点）。先頭で前振り、以降はピッチ送り＋ _read（測定/クランプ読取）。"""
    lines = list(_point_with_preswing(cfg, direction))  # 先頭点（n_points のうち1点目）
    step = pitch * direction
    for _ in range(max(n_points - 1, 0)):
        lines += [f"G00 {cfg.axis}{fmt_num(step)}"] + _read(cfg)
    return lines  # 完了信号 n_points 回


def repeat_body(cfg: FanucConfig) -> list:
    """再現1サイクル（CW読み・CCW読みの2点、正味移動0）。実物サブプロと同形。"""
    p = cfg.preswing
    a = cfg.axis
    return (
        [f"G91 G00 {a}{fmt_num(-p)}", _dwell(cfg, swing=True), f"{a}{fmt_num(p)}"]
        + _read(cfg)
        + [f"G91 G00 {a}{fmt_num(p)}", _dwell(cfg, swing=True), f"{a}{fmt_num(-p)}"]
        + _read(cfg)
    )  # 完了信号 2 回


def _goto(cfg: FanucConfig, delta: float) -> list:
    """完了信号を出さない位置決め移動（パス間・ブロック間の割り出し）。振りドゥエル（小）。"""
    if abs(delta) < 1e-9:
        return []
    return [f"G00 {cfg.axis}{fmt_num(delta)}", _dwell(cfg, swing=True)]


def _reset_block(cfg: FanucConfig) -> list:
    """カウンターリセット用: 0°でバックラッシュを消し M00 で停止（作業者が0設定）。

    現場の実物どおり: G91 G00 X+p / X-p / X-p / X+p / M00（正味移動0、0°のまま）。
    振り量はリセット専用（reset_swing）。完了信号(M80)は出さない＝測定点には数えない。
    """
    p = cfg.reset_swing
    a = cfg.axis
    return [
        f"G91 G00 {a}{fmt_num(p)}",
        f"{a}{fmt_num(-p)}",
        f"{a}{fmt_num(-p)}",
        f"{a}{fmt_num(p)}",
        "M00",
    ]


def _normalize_rotary(delta: float) -> float:
    """回転は ±180° に正規化（最短回りで開始位置へ戻す）。"""
    return ((delta + 180.0) % 360.0) - 180.0


def expected_signal_count(*, include_division, n_wheel, n_worm,
                          include_repeat, n_blocks, repeats) -> int:
    """ランタイムでカウンターが取り込む点数（アプリのシーケンス長と一致すべき）。"""
    total = 0
    if include_division:
        total += 2 * n_wheel + 2 * n_worm
    if include_repeat:
        total += n_blocks * repeats * 2
    return total


def generate(cfg: FanucConfig, *, rotary=True, title="MEASURE",
             wheel_pitch=10.0, wheel_start=0.0, wheel_end=360.0,
             worm_pitch=0.5, worm_range=5.0, worm_start=0.0,
             blocks=None, repeats=7,
             include_division=True, include_repeat=True) -> str:
    """測定プログラム（Gコードテキスト）を生成する。"""
    blocks = list(blocks or [])
    n_wheel = round((wheel_end - wheel_start) / wheel_pitch) + 1 if include_division else 0
    n_worm = round(worm_range / worm_pitch) + 1 if (include_division and worm_pitch) else 0

    # プログラムは 0°（カウンターの基準＝リセット位置）から始まる前提
    main = []
    cur = 0.0
    if include_division and cfg.counter_reset:
        # コメントにカッコを入れ子にしない（最初の ) でコメントが終わるため）
        main.append("(--- COUNTER RESET / SET 0 HERE ---)")
        main += _reset_block(cfg)   # G91 を含む。0°のまま、M00で停止
    else:
        main.append("G91 (INCREMENTAL)")

    if include_division:
        main.append("(--- BUNKATSU ---)")
        # 0°（リセット位置）から測定開始角度へ（回転は0なので移動なし、傾斜は例 X-30）
        main += _goto(cfg, wheel_start - cur)
        cur = wheel_start
        main.append("(WHEEL CW)")
        main += _division_pass(cfg, n_wheel, wheel_pitch, +1)
        cur = wheel_end
        main.append("(WHEEL CCW)")
        main += _division_pass(cfg, n_wheel, wheel_pitch, -1)
        cur = wheel_start
        if n_worm:
            # ウォームは0°（基準）で測定
            main += _goto(cfg, worm_start - cur)
            cur = worm_start
            main.append("(WORM CW)")
            main += _division_pass(cfg, n_worm, worm_pitch, +1)
            cur = worm_start + worm_range
            main.append("(WORM CCW)")
            main += _division_pass(cfg, n_worm, worm_pitch, -1)
            cur = worm_start

    rep_sub_lines = None
    if include_repeat and blocks and repeats:
        main.append("(--- SAIGEN ---)")
        if cfg.use_subprogram:
            rep_sub_lines = repeat_body(cfg)
            for b in blocks:
                main += _goto(cfg, b - cur)
                cur = b
                main.append(f"M98 P{cfg.rep_sub_number} L{repeats}")
        else:
            for b in blocks:
                main += _goto(cfg, b - cur)
                cur = b
                main.append(f"(BLOCK {fmt_num(b)} X{repeats})")
                for _ in range(repeats):
                    main += repeat_body(cfg)

    if cfg.return_to_start:
        # 0°（カウンター基準）へ戻す
        delta = 0.0 - cur
        if rotary:
            delta = _normalize_rotary(delta)
        main += _goto(cfg, delta)
        cur = 0.0
    main.append("M30")

    return _format_program(cfg, title, main, rep_sub_lines)


def _format_program(cfg: FanucConfig, title: str, main: list,
                    rep_sub_lines) -> str:
    out = ["%", _prog_head(cfg.main_number, title)]
    out += [_block(line) for line in main]
    if rep_sub_lines:
        out.append(_prog_head(cfg.rep_sub_number, "SAIGEN SUB"))
        out += [_block(line) for line in rep_sub_lines]
        out.append("M99")
    out.append("%")
    # 改行は「行の区切り」を表すだけ。実際にファイルへ書くときの EOB は
    # nc_bytes() で機械の形式（既定 LF CR CR）に変換する。
    return "\n".join(out) + "\n"


def _prog_head(number: int, title) -> str:
    """プログラム番号行。実機の出力と同じで、O番号とコメントの間に空白は入れない。"""
    comment = sanitize_comment(title)
    return f"O{int(number):04d}" + (f"({comment})" if comment else "")


def _block(line: str) -> str:
    """1ブロックを実機の出力と同じ形に整える。

    ・EOB（画面の ";"）は文字として書かない。ファイル上の EOB は改行そのもので、
      ";" はISOコードに無い文字。書き込むと制御装置が読み込めない。
    ・指令部の空白は詰める（実機の出力は "G91G00A-10." の形）。
    ・コメントは sanitize_comment を通す（大文字化・使えない文字と入れ子カッコを除去）。
    """
    line = str(line).strip()
    code, sep, rest = line.partition("(")
    code = code.replace(" ", "").replace("\t", "")
    if not sep:
        return code
    comment = sanitize_comment(rest.rstrip().rstrip(")"))
    return code + (f"({comment})" if comment else "")


def nc_bytes(text: str, eob: str = DEFAULT_EOB) -> bytes:
    """画面のプログラム文字列を、機械へ渡すバイト列にする。

    行の区切りを機械のEOB形式に置き換え、ISOコードに無い文字は取り除く。
    ";" が手編集で混ざっていても、ここで EOB として扱って落とす。
    """
    norm = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_block(l) for l in norm.replace(";", "\n").split("\n")]
    lines = [l for l in lines if l != ""]
    body = "".join(l + (eob or EOB_LF) for l in lines)
    return body.encode("ascii", "ignore")


def validate(text: str, cfg: FanucConfig = None) -> list:
    """機械が読めない書き方を洗い出す（読み込ませる前の自己点検）。"""
    problems = []
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stripped = [l for l in lines if l.strip()]
    if not stripped:
        return ["プログラムが空です"]
    if stripped[0].strip() != "%" or stripped[-1].strip() != "%":
        problems.append("先頭と末尾が % になっていません（FANUCの読取開始/終了記号）")
    if not any(re.match(r"\s*O\d+", l) for l in stripped):
        problems.append("O番号（プログラム番号）の行がありません")
    if not any(re.search(r"\bM(30|99|02)\b", l) for l in stripped):
        problems.append("M30/M99（プログラム終わり）がありません")
    # 途中の % は「読取終わり」。そこから先が読み込まれない
    # （パラメータ側の実データ F35BASIC で実際に起きていた）
    mid = [i for i, l in enumerate(stripped[1:-1], start=2) if l.strip().startswith("%")]
    if mid:
        problems.append(
            f"途中に % があります（{len(mid)}箇所）。% は読取終わりの記号なので、"
            "そこから先が読み込まれません")
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if ";" in line:
            problems.append(f"{i}行目: \";\" は文字として書けません（EOBは改行）")
        bad = sorted({c for c in line
                      if c not in LEGAL_CHARS and not c.isspace()})
        if bad:
            shown = "".join(bad)[:8]
            problems.append(f"{i}行目: 使えない文字 {shown!r}（ISOコードに無い）")
        code, sep, rest = line.partition("(")
        if sep and "(" in rest.rstrip().rstrip(")"):
            problems.append(f"{i}行目: コメントのカッコが入れ子です"
                            "（最初の ) でコメントが終わってしまう）")
        if sep and ")" not in rest:
            problems.append(f"{i}行目: コメントの ) がありません")
    for l in stripped:
        m = re.match(r"\s*O(\d+)", l)
        if m and 8000 <= int(m.group(1)) <= 9999:
            problems.append(
                f"O{int(m.group(1)):04d}: 保護プログラム領域（O8000〜O9999）です。"
                "パラメータ3202のNE8/NE9で書込禁止だと転送できません")
    # ※以前ここで「割出軸が X」を警告していたが取りやめた。この機械の軸は
    #   X/Y/Z/A/B/C で X も正規の軸名。現場の実物サブプロも割出軸 X と
    #   G04 X…（ドゥエル）を同じプログラムで使っている（FANUCは G04 のブロック内の
    #   X を時間として読む）。実物が通っているものを警告しない。
    # 同じ指摘の繰り返しは1回にまとめる
    seen, out = set(), []
    for p in problems:
        key = re.sub(r"^\d+行目", "N行目", p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out
