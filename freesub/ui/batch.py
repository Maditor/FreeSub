"""Batch page: every video of the list as a card, run one after another with the shared recipe (right
column) or with a video's own settings (gear on its card). The output folder opens once, when all are done."""
import os
import threading

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from ..i18n import tr
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core.overrides import describe
from ..core.project import Project
from ..core.subtitle import fmt_clock
from . import icons, theme
from .widgets import IconButton, button, label, row

THUMB = QSize(256, 144)
CHIPS = [("subs", "cc", "Subtitles"), ("tr", "translate", "Translation"), ("voice", "mic", "Narration"),
         ("export", "export", "Video")]
STEP_CHIP = {"audio": "subs", "separate": "subs", "transcribe": "subs", "ocr": "subs", "import": "subs",
             "translate": "tr", "dub": "voice", "export": "export"}


class _Sig(QObject):
    thumb = Signal(str, str)


class VideoCard(QFrame):
    open_requested = Signal(str)
    settings_requested = Signal(str)
    remove_requested = Signal(str)
    toggled = Signal()

    def __init__(self, video: str, info: dict):
        super().__init__()
        self.setObjectName("Card")
        self.video = video
        self.setFixedWidth(THUMB.width() + 24)
        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.setToolTip("Run this video")
        self.name = QLabel()
        self.name.setObjectName("CardTitle")
        self.name.setToolTip(video)
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(os.path.basename(video), Qt.ElideMiddle, THUMB.width() - 70))
        self.b_set = IconButton("settings", "Settings for this video only", size=16)
        self.b_rm = IconButton("cross", "Take it out of the list", size=16)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setCursor(Qt.PointingHandCursor)
        self.thumb.setToolTip("Double-click to open it in the editor")
        self.thumb.setStyleSheet(f"background: {theme.colors()['video']}; border-radius: 6px;")
        self._pm = None
        self.badge = QLabel(self.thumb)
        self.badge.setStyleSheet("background: rgba(0,0,0,0.65); color: white; border-radius: 4px; padding: 1px 5px;"
                                 "font-size: 11px;")
        self.badge.setText(fmt_clock(info.get("duration") or 0).split(".")[0] if info.get("duration") else "")
        self.badge.adjustSize()
        self.badge.move(THUMB.width() - self.badge.width() - 6, THUMB.height() - self.badge.height() - 6)
        self.badge.setVisible(bool(info.get("duration")))
        self.chip = {}
        chips = QHBoxLayout()
        chips.setSpacing(4)
        for key, ic, tip in CHIPS:
            lb = QLabel()
            lb.setToolTip(tip)
            lb.setFixedSize(20, 20)
            self.chip[key] = lb
            chips.addWidget(lb)
        chips.addSpacing(6)
        self.state = label("", muted=True)
        chips.addWidget(self.state, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self.check)
        head.addWidget(self.name, 1)
        head.addWidget(self.b_set)
        head.addWidget(self.b_rm)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(self.thumb)
        lay.addLayout(chips)
        lay.addWidget(self.bar)
        self.b_set.clicked.connect(lambda: self.settings_requested.emit(self.video))
        self.b_rm.clicked.connect(lambda: self.remove_requested.emit(self.video))
        self.check.toggled.connect(lambda _v: self.toggled.emit())
        self.thumb.mouseDoubleClickEvent = lambda _e: self.open_requested.emit(self.video)
        self.states = {}
        self.refresh()

    # ---- look
    def set_thumb(self, path):
        pm = QPixmap(path)
        if not pm.isNull():
            self._pm = pm
            self._show_thumb()

    def _show_thumb(self):
        if self._pm is None:
            return
        size = self.thumb.size()
        self.thumb.setPixmap(self._pm.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                             .copy(0, 0, size.width(), size.height()))

    def set_width(self, w: int):
        """Cards share the row: the picture keeps 16:9."""
        if self.width() == w and self.thumb.width() == w - 24:
            return
        self.setFixedWidth(w)
        tw = w - 24
        self.thumb.setFixedSize(tw, round(tw * 9 / 16))
        self.badge.move(tw - self.badge.width() - 6, self.thumb.height() - self.badge.height() - 6)
        self.name.setText(self.name.fontMetrics().elidedText(os.path.basename(self.video), Qt.ElideMiddle, tw - 70))
        self._show_thumb()

    def refresh(self):
        """Step states from the project (what is already done), the own-settings mark."""
        p = Project(self.video)
        st = p.meta.get("steps", {})
        for key in self.chip:
            names = [n for n, k in STEP_CHIP.items() if k == key]
            vals = [st.get(n, {}).get("status") for n in names]
            self.states[key] = "done" if "done" in vals else "failed" if "failed" in vals else ""
        over = p.meta.get("overrides") or {}
        self.b_set.setToolTip("Own settings: " + describe(over) if over else "Settings for this video only")
        self._own = bool(over)
        self.paint_chips()
        if not self.state.text() or self.state.text() in [tr(t) for t in ("Done", "Exported", "Subtitles ready",
                                                                            "Not started")]:
            self.state.setText("Exported" if self.states.get("export") == "done" else
                               "Subtitles ready" if self.states.get("subs") == "done" else "Not started")

    def paint_chips(self):
        c = theme.colors()
        col = {"done": c["ok"], "failed": c["err"], "running": c["accent"], "": c["faint"]}
        for key, ic, tip in CHIPS:
            state = self.states.get(key, "")
            self.chip[key].setPixmap(icons.pixmap(ic, col.get(state, c["faint"]), 18))
            self.chip[key].setToolTip(f"{tip}: {state or 'not done'}")
        self.b_set.tint = "accent" if self._own else None
        self.b_set.refresh_icon()

    # ---- while running
    def step(self, sid, state, text):
        key = STEP_CHIP.get(sid)
        if key:
            self.states[key] = {"start": "running", "done": "done", "failed": "failed"}.get(state, "")
            self.paint_chips()
        if state == "start":
            self.state.setText(text)
            self.bar.setVisible(True)
        elif state == "failed":
            self.state.setText("Failed")
            self.state.setToolTip(text)

    def progress(self, frac, text):
        self.bar.setVisible(True)
        if frac >= 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        else:
            self.bar.setRange(0, 0)
        if text:
            self.state.setText(text if len(text) < 34 else text[:32] + "…")
            self.state.setToolTip(text)

    def waiting(self):
        self.state.setText("Waiting")
        self.state.setToolTip("")

    def finished(self, result):
        self.bar.setVisible(False)
        self.state.setText({"done": "Done", "failed": "Failed", "stopped": "Stopped"}.get(result, result))
        self.refresh()


class BatchPage(QWidget):
    open_requested = Signal(str)          # open a video in the editor
    run_requested = Signal(str, list)     # "export" | "subs", videos
    stop_requested = Signal()
    add_files = Signal()
    add_folder = Signal()
    remove_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.sig = _Sig()
        self.sig.thumb.connect(self._thumb)
        self.cards = {}
        self.order = []
        title = label("Batch", name="PanelTitle")
        self.count = label("", muted=True)
        self.all = QCheckBox("All")
        self.all.setChecked(True)
        self.all.toggled.connect(self._check_all)
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll_vp = None
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.grid_box)
        self.scroll.viewport().installEventFilter(self)     # re-flow the cards whenever the room changes
        self.empty = label("Add videos or a whole folder: they are made one after another, each with the "
                           "settings on the right (or its own: the gear on a video).", muted=True, wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.b_add = button("Add videos", self.add_files.emit, primary=True)
        self.b_folder = button("Add a folder", self.add_folder.emit)
        self.summary = label("", muted=True)
        self.b_subs = button("Subtitles only", lambda: self.run_requested.emit("subs", self.checked()))
        self.b_subs.setToolTip("Make the .srt files (and the translation) for the ticked videos")
        self.b_run = button("Export all", lambda: self.run_requested.emit("export", self.checked()), primary=True)
        self.b_run.setObjectName("Big")
        self.b_stop = button("Stop", self.stop_requested.emit)
        self.b_stop.setVisible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(row(title, self.count, None, self.all))
        lay.addWidget(self.scroll, 1)
        lay.addWidget(self.empty, 1)
        lay.addWidget(row(self.b_add, self.b_folder, None, self.summary, self.b_subs, self.b_stop, self.b_run))
        self._cols = 0

    # ---- videos
    def set_videos(self, videos, infos):
        for v in list(self.cards):
            if v not in videos:
                self.cards.pop(v).deleteLater()
        for v in videos:
            if v not in self.cards:
                card = VideoCard(v, infos(v))
                card.open_requested.connect(self.open_requested)
                card.settings_requested.connect(self._own_settings)
                card.remove_requested.connect(self.remove_requested)
                card.toggled.connect(self._counts)
                self.cards[v] = card
                p = Project(v)
                if p.has("thumb.jpg"):
                    card.set_thumb(p.thumb)
                else:
                    threading.Thread(target=self._make_thumb, args=(v,), daemon=True).start()
            else:
                self.cards[v].refresh()
        self.order = list(videos)
        self._layout(force=True)
        self._counts()

    def _make_thumb(self, v):
        try:
            path = Project(v).make_thumb()
        except Exception:  # noqa: BLE001
            path = None
        if path:
            self.sig.thumb.emit(v, path)

    def _thumb(self, v, path):
        if v in self.cards:
            self.cards[v].set_thumb(path)

    def checked(self):
        return [v for v in self.order if v in self.cards and self.cards[v].check.isChecked()]

    def _check_all(self, on):
        for c in self.cards.values():
            c.check.setChecked(on)

    def _own_settings(self, v):
        from .video_settings import VideoSettingsDialog
        if VideoSettingsDialog(v, self).exec():
            self.cards[v].refresh()

    def _counts(self):
        n = len(self.order)
        done = sum(1 for v in self.order if self.cards[v].states.get("export") == "done")
        own = sum(1 for v in self.order if self.cards[v]._own)
        self.count.setText(f"{n} video{'s' if n != 1 else ''}" if n else "")
        bits = [f"{len(self.checked())} ticked"] if n else []
        if done:
            bits.append(f"{done} exported")
        if own:
            bits.append(f"{own} with own settings")
        self.summary.setText(" · ".join(bits))
        self.scroll.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        for b in (self.b_run, self.b_subs):
            b.setEnabled(bool(self.checked()) and not self.b_stop.isVisible())

    # ---- grid that follows the width: as many cards per row as fit, spread over the whole width
    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.scroll.viewport() and e.type() == QEvent.Resize:
            self._layout()
        return False

    def showEvent(self, e):
        super().showEvent(e)
        self._layout(force=True)

    def _layout(self, force=False):
        w = self.scroll.viewport().width()
        gap, smallest = 12, THUMB.width() + 24
        cols = max(1, (w + gap) // (smallest + gap))
        card = max(smallest, (w - gap * (cols - 1)) // cols)
        for c in self.cards.values():
            c.set_width(card)
        if cols == self._cols and not force:
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for i, v in enumerate(self.order):
            self.grid.addWidget(self.cards[v], i // cols, i % cols)

    # ---- the run
    def set_running(self, running):
        self.b_stop.setVisible(running)
        for w in (self.b_add, self.b_folder, self.all):
            w.setEnabled(not running)
        for c in self.cards.values():               # the gear stays: changes count for videos not started yet
            c.b_rm.setEnabled(not running)
            c.check.setEnabled(not running)
        self._counts()

    def queued(self, videos):
        for v in videos:
            if v in self.cards:
                self.cards[v].waiting()

    def on_step(self, v, sid, state, text):
        if v in self.cards:
            self.cards[v].step(sid, state, text)

    def on_progress(self, v, frac, text):
        if v in self.cards:
            self.cards[v].progress(frac, text)

    def on_video_finished(self, v, result):
        if v in self.cards:
            self.cards[v].finished(result)
            self.scroll.ensureWidgetVisible(self.cards[v])
        self._counts()

    def apply_theme(self):
        for c in self.cards.values():
            c.thumb.setStyleSheet(f"background: {theme.colors()['video']}; border-radius: 6px;")
            c.paint_chips()
