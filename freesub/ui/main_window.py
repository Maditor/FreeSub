"""Main window: videos on the left, the preview with its subtitle table in the middle, the recipe (what
Export does) on the right, progress and log at the bottom. Everything for one video is visible at once."""
import logging
import os
import threading

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION, resource_path, settings
from ..core import ffmpeg, models
from ..core.project import SRC_SRT, TR_SRT, Project
from . import icons, theme
from .editor import Editor
from .home import Home
from .preview import Preview
from .progress_panel import ProgressPanel
from .queue import Queue, open_folder
from .recipe import Recipe, gpu_summary
from .runner import LogBridge, Runner
from . import proxy
from .batch import BatchPage
from .export_dialog import ExportDialog
from .settings_dialog import SettingsDialog
from .style_panel import StylePanel
from ..i18n import tr
from .widgets import CommandButton, IconButton, Marquee, confirm, separator, wait_cursor

log = logging.getLogger(__name__)


def same_file(a, b) -> bool:
    """The same video file, however the path is written (Windows: C:\\ or c:/, any letter case)."""
    if not a or not b:
        return False
    if a == b:
        return True
    norm = lambda p: os.path.normcase(os.path.abspath(p))     # noqa: E731
    return norm(a) == norm(b)


class _Signals(QObject):
    gpu_text = Signal(str)


def _panel(widget, margins=(12, 10, 12, 10)):
    f = QFrame()
    f.setObjectName("Panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.addWidget(widget)
    return f


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        icon = resource_path(os.path.join("assets", "icon.png"))
        if os.path.isfile(icon):
            self.setWindowIcon(QIcon(icon))
        self.setAcceptDrops(True)
        self.resize(1500, 900)
        self.setMinimumSize(1100, 680)
        self.runner = Runner()
        self.proxies = proxy.ProxyMaker(self)
        self.proxies.progress.connect(self._proxy_progress)
        self.proxies.ready.connect(self._proxy_ready)
        self.proxies.failed.connect(self._proxy_failed)
        self._grow = None
        self.sig = _Signals()
        self.last_kind = "export"
        self.edit = None                    # the open project (core/edits.Edit)

        # ---------------- header
        header = QWidget()
        header.setObjectName("Header")
        hl = QHBoxLayout(header)
        header.setObjectName("CommandBar")
        own_bar = not settings()["native_titlebar"]
        hl.setContentsMargins(12, 0, 0, 0) if own_bar else hl.setContentsMargins(12, 6, 10, 4)
        if own_bar:
            header.setFixedHeight(40)
        hl.setSpacing(4)
        logo = QLabel()
        if os.path.isfile(icon):
            logo.setPixmap(QIcon(icon).pixmap(20, 20))
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        self.header_info = Marquee(340)                 # project › video: long names run along
        self.b_home = CommandButton("grid", "Projects", "Every project")
        self.b_batch = CommandButton("stack", "Batch", "The project's videos, one after another (click again to "
                                     "go back to editing)")
        self.c_run = CommandButton("play", "Run", "Make everything switched on in Steps (subtitles, translation, "
                                   "narration) without exporting: then look it over and edit it")
        self.c_export = CommandButton("export", "Export", "Export the video, or only the subtitles (.srt)",
                                      primary=True)
        self.b_theme = IconButton("moon", "Light / dark", size=20)
        self.b_settings = IconButton("settings", "Settings", size=20)
        for b in (self.b_theme, self.b_settings):
            b.setProperty("bar", True)                   # no command-bar padding: the icon keeps its full size
        hl.addWidget(logo)
        hl.addSpacing(4)
        hl.addWidget(title)
        hl.addSpacing(14)
        # these never move (whatever page is open); page-specific commands go on the right
        for w in (self.b_home, self.b_batch):
            hl.addWidget(w)
        hl.addSpacing(12)
        hl.addWidget(self.header_info)
        hl.addStretch(1)

        # ---------------- columns
        self.queue = Queue()
        self.preview = Preview()
        self.style = StylePanel()
        self.home = Home()
        self.editor = Editor()
        self.recipe = Recipe()
        self.progress = ProgressPanel(self.runner)          # what is running: at the bottom of the media column
        for w in (self.c_run, self.c_export, separator(), self.b_theme, self.b_settings):
            hl.addWidget(w)
        self.frameless = None
        if own_bar:                                  # the command bar is the window's title bar
            from .titlebar import Frameless, WindowButtons
            hl.addSpacing(8)
            self.win_buttons = WindowButtons(self)
            hl.addWidget(self.win_buttons, 0, Qt.AlignTop | Qt.AlignRight)
            self.frameless = Frameless(self, header, self.win_buttons)
        self.recipe.set_batch_mode(True)
        pv = QFrame()
        pv.setObjectName("Panel")
        pvl = QVBoxLayout(pv)
        pvl.setContentsMargins(1, 1, 1, 1)
        pvl.addWidget(self.preview)
        from .timeline_view import TimelineView
        self.timeline = TimelineView(lambda v: (self.queue.info_for(v) or {}).get("duration") or 0.0)
        self.editor_panel = _panel(self.editor)            # the subtitle table: under the video
        self.editor_panel.setMinimumHeight(150)
        self.timeline_panel = _panel(self.timeline, (12, 8, 12, 8))     # the timeline: the whole width
        self.timeline_panel.setMinimumHeight(170)
        self.timeline_panel.setVisible(False)
        pv.setMinimumHeight(220)
        self.split_mid = QSplitter(Qt.Vertical)
        self.split_mid.addWidget(pv)
        self.split_mid.addWidget(self.editor_panel)
        self.split_mid.setStretchFactor(0, 2)
        self.split_mid.setStretchFactor(1, 1)
        self.split_mid.setHandleWidth(8)
        self.split_mid.setChildrenCollapsible(False)

        self.split_h = QSplitter(Qt.Horizontal)
        left_box = QWidget()
        ll = QVBoxLayout(left_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(12)
        ll.addWidget(self.queue, 1)
        self.queue.list.setMinimumHeight(90)
        left_scroll = QScrollArea()                 # small windows: the column scrolls instead of squeezing
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_box)
        left_col = QWidget()                        # the media list scrolls; the progress stays at the bottom
        lcl = QVBoxLayout(left_col)
        lcl.setContentsMargins(0, 0, 0, 0)
        lcl.setSpacing(10)
        lcl.addWidget(left_scroll, 1)
        lcl.addWidget(self.progress)
        left = _panel(left_col)
        left.setFixedWidth(250)
        # Inspector (right): the recipe steps, the subtitle style and the cut, as tabs
        self.inspector = QTabWidget()
        self.inspector.setDocumentMode(True)
        self.style.layout().itemAt(0).widget().hide()       # the tab says it already
        style_scroll = QScrollArea()
        style_scroll.setWidgetResizable(True)
        style_scroll.setFrameShape(QFrame.NoFrame)
        style_box = QWidget()
        sbl = QVBoxLayout(style_box)
        sbl.setContentsMargins(4, 12, 4, 4)
        sbl.addWidget(self.style)
        sbl.addStretch(1)
        style_scroll.setWidget(style_box)
        steps_box = QWidget()
        stl = QVBoxLayout(steps_box)
        stl.setContentsMargins(0, 10, 0, 0)
        stl.addWidget(self.recipe)
        self.inspector.addTab(steps_box, "Steps")
        self.inspector.addTab(style_scroll, "Style")
        settings().style_sink = self._style_sink
        right = self.right = _panel(self.inspector, (12, 6, 12, 12))
        right.setMinimumWidth(420)
        # the left column sits OUTSIDE the splitter: fixed width, always flush with the video (a splitter
        # leaves a gap next to a fixed-width widget when a saved layout gives it more room)
        self.split_h.addWidget(self.split_mid)
        self.split_h.addWidget(right)
        self.split_h.setStretchFactor(0, 1)
        self.split_h.setStretchFactor(1, 0)
        self.split_h.setHandleWidth(8)
        self.split_h.setSizes([900, 440])
        self.split_h.setChildrenCollapsible(False)
        top_row = QWidget()
        tl_ = QHBoxLayout(top_row)
        tl_.setContentsMargins(0, 0, 0, 0)
        tl_.setSpacing(8)                           # same as the splitter handle: even gaps everywhere
        tl_.addWidget(left)
        tl_.addWidget(self.split_h, 1)
        # the subtitle table / timeline under everything, as wide as the window
        self.split_center = QSplitter(Qt.Vertical)
        self.split_center.addWidget(top_row)
        self.split_center.addWidget(self.timeline_panel)
        self.split_center.setStretchFactor(0, 2)
        self.split_center.setStretchFactor(1, 1)
        self.split_center.setHandleWidth(8)
        self.split_center.setChildrenCollapsible(False)
        self.work_page = QWidget()
        wl = QVBoxLayout(self.work_page)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self.split_center)

        home_panel = QFrame()
        home_panel.setObjectName("Panel")
        hpl = QVBoxLayout(home_panel)
        hpl.setContentsMargins(0, 0, 0, 0)
        hpl.addWidget(self.home)
        self.pages = QStackedWidget()
        self.pages.addWidget(home_panel)
        self.pages.addWidget(self.work_page)
        self.batch = BatchPage()
        self.batch_page = QWidget()
        self.batch_lay = QHBoxLayout(self.batch_page)
        self.batch_lay.setContentsMargins(0, 0, 0, 0)
        self.batch_lay.setSpacing(8)
        self.batch_lay.addWidget(_panel(self.batch, (0, 0, 0, 0)), 1)
        self.pages.addWidget(self.batch_page)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 0, 10, 10)
        bl.addWidget(self.pages)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(header)
        cl.addWidget(body, 1)
        self.setCentralWidget(central)

        self.log_bridge = LogBridge()
        self.log_bridge.line.connect(self.progress.add_log)

        # ---------------- wiring
        self.b_theme.clicked.connect(self.toggle_theme)
        self.b_home.clicked.connect(self.show_home)
        self.b_batch.clicked.connect(lambda: self.show_work() if self.pages.currentIndex() == 2 else self.show_batch())
        self.queue.b_add.clicked.connect(lambda: (self.queue.add_files(), self._after_add()))
        self.queue.b_folder.clicked.connect(lambda: (self.queue.add_folder(), self._after_add()))
        self.c_export.clicked.connect(lambda: self.run_kind("export"))
        self.c_run.clicked.connect(self._run_or_stop)
        for b in (self.batch.b_subs, self.batch.b_run):
            b.hide()                                         # the command bar has them
        self.batch.open_requested.connect(self._batch_open)
        self.batch.run_requested.connect(lambda kind, videos: self.start(kind, videos))
        self.batch.stop_requested.connect(self._stop_asked)
        self.batch.add_files.connect(lambda: (self.queue.add_files(), self._batch_refresh()))
        self.batch.add_folder.connect(lambda: (self.queue.add_folder(), self._batch_refresh()))
        self.batch.remove_requested.connect(self._batch_remove)
        self.runner.video_progress.connect(self.batch.on_progress)
        self.runner.step.connect(self.batch.on_step)
        self.runner.step.connect(self._step_event)
        self.runner.video_finished.connect(self.batch.on_video_finished)
        self.home.open_requested.connect(self.open_edit)
        self.home.new_requested.connect(lambda: self.new_edit())
        self.home.deleted.connect(self._edit_deleted)
        self.queue.changed.connect(self._media_changed)
        self.b_settings.clicked.connect(lambda: self.open_settings())
        self.queue.current_changed.connect(self.on_video)
        self.queue.imported.connect(self._imported)
        self.queue.reset_requested.connect(self.reset_video)
        self.editor.import_requested.connect(lambda kind: self.queue.import_srt(None, kind))
        self.editor.redo_requested.connect(self.redo_subtitles)
        self.editor.remake_voice.connect(self.remake_lines)
        self.editor.cues_changed.connect(lambda: self._touch([self.editor.project.video] if self.editor.project else []))
        self.batch.own_changed.connect(lambda v: self._touch([v]))
        self.runner.video_finished.connect(lambda v, r: self._run_results.__setitem__(v, r))
        self._touched, self._run_results, self._pending = {}, {}, None
        self.editor.seek.connect(self.preview.seek)
        self.editor.cues_changed.connect(self._cues_to_preview)
        self.preview.position_changed.connect(self.editor.highlight)
        self.preview.position_changed.connect(self._tl_follow)
        self.preview.b_timeline.toggled.connect(self._timeline_mode)
        self.runner.release.connect(self._release_sound, Qt.BlockingQueuedConnection)
        self.timeline.seek.connect(lambda t: self._tl_go(t))
        self.timeline.add_requested.connect(self._tl_add)
        self.timeline.selected.connect(self._tl_selected)
        self.timeline.retimed.connect(self._tl_retimed)
        self.timeline.retimed_back.connect(lambda v, i, a, b: self._tl_retimed(v, i, a, b, undoing=True))
        self.timeline.lines_delete.connect(self._tl_delete_lines)
        self.timeline.lines_revoice.connect(self._tl_revoice)
        self.timeline.line_add.connect(self._tl_add_line)
        self.timeline.files_back.connect(self._tl_files_back)
        self.timeline.undo_requested.connect(self._undo)
        self.timeline.redo_requested.connect(self._redo)
        self.timeline.other_history = lambda: (self.editor.undo_stamp() >= 0, self.editor.redo_stamp() >= 0)
        self.editor.cues_changed.connect(self.timeline.refresh_history)
        self.timeline.edit_text.connect(self._tl_edit_text)
        self.style.line_back.connect(lambda: self.timeline.select(None))
        self.style.line_text_changed.connect(self._line_text_changed)
        self.queue.list.setDragEnabled(True)                # videos are dragged onto the timeline
        self.queue.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.preview.region_selected.connect(self._region)
        self.preview.box_moved.connect(self._box_moved)
        self.preview.style_moved.connect(self.style.refresh)
        self.preview.end_reached.connect(self._preview_end)
        self.preview.seek_hook = self._preview_seek
        self.recipe.export_requested.connect(self.start)
        self.recipe.area_requested.connect(self._ask_area)
        self.recipe.keys_requested.connect(lambda: self.open_settings("translation"))
        self.recipe.download_whisper.connect(self.download_model)
        self.recipe.subtitle_source = self._names_source
        self.preview.which_changed.connect(self._cues_to_preview)
        self.recipe.download_voice.connect(self.download_voice)
        self.recipe.changed.connect(self._recipe_changed)
        self.recipe.mix_changed.connect(lambda: self.preview.mix.refresh_levels())
        self.recipe.narration_preview.connect(self.preview_narration)
        self.recipe.sync_changed.connect(self.editor.sync_voice_button)
        self.editor.b_voice.toggled.connect(lambda _on: self.recipe.refresh_dub_options())
        self.preview.b_mix.toggled.connect(self._mic)
        self.preview.b_mix.customContextMenuRequested.connect(self._mic_menu)
        self.recipe.one.currentIndexChanged.connect(lambda _i: self.editor._refill_later())     # the Voice column
        self.style.changed.connect(self._recipe_changed)
        self.style.area_requested.connect(self._ask_area)
        self.style.reset_requested.connect(self._box_reset)
        self.style.sample_requested.connect(self.preview.show_sample)
        self.runner.started.connect(lambda *_: self.set_busy(True))
        self.runner.finished.connect(self.on_finished)
        self.runner.video_finished.connect(self.on_video_finished)
        self.sig.gpu_text.connect(self._gpu_text)
        self.progress.b_log.toggled.connect(self._log_toggled)

        self._shortcuts()
        self.restore()
        self.apply_theme(startup=True)
        self._recipe_changed()
        self.show_home()
        # the graphics card check loads big libraries: after the window is up, so it opens quickly
        QTimer.singleShot(1500, self._startup_checks)

    # ------------------------------------------------------------ theme / settings
    def apply_theme(self, startup=False):
        """Restyling every widget is the slow part: done once (main.py already did it at start), with the
        window's painting held until it is over and a busy cursor meanwhile."""
        app = QApplication.instance()
        want = settings()["theme"]
        if not (startup and theme.applied(want)):
            app.setOverrideCursor(Qt.WaitCursor)
            self.setUpdatesEnabled(False)
            try:
                theme.apply(app, want)
            finally:
                self.setUpdatesEnabled(True)
                app.restoreOverrideCursor()
        self.b_theme.set_icon("sun" if theme.is_dark() else "moon")
        icons.refresh_all()
        self.preview.apply_theme()
        if hasattr(self, "batch"):
            self.batch.apply_theme()
        if hasattr(self, "queue"):
            self.queue.refresh_theme()

    def toggle_theme(self):
        settings().update(theme="light" if theme.is_dark() else "dark")
        self.apply_theme()

    def open_settings(self, tab="general"):
        before = (settings()["output_location"], settings()["output_root"])
        with wait_cursor():
            dlg = SettingsDialog(self, tab)          # checks ffmpeg, the models, the graphics card
        if dlg.exec():
            if dlg.restart:
                self.restart_app()
                return
            self.apply_theme()
            self.recipe.sync_quality()
            self.recipe.refresh()
            self._cues_to_preview()
            if (settings()["output_location"], settings()["output_root"]) != before:
                self.queue.update_all()

    def _log_toggled(self, on):
        pass                                          # the log has a window of its own (ProgressPanel)

    # ------------------------------------------------------------ pages
    def show_home(self):
        self.preview.pause()
        self.editor._flush()
        self.b_batch.setVisible(False)               # Batch belongs inside a project
        if self.home.in_trash:
            self.home.b_trash.setChecked(False)      # Projects button: out of the Trash (refreshes)
        else:
            self.home.refresh()
        self.pages.setCurrentIndex(0)
        self._run_buttons(False)
        self.b_home.setChecked(False)
        self.header_info.setText("")

    def _right_to(self, batch: bool):
        """The settings column (shared recipe) goes with the page: workspace or batch."""
        if batch and self.right.parent() is not self.batch_page:
            sizes = self.split_h.sizes()
            self._split_sizes = sizes
            self.batch_lay.addWidget(self.right)
            self.right.setFixedWidth(max(380, sizes[1] if len(sizes) > 1 else 400))
        elif not batch and self.right.parent() is self.batch_page:
            self.right.setMinimumWidth(420)
            self.right.setMaximumWidth(16777215)
            self.split_h.insertWidget(1, self.right)
            if getattr(self, "_split_sizes", None):
                self.split_h.setSizes(self._split_sizes)

    def show_batch(self):
        self.preview.pause()
        self.editor._flush()
        self._right_to(True)
        self._run_buttons(True)
        self._batch_refresh()
        self.pages.setCurrentIndex(2)
        self.b_batch.setVisible(True)
        self.header_info.setText(self.edit.name if self.edit else "")

    def _batch_refresh(self):
        self.batch.set_videos(self.queue.videos(), self.queue.info_for)

    def _batch_open(self, video):
        for i in range(self.queue.list.count()):
            if self.queue.list.item(i).data(Qt.UserRole) == video:
                self.queue.list.setCurrentRow(i)
                break
        self.show_work()

    def _batch_remove(self, video):
        for i in range(self.queue.list.count()):
            if self.queue.list.item(i).data(Qt.UserRole) == video:
                self.queue.list.takeItem(i)
                break
        self.queue.empty.setVisible(self.queue.list.count() == 0)
        self._batch_refresh()

    def _run_buttons(self, on):
        """Export belongs to the work and batch pages, not to the Projects screen."""
        self.c_export.setVisible(on)
        self.c_run.setVisible(on)

    def show_work(self):
        self._right_to(False)
        self._run_buttons(True)
        self.pages.setCurrentIndex(1)
        self.b_batch.setVisible(True)
        self._header_name()

    def _header_name(self):
        v = self.queue.current()
        name = self.edit.name if self.edit else ""
        self.header_info.setText(" › ".join(x for x in (name, os.path.basename(v) if v else "") if x))

    # ------------------------------------------------------------ projects (videos + their timeline)
    @wait_cursor()
    def open_videos(self, paths):
        """Videos given to FreeSub (dropped on the Projects screen, opened with it): a new project of them; with
        a project open, they are added to it."""
        if self.edit is not None and self.pages.currentIndex() in (1, 2):
            before = set(self.queue.videos())
            self.queue.add_paths(paths)
            new = next((v for v in self.queue.videos() if v not in before), None)
            if new:
                self._select_video(new)
            if self.pages.currentIndex() == 2:
                self._batch_refresh()
            return
        self.new_edit(paths)

    def new_edit(self, paths=()):
        from ..core import edits
        self.open_edit(edits.create().id, add=paths)

    @wait_cursor()
    def open_edit(self, eid, add=()):
        """Open a project: its videos in Media, its timeline (shown), the first video in the player."""
        from ..core import edits
        from ..steps.timeline import Timeline
        e = edits.load(eid)
        if e is None:
            return
        self.editor._flush()
        self.preview.pause()
        self.edit = None                               # nothing is saved into it while it is being loaded
        e.touch()
        gone = [v for v in e.videos if not os.path.isfile(v)]
        self.queue.set_videos(e.videos)
        self.edit = e
        self._unify_look()                             # one look for the project (older ones could differ)
        self._refresh_look()
        self.timeline.set_timeline(Timeline([dict(c) for c in e.clips]), self._store_clips)
        if add:
            self.queue.add_paths(list(add))            # saved in the project (changed)
        if self.queue.list.count() and self.queue.list.currentRow() < 0:
            self.queue.list.setCurrentRow(0)
        self.show_work()
        self.preview.b_timeline.setChecked(True)       # a project opens on its timeline
        if gone:
            QMessageBox.information(self, APP_NAME, "These videos are not there any more:\n\n" +
                                    "\n".join(gone[:10]) + ("\n…" if len(gone) > 10 else ""))

    def _store_clips(self, tl):
        if self.edit is not None:
            self.edit.clips = [dict(c) for c in tl.clips]
            self.edit.save()

    def _media_changed(self):
        if self.edit is not None:
            before = set(self.edit.videos)
            now = self.queue.videos()
            if any(v not in before for v in now):         # videos added: they take the project's look
                ref = next((v for v in now if v in before), None)
                if ref:
                    self._unify_look(ref)
                    self._refresh_look()
            self.edit.set_videos(now)
            if self.pages.currentIndex() == 1:
                self._header_name()
            elif self.pages.currentIndex() == 2:
                self._batch_refresh()

    def _edit_deleted(self, eid, videos):
        from ..steps.timeline import Timeline
        if self.edit is not None and self.edit.id == eid:
            self.edit = None                           # nothing may be saved into it any more
            self.timeline.set_timeline(Timeline(), None)
            self.queue.set_videos([])
        keep = set(self.queue.videos())
        for v in videos:
            if v in keep:
                continue                               # the open project has it too: it stays
            self.editor.forget(v)                      # unsaved edits must not write it back
            if self.preview.path == v:                 # let go of its files (Windows locks them)
                self._live_off()
                self.preview.mix.clear()
                self.preview.load("")

    def _project_deleted(self, video):
        self.editor.forget(video)                  # unsaved edits must not write the project back
        if video == self.queue.current() or self.preview.path == video:   # let go of its files (Windows locks them)
            self._live_off()
            self.preview.mix.clear()
            self.preview.load("")
            self._line_target = None
            self.style.set_line_mode(None)
            settings().ui_style = {}
        tl = self.timeline.tl
        if any(c["video"] == video for c in tl.clips):       # its clips leave the timeline too
            new = tl.copy()
            new.clips = [c for c in new.clips if c["video"] != video]
            self.timeline.apply(new, undoable=False)
        self.timeline._videos.pop(video, None)
        for i in range(self.queue.list.count()):
            if self.queue.list.item(i).data(Qt.UserRole) == video:
                self.queue.list.takeItem(i)
                break

    # ------------------------------------------------------------ the selected video
    @wait_cursor()
    def on_video(self, video):
        from ..core.style import video_style
        t = getattr(self, "_line_target", None)
        if t and t[0] != video:                       # another video: no line of the last one is chosen
            self._line_target = None
            self.timeline.sel = None
            self.preview.sample_look = None
            self.preview.line_mode = False                 # a box dragged now is the new video's
            self.style.set_line_mode(None)
        settings().ui_style = video_style(Project(video)) if video else {}
        self.style.refresh()
        self.preview.sync_speed()                      # the project's playback speed
        if not video:
            self._shown_video = None
            self.preview.load("")
            self.editor.load("")
            self._header_name()
            return
        if not same_file(video, getattr(self, "_shown_video", None)):
            self._live_off()                          # another video: its narration is not this one's
        self._shown_video = video
        info = self.queue.info_for(video)
        self.style.set_video_height(info.get("height"))
        self._show_video(video, info)
        self._update_mix(video)
        pv = self.preview
        if pv.mix.ready() and not pv.live.on:          # its narration is made: heard at once (mic lit)
            if not pv.b_mix.isChecked():
                pv.b_mix.blockSignals(True)            # (not a click: nothing is made)
                pv.b_mix.setChecked(True)
                pv.b_mix.blockSignals(False)
            pv.mix.set_enabled(True)
        self._header_name()
        self.editor.load(video)
        self._cues_to_preview()
        self._recipe_changed()

    def _release_sound(self, video):
        """A step is about to write this video's sound files: the preview lets go of them (Windows refuses to
        write a file that is open), and does not open them again until the step is over."""
        self._writing = video
        if self.preview.path == video:
            self.preview.set_mix(None, [])

    def _update_mix(self, video):
        """Give the preview this video's narration mix (if it was made) for live listening."""
        from ..steps import dub
        if not video or getattr(self, "_writing", None) == video:
            return
        p = Project(video)
        src, spans = dub.preview_sources(p)
        self.preview.set_mix(src, spans)
        self.recipe.refresh_mix_state(p.has("music.wav"))

    def preview_narration(self):
        """Narration card → Preview narration: the same as the mic button on the video."""
        if self.preview.b_mix.isChecked():
            self._mic(True)
        else:
            self.preview.b_mix.setChecked(True)

    def _mic(self, on):
        """The mic button on the video. On: hear the narration with the video. When it is not made yet (or
        the subtitles / voice changed since), it is made now, and each line can be heard as soon as it is
        ready; the timeline lights up as far as it goes."""
        pv = self.preview
        if not on:
            pv.mix.set_enabled(False)
            pv.live.stop()
            return
        v = self.queue.current()

        def off():
            pv.b_mix.blockSignals(True)
            pv.b_mix.setChecked(False)
            pv.b_mix.blockSignals(False)
        if not v:
            off()
            return
        self.recipe.voice_card.set_on(True)            # hearing the narration: Steps → Narration goes on too
        from ..steps import dub
        p = Project(v)
        if self.runner.busy:
            if pv.mix.ready() and p.is_done("dub", dub.MIX) and dub.track_fresh(p):
                pv.mix.set_enabled(True)                        # this one is done already: hear it
                return
            if v in (getattr(self, "_live_video", None), getattr(self, "_dubbing", None)):
                pv.live.start(p.path(dub.LIVE))                 # being made right now: listen along
                return
            QMessageBox.information(self, APP_NAME, "The narration of this video is not made yet: it is made in "
                                    "its turn (or stop the work to make it now).")
            off()
            return
        if pv.mix.ready() and p.is_done("dub", dub.MIX) and dub.track_fresh(p):
            pv.mix.set_enabled(True)                            # made already: the full mix
            return
        if not self.get_ffmpeg():
            off()
            return
        self.editor._flush()
        pv.set_mix(None, [])                       # let go of the old files: they are about to be written
        pv.live.stop(release=True)
        if not dub.track_fresh(p):
            dub.clear_live(p)
        pv.live.start(p.path(dub.LIVE))
        self.last_kind = "dub"
        self._live_video = v
        self._run_videos, self._done_videos = [v], []
        self.runner.run_videos([v], None, title="Narration", upto="live")

    def _step_event(self, video, sid, state, _text):
        """Subtitles or their translation just made: show them in the table (and on the video) at once, while
        the rest (narration, export) goes on, so they can be read and fixed meanwhile."""
        if sid in ("audio", "separate", "dub") and state != "start" and getattr(self, "_writing", None) == video:
            self._writing = None                     # its sound files are written: they may be opened again
            if sid != "dub" and video == self.queue.current():
                self._update_mix(video)
        if sid == "dub":                            # whose narration is being made (the mic can listen along)
            if state == "start":
                self._dubbing = video
            elif getattr(self, "_dubbing", None) == video:
                self._dubbing = None
                if video == self.queue.current():
                    self.editor.refresh_marks()          # colour the lines read late
                if state == "done" and video == self.queue.current():
                    pv = self.preview
                    if pv.live.on:
                        pv.live.clear()              # all made: the full mix takes over
                    self._update_mix(video)          # made: hear it while the other videos go on
                    if pv.b_mix.isChecked() and pv.mix.ready():
                        pv.mix.set_enabled(True)
        if state != "done" or sid not in ("transcribe", "ocr", "translate", "import"):
            return
        self.queue.update_item(video=video)
        if video != self.queue.current():
            return
        self.editor.reload_keep_place()
        self._cues_to_preview()

    def _mic_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        b = self.preview.b_mix
        m = QMenu(self)
        a = m.addAction("Make the narration again", self.remake_narration)
        a.setToolTip("With your subtitle edits and the current Narration settings, then hear it")
        a.setEnabled(bool(self.queue.current()) and not self.runner.busy)
        if b.isChecked():
            m.addAction("Stop listening", lambda: b.setChecked(False))
        m.exec(b.mapToGlobal(pos))

    def remake_narration(self):
        """Right-click on the mic: the narration made again (the lines' text, voices and timing as they are now),
        heard while it is made, like the mic button does the first time."""
        from ..steps import dub
        v = self.queue.current()
        if not v or self.runner.busy:
            return
        if not self.get_ffmpeg():
            return
        pv = self.preview
        self.editor._flush()
        p = Project(v)
        pv.set_mix(None, [])
        pv.live.stop(release=True)
        dub.clear_live(p)
        pv.b_mix.blockSignals(True)
        pv.b_mix.setChecked(True)
        pv.b_mix.blockSignals(False)
        pv.live.start(p.path(dub.LIVE))
        self.last_kind = "dub"
        self._live_video = v
        self._run_videos, self._done_videos = [v], []
        self.runner.run_videos([v], None, force={"dub"}, title="Narration again", upto="live")

    def remake_lines(self, starts):
        """Right click on lines → Make the voice again: their clips are made fresh (an edited line is read with
        its new text), then the whole narration is laid again (a shorter line moves the ones after it)."""
        from ..steps import dub
        v = self.queue.current()
        if not v or not starts:
            return
        if self.runner.busy:
            QMessageBox.information(self, APP_NAME, "Wait until the work running now is done (or stop it), then "
                                    "make the voice again.")
            return
        self.editor._flush()
        n = dub.forget_clips(Project(v), starts)
        log.info("Voice made again for %d line(s) (%d saved clips dropped)", len(starts), n)
        self.remake_narration()

    def _live_off(self):
        """Another video was opened: the last one's narration stops (and its lit timeline). The mic button stays
        as the user left it: only the user turns it off."""
        pv = self.preview
        self._shown_video = None                      # the next video opened starts from nothing
        pv.live.clear()
        pv.mix.set_enabled(False)

    def _show_video(self, video, info):
        """Play the video, or a preview copy when the player cannot decode it (AV1 without GPU support)."""
        if not proxy.needs_copy(info):
            self.preview.load(video, info.get("fps") or 0)
            return
        copy = proxy.ready_copy(video)
        if copy:
            self.preview.load(video, info.get("fps") or 0, media=copy)
            return
        self.preview.load(video, info.get("fps") or 0,
                          waiting="This AV1 video needs a preview copy to be shown here.\nMaking it…")
        self._grow = {"video": video, "playing": False, "avail": 0.0, "loaded": 0.0, "stalled": None}
        self.proxies.make(video, info.get("duration") or 0)

    def _proxy_progress(self, video, frac, avail):
        """The copy grows: start playing after a few seconds of it, then open it again when the player gets to
        the end of what is there (it goes on from the same place)."""
        g = getattr(self, "_grow", None)
        if video != self.queue.current() or not g or g["video"] != video:
            return
        g["avail"] = avail
        info = self.queue.info_for(video)
        if not g["playing"]:
            if avail >= 3.0:
                g["playing"], g["loaded"] = True, avail
                self.preview.load(video, info.get("fps") or 0, media=Project(video).path(proxy.PREVIEW),
                                  full_duration=info.get("duration") or 0)
                self._cues_to_preview()
                self._recipe_changed()
            else:
                self.preview.view.show_message("This AV1 video needs a preview copy to be shown here.\n"
                                               f"Making it… {frac * 100:.0f}%")
            return
        self.preview.time.setToolTip(f"Preview copy: {frac * 100:.0f}% made")
        if g["stalled"] is not None and avail >= g["stalled"] + 5:
            at, g["stalled"] = g["stalled"], None
            g["loaded"] = avail
            self.preview.view.show_message("")
            self.preview.reload(at, g.get("resume", True))
        elif g["stalled"] is not None:
            self.preview.view.show_message(f"Making the preview up to there… {frac * 100:.0f}%")

    def _preview_end(self):
        g = getattr(self, "_grow", None)
        if not g or not g["playing"] or g["video"] != self.queue.current() or not self.proxies.busy(g["video"]):
            return
        if g["stalled"] is None:                         # (a jump further on is already waiting)
            g["stalled"] = self.preview.player.position() / 1000
            g["resume"] = True
        self.preview.view.show_message("Making the rest of the preview…")

    def _preview_seek(self, seconds):
        """A jump past what is made so far: wait there until that part exists."""
        g = getattr(self, "_grow", None)
        if g and g["playing"] and g["video"] == self.queue.current() and self.proxies.busy(g["video"]) \
                and seconds > g["loaded"] - 0.5:
            if seconds <= g["avail"] - 0.5:
                g["loaded"] = g["avail"]
                self.preview.reload(seconds, self.preview.player.playbackState() == QMediaPlayer.PlayingState)
            else:
                g["resume"] = self.preview.player.playbackState() == QMediaPlayer.PlayingState or \
                    g["stalled"] is not None
                g["stalled"] = seconds
                self.preview.player.pause()
                self.preview.view.show_message("Making the preview up to there…")
            return True
        return False

    def _proxy_ready(self, video, _path):
        g = getattr(self, "_grow", None)
        if video == self.queue.current():
            self.preview.time.setToolTip("")
            if g and g["video"] == video and g["playing"]:
                self.preview.view.show_message("")
                pos = g["stalled"] if g["stalled"] is not None else self.preview.player.position() / 1000
                playing = (g["stalled"] is not None and g.get("resume", True)) or \
                    self.preview.player.playbackState() == QMediaPlayer.PlayingState
                g["stalled"] = None
                self.preview.reload(pos, playing)        # now with the whole length
            else:
                self.preview.path = ""                   # load again, now with the copy
                self._show_video(video, self.queue.info_for(video))
                self._cues_to_preview()
                self._recipe_changed()
        if g and g["video"] == video:
            self._grow = None

    def _proxy_failed(self, video, msg):
        if video == self.queue.current():
            self.preview.view.show_message("This video cannot be shown here (AV1).\nExport still works.\n"
                                           + msg[:120])

    def _cues_to_preview(self):
        """The preview shows what Export burns in: with narration (and its times button on in the subtitle
        table), each line when the narration reads it."""
        s = settings()
        v = self.queue.current()
        times = None
        if v and s["do_voice"] and s["export_which"] != "original":
            from ..steps.dub import narrated_times
            try:
                times = narrated_times(Project(v))
            except Exception:  # noqa: BLE001
                times = None
        self.editor.set_voice_times(times)
        has_tr = any((c.text or "").strip() for c in self.editor.tr)
        self.preview.set_cues(self.editor.shown_cues(
            "translation" if self.preview.shows_translation() or not has_tr else "original"))

    def _current_region(self):
        v = self.queue.current()
        if v:
            r = Project(v).meta.get("ocr_region")
            if r:
                return r
        return settings()["ocr_region"]

    def _recipe_changed(self):
        s = settings()
        ocr = s["subs_source"] == "ocr"
        region = self._current_region()
        box = bool(s["sub_box"])
        from ..core.style import box_area
        v = self.queue.current()
        self.preview.set_region(region, show=ocr or s["sub_box"], box_mode=box,
                                box_area=box_area(Project(v)) if v else box_area())
        self.style.set_area_state(region)
        self.recipe.set_area_state(region)
        self.preview.view.subs.update()

    def _ask_area(self):
        if not self.queue.current():
            QMessageBox.information(self, APP_NAME, "Add a video first, then draw the area on it.")
            return
        self.preview.start_area()

    def _region(self, region):
        """The area was drawn: OCR reads it and the box goes there, for every video of the project (videos of
        one project are one series: the same place)."""
        settings().update(ocr_region=region)            # remembered for the next projects too
        for v in self._style_videos():
            p = Project(v)
            p.meta["ocr_region"] = region
            p.meta["box_region"] = list(region)
            p.save()
        log.info("Subtitle area: %s", region)
        self._touch(self._style_videos())
        self._recipe_changed()

    # ------------------------------------------------------------ one look for the whole project
    def _style_videos(self):
        """Style goes to every video of the open project (one look for the project)."""
        return [v for v in self.queue.videos() if v]

    def _unify_look(self, ref=None):
        """Make every video of the project look like ref (default: the first one that has a look of its own).
        Used when a project opens (older projects could differ) and when videos are added."""
        videos = self._style_videos()
        if len(videos) < 2:
            return
        if ref is None:
            ref = next((v for v in videos if Project(v).meta.get("style") or Project(v).meta.get("box_region")),
                       None)
            if ref is None:
                return
        src = Project(ref)
        st, box = dict(src.meta.get("style") or {}), src.meta.get("box_region")
        ocr = src.meta.get("ocr_region")
        for v in videos:
            if v == ref:
                continue
            p = Project(v)
            if (p.meta.get("style") or {}) == st and p.meta.get("box_region") == box and \
                    (not ocr or p.meta.get("ocr_region") == ocr):
                continue
            p.meta["style"] = dict(st)
            if box:
                p.meta["box_region"] = list(box)
            else:
                p.meta.pop("box_region", None)
            if ocr:
                p.meta["ocr_region"] = list(ocr)
            p.save()

    def _refresh_look(self):
        from ..core.style import video_style
        v = self.queue.current()
        settings().ui_style = video_style(Project(v)) if v else {}
        self.style.refresh()
        self.preview.sync_speed()
        self._recipe_changed()

    def _style_sink(self, values) -> bool:
        """A Style value changed: every video of the project keeps it. The shared settings change too, so a
        new project starts with the last look. True = handled here (shared settings untouched)."""
        for v in self._style_videos():
            p = Project(v)
            st = dict(p.meta.get("style") or {})
            st.update(values)
            p.meta["style"] = st
            p.save()
        settings().ui_style = dict(settings().ui_style, **values)
        return False

    def _box_moved(self, area):
        """The box was dragged on the video: it moves for every video of the project (the area OCR reads
        stays)."""
        for v in self._style_videos():
            p = Project(v)
            p.meta["box_region"] = area
            p.save()
        self._touch(self._style_videos())

    def _box_reset(self):
        """Reset in Style: the box goes back on the area that was drawn (every video of the project)."""
        for v in self._style_videos():
            p = Project(v)
            if p.meta.pop("box_region", None) is not None:
                p.save()
        self._touch(self._style_videos())
        self._recipe_changed()

    # ------------------------------------------------------------ the timeline
    def _timeline_mode(self, on):
        """The timeline button on the video: the timeline takes the place of the subtitle table (taller)."""
        # the subtitle table and the timeline take turns under the video: the one shown gets the height the
        # other had (they look like one panel that changes what it shows)
        if on:
            h = self.editor_panel.height() if self.editor_panel.isVisible() else 0
            self.editor_panel.setVisible(False)
            self.timeline_panel.setVisible(True)
            QTimer.singleShot(0, self._tl_from_player)         # the timeline opens where the video is
            h = h or max(170, self.split_center.height() // 3)
            QTimer.singleShot(0, lambda: self.split_center.setSizes(        # after the layout has settled
                [max(1, self.split_center.height() - h - self.split_center.handleWidth()), h]))
            self._tl_index = -1
        else:
            h = self.timeline_panel.height() if self.timeline_panel.isVisible() else 0
            self.timeline.select(None)
            self.timeline_panel.setVisible(False)
            self.editor_panel.setVisible(True)
            if h:
                QTimer.singleShot(0, lambda: self.split_mid.setSizes(
                    [max(1, self.split_mid.height() - h - self.split_mid.handleWidth()), h]))

    def _tl_from_player(self):
        """Switched to the timeline: its playhead goes to the moment the video is at (in the clip of this video
        that holds that moment; nothing moves when no clip does). The video itself does not move."""
        try:
            tl = self.timeline.tl
            v = self.preview.path
            if not tl.clips or not v:
                return
            t = self.preview.player.position() / 1000
            i = next((k for k, c in enumerate(tl.clips) if c["video"] == v and c["in"] <= t <= c["out"]), -1)
            if i < 0:
                return
            self._tl_index = i
            self.timeline.reveal(tl.to_timeline(i, t))
        except Exception as e:  # noqa: BLE001        (never worth stopping the window for)
            log.warning("Timeline: could not go to the video's moment: %s", e)

    def timeline_on(self) -> bool:
        return self.preview.b_timeline.isChecked()

    def _select_video(self, video):
        if not any(same_file(video, v) for v in self.queue.videos()):
            self.queue.add_paths([video])
        for i in range(self.queue.list.count()):
            if same_file(self.queue.list.item(i).data(Qt.UserRole), video):
                if self.queue.list.currentRow() != i:
                    self.queue.list.setCurrentRow(i)
                return

    def _tl_add(self, paths, index):
        files = []
        for p in paths:
            if os.path.isdir(p):
                continue
            p = os.path.abspath(p)
            if p not in self.queue.videos():
                self.queue.add_paths([p])
            if p in self.queue.videos():
                files.append(p)
        if files:
            with wait_cursor():
                for p in files:
                    self.queue.info_for(p)                     # the length of each, read once
            self.timeline.add(files, index)

    def _tl_go(self, t, play=None):
        """Show the timeline at t: the clip's video, at the right time in it."""
        tl = self.timeline.tl
        i, vt = tl.locate(t)
        if i < 0:
            return
        self._tl_index = i
        clip = tl.clips[i]
        pv = self.preview
        if play is None:
            play = pv.player.playbackState() == QMediaPlayer.PlayingState
        self.timeline.set_playhead(tl.to_timeline(i, vt), follow=False)
        if not same_file(clip["video"], self.queue.current()) or not same_file(pv.path, clip["video"]):
            self._tl_switch = clip["video"]           # until it is open, the old video's times mean nothing
            self._select_video(clip["video"])
            if pv.path != clip["video"]:
                self._tl_switch = None
                return
            target, tries = clip["video"], [0]

            def when_open():                          # then go to the clip's time (and play on)
                if pv.path != target or self._tl_switch != target:
                    return
                ok = pv.player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                                                 QMediaPlayer.BufferingMedia) and pv.player.duration() > 0
                tries[0] += 1
                if not ok and tries[0] < 150:
                    QTimer.singleShot(20, when_open)
                    return
                pv._pending = None
                pv.player.setPosition(int(vt * 1000))
                pv.player.play() if play else pv.player.pause()
                self._tl_switch = None
            QTimer.singleShot(0, when_open)
            return
        pv.seek(vt)
        if play and pv.player.playbackState() != QMediaPlayer.PlayingState:
            pv.player.play()

    def _tl_follow(self, t):
        """The video plays: the timeline's line follows, and at the end of a clip the next one starts."""
        if not self.timeline_on():
            return
        switch = getattr(self, "_tl_switch", None)
        if switch:
            return                                       # the next clip's video is still being opened
        tl = self.timeline.tl
        if not tl.clips:
            return
        i = getattr(self, "_tl_index", -1)
        if not (0 <= i < len(tl.clips)) or not same_file(tl.clips[i]["video"], self.preview.path):
            i = next((k for k, c in enumerate(tl.clips)
                      if same_file(c["video"], self.preview.path) and c["in"] - 0.05 <= t <= c["out"] + 0.05), -1)
            if i < 0:
                return
            self._tl_index = i
        clip = tl.clips[i]
        playing = self.preview.player.playbackState() == QMediaPlayer.PlayingState
        if playing and t < clip["in"] - 0.3:
            return                                       # the next clip is still being opened
        if playing and t >= clip["out"] - 0.04:
            if i + 1 < len(tl.clips):
                self._tl_go(tl.start_of(i + 1), play=True)
            else:
                self.preview.player.pause()
                self.timeline.set_playhead(tl.total())
            return
        self.timeline.set_playhead(tl.to_timeline(i, t), follow=playing)

    def _tl_selected(self, sel):
        """A line clicked on the timeline: chosen, its words can be edited in Style. The video and the playhead
        stay where they are (the playhead marks where the line is to be moved)."""
        self.style.flush_text()                           # words typed for the line chosen before: kept there
        if sel and sel[0] == "cues":                      # several lines: no words to edit for one line
            self._line_target = None
            self.style.set_line_mode(None)
            return
        if sel and sel[0] == "cue":
            _k, video, index = sel
            self._line_target = (video, index)
            self.style.set_line_mode(index + 1, None, self._line_texts(video, index))
        else:
            self._line_target = None
            self.style.set_line_mode(None)

    def _line_texts(self, video, index):
        """(original, translation or None) of one line."""
        from ..core.subtitle import load_srt
        _p, src, tr = self._srt_files(video)
        if video == self.queue.current():
            self.editor._flush()
        out = []
        for path in (src, tr):
            if not path:
                out.append(None)
                continue
            cues = load_srt(path)
            out.append(cues[index].text if 0 <= index < len(cues) else "")
        return out[0] or "", out[1]

    def _line_text_changed(self, kind, text):
        """The chosen line's words edited in Style: saved in its .srt (a translation edit is kept when
        translating again)."""
        from ..core.subtitle import load_srt, save_srt
        t = getattr(self, "_line_target", None)
        if not t:
            return
        video, index = t
        ed = self.editor
        if video == self.queue.current() and ed.project is not None:
            if ed.set_line_text(index, kind, text):      # the table's history: Ctrl+Z brings the old words back
                self.timeline.reload_video(video)
            return
        p, src, tr = self._srt_files(video)
        path = tr if kind == "translation" else src
        if not path:
            return
        cues = load_srt(path)
        if not 0 <= index < len(cues) or cues[index].text == text.strip():
            return
        cues[index].text = text.strip()
        save_srt(cues, path)
        if path == tr:
            p.meta["tr_edited"] = True
            p.save()
        elif tr:                                  # keep the translation the newer file (not made again)
            os.utime(tr, None)
        self.timeline.reload_video(video)
        if video == self.queue.current():
            self.editor.reload_keep_place()
            self._cues_to_preview()

    def _srt_files(self, video):
        p = Project(video)
        return p, (p.src_srt if p.has(SRC_SRT) else None), (p.tr_srt if p.has(TR_SRT) else None)

    def _tl_retimed(self, video, index, a, b, undoing=False):
        """A line dragged on the timeline: new times in the original and the translation (same line). The open
        video's line goes through the subtitle table (one Ctrl+Z history with the table's edits); another
        video's is written here and kept by the timeline for Ctrl+Z."""
        from ..core.subtitle import load_srt, save_srt
        ed = self.editor
        if video == self.queue.current() and ed.project is not None and not undoing and 0 <= index < len(ed.src):
            ed.retime(index, a, b)
            self.timeline.reload_video(video)
            return
        if video == self.queue.current():
            ed._flush()
        p, src, tr = self._srt_files(video)
        before = None
        for path in (src, tr):                    # the original first: the translation stays the newer file
            if not path:
                continue
            cues = load_srt(path)
            if 0 <= index < len(cues):
                if before is None:
                    before = (cues[index].start, cues[index].end)
                cues[index].start, cues[index].end = a, b
                save_srt(cues, path)
        if before is not None and not undoing:
            self.timeline.remember_cue(video, index, before, (a, b))
        self.timeline.reload_video(video)
        if video == self.queue.current():
            ed.reload_keep_place()
            self._cues_to_preview()

    NEW_LINE = 2.0          # seconds: a new line's length (shorter when the next line starts sooner)

    @staticmethod
    def free_slot(cues, t, length=NEW_LINE):
        """(start, end) for a new line at t among cues [(start, end)], or None: t is inside a line, or the next
        line starts too soon after it (under 0.3 s)."""
        if any(a <= t < b for a, b in cues):
            return None
        nxt = min([a for a, _b in cues if a > t] or [t + length])
        end = min(t + length, nxt)
        return (round(t, 3), round(end, 3)) if end - t >= 0.3 else None

    def _tl_add_line(self, video, t):
        """New line button on the timeline: an empty line at the playhead, chosen, its words ready to type in
        Style. The open video's line goes through the subtitle table (Ctrl+Z takes it away); another video's is
        written here (the timeline keeps it for Ctrl+Z)."""
        from ..core.subtitle import Cue, load_srt, save_srt
        ed = self.editor
        current = video == self.queue.current() and ed.project is not None
        if current:
            ed._flush()
            cues = [(c.start, c.end) for c in ed.src]
        else:
            p, src, trs = self._srt_files(video)
            src = src or Project(video).src_srt
            cues = [(c.start, c.end) for c in load_srt(src)] if os.path.isfile(src) else []
        slot = self.free_slot(cues, t)
        if slot is None:
            QMessageBox.information(self, APP_NAME, tr("There is already a line at the playhead (or the next one "
                                                       "starts right after it): move the playhead to an empty "
                                                       "place first."))
            return
        a, b = slot
        if current:
            k = ed.insert_at(a, b)
            if k < 0:
                return
        else:
            import bisect
            k = bisect.bisect_right([c[0] for c in cues], a)
            before, after = {}, {}
            for path in (src, trs):
                if not path:
                    continue
                before[path] = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
                lines = load_srt(path) if os.path.isfile(path) else []
                lines.insert(min(k, len(lines)), Cue(a, b, ""))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                save_srt(lines, path)
                with open(path, encoding="utf-8") as f:
                    after[path] = f.read()
            self.timeline.remember_files(video, before, after)
        self.timeline.reload_video(video)
        self.timeline.select(("cue", video, k))           # chosen: its words open in Style
        self._tl_edit_text(video, k)

    def _tl_delete_line(self, video, index):
        self._tl_delete_lines([(video, index)])

    def _tl_delete_lines(self, chosen):
        """Delete on the timeline with subtitle lines chosen: they go (original and translation). The open
        video's lines are deleted by the subtitle table (its question, its Ctrl+Z history); other videos' here,
        after one question, kept by the timeline for Ctrl+Z."""
        from ..core.subtitle import load_srt, save_srt
        ed = self.editor
        by_video = {}
        for video, index in chosen:
            by_video.setdefault(video, set()).add(index)
        cur = self.queue.current() if ed.project is not None else None
        changed = False
        if cur in by_video:
            rows = sorted(i for i in by_video.pop(cur) if 0 <= i < len(ed.src))
            n = len(ed.src)
            if rows:
                ed.delete_lines(rows)
                if len(ed.src) < n:
                    changed = True
                    self.timeline.reload_video(cur)
                elif by_video:
                    return                                  # said No: the others are kept too
        jobs = []
        for video, idx in by_video.items():
            p, src, trs = self._srt_files(video)
            if src:
                cues = load_srt(src)
                idx = sorted(i for i in idx if 0 <= i < len(cues))
                if idx:
                    jobs.append((video, src, trs, idx, cues))
        if jobs:
            total = sum(len(j[3]) for j in jobs)
            if total == 1:
                video, src, trs, idx, cues = jobs[0]
                text = " ".join((cues[idx[0]].text or "").split())[:60]
                ask = (tr("Delete line {a}?\n“{b}”\n(Ctrl+Z brings it back.)").replace("{b}", text) if text
                       else tr("Delete line {a}?\n(Ctrl+Z brings it back.)")).replace("{a}", str(idx[0] + 1))
            else:
                ask = tr("Delete {a} lines?\n(Ctrl+Z brings them back.)").replace("{a}", str(total))
            if confirm(self, ask, "line_delete", tr("Delete"), danger=True):
                for video, src, trs, idx, _cues in jobs:
                    gone = set(idx)
                    before, after = {}, {}
                    for path in (src, trs):           # the original first: the translation stays the newer file
                        if not path:
                            continue
                        with open(path, encoding="utf-8") as f:
                            before[path] = f.read()
                        lines = [c for k, c in enumerate(load_srt(path)) if k not in gone]
                        save_srt(lines, path)
                        with open(path, encoding="utf-8") as f:
                            after[path] = f.read()
                    self.timeline.remember_files(video, before, after)
                    self.timeline.reload_video(video)
                changed = True
        if changed:
            self.timeline.select(None)

    def _tl_revoice(self, chosen):
        """Right click on the timeline → Make the voice again: like the subtitle table's (the open video's
        lines; another video's lines are made again once it is opened)."""
        cur = self.queue.current()
        info = self.timeline.video(cur) if cur else None
        starts = []
        if info is not None:
            want = {k for v, k in chosen if v == cur}
            starts = [a for (a, _b, _t, k) in info.cues if k in want]
        if not starts:
            QMessageBox.information(self, APP_NAME, tr("Open the video of these lines first (click it in the "
                                                       "list), then make their voice again."))
            return
        self.remake_lines(starts)

    def _tl_files_back(self, video, files):
        """Undo / redo of a line deleted on the timeline (a video that is not the open one)."""
        for path in sorted(files, key=lambda f: f.endswith(TR_SRT)):     # the original first
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(files[path])
            except OSError as e:
                log.warning("Could not write back %s: %s", path, e)
        self.timeline.reload_video(video)
        if video == self.queue.current():
            self.editor.reload_keep_place()
            self._cues_to_preview()

    def _undo(self):
        """Ctrl+Z: with the timeline open, whichever came last (a timeline change or a subtitle edit)."""
        tl, ed = self.timeline, self.editor
        if not self.timeline_panel.isVisible():
            ed.undo()
            return
        if ed.undo_stamp() > tl.undo_stamp():
            if ed.undo() and ed.project is not None:
                tl.reload_video(ed.project.video)
        else:
            tl.undo()
        tl.refresh_history()

    def _redo(self):
        tl, ed = self.timeline, self.editor
        if not self.timeline_panel.isVisible():
            ed.redo()
            return
        if ed.redo_stamp() > tl.redo_stamp():
            if ed.redo() and ed.project is not None:
                tl.reload_video(ed.project.video)
        else:
            tl.redo()
        tl.refresh_history()

    def _tl_edit_text(self, video, index):
        """Double-click on a line: its words, in Style, ready to type."""
        self.inspector.setCurrentIndex(1)
        ed = self.style.t_tr if getattr(self.style, "_has_tr", False) else self.style.t_src
        ed.setFocus()
        ed.selectAll()

    def export_joined(self, srt: bool):
        """Export on the editing page: the timeline as one video, or one .srt (and its translation)."""
        from ..steps import timeline as tl_mod
        tl = self.timeline.tl.copy()
        if not tl.clips:
            return
        if not self.get_ffmpeg() or self.runner.busy:
            return
        self.editor._flush()
        self.preview.pause()
        self.preview.set_mix(None, [])
        name = self.edit.name if self.edit else ""
        self.last_kind = "timeline"
        self._run_videos, self._done_videos = tl.videos(), []
        if srt:
            self.runner.run_task(lambda c: self._timeline_done(tl_mod.export_srt(tl, c, name)[-1]),
                                 "Export the timeline's subtitles")
        else:
            self.runner.run_task(lambda c: self._timeline_done(tl_mod.export(tl, c, name=name)), "Export the timeline")

    def _timeline_done(self, path):
        self._timeline_out = path

    def redo_subtitles(self):
        """The ↻ button: the subtitles of this video made again with the settings in Steps."""
        from ..steps import pipeline
        v = self.queue.current()
        if not v:
            return
        if self.runner.busy:
            QMessageBox.information(self, APP_NAME, "Wait for the current work to finish (or stop it) first.")
            return
        s = settings()
        p = Project(v)
        if s["do_subtitles"] and p.is_done("import"):
            p.mark("import", "replaced")               # an .srt of yours was used: make them from the video now
        steps = pipeline.resubs_recipe(p)
        if not steps:
            QMessageBox.information(self, APP_NAME, "Switch on Subtitles or Translate in Steps first.")
            return
        what = " and ".join(x for x, on in (("the subtitles", s["do_subtitles"]),
                                             ("the translation", s["do_translate"])) if on)
        if QMessageBox.question(self, APP_NAME, f"Make {what} of this video again with the settings in Steps?\n"
                                                "Your edits of its lines are replaced.") != QMessageBox.Yes:
            return
        if s["subs_source"] == "ocr" and s["do_subtitles"] and not (p.meta.get("ocr_region") or s["ocr_region"]):
            QMessageBox.information(self, APP_NAME, "Draw the subtitle area on the video first.")
            return
        if not self.get_ffmpeg():
            return
        self.editor._flush()
        p = Project(v)
        p.meta.pop("tr_edited", None)                   # the new translation replaces the edited one
        p.save()
        if s["do_subtitles"] and s["subs_source"] != "ocr":
            from ..steps import transcribe
            transcribe.forget_words(p)                  # asked and confirmed: Whisper listens again from the start
        self.last_kind = "redo"
        self._run_videos, self._done_videos = [v], []
        self.runner.run_videos([v], steps, force=pipeline.RESUBS_FORCE, title="Subtitles again")

    def _names_source(self):
        """For "Find names in the subtitles": the lines of the video open in the table (None: none)."""
        v = self.queue.current()
        if not v or not self.editor.src:
            return None
        self.editor._flush()
        src = [c.text for c in self.editor.src]
        trl = [c.text for c in self.editor.tr] if len(self.editor.tr) == len(src) else None
        lang = Project(v).meta.get("source_lang") or settings()["whisper_language"]
        return src, trl, "" if lang == "auto" else lang, os.path.splitext(os.path.basename(v))[0]

    def _imported(self, video):
        if video == self.queue.current():
            self.editor.load(video)
            self._cues_to_preview()

    def reset_video(self, video):
        if self.runner.busy:
            return
        if QMessageBox.question(self, APP_NAME, "Delete the work files of this video and start it again?\n"
                                                "(Your .srt files and exported videos are kept.)") != QMessageBox.Yes:
            return
        p = Project(video)
        region = p.meta.get("ocr_region")
        p.clear_work()
        if region:
            p.meta = {"video": p.video, "steps": {}, "source_lang": "", "info": {}, "ocr_region": region}
            p.save()
        self.queue.update_item(video=video)
        if video == self.queue.current():
            self.on_video(video)

    # ------------------------------------------------------------ work
    def _after_add(self):
        if self.pages.currentIndex() == 2:
            self._batch_refresh()
        elif self.pages.currentIndex() == 0 and self.queue.videos():
            self.show_work()

    def run_kind(self, kind):
        """Command bar Export: the ticked videos on the Batch page, else the video(s) selected in the list
        (many videos at once is what the Batch page is for). With the timeline on: its videos, made into one."""
        if self.pages.currentIndex() == 1:              # editing: what is on the timeline, as one
            if not self.timeline.tl.clips:
                if not self.timeline_on():
                    self.preview.b_timeline.setChecked(True)
                QMessageBox.information(self, APP_NAME, "Drag the videos onto the timeline first: Run and Export "
                                        "work on what is on the timeline, made into one video.\n\n"
                                        "(To make many videos one by one, use Batch.)")
                return
            if kind == "run" and not confirm(self, "Run the timeline now?\n(Subtitles, translation and narration "
                                                     "that are switched on in Steps.)", "run", "Run"):
                return
            self.start(kind, self.timeline.tl.videos(), joined=True)
            return
        if self.pages.currentIndex() == 2:
            n = len(self.batch.checked())
            if kind == "run" and n and not confirm(self, tr("Run the {a} ticked videos now?").replace("{a}", str(n))
                                                   if n > 1 else tr("Run the ticked video now?"), "run", tr("Run")):
                return
            self.start(kind, self.batch.checked())
        else:
            self.start(kind)

    def set_busy(self, busy):
        if busy and not getattr(self, "_busy_cursor", False):   # work started: a short spin, then the bar shows it
            self._busy_cursor = True
            QApplication.setOverrideCursor(Qt.BusyCursor)

            def done():
                if self._busy_cursor:
                    self._busy_cursor = False
                    QApplication.restoreOverrideCursor()
            QTimer.singleShot(1200, done)
        self.c_export.setEnabled(True)                # while working: Export adds the videos to the run
        self.c_run.setEnabled(True)
        self._run_button(busy)
        self._lock_right(False)                        # nothing is locked: each video keeps the settings it
                                                       # started with (pipeline.frozen_settings), no notice
        self.recipe.set_busy(busy)
        self.batch.set_running(busy)

    def _run_button(self, busy: bool):
        """While working, Run is Stop."""
        self.c_run.key = "stop" if busy else "play"
        self.c_run.setText(tr("Stop") if busy else tr("Run"))
        self.c_run.setToolTip(tr("Stop the work now") if busy else
                              tr("Make everything switched on in Steps (subtitles, translation, narration) without "
                                 "exporting: then look it over and edit it"))
        self.c_run.refresh_icon()

    def _run_or_stop(self):
        if self.runner.busy:
            self._stop_asked()
        else:
            self.run_kind("run")

    def _stop_asked(self):
        """Stop, after a Yes. Always asked (no "Don't ask again": a slip of the finger would throw away a long
        run), No is the default."""
        if not self.runner.busy:
            return
        if not confirm(self, tr("Stop the work now?\n(What is finished is kept: it goes on from there next time.)"),
                       "", tr("Stop"), danger=True):
            return
        self._pending = None                          # stop means stop: nothing waiting starts after it
        self.runner.stop()

    def _lock_right(self, on: bool):
        """Work running: Steps and Style (and dragging the lines / box on the video) are locked, so nothing
        changes what is being made. The tabs can still be looked at."""
        for i in range(self.inspector.count()):
            self.inspector.widget(i).setEnabled(not on)
        self.preview.view.locked = on

    def start(self, kind, videos=None, joined=False, ask=True):
        """kind: 'export' = the whole recipe, 'subs' = only up to the translated .srt files. videos: the batch's
        ticked videos (None = the videos selected in the list). While a run is going on, the videos are added
        to it (same kind of run) or start right after it. ask=False: no Export window (making again)."""
        if not self.get_ffmpeg():
            return
        s = settings()
        if videos is None:
            videos = self.queue.selected_videos()
        videos = [v for v in videos if v]
        if not videos:
            QMessageBox.information(self, APP_NAME, "Add a video first.")
            return
        if s["subs_source"] == "ocr" and (s["do_subtitles"] or kind == "subs"):
            missing = [v for v in videos if not (Project(v).meta.get("ocr_region") or s["ocr_region"])
                       and not Project(v).has_subs()]
            if missing:
                QMessageBox.information(self, APP_NAME, "Draw the subtitle area on the video first "
                                                        "(Subtitles → Select the subtitle area).")
                return
        if joined and self.runner.busy:
            QMessageBox.information(self, APP_NAME, "Wait until the work running now is done (or stop it).")
            return
        if kind == "export" and not ask:
            if s["export_target"] == "srt":
                kind = "subs"
        elif kind == "export":
            with wait_cursor():
                dlg = ExportDialog(self, 1 if joined else len(videos))
            if joined:
                dlg.setWindowTitle(tr("Export the timeline"))
            if not dlg.exec():
                return
            if joined:                                    # the timeline: one video, or one .srt
                self.export_joined(s["export_target"] == "srt")
                return
            if s["export_target"] == "srt":              # Export → Subtitles only: the .srt files, no video
                kind = "subs"
            self.recipe.refresh()
        self.editor._flush()
        upto = {"subs": "translate", "run": "run"}.get(kind)
        if self.runner.busy:                         # working: these join the run, or come right after it
            self._add_to_run(kind, videos, upto)
            return
        self.preview.pause()
        if s["do_voice"] and kind in ("export", "run"):
            self.preview.set_mix(None, [])           # let go of the mix files: the narration may write them
        self._touched, self._run_results = {}, {}
        self.last_kind = kind
        self._run_videos, self._done_videos = list(videos), []
        self.batch.queued(videos)
        title = {"export": "Export", "run": "Run"}.get(kind, "Subtitles") + \
            (f" · {len(videos)} videos" if len(videos) > 1 else "")
        self.runner.run_videos(videos, None, title=title, upto=upto)

    def _add_to_run(self, kind, videos, upto):
        """Export / Run pressed while working: the videos are made after the ones already in the run (with the
        settings as they are when their turn comes), or right after the run when it is another kind of work."""
        if kind == self.last_kind and self.runner.enqueue(list(videos), upto):
            self._run_videos = list(getattr(self, "_run_videos", [])) + list(videos)
            self.batch.queued(videos)
            self.progress.note(tr("Added to the run: {a}").replace("{a}", ", ".join(os.path.basename(v)
                                                                                     for v in videos)))
            return
        old = self._pending
        if old and old[0] == kind:
            videos = old[1] + [v for v in videos if v not in old[1]]
        self._pending = (kind, list(videos))
        self.progress.note(tr("Starts when the work running now is done: {a}")
                           .replace("{a}", ", ".join(os.path.basename(v) for v in videos)))

    def _touch(self, videos):
        """These videos were changed in the window (subtitles, box, area, own settings): if they were already
        made (or are being made) in this run, the end of the run offers to make them again."""
        import time
        now = time.monotonic()
        for v in videos:
            if v:
                self._touched[os.path.abspath(v)] = now

    def _out_of_date(self):
        """Videos of the run that was just made that started before a change (settings, look, subtitles)."""
        started = dict(self.runner.started_at)
        changed = settings().changed_at
        out = []
        for v, t in started.items():
            if self._run_results.get(v) not in ("done", "failed"):
                continue
            if max(changed, self._touched.get(os.path.abspath(v), 0.0)) > t:
                out.append(v)
        return out

    def _when_idle(self, fn, tries=0):
        """Do fn once the work thread is gone ('finished' comes a moment before)."""
        if self.runner.busy:
            if tries < 50:
                QTimer.singleShot(200, lambda: self._when_idle(fn, tries + 1))
            return
        fn()

    def _after_run(self, result):
        """The run is over: videos made before a change are offered to be made again, and videos asked for
        meanwhile (another kind of work) start now."""
        kind = self.last_kind
        again = self._out_of_date() if result != "stopped" and kind in ("export", "run", "subs") else []
        pending, self._pending = self._pending, None
        if again:
            names = "\n".join("• " + os.path.basename(v) for v in again[:8]) + ("\n…" if len(again) > 8 else "")
            ask = tr("You changed things while working. Made before the change ({a}):\n{b}\n\nMake them "
                     "again now? (Only what changed is made again.)").replace("{a}", str(len(again))).replace("{b}", names)
            if confirm(self, ask, "redo_changed", tr("Make again")):
                if pending and pending[0] == kind:
                    again += [v for v in pending[1] if v not in again]
                    pending = None
                self._when_idle(lambda: self.start(kind, again, ask=False))
                if pending:                          # another kind: after that
                    self._pending = pending
                return
        if pending:
            self._when_idle(lambda: self.start(pending[0], pending[1], ask=False))

    def download_model(self, name):
        self.runner.run_task(lambda c: models.download_whisper(name, c), f"Downloading Whisper {name}")

    def get_ffmpeg(self) -> bool:
        """ffmpeg is missing: offer the download (exe) or say how to get it (source run). True = it is there."""
        if ffmpeg.available():
            return True
        import sys
        if not getattr(sys, "frozen", False):
            QMessageBox.warning(self, APP_NAME, str(ffmpeg.FFmpegMissing()))
            return False
        if self.runner.busy:
            return False
        if QMessageBox.question(self, APP_NAME, "FreeSub needs ffmpeg to read and make videos (about 100 MB, "
                                "downloaded once). Download it now?") == QMessageBox.Yes:
            from ..core import ffmpeg_setup
            self.runner.run_task(ffmpeg_setup.install, "Downloading ffmpeg")
        return False

    def download_voice(self):
        if settings()["tts_engine"] == "kokoro":
            from ..tts import kokoro
            self.runner.run_task(kokoro.download_all, "Downloading the Kokoro voices")
            return
        from ..core import addons
        from ..tts import voices

        def work(c):
            if not voices.piper_installed() and addons.can_download():
                addons.install("piper", c)
            if not voices.piper_ready("vi"):
                voices.piper_download("vi", c)
        self.runner.run_task(work, "Downloading the Piper voice")

    def on_video_finished(self, video, result):
        self.queue.update_item(video=video)
        if video == self.queue.current():
            ep = self.editor.project
            if ep is not None and ep.video == os.path.abspath(video):
                self.editor.reload_keep_place()      # same row, scroll and Ctrl+Z as before the run
            else:
                self.editor.load(video)
            self._cues_to_preview()
            self._recipe_changed()
            live = self.last_kind == "dub" and video == getattr(self, "_live_video", None)
            if live and result == "done" and self.preview.live.on:
                self.preview.live.clear()                    # all made: the full mix takes over
            self._update_mix(video)
            if live and result == "done" and self.preview.b_mix.isChecked() and self.preview.mix.ready():
                self.preview.mix.set_enabled(True)
        if video == getattr(self, "_live_video", None):
            self._live_video = None
        if result == "done":
            self._done_videos = getattr(self, "_done_videos", []) + [video]

    def _open_results(self):
        """Open the output folder once, when the whole run is over: the file itself for one video, the shared
        folder for many (one 'freesub_…' folder per video sits next to the videos)."""
        if self.last_kind == "timeline":
            out = getattr(self, "_timeline_out", None)
            self._timeline_out = None
            if out and os.path.isfile(out) and settings()["open_folder_after"]:
                open_folder(os.path.dirname(out), out)
            return
        done = getattr(self, "_done_videos", [])
        if not done or not settings()["open_folder_after"] or self.last_kind in ("dub", "redo", "run"):
            return
        if len(done) == 1:
            p = Project(done[0])
            outs = p.meta.get("outputs", {})
            target = outs.get("video") if self.last_kind == "export" else \
                (outs.get("subs.tr.srt") or outs.get("subs.src.srt"))
            open_folder(p.out_dir, target if target and os.path.isfile(target) else None)
            return
        dirs = {Project(v).out_dir for v in done}
        if len(dirs) == 1:
            open_folder(dirs.pop())
            return
        parents = {os.path.dirname(d) for d in dirs}
        open_folder(parents.pop() if len(parents) == 1 else sorted(dirs)[0])

    def _rearm_mic(self, tries=0):
        """A run is over and the mic is on: hear the narration again exactly as switching the mic off and on
        does, with a new sound stream (after an export the old one could stay silent). Only when the narration
        is made and up to date: nothing is made again by itself here."""
        from ..steps import dub
        pv = self.preview
        if not pv.b_mix.isChecked():
            return
        if self.runner.busy:                      # 'finished' comes a moment before the work thread is gone
            if tries < 25:
                QTimer.singleShot(200, lambda: self._rearm_mic(tries + 1))
            return
        v = self.queue.current()
        if not v:
            return
        p = Project(v)
        log.info("Narration preview after the run: mix ready %s, on %s, live %s, sound %s, writing %s",
                 pv.mix.ready(), pv.mix.on, pv.live.on, pv.engine.active(), getattr(self, "_writing", None) == v)
        if getattr(self, "_writing", None) == v:
            self._writing = None                  # the run is over: nothing is being written any more
        if not (p.is_done("dub", dub.MIX) and dub.track_fresh(p)):
            return
        pv.live.stop()
        pv.mix.set_enabled(False)
        pv.engine.reset()
        self._update_mix(v)                       # the files again (enabled: the mic is on)
        if pv.mix.ready() and not pv.mix.on:
            pv.mix.set_enabled(True)

    def on_finished(self, result, msg):
        QTimer.singleShot(0, self._rearm_mic)
        QTimer.singleShot(0, lambda: self._after_run(result))
        self._open_results()
        self._done_videos = []
        if self.pages.currentIndex() == 0:
            self.home.refresh()
        self.set_busy(False)
        self.recipe.refresh_model()
        self.recipe.refresh_voice()
        if result == "failed":
            QMessageBox.warning(self, APP_NAME, msg)

    # ------------------------------------------------------------ startup / shortcuts / drop
    def _startup_checks(self):
        import sys
        if not ffmpeg.available():
            log.warning("ffmpeg not found")
            if getattr(sys, "frozen", False):
                self.get_ffmpeg()
            else:
                QMessageBox.warning(self, APP_NAME, tr("ffmpeg not found: run setup.bat, or set its folder in "
                                                       "Settings."))
        self.recipe.set_gpu_text("Checking the graphics card…")

        def work():
            try:
                self.sig.gpu_text.emit(gpu_summary())
            except Exception as e:  # noqa: BLE001
                self.sig.gpu_text.emit(f"GPU check failed: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _gpu_text(self, text):
        self.recipe.set_gpu_text(text)
        log.info(text)
        if "GPU support is downloaded" in text and not self.runner.busy:
            self.progress.note(text)

    def _typing(self):
        w = QApplication.focusWidget()
        if isinstance(w, QTableWidget) and w.state() == QTableWidget.EditingState:
            return True
        return isinstance(w, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)) or \
            (w is not None and isinstance(w.parent(), QTableWidget))

    def _shortcuts(self):
        """Keys as in common video editors (CapCut): whoever knows one knows FreeSub's."""
        def sc(key, fn, lists_too=True, timeline=False):
            # lists_too=False: the key is left to a list / table that has the focus (its arrows, Delete)
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)

            def go():
                if self._typing() or (not lists_too and isinstance(QApplication.focusWidget(), QAbstractItemView)):
                    return
                if timeline and not self.timeline_panel.isVisible():
                    return
                fn()
            s.activated.connect(go)
        tl = self.timeline
        sc(Qt.Key_Space, self.preview.toggle)
        sc(Qt.Key_K, self.preview.player.pause)                         # J K L: K stops, L plays
        sc(Qt.Key_L, self.preview.player.play)
        sc(Qt.Key_Left, lambda: self.preview.step_frame(-1), lists_too=False)   # one frame
        sc(Qt.Key_Right, lambda: self.preview.step_frame(1), lists_too=False)
        sc("Shift+Left", lambda: self.preview.seek_rel(-5), lists_too=False)    # a few seconds
        sc("Shift+Right", lambda: self.preview.seek_rel(5), lists_too=False)
        sc(Qt.Key_Comma, lambda: self.preview.step_frame(-1))
        sc(Qt.Key_Period, lambda: self.preview.step_frame(1))
        sc(Qt.Key_Up, lambda: self._jump_point(-1), lists_too=False)    # previous / next cut (or subtitle)
        sc(Qt.Key_Down, lambda: self._jump_point(1), lists_too=False)
        sc(Qt.Key_Home, lambda: self._jump_end(False), lists_too=False)
        sc(Qt.Key_End, lambda: self._jump_end(True), lists_too=False)
        sc(Qt.Key_Escape, self._escape)
        sc("Ctrl+I", self.queue.add_files)                               # import media
        sc("Ctrl+O", self.queue.add_files)
        sc("Ctrl+E", lambda: self.start("export"))
        sc("Ctrl+F", self.editor.open_find)                              # find and replace in the subtitles
        sc("Ctrl+H", self.editor.open_find)
        # timeline
        sc("Ctrl+B", tl.split, timeline=True)
        sc(Qt.Key_Q, lambda: tl.delete_side("left"), timeline=True)
        sc(Qt.Key_W, lambda: tl.delete_side("right"), timeline=True)
        sc(Qt.Key_Delete, tl.delete_selected, lists_too=False, timeline=True)
        sc(Qt.Key_Backspace, tl.delete_selected, lists_too=False, timeline=True)
        sc("Ctrl+Z", self._undo)
        sc("Ctrl+Shift+Z", self._redo)
        sc("Ctrl+Y", self._redo)
        sc("Ctrl+=", lambda: tl.zoom(1.25), timeline=True)
        sc("Ctrl++", lambda: tl.zoom(1.25), timeline=True)
        sc("Ctrl+-", lambda: tl.zoom(1 / 1.25), timeline=True)
        sc("Shift+Z", tl.fit, timeline=True)

    def _escape(self):
        """Esc: stop drawing the area; a line chosen on the timeline: back to the whole video."""
        if self.preview.b_area.isChecked():
            self.preview.b_area.setChecked(False)
        elif getattr(self, "_line_target", None):
            self.timeline.select(None)

    def _jump_point(self, way: int):
        """Up / Down: the previous / next cut on the timeline; without the timeline, the previous / next subtitle."""
        if self.timeline_panel.isVisible() and self.timeline.tl.clips:
            t = self.timeline.playhead
            tl = self.timeline.tl
            points = sorted({round(tl.start_of(i), 3) for i in range(len(tl.clips) + 1)})
        else:
            if not self.preview.path:
                return
            t = self.preview.player.position() / 1000
            points = sorted({round(c.start, 3) for c in self.editor.shown_cues()} | {0.0})
        if way < 0:
            pick = [p for p in points if p < t - 0.05]
            target = pick[-1] if pick else 0.0
        else:
            pick = [p for p in points if p > t + 0.05]
            if not pick:
                return
            target = pick[0]
        if self.timeline_panel.isVisible() and self.timeline.tl.clips:
            self.timeline.seek_to(target)
        else:
            self.preview.seek(target)

    def _jump_end(self, end: bool):
        if self.timeline_panel.isVisible() and self.timeline.tl.clips:
            self.timeline.seek_to(self.timeline.tl.total() - 0.05 if end else 0.0)
        elif self.preview.path:
            self.preview.seek(max(0.0, self.preview.duration - 0.05) if end else 0.0)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if self.pages.currentIndex() == 2:              # batch: add, stay here
            self.queue.add_paths(paths)
            self._batch_refresh()
            return
        if self.pages.currentIndex() == 0:
            self.open_videos(paths)
            return
        if len(paths) == 1 and paths[0].lower().endswith(".srt") and self.queue.current():
            from ..steps.transcribe import import_srt
            import_srt(Project(self.queue.current()), paths[0])
            self._imported(self.queue.current())
            return
        self.queue.add_paths(paths)

    # ------------------------------------------------------------ window state
    def restore(self):
        s = settings()
        try:
            if s["window_geometry"]:
                self.restoreGeometry(QByteArray.fromBase64(s["window_geometry"].encode()))
            for key, sp in (("split_h5", self.split_h), ("split_m1", self.split_mid), ("split_w2", self.split_center)):
                if s.get(key):
                    sp.restoreState(QByteArray.fromBase64(s[key].encode()))
                    self._layout_restored = True
        except Exception:  # noqa: BLE001
            pass

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_layout_restored", False) and not getattr(self, "_layout_set", False):
            self._layout_set = True                  # first start: the table / timeline is a third of the height
            QTimer.singleShot(0, lambda: self.split_mid.setSizes(
                [max(1, self.split_mid.height() * 2 // 3), max(150, self.split_mid.height() // 3)]))
        if self.frameless is not None and not getattr(self, "_native_done", False):
            self._native_done = True
            self.frameless.native_ready()

    def nativeEvent(self, event_type, message):
        if self.frameless is not None:
            try:
                r = self.frameless.native(event_type, message)
                if r is not None:
                    return r
            except Exception:  # noqa: BLE001  (never let the title bar break the window)
                pass
        return super().nativeEvent(event_type, message)

    def restart_app(self):
        """Close (asking first if work is running) and open FreeSub again, e.g. after a language change."""
        self._restart = True
        if not self.close():
            self._restart = False
            return
        import sys
        from PySide6.QtCore import QProcess
        args = [] if getattr(sys, "frozen", False) else [os.path.abspath(sys.argv[0])]   # the exe, or python main.py
        if getattr(self, "_single", None) is not None:
            self._single.close()                     # or the new FreeSub would hand over to this one and quit
        QProcess.startDetached(sys.executable, args)

    def closeEvent(self, e):
        if self.runner.busy:
            r = QMessageBox.question(self, APP_NAME, "Work is still running. Stop it and quit?\n"
                                                    "(It goes on from where it stopped next time.)")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.runner.stop()
            if self.runner.thread:
                self.runner.thread.join(timeout=5)
        elif settings()["confirm_close"] and not getattr(self, "_restart", False):
            box = QMessageBox(QMessageBox.Question, APP_NAME, tr("Close FreeSub?"),
                              QMessageBox.Yes | QMessageBox.No, self)
            box.setDefaultButton(QMessageBox.No)
            again = QCheckBox("Don't ask again")
            box.setCheckBox(again)
            if box.exec() != QMessageBox.Yes:
                e.ignore()
                return
            if again.isChecked():
                settings().update(confirm_close=False)
        self.proxies.stop_all()
        self.editor._flush()
        self.preview.player.stop()
        settings().update(window_geometry=bytes(self.saveGeometry().toBase64()).decode(),
                          split_h5=bytes(self.split_h.saveState().toBase64()).decode(),
                          split_m1=bytes(self.split_mid.saveState().toBase64()).decode(),
                          split_w2=bytes(self.split_center.saveState().toBase64()).decode())
        self.log_bridge.close()
        super().closeEvent(e)
