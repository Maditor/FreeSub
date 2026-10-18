"""Style (a tab of the right column): how the subtitles look and sit on the video. Every change shows on the
preview at once (a sample line appears when no line is on screen) and is exactly what Export burns in.

    Presets   ready-made looks (text colour, outline, box)
    Text      font, size, bold, colour, outline
    Layout    words per line, lines per subtitle, longest time on screen, position (drag the lines on the video)
    Box       the subtitles inside a box drawn on the video: colour, opacity, blur (drag the box to move it)
"""
import re

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..config import settings
from ..i18n import tr
from .widgets import Card, IconButton, button, section, wait_cursor

OUTLINES = [("none", "None"), ("thin", "Thin"), ("normal", "Medium"), ("thick", "Thick")]
SWATCHES = ["#ffffff", "#ffe14d", "#ffd08a", "#b8f5d0", "#9fd8ff", "#ffc2d6", "#000000", "#1b1b1b"]

# ready-made looks: text, outline colour, outline size, box (None = no box) as (colour, opacity %)
PRESETS = [
    ("Classic", "#ffffff", "#000000", "normal", None),
    ("Yellow", "#ffe14d", "#000000", "normal", None),
    ("Soft", "#ffffff", "#2b2b2b", "thin", None),
    ("Strong", "#ffffff", "#000000", "thick", None),
    ("Warm", "#ffd08a", "#3a2200", "normal", None),
    ("Mint", "#b8f5d0", "#0d3320", "normal", None),
    ("Sky", "#9fd8ff", "#06223a", "normal", None),
    ("Rose", "#ffc2d6", "#4a1030", "normal", None),
    ("Cinema", "#f5f5f5", "#000000", "none", ("#000000", 55)),
    ("Night", "#ffffff", "#000000", "none", ("#0b1a33", 70)),
    ("Paper", "#1b1b1b", "#ffffff", "none", ("#ffffff", 85)),
    ("Marker", "#111111", "#ffffff", "none", ("#ffd23f", 90)),
]
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _norm(text: str) -> str | None:
    t = text.strip()
    if not _HEX.match(t):
        return None
    return ("#" + t.lstrip("#")).lower()


def _swatch(hex_color: str, size=18) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(128, 128, 128), 1))
    p.setBrush(QColor(hex_color))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 4, 4)
    p.end()
    return QIcon(pm)


def _spin(lo, hi, value, step=1, suffix="", dec=None, tip="", width=72):
    w = QDoubleSpinBox() if dec is not None else QSpinBox()
    if dec is not None:
        w.setDecimals(dec)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    w.setSuffix(suffix)
    w.setToolTip(tip)
    w.setAlignment(Qt.AlignCenter)
    w.setButtonSymbols(QAbstractSpinBox.NoButtons)   # type a number or use the arrow keys: narrow and clean
    w.setFixedWidth(width)
    return w


def _unit(text):
    lb = QLabel(text)
    lb.setProperty("muted", True)
    return lb


def _name(text, tip=""):
    lb = QLabel(text)
    lb.setProperty("muted", True)
    if tip:
        lb.setToolTip(tip)
    return lb


def _hbox(*items, spacing=6):
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for w in items:
        if w is None:
            h.addStretch(1)
        elif isinstance(w, int):
            h.addSpacing(w)
        else:
            h.addWidget(w)
    return h


def _grid():
    g = QGridLayout()
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(10)
    g.setVerticalSpacing(8)
    g.setColumnMinimumWidth(0, 92)
    g.setColumnStretch(1, 1)
    return g


def _card_body(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


class ColorField(QWidget):
    """A colour: the swatch (opens the palette) and its code (#RRGGBB), typed or pasted."""
    changed = Signal(str)

    def __init__(self, value: str, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.value = _norm(value) or "#ffffff"
        self.swatch = QToolButton()
        self.swatch.setFixedSize(32, 30)
        self.swatch.setIconSize(QSize(18, 18))
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.setToolTip("Choose a colour")
        self.swatch.clicked.connect(self._pick)
        self.code = QLineEdit()
        self.code.setMaxLength(7)
        self.code.setFixedWidth(86)
        self.code.setFont(QFont("Consolas"))
        self.code.setToolTip("Colour code, e.g. #FFFFFF")
        self.code.editingFinished.connect(self._typed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.swatch)
        lay.addWidget(self.code)
        lay.addStretch(1)
        self._show()

    def set_value(self, value: str):
        v = _norm(value)
        if v:
            self.value = v
            self._show()

    def _show(self):
        self.swatch.setIcon(_swatch(self.value))
        self.code.setText(self.value.upper())

    def _typed(self):
        v = _norm(self.code.text())
        if v is None:
            self._show()                              # not a colour: back to the last good one
            return
        if v != self.value:
            self.value = v
            self._show()
            self.changed.emit(v)

    def _pick(self):
        """Colour palette; the preview follows while choosing, Cancel puts the old colour back."""
        old = self.value
        dlg = QColorDialog(QColor(old), self)
        dlg.setWindowTitle(self.title)
        for i, c in enumerate(SWATCHES):
            QColorDialog.setCustomColor(i, QColor(c))
        dlg.currentColorChanged.connect(lambda c: self.changed.emit(c.name()))
        new = dlg.selectedColor().name() if dlg.exec() else old
        self.value = new
        self._show()
        self.changed.emit(new)


def _preset_icon(text_c, edge_c, edge, box, w=46, h=30) -> QIcon:
    pm = QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 5, 5)
    p.setClipPath(clip)
    p.fillRect(QRectF(0, 0, w, h), QColor("#5b6472"))          # a mid tone, like a video frame
    if box:
        c = QColor(box[0])
        c.setAlpha(int(255 * box[1] / 100))
        p.fillRect(QRectF(0, 0, w, h), c)
    f = QFont("Segoe UI")
    f.setPixelSize(14)
    f.setBold(True)
    path = QPainterPath()
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(f)
    tw = fm.horizontalAdvance("Aa")
    path.addText((w - tw) / 2, h / 2 + fm.ascent() / 2 - 2, f, "Aa")
    width = {"none": 0, "thin": 1.4, "normal": 2.2, "thick": 3.4}.get(edge, 2.2)
    if width:
        p.strokePath(path, QPen(QColor(edge_c), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, QColor(text_c))
    p.end()
    return QIcon(pm)


class StylePanel(QWidget):
    changed = Signal()              # the preview must be drawn again
    sample_requested = Signal()     # show a sample line on the preview
    area_requested = Signal()       # draw the box on the video
    reset_requested = Signal()      # the box back on the drawn area (Reset)
    line_back = Signal()            # a line was chosen on the timeline (its words shown here): done with it
    line_text_changed = Signal(str, str)    # "translation" | "original", the new text of the chosen line

    def __init__(self):
        super().__init__()
        s = settings()
        self.region = None
        self._quiet = False
        self.video_h = 1080                # the size is shown in pixels of the current video

        # ---------------------------------------------------------------- presets
        pg = QGridLayout()
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setSpacing(6)
        for i, (name, tc, ec, edge, box) in enumerate(PRESETS):
            b = QToolButton()
            b.setIcon(_preset_icon(tc, ec, edge, box))
            b.setIconSize(QSize(46, 30))
            b.setFixedSize(54, 38)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(name)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _c=False, i=i: self._preset(i))
            pg.addWidget(b, i // 6, i % 6)
        self.presets = Card("grid", "Presets", _card_body(pg), switch=False)

        # ---------------------------------------------------------------- text
        self.font = button("", self._pick_font)
        self.font.setToolTip("Font: choose from the fonts installed on this PC")
        self._font_button()
        self.size = _spin(8, 400, self._px(), 1, "", tip="Text height in pixels of this video (other videos keep "
                          "the same proportion)", width=64)
        self.bold = QToolButton()
        self.bold.setText("B")
        self.bold.setCheckable(True)
        self.bold.setChecked(bool(s["sub_bold"]))
        self.bold.setToolTip("Bold")
        self.bold.setFixedSize(32, 30)
        self.bold.setStyleSheet("QToolButton { font-weight: 700; }")
        self.color = ColorField(s["sub_color"], "Text colour")
        self.outline = QComboBox()
        for k, t in OUTLINES:
            self.outline.addItem(t, k)
        self.outline.setCurrentIndex(max(0, self.outline.findData(s["sub_outline_size"])))
        self.outline.setToolTip("Outline around the letters")
        self.outline_color = ColorField(s["sub_outline"], "Outline colour")
        tg = _grid()
        tg.addWidget(_name("Font"), 0, 0)
        tg.addWidget(self.font, 0, 1)
        tg.addWidget(_name("Text size"), 1, 0)
        tg.addLayout(_hbox(self.size, _unit("px"), 6, self.bold, None), 1, 1)
        tg.addWidget(_name("Colour"), 2, 0)
        tg.addWidget(self.color, 2, 1)
        tg.addWidget(_name("Outline"), 3, 0)
        tg.addWidget(self.outline, 3, 1)
        tg.addWidget(_name("Outline colour"), 4, 0)
        tg.addWidget(self.outline_color, 4, 1)
        self.text_card = Card("font", "Text", _card_body(tg), switch=False)

        # ---------------------------------------------------------------- layout
        self.words = _spin(3, 20, int(s["sub_max_words"]), 1, "", tip="Words on one line at most (Chinese, "
                           "Japanese: twice as many characters). Used for new subtitles and the Fit button", width=60)
        self.lines = _spin(1, 3, int(s["sub_max_lines"]), 1, "", tip="Lines in one subtitle at most. 2 lets a "
                           "long line break in two", width=60)
        self.longest = _spin(2, 15, float(s["sub_max_duration"]), 0.5, "", 1,
                             "A subtitle stays on screen at most this long: longer speech becomes more "
                             "subtitles (speech recognition)", 60)
        self.b_center = button("Reset", self._center)
        self.b_center.setToolTip("Put the lines back in the middle near the bottom, and the box back on the "
                                 "area that was drawn")
        lg = _grid()
        lg.addWidget(_name("Words / line"), 0, 0)
        lg.addLayout(_hbox(self.words, _name("Lines / subtitle"), self.lines, None, spacing=10), 0, 1)
        lg.addWidget(_name("Longest on screen", self.longest.toolTip()), 1, 0)
        lg.addLayout(_hbox(self.longest, _unit("s"), None), 1, 1)
        lg.addWidget(_name("Position"), 2, 0)
        lg.addLayout(_hbox(self.b_center, None), 2, 1)
        self.layout_card = Card("wrap", "Layout", _card_body(lg), switch=False)

        # ---------------------------------------------------------------- box
        self.b_draw = IconButton("area", "Draw the box on the video (OCR reads hard subtitles from this same area)")
        self.b_draw.clicked.connect(self.area_requested.emit)
        self.always = QCheckBox("Always")
        self.always.setToolTip("Keep the box on screen the whole video, not only while a line is shown")
        self.always.setChecked(bool(s["sub_box_always"]))
        self.box_color = ColorField(s["sub_box_color"], "Box colour")
        self.opacity = _spin(0, 100, int(s["ocr_box_opacity"]), 5, "", tip="How strongly the box colour "
                             "covers the picture", width=60)
        self.blur = _spin(0, 40, int(s["ocr_box_blur"]), 1, "", tip="How blurred the picture under the box is",
                          width=60)
        self.box_hint = _name("")
        self.box_hint.setWordWrap(True)
        bg = _grid()
        bg.addWidget(_name("Area"), 0, 0)
        bg.addLayout(_hbox(self.b_draw, self.always, None), 0, 1)
        bg.addWidget(_name("Colour"), 1, 0)
        bg.addWidget(self.box_color, 1, 1)
        bg.addWidget(_name("Opacity"), 2, 0)
        bg.addLayout(_hbox(self.opacity, _unit("%"), None), 2, 1)
        bg.addWidget(_name("Blur"), 3, 0)
        bg.addLayout(_hbox(self.blur, _unit("px"), None), 3, 1)
        self.box_hint.setVisible(False)
        self.box_card = Card("area", "Box", _card_body(bg), checked=bool(s["sub_box"]))
        self.box_card.setToolTip("Show the subtitles inside a box you draw on the video")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(section("Style"))                # hidden by the window (the tab says it)
        self.line_bar = QWidget()                      # a line chosen on the timeline: Style is for it only
        self.line_bar.setObjectName("Card")
        lb = QHBoxLayout(self.line_bar)
        lb.setContentsMargins(12, 8, 8, 8)
        lb.setSpacing(6)
        self.line_text = QLabel("")
        self.line_text.setWordWrap(True)
        self.line_text.setStyleSheet("font-weight: 600;")
        self.b_line_back = IconButton("cross", "Done with this line (Esc)", size=16)
        self.b_line_back.clicked.connect(self.line_back.emit)
        lb.addWidget(self.line_text, 1)
        lb.addWidget(self.b_line_back)
        self.line_bar.setVisible(False)
        self.line_mode = None
        lay.addWidget(self.line_bar)
        # the chosen line's words (above the presets): edited here, saved as they are typed
        self.t_tr = QPlainTextEdit()
        self.t_src = QPlainTextEdit()
        for ed, ph in ((self.t_tr, "Translation"), (self.t_src, "Original")):
            ed.setPlaceholderText(ph)
            ed.setFixedHeight(54)
            ed.setTabChangesFocus(True)
        tx = QVBoxLayout()
        tx.setContentsMargins(0, 0, 0, 0)
        tx.setSpacing(6)
        self.t_tr_l, self.t_src_l = _name("Translation"), _name("Original")
        for w in (self.t_tr_l, self.t_tr, self.t_src_l, self.t_src):
            tx.addWidget(w)
        self.text_edit_card = Card("edit", "Line text", _card_body(tx), switch=False)
        self.text_edit_card.setVisible(False)
        lay.addWidget(self.text_edit_card)
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(600)
        self._text_timer.timeout.connect(self._text_save)
        self._text_loading = False
        self.t_tr.textChanged.connect(lambda: None if self._text_loading else self._text_timer.start())
        self.t_src.textChanged.connect(lambda: None if self._text_loading else self._text_timer.start())
        for card in (self.presets, self.text_card, self.layout_card, self.box_card):
            lay.addWidget(card)

        def changed(**kw):
            if self._quiet:
                return
            settings().update(**kw)
            self._rows()
            self.changed.emit()
            self.sample_requested.emit()
        self._changed = changed
        self.bold.toggled.connect(lambda v: changed(sub_bold=v))
        self.size.valueChanged.connect(lambda v: changed(sub_size=round(v / self.video_h * 100, 3)))
        self.color.changed.connect(lambda v: changed(sub_color=v))
        self.outline.currentIndexChanged.connect(lambda: changed(sub_outline_size=self.outline.currentData()))
        self.outline_color.changed.connect(lambda v: changed(sub_outline=v))
        self.words.valueChanged.connect(lambda v: changed(sub_max_words=int(v)))
        self.lines.valueChanged.connect(lambda v: changed(sub_max_lines=int(v)))
        self.longest.valueChanged.connect(lambda v: changed(sub_max_duration=float(v)))
        self.box_card.toggled.connect(self._box_toggled)
        self.always.toggled.connect(lambda v: changed(sub_box_always=v))
        self.box_color.changed.connect(lambda v: changed(sub_box_color=v))
        self.opacity.valueChanged.connect(lambda v: changed(ocr_box_opacity=int(v)))
        self.blur.valueChanged.connect(lambda v: changed(ocr_box_blur=int(v)))
        self._rows()

    # ------------------------------------------------------------ state
    def refresh(self):
        """Settings changed elsewhere (a preset, the lines dragged on the video): show them."""
        s = self._s()
        self._quiet = True
        try:
            self.size.setValue(self._px())
            self.bold.setChecked(bool(s["sub_bold"]))
            self.color.set_value(s["sub_color"])
            self.outline.setCurrentIndex(max(0, self.outline.findData(s["sub_outline_size"])))
            self.outline_color.set_value(s["sub_outline"])
            self.box_card.set_on(bool(s["sub_box"]))
            self.box_color.set_value(s["sub_box_color"])
            self.opacity.setValue(int(s["ocr_box_opacity"]))
            self.blur.setValue(int(s["ocr_box_blur"]))
            self.always.setChecked(bool(s["sub_box_always"]))
            self.words.setValue(int(s["sub_max_words"]))
            self.lines.setValue(int(s["sub_max_lines"]))
            self.longest.setValue(float(s["sub_max_duration"]))
            self._font_button()
        finally:
            self._quiet = False
        self._rows()

    def _s(self):
        """What the controls show: the project's look (one for all its videos)."""
        return settings()

    def set_line_mode(self, number, look=None, texts=None):
        """number: the line chosen on the timeline (1-based), or None. Its words are shown above the presets
        to be edited; the look stays the project's. (look: not used, kept for callers.)"""
        self.line_mode = number
        self.line_bar.setVisible(number is not None)
        if number is not None:
            self.line_text.setText(tr("Line {a}").replace("{a}", str(number)))
        self.text_edit_card.setVisible(number is not None and texts is not None)
        if texts is not None:
            self._text_loading = True
            src, trans = texts
            self.t_src.setPlainText(src or "")
            self.t_tr.setPlainText(trans or "")
            self.t_tr.setVisible(trans is not None)
            self.t_tr_l.setVisible(trans is not None)
            self._has_tr = trans is not None
            self._text_loading = False

    def _text_save(self):
        if self.line_mode is None:
            return
        if getattr(self, "_has_tr", False):
            self.line_text_changed.emit("translation", self.t_tr.toPlainText())
        self.line_text_changed.emit("original", self.t_src.toPlainText())

    def _px(self) -> int:
        return int(round(float(self._s()["sub_size"]) * self.video_h / 100))

    def set_video_height(self, height):
        """Another video: the same look, shown in its pixels."""
        self.video_h = int(height) if height else 1080
        self._quiet = True
        self.size.setValue(self._px())
        self._quiet = False
        self._rows()

    def set_area_state(self, region):
        self.region = region
        self._rows()

    def _rows(self):
        s = self._s()
        on = self.box_card.is_on()
        self.b_draw.setToolTip("Draw the box on the video (OCR reads hard subtitles from this same area)")
        self.box_hint.setText("Drag the box on the video to move it" if on else "")
        self.text_card.set_summary(f"{s['sub_font'] or 'Arial'} · {self._px()} px")
        self.layout_card.set_summary(f"{int(s['sub_max_words'])} · {int(s['sub_max_lines'])} · "
                                     f"{float(s['sub_max_duration']):g} s")
        self.box_card.set_summary(f"{int(s['ocr_box_opacity'])}% · {int(s['ocr_box_blur'])} px" if on else "")

    def _box_toggled(self, on):
        self._changed(sub_box=on)

    def _center(self):
        self.reset_requested.emit()                    # the window puts the box back on the drawn area
        self._changed(sub_pos_x=0.5, sub_margin=6.0)

    def _preset(self, i):
        _name_, tc, ec, edge, box = PRESETS[i]
        kw = {"sub_color": tc, "sub_outline": ec, "sub_outline_size": edge}
        if box:
            kw.update(sub_box=True, sub_box_color=box[0], ocr_box_opacity=box[1])
        settings().update(**kw)
        self.refresh()
        self.changed.emit()
        self.sample_requested.emit()

    # ------------------------------------------------------------ font
    def _font_button(self):
        fam = self._s()["sub_font"] or "Arial"
        self.font.setText(fam)
        self.font.setStyleSheet('font-family: "%s"; text-align: left; padding-left: 10px;' % fam.replace('"', ""))

    @wait_cursor()
    def _pick_font(self):
        from .font_picker import FontPopup
        self._popup = FontPopup(self._s()["sub_font"] or "Arial", self)

        def chosen(family):
            settings().update(sub_font=family)
            self._font_button()
            self._rows()
            self.changed.emit()
            self.sample_requested.emit()
        self._popup.picked.connect(chosen)
        self._popup.popup_under(self.font)
