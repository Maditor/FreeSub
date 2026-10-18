"""Name lists editor: the lists on the left (double-click one to rename it), its names on the right in two
columns, the original and the translation. Nothing is written until Save; closing without saving asks first."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..i18n import Keep, tr
from ..translate import names
from .widgets import IconButton, button, row


class NamesDialog(QDialog):
    def __init__(self, parent=None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Name lists")
        self.resize(860, 560)
        self.work = copy.deepcopy(names.all_lists())        # edited here, written on Save
        self.dirty = False
        self.cur = None
        self._loading = False
        self.picked = current

        self.list = QListWidget()
        self.list.setFixedWidth(230)
        self.list.setEditTriggers(QListWidget.DoubleClicked | QListWidget.EditKeyPressed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.b_new = button("New list", self._new, icon="add")
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.list, 1)
        left.addWidget(self.b_new)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Original"), tr("Translation (empty: keep it)")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.b_delete = IconButton("delete", "Delete this list", size=20)
        self.b_delete.clicked.connect(self._delete)
        self.b_row_del = button("Remove the selected names", self._remove_rows)
        self.b_save = button("Save", self._save, primary=True)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.table, 1)
        right.addWidget(row(self.b_delete, self.b_row_del, None, self.b_save))

        lay = QHBoxLayout(self)
        lay.setSpacing(14)
        lay.addLayout(left)
        lay.addLayout(right, 1)

        self.table.itemChanged.connect(self._typed)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._renamed)
        self._fill(current)

    # ------------------------------------------------------------ lists
    def _get(self, lid):
        return next((x for x in self.work if x["id"] == lid), None)

    def _fill(self, select: str = ""):
        self._loading = True
        self.list.clear()
        pick = None
        for x in self.work:
            it = QListWidgetItem(Keep(x["name"]))
            it.setData(Qt.UserRole, x["id"])
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            self.list.addItem(it)
            if x["id"] == select:
                pick = it
        self._loading = False
        if self.list.count():
            self.list.setCurrentItem(pick or self.list.item(0))
        else:
            self._selected(None, None)

    def _selected(self, it, _prev):
        self.cur = self._get(it.data(Qt.UserRole)) if it is not None else None
        self._loading = True
        self.table.setRowCount(0)
        for a, b in (self.cur["pairs"] if self.cur else []):
            self._add_row(a, b)
        if self.cur is not None:
            self._add_row("", "")                       # an empty row to type the next name in
        self._loading = False
        on = self.cur is not None
        for w in (self.table, self.b_delete, self.b_row_del):
            w.setEnabled(on)

    def _add_row(self, a, b):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(Keep(a)))
        self.table.setItem(r, 1, QTableWidgetItem(Keep(b)))

    def _rows(self):
        out = []
        for r in range(self.table.rowCount()):
            a = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            b = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if a:
                out.append([a, b])
        return out

    def _typed(self, _item):
        if self._loading or self.cur is None:
            return
        self.cur["pairs"] = self._rows()
        self.dirty = True
        last = self.table.rowCount() - 1                  # typing in the empty row: a new empty one under it
        if last < 0 or any((self.table.item(last, c).text() if self.table.item(last, c) else "").strip()
                           for c in (0, 1)):
            self._loading = True
            self._add_row("", "")
            self._loading = False

    def _remove_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows or self.cur is None:
            return
        self._loading = True
        for r in rows:
            if r < self.table.rowCount() - 1:             # the empty last row stays
                self.table.removeRow(r)
        self._loading = False
        self.cur["pairs"] = self._rows()
        self.dirty = True

    def _renamed(self, it):
        if self._loading:
            return
        x = self._get(it.data(Qt.UserRole))
        name = it.text().strip()
        if not name:
            self._loading = True
            it.setText(Keep(x["name"]))
            self._loading = False
            return
        if name != x["name"]:
            x["name"] = name
            self.dirty = True

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        m.addAction("Rename", lambda: self.list.editItem(it))
        m.addAction("Delete", self._delete)
        m.exec(self.list.mapToGlobal(pos))

    def _new(self):
        x = {"id": names.new_id(), "name": tr("New list"), "pairs": []}
        self.work.append(x)
        self.dirty = True
        self._fill(x["id"])
        it = self.list.currentItem()
        if it is not None:
            self.list.editItem(it)                        # type its name first

    def _delete(self):
        x = self.cur
        if x is None:
            return
        if QMessageBox.question(self, "FreeSub", Keep(tr("Delete the list \"{a}\"?").replace("{a}", x["name"]))) \
                != QMessageBox.Yes:
            return
        self.work.remove(x)
        if self.picked == x["id"]:
            self.picked = ""
        self.dirty = True
        self._fill()

    # ------------------------------------------------------------ save / close
    def _save(self):
        if self.cur is not None:
            self.cur["pairs"] = self._rows()
        names.save_all(self.work)
        if self.cur is not None:
            self.picked = self.cur["id"]                  # the list worked on is the one used
        self.dirty = False
        self.accept()

    def reject(self):
        if self.dirty and QMessageBox.question(self, "FreeSub", "Close without saving your changes?") \
                != QMessageBox.Yes:
            return
        super().reject()
