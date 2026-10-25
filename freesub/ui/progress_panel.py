"""What is running, at the bottom of the media column: ONE progress bar for the whole run (every step of every
video, it only goes forward and is full when everything is done), which video (1/3), elapsed / left, and the log
button (the log has a window of its own). The video's name and the step are in the tooltip (and the log).
Stopping is the Run button (it becomes Stop while working); there is no pause."""
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
        self.frac = 0.0                 # the whole run, 0..1 (what the bar shows; never goes back)
        self.step_frac = 0.0            # the step running now
        self.n_videos, self.video_i, self.step_i = 1, 0, 0

        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setMaximumWidth(120)
        self._tip = ["", ""]                          # video, what it is doing: the tooltip
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(4)
        self.text = QLabel("")
        self.text.setProperty("muted", True)
        self.text.setMinimumWidth(60)
        self.text.setWordWrap(True)
        from PySide6.QtWidgets import QSizePolicy
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # long messages never widen the column
        self.clock = QLabel("")
        self.clock.setProperty("muted", True)
        self.b_log = IconButton("log", "Show / hide the log", checkable=True)
        from ..i18n import Keep
        self.credit = QLabel(Keep("FreeSub by Maditor"))     # faint, on the left of the log button while idle
        self.credit.setObjectName("Credit")
        self._running = False

        # bottom of the media column: the bar over the whole width, then 1/3 · time ... log button, then a message
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self.chip_box = QWidget()                      # the step chips are kept (not shown: no room)
        self.chip_box.setLayout(self.chip_row)
        self.chip_box.setVisible(False)
        col.addWidget(self.bar)
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(self.credit)
        line.addWidget(self.title)
        line.addWidget(self.clock)
        line.addStretch(1)
        line.addWidget(self.b_log)
        col.addLayout(line)
        col.addWidget(self.text)

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

        self.b_log.toggled.connect(self._toggle_log)
        runner.started.connect(self._started)
        runner.progress.connect(self._progress)
        runner.status.connect(lambda t: self._set_tip(doing=t))
        runner.step.connect(self._step)
        runner.video_started.connect(self._video)
        runner.finished.connect(self._finished)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.clear_timer = QTimer(self)               # "Done" goes away by itself after a while
        self.clear_timer.setSingleShot(True)
        self.clear_timer.setInterval(10_000)
        self.clear_timer.timeout.connect(self.reset)

    def _idle(self, idle: bool):
        self._running = not idle
        for w in (self.bar, self.clock):
            w.setVisible(not idle)
        self.title.setVisible(not idle and bool(self.title.text()))
        self.text.setVisible(False)                   # while it runs: only 1/3, the bar and the time
        self._credit()

    def _set_title(self, text):
        self.title.setText(text)
        self.title.setVisible(bool(text))
        self._credit()

    def _credit(self):
        """The 'FreeSub by Maditor' line: only when nothing runs and nothing is said (Done / Stopped...)."""
        self.credit.setVisible(not self._running and not self.title.text())

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
        self.frac = self.step_frac = 0.0
        self.n_videos, self.video_i, self.step_i = 1, 0, 0
        self.timer.start()
        self._tick()

    def _video(self, video, i, n):
        name = os.path.basename(video)
        self.n_videos, self.video_i, self.step_i, self.step_frac = max(1, n), i, 0, 0.0
        self._show()
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
        order = list(self.chips)
        if state == "start":
            self.step_i = order.index(sid) if sid in order else self.step_i
            self.step_frac = 0.0
            self._show()
            self._set_tip(doing=STEPS[sid][0] + "…" if sid in STEPS else text)
        elif state == "done" and sid in order:
            self.step_i, self.step_frac = order.index(sid), 1.0
            self._show()
        elif state == "failed":
            self._set_tip(doing=text)

    def overall(self) -> float:
        """Where the whole run is: the videos done, then the steps of this video done, then this step."""
        n_steps = max(1, len(self.chips))
        video = min(1.0, (self.step_i + max(0.0, min(1.0, self.step_frac))) / n_steps)
        return min(1.0, (self.video_i + video) / self.n_videos)

    def _show(self):
        self.frac = max(self.frac, self.overall())       # one bar: it never starts again for the next step
        self.bar.setValue(int(self.frac * 1000))

    def _progress(self, frac, text):
        if frac is not None and frac >= 0:              # unknown (-1 / None): the bar just stays where it is
            self.step_frac = frac
            self._show()
        if text:
            self._set_tip(doing=text)

    def _tick(self):
        w = self.runner.watch
        if not w:
            return
        eta = fmt_eta(w.elapsed, self.frac) if self.frac < 1 else ""
        self.clock.setText(fmt_secs(w.elapsed) + (f"  ·  {eta}" if eta else ""))

    def _finished(self, result, msg):
        self.timer.stop()
        self._tick()
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
        if result == "done":
            self.frac = 1.0
            self.bar.setValue(1000)
        self._set_title({"done": "Done", "stopped": "Stopped", "failed": "Failed"}.get(result, result))
        self._set_tip(doing=msg)
        self.text.setText(msg if result == "failed" else "")       # what went wrong stays readable
        self.text.setToolTip(msg)
        self.text.setVisible(result == "failed" and bool(msg))
        if result == "done":                          # failures and stops stay until the next job
            self.clear_timer.start()
