"""Translation styles editor: the styles on the left (double-click one to rename it), its guide on the right.
Nothing is written until Save; closing without saving asks first."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QVBoxLayout)

from ..i18n import Keep, tr
from ..translate import styles
from .widgets import IconButton, button, row

TEMPLATE = """Bối cảnh:

Xưng hô:
-

Giọng văn:

Thuật ngữ (gốc = bản dịch):
"""


def _shown(st) -> str:
    return st["name"] if st["renamed"] else tr(st["name"])


class StylesDialog(QDialog):
    def __init__(self, parent=None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Translation styles")
        self.resize(900, 580)
        self.work = copy.deepcopy(styles.all_styles())       # edited here, written on Save
        self.removed = []                                     # ids: user styles to delete / shipped to restore
        self.dirty = False
        self.cur = None
        self._loading = False
        self._new_n = 0

        self.list = QListWidget()
        self.list.setFixedWidth(250)
        self.list.setEditTriggers(QListWidget.DoubleClicked | QListWidget.EditKeyPressed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.b_new = button("New style", self._new, icon="add")
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.list, 1)
        left.addWidget(self.b_new)

        self.text = QPlainTextEdit()
        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.Monospace)
        mono.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        mono.setPointSize(10)
        self.text.setFont(mono)
        self.b_delete = IconButton("delete", "Delete this style", size=20)
        self.b_delete.clicked.connect(self._delete)
        self.b_save = button("Save", self._save, primary=True)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.text, 1)
        right.addWidget(row(self.b_delete, None, self.b_save))

        lay = QHBoxLayout(self)
        lay.setSpacing(14)
        lay.addLayout(left)
        lay.addLayout(right, 1)

        self.text.textChanged.connect(self._typed)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._renamed)
        self._fill(current)

    # ------------------------------------------------------------ list
    def _style(self, sid):
        return next((s for s in self.work if s["id"] == sid), None)

    def _fill(self, select: str = ""):
        self._loading = True
        self.list.clear()
        pick = None
        for st in self.work:
            it = QListWidgetItem(Keep(_shown(st)))
            it.setData(Qt.UserRole, st["id"])
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            it.setToolTip(tr("Built-in style") if st["builtin"] else tr("Your style"))
            self.list.addItem(it)
            if st["id"] == select:
                pick = it
        self._loading = False
        self.list.setCurrentItem(pick or self.list.item(0))

    def _selected(self, it, _prev):
        self.cur = self._style(it.data(Qt.UserRole)) if it is not None else None
        self._loading = True
        self.text.setPlainText(self.cur["text"] if self.cur else "")
        self._loading = False
        builtin = bool(self.cur and self.cur["builtin"])
        self.b_delete.setEnabled(not builtin)
        self.b_delete.setToolTip(tr("Built-in styles cannot be deleted (right-click one to restore its text)")
                                 if builtin else tr("Delete this style"))

    def _typed(self):
        if self._loading or self.cur is None:
            return
        self.cur["text"] = self.text.toPlainText()
        self.dirty = True

    def _renamed(self, it):
        if self._loading:
            return
        st = self._style(it.data(Qt.UserRole))
        name = it.text().strip()
        if not name or name == _shown(st):
            self._loading = True
            it.setText(Keep(_shown(st)))
            self._loading = False
            return
        st["name"], st["renamed"] = name, True
        self.dirty = True

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        st = self._style(it.data(Qt.UserRole))
        m = QMenu(self)
        m.addAction("Rename", lambda: self.list.editItem(it))
        if st["builtin"]:
            m.addAction("Restore FreeSub's text", lambda: self._restore(st))
        else:
            m.addAction("Delete", self._delete)
        m.exec(self.list.mapToGlobal(pos))

    # ------------------------------------------------------------ add / delete / restore
    def _new(self):
        self._new_n += 1
        st = {"id": f"new:{self._new_n}", "name": tr("New style"), "text": TEMPLATE, "builtin": False,
              "edited": False, "renamed": True}
        self.work.append(st)
        self.dirty = True
        self._fill(st["id"])
        it = self.list.currentItem()
        if it is not None:
            self.list.editItem(it)                      # type its name first

    def _delete(self):
        st = self.cur
        if st is None or st["builtin"]:
            return
        if QMessageBox.question(self, "FreeSub", Keep(tr("Delete the style \"{a}\"?").replace(
                "{a}", _shown(st)))) != QMessageBox.Yes:
            return
        self.work.remove(st)
        if not st["id"].startswith("new:"):
            self.removed.append(st["id"])
        self.dirty = True
        self._fill()

    def _restore(self, st):
        st["name"] = styles.BUILTIN_NAMES[st["id"]]
        st["text"] = styles.shipped_text(st["id"])
        st["renamed"] = False
        self.removed.append(st["id"])                   # a shipped style "removed" = back to its own text
        self.dirty = True
        self._fill(st["id"])

    # ------------------------------------------------------------ save / close
    def _save(self):
        for sid in self.removed:
            styles.remove(sid)
        before = {s["id"]: s for s in styles.all_styles()}
        self.saved_new = []
        for st in self.work:
            if st["id"].startswith("new:"):
                self.saved_new.append(styles.add(st["name"], st["text"]))
                continue
            old = before.get(st["id"])
            if old is None:
                continue
            name = st["name"] if st["renamed"] and st["name"] != _shown(old) else None
            text = st["text"] if st["text"] != old["text"] else None
            if name is not None or text is not None:
                styles.update(st["id"], name=name, text=text)
        self.dirty = False
        self.accept()

    def reject(self):
        if self.dirty and QMessageBox.question(self, "FreeSub", "Close without saving your changes?") \
                != QMessageBox.Yes:
            return
        super().reject()
