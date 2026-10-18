"""What is running, small, in the command bar: which video (1/3), a thin progress bar, elapsed / left,
Pause / Stop, and the log (in a window of its own). The video's name and the step are in the tooltip (and the
log). Hidden bits while nothing runs."""
import os

from PySide6.QtCore import Qt, QTimer
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
        self.runner = runner
        self.chips = {}
        self.frac = 0.0

        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setMaximumWidth(200)
        self._tip = ["", ""]                          # video, what it is doing: the tooltip
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(4)
        self.bar.setFixedWidth(110)
        self.text = QLabel("")
        self.text.setProperty("muted", True)
        self.text.setMinimumWidth(60)
        self.text.setMaximumWidth(260)
        from PySide6.QtWidgets import QSizePolicy
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # long messages never widen the window
        self.clock = QLabel("")
        self.clock.setProperty("muted", True)
        self.b_pause = IconButton("pause", "Pause")
        self.b_stop = IconButton("stop", "Stop")
        self.b_log = IconButton("log", "Show / hide the log", checkable=True)
        self.b_pause.setEnabled(False)
        self.b_stop.setEnabled(False)

        # one small row in the command bar: video · what it is doing · bar · time · buttons
        top = QHBoxLayout(self)
        top.setContentsMargins(0, 0, 8, 0)
        top.setSpacing(8)
        self.chip_box = QWidget()                      # the step chips are kept (not shown: no room)
        self.chip_box.setLayout(self.chip_row)
        self.chip_box.setVisible(False)
        top.addWidget(self.title)
        top.addWidget(self.text)
        top.addWidget(self.bar)
        top.addWidget(self.clock)
        top.addWidget(self.b_pause)
        top.addWidget(self.b_stop)
        top.addWidget(self.b_log)

        self.log_window = QWidget(None, Qt.Tool)       # the log: a window of its own, shown when wanted
        self.log_window.setWindowTitle("Log")
        self.log_window.resize(820, 360)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        lw = QVBoxLayout(self.log_window)
        lw.setContentsMargins(8, 8, 8, 8)
        lw.addWidget(self.log)
        self.log_window.installEventFilter(self)
        self._idle(True)

        self.b_pause.clicked.connect(self._pause)
        self.b_stop.clicked.connect(runner.stop)
        self.b_log.toggled.connect(self._toggle_log)
        runner.started.connect(self._started)
        runner.progress.connect(self._progress)
        runner.status.connect(lambda t: self._set_tip(doing=t))
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

    def _idle(self, idle: bool):
        for w in (self.bar, self.clock, self.b_pause, self.b_stop):
            w.setVisible(not idle)
        self.title.setVisible(not idle and bool(self.title.text()))
        self.text.setVisible(False)                   # while it runs: only 1/3, the bar and the time

    def _set_title(self, text):
        self.title.setText(text)
        self.title.setVisible(bool(text))

    def _set_tip(self, video=None, doing=None):
        if video is not None:
            self._tip[0] = video
        if doing is not None:
            self._tip[1] = doing
        tip = "\n".join(t for t in self._tip if t)
        for w in (self.title, self.bar, self.clock):
            w.setToolTip(tip)

    def note(self, text: str):
        """A short message while nothing runs (e.g. ffmpeg is missing)."""
        self.text.setText(text)
        self.text.setToolTip(text)
        self.text.setVisible(bool(text))

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.log_window and e.type() == QEvent.Close and self.b_log.isChecked():
            self.b_log.setChecked(False)                # closed with its X: the button follows
        return False

    def reset(self):
        """Back to idle: nothing shown but the log button."""
        self.clear_timer.stop()
        self._clear_chips()
        self.title.setText("")
        self._idle(True)
        self.text.setText("")
        self._tip = ["", ""]
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
        if on:
            top = self.window()
            self.log_window.setParent(top, Qt.Tool)
            self.log_window.show()
            self.log_window.raise_()
        else:
            self.log_window.hide()
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
        self.title.setText("")
        self._idle(False)
        self._tip = [title, ""]
        self._set_tip()
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
        self._set_title(f"{i + 1}/{n}" if n > 1 else "")
        self._set_tip(video=f"{i + 1}/{n} · {name}" if n > 1 else name)
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
            self._set_tip(doing=STEPS[sid][0] + "…" if sid in STEPS else text)
        elif state == "failed":
            self._set_tip(doing=text)

    def _progress(self, frac, text):
        if frac < 0:
            self.bar.setRange(0, 0)        # busy animation
        else:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
            self.frac = frac
        if text:
            self._set_tip(doing=text)

    def _tick(self):
        w = self.runner.watch
        if not w:
            return
        eta = fmt_eta(w.elapsed, self.frac) if self.frac < 1 else ""
        self.clock.setText(fmt_secs(w.elapsed) + (f"  ·  {eta}" if eta else ""))

    def _paused(self, on):
        self.b_pause.set_icon("play" if on else "pause")
        self.b_pause.setToolTip("Resume" if on else "Pause")
        self._set_tip(doing="Paused" if on else "")

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
        self._set_title({"done": "Done", "stopped": "Stopped", "failed": "Failed"}.get(result, result))
        self._set_tip(doing=msg)
        self.text.setText(msg if result == "failed" else "")       # what went wrong stays readable
        self.text.setToolTip(msg)
        self.text.setVisible(result == "failed" and bool(msg))
        if result == "done":                          # failures and stops stay until the next job
            self.clear_timer.start()
