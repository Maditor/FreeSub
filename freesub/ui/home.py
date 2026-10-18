"""Projects screen (shown when FreeSub opens): every project, newest first, kept until deleted. A project holds
its videos and their timeline. Double-click one to open it (one click selects). Deleted projects wait in the
Trash for 20 days."""
import os
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from ..i18n import tr
from PySide6.QtWidgets import (QAbstractItemView, QInputDialog, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..core import edits
from ..core.project import (TRASH_DAYS, Project, clean_work_root, list_trash, output_dir_for, purge_trash,
                            restore_project)
from ..core.subtitle import fmt_clock
from . import icons, theme
from .queue import open_folder
from .widgets import IconButton, button, label, row, wait_cursor

THUMB = QSize(240, 135)


class _Sig(QObject):
    thumb = Signal(str, str)          # folder, image path


def _when(stamp: str) -> str:
    try:
        d = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""
    days = (datetime.now().date() - d.date()).days
    if days == 0:
        return tr("today {a}").replace("{a}", d.strftime("%H:%M"))
    if days == 1:
        return tr("yesterday")
    if days < 7:
        return tr(f"{days} days ago")
    return d.strftime("%d/%m/%Y")


def _when_ts(ts: float) -> str:
    try:
        return _when(datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"))
    except (ValueError, OSError, OverflowError):
        return ""


class Home(QWidget):
    open_requested = Signal(str)      # project id
    new_requested = Signal()
    deleted = Signal(str, list)       # project id, its videos (the window lets go of them first)

    def __init__(self):
        super().__init__()
        self.sig = _Sig()
        self.sig.thumb.connect(self._thumb_ready)
        self.items = {}
        self.in_trash = False
        self.title = title = label("Projects", name="PanelTitle")
        self.count = label("", muted=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search projects")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.b_new = QPushButton("New project")
        self.b_new.setObjectName("CreateProject")
        self.b_new.setCursor(Qt.PointingHandCursor)
        self.b_new.setMinimumHeight(96)
        self.b_new.clicked.connect(self.new_requested.emit)
        self._style_new()
        self.b_trash = IconButton("delete", f"Trash: deleted projects stay here for {TRASH_DAYS} days", size=20,
                                  checkable=True)
        self.b_trash.toggled.connect(self.show_trash)
        self.b_empty = button("Empty the trash", self.empty_trash)
        self.b_empty.setVisible(False)
        self.list = QListWidget()
        self.list.setObjectName("Projects")
        self.list.setViewMode(QListView.IconMode)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setIconSize(THUMB)
        self.list.setGridSize(QSize(THUMB.width() + 28, THUMB.height() + 78))
        self.list.setSpacing(6)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.empty = label("No projects yet.",
                           muted=True, wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("font-size: 15px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(self.b_new)
        lay.addSpacing(6)
        lay.addWidget(row(title, self.count, None, self.search, self.b_trash, self.b_empty))
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty, 1)
        self.list.itemDoubleClicked.connect(self._open_item)       # one click selects, two open
        self.list.customContextMenuRequested.connect(self._menu)
        self.search.textChanged.connect(self._filter)
        icons.track(self)
        def tidy():
            purge_trash()                                               # older than TRASH_DAYS: gone
            clean_work_root()                                           # half-deleted projects: gone
        threading.Thread(target=tidy, daemon=True).start()

    # ------------------------------------------------------------ list
    def show_trash(self, on: bool):
        self.in_trash = on
        self.title.setText("Trash" if on else "Projects")
        for w in (self.b_new, self.b_trash):
            w.setVisible(not on)                    # the Projects button at the top goes back
        self.b_empty.setVisible(on)
        self.refresh()

    def _refresh_trash(self):
        items = list_trash()
        for p in items:
            it = QListWidgetItem()
            d = dict(p, missing=False, trash=True)
            it.setData(Qt.UserRole, d)
            days = p["days_left"]
            it.setText(p["name"] + "\n" + tr(f"Deleted {_when_ts(p['deleted'])}") + "\n" +
                       (tr(f"Gone in {days} days") if days != 1 else tr("Gone tomorrow")))
            it.setToolTip(p["video"])
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            it.setIcon(QIcon(self._picture(p["thumb"], True)))
            self.list.addItem(it)
            self.items[p["folder"]] = it
        n = len(items)
        self.count.setText(f"{n} project{'s' if n != 1 else ''}" if n else "")
        self.b_empty.setEnabled(n > 0)
        self.empty.setText("The trash is empty.")
        self.list.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        self._filter(self.search.text())

    @wait_cursor()
    def refresh(self):
        self.list.clear()
        self.items = {}
        if self.in_trash:
            self._refresh_trash()
            return
        self.empty.setText("No projects yet.")
        projects = edits.list_edits()
        need = []
        for p in projects:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, p)
            n = len(p["videos"])
            meta = [tr("1 video") if n == 1 else tr(f"{n} videos")]
            if p["duration"]:
                meta.append(fmt_clock(p["duration"]).split(".")[0])
            when = _when(p["opened"])
            text = p["name"] + "\n" + " · ".join(meta) + ("\n" + when if when else "")
            if p["missing"]:
                text = p["name"] + "\n" + tr("Videos not found")
                it.setForeground(QColor(theme.colors()["faint"]))
            it.setText(text)
            it.setToolTip("\n".join(p["videos"]))
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            it.setIcon(QIcon(self._picture(p["thumb"], p["missing"])))
            self.list.addItem(it)
            self.items[p["folder"]] = it
            if not p["thumb"] and p["thumb_of"]:
                need.append(p)
        n = len(projects)
        self.count.setText(f"{n} project{'s' if n != 1 else ''}" if n else "")
        self.list.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        self._filter(self.search.text())
        if need:
            threading.Thread(target=self._make_thumbs, args=(need,), daemon=True).start()

    def _style_new(self):
        """The big New project button: the accent colour, a large plus."""
        c = theme.colors()
        self.b_new.setIcon(QIcon(icons.pixmap("add", c["accent_fg"], 30)))
        self.b_new.setIconSize(QSize(30, 30))
        a = QColor(c["accent"])
        grad = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a.name()}, "
                f"stop:1 {a.lighter(130).name()})")
        self.b_new.setStyleSheet(
            f"QPushButton#CreateProject {{ background: {grad}; color: {c['accent_fg']}; border: none;"
            f" border-radius: 14px; font-size: 22px; font-weight: 600; padding: 0 24px; min-height: 104px; }}"
            f"QPushButton#CreateProject:hover {{ background: {a.lighter(115).name()}; }}"
            f"QPushButton#CreateProject:pressed {{ background: {a.darker(110).name()}; }}")

    def refresh_icon(self):          # theme changed: draw the pictures again (the list itself is unchanged)
        self._style_new()
        for i in range(self.list.count()):
            it = self.list.item(i)
            p = it.data(Qt.UserRole) or {}
            it.setIcon(QIcon(self._picture(p.get("thumb"), p.get("missing"))))
            if p.get("missing"):
                it.setForeground(QColor(theme.colors()["faint"]))

    def _picture(self, path, missing=False) -> QPixmap:
        """The thumbnail fitted in a rounded 16:9 frame (a placeholder until it is made)."""
        c = theme.colors()
        pm = QPixmap(THUMB)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        from PySide6.QtGui import QPainterPath
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, THUMB.width(), THUMB.height(), 10, 10)
        p.setClipPath(clip)
        p.fillRect(pm.rect(), QColor(c["video"]))
        src = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        if not src.isNull():
            s = src.scaled(THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((THUMB.width() - s.width()) // 2, (THUMB.height() - s.height()) // 2, s)
            if missing:
                p.fillRect(pm.rect(), QColor(0, 0, 0, 150))
        else:
            ic = icons.pixmap("film", c["faint"], 40)
            p.drawPixmap((THUMB.width() - 40) // 2, (THUMB.height() - 40) // 2, ic)
        p.end()
        return pm

    def _make_thumbs(self, projects):
        for d in projects:
            try:
                path = Project(d["thumb_of"]).make_thumb()
            except Exception:  # noqa: BLE001
                path = None
            if path:
                self.sig.thumb.emit(d["folder"], path)

    def _thumb_ready(self, folder, path):
        it = self.items.get(folder)
        if it is not None:
            it.setIcon(QIcon(self._picture(path)))

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.data(Qt.UserRole)["name"].lower())

    # ------------------------------------------------------------ actions
    def _open_item(self, it):
        d = it.data(Qt.UserRole)
        if d.get("trash"):
            return                                  # right-click: Restore / Delete now
        self.open_requested.emit(d["id"])

    def rename(self, d):
        name, ok = QInputDialog.getText(self, "Rename project", "Name", text=d["name"])
        if ok and name.strip():
            e = edits.load(d["id"])
            if e is not None:
                e.rename(name)
            self.refresh()

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        if not it.isSelected():
            self.list.clearSelection()
            it.setSelected(True)
        sel = [i.data(Qt.UserRole) for i in self.list.selectedItems()]
        d = it.data(Qt.UserRole)
        m = QMenu(self)
        if d.get("trash"):
            m.addAction("Restore", lambda: self.restore(sel))
            m.addSeparator()
            m.addAction("Delete now", lambda: self.delete_now(sel))
            m.exec(self.list.mapToGlobal(pos))
            return
        m.addAction("Open", lambda: self.open_requested.emit(d["id"]))
        if len(sel) == 1:
            m.addAction("Rename", lambda: self.rename(d))
        out = output_dir_for(d["videos"][0]) if d["videos"] else ""
        if out and os.path.isdir(out):
            m.addAction("Open the output folder", lambda: open_folder(out))
        m.addSeparator()
        m.addAction("Delete project" if len(sel) == 1 else f"Delete {len(sel)} projects", lambda: self.delete(sel))
        m.exec(self.list.mapToGlobal(pos))

    def delete(self, sel):
        """Move to the Trash (no question: it can be undone there for TRASH_DAYS)."""
        if self.in_trash:
            self.delete_now(sel)
            return
        from .widgets import confirm
        names = "\n".join(x["name"] for x in sel[:8]) + ("\n…" if len(sel) > 8 else "")
        if not confirm(self, tr("Move to the Trash?") + "\n\n" + names + "\n\n" +
                       tr(f"(It can be restored for {TRASH_DAYS} days.)"), "trash", tr("Delete"), danger=True):
            return
        for x in sel:
            self.deleted.emit(x["id"], list(x["videos"]))      # the work page lets go of them first
            edits.trash_edit(x["folder"])
        self.refresh()

    def restore(self, sel):
        clash = []
        for x in sel:
            try:
                restore_project(x["folder"])
            except FileExistsError:
                clash.append(x["name"])
            except OSError as e:
                QMessageBox.warning(self, "FreeSub", f"Could not restore {x['name']}:\n{e}")
        if clash:
            QMessageBox.information(self, "FreeSub", "These videos have a newer project, so they stay in the "
                                                     "trash:\n\n" + "\n".join(clash))
        self.refresh()

    def delete_now(self, sel):
        names = "\n".join(x["name"] for x in sel[:8]) + ("\n…" if len(sel) > 8 else "")
        if QMessageBox.question(self, "FreeSub", f"Delete these projects for good?\n\n{names}\n\n"
                                "The videos, .srt files and exported videos are kept.") != QMessageBox.Yes:
            return
        from ..core.project import _rmtree
        for x in sel:
            _rmtree(x["folder"])
        self.refresh()

    def empty_trash(self):
        if QMessageBox.question(self, "FreeSub", "Delete every project in the trash for good?\n\n"
                                "The videos, .srt files and exported videos are kept.") != QMessageBox.Yes:
            return
        purge_trash(everything=True)
        self.refresh()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.list.currentItem() and not self.in_trash:
            self.open_requested.emit(self.list.currentItem().data(Qt.UserRole)["id"])
            return
        if e.key() == Qt.Key_Delete and self.list.selectedItems():
            self.delete([i.data(Qt.UserRole) for i in self.list.selectedItems()])
            return
        super().keyPressEvent(e)
