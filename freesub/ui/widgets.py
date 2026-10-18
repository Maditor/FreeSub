"""Small shared widgets."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from . import icons, theme


class wait_cursor:
    """The spinning cursor while something takes a moment, so it does not look stuck. Use as
    `with wait_cursor():` or as a decorator. Nested uses are fine."""

    def __enter__(self):
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)      # show it before the work starts
        return self

    def __exit__(self, *_exc):
        from PySide6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        return False

    def __call__(self, fn):
        import functools

        @functools.wraps(fn)
        def run(*a, **kw):
            with wait_cursor():
                return fn(*a, **kw)
        return run


class IconButton(QToolButton):
    """A flat button with one of FreeSub's own vector icons (icons.py) and a tooltip."""

    def __init__(self, icon_key: str, tip: str = "", size: int = 18, checkable=False, parent=None):
        super().__init__(parent)
        self.key = icon_key
        self.px = size
        self.tint = None                        # a theme colour name ("accent"...) instead of the text colour
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)         # Space must reach the player shortcut, not click this
        self.setIconSize(icons.size(size))
        self.setFixedSize(size + 14, size + 12)
        icons.track(self)
        self.refresh_icon()

    def set_icon(self, icon_key: str):
        self.key = icon_key
        self.refresh_icon()

    def refresh_icon(self):
        from . import theme
        self.setIcon(icons.icon(self.key, self.px, color=theme.colors()[self.tint] if self.tint else None))


class CommandButton(QToolButton):
    """Command bar button: icon + text (Fluent command bar). primary = the accent-filled main action."""

    def __init__(self, icon_key: str, text: str, tip: str = "", primary=False, parent=None):
        super().__init__(parent)
        self.key = icon_key
        self.primary = primary
        self.setText(text)
        self.setToolTip(tip or text)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setIconSize(icons.size(16))
        self.setProperty("primary", primary)
        icons.track(self)
        self.refresh_icon()

    def refresh_icon(self):
        c = theme.colors()
        self.setIcon(icons.icon(self.key, 16, color=c["accent_fg"] if self.primary else c["fg"]))


def separator() -> QFrame:
    f = QFrame()
    f.setObjectName("BarSep")
    f.setFixedSize(1, 20)
    return f


def hline() -> QFrame:
    """A thin horizontal dividing line."""
    f = QFrame()
    f.setObjectName("HLine")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedHeight(1)
    return f


def button(text: str, on_click=None, primary=False, icon: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def label(text: str = "", muted=False, wrap=False, name: str | None = None) -> QLabel:
    lb = QLabel(text)
    if muted:
        lb.setProperty("muted", True)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def section(text: str) -> QLabel:
    from ..i18n import tr
    lb = QLabel(tr(text).upper())
    lb.setObjectName("SectionTitle")
    return lb


def row(*widgets, spacing=8, stretch_last=False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def form_row(text: str, widget: QWidget, tip: str = "") -> QWidget:
    lb = QLabel(text)
    lb.setMinimumWidth(110)
    if tip:
        lb.setToolTip(tip)
        widget.setToolTip(tip)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    if isinstance(widget, QComboBox):      # long items must not widen the panel
        widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        widget.setMinimumContentsLength(12)
    return row(lb, widget)


class Collapsible(QWidget):
    """A header you click to show / hide its content."""
    toggled = Signal(bool)

    def __init__(self, title: str, content: QWidget, opened=False):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.head = QToolButton()
        self.head.setText(title)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setIconSize(icons.size(14))
        self.head.setCheckable(True)
        self.head.setChecked(opened)
        self.refresh_icon = lambda: self.head.setIcon(icons.icon(
            "chevron_down" if self.head.isChecked() else "chevron_right", 14, checked_color=theme.colors()["fg"]))
        icons.track(self)
        self.refresh_icon()
        self.head.setStyleSheet("QToolButton { font-weight: 600; padding: 4px 2px; text-align: left; } "
                                "QToolButton:checked { background: transparent; color: palette(window-text); }")
        self.head.setCursor(Qt.PointingHandCursor)
        self.title = title
        self.content = content
        lay.addWidget(self.head)
        lay.addWidget(content)
        content.setVisible(opened)
        self.head.toggled.connect(self._toggle)

    def _toggle(self, on):
        self.content.setVisible(on)
        self.refresh_icon()
        self.toggled.emit(on)


class Switch(QToolButton):
    """A small on/off switch drawn in the theme colours."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

    def paintEvent(self, _e):
        """Windows 11 toggle: an outlined track with a small grey knob (off), a filled accent track (on)."""
        from PySide6.QtGui import QColor, QPainter, QPen
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        if not self.isEnabled():
            p.setOpacity(0.45)
        hover = self.underMouse()
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent_hover"] if hover else c["accent"]))
        else:
            p.setPen(QPen(QColor(c["muted"]), 1))
            p.setBrush(QColor(c["hover"] if hover else c["field"]))
        p.drawRoundedRect(1.5, 1.5, 37, 17, 8.5, 8.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["accent_fg"] if on else c["muted"]))
        r = 6 if hover else 5
        cx = 29 if on else 10
        p.drawEllipse(cx - r, 10 - r, 2 * r, 2 * r)
        p.end()


class Card(QFrame):
    """A recipe step: switch + title + one-line summary in the header, settings below (collapsible).
    When switched off the settings are greyed out."""
    toggled = Signal(bool)

    def __init__(self, icon_key: str, title: str, content: QWidget, checked=True, opened=True, switch=True):
        super().__init__()
        self.setObjectName("Card")
        self.key = icon_key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.title = QLabel(title)
        self.title.setObjectName("CardTitle")
        self.summary = QLabel("")
        self.summary.setProperty("muted", True)
        self.switch = Switch(checked) if switch else None
        self.fold = QToolButton()
        self.fold.setCheckable(True)
        self.fold.setChecked(opened)
        self.fold.setFocusPolicy(Qt.NoFocus)
        self.fold.setCursor(Qt.PointingHandCursor)
        self.fold.setIconSize(icons.size(14))
        self.fold.setStyleSheet("QToolButton:checked { background: transparent; }")
        head.addWidget(self.icon)
        head.addWidget(self.title)
        head.addWidget(self.summary, 1)
        head.addWidget(self.fold)
        if self.switch:
            head.addWidget(self.switch)
        lay.addLayout(head)
        self.content = content
        lay.addWidget(content)
        content.setVisible(opened)
        self.fold.toggled.connect(self._fold)
        if self.switch:
            self.switch.toggled.connect(self._switch)
        icons.track(self)
        self.refresh_icon()
        self._switch(checked, emit=False)

    def refresh_icon(self):
        c = theme.colors()
        on = self.is_on()
        self.icon.setPixmap(icons.pixmap(self.key, c["accent"] if on else c["faint"], 18))
        self.fold.setIcon(icons.icon("chevron_down" if self.fold.isChecked() else "chevron_right", 14,
                                     checked_color=c["muted"], color=c["muted"]))

    def is_on(self) -> bool:
        return self.switch.isChecked() if self.switch else True

    def set_on(self, on: bool):
        if self.switch:
            self.switch.setChecked(on)

    def _fold(self, opened):
        self.content.setVisible(opened)
        self.refresh_icon()

    def _switch(self, on, emit=True):
        self.content.setEnabled(on)
        self.title.setEnabled(on)
        self.refresh_icon()
        if emit:
            self.toggled.emit(on)

    def set_summary(self, text: str):
        self.summary.setText(text)


class _SegRing(QWidget):
    """The accent outline of the chosen segment, drawn on top of the buttons so all four sides show (the
    buttons share their dividing borders, so a border of their own would miss one side)."""

    def __init__(self, seg):
        super().__init__(seg)
        self.seg = seg
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        keys = list(self.seg.buttons)
        key = self.seg.value()
        if key not in self.seg.buttons:
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath, QPen
        from . import theme
        i = keys.index(key)
        g = QRectF(self.seg.buttons[key].geometry())
        if i > 0:
            g.adjust(-1, 0, 0, 0)                  # cover the divider on its left (drawn by the neighbour)
        r = g.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        square = QPainterPath()
        if i > 0:                                  # only the outer ends of the whole control are rounded
            square.addRect(QRectF(r.left(), r.top(), r.width() / 2, r.height()))
        if i < len(keys) - 1:
            square.addRect(QRectF(r.center().x(), r.top(), r.width() / 2, r.height()))
        if not square.isEmpty():
            path = path.united(square)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.colors()["accent"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path.simplified())
        p.end()


class Segmented(QWidget):
    """Two or three choices side by side (like a radio group, but compact)."""
    changed = Signal(str)

    def __init__(self, choices: list[tuple[str, str]], value: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.buttons = {}
        for i, (key, text) in enumerate(choices):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("SegFirst" if i == 0 else "SegLast" if i == len(choices) - 1 else "SegMid")
            b.clicked.connect(lambda _=False, k=key: self.set_value(k, emit=True))
            lay.addWidget(b)
            self.buttons[key] = b
        self.ring = _SegRing(self)
        self.set_value(value)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ring.setGeometry(self.rect())
        self.ring.raise_()

    def value(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "")

    def set_value(self, key, emit=False):
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self.ring.raise_()
        self.ring.update()
        if emit:
            self.changed.emit(key)


class Marquee(QWidget):
    """One line of text in a fixed room: when it is longer, it runs from right to left without end."""
    GAP = 48            # px between the end of the text and its next start
    SPEED = 1           # px per tick

    def __init__(self, width: int = 340, muted=True):
        super().__init__()
        from PySide6.QtCore import QTimer
        self._text = ""
        self._x = 0
        self._muted = muted
        self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._wait = 0

    def text(self) -> str:
        return self._text

    def setText(self, text: str):
        from ..i18n import tr
        text = tr(text or "")
        if text == self._text:
            return
        self._text = text
        self._x = 0
        self._wait = 60                     # stays still a moment first, so the start can be read
        self.setToolTip(text)
        self._update_timer()
        self.update()

    def _long(self) -> bool:
        return self.fontMetrics().horizontalAdvance(self._text) > self.width()

    def _update_timer(self):
        if self._long() and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
            self._x = 0

    def showEvent(self, e):
        super().showEvent(e)
        self._update_timer()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_timer()

    def _tick(self):
        if self._wait > 0:
            self._wait -= 1
            return
        loop = self.fontMetrics().horizontalAdvance(self._text) + self.GAP
        self._x = (self._x + self.SPEED) % loop
        if self._x == 0:
            self._wait = 60                 # back at the start: a pause again
        self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(self.width(), self.fontMetrics().height() + 4)

    def paintEvent(self, _e):
        from PySide6.QtGui import QLinearGradient, QPainter
        p = QPainter(self)
        c = theme.colors()
        p.setPen(QColor(c["muted"] if self._muted else c["fg"]))
        h = self.height()
        if not self._long():
            p.drawText(0, 0, self.width(), h, Qt.AlignVCenter | Qt.AlignLeft, self._text)
            p.end()
            return
        w = self.fontMetrics().horizontalAdvance(self._text)
        x = -self._x
        while x < self.width():
            p.drawText(x, 0, w + 4, h, Qt.AlignVCenter | Qt.AlignLeft, self._text)
            x += w + self.GAP
        # soft ends, so the text fades in and out instead of being cut
        bg = QColor(c["bg"])                    # the command bar's colour
        for left in (True, False):
            g = QLinearGradient(0 if left else self.width(), 0, 18 if left else self.width() - 18, 0)
            g.setColorAt(0, bg)
            clear = QColor(bg)
            clear.setAlpha(0)
            g.setColorAt(1, clear)
            p.fillRect(0 if left else self.width() - 18, 0, 18, h, g)
        p.end()


def confirm(parent, text: str, key: str = "", yes: str = "", danger: bool = False) -> bool:
    """Ask before an important action (Run, deleting...). key: the action's name; "Don't ask again" skips it
    from then on (Settings → General brings every question back). danger: No is the default button."""
    from PySide6.QtWidgets import QCheckBox, QMessageBox
    from ..config import settings
    skip = list(settings().get("confirm_skip") or [])
    if key and (key in skip or "*" in skip):
        return True
    box = QMessageBox(QMessageBox.Question, "FreeSub", text, QMessageBox.Yes | QMessageBox.No, parent)
    if yes:
        box.button(QMessageBox.Yes).setText(yes)
    box.setDefaultButton(QMessageBox.No if danger else QMessageBox.Yes)
    again = None
    if key:
        again = QCheckBox("Don't ask again")
        box.setCheckBox(again)
    ok = box.exec() == QMessageBox.Yes
    if ok and again is not None and again.isChecked():
        settings().update(confirm_skip=skip + [key])
    return ok


from PySide6.QtWidgets import QComboBox as _QComboBox  # noqa: E402


class MenuPicker(_QComboBox):
    """Looks exactly like the other drop-down boxes, but opens a menu (choices + actions like 'New list…').
    setText() shows the chosen one."""

    def __init__(self):
        super().__init__()
        self._menu = None
        self.addItem("")

    def setMenu(self, menu):
        self._menu = menu

    def setText(self, text: str):
        self.setItemText(0, text)

    def text(self) -> str:
        return self.itemText(0)

    def showPopup(self):
        if self._menu is None:
            return
        self._menu.setMinimumWidth(self.width())
        self._menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def wheelEvent(self, e):
        e.ignore()                                     # never changes by scrolling past it
