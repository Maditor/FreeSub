"""Name lists editor: the lists on the left (double-click one to rename it), its names on the right in two
columns, the original and the translation. Nothing is written until Save; closing without saving asks first."""
import copy
import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..i18n import Keep, tr
from ..translate import names
from .widgets import IconButton, button, row

log = logging.getLogger(__name__)
KIND_TEXT = {"person": "Person", "place": "Place", "group": "Group", "term": "Term"}


class _Done(QObject):
    done = Signal(object, str)            # (found list or None, error text)


class NamesDialog(QDialog):
    def __init__(self, parent=None, current: str = "", source=None):
        """source(): (original lines, translated lines, spoken language, video name) of the video open in the
        window, for "Find names in the subtitles" (None: no video)."""
        super().__init__(parent)
        self.source = source
        self._sig = _Done()
        self._sig.done.connect(self._found)
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
        self.table.setHorizontalHeaderLabels([tr("Original"), tr("Translation")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.table.horizontalHeaderItem(0).setToolTip(tr("The right spelling first; other ways it is heard after a "
                                                         "comma (齐桦, 奇花) are translated the same way"))
        self.b_delete = IconButton("delete", "Delete this list", size=20)
        self.b_delete.clicked.connect(self._delete)
        self.b_row_del = button("Remove the selected names", self._remove_rows)
        self.b_find = button("Find names in the subtitles", self._find, icon="search")
        self.b_find.setToolTip("The AI (Gemini or Cloudflare) reads the subtitles of the video that is open and "
                               "suggests its names and special words, with a translation. You choose which to add.")
        self.b_save = button("Save", self._save, primary=True)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.table, 1)
        right.addWidget(row(self.b_delete, self.b_row_del, self.b_find, None, self.b_save))

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
        for a, b in sorted(self.cur["pairs"] if self.cur else [], key=names.sort_key):    # A to Z
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

    # ------------------------------------------------------------ names found in the subtitles
    def _find(self):
        from ..config import settings
        from ..translate import find_names
        got = self.source() if self.source else None
        if not got or not any(t.strip() for t in got[0]):
            QMessageBox.information(self, "FreeSub", "Open a video that has subtitles first.")
            return
        try:
            find_names.service()
        except find_names.NoAI as e:
            QMessageBox.information(self, "FreeSub", tr(str(e)))
            return
        lines, tr_lines, lang, self._video = got
        target = settings()["target_lang"]
        self.b_find.setEnabled(False)
        self.b_find.setText(tr("Finding names…"))
        sig = self._sig

        def work():
            try:
                sig.done.emit(find_names.find(lines, tr_lines, lang, target), "")
            except Exception as e:  # noqa: BLE001
                log.warning("Finding names failed: %s", e)
                sig.done.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _found(self, found, error):
        self.b_find.setEnabled(True)
        self.b_find.setText(tr("Find names in the subtitles"))
        if not self.isVisible():
            return
        if error:
            QMessageBox.warning(self, "FreeSub", tr("Could not find the names: {a}").replace("{a}", error[:300]))
            return
        if not found:
            QMessageBox.information(self, "FreeSub", "No names or special words were found.")
            return
        have = {v for a, _b in (self.cur["pairs"] if self.cur else []) for v in names.variants(a)}
        dlg = FoundDialog(self, found, have)
        if not dlg.exec():
            return
        picked = dlg.picked()
        if not picked:
            return
        if self.cur is None:                              # no list yet: one named after the video
            x = {"id": names.new_id(), "name": self._video or tr("New list"), "pairs": []}
            self.work.append(x)
            self._fill(x["id"])
        self.cur["pairs"] = [p for p in self._rows()] + [[a, b] for a, b in picked
                                                          if names.variants(a)[0] not in have]
        self.dirty = True
        self._selected(self.list.currentItem(), None)

    # ------------------------------------------------------------ save / close
    def _save(self):
        if self.cur is not None:
            self.cur["pairs"] = sorted(self._rows(), key=names.sort_key)
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


class FoundDialog(QDialog):
    """The names the AI found: tick the ones to add, fix the spelling or the translation if needed."""

    def __init__(self, parent, found, have):
        super().__init__(parent)
        self.setWindowTitle("Names found in the subtitles")
        self.resize(820, 520)
        self.table = QTableWidget(len(found), 5)
        self.table.setHorizontalHeaderLabels([tr("Original"), tr("Translation"), tr("Also heard as"), tr("Kind"),
                                              tr("Times")])
        h = self.table.horizontalHeader()
        for c, mode in enumerate((QHeaderView.Stretch, QHeaderView.Stretch, QHeaderView.Stretch,
                                  QHeaderView.ResizeToContents, QHeaderView.ResizeToContents)):
            h.setSectionResizeMode(c, mode)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.SelectedClicked)
        for r, x in enumerate(found):
            first = QTableWidgetItem(Keep(x["original"]))
            first.setFlags(first.flags() | Qt.ItemIsUserCheckable)
            old = x["original"] in have
            first.setCheckState(Qt.Unchecked if old else Qt.Checked)
            if old:
                first.setToolTip(tr("Already in the list"))
            self.table.setItem(r, 0, first)
            self.table.setItem(r, 1, QTableWidgetItem(Keep(x["translation"])))
            self.table.setItem(r, 2, QTableWidgetItem(Keep(", ".join(x["seen"]))))     # editable: fix or clear
            for c, text in ((3, tr(KIND_TEXT.get(x["kind"], "Term"))), (4, str(x["count"]))):
                it = QTableWidgetItem(Keep(text))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.merge = QCheckBox(tr("Translate the \u201calso heard as\u201d spellings the same way (they go into "
                                  "the original: 齐桦, 奇花 = Tề Hoa)"))
        self.merge.setChecked(True)
        b_all = button("Tick all", lambda: self._tick(True))
        b_none = button("Untick all", lambda: self._tick(False))
        b_add = button("Add to the list", self.accept, primary=True)
        b_cancel = button("Cancel", self.reject)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.merge)
        lay.addWidget(row(b_all, b_none, None, b_cancel, b_add))

    def _tick(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def picked(self):
        out = []
        for r in range(self.table.rowCount()):
            a = self.table.item(r, 0)
            if a.checkState() == Qt.Checked and a.text().strip():
                orig = a.text().strip()
                seen = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
                if self.merge.isChecked() and seen.strip():
                    orig = ", ".join(names.variants(orig + ", " + seen))
                out.append([orig, (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()])
        return out
