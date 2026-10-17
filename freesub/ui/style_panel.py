"""Style (left column, above Cut): how the subtitles look. Every change shows on the video preview at once
(a sample line appears when no line is on screen) and is exactly what Export burns in.

The box is an area drawn on the video: the subtitles show inside it, over a blurred, darker picture. It is the
same area OCR reads hard subtitles from, so drawing it once serves both.
"""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QLabel, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..config import settings
from .widgets import IconButton, Switch, button, section

COLOURS = [("#ffffff", "White"), ("#ffeb3b", "Yellow"), ("#80deea", "Light blue"), ("#a5d6a7", "Light green"),
           ("#ffab91", "Peach")]
OUTLINES = [("none", "No outline"), ("thin", "Thin outline"), ("normal", "Outline"), ("thick", "Thick outline")]


def _swatch(hex_color: str, size=14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(128, 128, 128))
    p.setBrush(QColor(hex_color))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
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


def _name(text):
    lb = QLabel(text)
    lb.setProperty("muted", True)
    return lb


def _hbox(*items):
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    for w in items:
        if w is None:
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h


class StylePanel(QWidget):
    changed = Signal()              # the preview must be drawn again
    sample_requested = Signal()     # show a sample line on the preview
    area_requested = Signal()       # draw the box on the video

    def __init__(self):
        super().__init__()
        s = settings()
        # --- text
        self.font = button("", self._pick_font)
        self.font.setToolTip("Font: choose from the fonts installed on this PC")
        self._font_button()
        self.size = _spin(2, 15, float(s["sub_size"]), 0.5, " %", 1, "Text height, in % of the video height", 64)
        self.bold = QToolButton()
        self.bold.setText("B")
        self.bold.setCheckable(True)
        self.bold.setChecked(bool(s["sub_bold"]))
        self.bold.setToolTip("Bold")
        self.bold.setFixedSize(30, 30)
        self.bold.setStyleSheet("QToolButton { font-weight: 700; }")
        self.color = QToolButton()
        self.color.setFixedSize(30, 30)
        self.color.setIconSize(QSize(18, 18))
        self.color.setCursor(Qt.PointingHandCursor)
        self.color.clicked.connect(self._pick_colour)
        self._colour_icon(s["sub_color"])
        self.outline = QComboBox()
        for k, t in OUTLINES:
            self.outline.addItem(t, k)
        self.outline.setCurrentIndex(max(0, self.outline.findData(s["sub_outline_size"])))
        self.outline.setToolTip("Outline around the letters")

        # --- box
        self.box = Switch(bool(s["sub_box"]))
        self.box.setToolTip("Show the subtitles inside a box you draw on the video")
        self.b_draw = IconButton("area", "Draw the box on the video (OCR reads hard subtitles from this same area)")
        self.b_draw.clicked.connect(self.area_requested.emit)
        self.always = QCheckBox("Always")
        self.always.setToolTip("Keep the box on screen the whole video, not only while a line is shown")
        self.always.setChecked(bool(s["sub_box_always"]))
        self.dark = _spin(0, 100, int(s["ocr_box_opacity"]), 5, " %", tip="How dark the box is", width=58)
        self.blur = _spin(0, 40, int(s["ocr_box_blur"]), 1, " px", tip="How blurred the picture under the box is",
                          width=58)

        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        g.setColumnMinimumWidth(0, 40)
        g.setColumnStretch(1, 1)
        g.addWidget(_name("Font"), 0, 0)
        g.addWidget(self.font, 0, 1)
        g.addWidget(_name("Text"), 1, 0)
        g.addLayout(_hbox(self.size, self.bold, None, self.color), 1, 1)
        g.addWidget(_name("Edge"), 2, 0)
        g.addWidget(self.outline, 2, 1)
        g.addWidget(_name("Box"), 3, 0)
        g.addLayout(_hbox(self.box, None, self.always, None, self.b_draw), 3, 1)
        self.dark_l, self.blur_l = _name("Dark"), _name("Blur")
        g.addWidget(self.dark_l, 4, 0)
        g.addLayout(_hbox(self.dark, None, self.blur_l, self.blur), 4, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(section("Style"))
        lay.addLayout(g)

        def changed(**kw):
            settings().update(**kw)
            self._rows()
            self.changed.emit()
            self.sample_requested.emit()
        self._changed = changed
        self.bold.toggled.connect(lambda v: changed(sub_bold=v))
        self.size.valueChanged.connect(lambda v: changed(sub_size=v))
        self.outline.currentIndexChanged.connect(lambda: changed(sub_outline_size=self.outline.currentData()))
        self.box.toggled.connect(lambda v: changed(sub_box=v))
        self.always.toggled.connect(lambda v: changed(sub_box_always=v))
        self.dark.valueChanged.connect(lambda v: changed(ocr_box_opacity=v))
        self.blur.valueChanged.connect(lambda v: changed(ocr_box_blur=v))
        self.region = None
        self._rows()

    def _colour_icon(self, hex_color):
        self.color.setIcon(_swatch(hex_color, 18))
        self.color.setToolTip(f"Text colour: {hex_color}")

    def _pick_colour(self):
        """Colour palette; the preview follows while choosing, Cancel puts the old colour back."""
        old = settings()["sub_color"]
        dlg = QColorDialog(QColor(old), self)
        dlg.setWindowTitle("Text colour")
        for i, (k, _t) in enumerate(COLOURS):
            QColorDialog.setCustomColor(i, QColor(k))
        dlg.currentColorChanged.connect(lambda c: self._changed(sub_color=c.name()))
        if dlg.exec():
            new = dlg.selectedColor().name()
        else:
            new = old
        self._colour_icon(new)
        self._changed(sub_color=new)

    def set_area_state(self, region):
        self.region = region
        self._rows()

    def _rows(self):
        on = self.box.isChecked()
        for w in (self.always, self.dark, self.blur, self.dark_l, self.blur_l):
            w.setEnabled(on and bool(self.region))
        self.b_draw.setToolTip(("Draw the box again" if self.region else "Draw the box on the video") +
                               " (OCR reads hard subtitles from this same area)")

    def _font_button(self):
        fam = settings()["sub_font"] or "Arial"
        self.font.setText(fam)
        self.font.setStyleSheet('font-family: "%s"; text-align: left; padding-left: 10px;' % fam.replace('"', ""))

    def _pick_font(self):
        from .font_picker import FontPopup
        self._popup = FontPopup(settings()["sub_font"] or "Arial", self)

        def chosen(family):
            settings().update(sub_font=family)
            self._font_button()
            self.changed.emit()
            self.sample_requested.emit()
        self._popup.picked.connect(chosen)
        self._popup.popup_under(self.font)
