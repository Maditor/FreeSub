"""Main window: videos on the left, the preview with its subtitle table in the middle, the recipe (what
Export does) on the right, progress and log at the bottom. Everything for one video is visible at once."""
import logging
import os
import threading

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION, resource_path, settings
from ..core import ffmpeg, models
from ..core.project import Project
from . import icons, theme
from .cut import CutPanel
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
from .widgets import CommandButton, separator, IconButton, label

log = logging.getLogger(__name__)


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
        self.header_info = label("", muted=True)
        self.b_home = CommandButton("grid", "Projects", "Every video worked on before")
        self.b_batch = CommandButton("stack", "Batch", "Many videos, one after another")
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
        self.cut = CutPanel(lambda: self.preview.player.position() / 1000)
        self.style = StylePanel()
        self.home = Home()
        self.editor = Editor()
        self.recipe = Recipe()
        for w in (self.c_export, separator(), self.b_theme, self.b_settings):
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
        self.split_center = QSplitter(Qt.Vertical)
        self.split_center.addWidget(pv)
        self.split_center.addWidget(_panel(self.editor))
        self.split_center.setStretchFactor(0, 3)
        self.split_center.setStretchFactor(1, 2)
        self.split_center.setHandleWidth(8)
        self.split_center.setSizes([520, 260])
        self.split_center.setChildrenCollapsible(False)     # the subtitle table can't vanish under the video
        pv.setMinimumHeight(220)
        self.split_center.widget(1).setMinimumHeight(150)

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
        left = _panel(left_scroll)
        left.setFixedWidth(250)
        # Inspector (right): the recipe steps, the subtitle style and the cut, as tabs
        self.inspector = QTabWidget()
        self.inspector.setDocumentMode(True)
        for w, name in ((self.style, "Style"), (self.cut, "Cut")):
            w.layout().itemAt(0).widget().hide()             # the tab says it already
        style_scroll = QScrollArea()
        style_scroll.setWidgetResizable(True)
        style_scroll.setFrameShape(QFrame.NoFrame)
        style_box = QWidget()
        sbl = QVBoxLayout(style_box)
        sbl.setContentsMargins(4, 12, 4, 4)
        sbl.addWidget(self.style)
        sbl.addStretch(1)
        style_scroll.setWidget(style_box)
        cut_box = QWidget()
        cbl = QVBoxLayout(cut_box)
        cbl.setContentsMargins(4, 12, 4, 4)
        cbl.addWidget(self.cut)
        cbl.addStretch(1)
        steps_box = QWidget()
        stl = QVBoxLayout(steps_box)
        stl.setContentsMargins(0, 10, 0, 0)
        stl.addWidget(self.recipe)
        self.inspector.addTab(steps_box, "Steps")
        self.inspector.addTab(style_scroll, "Style")
        self.inspector.addTab(cut_box, "Cut")
        right = self.right = _panel(self.inspector, (12, 6, 12, 12))
        right.setMinimumWidth(420)
        # the left column sits OUTSIDE the splitter: fixed width, always flush with the video (a splitter
        # leaves a gap next to a fixed-width widget when a saved layout gives it more room)
        self.split_h.addWidget(self.split_center)
        self.split_h.addWidget(right)
        self.split_h.setStretchFactor(0, 1)
        self.split_h.setStretchFactor(1, 0)
        self.split_h.setHandleWidth(8)
        self.split_h.setSizes([900, 440])
        self.split_h.setChildrenCollapsible(False)
        self.work_page = QWidget()
        wl = QHBoxLayout(self.work_page)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)                            # same as the splitter handle: even gaps everywhere
        wl.addWidget(left)
        wl.addWidget(self.split_h, 1)

        self.progress = ProgressPanel(self.runner)
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
        self.split_v = QSplitter(Qt.Vertical)
        self.split_v.addWidget(self.pages)
        self.split_v.addWidget(self.progress)
        self.split_v.setStretchFactor(0, 1)
        self.split_v.setHandleWidth(8)
        self.split_v.setSizes([840, 40])

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 0, 10, 10)
        bl.addWidget(self.split_v)
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
        self.b_batch.clicked.connect(self.show_batch)
        self.queue.b_add.clicked.connect(lambda: (self.queue.add_files(), self._after_add()))
        self.queue.b_folder.clicked.connect(lambda: (self.queue.add_folder(), self._after_add()))
        self.c_export.clicked.connect(lambda: self.run_kind("export"))
        for b in (self.batch.b_add, self.batch.b_folder, self.batch.b_subs, self.batch.b_run):
            b.hide()                                         # the command bar has them
        self.batch.open_requested.connect(self._batch_open)
        self.batch.run_requested.connect(lambda kind, videos: self.start(kind, videos))
        self.batch.stop_requested.connect(self.runner.stop)
        self.batch.add_files.connect(lambda: (self.queue.add_files(), self._batch_refresh()))
        self.batch.add_folder.connect(lambda: (self.queue.add_folder(), self._batch_refresh()))
        self.batch.remove_requested.connect(self._batch_remove)
        self.runner.video_progress.connect(self.batch.on_progress)
        self.runner.step.connect(self.batch.on_step)
        self.runner.step.connect(self._step_event)
        self.runner.video_finished.connect(self.batch.on_video_finished)
        self.home.open_requested.connect(self.open_videos)
        self.home.deleted.connect(self._project_deleted)
        self.cut.changed.connect(lambda: self.queue.update_item(video=self.queue.current()))
        self.b_settings.clicked.connect(lambda: self.open_settings())
        self.queue.current_changed.connect(self.on_video)
        self.queue.imported.connect(self._imported)
        self.queue.reset_requested.connect(self.reset_video)
        self.editor.import_requested.connect(lambda kind: self.queue.import_srt(None, kind))
        self.editor.redo_requested.connect(self.redo_subtitles)
        self.editor.seek.connect(self.preview.seek)
        self.editor.cues_changed.connect(self._cues_to_preview)
        self.preview.position_changed.connect(self.editor.highlight)
        self.preview.region_selected.connect(self._region)
        self.preview.end_reached.connect(self._preview_end)
        self.preview.seek_hook = self._preview_seek
        self.recipe.export_requested.connect(self.start)
        self.recipe.area_requested.connect(self._ask_area)
        self.recipe.keys_requested.connect(lambda: self.open_settings("translation"))
        self.recipe.download_whisper.connect(self.download_model)
        self.recipe.download_voice.connect(self.download_voice)
        self.recipe.changed.connect(self._recipe_changed)
        self.recipe.mix_changed.connect(lambda: self.preview.mix.refresh_levels())
        self.recipe.narration_preview.connect(self.preview_narration)
        self.preview.b_mix.toggled.connect(self._mic)
        self.preview.b_mix.customContextMenuRequested.connect(self._mic_menu)
        self.recipe.one.toggled.connect(lambda _v: self.editor._refill_later())     # the Voice column
        self.style.changed.connect(self._recipe_changed)
        self.style.area_requested.connect(self._ask_area)
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
        dlg = SettingsDialog(self, tab)
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
        total = sum(self.split_v.sizes())
        want = 230 if on else 40
        self.split_v.setSizes([max(300, total - want), want])

    # ------------------------------------------------------------ pages
    def show_home(self):
        self.preview.pause()
        self.editor._flush()
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
        self.header_info.setText("")

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

    def show_work(self):
        self._right_to(False)
        self._run_buttons(True)
        self.pages.setCurrentIndex(1)
        v = self.queue.current()
        self.header_info.setText(os.path.basename(v) if v else "")

    def open_videos(self, paths):
        """Open projects: add them to the list on the left, select the first, show the work page."""
        before = set(self.queue.videos())
        self.queue.add_paths(paths)
        target = next((os.path.abspath(p) for p in paths if os.path.isfile(p)), None)
        if target is None:                    # a folder: select the first video that came from it
            target = next((v for v in self.queue.videos() if v not in before), None)
        if target:
            for i in range(self.queue.list.count()):
                if self.queue.list.item(i).data(Qt.UserRole) == target:
                    self.queue.list.setCurrentRow(i)
                    break
            Project(target).touch()
        if self.queue.videos():
            self.show_work()

    def _project_deleted(self, video):
        if video == self.queue.current():          # let go of its files, so the folder can move to the Trash
            self._live_off()
            self.preview.mix.clear()
            self.preview.load("")
        for i in range(self.queue.list.count()):
            if self.queue.list.item(i).data(Qt.UserRole) == video:
                self.queue.list.takeItem(i)
                break

    # ------------------------------------------------------------ the selected video
    def on_video(self, video):
        if not video:
            self.preview.load("")
            self.editor.load("")
            self.cut.load("", 0)
            self.header_info.setText("")
            return
        self._live_off()
        info = self.queue.info_for(video)
        self.cut.load(video, info.get("duration") or 0)
        self._show_video(video, info)
        self._update_mix(video)
        self.header_info.setText(os.path.basename(video))
        self.editor.load(video)
        self._cues_to_preview()
        self._recipe_changed()

    def _update_mix(self, video):
        """Give the preview this video's narration mix (if it was made) for live listening."""
        from ..steps import dub
        if not video:
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
        from ..steps import dub
        p = Project(v)
        if self.runner.busy:
            if getattr(self, "_live_video", None) == v:          # being made right now: listen along
                pv.live.start(p.path(dub.LIVE))
                return
            QMessageBox.information(self, APP_NAME, "Wait for the current work to finish (or stop it) first.")
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

    def _live_off(self):
        """Another video was opened: stop hearing (and lighting) the narration of the last one."""
        pv = self.preview
        pv.live.clear()
        pv.mix.set_enabled(False)
        pv.b_mix.blockSignals(True)
        pv.b_mix.setChecked(False)
        pv.b_mix.blockSignals(False)

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
        self.preview.set_cues(self.editor.shown_cues())

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
        box = bool(region) and s["sub_box"]
        self.preview.set_region(region, show=ocr or s["sub_box"], box_mode=box)
        self.style.set_area_state(region)
        self.recipe.set_area_state(region)
        self.preview.view.subs.update()

    def _ask_area(self):
        if not self.queue.current():
            QMessageBox.information(self, APP_NAME, "Add a video first, then draw the area on it.")
            return
        self.preview.start_area()

    def _region(self, region):
        settings().update(ocr_region=region)            # remembered for the next videos too
        v = self.queue.current()
        if v:
            p = Project(v)
            p.meta["ocr_region"] = region
            p.save()
        log.info("Subtitle area: %s", region)
        self._recipe_changed()

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
        self.last_kind = "redo"
        self._run_videos, self._done_videos = [v], []
        self.runner.run_videos([v], steps, force=pipeline.RESUBS_FORCE, title="Subtitles again")

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
        (many videos at once is what the Batch page is for)."""
        if self.pages.currentIndex() == 2:
            self.start(kind, self.batch.checked())
        else:
            self.start(kind)

    def set_busy(self, busy):
        self.c_export.setEnabled(not busy)
        self.recipe.set_busy(busy)
        self.batch.set_running(busy)

    def start(self, kind, videos=None):
        """kind: 'export' = the whole recipe, 'subs' = only up to the translated .srt files. videos: the batch's
        ticked videos (None = the videos selected in the list)."""
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
        if kind == "export":
            if not ExportDialog(self, len(videos)).exec():
                return
            if s["export_target"] == "srt":              # Export → Subtitles only: the .srt files, no video
                kind = "subs"
            self.recipe.refresh()
        self.editor._flush()
        self.preview.pause()
        if s["do_voice"] and kind == "export":
            self.preview.set_mix(None, [])           # let go of the mix files: the narration may write them
        self.last_kind = kind
        self._run_videos, self._done_videos = list(videos), []
        self.batch.queued(videos)
        title = ("Export" if kind == "export" else "Subtitles") + (f" · {len(videos)} videos" if len(videos) > 1 else "")
        self.runner.run_videos(videos, None, title=title, upto="translate" if kind == "subs" else None)

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
        done = getattr(self, "_done_videos", [])
        if not done or not settings()["open_folder_after"] or self.last_kind in ("dub", "redo"):
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

    def on_finished(self, result, msg):
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
                self.progress.text.setText("ffmpeg not found: run setup.bat, or set its folder in Settings.")
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
            self.progress.text.setText(text)

    def _typing(self):
        w = QApplication.focusWidget()
        if isinstance(w, QTableWidget) and w.state() == QTableWidget.EditingState:
            return True
        return isinstance(w, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)) or \
            (w is not None and isinstance(w.parent(), QTableWidget))

    def _shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(lambda: None if self._typing() else fn())
        sc(Qt.Key_Space, self.preview.toggle)
        sc(Qt.Key_Left, lambda: self.preview.seek_rel(-5))
        sc(Qt.Key_Right, lambda: self.preview.seek_rel(5))
        sc(Qt.Key_Comma, lambda: self.preview.step_frame(-1))
        sc(Qt.Key_Period, lambda: self.preview.step_frame(1))
        sc(Qt.Key_Escape, lambda: self.preview.b_area.setChecked(False))
        sc("Ctrl+O", self.queue.add_files)
        sc("Ctrl+E", lambda: self.start("export"))

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
            for key, sp in (("split_h3", self.split_h), ("split_c2", self.split_center)):
                if s.get(key):
                    sp.restoreState(QByteArray.fromBase64(s[key].encode()))
        except Exception:  # noqa: BLE001
            pass
        a, b = self.split_center.sizes()                    # an old saved layout may have hidden the table
        if b < 150:
            self.split_center.setSizes([max(220, a + b - 260), 260])
        if self.progress.b_log.isChecked():
            self._log_toggled(True)

    def showEvent(self, e):
        super().showEvent(e)
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
            box = QMessageBox(QMessageBox.Question, APP_NAME, "Close FreeSub?",
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
                          split_h3=bytes(self.split_h.saveState().toBase64()).decode(),
                          split_c2=bytes(self.split_center.saveState().toBase64()).decode())
        self.log_bridge.close()
        super().closeEvent(e)
