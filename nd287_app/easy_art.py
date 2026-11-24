# -*- coding: utf-8 -*-
"""はじめてモードの絵（SVG の文字列を作るだけ。Qt なし）。

操作盤・つまみ・ボタンは **一般的な形で描いた絵**（FANUC の画像は使わない）。
つまみの並びとボタンの位置は仮で、実物の写真をもらったら描き直す（docs/はじめてモード_決めたやり方.md）。
ただし操作モードのつまみの8つの名前は、試験装置の実物のスイッチを見て書いたもの（help_text に記録）。
光る輪（さわる所）は phase（0〜1）で大きさを変える（画面が 0.5 秒ごとに変えて、点滅して見せる）。
"""

FONT = "Meiryo, Yu Gothic UI, BIZ UDPGothic, sans-serif"
INK = "#20262C"
FG = "#17212B"
MODES = ("手動早送り", "手動送り", "原点復帰", "MDI", "ハンドル", "メモリ運転", "記憶編集", "自動測定")
MODE_INDEX = {m: i for i, m in enumerate(MODES)}
HERE = "#F0A400"
PANEL = "#C5CAD0"
PANEL_EDGE = "#8D959E"
DARK = "#2B3138"
KEY = "#E9ECEF"
KEY_EDGE = "#9AA2AB"
GREEN = "#27A35A"
RED = "#D43A2F"
YELLOW = "#F2C200"

import math


def _pol(cx, cy, r, deg):
    return (cx + r * math.sin(math.radians(deg)), cy - r * math.cos(math.radians(deg)))


def _ang(i):
    return -135 + i * 270 / 7


def _t(x, y, size, text, anchor="middle", fill=INK, weight=700):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{fill}" '
            f'font-weight="{weight}" font-family="{FONT}">{text}</text>')


def _ring(x, y, r, phase=0.0):
    """さわる所を囲む輪。phase で外側の輪が広がって薄くなる（点滅）。"""
    p = max(0.0, min(1.0, phase))
    return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="{HERE}" stroke-width="5"/>'
            f'<circle cx="{x}" cy="{y}" r="{r + 9 * p:.1f}" fill="none" stroke="{HERE}" stroke-width="3" '
            f'stroke-opacity="{1 - p:.2f}"/>')


def _svg(w, h, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<title>{label}</title>{body}</svg>')


# ------------------------------------------------------------------ 操作盤の全体（どこをさわるか）

PANEL_SPOTS = {
    "power": (62, 262, 34), "dial": (190, 292, 60), "rapid": (318, 282, 34),
    "start": (300, 362, 26), "estop": (452, 300, 44), "keys": (392, 112, 112),
}


def panel(part, mode_index, phase=0.0):
    keys = "".join(
        f'<rect x="{296 + c * 28}" y="{30 + r * 28}" width="23" height="23" rx="4" fill="{KEY}" stroke="{KEY_EDGE}"/>'
        for r in range(6) for c in range(7))
    ticks = ""
    for i in range(len(MODES)):
        a, b = _pol(190, 292, 40, _ang(i))
        ticks += f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{INK}"/>'
    px, py = _pol(190, 292, 24, _ang(mode_index))
    spot = PANEL_SPOTS.get(part)
    body = f"""
  <rect x="4" y="4" width="512" height="392" rx="18" fill="#C5CAD0" stroke="{PANEL_EDGE}" stroke-width="3"/>
  <rect x="22" y="22" width="254" height="182" rx="8" fill="{DARK}"/><rect x="34" y="34" width="230" height="158" rx="4" fill="#0F2A3A"/>
  {_t(46, 58, 13, "プログラム　O0100", "start", "#8FE0C0")}
  <rect x="46" y="72" width="150" height="6" fill="#8FE0C0" opacity=".5"/><rect x="46" y="88" width="190" height="6" fill="#8FE0C0" opacity=".5"/>
  {_t(46, 182, 12, MODES[mode_index], "start", "#FFE27A")}
  <rect x="286" y="22" width="212" height="182" rx="8" fill="{DARK}"/>{keys}
  <rect x="22" y="222" width="476" height="160" rx="10" fill="#B4BAC1" stroke="{PANEL_EDGE}"/>
  <circle cx="46" cy="262" r="13" fill="{GREEN}" stroke="{INK}"/><circle cx="80" cy="262" r="13" fill="{RED}" stroke="{INK}"/>{_t(63, 294, 10, "電源 入／切")}
  <circle cx="190" cy="292" r="30" fill="{DARK}"/>{ticks}<line x1="190" y1="292" x2="{px:.1f}" y2="{py:.1f}" stroke="#fff" stroke-width="5" stroke-linecap="round"/>{_t(190, 352, 10, "操作モード")}
  <circle cx="318" cy="282" r="20" fill="{DARK}"/><line x1="318" y1="282" x2="318" y2="266" stroke="#fff" stroke-width="4" stroke-linecap="round"/>{_t(318, 246, 10, "早送り")}
  <circle cx="378" cy="282" r="20" fill="{DARK}"/><line x1="378" y1="282" x2="390" y2="272" stroke="#fff" stroke-width="4" stroke-linecap="round"/>{_t(378, 246, 10, "送り")}
  <rect x="284" y="346" width="32" height="32" rx="6" fill="{GREEN}" stroke="{INK}"/><rect x="336" y="346" width="32" height="32" rx="6" fill="#E57B1E" stroke="{INK}"/>{_t(300, 342, 9, "起動")}{_t(352, 342, 9, "一時停止")}
  <circle cx="452" cy="300" r="34" fill="{YELLOW}" stroke="{INK}"/><circle cx="452" cy="300" r="23" fill="{RED}" stroke="#7E1A14" stroke-width="2"/>{_t(452, 356, 10, "非常停止")}
  {_ring(*spot, phase) if spot else ""}"""
    return _svg(520, 400, body, "操作盤の全体。光っている所が、いまさわる場所")


# ------------------------------------------------------------------ 拡大の絵（300x300）

def _zoom_frame(body, label, bg="#B4BAC1"):
    return _svg(300, 300, f'<rect width="300" height="300" rx="16" fill="{bg}"/>' + body, label)


def dial(to, current, done, phase=0.0):
    """操作モードのつまみ。to=合わせる位置、current=いまの位置（done のときは to）。"""
    cur = to if done else current
    lab = ""
    for i, m in enumerate(MODES):
        x, y = _pol(150, 165, 112, _ang(i))
        tx, ty = _pol(150, 165, 84, _ang(i))
        on = i == to
        lab += f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="{HERE if on else INK}"/>'
        if on:
            lab += f'<rect x="{x - 44:.1f}" y="{y - 15:.1f}" width="88" height="24" rx="12" fill="{HERE}"/>'
        lab += _t(x, y + 2, 14 if on else 11, m, fill="#2A1E00" if on else "#59636E")
    px, py = _pol(150, 165, 52, _ang(cur))
    ring = "" if done else _ring(150, 165, 70, phase)
    body = (lab + f'<circle cx="150" cy="165" r="66" fill="{DARK}" stroke="#111" stroke-width="3"/>'
            f'<circle cx="150" cy="165" r="20" fill="#454D56"/>'
            f'<line x1="150" y1="165" x2="{px:.1f}" y2="{py:.1f}" stroke="#fff" stroke-width="10" stroke-linecap="round"/>'
            + ring + _t(150, 285, 14, "合いました" if done else "つまみを回して合わせる"))
    return _zoom_frame(body, f"操作モードのつまみを「{MODES[to]}」に合わせる")


def estop(done, phase=0.0):
    r_out = 74 if done else 66
    r_in = 50 if done else 44
    arrow = ""
    if not done:
        arrow = (f'<path d="M92 82 A82 82 0 0 1 222 100" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round"/>'
                 f'<path d="M228 76 L226 108 L198 96 Z" fill="#fff"/>' + _ring(150, 140, 96, phase))
    body = (f'<circle cx="150" cy="140" r="104" fill="{YELLOW}" stroke="{INK}" stroke-width="3"/>'
            f'<circle cx="150" cy="140" r="{r_out}" fill="{RED}" stroke="#7E1A14" stroke-width="4"/>'
            f'<circle cx="150" cy="140" r="{r_in}" fill="#E2574C"/>' + arrow
            + _t(150, 282, 14, "ボタンが手前に出ました" if done else "矢印の向き（右）に回す"))
    return _zoom_frame(body, "非常停止ボタン。右に回してもどす")


def estop_press(done, phase=0.0):
    """非常停止を押す（パラメータを書くあいだ押したままにする）。押すと、ボタンがへこむ。"""
    r_out = 66 if done else 74
    r_in = 44 if done else 50
    arrow = ""
    if not done:
        arrow = (f'<path d="M150 14 L150 54" stroke="{INK}" stroke-width="10" stroke-linecap="round"/>'
                 f'<path d="M130 42 L150 66 L170 42 Z" fill="{INK}"/>' + _ring(150, 150, 96, phase))
    body = (f'<circle cx="150" cy="150" r="104" fill="{YELLOW}" stroke="{INK}" stroke-width="3"/>'
            f'<circle cx="150" cy="150" r="{r_out}" fill="{RED}" stroke="#7E1A14" stroke-width="4"/>'
            f'<circle cx="150" cy="150" r="{r_in}" fill="#E2574C"/>' + arrow
            + _t(150, 282, 14, "押しこまれました。そのまま" if done else "まっすぐ押しこむ"))
    return _zoom_frame(body, "非常停止ボタンを押す")


def power(done, cycle=False, phase=0.0):
    ring = "" if done else _ring(212 if cycle else 92, 130, 62, phase)
    note = (_t(212, 216, 15, "① 先に") + _t(92, 216, 15, "② 画面が消えたら")) if cycle else ""
    msg = "電源が入りました" if done else ("切 → 少し待つ → 入" if cycle else "緑の「入」を押す")
    body = (f'<circle cx="92" cy="130" r="56" fill="{GREEN}" stroke="{INK}" stroke-width="3"/>{_t(92, 140, 30, "入", fill="#fff")}'
            f'<circle cx="212" cy="130" r="56" fill="{RED}" stroke="{INK}" stroke-width="3"/>{_t(212, 140, 30, "切", fill="#fff")}'
            + note + ring + _t(150, 278, 14, msg))
    return _zoom_frame(body, "電源のボタン")


def knob(done, phase=0.0):
    t = ""
    for i, v in enumerate((0, 25, 50, 100)):
        a = -100 + i * 200 / 3
        x, y = _pol(150, 160, 104, a)
        tx, ty = _pol(150, 160, 78, a)
        on = v == 100
        t += f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="{HERE if on else INK}"/>'
        if on:
            t += f'<rect x="{x - 30:.1f}" y="{y - 16:.1f}" width="60" height="24" rx="12" fill="{HERE}"/>'
        t += _t(x, y + 2, 14 if on else 12, f"{v}%", fill="#2A1E00" if on else "#59636E")
    px, py = _pol(150, 160, 48, 100 if done else -100 + 200 / 3)
    body = (t + f'<circle cx="150" cy="160" r="60" fill="{DARK}" stroke="#111" stroke-width="3"/>'
            f'<line x1="150" y1="160" x2="{px:.1f}" y2="{py:.1f}" stroke="#fff" stroke-width="10" stroke-linecap="round"/>'
            + ("" if done else _ring(150, 160, 64, phase)) + _t(150, 36, 15, "早送りのつまみ")
            + _t(150, 282, 14, "100% になりました" if done else "右いっぱい（100%）まで上げる"))
    return _zoom_frame(body, "早送りのつまみを 100% に上げる")


def start(done, again=False, phase=0.0):
    msg = "ランプがつきました" if done else ("もう一度、緑のボタンを押す" if again else "緑のボタンを1回押す")
    body = (f'<rect x="70" y="50" width="160" height="160" rx="26" fill="{GREEN}" stroke="{INK}" stroke-width="3"/>'
            f'<rect x="88" y="68" width="124" height="124" rx="18" fill="{"#7BE0A3" if done else "#39B96D"}"/>'
            f'<path d="M150 96 L184 130 L150 164 L116 130 Z" fill="none" stroke="#fff" stroke-width="8" stroke-linejoin="round"/>'
            + ("" if done else _ring(150, 130, 92, phase))
            + _t(150, 240, 16, "起動（サイクルスタート）") + _t(150, 282, 14, msg))
    return _zoom_frame(body, "緑の起動ボタンを押す")


def keys(done, program="O0100"):
    def key(x, y, w, t, hot):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="46" rx="8" fill="{HERE if hot else KEY}" stroke="{KEY_EDGE}" stroke-width="2"/>'
                + _t(x + w / 2, y + 30, 17, t))
    chars = list(program)[:5]
    body = (_t(26, 40, 14, "① このキー", "start", "#fff") + key(26, 50, 110, "PROG", not done)
            + _t(26, 128, 14, "② 番号を打つ", "start", "#fff")
            + "".join(key(26 + i * 50, 138, 44, c, False) for i, c in enumerate(chars))
            + _t(26, 216, 14, "③ 画面の下のキー", "start", "#fff") + key(26, 226, 130, "O 検索", False)
            + (_t(226, 256, 15, "✓ 出ました", fill="#7BE0A3") if done else ""))
    return _zoom_frame(body, "押すキーの順番", bg=DARK)


def jog(done, phase=0.0):
    cells = ""
    for i, a in enumerate("XYZABC"):
        x, y = 40 + (i % 3) * 78, 44 + (i // 3) * 86
        hot = a == "B"
        cells += (f'<rect x="{x}" y="{y}" width="64" height="64" rx="10" fill="{HERE if hot else KEY}" stroke="{KEY_EDGE}" stroke-width="2"/>'
                  + _t(x + 32, y + 42, 24, "+" + a)
                  + (_ring(x + 32, y + 32, 44, phase) if hot and not done else ""))
    body = (cells + _t(150, 246, 15, "「＋B」を押し続ける")
            + _t(150, 280, 13, "原点にもどりました" if done else "ランプがつくまで離さない"))
    return _zoom_frame(body, "軸のボタンを押し続けて原点へもどす")


def counter(done, phase=0.0):
    body = (f'<rect x="24" y="50" width="252" height="150" rx="12" fill="{DARK}"/>'
            f'<rect x="40" y="66" width="220" height="70" rx="6" fill="#0C1F14"/>'
            + _t(250, 116, 40, "0.0000" if done else "0.0132", "end", "#7CF0A0")
            + f'<rect x="40" y="148" width="70" height="38" rx="6" fill="{HERE}"/>' + _t(75, 173, 15, "0にする")
            + f'<rect x="124" y="148" width="60" height="38" rx="6" fill="{KEY}"/><rect x="198" y="148" width="60" height="38" rx="6" fill="{KEY}"/>'
            + ("" if done else _ring(75, 167, 46, phase)) + _t(150, 236, 15, "角度の表示器")
            + _t(150, 276, 14, "0 になりました" if done else "黄色のキーを押して 0 にする"))
    return _zoom_frame(body, "角度の表示器を0にする", bg="#D9DDE1")


# ------------------------------------------------------------------ 1枚ものの絵（520x300）

def _wide(body, label):
    return _svg(520, 300, body, label)


def scene(phase=0.0):
    body = (f'<rect x="24" y="90" width="150" height="100" rx="8" fill="{DARK}"/><rect x="34" y="100" width="130" height="80" rx="3" fill="#3F7FE0"/>'
            f'<rect x="10" y="194" width="178" height="12" rx="6" fill="{PANEL_EDGE}"/>' + _t(99, 236, 15, "PC", fill=FG)
            + f'<path d="M188 150 C240 150 250 110 316 110" fill="none" stroke="{HERE}" stroke-width="6" stroke-linecap="round" stroke-dasharray="2 12"/>'
            + _t(250, 98, 13, "LAN ケーブル", fill=FG)
            + f'<rect x="316" y="40" width="180" height="150" rx="12" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="330" y="54" width="90" height="64" rx="4" fill="#0F2A3A"/><circle cx="460" cy="150" r="16" fill="{RED}"/>'
            f'<circle cx="356" cy="150" r="14" fill="{DARK}"/><circle cx="400" cy="150" r="14" fill="{DARK}"/>' + _t(406, 216, 15, "機械（制御装置）", fill=FG)
            + f'<rect x="210" y="210" width="96" height="50" rx="8" fill="{DARK}"/>' + _t(258, 242, 16, "0.0000", fill="#7CF0A0")
            + _t(258, 284, 13, "角度の表示器", fill=FG)
            + f'<path d="M150 206 C150 240 180 236 210 236" fill="none" stroke="{PANEL_EDGE}" stroke-width="5" stroke-linecap="round"/>')
    return _wide(body, "PC と機械と表示器をつなぐ")


def auto(done, label):
    mid = (f'<circle cx="260" cy="137" r="30" fill="{GREEN}"/><path d="M246 138 L256 149 L276 126" fill="none" stroke="#fff" '
           f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>') if done else ""
    body = (f'<rect x="40" y="90" width="140" height="94" rx="8" fill="{DARK}"/><rect x="50" y="100" width="120" height="74" rx="3" fill="#3F7FE0"/>'
            + _t(110, 220, 15, "PC", fill=FG)
            + f'<rect x="340" y="70" width="140" height="130" rx="12" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="352" y="82" width="70" height="50" rx="4" fill="#0F2A3A"/><circle cx="448" cy="170" r="13" fill="{RED}"/>'
            + _t(410, 236, 15, "機械", fill=FG)
            + f'<line x1="190" y1="137" x2="330" y2="137" stroke="{GREEN if done else HERE}" stroke-width="7" stroke-linecap="round"'
            + ('' if done else ' stroke-dasharray="3 16"') + '/>' + mid
            + _t(260, 90, 14, label, fill=FG))
    return _wide(body, "PC が自動でやっています")


def product(found):
    ok = (f'<circle cx="400" cy="40" r="26" fill="{GREEN}"/><path d="M388 41 L397 51 L414 30" fill="none" stroke="#fff" '
          f'stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>') if found else ""
    body = (f'<rect x="120" y="20" width="280" height="260" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<rect x="144" y="44" width="110" height="12" fill="{PANEL}"/>' + _t(144, 96, 13, "受注伝票番号", "start", "#59636E")
            + f'<rect x="140" y="106" width="240" height="46" rx="8" fill="#FFF3CF" stroke="{HERE}" stroke-width="3"/>' + _t(260, 138, 26, "50014984")
            + '<rect x="144" y="176" width="232" height="8" fill="#DDE2E7"/><rect x="144" y="196" width="190" height="8" fill="#DDE2E7"/>'
            '<rect x="144" y="216" width="214" height="8" fill="#DDE2E7"/>' + ok)
    return _wide(body, "受注伝票の番号")


def measure(n, total):
    total = max(2, int(total))
    pts = [(40 + i * (440 / (total - 1)), 150 - 28 * math.sin(i / 5.2) - 10 * math.sin(i / 1.7)) for i in range(max(0, min(n, total)))]
    line = ""
    if len(pts) >= 2:
        line = (f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="#1560D4" '
                f'stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>')
    dot = f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="7" fill="#1560D4"/>' if pts else ""
    body = (f'<rect x="24" y="40" width="472" height="220" rx="10" fill="none" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<line x1="40" y1="150" x2="480" y2="150" stroke="{PANEL_EDGE}" stroke-dasharray="4 6"/>'
            '<line x1="40" y1="96" x2="480" y2="96" stroke="#C8362B" stroke-width="2" stroke-dasharray="8 6"/>'
            '<line x1="40" y1="204" x2="480" y2="204" stroke="#C8362B" stroke-width="2" stroke-dasharray="8 6"/>'
            + _t(476, 88, 12, "この線の中が合格", "end", "#C8362B") + line + dot
            + _t(260, 290, 14, f"{n} ／ {total} か所", fill=FG))
    return _wide(body, "測っているところ")


def verdict(ok, saved=False):
    """結果の大きな印。ok=True 合格 / False 外れあり / None 規格が画面に無い。"""
    if saved:
        col, mark, msg = GREEN, "M214 132 L246 166 L310 96", "保存しました"
    elif ok is True:
        col, mark, msg = GREEN, "M214 132 L246 166 L310 96", "合格"
    elif ok is False:
        col, mark, msg = "#C8362B", "M222 90 L298 166 M298 90 L222 166", "外れた所があります"
    else:
        col, mark, msg = "#5A6876", "M260 84 L260 140 M260 164 L260 168", "値を見て確かめてください"
    body = (f'<circle cx="260" cy="130" r="96" fill="{col}"/><path d="{mark}" fill="none" stroke="#fff" stroke-width="20" '
            f'stroke-linecap="round" stroke-linejoin="round"/>' + _t(260, 276, 26, msg, fill=FG, weight=900))
    return _wide(body, msg)


def table_picture():
    body = (f'<rect x="60" y="200" width="400" height="30" rx="6" fill="{PANEL_EDGE}"/>'
            f'<rect x="150" y="90" width="220" height="110" rx="10" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<ellipse cx="260" cy="92" rx="96" ry="26" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<path d="M176 50 A90 26 0 0 1 344 50" fill="none" stroke="{HERE}" stroke-width="6" stroke-linecap="round"/>'
            f'<path d="M352 44 L338 64 L330 40 Z" fill="{HERE}"/>'
            f'<rect x="370" y="120" width="70" height="56" rx="8" fill="{DARK}"/>' + _t(405, 154, 13, "モーター", fill="#fff"))
    return _wide(body, "回転テーブル")


def blank():
    return _wide("", "")


# ------------------------------------------------------------------ 入り口のタイルと、ほかの流れの絵

def _tile(w, h, body, label):
    return _svg(w, h, body, label)


def tile_measure():
    body = (f'<rect x="20" y="130" width="260" height="22" rx="6" fill="{PANEL_EDGE}"/>'
            f'<rect x="80" y="62" width="140" height="70" rx="8" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<ellipse cx="150" cy="64" rx="64" ry="18" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<path d="M96 36 A56 18 0 0 1 204 36" fill="none" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/>'
            f'<path d="M210 32 L198 48 L190 28 Z" fill="{HERE}"/>'
            f'<rect x="224" y="76" width="46" height="36" rx="6" fill="{DARK}"/>' + _t(247, 100, 10, "表示器", fill="#7CF0A0")
            + _t(150, 180, 14, "角度を測る", fill=FG))
    return _tile(300, 200, body, "測る")


def tile_program():
    body = (f'<rect x="24" y="40" width="96" height="70" rx="6" fill="{DARK}"/><rect x="32" y="48" width="80" height="48" rx="3" fill="#3F7FE0"/>'
            f'<rect x="18" y="112" width="108" height="8" rx="4" fill="{PANEL_EDGE}"/>'
            f'<path d="M132 76 L176 76" stroke="{HERE}" stroke-width="6" stroke-linecap="round"/><path d="M174 64 L192 76 L174 88 Z" fill="{HERE}"/>'
            f'<rect x="196" y="30" width="84" height="90" rx="10" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="206" y="40" width="42" height="30" rx="3" fill="#0F2A3A"/>'
            f'<rect x="212" y="46" width="28" height="4" fill="#8FE0C0"/><rect x="212" y="56" width="20" height="4" fill="#8FE0C0"/>'
            f'<circle cx="266" cy="100" r="8" fill="{RED}"/>' + _t(150, 180, 14, "測る動きを入れる", fill=FG))
    return _tile(300, 200, body, "プログラムを機械に入れる")


def tile_results():
    body = (f'<rect x="60" y="18" width="130" height="120" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<polyline points="76,100 100,70 124,86 148,52 172,78" fill="none" stroke="#1560D4" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<line x1="76" y1="116" x2="174" y2="116" stroke="{PANEL_EDGE}"/>'
            f'<circle cx="214" cy="104" r="34" fill="{GREEN}"/><path d="M198 105 L210 118 L232 90" fill="none" stroke="#fff" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<rect x="224" y="24" width="56" height="40" rx="6" fill="{DARK}"/><rect x="232" y="14" width="40" height="14" rx="3" fill="#fff" stroke="{PANEL_EDGE}"/>'
            + _t(150, 180, 14, "結果を見る・印刷", fill=FG))
    return _tile(300, 200, body, "結果を見る・印刷")


def tile_alarm():
    body = (f'<rect x="60" y="20" width="130" height="104" rx="10" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="72" y="32" width="106" height="64" rx="4" fill="#0F2A3A"/>'
            f'<rect x="80" y="74" width="60" height="6" fill="#FF8A80"/><rect x="80" y="84" width="40" height="6" fill="#FF8A80"/>'
            f'<path d="M222 28 L270 112 L174 112 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>'
            f'<rect x="218" y="56" width="8" height="30" rx="3" fill="{INK}"/><circle cx="222" cy="98" r="5" fill="{INK}"/>'
            + _t(150, 180, 14, "アラームを調べる", fill=FG))
    return _tile(300, 200, body, "アラームを調べる")


def folder():
    sheet = lambda x, y: (f'<rect x="{x}" y="{y}" width="80" height="104" rx="6" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                          f'<polyline points="{x + 10},{y + 70} {x + 28},{y + 48} {x + 44},{y + 60} {x + 62},{y + 36}" fill="none" stroke="#1560D4" stroke-width="3"/>'
                          f'<rect x="{x + 10}" y="{y + 82}" width="60" height="6" fill="#DDE2E7"/>')
    body = (sheet(120, 40) + sheet(210, 52) + sheet(300, 40)
            + f'<path d="M60 130 L60 250 L460 250 L460 130 L260 130 L240 106 L80 106 Q60 106 60 130 Z" fill="{HERE}" stroke="#B57A00" stroke-width="3" stroke-linejoin="round"/>'
            + _t(260, 292, 14, "保存した結果（.BS・.KS）", fill=FG))
    return _wide(body, "保存した結果")


def printer():
    body = (f'<rect x="150" y="30" width="220" height="90" rx="6" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<polyline points="170,90 200,60 230,76 270,48 300,70" fill="none" stroke="#1560D4" stroke-width="4"/>'
            f'<rect x="110" y="110" width="300" height="90" rx="14" fill="{DARK}"/><rect x="140" y="170" width="240" height="70" rx="4" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<circle cx="380" cy="138" r="8" fill="{GREEN}"/>'
            + _t(260, 280, 14, "印刷、またはPDFにします", fill=FG))
    return _wide(body, "印刷とPDF")


def alarm_screen(has_alarm=True):
    mark = ""
    if has_alarm:
        mark = (f'<path d="M260 40 L330 160 L190 160 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="5" stroke-linejoin="round"/>'
                f'<rect x="254" y="80" width="12" height="42" rx="4" fill="{INK}"/><circle cx="260" cy="140" r="7" fill="{INK}"/>')
    else:
        mark = (f'<circle cx="260" cy="110" r="70" fill="{GREEN}"/><path d="M228 112 L252 138 L296 84" fill="none" stroke="#fff" '
                f'stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>')
    body = (mark + _t(260, 250, 18, "アラームがあります" if has_alarm else "アラームは出ていません", fill=FG, weight=900))
    return _wide(body, "アラーム")
