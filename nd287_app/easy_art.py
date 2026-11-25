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
            f'<rect x="224" y="76" width="46" height="36" rx="6" fill="{DARK}"/>' + _t(247, 100, 12, "90.00°", fill="#7CF0A0", weight=900))
    return _tile(300, 165, body, "測る")


def tile_program():
    body = (f'<rect x="24" y="40" width="96" height="70" rx="6" fill="{DARK}"/><rect x="32" y="48" width="80" height="48" rx="3" fill="#3F7FE0"/>'
            f'<rect x="18" y="112" width="108" height="8" rx="4" fill="{PANEL_EDGE}"/>'
            f'<path d="M132 76 L176 76" stroke="{HERE}" stroke-width="6" stroke-linecap="round"/><path d="M174 64 L192 76 L174 88 Z" fill="{HERE}"/>'
            f'<rect x="196" y="30" width="84" height="90" rx="10" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="206" y="40" width="42" height="30" rx="3" fill="#0F2A3A"/>'
            f'<rect x="212" y="46" width="28" height="4" fill="#8FE0C0"/><rect x="212" y="56" width="20" height="4" fill="#8FE0C0"/>'
            f'<circle cx="266" cy="100" r="8" fill="{RED}"/>')
    return _tile(300, 165, body, "プログラムを機械に入れる")


def tile_results():
    """保存したグラフを、虫めがねで見る。右上は印刷。"""
    body = (f'<rect x="40" y="14" width="150" height="130" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<polyline points="56,104 82,66 108,86 136,42 170,72" fill="none" stroke="#1560D4" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<line x1="56" y1="122" x2="174" y2="122" stroke="{PANEL_EDGE}"/>'
            f'<circle cx="188" cy="92" r="30" fill="#fff" fill-opacity="0.85" stroke="{DARK}" stroke-width="7"/>'
            f'<line x1="210" y1="114" x2="238" y2="142" stroke="{DARK}" stroke-width="11" stroke-linecap="round"/>'
            f'<rect x="232" y="30" width="46" height="30" rx="6" fill="{DARK}"/><rect x="240" y="14" width="30" height="20" rx="2" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<rect x="240" y="48" width="30" height="18" rx="2" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>')
    return _tile(300, 165, body, "結果を見る・印刷")


def tile_alarm():
    body = (f'<rect x="60" y="20" width="130" height="104" rx="10" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="72" y="32" width="106" height="64" rx="4" fill="#0F2A3A"/>'
            f'<rect x="80" y="74" width="60" height="6" fill="#FF8A80"/><rect x="80" y="84" width="40" height="6" fill="#FF8A80"/>'
            f'<path d="M222 28 L270 112 L174 112 Z" fill="{YELLOW}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>'
            f'<rect x="218" y="56" width="8" height="30" rx="3" fill="{INK}"/><circle cx="222" cy="98" r="5" fill="{INK}"/>')
    return _tile(300, 165, body, "アラームを調べる")


def folder():
    """保存した結果（グラフの紙）が、フォルダに入っている。"""
    sheet = lambda x, y: (f'<rect x="{x}" y="{y}" width="96" height="130" rx="6" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                          f'<polyline points="{x + 12},{y + 72} {x + 32},{y + 44} {x + 52},{y + 58} {x + 76},{y + 28}" fill="none" stroke="#1560D4" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
                          f'<rect x="{x + 12}" y="{y + 86}" width="72" height="6" fill="#DDE2E7"/>')
    body = (sheet(100, 24) + sheet(208, 14) + sheet(316, 24)
            + f'<path d="M50 150 L50 262 Q50 272 60 272 L460 272 Q470 272 470 262 L470 160 Q470 150 460 150 L280 150 L260 128 L64 128 Q50 128 50 142 Z" fill="{HERE}" stroke="#B57A00" stroke-width="3" stroke-linejoin="round"/>'
            + _t(260, 224, 26, "保存した結果", fill="#3A2A00", weight=900))
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


# ------------------------------------------------------------------ はじめに「どうやって準備するか」を選ぶ（分かれ道）

def _pc(x, y, s=1.0):
    return (f'<rect x="{x}" y="{y}" width="{54 * s:.0f}" height="{36 * s:.0f}" rx="4" fill="{DARK}"/>'
            f'<rect x="{x + 4}" y="{y + 4}" width="{46 * s:.0f}" height="{28 * s:.0f}" rx="2" fill="#3F7FE0"/>'
            f'<rect x="{x - 4}" y="{y + 38 * s:.0f}" width="{62 * s:.0f}" height="5" rx="2" fill="{PANEL_EDGE}"/>')


def _cnc(x, y):
    return (f'<rect x="{x}" y="{y}" width="64" height="52" rx="6" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<rect x="{x + 6}" y="{y + 6}" width="32" height="22" rx="2" fill="#0F2A3A"/><circle cx="{x + 50}" cy="{y + 38}" r="6" fill="{RED}"/>')


def _hand_keys(x, y):
    keys = "".join(f'<rect x="{x + (i % 4) * 14}" y="{y + (i // 4) * 12}" width="11" height="9" rx="2" fill="{KEY}" stroke="{KEY_EDGE}"/>'
                   for i in range(12))
    return (f'<rect x="{x - 6}" y="{y - 6}" width="68" height="46" rx="5" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="2"/>' + keys
            + f'<circle cx="{x + 46}" cy="{y + 18}" r="9" fill="{HERE}" fill-opacity="0.85"/>')


def choice_params(auto):
    """パラメータ: 自動（PC が機械へ入れる）／手（操作盤で打つ）。"""
    if auto:
        body = _pc(14, 18) + _cnc(130, 14) + (f'<path d="M76 38 L120 38" stroke="{HERE}" stroke-width="5" stroke-linecap="round" stroke-dasharray="2 9"/>'
                                               f'<path d="M118 29 L132 38 L118 47 Z" fill="{HERE}"/>')
    else:
        body = _hand_keys(34, 20) + (f'<rect x="130" y="12" width="56" height="62" rx="4" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                                     + "".join(f'<rect x="138" y="{22 + i * 12}" width="40" height="5" fill="#DDE2E7"/>' for i in range(4)))
    return _svg(200, 86, body, "パラメータ")


def choice_prog(auto):
    """プログラム: 自動（PC が作って入れる）／自分で作る（機械の側で用意する）。"""
    if auto:
        body = _pc(14, 18) + _cnc(130, 14) + (f'<path d="M76 38 L120 38" stroke="{HERE}" stroke-width="5" stroke-linecap="round" stroke-dasharray="2 9"/>'
                                               f'<path d="M118 29 L132 38 L118 47 Z" fill="{HERE}"/>'
                                               + _t(100, 12, 11, "O0100", fill=FG))
    else:
        body = _cnc(18, 14) + (f'<rect x="108" y="12" width="76" height="62" rx="4" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                               + _t(146, 34, 12, "O0100", fill=FG) + "".join(f'<rect x="116" y="{42 + i * 10}" width="{60 - i * 12}" height="4" fill="#DDE2E7"/>' for i in range(3))
                               + f'<path d="M92 40 L104 40" stroke="{HERE}" stroke-width="4" stroke-linecap="round"/>')
    return _svg(200, 86, body, "測定プログラム")


def flowchart(params, prog):
    """分かれ道を、流れ図で見せる。選んだものは色が付き、まだのものは「？」。"""
    names = {"auto": "自動", "manual": "手で打つ"}
    pnames = {"auto": "自動", "manual": "自分で作る"}

    def box(x, w, head, val, col):
        fill = {"ok": "#E3F5EA", "ask": "#FFF3CF", "plain": "#EEF2F6"}[col]
        edge = {"ok": GREEN, "ask": HERE, "plain": PANEL_EDGE}[col]
        return (f'<rect x="{x}" y="70" width="{w}" height="86" rx="12" fill="{fill}" stroke="{edge}" stroke-width="3"/>'
                + _t(x + w / 2, 100, 15, head, fill=FG) + _t(x + w / 2, 136, 19, val, fill=FG))

    def arrow(x):
        return (f'<path d="M{x} 113 L{x + 20} 113" stroke="{PANEL_EDGE}" stroke-width="4" stroke-linecap="round"/>'
                f'<path d="M{x + 18} 105 L{x + 30} 113 L{x + 18} 121 Z" fill="{PANEL_EDGE}"/>')

    pv = names.get(params) if params else "？"
    gv = pnames.get(prog) if prog else "？"
    body = (box(10, 118, "パラメータ", pv, "ok" if params else "ask") + arrow(130)
            + box(166, 118, "プログラム", gv, "ok" if prog else "ask") + arrow(286)
            + box(322, 86, "測る", "", "plain") + arrow(410)
            + box(446, 86, "保存", "", "plain")
            + _t(270, 36, 16, "分かれ道は、はじめの1回だけです", fill="#59636E"))
    return _svg(540, 200, body, "流れ図")


def params_manual():
    body = (_hand_keys(60, 90) + _t(100, 60, 15, "操作盤で打つ", fill=FG)
            + f'<rect x="250" y="40" width="190" height="200" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            + _t(345, 70, 14, "番号  値", fill=FG)
            + "".join(_t(300, 108 + i * 30, 15, f"{n}", "middle", "#59636E") + f'<rect x="330" y="{94 + i * 30}" width="80" height="6" fill="#DDE2E7"/>'
                      for i, n in enumerate((1815, 2000, 2024, 2084)))
            + f'<path d="M244 140 L212 140" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/><path d="M216 131 L202 140 L216 149 Z" fill="{HERE}"/>')
    return _wide(body, "パラメータを手で打つ")


def prog_manual():
    body = (_cnc(70, 80) + _t(102, 170, 14, "機械", fill=FG)
            + f'<rect x="250" y="40" width="190" height="200" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            + _t(345, 76, 20, "O0100", fill=FG)
            + "".join(f'<rect x="274" y="{100 + i * 28}" width="{140 - i * 22}" height="6" fill="#DDE2E7"/>' for i in range(4))
            + f'<path d="M244 140 L160 140" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/><path d="M164 131 L150 140 L164 149 Z" fill="{HERE}"/>')
    return _wide(body, "測定プログラムを機械に用意する")


# ------------------------------------------------------------------ 一旦やめる

def pause_stop(phase=0.0):
    """測っている途中にやめるとき: まず機械の「送り停止」を押す（赤い非常停止はあぶないとき）。"""
    body = (f'<rect x="20" y="30" width="480" height="150" rx="14" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="40" y="46" width="130" height="90" rx="6" fill="#0F2A3A"/>'
            + _t(105, 98, 18, "測定中", fill="#7CF0A0")
            + f'<rect x="210" y="60" width="110" height="64" rx="12" fill="{YELLOW}" stroke="{INK}" stroke-width="3"/>'
            + _t(265, 100, 20, "送り停止", fill=INK)
            + _ring(265, 92, 46, phase)
            + f'<circle cx="430" cy="92" r="40" fill="{RED}" stroke="#8E1E17" stroke-width="4"/>'
            + _t(430, 98, 15, "非常停止", fill="#fff")
            + _t(265, 212, 16, "まず ここ", fill=FG) + _t(430, 212, 14, "あぶないとき", fill=FG))
    return _svg(520, 230, body, "まず機械を止める")


def unsaved():
    """まだ保存していない: 結果の紙が、フォルダに入っていない。"""
    body = (f'<rect x="60" y="30" width="150" height="130" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            f'<polyline points="76,124 102,86 128,106 156,62 190,92" fill="none" stroke="#1560D4" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<path d="M222 96 L262 96" stroke="{HERE}" stroke-width="6" stroke-linecap="round" stroke-dasharray="3 10"/>'
            f'<path d="M258 84 L278 96 L258 108 Z" fill="{HERE}"/>'
            f'<path d="M290 70 L330 70 L342 84 L430 84 L430 160 L290 160 Z" fill="#E9C46A" stroke="#B88A1B" stroke-width="3" stroke-linejoin="round"/>'
            f'<circle cx="400" cy="46" r="22" fill="{YELLOW}" stroke="{INK}" stroke-width="3"/>' + _t(400, 54, 24, "!", fill=INK, weight=900)
            + _t(360, 194, 15, "まだ入っていません", fill=FG))
    return _svg(480, 210, body, "まだ保存していません")


# ------------------------------------------------------------------ 測り方（測定条件）を決める

def tile_cond():
    body = (f'<rect x="30" y="20" width="150" height="124" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="2"/>'
            + "".join(f'<rect x="44" y="{34 + i * 24}" width="{[90, 60, 100, 70][i]}" height="8" fill="#DDE2E7"/>'
                      f'<rect x="{150}" y="{30 + i * 24}" width="22" height="18" rx="3" fill="#FFF3CF" stroke="{HERE}" stroke-width="2"/>' for i in range(4))
            + f'<circle cx="236" cy="82" r="40" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            + "".join(f'<line x1="{236 + 40 * math.sin(math.radians(a)):.1f}" y1="{82 - 40 * math.cos(math.radians(a)):.1f}" '
                      f'x2="{236 + 31 * math.sin(math.radians(a)):.1f}" y2="{82 - 31 * math.cos(math.radians(a)):.1f}" stroke="{INK}" stroke-width="3"/>'
                      for a in range(0, 360, 30))
            + f'<path d="M236 82 L236 52" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/>')
    return _tile(300, 165, body, "測り方を決める・登録する")


def choice_kind(kind):
    """何を測るか: 分割（等間隔の目盛り）／再現性（止まる所に印）／両方。"""
    def dial(cx, ticks=(), marks=()):
        b = f'<circle cx="{cx}" cy="43" r="30" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
        for a in ticks:
            b += (f'<line x1="{cx + 30 * math.sin(math.radians(a)):.1f}" y1="{43 - 30 * math.cos(math.radians(a)):.1f}" '
                  f'x2="{cx + 23 * math.sin(math.radians(a)):.1f}" y2="{43 - 23 * math.cos(math.radians(a)):.1f}" stroke="{INK}" stroke-width="2"/>')
        for a in marks:
            b += f'<circle cx="{cx + 22 * math.sin(math.radians(a)):.1f}" cy="{43 - 22 * math.cos(math.radians(a)):.1f}" r="5" fill="{HERE}"/>'
        return b
    ticks, marks = tuple(range(0, 360, 15)), (0, 90, 180, 270)
    if kind == "division":
        body = dial(100, ticks=ticks)
    elif kind == "repeat":
        body = dial(100, marks=marks)
    else:
        body = dial(60, ticks=ticks) + dial(150, marks=marks)
    return _svg(200, 86, body, "測るもの")


def choice_tilt(tilt):
    """回転（まわる矢印）か、傾斜（かたむく板）。"""
    if tilt:
        body = (f'<rect x="60" y="56" width="80" height="14" rx="4" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3" transform="rotate(-22 100 63)"/>'
                f'<path d="M70 70 L100 70 L100 40 Z" fill="none" stroke="{HERE}" stroke-width="4"/>')
    else:
        body = (f'<ellipse cx="100" cy="46" rx="42" ry="14" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
                f'<path d="M62 34 A44 20 0 0 1 138 34" fill="none" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/>'
                f'<path d="M142 28 L130 40 L124 24 Z" fill="{HERE}"/>')
    return _svg(200, 86, body, "回転か傾斜か")


def cond_dial(pitch=10.0, marks=None, hot=None):
    """円盤に目盛り。pitch ごとの目盛りを描く（多すぎるときは間引く）。marks があれば、止まる所の印（橙の丸）を描く。"""
    cx, cy, r = 260, 150, 110
    body = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="4"/>'
    if marks is None:
        n = max(1, int(round(360 / pitch))) if pitch and pitch > 0 else 1
        step = max(1, math.ceil(n / 72))
        for i in range(0, n, step):
            a = i * 360 / n
            x1, y1 = _pol(cx, cy, r, a)
            x2, y2 = _pol(cx, cy, r - 16, a)
            body += f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{INK}" stroke-width="3"/>'
        body += _t(cx, cy + 10, 30, f"{n} か所", fill=FG)
    else:
        for a in marks:
            x, y = _pol(cx, cy, r - 24, a)
            body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="{HERE}" stroke="{INK}" stroke-width="2"/>' + _t(*_pol(cx, cy, r + 22, a), 14, f"{a:g}°", fill=FG)
        body += _t(cx, cy + 10, 26, f"{len(marks)} か所", fill=FG)
    return _wide(body.replace('font-size="14"', 'font-size="13"'), "測る所")


def cond_drawer():
    """型式の札が、引き出しに入る。"""
    body = (f'<rect x="150" y="150" width="240" height="110" rx="10" fill="#E9C46A" stroke="#B88A1B" stroke-width="3"/>'
            f'<rect x="230" y="190" width="80" height="16" rx="6" fill="#B88A1B"/>'
            f'<rect x="190" y="40" width="160" height="90" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            + _t(270, 92, 22, "RWE-200") + f'<path d="M270 136 L270 150" stroke="{HERE}" stroke-width="6" stroke-linecap="round"/>'
            f'<path d="M258 142 L270 156 L282 142 Z" fill="{HERE}"/>')
    return _wide(body, "この型式で登録する")


# ------------------------------------------------------------------ ことばの説明の絵

def graph():
    """横が角度、縦がずれ。規格の線の中に入っている。"""
    body = (f'<line x1="60" y1="240" x2="480" y2="240" stroke="{INK}" stroke-width="3"/><line x1="60" y1="30" x2="60" y2="240" stroke="{INK}" stroke-width="3"/>'
            f'<line x1="60" y1="70" x2="480" y2="70" stroke="{RED}" stroke-width="2" stroke-dasharray="8 6"/>'
            f'<line x1="60" y1="190" x2="480" y2="190" stroke="{RED}" stroke-width="2" stroke-dasharray="8 6"/>'
            f'<polyline points="60,140 110,110 160,150 210,95 260,160 310,120 360,170 410,105 470,130" fill="none" stroke="#1560D4" stroke-width="4" stroke-linejoin="round"/>'
            + _t(270, 270, 15, "角度 →", fill=FG) + _t(34, 140, 15, "ずれ", fill=FG) + _t(470, 62, 13, "規格", "end", RED) + _t(470, 208, 13, "規格", "end", RED))
    return _wide(body.replace('height="300"', 'height="300"'), "グラフの見方")


def repeat():
    """同じ所に何度も止まる。止まる位置が少しばらつく。"""
    body = f'<line x1="260" y1="40" x2="260" y2="250" stroke="{PANEL_EDGE}" stroke-width="3" stroke-dasharray="6 6"/>'
    pts = ((248, 80), (270, 110), (255, 140), (266, 170), (252, 200), (262, 225))
    for x, y in pts:
        body += f'<circle cx="{x}" cy="{y}" r="9" fill="{HERE}" stroke="{INK}" stroke-width="2"/>'
    body += _t(380, 150, 16, "止まった所の", fill=FG) + _t(380, 174, 16, "ばらつき", fill=FG) + f'<path d="M320 160 L290 160" stroke="{HERE}" stroke-width="5" stroke-linecap="round"/>'
    return _wide(body, "再現性")


def tile_words():
    body = (f'<rect x="50" y="22" width="130" height="120" rx="8" fill="#fff" stroke="{PANEL_EDGE}" stroke-width="3"/>'
            f'<rect x="62" y="38" width="80" height="8" fill="#DDE2E7"/><rect x="62" y="56" width="104" height="8" fill="#DDE2E7"/><rect x="62" y="74" width="64" height="8" fill="#DDE2E7"/>'
            f'<polyline points="62,126 88,104 112,116 140,92 168,108" fill="none" stroke="#1560D4" stroke-width="3"/>'
            f'<circle cx="218" cy="84" r="44" fill="#E3EEFF" stroke="#1560D4" stroke-width="5"/>' + _t(218, 106, 64, "?", fill="#1560D4", weight=900))
    return _tile(300, 165, body, "使い方・ことばの説明")


def measure_demo():
    """ことばの説明用: 36 か所のうち 10 か所まで入った絵。"""
    return measure(10, 36)


# ------------------------------------------------------------------ 機械に入れる（専用の画面）

def kinds(items, ok=True):
    """見つかった製品の絵。items = [(種別 "回転"/"傾斜", 「80A 200V」など)]。2つなら左右に並べる。"""
    def one(cx, kind, sub):
        if kind == "傾斜":
            pic = (f'<rect x="{cx - 70}" y="150" width="140" height="22" rx="4" fill="{PANEL_EDGE}"/>'
                   f'<g transform="rotate(-24 {cx} 140)"><rect x="{cx - 62}" y="104" width="124" height="22" rx="4" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
                   f'<circle cx="{cx}" cy="95" r="14" fill="{HERE}"/></g>')
        else:
            pic = (f'<ellipse cx="{cx}" cy="140" rx="74" ry="26" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
                   f'<ellipse cx="{cx}" cy="132" rx="62" ry="20" fill="#E3E7EB" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                   f'<path d="M{cx - 40} 96 A44 22 0 0 1 {cx + 44} 98" fill="none" stroke="{HERE}" stroke-width="7" stroke-linecap="round"/>'
                   f'<path d="M{cx + 44} 98 l-4 -14 m4 14 l-15 -3" fill="none" stroke="{HERE}" stroke-width="7" stroke-linecap="round"/>')
        return pic + _t(cx, 214, 26, kind, fill=FG, weight=900) + _t(cx, 244, 16, sub, fill="#59636E")
    items = list(items)[:2] or [("回転", "")]
    xs = [260] if len(items) == 1 else [140, 380]
    body = "".join(one(x, k, s) for x, (k, s) in zip(xs, items))
    if ok:
        body += (f'<circle cx="480" cy="34" r="22" fill="{GREEN}"/><path d="M470 35 L477 43 L491 26" fill="none" stroke="#fff" '
                 f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')
    return _wide(body, "見つかった製品")


def plug(done):
    """つなぐ: PC と機械。つながったら緑の線とチェック。"""
    return auto(done, "機械とつながりました" if done else "機械を読んでいます")


def _gear(cx, cy, r, teeth=10, fill=KEY, stroke=PANEL_EDGE):
    """歯車（まるに歯を描く）。"""
    pts = ""
    for i in range(teeth * 2):
        rr = r if i % 2 == 0 else r * 0.78
        for a in (i * 180 / teeth - 4, i * 180 / teeth + 4):
            x, y = _pol(cx, cy, rr, a)
            pts += f"{x:.1f},{y:.1f} "
    return (f'<polygon points="{pts.strip()}" fill="{fill}" stroke="{stroke}" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r * 0.28:.1f}" fill="{PANEL}" stroke="{stroke}" stroke-width="2"/>')


def choice_measure_kind(kind):
    """測定の種類: モーター（モーターと小さな歯車）／駆動装置（歯車がかみ合う箱）／フルクロ（目盛りの円盤と検出器）。"""
    if kind == "セミ・モーター":
        body = (f'<rect x="38" y="26" width="64" height="34" rx="6" fill="{PANEL}" stroke="{PANEL_EDGE}" stroke-width="3"/>'
                f'<rect x="102" y="38" width="18" height="10" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="2"/>'
                + _gear(144, 43, 22, 9) + _t(70, 48, 13, "M", fill=FG))
    elif kind == "セミ・駆動装置":
        body = (f'<rect x="40" y="10" width="120" height="66" rx="10" fill="none" stroke="{PANEL_EDGE}" stroke-width="3" stroke-dasharray="6 4"/>'
                + _gear(80, 44, 22, 9, fill=KEY) + _gear(122, 44, 18, 8, fill=PANEL))
    else:
        body = (f'<circle cx="92" cy="43" r="31" fill="{KEY}" stroke="{PANEL_EDGE}" stroke-width="3"/>')
        for a in range(0, 360, 20):
            x1, y1 = _pol(92, 43, 31, a)
            x2, y2 = _pol(92, 43, 24, a)
            body += f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{INK}" stroke-width="2"/>'
        body += (f'<rect x="132" y="30" width="30" height="26" rx="5" fill="{PANEL}" stroke="{HERE}" stroke-width="3"/>'
                 f'<line x1="132" y1="43" x2="118" y2="43" stroke="{HERE}" stroke-width="3"/>')
    return _svg(200, 86, body, "測定の種類")


def renzoku(items, cur=0, back=False):
    """連続測定: 2つの札（フルクロ→セミクロ）を矢印でつなぐ。items=[(名前,何を測る,種類)]、cur=いま何番目（全部済んだら len）。
    済んだ札は緑のチェック・いまの札はオレンジの枠。back=最後にフルへもどす矢印を描く。"""
    n = max(1, min(2, len(items)))
    xs = [30, 290] if n == 2 else [150]
    body = ""
    for i, (name, what, kind) in enumerate(items[:2]):
        x = xs[i]
        done, here = i < cur, i == cur
        edge = GREEN if done else (HERE if here else PANEL_EDGE)
        body += (f'<rect x="{x}" y="46" width="200" height="190" rx="14" fill="#fff" stroke="{edge}" stroke-width="{6 if here else 3}"/>')
        body += f'<g transform="translate({x + 6},{60})">' + choice_measure_kind(kind).split("</title>")[1].rsplit("</svg>", 1)[0] + "</g>"
        body += _t(x + 100, 172, 28, name, fill=FG)
        body += _t(x + 100, 206, 16, what, fill="#59636E", weight=600)
        if done:
            body += (f'<circle cx="{x + 176}" cy="70" r="18" fill="{GREEN}"/><path d="M{x + 167} 70 L{x + 174} 78 L{x + 187} 61" '
                     f'fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')
        elif here:
            body += _t(x + 100, 36, 15, "いま", fill="#8A6A00")
    if n == 2:
        body += (f'<path d="M232 141 L276 141" stroke="{PANEL_EDGE}" stroke-width="7" stroke-linecap="round"/>'
                 f'<path d="M264 128 L280 141 L264 154" fill="none" stroke="{PANEL_EDGE}" stroke-width="7" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    if back:
        body += (f'<path d="M390 244 C390 282 130 282 130 244" fill="none" stroke="{HERE}" stroke-width="5" stroke-linecap="round" '
                 f'stroke-dasharray="3 10"/><path d="M122 254 L130 240 L142 252" fill="none" stroke="{HERE}" stroke-width="5" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>' + _t(260, 296, 13, "最後に、フルへもどすか選びます", fill="#8A6A00"))
    return _wide(body, "連続して測る")
