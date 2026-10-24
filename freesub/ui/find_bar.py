"""Find and replace bar for the subtitle table (the idea and the options come from VisionBox), and the delegate
that paints the matches inside the cells. The bar only shows things and sends signals: the Editor owns the lines."""
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLineEdit, QMenu, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from ..i18n import tr
from . import theme
from .widgets import IconButton, button, label, tick_row


class _Drop(QToolButton):
    """A button that opens a small menu of check boxes. The menu stays open while several are ticked, and the
    button says how many are on, e.g. "Options (1/3)"."""
    changed = Signal()

    def __init__(self, title: str, items):
        super().__init__()
        self.setObjectName("FindDrop")
        self.title = title
        self.setPopupMode(QToolButton.InstantPopup)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.boxes = {}
        menu = QMenu(self)
        for key, text, tip, on in items:
            box = QCheckBox(text)
            box.setChecked(on)
            box.setToolTip(tip)
            box.toggled.connect(self._toggled)
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(12, 5, 12, 5)
            lay.addWidget(tick_row(box))                  # text on the left, box on the right (like Steps)
            act = QWidgetAction(menu)
            act.setDefaultWidget(holder)
            menu.addAction(act)
            self.boxes[key] = box
        self.setMenu(menu)
        self._refresh()

    def _toggled(self, _on):
        self._refresh()
        self.changed.emit()

    def _refresh(self):
        on = sum(1 for b in self.boxes.values() if b.isChecked() and b.isEnabled())
        self.setText(f"{tr(self.title)} ({on}/{len(self.boxes)})")

    def checked(self, key: str) -> bool:
        b = self.boxes[key]
        return b.isChecked() and b.isEnabled()

    def set_enabled(self, key: str, on: bool):
        self.boxes[key].setEnabled(on)
        self._refresh()


class FindBar(QFrame):
    changed = Signal()                  # the text to find, or an option, changed
    step = Signal(int)                  # -1 previous match, +1 next match
    replace_one = Signal()
    replace_all = Signal()
    closed = Signal()
    undo_asked = Signal()               # Ctrl+Z in a box with nothing of its own to undo: undo the last replace
    redo_asked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("FindBar")
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find…")
        self.find.setClearButtonEnabled(True)
        self.find.setToolTip("Text to find (Enter: next, Shift+Enter: previous). Type ^## to find the lines that "
                             "are only punctuation, like ... or ?!")
        self.repl = QLineEdit()
        self.repl.setPlaceholderText("Replace with…")
        self.repl.setClearButtonEnabled(True)
        self.repl.setToolTip("What takes its place (Enter: replace this one). Leave it empty to delete the text found")
        self.count = label("0/0", muted=True)
        self.count.setMinimumWidth(54)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._count_text = "0/0"
        self.b_prev = IconButton("chevron_up", "Previous match (Shift+Enter)")
        self.b_next = IconButton("chevron_down", "Next match (Enter)")
        self.b_close = IconButton("cross", "Close (Esc)")
        self.where = _Drop("Where", [
            ("src", "Original", "Look in the original subtitles", True),
            ("tr", "Translation", "Look in the translation", True)])
        self.options = _Drop("Options", [
            ("whole", "Whole word", "What is found may not be a piece of a longer word", False),
            ("start", "Start with", "Type the beginning of a line (like (nam)): the whole line that starts with it "
                                    "is the match, so one Replace removes it", False),
            ("case", "Match case", "Capital and small letters must be the same", False)])
        self.b_one = button("Replace", lambda: self.replace_one.emit())
        self.b_all = button("Replace all", lambda: self.replace_all.emit(), primary=True)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.find, 1)
        top.addWidget(label("→", muted=True))
        top.addWidget(self.repl, 1)
        top.addSpacing(4)
        top.addWidget(self.count)
        top.addWidget(self.b_prev)
        top.addWidget(self.b_next)
        top.addWidget(self.b_close)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.where)
        bottom.addWidget(self.options)
        bottom.addStretch(1)
        bottom.addWidget(self.b_one)
        bottom.addWidget(self.b_all)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addLayout(bottom)

        self.find.textChanged.connect(lambda _t: self.changed.emit())
        self.where.changed.connect(self.changed.emit)
        self.options.changed.connect(self.changed.emit)
        self.b_prev.clicked.connect(lambda: self.step.emit(-1))
        self.b_next.clicked.connect(lambda: self.step.emit(1))
        self.b_close.clicked.connect(self.closed.emit)
        for box in (self.find, self.repl):
            box.installEventFilter(self)
        self.set_count(-1, 0)

    # ------------------------------------------------------------ what the user asked for
    def needle(self) -> str:
        return self.find.text()

    def replacement(self) -> str:
        return self.repl.text()

    def kinds(self) -> tuple:
        return tuple(k for k in ("src", "tr") if self.where.checked(k))

    def options_on(self) -> tuple:
        """(match case, whole word, start with)"""
        return self.options.checked("case"), self.options.checked("whole"), self.options.checked("start")

    # ------------------------------------------------------------ what the bar shows
    def set_has_translation(self, has: bool):
        self.where.set_enabled("tr", has)

    def set_count(self, at: int, total: int):
        self._count_text = f"{at + 1}/{total}" if total else "0/0"
        self.count.setText(self._count_text)
        for b in (self.b_prev, self.b_next, self.b_one, self.b_all):
            b.setEnabled(total > 0)

    def flash(self, text: str, ms: int = 2600):
        """A short message in the place of the number of matches (e.g. '12 replaced')."""
        self.count.setText(text)
        QTimer.singleShot(ms, lambda: self.count.setText(self._count_text))

    def focus_find(self):
        self.find.setFocus()
        self.find.selectAll()

    # ------------------------------------------------------------ keys in the two boxes
    def eventFilter(self, obj, e):
        if obj in (self.find, self.repl):
            if e.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
                k, mod = e.key(), e.modifiers()
                ctrl, shift = bool(mod & Qt.ControlModifier), bool(mod & Qt.ShiftModifier)
                mine = (k == Qt.Key_Escape or (ctrl and k in (Qt.Key_F, Qt.Key_H)) or
                        (ctrl and k == Qt.Key_Z and not shift and not obj.isUndoAvailable()) or
                        (ctrl and ((k == Qt.Key_Y) or (k == Qt.Key_Z and shift)) and not obj.isRedoAvailable()))
                if e.type() == QEvent.ShortcutOverride:      # the window's own shortcuts must not take these
                    if mine:
                        e.accept()
                        return True
                    return False
                if k in (Qt.Key_Return, Qt.Key_Enter):
                    if obj is self.find:
                        self.step.emit(-1 if shift else 1)
                    else:
                        self.replace_one.emit()
                    return True
                if k == Qt.Key_Escape:
                    self.closed.emit()
                    return True
                if ctrl and k in (Qt.Key_F, Qt.Key_H):
                    self.focus_find()
                    return True
                if mine and ctrl and k == Qt.Key_Z and not shift:
                    self.undo_asked.emit()
                    return True
                if mine and ctrl:
                    self.redo_asked.emit()
                    return True
        return super().eventFilter(obj, e)


class FindDelegate(QStyledItemDelegate):
    """Draws the cell as usual, then a soft colour behind every match (a stronger one on the current match).
    marks: {(row, column): [(start, end, is_current)]} - positions in the text as the cell shows it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.marks = {}

    def paint(self, painter, option, index):
        back = index.data(Qt.BackgroundRole)          # a row colour (lines read late): the stylesheet hides it
        if back is not None:
            painter.fillRect(option.rect.adjusted(0, 0, 0, -1), back)
        super().paint(painter, option, index)
        marks = self.marks.get((index.row(), index.column()))
        if not marks:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        left = rect.left() + style.pixelMetric(QStyle.PM_FocusFrameHMargin, opt, widget) + 1
        text, fm = opt.text, opt.fontMetrics
        accent = QColor(theme.colors()["accent"])
        right = rect.right() - (left - rect.left())
        if fm.horizontalAdvance(text) > right - left:       # the cell cuts the text with "…": nothing on the dots
            right -= fm.horizontalAdvance("…")
        painter.save()
        painter.setClipRect(QRectF(rect.left(), rect.top(), max(0, right - rect.left()), rect.height()))
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        for s, e, current in marks:
            x = left + fm.horizontalAdvance(text[:s])
            if x >= right - 4:
                continue
            w = max(4, fm.horizontalAdvance(text[s:e]))
            box = QRectF(x - 1, rect.top() + 3, w + 2, rect.height() - 6)
            fill = QColor(accent)
            fill.setAlpha(120 if current else 55)
            painter.setBrush(fill)
            painter.setPen(accent if current else Qt.NoPen)
            painter.drawRoundedRect(box, 3, 3)
        painter.restore()
