"""Cut (left column, under the video list): keep only a part of the video, or remove a part.
Saved per video; the export applies it to the picture, the sound and the subtitles."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from ..core.project import Project
from ..core.subtitle import fmt_clock
from .editor import _parse_clock
from .widgets import Segmented, button, label, row, section


def _fmt(t):
    return fmt_clock(t) if t is not None else ""


class CutPanel(QWidget):
    changed = Signal()

    def __init__(self, now_fn):
        super().__init__()
        self.now = now_fn                     # () -> current player time in seconds
        self.video = ""
        self.duration = 0.0
        self.mode = Segmented([("off", "Off"), ("keep", "Keep"), ("remove", "Remove")], "off")
        self.start = QLineEdit()
        self.end = QLineEdit()
        for e, ph in ((self.start, "Start  0:00.0"), (self.end, "End  1:30.0")):
            e.setPlaceholderText(ph)
        self.b_start = button("Now", lambda: self._now(self.start))
        self.b_end = button("Now", lambda: self._now(self.end))
        for b in (self.b_start, self.b_end):
            b.setToolTip("Use the current position of the video")
            b.setFixedWidth(52)
        self.info = label("", muted=True, wrap=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(section("Cut"))
        lay.addWidget(self.mode)
        lay.addWidget(row(self.start, self.b_start))
        lay.addWidget(row(self.end, self.b_end))
        lay.addWidget(self.info)
        self.mode.changed.connect(lambda _v: self._save())
        self.start.editingFinished.connect(self._save)
        self.end.editingFinished.connect(self._save)
        self.load("", 0)

    def load(self, video, duration):
        self.video, self.duration = video, duration or 0.0
        cut = Project(video).cut if video else None
        self.mode.set_value(cut["mode"] if cut else "off")
        self.start.setText(_fmt(cut["start"]) if cut else "")
        self.end.setText(_fmt(cut["end"]) if cut else "")
        self._enable()
        self._info(cut)

    def _enable(self):
        on = bool(self.video) and self.mode.value() != "off"
        for w in (self.start, self.end, self.b_start, self.b_end):
            w.setEnabled(on)
        self.mode.setEnabled(bool(self.video))

    def _now(self, edit):
        edit.setText(fmt_clock(self.now()))
        self._save()

    def _read(self, edit):
        t = edit.text().strip()
        if not t:
            return None
        try:
            return max(0.0, _parse_clock(t))
        except ValueError:
            return None

    def _save(self):
        self._enable()
        if not self.video:
            return
        mode = self.mode.value()
        a, b = self._read(self.start), self._read(self.end)
        if mode != "off" and a is None and b is not None:
            a = 0.0
        if mode != "off" and b is None and a is not None and self.duration:
            b = self.duration
        p = Project(self.video)
        if mode == "off" or a is None or b is None or b <= a:
            p.set_cut(None, None, None)
            self._info(None, bad=mode != "off" and (a is not None or b is not None))
        else:
            p.set_cut(mode, a, b)
            self._info(p.cut)
        self.changed.emit()

    def _info(self, cut, bad=False):
        if bad:
            self.info.setText("The end must be after the start.")
        elif not cut:
            self.info.setText("The whole video is exported." if self.video else "")
        elif cut["mode"] == "keep":
            self.info.setText(f"Exports only {_fmt(cut['start'])} to {_fmt(cut['end'])} "
                              f"({cut['end'] - cut['start']:.1f} s).")
        else:
            left = max(0.0, self.duration - (cut["end"] - cut["start"])) if self.duration else 0
            self.info.setText(f"Removes {_fmt(cut['start'])} to {_fmt(cut['end'])}"
                              + (f": {left:.1f} s left." if self.duration else "."))
