# -*- coding: utf-8 -*-
"""はじめてモード（本当の初心者用の画面。今の画面は「通常」として残す）。

2026-11-24 利用者: 無駄なボタンを押させない・一方通行（もどる／次へ）・制御装置の操作は絵で案内・測定と保存まで。
文字ばかりにせず、イラスト多め。今の画面のデザインにとらわれない。

中身は新しく作らない。今ある機能を呼ぶだけ:
  機械に入れる … かんたん作成（ParamWizardDialog）を、この画面の中に入れる（host.easy_make_wizard）
  取込開始・保存 … 測定の画面と同じ関数（host.start / host.save）
  機械の様子   … easy_machine.MachinePoller（読むだけ。非常停止・操作モード・運転中・アラーム）
手順の決め方は easy_flow（Qt なし）。絵は easy_art（SVG。Qt なし）。
"""
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

from . import easy_art, easy_flow, easy_photo, easy_words, flow_guide
from . import kind_runtime as KR
from . import renzoku as RZ
from .easy_renzoku import RenzokuMixin, RZ_CTX_RESET
from .easy_flow import STEPS, INDEX, CHAPTERS

# 機械の「状態の数値」→ 絵のつまみの位置（easy_art.MODES の番号）
AUT_TO_DIAL = {0: 3, 1: 5, 3: 6, 4: 4, 5: 1, 9: 2}
WATCH_COLORS = {"wait": ("#E9EEF5", "#5A6876"), "ok": ("#E3F4EA", "#16864C"), "done": ("#E3F4EA", "#16864C"),
                "stop": ("#FBE9E7", "#C8362B")}
MANUAL_TEXT = "機械から読めません。\nやったら、ここを押します"
OFFLINE_TEXT = "機械とつながっていません（電源と LAN ケーブル）。\nつながらないまま進む"
FALLBACK_SEC = 25        # 機械から「できた」を読めないまま、これだけたったら、人が押す小さなボタンを出す
FALLBACK_MISMATCH_SEC = 6   # つまみの位置を読めているのに「合っていない」と出つづけるとき、早めに「やりました」を出す（読みが違う機械でも進める）

QSS = """
QDialog#easy { background:#EEF1F4; }
QLabel#title { font-size:26px; font-weight:900; color:#17212B; }
QLabel#line { font-size:17px; color:#17212B; }
QLabel#who { font-size:13px; font-weight:bold; border-radius:11px; padding:3px 14px; }
QLabel#watch { font-size:16px; font-weight:bold; border-radius:10px; padding:10px 14px; }
QFrame#art { background:#F4F6F8; border-radius:14px; }
QFrame#rightcard { background:#FFFFFF; border:1px solid #D5DBE1; border-radius:14px; }
QFrame#card { background:#FFFFFF; border:1px solid #D5DBE1; border-radius:12px; }
QPushButton#next { background:#1560D4; color:#FFFFFF; font-size:19px; font-weight:bold; border-radius:12px; padding:12px 30px; border:0; }
QPushButton#next:disabled { background:#A9BFE6; }
QPushButton#next[go="true"] { background:#16864C; }
QPushButton#back { background:#FFFFFF; color:#5A6876; font-size:15px; border:2px solid #D5DBE1; border-radius:12px; padding:10px 18px; }
QPushButton#back:disabled { color:#B5BEC8; }
QPushButton#act { background:#F0A400; color:#2A1E00; font-size:19px; font-weight:bold; border-radius:12px; padding:14px 22px; border:0; }
QPushButton#act:disabled { background:#EBD9A8; }
QPushButton#sub { background:transparent; color:#5A6876; font-size:13px; text-decoration:underline; border:0; padding:4px; text-align:left; }
QPushButton#fallback { background:#FFFFFF; color:#17212B; font-size:15px; font-weight:700; border:2px solid #9AA6B2; border-radius:10px; padding:10px 16px; text-align:left; }
QPushButton#fallback:hover { border-color:#1560D4; background:#F3F7FD; }
QLineEdit#fld { font-size:21px; font-weight:bold; padding:7px 12px; border:2px solid #D5DBE1; border-radius:10px; background:#FFFFFF; color:#17212B; }
QLineEdit#fld:focus { border-color:#1560D4; }
QProgressBar#pbar { border:0; border-radius:7px; background:#DCE2E8; height:14px; text-align:center; }
QProgressBar#pbar::chunk { background:#1560D4; border-radius:7px; }
"""


class SvgView(QtWidgets.QWidget):
    """SVG を縦横の比を保って、いっぱいに描く。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._r = QtSvg.QSvgRenderer(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(150, 120)
        self._svg = ""

    def set_svg(self, svg):
        self._pix = None
        if svg != self._svg:
            self._svg = svg
            self._r.load(QtCore.QByteArray(svg.encode("utf-8")))
        self.update()

    def set_photo(self, path):
        """写真を出す（向きは写真に入っている情報どおり）。読めなければ False（絵のまま）。"""
        cache = self.__dict__.setdefault("_pix_cache", {})
        key = str(path)
        if key not in cache:                          # 0.5秒ごとの描き直しで、毎回読み直さない
            rd = QtGui.QImageReader(key)
            rd.setAutoTransform(True)
            img = rd.read()
            if img.isNull():
                return False
            cache[key] = QtGui.QPixmap.fromImage(img.scaled(1400, 1400, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self._pix = cache[key]
        self._svg = ""
        self.update()
        return True

    def paintEvent(self, _e):
        pix = getattr(self, "_pix", None)
        if pix is not None:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            sc = pix.scaled(self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            p.drawPixmap((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
            return
        if not self._r.isValid():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        size = self._r.defaultSize()
        if size.isEmpty():
            return
        k = min(self.width() / size.width(), self.height() / size.height())
        w, h = size.width() * k, size.height() * k
        self._r.render(p, QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h))


class ElideLabel(QtWidgets.QLabel):
    """1行に収まらない文は、まん中を … にして縮める（全文は、ふきだし）。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setFull(text)

    def setFull(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self._fit()

    def full(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        fm = QtGui.QFontMetrics(self.font())
        w = max(40, self.width() - 4)
        self.setText(fm.elidedText(self._full, QtCore.Qt.ElideMiddle, w))
        self.setFixedHeight(fm.height() + 6)


class TileButton(QtWidgets.QAbstractButton):
    """入り口の大きなタイル（絵と短い言葉。押すと、その流れが始まる）。"""

    def __init__(self, title, sub, svg, parent=None, badge=""):
        super().__init__(parent)
        self._title, self._sub, self._badge = title, sub, badge
        self._r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")), self)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(220, 150)
        self.setFocusPolicy(QtCore.Qt.NoFocus)           # 青い枠は「マウスが乗っている」だけ（選んだ印と見まちがえない）
        self.setAccessibleName(title)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hot = self.underMouse()
        p.setPen(QtGui.QPen(QtGui.QColor("#1560D4" if hot else "#D5DBE1"), 3 if hot else 2))
        p.setBrush(QtGui.QColor("#EAF2FF" if hot else "#FFFFFF"))
        p.drawRoundedRect(r, 16, 16)
        pic_h = r.height() - 58
        size = self._r.defaultSize()
        if pic_h > 20 and not size.isEmpty():
            k = min((r.width() - 20) / size.width(), pic_h / size.height())
            w, h = size.width() * k, size.height() * k
            self._r.render(p, QtCore.QRectF(r.center().x() - w / 2, r.top() + 8, w, h))
        f = p.font()
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor("#17212B"))
        p.drawText(QtCore.QRectF(r.left(), r.bottom() - 54, r.width(), 28), QtCore.Qt.AlignCenter, self._title)
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QtGui.QColor("#5A6876"))
        p.drawText(QtCore.QRectF(r.left(), r.bottom() - 28, r.width(), 22), QtCore.Qt.AlignCenter, self._sub)
        if self._badge:                                   # 「はじめてはこれ」の札
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            bw = p.fontMetrics().horizontalAdvance(self._badge) + 24
            br = QtCore.QRectF(r.left() + 12, r.top() + 12, bw, 28)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#16864C"))
            p.drawRoundedRect(br, 14, 14)
            p.setPen(QtGui.QColor("#FFFFFF"))
            p.drawText(br, QtCore.Qt.AlignCenter, self._badge)


class ChoiceButton(QtWidgets.QAbstractButton):
    """分かれ道の小さなボタン（絵と短い言葉。選ぶと緑の枠とチェックが付く）。"""

    def __init__(self, title, svg, parent=None, compact=False):
        super().__init__(parent)
        self._title = title
        self._compact = compact                      # 2軸の製品など、選びが縦に並ぶとき: 低くする
        self._r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")), self)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setMinimumSize(110 if compact else 150, 66 if compact else 96)       # 3つ並べても狭い窓の右に切れない
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setAccessibleName(title)

    def sizeHint(self):
        return QtCore.QSize(190, 68 if self._compact else 100)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        on, hot = self.isChecked(), self.underMouse()
        p.setPen(QtGui.QPen(QtGui.QColor("#16864C" if on else "#1560D4" if hot else "#D5DBE1"), 4 if on else 2))
        p.setBrush(QtGui.QColor("#E3F5EA" if on else "#EAF2FF" if hot else "#FFFFFF"))
        p.drawRoundedRect(r, 12, 12)
        size = self._r.defaultSize()
        pic_h = r.height() - 30
        if pic_h > 10 and not size.isEmpty():
            k = min((r.width() - 12) / size.width(), pic_h / size.height())
            w, h = size.width() * k, size.height() * k
            self._r.render(p, QtCore.QRectF(r.center().x() - w / 2, r.top() + 4, w, h))
        f = p.font()
        f.setPixelSize(16)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor("#17212B"))
        p.drawText(QtCore.QRectF(r.left(), r.bottom() - 26, r.width(), 24), QtCore.Qt.AlignCenter, self._title)
        if on:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#16864C"))
            c = QtCore.QRectF(r.right() - 26, r.top() + 4, 22, 22)
            p.drawEllipse(c)
            p.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            p.drawPolyline([QtCore.QPointF(c.left() + 5, c.center().y()), QtCore.QPointF(c.center().x() - 1, c.bottom() - 6),
                            QtCore.QPointF(c.right() - 5, c.top() + 6)])


class PauseDialog(QtWidgets.QDialog):
    """「一旦やめる」の画面。押しただけでは何も捨てず、機械にも何もしない。選んだものを self.chosen に入れる。"""

    def __init__(self, parent, capturing, options):
        super().__init__(parent)
        self.setWindowTitle("一旦やめる")
        self.chosen = "continue"
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        pic = SvgView()
        pic.setFixedHeight(150)
        if capturing:
            pic.set_svg(easy_art.pause_stop())
            v.addWidget(pic)
            t = QtWidgets.QLabel("① まず、機械を止めます\n操作盤の「送り停止」のボタンを押します。（PC からは止められません）")
            t.setWordWrap(True)
            t.setStyleSheet("background:#FFF3CF; color:#7A5200; padding:10px; font-size:15px; font-weight:700;")
            v.addWidget(t)
            n = QtWidgets.QLabel("② 下から選びます。アプリを閉じると、途中の値は消えます。")
            n.setWordWrap(True)
            v.addWidget(n)
        elif any(k == "save" for k, _t in options):
            pic.set_svg(easy_art.unsaved())
            v.addWidget(pic)
            t = QtWidgets.QLabel("まだ保存していません")
            t.setStyleSheet("font-size:16px; font-weight:900; color:#7A5200;")
            v.addWidget(t)
        for key, text in options:
            b = QtWidgets.QPushButton(text)
            b.setMinimumHeight(40)
            b.setStyleSheet("QPushButton{background:#F0A400;color:#2A1E00;font-size:17px;font-weight:900;border-radius:10px;}"
                            if key == "continue" else
                            "QPushButton{background:#FFFFFF;color:#17212B;font-size:15px;border:2px solid #C5CCD3;border-radius:10px;}"
                            "QPushButton:hover{border-color:#1560D4;}")
            b.clicked.connect(lambda _c=False, k=key: self._pick(k))
            v.addWidget(b)
        self.setMinimumWidth(420)

    def _pick(self, key):
        self.chosen = key
        self.accept()


class WordsDialog(QtWidgets.QDialog):
    """ことばの説明カード（絵1つ＋3行まで）。上の小さなボタンで、ほかのカードに切り替える。"""

    def __init__(self, parent, ids):
        super().__init__(parent)
        self.setWindowTitle("ことばの説明")
        self.ids = tuple(ids)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        self.row = QtWidgets.QHBoxLayout()
        self._btns = {}
        if len(self.ids) > 1:
            wrap = QtWidgets.QScrollArea()
            wrap.setWidgetResizable(True)
            wrap.setFixedHeight(54)
            wrap.setFrameShape(QtWidgets.QFrame.NoFrame)
            inner = QtWidgets.QWidget()
            inner.setLayout(self.row)
            wrap.setWidget(inner)
            v.addWidget(wrap)
            for cid in self.ids:
                b = QtWidgets.QPushButton(easy_words.card(cid)["title"])
                b.setCheckable(True)
                b.clicked.connect(lambda _c=False, c=cid: self.show_card(c))
                self.row.addWidget(b)
                self._btns[cid] = b
            self.row.addStretch(1)
        self.pic = SvgView()
        self.pic.setMinimumHeight(220)
        v.addWidget(self.pic, 1)
        self.lbl_t = QtWidgets.QLabel("")
        self.lbl_t.setStyleSheet("font-size:20px; font-weight:900; color:#17212B;")
        v.addWidget(self.lbl_t)
        self.lbl_l = QtWidgets.QLabel("")
        self.lbl_l.setWordWrap(True)
        self.lbl_l.setStyleSheet("font-size:16px; color:#17212B;")
        v.addWidget(self.lbl_l)
        b = QtWidgets.QPushButton("とじる")
        b.clicked.connect(self.accept)
        v.addWidget(b)
        self.setMinimumWidth(520)
        self.show_card(self.ids[0])

    def show_card(self, cid):
        c = easy_words.card(cid)
        self.current = cid
        for k, b in self._btns.items():
            b.setChecked(k == cid)
        self.lbl_t.setText(c["title"])
        self.lbl_l.setText("\n".join(c["lines"]))
        art = getattr(easy_art, c["art"], easy_art.blank)
        self.pic.set_svg(art() if c["art"] not in ("verdict",) else easy_art.verdict(False))


class ChapterBar(QtWidgets.QWidget):
    """上の6つの章。済んだ章は緑、いまの章は青。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._names = list(CHAPTERS)
        self._p = [0.0] * len(CHAPTERS)
        self._cur = 0

    def set_progress(self, progress, cur, names=None):
        names = list(names or self._names)
        if progress != self._p or cur != self._cur or names != self._names:
            self._p, self._cur, self._names = list(progress), cur, names
            self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        n = max(1, len(self._names))
        gap = 8
        w = (self.width() - gap * (n - 1)) / n
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        for k, name in enumerate(self._names):
            x = k * (w + gap)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#DCE2E8"))
            p.drawRoundedRect(QtCore.QRectF(x, 0, w, 6), 3, 3)
            done = k < self._cur
            col = QtGui.QColor("#16864C" if done else "#1560D4")
            if done or k == self._cur:
                p.setBrush(col)
                p.drawRoundedRect(QtCore.QRectF(x, 0, w * (1.0 if done else self._p[k]), 6), 3, 3)
            p.setPen(QtGui.QColor("#16864C" if done else ("#17212B" if k == self._cur else "#5A6876")))
            p.drawText(QtCore.QRectF(x, 12, w, 24), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       f"{k + 1} {name}")


class EasyModeDialog(RenzokuMixin, QtWidgets.QDialog):
    """はじめてモード。host（測定の画面）の easy_* を呼ぶだけ。poller は機械の様子を読む（読むだけ）。"""

    def __init__(self, host, poller, parent=None):
        super().__init__(parent if parent is not None else (host if isinstance(host, QtWidgets.QWidget) else None))
        self.setObjectName("easy")
        self.setWindowTitle("はじめてモード")
        self.setStyleSheet(QSS)
        self.host = host
        self.poller = poller
        self.i = 0
        self.flow = "home"               # home / measure / program / results / alarm
        self.history = []
        self.ctx = {"powered_on": False, "cycled": False, "cycled_in_wizard": False, "handoff": False,
                    "skip_machine": False, "counter_reset": True, "offline_go": False}
        self._rz_init()
        self.track = {"offline": False}
        self.manual = False              # 人が「やりました」を押した（機械から読めない手順）
        self._entered_at = 0.0
        self._phase = 0
        self._shown_key = None
        self._notice = ""
        self._pending_next = False
        self._wiz = None                 # 中に入れたかんたん作成
        self._wiz_closing = False
        self.st = easy_flow.OFFLINE
        self._build()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        QtCore.QTimer.singleShot(0, self._first)

    # ------------------------------------------------------------ 組み立て
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(12)
        self.bar = ChapterBar()
        root.addWidget(self.bar)
        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(22)
        self.art = QtWidgets.QFrame()
        self.art.setObjectName("art")
        al = QtWidgets.QHBoxLayout(self.art)
        al.setContentsMargins(12, 12, 12, 12)
        al.setSpacing(10)
        self.v_map = SvgView()
        self.v_main = SvgView()
        al.addWidget(self.v_main, 1)
        self.b_photo = QtWidgets.QPushButton("絵 ／ 写真")
        self.b_photo.setCheckable(True)
        self.b_photo.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_photo.setStyleSheet("QPushButton{background:#FFFFFF;border:2px solid #C5CCD3;border-radius:10px;padding:6px 12px;font-weight:700;}"
                                   "QPushButton:checked{background:#E3EEFF;border-color:#1560D4;}")
        self.b_photo.clicked.connect(self._toggle_photo)
        self.b_photo.setVisible(False)
        self.show_photo = True                      # 写真があれば、初めは写真を出す（絵とは「絵 ／ 写真」で切り替え）
        self.art_wrap = QtWidgets.QWidget()          # 絵と写真の切り替えを1つにまとめる（隠すときは、まとめて隠す＝左を空けない）
        art_col = QtWidgets.QVBoxLayout(self.art_wrap)
        art_col.setContentsMargins(0, 0, 0, 0)
        art_col.setSpacing(6)
        art_col.addWidget(self.art, 1)
        art_col.addWidget(self.b_photo, 0, QtCore.Qt.AlignRight)
        mid.addWidget(self.art_wrap, 6)
        self.right_w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.right_w)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QtWidgets.QFrame()               # 右の文は、白い札にまとめて、上下まん中に置く（片側に寄せない・空白だらけにしない）
        self.card.setObjectName("rightcard")
        outer.addStretch(1)
        outer.addWidget(self.card)
        outer.addStretch(1)
        right = QtWidgets.QVBoxLayout(self.card)
        right.setContentsMargins(26, 22, 26, 22)
        right.setSpacing(10)
        self.lbl_rz = QtWidgets.QLabel("")                # 連続測定のあいだだけ出す帯（いま何番目の工程か）
        self.lbl_rz.setObjectName("rz_band")
        self.lbl_rz.setWordWrap(True)
        self.lbl_rz.setStyleSheet("background:#FFF3CF; color:#5A4300; border:2px solid #F0A400; border-radius:10px;"
                                  " padding:6px 12px; font-size:14px; font-weight:700;")
        self.lbl_rz.setVisible(False)
        right.addWidget(self.lbl_rz)
        self.lbl_who = QtWidgets.QLabel("")
        self.lbl_who.setObjectName("who")
        self.lbl_who.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        right.addWidget(self.lbl_who)
        self.lbl_title = QtWidgets.QLabel("")
        self.lbl_title.setObjectName("title")
        self.lbl_title.setWordWrap(True)
        right.addWidget(self.lbl_title)
        self.lbl_lines = QtWidgets.QLabel("")
        self.lbl_lines.setObjectName("line")
        self.lbl_lines.setWordWrap(True)
        right.addWidget(self.lbl_lines)
        self.extra = QtWidgets.QVBoxLayout()
        self.extra.setSpacing(8)
        right.addLayout(self.extra)
        self.lbl_watch = QtWidgets.QLabel("")
        self.lbl_watch.setObjectName("watch")
        self.lbl_watch.setWordWrap(True)
        right.addWidget(self.lbl_watch)
        self.b_manual = QtWidgets.QPushButton(MANUAL_TEXT)
        self.b_manual.setObjectName("fallback")      # 小さい下線の文字ではなく、はっきりしたボタン（主ボタンの「次へ」よりは控えめ）
        self.b_manual.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_manual.clicked.connect(self._manual_done)
        right.addWidget(self.b_manual)
        self.v_map.setParent(self.right_w)          # 操作盤の全体の絵（どこを触るか）は右の空きに置き、左は拡大の絵を大きく
        self.v_map.setMinimumSize(200, 130)
        self.v_map.setMaximumHeight(300)
        right.addWidget(self.v_map, 3)
        mid.addWidget(self.right_w, 5)
        self.embed = QtWidgets.QWidget()           # 「機械に入れる」: かんたん作成を、この画面の中に入れる
        el = QtWidgets.QVBoxLayout(self.embed)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(8)
        self.strip = QtWidgets.QFrame()              # 絵と短い言葉（かんたん作成の長い説明の代わり）
        self.strip.setObjectName("art")
        sl = QtWidgets.QHBoxLayout(self.strip)
        sl.setContentsMargins(12, 8, 12, 8)
        sl.setSpacing(16)
        self.v_strip = SvgView()
        self.v_strip.setFixedSize(200, 120)
        sl.addWidget(self.v_strip)
        sv = QtWidgets.QVBoxLayout()
        sv.setSpacing(4)
        self.lbl_s_title = QtWidgets.QLabel("")
        self.lbl_s_title.setStyleSheet("font-size:20px; font-weight:900; color:#17212B;")
        self.lbl_s_say = QtWidgets.QLabel("")
        self.lbl_s_say.setWordWrap(True)
        self.lbl_s_say.setObjectName("watch")
        self.lbl_s_detail = QtWidgets.QLabel("")
        self.lbl_s_detail.setWordWrap(True)
        self.lbl_s_detail.setStyleSheet("font-size:12px; color:#5A6876;")
        for wd in (self.lbl_s_title, self.lbl_s_say, self.lbl_s_detail):
            sv.addWidget(wd)
        sl.addLayout(sv, 1)
        el.addWidget(self.strip)
        self.lbl_rz_in = QtWidgets.QLabel("")              # 連続測定の帯（「機械に入れる」の中でも、いま何番目かが見えるように）
        self.lbl_rz_in.setObjectName("rz_band_in")
        self.lbl_rz_in.setWordWrap(True)
        self.lbl_rz_in.setStyleSheet(self.lbl_rz.styleSheet())
        self.lbl_rz_in.setVisible(False)
        el.insertWidget(0, self.lbl_rz_in)
        self.embed.setVisible(False)
        mid.addWidget(self.embed, 1)
        self.home = QtWidgets.QWidget()               # 入り口: 絵のタイルから選ぶ
        hg = QtWidgets.QGridLayout(self.home)
        hg.setContentsMargins(0, 0, 0, 0)
        hg.setSpacing(18)
        pics = {"measure": easy_art.tile_measure, "program": easy_art.tile_program,
                "results": easy_art.tile_results, "alarm": easy_art.tile_alarm, "cond": easy_art.tile_cond, "words": easy_art.tile_words}
        self.tiles = {}
        for n, (fid, fl) in enumerate(easy_flow.FLOWS.items()):
            t = TileButton(fl["title"], fl["sub"], pics[fid](), badge=fl.get("badge", ""))
            t.clicked.connect(lambda _c=False, f=fid: self._start_flow(f))
            hg.addWidget(t, n // 2, n % 2)
            self.tiles[fid] = t
        self.home.setVisible(False)
        mid.addWidget(self.home, 1)
        root.addLayout(mid, 1)
        foot = QtWidgets.QHBoxLayout()
        self.b_back = QtWidgets.QPushButton("◀ もどる")
        self.b_back.setObjectName("back")
        self.b_back.clicked.connect(self._back)
        foot.addWidget(self.b_back)
        self.b_words = QtWidgets.QPushButton("？ことば")
        self.b_words.setObjectName("sub")
        self.b_words.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_words.clicked.connect(lambda: self._open_words(easy_words.cards_for(STEPS[self.i]["id"])))
        self.b_words.setStyleSheet("QPushButton{background:#FFFFFF;color:#17212B;font-size:14px;font-weight:700;border:2px solid #C5CCD3;border-radius:10px;padding:8px 14px;}QPushButton:hover{border-color:#1560D4;}")
        self.b_words.setVisible(False)
        foot.addWidget(self.b_words)
        self.b_alarm = QtWidgets.QPushButton("アラーム")
        self.b_alarm.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_alarm.setStyleSheet("QPushButton{background:#FFFFFF;color:#17212B;font-size:14px;font-weight:700;border:2px solid #C5CCD3;border-radius:10px;padding:8px 14px;}QPushButton:hover{border-color:#1560D4;}")
        self.b_alarm.clicked.connect(self.open_alarm)
        self.b_alarm.setVisible(False)
        foot.addWidget(self.b_alarm)
        self.b_pause = QtWidgets.QPushButton("■ 一旦やめる")
        self.b_pause.setObjectName("sub")
        self.b_pause.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_pause.clicked.connect(self._open_pause)
        self.b_pause.setStyleSheet("QPushButton{background:#FFFFFF;color:#17212B;font-size:14px;font-weight:700;border:2px solid #C5CCD3;border-radius:10px;padding:8px 14px;}QPushButton:hover{border-color:#1560D4;}")
        self.b_pause.setVisible(False)
        foot.addWidget(self.b_pause)
        self.b_skip = QtWidgets.QPushButton("もう機械に入っている（飛ばす）")
        self.b_skip.setObjectName("sub")
        self.b_skip.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_skip.clicked.connect(self._skip_machine)
        self.b_skip.setVisible(False)
        foot.addWidget(self.b_skip)
        self.lbl_count = QtWidgets.QLabel("")
        self.lbl_count.setStyleSheet("color:#5A6876; font-size:13px;")
        foot.addWidget(self.lbl_count)
        foot.addStretch(1)
        self.b_next = QtWidgets.QPushButton("次へ ▶")
        self.b_next.setObjectName("next")
        self.b_next.clicked.connect(self._next)
        foot.addWidget(self.b_next)
        root.addLayout(foot)
        self.resize(1100, 700)
        scr = QtWidgets.QApplication.primaryScreen()
        if scr is not None:                          # 小さい画面（1024x768・1366x768）に収める
            g = scr.availableGeometry()
            self.resize(min(1100, g.width() - 20), min(700, g.height() - 40))
        self.setMinimumSize(900, 580)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_scale()

    def _apply_scale(self):
        """画面が大きいほど、右の文字を大きくする（右がスカスカにならないように）。小さい画面では今のまま。"""
        k = max(1.0, min(1.5, min(self.width() / 1100.0, self.height() / 700.0)))
        key = round(k, 2)
        if getattr(self, "_scale_key", None) == key:
            return
        self._scale_key = key
        self.right_w.setStyleSheet(
            f"QLabel#title{{font-size:{int(26 * k)}px;}} QLabel#line{{font-size:{int(17 * k)}px;}}"
            f"QLabel#watch{{font-size:{int(16 * k)}px;}} QLabel#who{{font-size:{int(13 * k)}px;}}"
            f"QPushButton#act{{font-size:{int(19 * k)}px;}} QPushButton#fallback{{font-size:{int(15 * k)}px;}}")

    # ------------------------------------------------------------ 手順の移り変わり
    def _hctx(self):
        """測定の画面の状態（取込・結果・入力欄）。"""
        try:
            return self.host.easy_ctx()
        except Exception:
            return {}

    def _all_ctx(self):
        c = dict(self.ctx)
        c.update(self._hctx())
        try:
            c["cond_missing"] = not self.host.e_model.text().strip()
        except Exception:
            c["cond_missing"] = False
        try:                                                    # 型式に種類の登録があるのに、まだ選んでいない・決まらない
            axes = self._kind_axes()
            c["kind_missing"] = any((not a["state"].get("kind")) or bool(a["state"].get("error")) for a in axes)
        except Exception:
            c["kind_missing"] = False
        return self._rz_ctx(c)

    def _first(self):
        self.st = self.poller.state() if self.poller else easy_flow.OFFLINE
        self._enter(INDEX["home"])

    def _enter(self, i):
        import time
        self.i = i
        s = STEPS[i]
        if s["id"] != "alarm_look":
            self._alarm_return = None
        self.manual = False
        self._entered_at = time.time()
        self.track = {"offline": not self.st["online"]} if s["id"] == "cycle_power" else {"offline": False}
        if s["id"] == "power":
            self.ctx["powered_on"] = True
        if s["id"] == "rz_plan":
            self._rz_enter_plan()
        if s["id"] == "start1" or (s["id"] == "start2" and False):
            self._start_capture()
        self._shown_key = None
        self._render()

    def _start_capture(self):
        """機械を動かす前に、PC の「取込」を始めておく（値を取りこぼさない。取込開始が先、起動があと）。"""
        h = self._hctx()
        if h.get("capturing"):
            return
        try:
            self.host.easy_start_capture()
        except Exception as e:                          # 取込を始められないとき: 理由を出す（黙らない）
            self._notice = f"取込を始められませんでした: {e}"

    def _start_flow(self, flow):
        """ホームのタイルが押された: その流れの最初の手順へ。"""
        if flow == "words":                              # ことばの説明は手順ではなく、カードの窓を開く
            self._open_words(easy_words.ALL)
            return
        c = self._all_ctx()
        j = easy_flow.first_index(self.st, c, flow)
        if j is None:
            return
        self.flow = flow
        self.history = [INDEX["home"]]
        self.ctx["offline_go"] = False
        if flow == "measure":
            self.rz, self.rz_last_machine = None, None
            self.ctx.update(RZ_CTX_RESET)
            self.ctx.update(handoff=False, skip_machine=False, offline_go=False, params="auto", prog="auto")   # 初めから「自動・自動」が選ばれている（人に決めさせない。違うときだけ押し直す）
        if flow == "cond":
            self.ctx.update(cond_kind="one", cond_tilt=self._default_tilt(), cond_registered=False, cond_msgs=[])
        self._enter(j)

    def _default_tilt(self):
        """回転か傾斜かは、いまの測り方（モード）から。決められなければ回転。"""
        try:
            return bool(self.host.easy_cond_state().get("tilt"))
        except Exception:
            return False

    def _go_home(self, force=False):
        if not force and not self._unsaved_ok():
            return
        if self._wiz is not None and not self._close_wizard():
            return
        self.flow = "home"
        self.history = []
        self._enter(INDEX["home"])

    def _next(self):
        s = STEPS[self.i]
        c = self._all_ctx()
        if s["id"] == "rz_plan":
            self._rz_confirm()
            return
        if s["id"] == "finish" and self.rz is not None and not self.rz.is_last and not self.ctx.get("rz_return_mode"):
            self._rz_advance()
            return
        if s["id"] == "finish":
            nm = c.get("next_mode")
            if nm:
                self._next_measurement(nm)
                return
            self._go_home()
            return
        if s["id"] == "cond_kind":                       # 選んだ測り方を、測定の画面のモードに入れる（登録があれば、その値が入る）
            try:
                self.host.easy_cond_mode(self.ctx["cond_kind"], bool(self.ctx["cond_tilt"]))
            except Exception as e:
                self._notice = f"測り方を入れられませんでした: {e}"
        if s["id"] == "cond_done" and self.ctx.get("cond_return") is not None:      # 測るの流れから来たとき: そこへもどる
            back = self.ctx.pop("cond_return")
            self.flow = "measure"
            self.history.append(self.i)
            self._enter(back)
            return
        if s["id"] == "alarm_look" and self._alarm_return:     # 別の手順から飛んできた: もとへもどる
            self._alarm_back()
            return
        j = easy_flow.next_index(self.i, self.st, c, self.flow)
        if j is None or s["id"] in ("output", "alarm_look", "prog_done", "cond_done"):
            self._go_home()
            return
        self.history.append(self.i)
        self._enter(j)

    def _back(self):
        c = self._hctx()
        if c.get("capturing") and not c.get("finished"):
            self._open_pause()                          # 測っている途中: 黙って前へもどらない（取込が続いたままになるため）
            return
        if STEPS[self.i]["id"] in ("result", "finish") and not self._unsaved_ok():
            return
        if self._wiz is not None and not self._close_wizard():
            return
        if self.history:
            j = self.history.pop()
            while STEPS[j]["id"] == "measuring" and self.history:
                j = self.history.pop()                  # 「測っています」は待つだけの画面。もどるときは飛び越える（入ると 1.2 秒後に結果へ戻されるため）
            if STEPS[j]["id"] == "home":
                self.flow = "home"
            self._enter(j)

    def _next_measurement(self, mode):
        """分割 → 再現性のように、続けて測る。測り方を切り替えて、プログラムの呼び出しからやり直す。"""
        try:
            self.host.easy_next_mode(mode)
        except Exception:
            pass
        self.ctx["finish_seen"] = False
        self.history.append(self.i)
        self._enter(INDEX["prog_select"])

    def _remeasure(self):
        self.history.append(self.i)
        self._enter(INDEX["prog_select"])

    # ------------------------------------------------------------ 一旦やめる・保存忘れ
    def _yesno(self, text, yes="はい", no="いいえ"):
        """確かめる（試験では、この関数を差しかえる）。"""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("はじめてモード")
        box.setText(text)
        by = box.addButton(yes, QtWidgets.QMessageBox.AcceptRole)
        box.addButton(no, QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()
        return box.clickedButton() is by

    def _unsaved(self):
        c = self._hctx()
        return bool(c.get("finished") and not c.get("saved") and not c.get("capturing"))

    def _unsaved_ok(self):
        """測り終えて、まだ保存していないとき、先へ進んでよいか確かめる。進んでよい（または保存済み）なら True。"""
        if not self._unsaved():
            return True
        if self._yesno("まだ保存していません。保存しないで進みますか？\n（保存しないと、アプリを閉じたときに測った値が消えます）",
                       "保存しないで進む", "もどる"):
            return True
        return False

    def _pause_options(self):
        """いまの様子に合わせた、選べるもの [(キー, 文)]。先頭がおすすめ。"""
        c = self._hctx()
        sid = STEPS[self.i]["id"]
        opts = [("continue", "つづける")]
        if c.get("capturing") and not c.get("finished"):
            opts += [("undo", "1か所もどす（測った値は残ります）"),
                     ("remeasure", "この測定をやめて、最初から測り直す"),
                     ("home", "やめてホームへ")]
        elif self._unsaved():
            opts += [("save", "保存する"), ("home", "保存しないでホームへ")]
        else:
            if self.history:
                opts.append(("back", "前の手順にもどる"))
            opts.append(("home", "この作業をやめてホームへ"))
        if sid == "machine_in" and self._wiz is not None:
            opts.insert(0, ("continue", "書き終わるまで待つ（おすすめ）"))
            opts = opts[:1] + [o for o in opts[1:] if o[0] != "continue"]
        return opts

    def _ask_pause(self, capturing, options):
        """「一旦やめる」の画面を出して、選んだキーを返す（試験では差しかえる）。"""
        dlg = PauseDialog(self, capturing, options)
        dlg.exec()
        return dlg.chosen

    def _open_pause(self):
        c = self._hctx()
        capturing = bool(c.get("capturing") and not c.get("finished"))
        key = self._ask_pause(capturing, self._pause_options())
        if key == "undo":
            try:
                self.host.easy_undo()
            except Exception as e:
                self._notice = f"もどせませんでした: {e}"
            self._refresh()
        elif key == "remeasure":
            n = (c.get("count") or (0, 0))[0]
            if self._yesno(f"測った {n} か所は消えます。最初から測り直しますか？", "消して測り直す", "やめる"):
                self._cancel_capture()
                self.history.append(self.i)
                self._enter(INDEX["prog_select"])
        elif key == "home":
            if capturing:
                n = (c.get("count") or (0, 0))[0]
                if not self._yesno(f"測った {n} か所は消えます。やめてホームへ行きますか？", "消してホームへ", "やめる"):
                    return
                self._cancel_capture()
                self._go_home(force=True)
            else:
                self._go_home()
        elif key == "back":
            self._back()
        elif key == "save":
            self._save()

    def _update_alarm_button(self):
        """どの手順にも出る「アラーム」。出ているときは赤く件数。読めていないときは、出ているともいないとも言わない。"""
        alarms = (self.st or {}).get("alarms")
        if alarms:
            from . import easy_machine_in
            from . import alarm_watch
            alarms = easy_machine_in.split_alarms(alarm_watch.normalize(alarms), self._alarm_settings(), self._ignore_unit())[0]
        here = STEPS[self.i]["id"] == "alarm_look"
        self.b_alarm.setVisible(not here)
        if alarms:
            self.b_alarm.setText(f"▲ アラーム {len(alarms)} 件")
            self.b_alarm.setStyleSheet("QPushButton{background:#FDE8E6;color:#B02A20;font-size:14px;font-weight:900;border:2px solid #D43A2F;border-radius:10px;padding:8px 14px;}")
        else:
            self.b_alarm.setText("アラーム")
            self.b_alarm.setStyleSheet("QPushButton{background:#FFFFFF;color:#17212B;font-size:14px;font-weight:700;border:2px solid #C5CCD3;border-radius:10px;padding:8px 14px;}QPushButton:hover{border-color:#1560D4;}")

    def _open_words(self, ids):
        WordsDialog(self, ids).exec()

    def _cancel_capture(self):
        try:
            self.host.easy_cancel()
        except Exception as e:
            self._notice = f"中止できませんでした: {e}"

    def _manual_done(self):
        self.manual = True
        if STEPS[self.i].get("detect") == "online":     # 機械につながらないまま進む。以降の機械の手順は、人の「やりました」で進める
            self.ctx["offline_go"] = True
        self._refresh()

    # ------------------------------------------------------------ 画面
    def _clear_extra(self):
        while self.extra.count():
            it = self.extra.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0)
                    if x.widget():
                        x.widget().setParent(None)
                        x.widget().deleteLater()

    def _render(self):
        """手順が変わったとき: 文・ボタン・入力欄を作り直す。"""
        s = STEPS[self.i]
        if s["id"] != "machine_in" and self._wiz is not None and not self._close_wizard():
            self.i = INDEX["machine_in"]            # 書いている最中にやめない、と答えたとき
            s = STEPS[self.i]
        c = self._all_ctx()
        self.lbl_who.setText(easy_flow.WHO.get("auto" if s["kind"] == "run" else s["kind"], "PC で")
                             if s["kind"] != "info" else "")
        self.lbl_who.setVisible(s["kind"] not in ("info", "choose", "cond"))
        if s["kind"] == "machine":
            self.lbl_who.setStyleSheet("background:#F0A400; color:#2A1E00;")
        else:
            self.lbl_who.setStyleSheet("background:#E9EEF5; color:#1560D4;")
        self._clear_extra()
        self._extra_widgets = {}
        title, lines = s["title"], list(s["lines"])
        sid = s["id"]
        if sid == "prog_select":
            progs = c.get("programs") or ()
            if progs:
                title = f"{flow_guide._o(progs[:1])} を呼び出す"
        if sid == "result":
            ng, spec = c.get("ng", 0), c.get("has_spec", False)
            lines = ([f"外れた所が {ng} か所あります。"] if ng else
                     ["ぜんぶ規格の中です。"] if spec else
                     ["規格は画面に出ていません。値を見て、保存します。"])
            title = "測り終わりました"
        if sid == "view_result":
            ng, spec = c.get("view_ng", 0), c.get("view_spec", False)
            title = "規格から外れた所があります" if ng else ("ぜんぶ規格の中です" if spec else "結果")
            lines = ([f"外れた所は {ng} か所です。"] + ["通常の画面で、くわしく見られます。"]) if ng else []
        if sid == "finish":
            path = c.get("saved_path") or ""
            lines = ["保存しました。"]
            if path:
                import ntpath              # Windows の道（\\ 区切り）を、どの OS で動かしても同じに分ける
                folder = ntpath.basename(ntpath.dirname(path)) or ntpath.dirname(path)
                lines = [f"保存先: {folder} フォルダ（{ntpath.basename(path)}）"]
            if c.get("next_mode"):
                lines.append(f"つぎは「{c['next_mode']}」。")
        if sid == "rz_plan":
            plan = self._rz_plan() or {}
            lines = []                                    # 予定の一行は、下の札の中に出す（二重にしない）
            title = "つづけて測ります"
        if sid == "finish" and self.rz is not None:
            title, lines2, _b = self._rz_finish_lines()
            if lines2:
                lines = (lines[:1] if c.get("saved_path") else []) + lines2
        self._rz_band(sid)
        self.lbl_title.setText(title)
        self.lbl_lines.setText("\n".join(lines))
        self.lbl_lines.setVisible(bool(lines))
        if sid == "result" and c.get("ng"):
            self._ng_rows(c.get("ng_items") or [])
        if sid == "view_result" and c.get("view_ng"):
            self._ng_rows(c.get("view_ng_items") or [])
        if sid == "machine_in":
            self._open_wizard_inside()
        elif sid == "rz_plan":
            self._build_rz_plan()
        elif sid == "rz_return":
            self._build_rz_return()
        elif sid == "choose":
            self._build_choose()
        elif sid == "cond_check":
            self._build_cond_check()
        elif sid == "cond_kind":
            self._build_cond_kind()
        elif sid in ("cond_div", "cond_rep"):
            self._build_cond_fields(sid)
        elif sid == "cond_reg":
            self._build_cond_reg()
        elif sid == "cond_done":
            lines = list(self.ctx.get("cond_msgs") or []) + ["次から、この型式を入れると自動で入ります。"]
            self.lbl_lines.setText("\n".join(lines))
        elif sid == "params_manual":
            self._extra_widgets["act"] = self._act_button("打つ一覧を見る…", self._param_list)
            self._small_note("機械の操作は、まだ実機で確かめていません。")
        elif sid == "prog_manual":
            self._small_note("機械の操作は、まだ実機で確かめていません。")
        elif sid == "finish":
            if c.get("saved_path"):
                el = ElideLabel(c["saved_path"])
                el.setStyleSheet("color:#5A6876; font-size:12px;")
                self.extra.addWidget(el)
                self._extra_widgets["path"] = el
            self._act_button("印刷する", self._print)
            self._act_button("PDFにする", self._pdf)
            if c.get("saved_path"):
                self._act_button("保存した場所を開く", self._open_folder)
            if self.rz is not None and self.rz.is_last and not self.ctx.get("rz_return_mode"):
                self._act_button("つぎの1台へ（載せ替え）", self._rz_next_unit)
        elif sid == "fields":
            self._build_fields()
        elif sid == "open_past":
            self._extra_widgets["act"] = self._act_button("結果をさがす…", self._open_past)
        elif sid == "output":
            self._act_button("印刷する", self._print)
            self._act_button("PDFにする", self._pdf)
        elif sid == "alarm_look":
            self._build_alarm()
        elif sid == "result":
            self._build_suffix()
            b = self._act_button("保存する", self._save)
            self._extra_widgets["act"] = b
            if c.get("ng"):
                r = QtWidgets.QPushButton("もう一度測る")
                r.setObjectName("sub")
                r.setCursor(QtCore.Qt.PointingHandCursor)
                r.clicked.connect(self._remeasure)
                self.extra.addWidget(r)
        elif sid == "measuring":
            pg = QtWidgets.QProgressBar()
            pg.setObjectName("pbar")
            pg.setTextVisible(False)
            self.extra.addWidget(pg)
            self._extra_widgets["pg"] = pg
        self._refresh()

    def _ng_rows(self, items, limit=8):
        """外れた所の一覧。項目名は折り返し、値は右にそろえて折り返さない（値だけが次の行に落ちない）。
        値が長いときだけ、項目名の下に横いっぱいで出す（名前の欄を押しつぶさない）。"""
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        for n, val in items[:limit]:
            a = QtWidgets.QLabel("・" + str(n))
            a.setObjectName("line")
            a.setWordWrap(True)
            b = QtWidgets.QLabel(str(val))
            b.setObjectName("line")
            b.setStyleSheet("font-weight:900; color:#C8362B;")
            if len(str(val)) <= 12:
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(14)
                row.addWidget(a, 1)
                b.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)
                row.addWidget(b, 0)
            else:
                row = QtWidgets.QVBoxLayout()
                row.setSpacing(0)
                b.setWordWrap(True)
                b.setIndent(14)
                row.addWidget(a)
                row.addWidget(b)
            v.addLayout(row)
        if len(items) > limit:
            m = QtWidgets.QLabel(f"…ほか {len(items) - limit} か所")
            m.setObjectName("line")
            v.addWidget(m)
        box.setObjectName("ng_rows")
        self.extra.addWidget(box)
        self._extra_widgets["ng"] = box

    # ------------------------------------------------------------ 測り方（測定条件）
    def _cond_get(self):
        try:
            return self.host.easy_cond_get()
        except Exception:
            return {}

    def _cond_marks(self):
        try:
            return list(self.host.easy_cond_state().get("blocks") or [])
        except Exception:
            return []

    def _model_edit(self):
        h = self.host
        lb = QtWidgets.QLabel("型式（例 RWE-200）")
        lb.setStyleSheet("color:#5A6876; font-size:13px;")
        self.extra.addWidget(lb)
        e = QtWidgets.QLineEdit(h.e_model.text())
        e.setObjectName("fld")
        e.setMaximumWidth(340)
        e.textChanged.connect(lambda t: h.e_model.setText(t))
        self.extra.addWidget(e)
        self._edits = [e]

    def _build_cond_check(self):
        """「測る」の流れの中で、測り方がもう登録されているか見せる。無ければ、その場で決めて登録できる。"""
        self._model_edit()
        self._kind_box = QtWidgets.QWidget()                # 測定の種類（モーター／駆動装置／フルクロ）の選び方。型式に登録があるときだけ中身が出る
        self._kind_lay = QtWidgets.QVBoxLayout(self._kind_box)
        self._kind_lay.setContentsMargins(0, 0, 0, 0)
        self._kind_lay.setSpacing(8)
        self.extra.addWidget(self._kind_box)
        for e in getattr(self, "_edits", []):
            e.editingFinished.connect(self._fill_kind_box)
        self._fill_kind_box()
        try:
            st = self.host.easy_cond_state()
        except Exception:
            st = {}
        if st.get("model") and not self._kind_axes():
            have = [n for n, k in (("分割", st.get("div")), ("再現性", st.get("rep"))) if k in ("登録", "会社の一覧")]
            lb = QtWidgets.QLabel(("登録から自動で入りました: " + "・".join(have)) if have else
                                  "この型式は、まだ測り方が登録されていません。下のボタンで決めて登録できます。")
            lb.setWordWrap(True)
            lb.setStyleSheet("font-size:14px; font-weight:700; color:" + ("#16864C" if have else "#B02A20") + ";")
            self.extra.addWidget(lb)
        r = QtWidgets.QPushButton("測り方を決める・登録する…")
        r.setObjectName("sub")
        r.setCursor(QtCore.Qt.PointingHandCursor)
        r.clicked.connect(self._cond_from_check)
        self.extra.addWidget(r)
        if hasattr(self.host, "easy_open_kind_conditions"):
            # モーター・駆動装置・フルクロの3種類をまとめて登録する画面（専用 Excel にも保存）。型式を渡して開く
            k = QtWidgets.QPushButton("種類別（モーター・駆動装置・フルクロ）にまとめて登録…")
            k.setObjectName("sub")
            k.setCursor(QtCore.Qt.PointingHandCursor)
            k.clicked.connect(lambda: self.host.easy_open_kind_conditions(st.get("model") or ""))
            self.extra.addWidget(k)

    def _build_suffix(self):
        """保存する名前（機番＋末尾）と、末尾の表。自動の記号は選んである。違うときだけ表を押して選び直す。"""
        h = self.host
        if not hasattr(h, "make_suffix_panel"):
            return
        try:
            h.refresh_save_name()
            lb = QtWidgets.QLabel("")
            lb.setWordWrap(True)
            lb.setObjectName("save_name")
            lb.setStyleSheet("color:#17212B; font-size:14px; font-weight:700;")
            self.extra.addWidget(lb)
            panel = h.make_suffix_panel(self)
            self.extra.addWidget(panel)
            self._save_name_lbl, self._suffix_panel = lb, panel

            h.register_save_name_label(lb)         # 末尾・機番が変わるたび、ホストが一斉に更新する（表の選択と必ず一致）
        except Exception as e:
            self._notice = f"機番の末尾の表を出せませんでした: {e}"

    def _kind_state(self):
        try:
            return self.host.easy_kind_state() or {}
        except Exception:
            return {}

    def _fill_kind_box(self):
        """「測り方を確かめる」の中の、測定の種類の帯と選び方。種類別の表の問題は、種類の有無にかかわらず赤い帯で出す。"""
        lay = getattr(self, "_kind_lay", None)
        if lay is None:
            return
        try:
            lay.count()
        except RuntimeError:
            return
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        try:
            self.host.refresh_kind_choices()
        except Exception:
            pass
        st = self._kind_state()
        if st.get("problems"):
            band = QtWidgets.QLabel(st["problems"])
            band.setObjectName("kind_problem")
            band.setWordWrap(True)
            band.setStyleSheet("background:#FBE9E7; color:#B02A20; border:2px solid #D43A2F; border-radius:10px;"
                               " padding:8px 12px; font-size:14px; font-weight:700;")
            lay.addWidget(band)
        axes = self._kind_axes()
        if not axes:
            return
        self._kind_btns, self._kind_btns_axis = {}, {}
        two = len(axes) > 1
        for ax in axes:
            self._fill_kind_axis(lay, ax["state"], ax["label"] if two else "", ax["axis"], first=(ax is axes[0]))
        lay.invalidate()                                    # 中身を作り直したあと、入れ物の高さを取り直す（文が選びの絵に重ならないように）
        self._kind_box.updateGeometry()
        lay.activate()

    def _kind_axes(self):
        """種類の登録がある軸ごとの状態。ホストが軸ごとに答えられないとき（古い形）は、今の軸だけ。"""
        try:
            return list(self.host.easy_kind_axes())
        except AttributeError:
            st = self._kind_state()
            return [{"axis": "rotary", "label": "", "state": st}] if st.get("kinds") else []
        except Exception:
            return []

    def _fill_kind_axis(self, lay, st, axis_label, axis, first=True):
        kinds = st.get("kinds") or []
        head = QtWidgets.QLabel((f"【{axis_label}】" if axis_label else "") + "何を測りますか？")
        head.setObjectName("kind_head_" + axis)
        head.setWordWrap(True)
        head.setStyleSheet("color:#17212B; font-size:15px; font-weight:700;")
        lay.addWidget(head)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        btns = {}
        for k in kinds:
            b = ChoiceButton(k["label"], easy_art.choice_measure_kind(k["kind"]), compact=True)               # 低い窓でも、下の説明が絵に重ならない高さ
            b.setChecked(st.get("kind") == k["kind"])
            b.setToolTip(k.get("hint", ""))
            b.clicked.connect(lambda _c=False, kk=k["kind"], tt=bool(st.get("tilt")): self._choose_kind(kk, tt))
            row.addWidget(b)
            btns[k["kind"]] = b
        lay.addLayout(row)
        self._kind_btns_axis[axis] = btns
        if first:
            self._kind_btns = btns
        cur = next((k for k in kinds if k["kind"] == st.get("kind")), None)
        if cur is None:
            msg, col = "どれで測るか、選んでください。選ぶと「次へ」が押せます。", "#8A6A00"
        elif st.get("error"):
            msg, col = st["error"] + "\nこのままでは測定プログラムを作りません。登録を直してください。", "#B02A20"
        else:
            msg, col = cur.get("hint", ""), "#16864C"
            if cur.get("semi") and not axis_label:
                msg += "\nセミクロの結果も検査表・Webに出します。保存の機番の末尾でフルと名前を分けます。"
        lb = QtWidgets.QLabel(msg)
        lb.setWordWrap(True)
        lb.setStyleSheet(f"font-size:14px; font-weight:700; color:{col};")
        lb.setMinimumHeight(lb.fontMetrics().lineSpacing() * (msg.count("\n") + 2))      # 折り返しでも、前の絵に重ならない高さ
        lay.addWidget(lb)

    def _choose_kind(self, kind, tilt=None):
        try:
            if tilt is None:
                self.host.easy_set_kind(kind)
            else:
                self.host.easy_set_kind(kind, tilt)
        except Exception as e:
            self._notice = f"種類を入れられませんでした: {e}"
        self._fill_kind_box()
        self._refresh()

    def _cond_from_check(self):
        """測るの流れの途中から、測り方の流れへ。終わったら、ここにもどる。"""
        self.ctx["cond_return"] = self.i
        self.ctx.update(cond_kind="one", cond_tilt=self._default_tilt(), cond_registered=False, cond_msgs=[])
        self.flow = "cond"
        self.history.append(self.i)
        self._enter(easy_flow.first_index(self.st, self._all_ctx(), "cond"))

    def _build_cond_kind(self):
        A = easy_art
        self._choice_btns = {}
        rows = (("cond_kind", "何を測りますか", (("division", "分割だけ", A.choice_kind("division")),
                                                  ("repeat", "再現性だけ", A.choice_kind("repeat")),
                                                  ("one", "両方", A.choice_kind("one")))),
                ("cond_tilt", "回転ですか、傾斜ですか", ((False, "回転", A.choice_tilt(False)), (True, "傾斜", A.choice_tilt(True)))))
        self._model_edit()
        for key, head, opts in rows:
            lb = QtWidgets.QLabel(head)
            lb.setStyleSheet("color:#17212B; font-size:15px; font-weight:700;")
            self.extra.addWidget(lb)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            for val, text, svg in opts:
                b = ChoiceButton(text, svg)
                b.setChecked(self.ctx.get(key) is not None and self.ctx.get(key) == val)
                b.clicked.connect(lambda _c=False, k=key, v=val: self._choose(k, v))
                row.addWidget(b)
                self._choice_btns[(key, val)] = b
            self.extra.addLayout(row)

    _COND_FIELDS = {
        "cond_div": (("wheel", "何度ごとに測るか（°）", "ホイールの刻みです。360 ÷ 刻み ＝ 測る場所の数。"),
                     ("worm", "細かく測る刻み（°）", "ウォームを細かく見る所の刻みです。"),
                     ("range", "細かく測る範囲（°）", "細かく測る範囲の広さです。"),
                     ("start", "細かく測り始める角度（°）", "")),
        "cond_div_tilt": (("wstart", "どこから（°）", "傾斜の開始角度です。"),
                          ("wend", "どこまで（°）", "傾斜の終了角度です。"),
                          ("wheel", "何度ごとに測るか（°）", ""),
                          ("worm", "細かく測る刻み（°）", ""),
                          ("range", "細かく測る範囲（°）", ""),
                          ("start", "細かく測り始める角度（°）", "")),
        "cond_rep": (("blocks", "何か所で測るか", "止まる所の数です。"),
                     ("repeats", "何回ずつ測るか", "同じ所へ行って、くり返す回数です。"),
                     ("rstart", "どこから（°）", "最初の止まる所です。"),
                     ("rend", "どこまで（°）", "最後の止まる所です。")),
    }

    def _build_cond_fields(self, sid):
        tilt = bool(self.ctx.get("cond_tilt"))
        key = "cond_div_tilt" if (sid == "cond_div" and tilt) else sid
        self._cond_spins = {}
        vals = self._cond_get()
        for name, label, help_ in self._COND_FIELDS[key]:
            lb = QtWidgets.QLabel(label + ("　" + help_ if help_ else ""))
            lb.setStyleSheet("color:#5A6876; font-size:13px;")
            lb.setWordWrap(True)
            self.extra.addWidget(lb)
            integer = name in ("blocks", "repeats")
            sp = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
            sp.setRange(0, 999 if integer else 3600)
            if not integer:
                sp.setDecimals(3)
            sp.setValue(int(vals.get(name, 0)) if integer else float(vals.get(name, 0)))
            sp.setMaximumWidth(200)
            sp.valueChanged.connect(lambda v, n=name: self._cond_changed(n, v))
            self.extra.addWidget(sp)
            self._cond_spins[name] = sp
        self.lbl_cond = QtWidgets.QLabel("")
        self.lbl_cond.setStyleSheet("background:#FFF3CF; color:#7A5200; padding:6px; font-size:15px; font-weight:700;")
        self.extra.addWidget(self.lbl_cond)
        self._cond_summary(sid)

    def _cond_changed(self, name, v):
        try:
            self.host.easy_cond_set(name, v)
        except Exception as e:
            self._notice = f"入れられませんでした: {e}"
        self._cond_summary(STEPS[self.i]["id"])
        self._refresh()

    def _cond_summary(self, sid):
        g = self._cond_get()
        if sid == "cond_div":
            w = g.get("wheel") or 0
            txt = f"360 ÷ {w:g} ＝ {360 / w:g} か所" if w else "刻みを入れてください"
        else:
            marks = self._cond_marks()
            txt = (" ・ ".join(f"{a:g}°" for a in marks) + f"　の {len(marks)} か所 × {g.get('repeats', 0):g} 回") if marks else ""
        if getattr(self, "lbl_cond", None) is not None:
            try:
                self.lbl_cond.setText(txt)
            except RuntimeError:
                pass

    def _build_cond_reg(self):
        try:
            st = self.host.easy_cond_state()
        except Exception:
            st = {}
        ck = self.ctx.get("cond_kind")
        lines = [f"型式 {st.get('model', '')}　（{'傾斜' if self.ctx.get('cond_tilt') else '回転'}）"]
        if ck in ("division", "one"):
            lines.append("分割: " + {"登録": "登録あり", "会社の一覧": "会社の一覧にあります", "なし": "まだ登録なし"}.get(st.get("div"), ""))
        if ck in ("repeat", "one"):
            lines.append("再現性: " + ("登録あり（上書きします）" if st.get("rep") == "登録" else "まだ登録なし"))
        if (ck in ("division", "one") and not self.ctx.get("cond_tilt") and st.get("div") == "会社の一覧"
                and not st.get("div_same_master")):
            lines.append("これからは、あなたの登録を先に使います。測る順番は標準にもどります。")
        lb = QtWidgets.QLabel("\n".join(lines))
        lb.setWordWrap(True)
        lb.setStyleSheet("font-size:14px; color:#17212B;")
        self.extra.addWidget(lb)
        self._extra_widgets["act"] = self._act_button("この型式で登録する", self._cond_register)

    def _cond_register(self):
        try:
            msgs = self.host.easy_cond_register(self.ctx["cond_kind"], bool(self.ctx["cond_tilt"]))
        except Exception as e:
            self._notice = f"登録できませんでした: {e}"
            self._refresh()
            return
        self.ctx["cond_msgs"] = list(msgs)
        self.ctx["cond_registered"] = True
        self.history.append(self.i)
        self._next_after_register()

    def _next_after_register(self):
        j = easy_flow.next_index(self.i, self.st, self._all_ctx(), self.flow)
        if j is not None:
            self._enter(j)

    def _small_note(self, text):
        lb = QtWidgets.QLabel(text)
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#8A6A00; font-size:12px;")
        self.extra.addWidget(lb)

    def _build_choose(self):
        """分かれ道: パラメータと、プログラム。それぞれ「自動」か「手」。最初の1回で決める。"""
        A = easy_art
        self._choice_btns = {}
        rows = (("params", "パラメータ", (("auto", "自動で入れる", A.choice_params(True)), ("manual", "手で打つ", A.choice_params(False)))),
                ("prog", "測定プログラム", (("auto", "自動で作って入れる", A.choice_prog(True)), ("manual", "自分で作る", A.choice_prog(False)))))
        for key, head, opts in rows:
            lb = QtWidgets.QLabel(head)
            lb.setStyleSheet("color:#17212B; font-size:15px; font-weight:700;")
            self.extra.addWidget(lb)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            for val, text, svg in opts:
                b = ChoiceButton(text, svg)
                b.setChecked(self.ctx.get(key) == val)
                b.clicked.connect(lambda _c=False, k=key, v=val: self._choose(k, v))
                row.addWidget(b)
                self._choice_btns[(key, val)] = b
            self.extra.addLayout(row)

    def _choose(self, key, val):
        self.ctx[key] = val
        for (k, v), b in self._choice_btns.items():
            if k == key:
                b.setChecked(v == val)
        self._refresh()

    def _param_list(self):
        try:
            self.host.easy_param_list()
        except Exception as e:
            self._notice = f"一覧を開けませんでした: {e}"
        self._refresh()

    def _act_button(self, text, slot):
        b = QtWidgets.QPushButton(text)
        b.setObjectName("act")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.clicked.connect(slot)
        self.extra.addWidget(b)
        return b

    def _build_fields(self):
        h = self.host
        specs = [("機番（保存するファイルの名前になります）", h.e_machine), ("名前（測る人）", h.e_operator),
                 ("温度（℃）", h.e_temp)]
        if not h.e_model.text().strip():
            specs.insert(0, ("型式（例 RWE-200）", h.e_model))
        self._edits = []
        for label, src in specs:
            lb = QtWidgets.QLabel(label)
            lb.setStyleSheet("color:#5A6876; font-size:13px;")
            self.extra.addWidget(lb)
            e = QtWidgets.QLineEdit(src.text())
            e.setObjectName("fld")
            e.setMaximumWidth(340)
            e.textChanged.connect(lambda t, src=src: src.setText(t))
            if src is h.e_model:
                e.editingFinished.connect(lambda: self._model_entered())
            self.extra.addWidget(e)
            self._edits.append(e)

    def _open_past(self):
        try:
            self.host.easy_open_past()
        except Exception as e:
            self._notice = f"開けませんでした: {e}"
        self._refresh()

    def _open_folder(self):
        try:
            self.host.easy_open_folder()
        except Exception as e:
            self._notice = f"開けませんでした: {e}"
        self._refresh()

    def _print(self):
        try:
            self.host.easy_print()
        except Exception as e:
            self._notice = f"印刷できませんでした: {e}"

    def _pdf(self):
        try:
            self.host.easy_pdf()
        except Exception as e:
            self._notice = f"PDFにできませんでした: {e}"

    # ------------------------------------------------------------ アラーム（① 一覧 → ② 意味とやること → ③ 確かめる → ④ 記録）
    # 機械から読むだけ。PC からアラームを消す（リセットする）ことはしない。
    _alarm_return = None             # open_alarm で飛んできたとき、もどる先 (手順の番号, 流れ, 履歴)

    def _alarm_settings(self):
        return getattr(self.host, "settings", None) or {}

    def _alarm_db(self):
        from . import fanuc_alarms
        p = self._alarm_settings().get("fanuc_alarm_csv")
        try:
            if p:
                from pathlib import Path
                from .settings import app_dir
                if not Path(p).is_absolute():
                    p = str(app_dir() / p)
            return fanuc_alarms.load_alarms(p or None)
        except Exception:
            return fanuc_alarms.load_alarms()

    def _alarm_log_path(self):
        """アラーム記録.csv の場所（PC 側。マスタの外）。試験では差しかえる。"""
        from . import alarm_watch
        from .settings import app_dir
        return app_dir() / alarm_watch.RECORD_FILE

    def _alarm_allowed(self):
        from . import focas_ops
        return focas_ops.allowed_codes(self._alarm_settings())

    def _ignore_unit(self):
        return str(self.ctx.get("unit") or "")

    def _alarm_all(self):
        from . import alarm_watch
        return alarm_watch.normalize(self.st.get("alarms"))

    def _alarm_now(self):
        """出すアラーム（無視中のものは除く。無視中は _alarm_hidden）。"""
        from . import easy_machine_in
        return easy_machine_in.split_alarms(self._alarm_all(), self._alarm_settings(), self._ignore_unit())[0]

    def _alarm_hidden(self):
        from . import easy_machine_in
        return easy_machine_in.split_alarms(self._alarm_all(), self._alarm_settings(), self._ignore_unit())[1]

    def _alarm_ignore(self, code):
        from . import easy_machine_in
        easy_machine_in.add_ignore(self._alarm_settings(), self._ignore_unit(), code)
        self._alarm_save_settings()
        self._al["key"] = None
        self._alarm_sync()
        self._refresh()

    def _alarm_unignore(self, code):
        from . import easy_machine_in
        easy_machine_in.remove_ignore(self._alarm_settings(), self._ignore_unit(), code)
        self._alarm_save_settings()
        self._al["key"] = None
        self._alarm_sync()
        self._refresh()

    def _alarm_save_settings(self):
        """無視の一覧を設定に残す（試験では差しかえる）。"""
        try:
            self.host.easy_save_settings()
        except Exception:
            pass

    def _alarm_unread(self):
        """つながっているのに、アラームを読めなかった（None）。「アラームなし」とは別。"""
        return bool(self.st.get("online")) and self.st.get("alarms") is None

    def _al_label(self, style="", wrap=True):
        lb = QtWidgets.QLabel("")
        lb.setWordWrap(wrap)
        if style:
            lb.setStyleSheet(style)
        return lb

    def _build_alarm(self):
        """① いま機械に出ているアラームの一覧 ② 意味とやること ③ やったあと確かめる ④ 直ったら記録。
        番号を打って調べることも、今までどおりできる。"""
        self._al = {"sel": None, "snap": [], "key": None, "pic": None, "asked": set(), "fixed": None, "remedy": ""}
        self.lbl_alarm_head = self._al_label("font-size:16px; font-weight:bold; color:#17212B;")
        self.extra.addWidget(self.lbl_alarm_head)
        self.alarm_scroll = QtWidgets.QScrollArea()          # 最大 10 件。画面が伸びないように、高さを決める
        self.alarm_scroll.setWidgetResizable(True)
        self.alarm_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.alarm_scroll.setMaximumHeight(130)
        self.alarm_box = QtWidgets.QWidget()
        self.alarm_list = QtWidgets.QVBoxLayout(self.alarm_box)
        self.alarm_list.setContentsMargins(0, 0, 0, 0)
        self.alarm_list.setSpacing(4)
        self.alarm_scroll.setWidget(self.alarm_box)
        self.extra.addWidget(self.alarm_scroll)
        self.b_al_read = QtWidgets.QPushButton("もう一度読む")
        self.b_al_read.setObjectName("sub")
        self.b_al_read.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_al_read.clicked.connect(self._alarm_reread)
        self.extra.addWidget(self.b_al_read)
        self.lbl_al_big = self._al_label("font-size:20px; font-weight:900; color:#17212B; background:#FFF3CF; "
                                         "border-radius:8px; padding:8px 12px;")
        self.lbl_al_big.setVisible(False)
        self.extra.addWidget(self.lbl_al_big)
        self.lbl_alarm = self._al_label()
        self.lbl_alarm.setObjectName("line")
        self.extra.addWidget(self.lbl_alarm)
        self.b_al_ignore = QtWidgets.QPushButton("この機械では無視する")
        self.b_al_ignore.setObjectName("sub")
        self.b_al_ignore.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_al_ignore.clicked.connect(lambda: self._al["sel"] and self._alarm_ignore(self._al["sel"]["code"]))
        self.b_al_ignore.setVisible(False)
        self.extra.addWidget(self.b_al_ignore)
        self.b_al_check = self._act_button("やりました。確かめる ▶", self._alarm_check)
        self.b_al_check.setVisible(False)
        self.lbl_al_result = self._al_label("font-size:15px; font-weight:bold; color:#17212B;")
        self.lbl_al_result.setVisible(False)
        self.extra.addWidget(self.lbl_al_result)
        self.al_ask = QtWidgets.QWidget()                      # ④ 直ったら1回だけ聞く
        ak = QtWidgets.QVBoxLayout(self.al_ask)
        ak.setContentsMargins(0, 0, 0, 0)
        ak.setSpacing(6)
        self.al_ask_title = self._al_label("font-size:15px; font-weight:bold; color:#16864C;")
        ak.addWidget(self.al_ask_title)
        self.b_al_fix_std = QtWidgets.QPushButton("")
        self.b_al_fix_std.setObjectName("act")
        self.b_al_fix_std.clicked.connect(self._alarm_fix_std)
        ak.addWidget(self.b_al_fix_std)
        self.e_al_fix = QtWidgets.QLineEdit()
        self.e_al_fix.setObjectName("fld")
        self.e_al_fix.setPlaceholderText("ほかのやり方なら、ここに一言（例: 非常停止を押して、もどした）")
        ak.addWidget(self.e_al_fix)
        row = QtWidgets.QHBoxLayout()
        b_free = QtWidgets.QPushButton("この一言で記録する")
        b_free.setObjectName("back")
        b_free.clicked.connect(self._alarm_fix_free)
        b_skip = QtWidgets.QPushButton("記録しない")
        b_skip.setObjectName("sub")
        b_skip.clicked.connect(self._alarm_fix_skip)
        row.addWidget(b_free)
        row.addWidget(b_skip)
        row.addStretch(1)
        ak.addLayout(row)
        self.al_ask.setVisible(False)
        self.extra.addWidget(self.al_ask)
        lb = QtWidgets.QLabel("番号を打って調べる（例 PS0010。機械の画面に出ています）")
        lb.setStyleSheet("color:#5A6876; font-size:13px;")
        self.extra.addWidget(lb)
        e = QtWidgets.QLineEdit()
        e.setObjectName("fld")
        e.setMaximumWidth(340)
        self.extra.addWidget(e)
        self.e_alarm = e
        e.textChanged.connect(self._alarm_search)
        self._alarm_sync()
        first = next((a for a in self._alarm_now() if not self._alarm_is_normal(a)), None)
        if first is not None:                         # 機械に出ているものがあれば、最初から意味を出す
            self._alarm_pick(first)
        else:
            self._alarm_search("")

    def _alarm_is_normal(self, a):
        from . import alarm_watch
        return alarm_watch.is_normal(a, self._alarm_allowed())

    def _alarm_sync(self):
        """① 一覧を、いまの機械の様子に合わせる（変わったときだけ作り直す。打っている字は消さない）。"""
        if not hasattr(self, "_al") or not hasattr(self, "alarm_list"):
            return
        now = self._alarm_now()
        online = bool(self.st.get("online"))
        unread = self._alarm_unread()
        hidden = self._alarm_hidden()
        key = (online, unread) + tuple((a["code"], a["text"], a["axis"]) for a in now) + tuple(a["code"] for a in hidden)
        if key == self._al["key"]:
            return
        self._al["key"] = key
        while self.alarm_list.count():
            w = self.alarm_list.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not online:
            self.lbl_alarm_head.setText("機械につながっていません（電源と LAN ケーブル）")
        elif unread:
            self.lbl_alarm_head.setText("アラームを読めませんでした（もう一度読むを押してください）")
        elif not now:
            self.lbl_alarm_head.setText("いま機械に出ているアラームは、ありません")
        else:
            self.lbl_alarm_head.setText(f"いま機械に出ているアラーム {len(now)} 件（押すと、意味とやることが出ます）")
        for a in (now if online else []):
            normal = self._alarm_is_normal(a)
            axis = f"（軸 {a['axis']}）" if a.get("axis") else ""
            b = QtWidgets.QPushButton(f"{'●' if normal else '▲'} {a['code']}{axis}　{a['text']}")
            b.setObjectName("back")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet("text-align:left; font-size:14px; padding:6px 12px;")
            b.clicked.connect(lambda _=False, a=a: self._alarm_pick(a))
            self.alarm_list.addWidget(b)
        if online and now:
            leg = self._al_label("color:#5A6876; font-size:12px;")
            leg.setText("▲ 異常　● 異常ではない")
            self.alarm_list.addWidget(leg)
        for a in (hidden if online else []):                # 無視中: 小さな灰色の1行（消さない。やめられる）
            row = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            gl = self._al_label("color:#5A6876; font-size:12px;")
            gl.setText(f"無視中: {a['code']}")
            hl.addWidget(gl)
            bu = QtWidgets.QPushButton("無視をやめる")
            bu.setObjectName("sub")
            bu.clicked.connect(lambda _=False, c=a["code"]: self._alarm_unignore(c))
            hl.addWidget(bu)
            hl.addStretch(1)
            self.alarm_list.addWidget(row)
        self.alarm_list.addStretch(1)

    def _alarm_reread(self):
        """もう一度読む（読む係が持っている、いちばん新しい値に合わせる）。"""
        if self.poller is not None:
            try:
                self.st = self.poller.state()
            except Exception:
                pass
        self._al["key"] = None
        self._alarm_sync()
        self._refresh()

    def _alarm_pick(self, a):
        """② 1件を選んだ: 意味・原因・やること。ここで「いまの一覧」を覚えておく（③ で比べる）。"""
        from . import alarm_watch as AW
        self._al.update(sel=a, snap=self._alarm_now(), pic=None, remedy="")
        self.lbl_al_result.setVisible(False)
        self.al_ask.setVisible(False)
        code, text = a["code"], a["text"]
        lines = []
        normal = self._alarm_is_normal(a)
        if normal:
            lines.append(AW.NORMAL_NOTE + "。")
        else:
            fix = AW.last_fix(self._alarm_log_path(), code)
            if fix:
                lines.append(f"前はこれで直りました: {fix}")
            res = AW.lookup(self._alarm_db(), code, text)
            if res["known"]:
                x = res["exact"][0]
                lines.append(f"{x.get('code', '')}  {x.get('title', '')}")
                lines.append(f"原因: {x.get('cause', '')}")
                lines.append(f"やること: {x.get('remedy', '')}")
                self._al["remedy"] = x.get("remedy", "")
            else:
                lines.append(f"{AW.UNKNOWN_NOTE}。上の機械の文字を見て、機械メーカーの資料で確かめてください。")
                for x in res["near"][:2]:
                    lines.append(f"{AW.NEAR_LABEL}: {x.get('code', '')}  {x.get('title', '')}（{x.get('remedy', '')}）")
            self._al["pic"] = AW.remedy_art(code, text)
        self.lbl_al_big.setText(f"機械の画面の文字（{code}）\n{text}" if text else f"{code}")
        self.lbl_al_big.setVisible(True)
        self.lbl_alarm.setText("\n".join(lines))
        self.b_al_check.setVisible(not normal)
        self.b_al_ignore.setVisible(not normal)
        self.b_al_ignore.setText("この機械では無視する" if self._ignore_unit() else "全部の機械で無視する")
        self.e_alarm.blockSignals(True)
        self.e_alarm.setText("")
        self.e_alarm.blockSignals(False)
        self._refresh()

    def _alarm_check(self):
        """③ やったあと、読み直して比べる。消えた ✓／まだ出ている／新しく出た。"""
        from . import alarm_watch as AW
        sel = self._al.get("sel")
        if sel is None:
            return
        self._alarm_reread()
        if not self.st.get("online"):
            self.lbl_al_result.setText("機械につながっていません（電源と LAN ケーブル）。つながってから、もう一度押してください。")
            self.lbl_al_result.setVisible(True)
            return
        if self._alarm_unread():                          # 読めていない: 直ったとも、まだとも言わない
            self.lbl_al_result.setText("アラームを読めませんでした（もう一度読むを押してください）。")
            self.lbl_al_result.setVisible(True)
            self.al_ask.setVisible(False)
            return
        d = AW.alarm_diff(self._al["snap"], self._alarm_now())

        def bad(lst):
            return [a for a in lst if not self._alarm_is_normal(a)]
        gone, still, new = bad(d["gone"]), bad(d["still"]), bad(d["new"])
        lines = [f"消えました ✓  {a['code']}" for a in gone]
        lines += [f"まだ出ています  {a['code']}  {a['text']}" for a in still]
        lines += [f"新しく出ました  {a['code']}  {a['text']}" for a in new]
        if not still and not new:
            lines.append("直りました。")
        else:
            lines.append("まだ残っています。「やること」をもう一度見て、機械で試してください（アプリからは消せません）。")
        self.lbl_al_result.setText("\n".join(lines))
        self.lbl_al_result.setVisible(True)
        fixed = next((a for a in gone if a["code"] == sel["code"]), None) or (gone[0] if gone else None)
        if not still and not new and fixed is not None and fixed["code"] not in self._al["asked"]:
            self._alarm_ask(fixed)

    def _alarm_ask(self, a):
        """④ 全部消えたら1回だけ聞く: どれで直りましたか。"""
        self._al["fixed"] = a
        self._al["asked"].add(a["code"])
        self.al_ask_title.setText(f"どれで直りましたか（{a['code']}）。次に同じアラームが出たとき、最初に出します。")
        rem = self._al.get("remedy") or ""
        self.b_al_fix_std.setText(f"「やること」のとおりにしたら直りました\n{rem}" if rem else "")
        self.b_al_fix_std.setVisible(bool(rem))
        self.e_al_fix.setText("")
        self.al_ask.setVisible(True)

    def _alarm_fix_std(self):
        self._alarm_record(self._al.get("remedy") or "")

    def _alarm_fix_free(self):
        t = self.e_al_fix.text().strip()
        if not t:
            self.e_al_fix.setFocus()
            return
        self._alarm_record(t)

    def _alarm_fix_skip(self):
        self.al_ask.setVisible(False)

    def _alarm_record(self, did):
        from . import alarm_watch as AW
        a = self._al.get("fixed")
        if a is None:
            return
        em = getattr(self.host, "e_machine", None)
        try:
            AW.append_record(self._alarm_log_path(), {
                "機番": em.text() if em is not None else "", "CNC ID": self.st.get("cnc_id") or "",   # CNC ID は読む係がまだ出さない（空）
                "番号": a["code"], "機械の文字": a["text"], "やったこと": did, "結果": AW.RESULT_FIXED})
            msg = "記録しました。次に同じ番号が出たら、この直し方を最初に出します。"
        except Exception as e:
            msg = f"記録できませんでした: {e}"
        self.al_ask.setVisible(False)
        self.lbl_al_result.setText(self.lbl_al_result.text() + "\n" + msg)

    def _alarm_search(self, q):
        """番号を打って調べる（機械につながっていなくても使える）。ぴったり同じ番号を先に出す。"""
        from . import fanuc_alarms, alarm_watch as AW
        q = q.strip()
        self.b_al_check.setVisible(False)
        self.lbl_al_result.setVisible(False)
        self.al_ask.setVisible(False)
        self.lbl_al_big.setVisible(False)
        self._al["sel"], self._al["pic"] = None, None
        if not q:
            self.lbl_alarm.setText("番号を入れてください。")
            return
        if self._alarm_typed_normal(q):
            self.lbl_alarm.setText(AW.NORMAL_NOTE + "。")
            return
        try:
            rows = self.host.easy_alarm_info(q)
        except Exception as e:
            self.lbl_alarm.setText(f"調べられませんでした: {e}")
            return
        exact = [r for r in rows if fanuc_alarms.is_exact_code({"code": r[0]}, q)]
        if exact:
            code, title, cause, remedy = exact[0]
            self.lbl_alarm.setText(f"{code}  {title}\n原因: {cause}\nやること: {remedy}")
            return
        letters, num = fanuc_alarms._split_code(q)
        prefixes = {fanuc_alarms._split_code(a.get("code", ""))[0] for a in self._alarm_db()}
        looks_code = (letters in prefixes and num is not None) or q.isdigit()
        if looks_code:                                     # 「PS0010」のような番号: ぴったりは無い
            near = "".join(f"\n{AW.NEAR_LABEL}: {r[0]}  {r[1]}" for r in rows[:2])
            self.lbl_alarm.setText(f"{AW.UNKNOWN_NOTE}。機械の画面の文字を見て、機械メーカーの資料で確かめてください。{near}")
            return
        if not rows:
            self.lbl_alarm.setText("見つかりませんでした。通常の画面の「アラーム検索」で、ことばでも探せます。")
            return
        code, title, cause, remedy = rows[0]
        self.lbl_alarm.setText(f"{code}  {title}\n原因: {cause}\nやること: {remedy}")

    def _alarm_typed_normal(self, q):
        """打った番号が、異常ではないアラーム（SW0100 など）か。"""
        from . import fanuc_alarms
        want = fanuc_alarms._split_code(q)
        if not want[0] or want[1] is None:
            return False
        return any(fanuc_alarms._split_code(c) == want for c in self._alarm_allowed())

    def open_alarm(self):
        """どの手順からでも、アラームの手順へ飛ぶ。「もとの手順にもどる」で、飛ぶ前の手順へもどる。"""
        if STEPS[self.i]["id"] == "alarm_look":
            return
        prev = (self.i, self.flow, list(self.history))
        self.flow = "alarm"
        self.history = prev[2] + [self.i]
        self._alarm_return = prev
        self._enter(INDEX["alarm_look"])
        if STEPS[self.i]["id"] != "alarm_look":            # 書き込み中などで移れなかった: 元のまま
            self.flow, self.history, self._alarm_return = prev[1], prev[2], None

    def _alarm_back(self):
        r, self._alarm_return = self._alarm_return, None
        if not r:
            self._back()
            return
        i, flow, hist = r
        self.flow, self.history = flow, hist
        self._enter(i)

    def _model_entered(self):
        try:
            self.host.on_model_entered()
        except Exception:
            pass

    def _refresh(self):
        """毎回（0.5秒ごと）: 絵・機械の様子・「次へ」を、いまの状態に合わせる。"""
        s = STEPS[self.i]
        sid = s["id"]
        c = self._all_ctx()
        st = self.st
        if sid == "home":                            # 入り口: 絵のタイルだけ
            for w in (self.right_w, self.art_wrap, self.embed, self.b_next, self.b_back, self.b_pause, self.b_words, self.b_alarm, self.b_skip, self.lbl_count, self.bar):
                w.setVisible(False)
            self.home.setVisible(True)
            self.b_back.setEnabled(False)
            return
        if sid == "alarm_look":
            self._alarm_sync()
        self.home.setVisible(False)
        self.bar.setVisible(True)
        self.b_back.setVisible(True)
        self.b_pause.setVisible(True)
        self.b_words.setVisible(True)
        self._update_alarm_button()
        inside = sid == "machine_in" and self._wiz is not None
        self.right_w.setVisible(not inside)
        self.art_wrap.setVisible(not inside and sid != "result")      # 結果の段は、機番の末尾の表を広く見せるため、左の絵を外す
        self.embed.setVisible(inside)
        self.b_skip.setVisible(sid == "machine_in" and not c.get("handoff") and self.flow == "measure")
        self.b_next.setVisible(not inside)
        self.lbl_count.setVisible(not inside)
        if inside:                                   # 中のかんたん作成の操作（入れる・押す）はそのまま。案内は絵と短い言葉
            self._update_strip()
            self.b_back.setEnabled(bool(self.history))
            self._set_bar()
            return
        done = easy_flow.done_now(sid, st, c, self.track) or (self.manual and s["kind"] == "machine")
        # 手順の絵
        ph = (self._phase % 2) * 0.9
        self._set_art(s, st, c, done, ph)
        # 機械の様子（読めたものだけ）
        wl = easy_flow.watch_line(sid, st, c, self.track)
        if self._notice:
            wl = ("stop", self._notice)
        if sid == "measuring":
            cur, total = c.get("count") or (0, 0)
            wl = ("wait", f"{cur} ／ {total} か所" if total else "値を待っています…")
            pg = self._extra_widgets.get("pg")
            if pg is not None:
                pg.setMaximum(max(1, total))
                pg.setValue(cur)
            if c.get("finished") and not self._pending_next:
                self._pending_next = True
                QtCore.QTimer.singleShot(1200, self._after_measuring)
        if sid == "start1" and not c.get("capturing") and not self._notice:
            wl = ("wait", "PC の取込を始めています…")
        if wl:
            bg, fg = WATCH_COLORS[wl[0]]
            self.lbl_watch.setStyleSheet(f"background:{bg}; color:{fg};")
            self.lbl_watch.setText(("● " if wl[0] == "wait" and self._phase % 2 else "● ") + wl[1])
            self.lbl_watch.setVisible(True)
        else:
            self.lbl_watch.setVisible(False)
        # 機械から読めないときだけ、人が押す小さなボタン
        import time
        d = s.get("detect")
        waited = time.time() - self._entered_at
        offline_go = bool(self.ctx.get("offline_go")) and not st["online"]   # つながらないまま進むと決めたあと: 機械の手順は、待たずに人が押せる
        if d == "online":                                      # 電源: 機械が見えないまま待ちつづけない
            show_manual = bool(s["kind"] == "machine" and not done and not st["online"] and waited > FALLBACK_SEC)
            self.b_manual.setText(OFFLINE_TEXT)
        else:
            show_manual = bool(s["kind"] == "machine" and d and not done
                               and (not easy_flow.can_detect(d, st) and st["online"] or waited > FALLBACK_SEC or offline_go
                                    or (str(d).startswith("aut_") and st["online"] and waited > FALLBACK_MISMATCH_SEC)))
            self.b_manual.setText(MANUAL_TEXT)
        self.b_manual.setVisible(show_manual)
        # 「次へ」
        no_detect = s["kind"] == "machine" and not d
        nx = self.b_next
        label, go, enabled = "次へ ▶", False, False
        if sid in ("machine_in", "result"):
            label = "次へ ▶"
            enabled = bool(done)
        elif sid == "measuring":
            label, enabled = "測り終わるまで待ちます", False
        elif sid == "finish":
            label, go, enabled = ("次の測定へ ▶" if c.get("next_mode") else "ホームへ"), True, True
            if self.rz is not None and not self.ctx.get("rz_return_mode"):
                b = self._rz_finish_lines()[2]
                if b:
                    label = b
        elif sid == "cond_done" and self.ctx.get("cond_return") is not None:
            label, go, enabled = "もどる ▶", True, True
        elif sid == "alarm_look" and getattr(self, "_alarm_return", None):
            label, go, enabled = "もとの手順にもどる", True, True
        elif sid in ("output", "alarm_look", "prog_done", "cond_done"):
            label, go, enabled = "ホームへ", True, True
        elif sid == "open_past":
            label, enabled = "次へ ▶", bool(c.get("has_data"))
            go = enabled
        elif no_detect:
            label, enabled = "やりました ▶", True
        elif s["kind"] == "info":
            label, enabled = s.get("button") or "次へ ▶", True
        else:
            enabled = bool(done)
            go = enabled
        if sid == "machine_in" and done:
            go = True
        nx.setText(label)
        nx.setEnabled(enabled)
        if nx.property("go") != go:
            nx.setProperty("go", go)
            nx.style().unpolish(nx)
            nx.style().polish(nx)
        act = self._extra_widgets.get("act") if hasattr(self, "_extra_widgets") else None
        if act is not None:
            if sid == "machine_in":
                act.setEnabled(not done)
            if sid == "result":
                act.setEnabled(not done)
        self.b_back.setEnabled(bool(self.history))
        shown = [x for x in range(len(STEPS)) if x == self.i or (self.flow in STEPS[x]["fl"] and easy_flow.needed(STEPS[x]["id"], st, c))]
        self.lbl_count.setText(f"{shown.index(self.i) + 1} ／ {len(shown)}" if self.i in shown else "")
        self._set_bar()

    def _set_bar(self):
        f = self.flow if self.flow in easy_flow.FLOWS else "measure"
        self.bar.set_progress(easy_flow.chapter_progress(self.i, f), STEPS[self.i]["fl"].get(f, 0),
                              easy_flow.FLOWS[f]["chapters"])

    def _update_strip(self):
        w = self._wiz
        if type(w).__name__ == "MachineInPanel":
            self.strip.setVisible(False)
            return
        self.strip.setVisible(True)
        try:
            b = w.guide_brief()
        except Exception:
            return
        br = easy_flow.wizard_brief(b["step"], b["kind"], b["text"], b["missing"], b["done"])
        A = easy_art
        ph = (self._phase % 2) * 0.9
        pic = br["pic"]
        label = "機械に入れています" if b["step"] == 3 else "測定の動きを入れます"
        if pic == "product":
            svg = A.product(not b["missing"])
        elif pic == "table":
            svg = A.table_picture()
        elif pic == "power":
            svg = A.power(False, True, ph)
        elif pic == "scene":
            svg = A.scene()
        elif pic == "estop":
            svg = A.estop(False, ph)
        elif pic == "estop_press":
            svg = A.estop_press(False, ph)
        elif pic == "auto_done":
            svg = A.auto(True, "入りました")
        else:
            svg = A.auto(br["kind"] == "done", label)
        self.v_strip.set_svg(svg)
        self.lbl_s_title.setText(f"{br['title']}")
        bg, fg = WATCH_COLORS["wait" if br["kind"] == "do" else br["kind"]]
        if br["kind"] == "do":
            bg, fg = "#FFF3CF", "#7A5200"
        self.lbl_s_say.setStyleSheet(f"background:{bg}; color:{fg};")
        self.lbl_s_say.setText(br["say"])
        self.lbl_s_detail.setText(br["detail"])
        self.lbl_s_detail.setVisible(bool(br["detail"]))

    def _photo_base(self):
        try:
            from .settings import app_dir
            return getattr(self, "photo_base", None) or app_dir()
        except Exception:
            return "."

    def _toggle_photo(self):
        self.show_photo = self.b_photo.isChecked()
        self._refresh()

    def _set_art(self, s, st, c, done, ph):
        """絵を出し、同じ内容の写真が `写真` フォルダにあれば、写真に切り替えられるようにする（無ければ絵のまま）。"""
        self._set_art_svg(s, st, c, done, ph)
        path = None
        try:
            path = easy_photo.step_photo(s, self._photo_base())
        except Exception:
            path = None
        self.b_photo.setVisible(path is not None)
        if path is not None:
            self.b_photo.setChecked(self.show_photo)
            if self.show_photo:
                if not self.v_main.set_photo(path):
                    self.b_photo.setVisible(False)
                if s.get("part"):
                    self.v_map.setVisible(True)          # 操作盤の全体の絵（どこを触るか）は、写真のときも残す

    def _set_art_svg(self, s, st, c, done, ph):
        sid = s["id"]
        A = easy_art
        aut = st["aut"]
        dial_now = AUT_TO_DIAL.get(aut, 5)
        if s.get("part"):
            self.v_map.setVisible(True)
            mode_idx = A.MODE_INDEX[s["to"]] if (s.get("to") and done) else dial_now
            self.v_map.set_svg(A.panel(s["part"], mode_idx, ph))
            z = s["zoom"]
            if z == "power":
                svg = A.power(done, False, ph)
            elif z == "power_cycle":
                svg = A.power(done, True, ph)
            elif z == "estop":
                svg = A.estop(done, ph)
            elif z == "estop_press":
                svg = A.estop_press(done, ph)
            elif z == "dial":
                svg = A.dial(A.MODE_INDEX[s["to"]], dial_now, done, ph)
            elif z == "knob":
                svg = A.knob(self.manual, ph)
            elif z == "start":
                svg = A.start(done, False, ph)
            elif z == "start_again":
                svg = A.start(done, True, ph)
            elif z == "keys":
                progs = c.get("programs") or ()
                svg = A.keys(self.manual, flow_guide._o(progs[:1]) or "O0100")
            elif z == "jog":
                svg = A.jog(self.manual, ph)
            else:
                svg = A.blank()
            self.v_main.set_svg(svg)
            return
        self.v_map.setVisible(False)
        art = s.get("art")
        if sid == "finish" and self.rz is not None:                  # 連続測定: 済んだ工程の印つきの絵
            self.v_main.set_svg(A.renzoku(self._rz_art_items(), self.rz.pos + 1, back=False))
            return
        if sid == "counter0":
            svg = A.counter(self.manual, ph)
        elif art == "scene":
            svg = A.scene()
        elif art == "choose":
            svg = A.flowchart(c.get("params"), c.get("prog"))
        elif art == "renzoku":
            svg = A.renzoku(self._rz_art_items(), 0 if sid == "rz_plan" else 2, back=(sid == "rz_return"))
        elif art == "cond_kind":
            svg = A.tile_cond()
        elif art == "cond_div":
            g = self._cond_get()
            svg = A.cond_dial(g.get("wheel") or 0)
        elif art == "cond_rep":
            svg = A.cond_dial(marks=self._cond_marks())
        elif art == "cond_reg":
            svg = A.cond_drawer()
        elif art == "params_manual":
            svg = A.params_manual()
        elif art == "prog_manual":
            svg = A.prog_manual()
        elif art == "product":
            svg = A.product(bool(c.get("handoff") or c.get("skip_machine")))
        elif art == "table":
            svg = A.table_picture()
        elif art == "measure":
            cur, total = c.get("count") or (0, 0)
            svg = A.measure(cur, total or 36)
        elif art == "verdict":
            if sid == "view_result":
                ng, spec = c.get("view_ng", 0), c.get("view_spec", False)
                svg = A.verdict(False if ng else (True if spec else None), saved=True)
            else:
                ng, spec = c.get("ng", 0), c.get("has_spec", False)
                svg = A.verdict(False if ng else (True if spec else None), saved=bool(c.get("saved")))
        elif art == "verdict_saved":
            svg = A.verdict(True, saved=True)
        elif art == "folder":
            svg = A.folder()
        elif art == "printer":
            svg = A.printer()
        elif art == "alarm":
            pic = (getattr(self, "_al", None) or {}).get("pic")
            if pic == "power_cycle":
                svg = A.power(False, True, ph)
            elif pic == "estop":
                svg = A.estop(False, ph)
            else:
                svg = A.alarm_screen(bool(st.get("alarms")))
        elif art == "auto_done":
            svg = A.auto(True, "入りました")

        else:
            svg = A.blank()
        self.v_main.set_svg(svg)

    # ------------------------------------------------------------ 0.5秒ごと
    def _tick(self):
        self._phase += 1
        if self.poller is not None:
            st = self.poller.state()
            if not st["online"]:
                self.track["offline"] = True
            self.st = st
        s = STEPS[self.i]
        # 電源を入れ直す手順が済んだら記録（次から出さない）
        if s["id"] == "cycle_power" and easy_flow.is_done("cycled", self.st, self.track):
            self.ctx["cycled"] = True
        if s["id"] == "machine_in" and self._wiz is not None and self.ctx.get("prog") == "manual":
            w = self._wiz                                  # プログラムは自分で作る: パラメータが入ったら、ここは終わり
            if getattr(w, "_params_ok", False) or getattr(w, "_params_same", False):
                self._params_only_done()
                return
        # 機械の様子が変わって、いまの手順がもう要らなくなったときは、そのまま見せる（勝手に飛ばさない）
        self._refresh()

    def _params_only_done(self):
        """パラメータが機械に入った（または同じだった）。プログラムは送らず、次へ進む。"""
        if self._wiz is not None and not self._close_wizard():
            return
        self.ctx["handoff"] = True
        QtCore.QTimer.singleShot(0, self._next)

    def _after_measuring(self):
        self._pending_next = False
        if STEPS[self.i]["id"] == "measuring" and self._hctx().get("finished"):
            j = INDEX["result"]
            self.history.append(self.i)
            self._enter(j)

    # ------------------------------------------------------------ 押すもの
    def _skip_machine(self):
        if self._wiz is not None and not self._close_wizard():
            return
        self._rz_skip_machine()
        self.ctx["skip_machine"] = True
        self._render()

    def _open_wizard_inside(self):
        """かんたん作成を、この画面の中に入れる（別の窓を開かない）。開いている間は、機械を読む係を止める。"""
        c = self._all_ctx()
        if self._wiz is not None or c.get("handoff") or c.get("skip_machine"):
            return
        env = None
        try:
            env = self.host.easy_machine_in_env()
        except Exception:
            env = None
        if env:                                            # 専用の画面（伝票番号→号機→つなぐ→決まったこと→書く。すでにつながっていれば先につなぐ）
            from .easy_machine_in_ui import MachineInPanel
            if self.rz is not None:                        # 連続測定: 今の工程に合わせて、初めの選びを決めておく（人が直せる）
                env = dict(env, semi_pref=bool(self.ctx.get("rz_semi")), choice_pref=self.ctx.get("rz_choice"))
            panel = MachineInPanel(env, self.embed)
            self.strip.setVisible(False)
            self.embed.layout().addWidget(panel, 1)
            panel.finished.connect(self._wizard_finished)
            self._wiz = panel
            if self.poller is not None:
                self.poller.pause()
            panel.start()
            return
        w = self.host.easy_make_wizard(self.embed)
        w.embedded = True                                  # 長い説明は出さない（上の絵と短い言葉が案内する）
        w.setWindowFlags(QtCore.Qt.Widget)
        w.setMaximumSize(16777215, 16777215)               # 単独の窓用の幅の上限を外す（中に入れたら、見せている側が横いっぱいを使う）
        for name in ("b_mode", "b_close"):               # 「全部表示」への切り替えと「閉じる」は出さない（初心者用。進むのは「測定へ進む」）
            try:
                getattr(w, name).hide()
            except Exception:
                pass
        self.embed.layout().addWidget(w, 1)
        w.finished.connect(self._wizard_finished)
        w.show()
        try:
            w._apply_mode()                                # 長い説明を隠す
        except Exception:
            pass
        self._wiz = w
        if self.poller is not None:
            self.poller.pause()

    def _wizard_finished(self, _r):
        """中のかんたん作成の「測定へ進む」が押された（accept）。結果を受け取って、次へ進む。"""
        w = self._wiz
        if w is None or self._wiz_closing:
            return
        res = (w.result() if type(w).__name__ == "MachineInPanel" else self.host.easy_wizard_result(w)) or {}
        self._drop_wizard()
        self._rz_after_wizard(res)
        if res.get("unit"):
            self.ctx["unit"] = res["unit"]
        if res.get("program_manual"):                      # 測定プログラムを送らなかったときは、機械で用意する
            self.ctx["prog"] = "manual"
        if res.get("program_handoff"):                     # 送ったとき: 型式・測り方を、送ったプログラムに合わせる
            try:
                self.host.easy_apply_handoff(res["program_handoff"])
            except Exception as e:
                self._notice = f"型式と測り方を入れられませんでした: {e}"
        if res.get("cycled") and (res.get("program_manual") or res.get("panel")):
            self.ctx["cycled"] = True                      # 電源の入れ直しは、画面の中で済んだ（そのあとの原点復帰は次の手順）
        if res.get("handoff"):
            self.ctx["handoff"] = True
        if res.get("cycled"):
            self.ctx["cycled_in_wizard"] = True
        if self.ctx.get("handoff"):
            QtCore.QTimer.singleShot(0, self._next)
        else:
            self._render()

    def _drop_wizard(self):
        w, self._wiz = self._wiz, None
        if self.poller is not None:
            self.poller.resume()
        if w is not None:
            w.setParent(None)
            w.deleteLater()

    def _close_wizard(self):
        """中のかんたん作成を片づける。書いている最中は、今までと同じ確認が出る。片づけたら True。"""
        w = self._wiz
        if w is None:
            return True
        try:
            if getattr(w, "_writer", None) is not None and not w._close_writer():
                return False
        except Exception:
            pass
        self._wiz_closing = True
        try:
            w.reject()
        except Exception:
            pass
        self._wiz_closing = False
        self._drop_wizard()
        return True

    def _save(self):
        try:
            self.host.easy_save()
        except Exception as e:
            self._notice = f"保存できませんでした: {e}"
        self._refresh()

    # ------------------------------------------------------------ 閉じる
    def done(self, r):
        if self._wiz is not None:
            self._close_wizard()
        if self.poller is not None:
            self.poller.stop()
        self._timer.stop()
        super().done(r)

    def reject(self):
        if self._hctx().get("capturing"):
            a = QtWidgets.QMessageBox.question(
                self, "はじめてモード", "測っている途中です。やめますか？（測った値は通常の画面に残ります）",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if a != QtWidgets.QMessageBox.Yes:
                return
        elif not self._unsaved_ok():
            return
        super().reject()
