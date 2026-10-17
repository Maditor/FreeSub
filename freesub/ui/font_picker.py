"""Font picker: a small drop-down under the Font button with a search box and the fonts installed in Windows.

Clicking a font applies it at once (the video preview follows). Fonts without Vietnamese letters are greyed.
"""
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import theme


def installed_fonts() -> list[str]:
    """Font families on this PC that the export can use (TrueType / OpenType; no '@' vertical variants and no
    old bitmap fonts), A-Z."""
    fams = {f for f in QFontDatabase.families() if not f.startswith("@")}
    try:
        from ..core.fonts import usable_names
        ok = usable_names()
    except Exception:  # noqa: BLE001
        ok = set()
    if ok:
        fams = {f for f in fams if f.lower() in ok}
    return sorted(fams, key=str.lower)


def has_vietnamese(family: str) -> bool:
    return QFontDatabase.WritingSystem.Vietnamese in QFontDatabase.writingSystems(family)


class FontPopup(QFrame):
    picked = Signal(str)

    def __init__(self, family: str, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("Card")
        self.setFixedSize(280, 340)
        self.current = family
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search fonts")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        faint = QColor(theme.colors()["faint"])
        for f in installed_fonts():
            it = QListWidgetItem(f)
            it.setFont(QFont(f, 10))            # each name shown in its own font
            if not has_vietnamese(f):
                it.setForeground(faint)
                it.setToolTip("This font may not have Vietnamese letters")
            self.list.addItem(it)
            if f == family:
                self.list.setCurrentItem(it)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._enter)
        self.list.itemClicked.connect(self._choose)
        self.list.itemActivated.connect(self._choose)

    def popup_under(self, widget):
        self.move(widget.mapToGlobal(QPoint(0, widget.height() + 2)))
        self.show()
        if self.list.currentItem():
            self.list.scrollToItem(self.list.currentItem(), QListWidget.PositionAtCenter)
        self.search.setFocus()

    def _filter(self, text):
        q = text.strip().lower()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = bool(q) and q not in it.text().lower()
            it.setHidden(hide)
            if not hide and first is None:
                first = it
        if q and first:
            self.list.setCurrentItem(first)

    def _enter(self):
        it = self.list.currentItem()
        if it and not it.isHidden():
            self._choose(it)

    def _choose(self, it):
        self.picked.emit(it.text())
        self.close()
