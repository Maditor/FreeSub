"""Bottom bar: current video, a chip per step, progress, elapsed / left, Pause / Stop, collapsible log."""
import os

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from ..config import settings
from ..core.jobs import fmt_eta, fmt_secs
from ..steps.pipeline import STEPS
from . import icons, theme
from .widgets import IconButton

SHORT = {"audio": "Audio", "separate": "Separate voice", "transcribe": "Speech to SRT", "ocr": "Read subtitles",
         "translate": "Translate",
         "dub": "Narration", "export": "Export"}


class Chip(QWidget):
    """Step name with a small state icon: ring = waiting, dot = running, check = done, cross = failed."""
    ICON = {"idle": "ring", "start": "dot", "done": "check", "failed": "cross", "stopped": "square"}
    NAME = {"idle": "ChipIdle", "start": "ChipRun", "done": "ChipDone", "failed": "ChipFail", "stopped": "ChipFail"}
    COLOR = {"idle": "faint", "start": "accent", "done": "ok", "failed": "err", "stopped": "err"}

    def __init__(self, text):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.icon = QLabel()
        self.text = QLabel(text)
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self.state = "idle"
        icons.track(self)
        self.set_state("idle")

    def set_state(self, state):
        self.state = state if state in self.ICON else "idle"
        self.refresh_icon()
        self.text.setObjectName(self.NAME[self.state])
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)

    def refresh_icon(self):
        c = theme.colors()[self.COLOR[self.state]]
        self.icon.setPixmap(icons.pixmap(self.ICON[self.state], c, 14))


class ProgressPanel(QFrame):
    def __init__(self, runner):
        super().__init__()
        self.setObjectName("Panel")
        self.runner = runner
        self.chips = {}
        self.frac = 0.0

        self.title = QLabel("Ready")
        self.title.setStyleSheet("font-weight: 600;")
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(3)
        self.bar.setFixedWidth(220)
        self.text = QLabel("")
        self.text.setProperty("muted", True)
        self.text.setMinimumWidth(80)
        from PySide6.QtWidgets import QSizePolicy
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # long messages never widen the window
        self.clock = QLabel("")
        self.clock.setProperty("muted", True)
        self.b_pause = IconButton("pause", "Pause")
        self.b_stop = IconButton("stop", "Stop")
        self.b_log = IconButton("log", "Show / hide the log", checkable=True)
        self.b_pause.setEnabled(False)
        self.b_stop.setEnabled(False)

        # one slim row, like a Windows status bar: state · steps · what it is doing · progress · time · buttons
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.title)
        top.addLayout(self.chip_row)
        top.addSpacing(8)
        top.addWidget(self.text, 1)
        top.addWidget(self.bar)
        top.addWidget(self.clock)
        top.addWidget(self.b_pause)
        top.addWidget(self.b_stop)
        top.addWidget(self.b_log)

        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMinimumHeight(120)
        self.log.setVisible(bool(settings()["log_open"]))
        self.b_log.setChecked(bool(settings()["log_open"]))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 8, 4)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.log, 1)

        self.b_pause.clicked.connect(self._pause)
        self.b_stop.clicked.connect(runner.stop)
        self.b_log.toggled.connect(self._toggle_log)
        runner.started.connect(self._started)
        runner.progress.connect(self._progress)
        runner.status.connect(lambda t: self.text.setText(t))
        runner.step.connect(self._step)
        runner.video_started.connect(self._video)
        runner.finished.connect(self._finished)
        runner.paused_changed.connect(self._paused)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.clear_timer = QTimer(self)               # "Done" goes away by itself after a while
        self.clear_timer.setSingleShot(True)
        self.clear_timer.setInterval(10_000)
        self.clear_timer.timeout.connect(self.reset)

    def reset(self):
        """Back to the idle bar: Ready, no steps, no time, empty progress."""
        self.clear_timer.stop()
        self._clear_chips()
        self.title.setText("Ready")
        self.text.setText("")
        self.clock.setText("")
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
        self.bar.setValue(0)

    # ------------------------------------------------------------ log
    def add_log(self, line: str, level: int):
        c = theme.colors()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(c["err"] if level >= 40 else c["warn"] if level >= 30 else c["muted"]))
        cur = self.log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(line + "\n", fmt)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _toggle_log(self, on):
        self.log.setVisible(on)
        settings().update(log_open=on)

    # ------------------------------------------------------------ job events
    def _clear_chips(self):
        while self.chip_row.count():
            w = self.chip_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.chips = {}

    def _set_chip(self, sid, state):
        chip = self.chips.get(sid)
        if chip:
            chip.set_state(state)

    def _started(self, title, steps):
        self.clear_timer.stop()
        self._clear_chips()
        for sid in steps:
            chip = Chip(SHORT.get(sid, sid))
            self.chip_row.addWidget(chip)
            self.chips[sid] = chip
        self.title.setText(title)
        self.text.setText("")
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.frac = 0.0
        self.b_pause.setEnabled(True)
        self.b_stop.setEnabled(True)
        self.timer.start()
        self._tick()

    def _video(self, video, i, n):
        name = os.path.basename(video)
        self.title.setText(f"{i + 1}/{n} · {name}" if n > 1 else name)
        for sid in self.chips:
            self._set_chip(sid, "idle")

    def _step(self, _video, sid, state, text):
        if sid not in self.chips and state == "start":          # a step the video needed on its own
            chip = Chip(SHORT.get(sid, sid))
            self.chip_row.addWidget(chip)
            self.chips[sid] = chip
        self._set_chip(sid, state)
        if state == "start":
            self.frac = 0.0
            self.bar.setValue(0)
            self.text.setText(STEPS[sid][0] + "…" if sid in STEPS else text)
        elif state == "failed":
            self.text.setText(text)

    def _progress(self, frac, text):
        if frac < 0:
            self.bar.setRange(0, 0)        # busy animation
        else:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
            self.frac = frac
        if text:
            self.text.setText(text)

    def _tick(self):
        w = self.runner.watch
        if not w:
            return
        eta = fmt_eta(w.elapsed, self.frac) if self.frac < 1 else ""
        self.clock.setText(fmt_secs(w.elapsed) + (f"  ·  {eta}" if eta else ""))

    def _paused(self, on):
        self.b_pause.set_icon("play" if on else "pause")
        self.b_pause.setToolTip("Resume" if on else "Pause")
        if on:
            self.text.setText("Paused")

    def _pause(self):
        if self.runner.control and self.runner.control.paused:
            self.runner.resume()
        else:
            self.runner.pause()

    def _finished(self, result, msg):
        self.timer.stop()
        self._tick()
        self.b_pause.setEnabled(False)
        self.b_stop.setEnabled(False)
        self.b_pause.set_icon("pause")
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
        if result == "done":
            self.bar.setValue(1000)
        self.text.setText(msg)
        self.title.setText({"done": "Done", "stopped": "Stopped", "failed": "Failed"}.get(result, result))
        if result == "done":                          # failures and stops stay until the next job
            self.clear_timer.start()
