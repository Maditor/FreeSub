"""Left column (Media): the open project's videos, numbered, in order (drag one to move it, or onto the
timeline), their status, right-click actions."""
import os
import subprocess
import threading

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPixmap
from ..i18n import tr
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QStyle, QVBoxLayout, QWidget)

from ..config import settings
from ..core import ffmpeg
from ..core.project import Project
from ..core.subtitle import fmt_clock
from . import icons, theme
from .widgets import IconButton, hline, label, row, wait_cursor

THUMB = QSize(96, 54)           # grid view: a 16:9 picture of each video, its name under it
CELL = QSize(102, 82)  # two per row in the 250 px column (Qt keeps room for a scroll bar)


def thumb_pixmap(path) -> QPixmap:
    """The video's picture in a rounded 16:9 frame (a film icon until the picture is made)."""
    c = theme.colors()
    ratio = 2.0
    pm = QPixmap(THUMB * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, THUMB.width(), THUMB.height(), 5, 5)
    p.setClipPath(clip)
    p.fillRect(0, 0, THUMB.width(), THUMB.height(), QColor(c["video"]))
    src = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
    if not src.isNull():
        s = src.scaled(THUMB * ratio, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        s.setDevicePixelRatio(ratio)
        w, h = s.width() / ratio, s.height() / ratio
        p.drawPixmap(int((THUMB.width() - w) / 2), int((THUMB.height() - h) / 2), s)
    else:
        ic = icons.pixmap("film", c["faint"], 24)
        p.drawPixmap((THUMB.width() - 24) // 2, (THUMB.height() - 24) // 2, ic)
    p.end()
    return pm


class _ThumbSignal(QObject):
    ready = Signal(str, str)


def open_folder(path: str, select: str | None = None):
    """Open a folder in Explorer (selecting a file in it when given)."""
    os.makedirs(path, exist_ok=True)
    if os.name == "nt" and select and os.path.isfile(select):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(select)])
    elif os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class _MediaList(QListWidget):
    """The list itself: a video dragged inside it moves (a line shows where; its number changes); dragged out,
    it goes onto the timeline (copied, never taken out of here). Files dropped on it from Explorer are added.
    Dragging on an empty spot draws a frame that selects every video it touches."""
    moved = Signal()
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)              # our own line (Qt's does not show in the grid)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionRectVisible(False)            # our own frame (below): it also starts beside a name
        self._drop_row = None
        self._band = None                              # (start point, QRubberBand, selection before)

    # ------------------------------------------------------------ selecting with a frame (as in Explorer)
    def _blank(self, pos) -> bool:
        """Nothing under the mouse: an empty spot, or (list view) to the right of a video's name."""
        it = self.itemAt(pos)
        if it is None:
            return True
        if self.viewMode() == QListView.IconMode:
            return False
        r = self.visualItemRect(it)
        fm = self.fontMetrics()
        text_w = max((fm.horizontalAdvance(t) for t in it.text().split("\n")), default=0)
        return pos.x() > r.left() + text_w + 24

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() == Qt.LeftButton and self._blank(pos):
            from PySide6.QtWidgets import QRubberBand
            keep = e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
            before = {self.row(i) for i in self.selectedItems()} if keep else set()
            if not keep:
                self.clearSelection()
            band = QRubberBand(QRubberBand.Rectangle, self.viewport())
            band.setGeometry(pos.x(), pos.y(), 1, 1)
            band.show()
            self._band = (pos, band, before)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._band is not None:
            from PySide6.QtCore import QRect
            start, band, before = self._band
            rect = QRect(start, e.position().toPoint()).normalized()
            band.setGeometry(rect)
            for i in range(self.count()):
                it = self.item(i)
                it.setSelected(i in before or self.visualItemRect(it).intersects(rect))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._band is not None:
            _start, band, _before = self._band
            band.hide()
            band.deleteLater()
            self._band = None
            sel = self.selectedItems()
            if sel and self.currentItem() not in sel:     # the player shows one of them
                self.setCurrentItem(sel[0], QItemSelectionModel.NoUpdate)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    @staticmethod
    def _files(e):
        md = e.mimeData()
        if not md.hasUrls():
            return []
        out = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        return [p for p in out if os.path.isdir(p) or p.lower().endswith(ffmpeg.MEDIA_EXTS)]

    def dragEnterEvent(self, e):
        if e.source() is self:
            e.setDropAction(Qt.MoveAction)
            e.accept()
        elif self._files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()                                # anything else (an .srt): the window takes it

    def dragMoveEvent(self, e):
        if e.source() is self:
            e.setDropAction(Qt.MoveAction)
            e.accept()
            row = self._target_row(e.position().toPoint())
            if row != self._drop_row:
                self._drop_row = row
                self.viewport().update()
        elif self._files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_row is None or not self.count():
            return
        grid = self.viewMode() == QListView.IconMode
        last = self._drop_row >= self.count()
        r = self.visualItemRect(self.item(self.count() - 1 if last else self._drop_row))
        p = QPainter(self.viewport())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.colors()["accent"]))
        if grid:                                       # a bar left (or right) of the cell
            x = r.right() + 1 if last else r.left() - 1
            p.drawRoundedRect(x - 2, r.top() + 2, 4, r.height() - 4, 2, 2)
        else:                                          # a line above (or under) the row
            y = r.bottom() + 1 if last else r.top() - 1
            p.drawRoundedRect(r.left() + 4, y - 2, r.width() - 8, 4, 2, 2)
        p.end()

    def move_selected(self, to: int):
        """Move the selected videos (kept in their order) to row to (counted before they are taken out)."""
        rows = sorted(self.row(i) for i in self.selectedItems())
        if not rows:
            return
        cur = self.currentItem()
        items = [self.takeItem(r) for r in reversed(rows)][::-1]
        to = max(0, min(to - sum(1 for r in rows if r < to), self.count()))
        self.blockSignals(True)
        for k, it in enumerate(items):
            self.insertItem(to + k, it)
            it.setSelected(True)
        if cur is not None:
            self.setCurrentItem(cur)
        self.blockSignals(False)
        self.moved.emit()

    def step_selected(self, way: int):
        rows = sorted(self.row(i) for i in self.selectedItems())
        if not rows:
            return
        if way < 0 and rows[0] > 0:
            self.move_selected(rows[0] - 1)
        elif way > 0 and rows[-1] < self.count() - 1:
            self.move_selected(rows[-1] + 2)

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.AltModifier and e.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            self.step_selected(-1 if e.key() in (Qt.Key_Up, Qt.Key_Left) else 1)    # Alt + arrow: move it
            return
        super().keyPressEvent(e)

    def _target_row(self, pos) -> int:
        it = self.itemAt(pos)
        if it is None:
            return self.count()
        r = self.visualItemRect(it)
        row = self.row(it)
        after = pos.x() > r.center().x() if self.viewMode() == QListView.IconMode else pos.y() > r.center().y()
        return row + 1 if after else row

    def dropEvent(self, e):
        self._drop_row = None
        self.viewport().update()
        if e.source() is not self:
            files = self._files(e)
            if files:
                e.setDropAction(Qt.CopyAction)
                e.accept()
                self.files_dropped.emit(files)
            else:
                e.ignore()
            return
        self.move_selected(self._target_row(e.position().toPoint()))
        e.setDropAction(Qt.CopyAction)                # handled here: Qt must not take anything out
        e.accept()


class Queue(QWidget):
    current_changed = Signal(str)
    imported = Signal(str)
    reset_requested = Signal(str)
    changed = Signal()                  # videos added, removed or moved (the project keeps the list)

    def __init__(self):
        super().__init__()
        self.infos = {}
        self.list = _MediaList()
        self.list.setObjectName("Queue")
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        # the window wires these two (it also has to switch pages after adding)
        self.b_add = IconButton("file_add", "Add video or audio files (or drop them on the window)", size=22)
        self.b_folder = IconButton("folder_add", "Add every video in a folder", size=22)
        self.b_remove = IconButton("delete", "Remove the selected videos from the list", size=20)
        self.b_view = IconButton("grid", "", size=20)
        self.b_view.clicked.connect(lambda: self.set_view("list" if self.view == "grid" else "grid"))
        self.thumbs = {}                       # video -> picture path (made once, in the background)
        self._thumb_sig = _ThumbSignal()
        self._thumb_sig.ready.connect(self._thumb_ready)
        self.view = ""
        self.list.viewport().installEventFilter(self)
        self.empty = label("Drop videos or folders here.", muted=True, wrap=True)
        self.info = label("", muted=True, wrap=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(row(self.b_add, self.b_folder, None, self.b_view, self.b_remove))
        lay.addWidget(hline())
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty)
        lay.addWidget(self.info)
        self.b_remove.clicked.connect(self.remove_selected)
        self.list.moved.connect(self._moved)
        self.list.files_dropped.connect(self.add_paths)
        self.list.currentItemChanged.connect(self._current)
        self.list.customContextMenuRequested.connect(self._menu)
        self.set_view(settings()["queue_view"] if settings()["queue_view"] in ("list", "grid") else "list")
        self._refresh_info()

    # ------------------------------------------------------------ list / grid
    def set_view(self, view):
        """list: name and status on two lines. grid: a picture of each video with its name under it."""
        if view == self.view:
            return
        self.view = view
        settings().update(queue_view=view)
        lw = self.list
        grid = view == "grid"
        lw.setObjectName("QueueGrid" if grid else "Queue")
        lw.setViewMode(QListView.IconMode if grid else QListView.ListMode)
        lw.setFlow(QListView.LeftToRight if grid else QListView.TopToBottom)
        lw.setWrapping(grid)
        lw.setResizeMode(QListView.Adjust)
        lw.setMovement(QListView.Static)
        lw.setDragEnabled(True)                  # (Static switches dragging off) moved here, or onto the timeline
        lw.setAcceptDrops(True)
        lw.setDragDropMode(QAbstractItemView.DragDrop)
        lw.setDefaultDropAction(Qt.CopyAction)
        lw.setIconSize(THUMB if grid else QSize(0, 0))
        lw.setGridSize(CELL if grid else QSize())
        if grid:
            self._fit_grid()
        lw.setSpacing(0)
        lw.setWordWrap(False)
        lw.setTextElideMode(Qt.ElideMiddle)
        lw.setUniformItemSizes(grid)
        lw.style().unpolish(lw)
        lw.style().polish(lw)
        self.b_view.set_icon("list" if grid else "grid")
        self.b_view.setToolTip("Show as a list" if grid else "Show as a grid with pictures")
        self.update_all()

    def _fit_grid(self):
        """Cells share the whole width evenly (as many per row as fit)."""
        vw = self.list.viewport().width()
        room = vw - self.list.style().pixelMetric(QStyle.PM_ScrollBarExtent) - 2
        cols = max(1, room // CELL.width())
        size = QSize(max(CELL.width(), room // cols), CELL.height())
        if self.list.gridSize() != size:
            self.list.setGridSize(size)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize and self.view == "grid":
            self._fit_grid()
        return False

    def _icon_for(self, video):
        path = self.thumbs.get(video)
        if path is None:
            self.thumbs[video] = ""                      # being made
            threading.Thread(target=self._make_thumb, args=(video,), daemon=True).start()
        return QIcon(thumb_pixmap(path))

    def _make_thumb(self, video):
        try:
            path = Project(video).make_thumb()
        except Exception:  # noqa: BLE001
            path = None
        if path:
            self._thumb_sig.ready.emit(video, path)

    def _thumb_ready(self, video, path):
        self.thumbs[video] = path
        if self.view == "grid":
            self.update_item(video=video)

    def refresh_theme(self):
        if self.view == "grid":                   # only the pictures' frames change colour
            for i in range(self.list.count()):
                it = self.list.item(i)
                it.setIcon(QIcon(thumb_pixmap(self.thumbs.get(it.data(Qt.UserRole)))))

    # ------------------------------------------------------------ list
    def videos(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def selected_videos(self):
        sel = [it.data(Qt.UserRole) for it in self.list.selectedItems()]
        return sel or ([self.current()] if self.current() else [])

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def _moved(self):
        self.update_all()                         # the numbers
        self.changed.emit()

    def set_videos(self, videos):
        """The open project's videos (in its order)."""
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.add_paths(list(videos), emit=False)
        self.empty.setVisible(self.list.count() == 0)
        if not self.list.count():
            self.current_changed.emit("")

    def add_paths(self, paths, emit=True) -> int:
        known = set(self.videos())
        found = []
        for p in paths:
            if os.path.isdir(p):
                for root, _d, files in os.walk(p):
                    if os.path.basename(root).startswith("freesub_"):
                        continue                         # our own results
                    found += [os.path.join(root, f) for f in sorted(files)
                              if f.lower().endswith(ffmpeg.MEDIA_EXTS) and ".freesub." not in f.lower()]
            elif os.path.isfile(p) and p.lower().endswith(ffmpeg.MEDIA_EXTS):
                found.append(p)
        first = None
        for p in found:
            p = os.path.abspath(p)
            self.infos.pop(p, None)            # the file may have been replaced: read it again
            if p in known:
                self.update_item(video=p)
                continue
            known.add(p)
            try:
                Project(p).touch()             # it is a project now: it shows on the Projects screen
            except OSError:
                pass
            it = QListWidgetItem()
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.list.addItem(it)
            self.update_item(it)
            first = first or it
        if first and not self.list.currentItem():
            self.list.setCurrentItem(first)
        self.empty.setVisible(self.list.count() == 0)
        if emit and first is not None:
            self.changed.emit()
        return len(found)

    def update_item(self, it=None, video=None):
        if it is None:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == video:
                    it = self.list.item(i)
                    break
        if it is None:
            return
        v = it.data(Qt.UserRole)
        st = tr(Project(v).summary())
        name = f"{self.list.row(it) + 1}. {os.path.basename(v)}"
        if self.view == "grid":
            it.setText(name)
            it.setIcon(self._icon_for(v))
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            it.setToolTip(f"{name}\n{st}" if st else name)
        else:
            it.setText(name + (f"\n{st}" if st else ""))
            it.setIcon(QIcon())
            it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            it.setToolTip(v)
        if v == self.current():
            self._refresh_info()

    def update_all(self):
        for i in range(self.list.count()):
            self.update_item(self.list.item(i))

    def add_files(self):
        exts = " ".join("*" + e for e in ffmpeg.MEDIA_EXTS)
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", settings()["last_open_dir"],
                                                f"Videos and audio ({exts});;All files (*.*)")
        if files:
            settings().update(last_open_dir=os.path.dirname(files[0]))
            self.add_paths(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add every video in a folder", settings()["last_open_dir"])
        if d:
            settings().update(last_open_dir=d)
            if not self.add_paths([d]):
                QMessageBox.information(self, "FreeSub", "No video or audio files in this folder.")

    def remove_selected(self):
        sel = self.list.selectedItems()
        if not sel:
            return
        from .widgets import confirm
        text = (tr("Remove {a} from the project?").replace("{a}", os.path.basename(sel[0].data(Qt.UserRole)))
                if len(sel) == 1 else tr("Remove {a} videos from the project?").replace("{a}", str(len(sel))))
        if not confirm(self, text + "\n" + tr("(The video files and their work are kept.)"), "media_remove",
                       tr("Remove"), danger=True):
            return
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.update_all()
        self.empty.setVisible(self.list.count() == 0)
        self.changed.emit()
        if not self.list.count():
            self.current_changed.emit("")

    # ------------------------------------------------------------ selected video
    def info_for(self, video):
        if video not in self.infos:
            try:
                self.infos[video] = ffmpeg.probe(video)
            except Exception as e:  # noqa: BLE001
                self.infos[video] = {"error": str(e)}
        return self.infos[video]

    def _current(self, cur, _prev):
        self._refresh_info()
        self.current_changed.emit(cur.data(Qt.UserRole) if cur else "")

    def _refresh_info(self):
        v = self.current()
        if not v:
            self.info.setText("")
            return
        inf = self.info_for(v)
        if inf.get("error"):
            self.info.setText(inf["error"])
            return
        parts = [fmt_clock(inf["duration"]).split(".")[0]]
        if inf.get("has_video"):
            parts.append(f"{inf['width']} x {inf['height']}")
        self.info.setText(" · ".join(parts))

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        if not it.isSelected():                        # the menu is for the video under the mouse
            self.list.clearSelection()
            it.setSelected(True)
        v = it.data(Qt.UserRole)
        p = Project(v)
        m = QMenu(self)
        m.addAction("Open the output folder", lambda: open_folder(p.out_dir, p.meta.get("outputs", {}).get("subs.tr.srt")
                                                                  or p.meta.get("outputs", {}).get("subs.src.srt")))
        m.addAction("Use my own .srt as the original", lambda: self.import_srt(v))
        m.addAction("Use my own .srt as the translation", lambda: self.import_srt(v, "translation"))
        m.addSeparator()
        n, row = self.list.count(), self.list.row(it)
        if n > 1:
            mv = m.addMenu("Move")
            mv.addAction("To the top", lambda: self.list.move_selected(0)).setEnabled(row > 0)
            mv.addAction("Up  (Alt+↑)", lambda: self.list.step_selected(-1)).setEnabled(row > 0)
            mv.addAction("Down  (Alt+↓)", lambda: self.list.step_selected(1)).setEnabled(row < n - 1)
            mv.addAction("To the bottom", lambda: self.list.move_selected(n)).setEnabled(row < n - 1)
        m.addAction("Start this video again (delete its work files)", lambda: self.reset_requested.emit(v))
        m.addAction("Remove from the list", self.remove_selected)
        m.exec(self.list.mapToGlobal(pos))

    def import_srt(self, v=None, kind="original"):
        """kind: 'original' (the file replaces the original subtitles; translation and narration follow) or
        'translation' (it becomes the translation, kept as it is)."""
        v = v or self.current()
        if not v:
            return
        title = "Translation for this video" if kind == "translation" else "Subtitles for this video"
        path, _ = QFileDialog.getOpenFileName(self, title, os.path.dirname(v), "Subtitles (*.srt)")
        if not path:
            return
        from ..steps.transcribe import import_srt, import_translation
        try:
            with wait_cursor():
                (import_translation if kind == "translation" else import_srt)(Project(v), path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "FreeSub", f"Could not use that file:\n{e}")
            return
        self.update_item(video=v)
        self.imported.emit(v)
