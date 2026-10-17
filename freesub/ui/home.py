"""Projects screen (shown when FreeSub opens): every video worked on before, newest first, kept until deleted.
Click a project to open its video and subtitles."""
import os
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from ..i18n import tr
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QVBoxLayout, QWidget)

from ..config import settings
from ..core import ffmpeg
from ..core.project import Project, delete_project, list_projects, output_dir_for
from ..core.subtitle import fmt_clock
from . import icons, theme
from .queue import open_folder
from .widgets import button, label, row

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


class Home(QWidget):
    open_requested = Signal(list)     # videos to open
    deleted = Signal(str)             # video whose project was deleted

    def __init__(self):
        super().__init__()
        self.sig = _Sig()
        self.sig.thumb.connect(self._thumb_ready)
        self.items = {}
        title = label("Projects", name="PanelTitle")
        self.count = label("", muted=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search projects")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.b_new = button("New project", self.new_project, primary=True)
        self.b_folder = button("Add a folder", self.new_folder)
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
        self.empty = label("No projects yet.\nDrop videos anywhere in this window, or press New project.",
                           muted=True, wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("font-size: 15px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(row(title, self.count, None, self.search, self.b_folder, self.b_new))
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty, 1)
        self.list.itemClicked.connect(self._open_item)
        self.list.customContextMenuRequested.connect(self._menu)
        self.search.textChanged.connect(self._filter)
        icons.track(self)

    # ------------------------------------------------------------ list
    def refresh(self):
        self.list.clear()
        self.items = {}
        projects = list_projects()
        need = []
        for p in projects:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, p)
            meta = [tr(p["status"])]
            if p["duration"]:
                meta.append(fmt_clock(p["duration"]).split(".")[0])
            when = _when(p["opened"])
            text = p["name"] + "\n" + " · ".join(meta) + ("\n" + when if when else "")
            if p["missing"]:
                text = p["name"] + "\n" + tr("Video not found")
                it.setForeground(QColor(theme.colors()["faint"]))
            it.setText(text)
            it.setToolTip(p["video"])
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            it.setIcon(QIcon(self._picture(p["thumb"], p["missing"])))
            self.list.addItem(it)
            self.items[p["folder"]] = it
            if not p["thumb"] and not p["missing"]:
                need.append(p)
        n = len(projects)
        self.count.setText(f"{n} project{'s' if n != 1 else ''}" if n else "")
        self.list.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        self._filter(self.search.text())
        if need:
            threading.Thread(target=self._make_thumbs, args=(need,), daemon=True).start()

    def refresh_icon(self):          # theme changed: draw the pictures again (the list itself is unchanged)
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
                path = Project(d["video"], folder=d["folder"]).make_thumb()
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
        if d["missing"]:
            QMessageBox.information(self, "FreeSub", f"The video is not there any more:\n{d['video']}\n\n"
                                                     "Put it back, or delete this project (right-click).")
            return
        self.open_requested.emit([d["video"]])

    def new_project(self):
        exts = " ".join("*" + e for e in ffmpeg.MEDIA_EXTS)
        files, _ = QFileDialog.getOpenFileNames(self, "New project: choose videos", settings()["last_open_dir"],
                                                f"Videos and audio ({exts});;All files (*.*)")
        if files:
            settings().update(last_open_dir=os.path.dirname(files[0]))
            self.open_requested.emit(files)

    def new_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add every video in a folder", settings()["last_open_dir"])
        if d:
            settings().update(last_open_dir=d)
            self.open_requested.emit([d])

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
        if not d["missing"]:
            m.addAction("Open", lambda: self.open_requested.emit([x["video"] for x in sel if not x["missing"]]))
        out = output_dir_for(d["video"])
        if os.path.isdir(out):
            m.addAction("Open the output folder", lambda: open_folder(out))
        m.addSeparator()
        m.addAction("Delete project" if len(sel) == 1 else f"Delete {len(sel)} projects", lambda: self.delete(sel))
        m.exec(self.list.mapToGlobal(pos))

    def delete(self, sel):
        names = "\n".join(x["name"] for x in sel[:8]) + ("\n…" if len(sel) > 8 else "")
        r = QMessageBox.question(self, "FreeSub", f"Delete these projects?\n\n{names}\n\nTheir work files go. "
                                                  "The videos, .srt files and exported videos are kept.")
        if r != QMessageBox.Yes:
            return
        for x in sel:
            delete_project(x["folder"])
            self.deleted.emit(x["video"])
        self.refresh()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and self.list.selectedItems():
            self.delete([i.data(Qt.UserRole) for i in self.list.selectedItems()])
            return
        super().keyPressEvent(e)
