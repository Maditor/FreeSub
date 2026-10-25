"""Subtitle table under the preview: time, original, translation. Edit a cell to fix a line; changes are saved
to the project (and the .srt in the output folder) a moment later. Click a line to choose it; double-click its words
to jump the video there and edit them (double-click a time: edit it, the video stays)."""
import logging
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..i18n import tr
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import find as fnd
from ..core.project import SRC_SRT, TR_SRT, Project
from ..core.subtitle import Cue, cue_at, fmt_clock, load_srt, parse_time, save_srt, slot_between
from .find_bar import FindBar, FindDelegate
from .queue import open_folder
from .widgets import IconButton, confirm, label, row, section, wait_cursor

log = logging.getLogger(__name__)
COLS = ["Start", "End", "Voice", "Original", "Translation"]
C_VOICE, C_SRC, C_TR = 2, 3, 4
SIGN = {"f": "♀", "m": "♂", None: "·"}


def _parse_clock(text: str) -> float:
    """'1:02.5', '01:02:03,400' or '62.5' -> seconds."""
    t = text.strip().replace(",", ".")
    if t.count(":") >= 2:
        return parse_time(t)
    if ":" in t:
        m, s = t.split(":", 1)
        return int(m) * 60 + float(s)
    return float(t)


class _VoiceSignal(QObject):
    done = Signal(str)                     # video whose voices were worked out again


class Editor(QWidget):
    seek = Signal(float)
    cues_changed = Signal()
    redo_requested = Signal()              # make the subtitles again with the Steps settings
    import_requested = Signal(str)         # "original" | "translation"
    remake_voice = Signal(list)            # start times of the lines whose voice is made again (right click)

    def __init__(self):
        super().__init__()
        self.project = None
        self.src, self.tr = [], []
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        # the time columns have a fixed width (always the same format): "fit the contents" measured every row
        # again on each row added, so thousands of lines froze the window
        time_w = self.table.fontMetrics().horizontalAdvance("00:00:00.000") + 22
        for col in (0, 1):
            h.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, time_w)
        h.setSectionResizeMode(C_VOICE, QHeaderView.Fixed)
        self.table.setColumnWidth(C_VOICE, 52)
        self.table.horizontalHeaderItem(C_VOICE).setToolTip(
            "Women's or men's narration voice (in testing). Click a line to set it: mark a line or two of each "
            "voice and the other lines that sound like them follow")
        h.setSectionResizeMode(C_SRC, QHeaderView.Stretch)
        h.setSectionResizeMode(C_TR, QHeaderView.Stretch)
        self.voices = []                   # [(gender, how)] per row: how = you | learned | auto
        self.marks = {}                    # what the last narration found per line (dub.line_marks): read late?
        self._vsig = _VoiceSignal()
        self._vsig.done.connect(self._voices_done)
        self._learning = False
        self._learn_again = False
        self._learn_timer = QTimer(self)
        self._learn_timer.setSingleShot(True)
        self._learn_timer.setInterval(600)
        self._learn_timer.timeout.connect(self._learn)
        self.count = label("", muted=True)
        self.b_open = IconButton("folder_open", "Open the folder with the .srt files")
        self.b_import = IconButton("import", "Use my own .srt for this video (as the original or the translation)")
        self.b_redo = IconButton("redo", "Make the subtitles again with the settings in Steps (Subtitles, "
                                         "Translate). Your edits of this video's lines are replaced")
        self.b_fit = IconButton("wrap", "Fit the lines to the box: break every line again so it fits the box "
                                        "drawn on the video (or the video width), with the font and size of Style")
        self.b_find = IconButton("search", "Find and replace in the subtitles (Ctrl+F)", checkable=True)
        self.find = FindBar()
        self.find.hide()
        self._find_hits, self._find_at = [], -1           # the matches (core/find.py) and the current one
        self._find_delegate = FindDelegate(self.table)
        self.table.setItemDelegate(self._find_delegate)          # every column: it also paints the row colours
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.b_voice = IconButton("voice_time", "", checkable=True)
        from ..config import settings
        self.b_voice.setChecked(bool(settings()["dub_sync_subs"]))
        self.b_voice.toggled.connect(self._voice_toggled)
        self.voice_times = None      # {row: (start, end)} when the narration read the lines (None: no narration)
        self._voice_tip()
        lay.addWidget(row(section("Subtitles"), self.count, None, self.b_find, self.b_fit, self.b_voice,
                          self.b_redo, self.b_import, self.b_open))
        lay.addWidget(self.find)
        self.b_redo.clicked.connect(self.redo_requested.emit)
        self.b_import.clicked.connect(self._import_menu)
        lay.addWidget(self.table, 1)
        self.hint = label("No subtitles yet.", muted=True, wrap=True)
        lay.addWidget(self.hint)
        self.filler = QWidget()          # takes the room of the table while there is none: the header stays on top
        lay.addWidget(self.filler, 1)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.itemChanged.connect(self._edited)
        self.b_open.clicked.connect(self.open_output)
        self.b_fit.clicked.connect(self.fit_lines)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)       # right click: delete / add a line
        self.table.customContextMenuRequested.connect(self._row_menu)
        self.b_find.toggled.connect(self.show_find)
        self.find.changed.connect(self._find_changed)
        self.find.step.connect(self._find_step)
        self.find.replace_one.connect(self._replace_one)
        self.find.replace_all.connect(self._replace_all)
        self.find.closed.connect(lambda: self.b_find.setChecked(False))
        self.find.undo_asked.connect(self.undo)
        self.find.redo_asked.connect(self.redo)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self._save)
        self._dirty = set()

    def _import_menu(self):
        """A small menu under the button (no arrow on the button): which subtitles the file replaces."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("As the original subtitles", lambda: self.import_requested.emit("original"))
        m.addAction("As the translation", lambda: self.import_requested.emit("translation"))
        m.exec(self.b_import.mapToGlobal(QPoint(0, self.b_import.height())))

    # ------------------------------------------------------------ narration times
    def voice_mode(self) -> bool:
        """Times shown (table and video) are when the narration reads each line, not the original ones."""
        return self.b_voice.isChecked() and bool(self.voice_times)

    def set_voice_times(self, times):
        """times: {row: (start, end)} from the narration, or None. The table shows them when the button is on."""
        from ..config import settings
        on = bool(settings()["dub_sync_subs"])
        if self.b_voice.isChecked() != on:
            self.b_voice.blockSignals(True)
            self.b_voice.setChecked(on)
            self.b_voice.blockSignals(False)
        if times and max(times) >= len(self.src):
            times = None
        changed = times != self.voice_times
        self.voice_times = times
        self._hl = None
        self._voice_tip()
        if changed and on:
            self._refill_later()

    def sync_voice_button(self):
        """'Subtitles follow the narration' was switched in the narration card: the headset button follows."""
        from ..config import settings
        on = bool(settings()["dub_sync_subs"])
        if self.b_voice.isChecked() != on:
            self.b_voice.setChecked(on)                  # -> _voice_toggled: table, tip, preview

    def _voice_tip(self):
        self.b_voice.setEnabled(bool(self.voice_times))      # nothing to switch to before the narration exists
        if not self.voice_times:
            self.b_voice.setToolTip("Times of the narration: make the narration first (the mic button on the "
                                    "video), then this shows when each line is read")
        elif self.b_voice.isChecked():
            self.b_voice.setToolTip("Showing when the narration reads each line (as Export burns them in). "
                                    "Click for the original times")
        else:
            self.b_voice.setToolTip("Showing the original times. Click to show when the narration reads each "
                                    "line (the video and Export follow it)")

    def _voice_toggled(self, on):
        self._hl = None
        from ..config import settings
        settings().update(dub_sync_subs=on)
        self._voice_tip()
        self._refill_later()
        self.cues_changed.emit()

    def _refill_later(self):
        """Fill the table again, but never under an open cell editor."""
        if self.table.state() == QAbstractItemView.EditingState:
            QTimer.singleShot(400, self._refill_later)
            return
        row_, bar = self.table.currentRow(), self.table.verticalScrollBar().value()
        self._fill()
        if 0 <= row_ < self.table.rowCount():
            self.table.blockSignals(True)
            self.table.setCurrentCell(row_, max(0, self.table.currentColumn()))
            self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(bar)

    def _time(self, i):
        """(start, end) of row i as shown now."""
        if self.voice_mode() and i in self.voice_times:
            return self.voice_times[i]
        return self.src[i].start, self.src[i].end

    # ------------------------------------------------------------ load / show
    def load(self, video: str):
        if getattr(self, "_fit_job", None):              # lines being fitted: keep what is done so far
            self._save()
        self._flush()
        same = bool(video) and self.project is not None and self.project.video == os.path.abspath(video)
        if not (same and hasattr(self, "_undo")):
            self._undo, self._redo = [], []              # undo is for this video's lines (kept on a reload)
        self.project = Project(video) if video else None
        self.src = load_srt(self.project.src_srt) if self.project and self.project.has(SRC_SRT) else []
        tr = load_srt(self.project.tr_srt) if self.project and self.project.has(TR_SRT) else []
        if tr and len(tr) != len(self.src):          # match by start time when the counts differ
            starts = {round(c.start, 1): c for c in tr}
            tr = [starts.get(round(c.start, 1), Cue(c.start, c.end, "")) for c in self.src]
        self.tr = tr
        self._read_voices()
        self._read_marks()
        self._fill()

    def reload_keep_place(self):
        """Read the subtitle files again (a step just wrote them), keeping the selected row and the scroll."""
        if self.project is None:
            return
        row = self.table.currentRow()
        bar = self.table.verticalScrollBar().value()
        self.load(self.project.video)
        if 0 <= row < self.table.rowCount():
            self.table.setCurrentCell(row, max(0, self.table.currentColumn()))
        self.table.verticalScrollBar().setValue(bar)

    def _fill(self):
        self._hl = None                                   # the times the row highlight follows: again
        self.table.setUpdatesEnabled(False)               # thousands of rows: drawn once, at the end
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.src))
        voice = self.voice_mode()
        if voice:
            from . import theme
            accent = QColor(theme.colors()["accent"])
        for i, c in enumerate(self.src):
            t = self.tr[i].text if i < len(self.tr) else ""
            a, b = self._time(i)
            vals = (fmt_clock(a), fmt_clock(b), "", c.text.replace("\n", " / "), t.replace("\n", " / "))
            for col, v in enumerate(vals):
                if col == C_VOICE:
                    self.table.setItem(i, col, self._voice_item(i))
                    continue
                it = QTableWidgetItem(v)
                if col < 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if voice and i in self.voice_times:      # narration times: shown, not edited here
                        it.setForeground(accent)
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                        it.setToolTip(f"Read by the narration. Original: {fmt_clock(c.start)} - "
                                      f"{fmt_clock(c.end)} (turn the narration times off to edit it)")
                    else:
                        it.setForeground(self.palette().placeholderText())
                self.table.setItem(i, col, it)
            if self.marks:
                self._paint_mark(i)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        n = len(self.src)
        self.count.setText(f"{n} line{'s' if n != 1 else ''}" + (" · translated" if any(c.text for c in self.tr) else ""))
        self._count_marks()
        self.table.setVisible(n > 0)
        self.filler.setVisible(n == 0)
        self.hint.setVisible(n == 0 and self.project is not None)
        self.table.setColumnHidden(C_TR, not self.tr)
        from ..config import settings
        self.table.setColumnHidden(C_VOICE, settings()["voice_mode"] != "split")   # one voice: nothing to set
        self.b_open.setEnabled(self.project is not None)
        self.b_import.setEnabled(self.project is not None)
        self.b_redo.setEnabled(self.project is not None)
        self.b_fit.setEnabled(n > 0)
        self.b_find.setEnabled(n > 0)
        if n == 0 and self.b_find.isChecked():
            self.b_find.setChecked(False)
        self.find.set_has_translation(bool(self.tr))
        self._find_refresh()                              # the matches follow the lines (undo, a new video...)

    def shown_cues(self, prefer="translation"):
        """Cues for the preview: the translation where there is one, else the original; with the narration's
        times when those are shown."""
        if prefer == "translation" and self.tr:
            cues = [Cue(s.start, s.end, (t.text or s.text)) for s, t in zip(self.src, self.tr)]
        else:
            cues = [Cue(s.start, s.end, s.text) for s in self.src]
        if self.voice_mode():
            for i, c in enumerate(cues):
                if i in self.voice_times:
                    c.start, c.end = self.voice_times[i]
        from ..core.style import attach_looks
        fresh = Project(self.project.video) if self.project else None     # read now: looks change meanwhile
        return attach_looks(cues, fresh)                 # lines with a look of their own (the timeline)

    def highlight(self, t: float):
        if not self.src or not self.isVisible() or self.table.state() == QAbstractItemView.EditingState:
            return
        if getattr(self, "_hl", None) is None:            # made once (not 30 times a second while playing)
            self._hl = self.shown_cues("original") if self.voice_mode() else self.src
        i = cue_at(self._hl, t)
        if i >= 0 and i != self.table.currentRow():
            self.table.blockSignals(True)
            self.table.selectRow(i)
            self.table.scrollToItem(self.table.item(i, 0), QAbstractItemView.PositionAtCenter)
            self.table.blockSignals(False)

    # ------------------------------------------------------------ edits
    def _clicked(self, r, c):
        """One click only chooses the line (the video stays where it is); the voice cell switches the voice."""
        if r >= len(self.src):
            return
        if c == C_VOICE:
            self._cycle_voice(r)

    def _double_clicked(self, r, c):
        """Double-click on the words: the video goes to the line, and the words open for editing. On a time:
        only the time opens (the video stays, to read the time to type from it)."""
        if r < len(self.src) and c in (C_SRC, C_TR):
            self.seek.emit(self._time(r)[0] + 0.01)

    # ------------------------------------------------------------ women's / men's voice
    def _read_voices(self):
        from ..steps import genders
        self.voices = genders.table_rows(self.project, self.src) if self.project and self.src else []

    def _voice_item(self, i):
        from . import theme
        g, how = self.voices[i] if i < len(self.voices) else (None, "auto")
        it = QTableWidgetItem(SIGN.get(g, "·"))
        it.setTextAlignment(Qt.AlignCenter)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        f = it.font()
        f.setPointSizeF(f.pointSizeF() * 1.25 if f.pointSizeF() > 0 else 12)
        it.setFont(f)
        c = theme.colors()
        word = {"f": "Women's voice", "m": "Men's voice", None: "Not sure (the women's voice is used)"}[g]
        if how == "you":
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setForeground(QColor(c["accent"]))
            it.setToolTip(f"{word}: set by you. Click to change")
        elif how == "learned":
            it.setForeground(QColor(c["fg"]))
            it.setToolTip(f"{word}: learned from the lines you marked. Click to set it yourself")
        else:
            it.setForeground(QColor(c["faint"]))
            it.setToolTip(f"{word}: guessed from the voice. Click to set it yourself")
        return it

    def _cycle_voice(self, r):
        """Click: a guess becomes the other voice, set by you; then the other one; then back to the guess."""
        from ..steps import genders
        if self.project is None:
            return
        g, how = self.voices[r] if r < len(self.voices) else (None, "auto")
        base = genders.table_rows(self.project, [self.src[r]], marks=False)[0][0]    # what it is without a mark
        other = {"f": "m", "m": "f"}
        if how != "you":
            new = other.get(g, "f")               # 1st click: the other voice
        elif g != base:
            new = base or other[g]                # 2nd click: the guessed voice, but set by you
        else:
            new = None                            # 3rd click: back to the guess
        genders.set_mark(self.project, self.src[r].start, new)
        self._read_voices()
        self.table.blockSignals(True)
        self.table.setItem(r, C_VOICE, self._voice_item(r))
        self.table.blockSignals(False)
        self._learn_timer.start()                  # a few quick clicks: worked out once

    def _learn(self):
        """Work out the other lines from the marks, on a thread (the first time it reads every line's voice)."""
        if self.project is None:
            return
        if self._learning:
            self._learn_again = True
            return
        self._learning = True
        self.count.setText(self.count.text().split(" · learning")[0] + " · learning the voices…")
        project, video = self.project, self.project.video

        def work():
            from ..steps import genders
            try:
                genders.apply_marks(project)
            except Exception as e:  # noqa: BLE001
                log.warning("Voices not worked out: %s", e)
            self._vsig.done.emit(video)
        threading.Thread(target=work, daemon=True).start()

    def _voices_done(self, video):
        self._learning = False
        self.count.setText(self.count.text().split(" · learning")[0])
        if self.project is not None and self.project.video == video:
            self._read_voices()
            self.table.blockSignals(True)
            for i in range(min(self.table.rowCount(), len(self.src))):
                self.table.setItem(i, C_VOICE, self._voice_item(i))
            self.table.blockSignals(False)
        if self._learn_again:
            self._learn_again = False
            self._learn()

    # ------------------------------------------------------------ undo / redo (Ctrl+Z / Ctrl+Shift+Z)
    def _snap(self):
        return ([(c.start, c.end, c.text) for c in self.src], [(c.start, c.end, c.text) for c in self.tr])

    def _remember(self, label: str):
        """Before a change: the lines as they are now can be brought back."""
        if not hasattr(self, "_undo"):
            self._undo, self._redo = [], []
        self._undo.append((label, self._snap(), time.monotonic()))
        del self._undo[:-100]
        self._redo.clear()

    def undo_stamp(self) -> float:
        """When the change Ctrl+Z would undo was made (-1: nothing to undo); the timeline compares it with its
        own, so Ctrl+Z undoes whichever came last."""
        u = getattr(self, "_undo", None)
        return u[-1][2] if u and not getattr(self, "_fit_job", None) else -1.0

    def redo_stamp(self) -> float:
        r = getattr(self, "_redo", None)
        return r[-1][2] if r and not getattr(self, "_fit_job", None) else -1.0

    def retime(self, i: int, a: float, b: float):
        """A line dragged on the timeline (this video): new times, which Ctrl+Z brings back like any edit."""
        if not (0 <= i < len(self.src)):
            return
        self._remember("timing")
        cue = self.src[i]
        if abs(cue.start - a) > 1e-6:
            from ..steps import genders
            genders.move_mark(self.project, cue.start, a)              # a voice mark follows its line
        cue.start, cue.end = a, b
        if i < len(self.tr):
            self.tr[i].start, self.tr[i].end = a, b
        self._dirty.update({"src", "tr"} if self.tr else {"src"})
        self._hl = None
        ta, tb = self._time(i)
        self.table.blockSignals(True)
        for col, v in ((0, ta), (1, tb)):
            it = self.table.item(i, col)
            if it is not None:
                it.setText(fmt_clock(v))
        self.table.blockSignals(False)
        self._save_timer.stop()
        self._save()                                  # written now: the timeline reads the file
        self.cues_changed.emit()

    def _restore(self, snap):
        src, tr_ = snap
        self.src = [Cue(a, b, t) for a, b, t in src]
        self.tr = [Cue(a, b, t) for a, b, t in tr_]
        self._dirty.update({"src", "tr"} if self.tr else {"src"})
        row_, bar = self.table.currentRow(), self.table.verticalScrollBar().value()
        self._fill()
        if 0 <= row_ < self.table.rowCount():
            self.table.blockSignals(True)
            self.table.setCurrentCell(row_, max(0, self.table.currentColumn()))
            self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(bar)
        self._save()
        self.cues_changed.emit()

    def undo(self) -> bool:
        if getattr(self, "_fit_job", None) or not getattr(self, "_undo", None):
            return False
        label, snap, _at = self._undo.pop()
        self._redo.append((label, self._snap(), time.monotonic()))
        self._restore(snap)
        self.count.setText(self.count.text() + tr(" · undone: {a}").replace("{a}", tr(label)))
        return True

    def redo(self) -> bool:
        if getattr(self, "_fit_job", None) or not getattr(self, "_redo", None):
            return False
        label, snap, _at = self._redo.pop()
        self._undo.append((label, self._snap(), time.monotonic()))
        self._restore(snap)
        self.count.setText(self.count.text() + tr(" · redone: {a}").replace("{a}", tr(label)))
        return True

    def _edited(self, item):
        self._hl = None
        r, c = item.row(), item.column()
        self._remember("line edit")                     # self.src / self.tr are not changed yet
        text = item.text().replace(" / ", "\n").strip()
        try:
            if c in (0, 1):
                v = _parse_clock(item.text())
                cue = self.src[r]
                if c == 0:
                    from ..steps import genders
                    genders.move_mark(self.project, cue.start, v)     # a voice mark follows its line
                    cue.start = v
                else:
                    cue.end = max(cue.start + 0.1, v)
                if r < len(self.tr):
                    self.tr[r].start, self.tr[r].end = cue.start, cue.end
                self._dirty.update({"src", "tr"} if self.tr else {"src"})
            elif c == C_SRC:
                self.src[r].text = text
                self._dirty.add("src")
            elif c == C_TR:
                while len(self.tr) < len(self.src):
                    s = self.src[len(self.tr)]
                    self.tr.append(Cue(s.start, s.end, ""))
                self.tr[r].text = text
                self._dirty.add("tr")
        except (ValueError, IndexError):
            self._fill()
            return
        if self.marks and c in (C_SRC, C_TR):           # read late -> edited: the colour changes at once
            self.table.blockSignals(True)
            self._paint_mark(r)
            self.table.blockSignals(False)
            self._count_marks()
        self._save_timer.start()
        self.cues_changed.emit()
        self._find_refresh()

    # ------------------------------------------------------------ lines read late by the narration
    def _read_marks(self):
        from ..steps import dub
        self.marks = dub.line_marks(self.project) if self.project and self.src else {}

    def refresh_marks(self):
        """A narration was just made: colour the lines that were read late (and only those)."""
        if self.project is None:
            return
        self._read_marks()
        self._refill_later()

    def _said_text(self, i) -> str:
        """The text of row i that the narration reads (the translation when there is one)."""
        if self.tr:
            return self.tr[i].text if i < len(self.tr) else ""
        return self.src[i].text

    def line_mark(self, i):
        """'late' (read past its time even at the maximum speed), 'edited' (was late, its text changed since:
        make the voice again to see), or None; with what the narration found."""
        m = self.marks.get(round(self.src[i].start, 1)) if i < len(self.src) else None
        if not m or not m.get("over"):
            return None, m
        if " ".join(m.get("t", "").split()) != " ".join(self._said_text(i).split()):
            return "edited", m
        return "late", m

    def _paint_mark(self, i):
        from . import theme
        kind, m = self.line_mark(i)
        if kind:
            back = QColor(theme.colors()["err" if kind == "late" else "warn"])
            back.setAlpha(60 if kind == "late" else 38)
            if kind == "late":
                tip = tr("{a} s late").replace("{a}", f"{m['over']:.1f}")
            else:
                tip = tr("Edited, voice not made again yet")
        for col in range(self.table.columnCount()):
            it = self.table.item(i, col)
            if it is None:
                continue
            if kind:
                it.setBackground(back)
            else:
                it.setData(Qt.BackgroundRole, None)
            if col == (C_TR if self.tr else C_SRC):
                it.setToolTip(tip if kind else "")

    def edited_rows(self):
        """Lines whose spoken text is not what the last narration read (changed, or added since). None before
        a narration was made."""
        if not self.marks:
            return []
        out = []
        for i, c in enumerate(self.src):
            said = " ".join(self._said_text(i).split())
            m = self.marks.get(round(c.start, 1))
            if said and (not m or " ".join(m.get("t", "").split()) != said):
                out.append(i)
        return out

    def late_rows(self):
        return [i for i in range(len(self.src)) if self.line_mark(i)[0] == "late"]

    def _count_marks(self):
        """'· 3 read late' after the line count (the tooltip says what to do)."""
        text = self.count.text().split(" · ")
        late_word = tr("{a} read late").split("{a}")[-1]
        text = [x for x in text if not x.endswith(late_word)]
        n = len(self.late_rows()) if self.marks else 0
        if n:
            text.append(tr("{a} read late").replace("{a}", str(n)))
        self.count.setText(" · ".join(text))
        self.count.setToolTip(tr("Too long even at the maximum speed") if n else "")

    # ------------------------------------------------------------ delete / add a line (right click)
    def _lines_blocked(self) -> str:
        """Why lines can not be added or deleted right now ('' = they can)."""
        if getattr(self, "_fit_job", None):
            return tr("Wait: the lines are being fitted")
        return ""

    def _row_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if not 0 <= r < len(self.src):
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if r not in rows:                        # right click on a line outside the selection: that line only
            rows = [r]
            self.table.blockSignals(True)
            self.table.selectRow(r)
            self.table.blockSignals(False)
        self._build_row_menu(r, rows).exec(self.table.viewport().mapToGlobal(pos))

    def _build_row_menu(self, r: int, rows):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.setToolTipsVisible(True)
        acts = [m.addAction("Delete line" if len(rows) == 1 else tr("Delete {a} lines").replace("{a}", str(len(rows))),
                            lambda: self.delete_lines(rows))]
        m.addSeparator()
        acts.append(m.addAction("Add line above", lambda: self.add_line(r, above=True)))
        acts.append(m.addAction("Add line below", lambda: self.add_line(r, above=False)))
        m.addSeparator()
        starts = [self.src[i].start for i in rows if 0 <= i < len(self.src)]
        edited = self.edited_rows()
        sub = m.addMenu("Make the voice again")
        sub.setToolTipsVisible(True)
        a_sel = sub.addAction(tr("Selected lines ({a})").replace("{a}", str(len(rows))),
                              lambda: self.remake_voice.emit(starts))
        a_sel.setToolTip("Read these lines again, then play")
        a_ed = sub.addAction(tr("All edited lines ({a})").replace("{a}", str(len(edited))),
                             lambda: self.remake_voice.emit([self.src[i].start for i in self.edited_rows()]))
        a_ed.setToolTip("Lines changed since the last narration")
        a_ed.setEnabled(bool(edited))
        v = sub.menuAction()
        acts.append(v)
        why = self._lines_blocked()
        if why:
            for a in acts:
                a.setEnabled(False)
                a.setToolTip(why)
        elif self.project is None:
            v.setEnabled(False)
        return m

    def _commit_edit(self):
        """A cell still being typed in is closed first (what was typed is kept): the table is filled again."""
        if self.table.state() == QAbstractItemView.EditingState:
            w = QApplication.focusWidget()
            if w is not None:
                w.clearFocus()

    def _lines_changed(self, select: int, seek: bool = False, edit: bool = False):
        """The list of lines changed (a line added or deleted): show it, save it, tell the others.
        The narration was made for the old lines (its times are counted by line number): it is out of date now
        and has to be made again, so the table goes back to the lines' own times at once. It is saved before the
        others are told, so that nobody reads the old narration times against the new line numbers."""
        self.voice_times = None
        self._voice_tip()
        self._dirty.update({"src", "tr"} if self.tr else {"src"})
        bar = self.table.verticalScrollBar().value()
        self._read_voices()                      # voice marks follow a line by its start time, so they stay right
        self._fill()
        self.table.verticalScrollBar().setValue(bar)
        n = self.table.rowCount()
        if n:
            r = max(0, min(select, n - 1))
            self.table.blockSignals(True)
            self.table.setCurrentCell(r, C_SRC)
            self.table.blockSignals(False)
            self.table.scrollToItem(self.table.item(r, C_SRC))
            if seek:
                self.seek.emit(self._time(r)[0] + 0.01)
            if edit:                             # the new line is empty: type its text straight away
                def start_edit():                # (if a click or an undo came first, nothing to open any more)
                    it = self.table.item(r, C_SRC)
                    if it is not None and self.table.currentRow() == r:
                        self.table.editItem(it)
                QTimer.singleShot(0, start_edit)
        self._save()
        self.cues_changed.emit()

    def add_line(self, r: int, above: bool = True):
        """An empty line above / below line r. Its time is a short slot in the gap between the two lines."""
        if self._lines_blocked() or not 0 <= r < len(self.src):
            return
        self._commit_edit()
        k = r if above else r + 1
        a, b = slot_between(self.src[k - 1] if k > 0 else None, self.src[k] if k < len(self.src) else None)
        self._remember("add line")
        self.src.insert(k, Cue(a, b, ""))
        if self.tr:
            while len(self.tr) < len(self.src) - 1:
                s = self.src[len(self.tr)]
                self.tr.append(Cue(s.start, s.end, ""))
            self.tr.insert(k, Cue(a, b, ""))
        self._lines_changed(k, seek=True, edit=True)

    def insert_at(self, a: float, b: float) -> int:
        """A new empty line from a to b (the timeline's New line button): put in time order, Ctrl+Z takes it
        away. Returns its row (-1: lines can not be added now)."""
        if self._lines_blocked():
            return -1
        self._commit_edit()
        import bisect
        k = bisect.bisect_right([c.start for c in self.src], a)
        self._remember("add line")
        self.src.insert(k, Cue(a, b, ""))
        if self.tr:
            while len(self.tr) < len(self.src) - 1:
                s = self.src[len(self.tr)]
                self.tr.append(Cue(s.start, s.end, ""))
            self.tr.insert(k, Cue(a, b, ""))
        self._lines_changed(k)
        return k

    def set_line_text(self, i: int, kind: str, text: str) -> bool:
        """The words of line i ('original' / 'translation') typed elsewhere (Style, for a line chosen on the
        timeline): kept in the table's history, so Ctrl+Z brings the old words back. False: nothing changed."""
        text = text.strip()
        if not 0 <= i < len(self.src):
            return False
        if kind == "translation":
            while len(self.tr) < len(self.src):
                s = self.src[len(self.tr)]
                self.tr.append(Cue(s.start, s.end, ""))
            cue, col, key = self.tr[i], C_TR, "tr"
        else:
            cue, col, key = self.src[i], C_SRC, "src"
        if cue.text == text:
            return False
        self._remember("line edit")
        cue.text = text
        self._dirty.add(key)
        it = self.table.item(i, col)
        if it is not None:
            self.table.blockSignals(True)
            it.setText(text.replace("\n", " / "))
            self.table.blockSignals(False)
        self._save_timer.stop()
        self._save()
        self.cues_changed.emit()
        return True

    def delete_lines(self, rows):
        rows = sorted({r for r in rows if 0 <= r < len(self.src)})
        if not rows or self._lines_blocked():
            return
        self._commit_edit()
        if len(rows) == 1:
            r = rows[0]
            text = self._short(self.src[r].text or (self.tr[r].text if r < len(self.tr) else ""))
            ask = (tr("Delete line {a}?\n“{b}”\n(Ctrl+Z brings it back.)").replace("{b}", text) if text
                   else tr("Delete line {a}?\n(Ctrl+Z brings it back.)")).replace("{a}", str(r + 1))
        else:
            ask = tr("Delete {a} lines?\n(Ctrl+Z brings them back.)").replace("{a}", str(len(rows)))
        if not confirm(self, ask, "line_delete", "Delete", danger=True):
            return
        self._remember("delete line")
        gone = set(rows)
        keep = [i for i in range(len(self.src)) if i not in gone]
        self.src = [self.src[i] for i in keep]
        if self.tr:
            self.tr = [self.tr[i] for i in keep if i < len(self.tr)]
        self._lines_changed(rows[0])

    # ------------------------------------------------------------ find and replace
    def open_find(self) -> bool:
        """Ctrl+F / Ctrl+H: show the find and replace bar, or jump into its box when it is already there."""
        if not self.src or not self.isVisible():
            return False
        if self.b_find.isChecked():
            self.find.focus_find()
        else:
            self.b_find.setChecked(True)                  # -> show_find(True)
        return True

    def show_find(self, on: bool):
        self.find.setVisible(on)
        if on:
            self.find.set_has_translation(bool(self.tr))
            self.find.focus_find()
            self._find_refresh(anchor=self._find_row_anchor())
        else:
            self._find_hits, self._find_at = [], -1
            self._find_delegate.marks = {}
            self.table.viewport().update()

    def _find_row_anchor(self):
        return (max(0, self.table.currentRow()), 0, 0, 0)

    def _find_refresh(self, anchor=None, scroll=False):
        """Look for the text again in the lines as they are now. The current match stays where it was (or is
        the first one from `anchor` on)."""
        if not self.find.isVisible():
            return
        case, whole, start = self.find.options_on()
        old = self._find_hits[self._find_at].key() if 0 <= self._find_at < len(self._find_hits) else None
        self._find_hits = fnd.find_all([c.text for c in self.src], [c.text for c in self.tr], self.find.needle(),
                                       self.find.kinds(), case, whole, start)
        at = -1
        if self._find_hits:
            key = anchor or old or (0, 0, 0, 0)
            at = next((i for i, m in enumerate(self._find_hits) if m.key() >= key), 0)
        self._find_at = at
        self._find_paint()
        if scroll and at >= 0:
            self._find_show(at, seek=False)

    def _find_paint(self):
        marks = {}
        for i, m in enumerate(self._find_hits):
            col = C_SRC if m.kind == fnd.SRC else C_TR
            text = (self.src if m.kind == fnd.SRC else self.tr)[m.row].text
            off = sum(len(l) + 3 for l in text.split("\n")[:m.line])      # the table shows " / " for a line break
            marks.setdefault((m.row, col), []).append((off + m.start, off + m.end, i == self._find_at))
        self._find_delegate.marks = marks
        self.table.viewport().update()
        self.find.set_count(self._find_at, len(self._find_hits))

    def _find_show(self, i: int, seek: bool = True):
        m = self._find_hits[i]
        col = C_SRC if m.kind == fnd.SRC else C_TR
        self.table.blockSignals(True)
        self.table.setCurrentCell(m.row, col)
        self.table.blockSignals(False)
        it = self.table.item(m.row, col)
        if it is not None:
            self.table.scrollToItem(it, QAbstractItemView.PositionAtCenter)
        if seek:
            self.seek.emit(self._time(m.row)[0] + 0.01)

    def _find_changed(self):
        self._find_refresh(anchor=self._find_row_anchor(), scroll=True)

    def _find_step(self, d: int):
        n = len(self._find_hits)
        if not n:
            return
        self._find_at = (self._find_at + d) % n if self._find_at >= 0 else 0
        self._find_paint()
        self._find_show(self._find_at)

    def _find_busy(self) -> bool:
        if getattr(self, "_fit_job", None):
            self.find.flash(tr("Wait: the lines are being fitted"))
            return True
        return False

    def _find_put(self, row: int, kind: str, text: str):
        (self.src if kind == fnd.SRC else self.tr)[row].text = text
        it = self.table.item(row, C_SRC if kind == fnd.SRC else C_TR)
        if it is not None:
            self.table.blockSignals(True)
            it.setText(text.replace("\n", " / "))
            self.table.blockSignals(False)

    def _find_touched(self, kinds):
        self._hl = None
        self._dirty.update(kinds)
        self._save_timer.start()
        self.cues_changed.emit()

    @staticmethod
    def _short(text: str, n: int = 60) -> str:
        text = " ".join(text.split())
        return text if len(text) <= n else text[:n - 1] + "…"

    def _replace_one(self):
        if self._find_busy() or not 0 <= self._find_at < len(self._find_hits):
            return
        m = self._find_hits[self._find_at]
        repl = self.find.replacement()
        found = (self.src if m.kind == fnd.SRC else self.tr)[m.row].text.split("\n")[m.line][m.start:m.end]
        ask = (tr("Replace “{a}” with “{b}”?\n(Ctrl+Z brings it back.)").replace("{b}", self._short(repl))
               if repl else tr("Delete “{a}”?\n(Ctrl+Z brings it back.)"))
        if not confirm(self, ask.replace("{a}", self._short(found)), "find_replace_one", "Replace"):
            return
        self._remember("find and replace")
        cue = (self.src if m.kind == fnd.SRC else self.tr)[m.row]
        lines = cue.text.count("\n") + 1
        new = fnd.replace_in_text(cue.text, [(m.line, m.start, m.end)], repl)
        self._find_put(m.row, m.kind, new)
        self._find_touched({m.kind})
        dropped = new.count("\n") + 1 < lines            # a line became empty and went away
        self._find_refresh(anchor=(m.row, 0 if m.kind == fnd.SRC else 1, m.line, 0 if dropped else m.start + len(repl)))
        if self._find_at >= 0:
            self._find_show(self._find_at)

    def _replace_all(self):
        if self._find_busy() or not self._find_hits:
            return
        repl = self.find.replacement()
        by = {}
        for m in self._find_hits:
            by.setdefault((m.row, m.kind), []).append((m.line, m.start, m.end))
        n = len(self._find_hits)
        needle = self.find.needle()
        ask = (tr("Replace {a} matches of “{b}” with “{c}”?\n(Ctrl+Z brings them back.)").replace("{c}", self._short(repl))
               if repl else tr("Delete {a} matches of “{b}”?\n(Ctrl+Z brings them back.)"))
        if not confirm(self, ask.replace("{a}", str(n)).replace("{b}", self._short(needle)), "find_replace_all",
                       "Replace all", danger=True):          # No is the default here: a slip of the finger is safe
            return
        self._remember("find and replace")                # one Ctrl+Z brings all of it back
        with wait_cursor():
            for (r, kind), spans in by.items():
                cue = (self.src if kind == fnd.SRC else self.tr)[r]
                self._find_put(r, kind, fnd.replace_in_text(cue.text, spans, repl))
        self._find_touched({kind for _row, kind in by})
        self._find_refresh(anchor=(0, 0, 0, 0))
        self.find.flash(tr("{a} replaced").replace("{a}", str(n)))

    def forget(self, video: str):
        """This video's project is being deleted: drop unsaved edits (they must not write it back) and let go."""
        if self.project is not None and self.project.video == os.path.abspath(video):
            self._save_timer.stop()
            self._dirty.clear()
            self.load("")

    def _flush(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()

    def _save(self):
        p = self.project
        if not p or not self._dirty:
            return
        try:
            if "src" in self._dirty:
                save_srt(self.src, p.src_srt)
                p.publish(SRC_SRT, p.meta.get("source_lang") or "orig")
            if "tr" in self._dirty and self.tr:
                save_srt(self.tr, p.tr_srt)
                p.meta["tr_edited"] = True
                from ..config import settings
                p.publish(TR_SRT, settings()["target_lang"])
            log.info("Subtitles edited and saved")
        except OSError as e:
            log.warning("Could not save the subtitles: %s", e)
        self._dirty.clear()

    def fit_lines(self):
        """Break every line (original and translation) again so it fits the box / the video width. Done a few
        lines at a time (the window keeps working, the table changes as it goes): thousands of lines take a
        while."""
        p = self.project
        if not p or not self.src or getattr(self, "_fit_job", None):
            return
        from PySide6.QtGui import QFontMetricsF
        from ..core import ffmpeg
        from ..core.fit import fit, limits_text
        from ..core.style import box_region, sub_style
        from ..steps.ocr import region_px
        info = p.meta.get("info") or {}
        if not info.get("width"):
            try:
                info = ffmpeg.probe(p.video)
            except Exception:  # noqa: BLE001
                return
        W, H = info.get("width") or 1280, info.get("height") or 720
        st = sub_style(H)
        region = box_region(p)
        if region:
            _x, _y, bw, bh = region_px(region, W, H)
            max_w, box_h = bw - 16, bh
        else:
            max_w, box_h = W - 40, None
        fonts = {}

        def measure_for(px):
            if px not in fonts:
                from .preview import qt_font
                f = qt_font(st["font"], st["bold"])
                f.setPixelSize(px)
                fm = QFontMetricsF(f)
                cache = {}

                def measure(s, fm=fm, cache=cache):
                    w = cache.get(s)
                    if w is None:
                        w = cache[s] = fm.horizontalAdvance(s)
                    return w
                fonts[px] = measure
            return fonts[px]
        n_lines, n_words = limits_text()
        where = "the box" if region else "the video width"
        work = [(c, "src", i, C_SRC) for i, c in enumerate(self.src)] + \
               [(c, "tr", i, C_TR) for i, c in enumerate(self.tr)]
        self._remember("fit the lines")                 # all of it can be undone at once
        job = {"i": 0, "changed": 0, "project": p, "base": self.count.text()}
        self._fit_job = job
        self.b_fit.setEnabled(False)
        timer = QTimer(self)
        timer.setInterval(15)
        BATCH = 40                                        # lines per step: the window stays responsive

        def stop(done):
            timer.stop()
            timer.deleteLater()
            self._fit_job = None
            self.b_fit.setEnabled(bool(self.src))
            if not done:
                return
            self._save()
            self.cues_changed.emit()
            n = job["changed"]
            self.count.setText(job["base"] + tr(f" · {n} line{'s' if n != 1 else ''} fitted to {where}"))
            log.info("Lines fitted to %s (%d px wide): %d changed", where, max_w, n)

        def step():
            if self.project is not job["project"]:        # another video was opened meanwhile: stop
                stop(False)
                return
            end = min(len(work), job["i"] + BATCH)
            self.table.blockSignals(True)
            for c, key, r_, col in work[job["i"]:end]:
                new = fit(c.text, measure_for, max_w, st["size"], box_h, n_lines, n_words)
                if new != c.text:
                    c.text = new
                    job["changed"] += 1
                    self._dirty.add(key)
                    it = self.table.item(r_, col)
                    if it is not None:
                        it.setText(new.replace("\n", " / "))
            self.table.blockSignals(False)
            job["i"] = end
            self.count.setText(tr("Fitting the lines… {a}/{b}").replace("{a}", str(end))
                               .replace("{b}", str(len(work))))
            if end >= len(work):
                stop(True)
        timer.timeout.connect(step)
        timer.start()

    def open_output(self):
        if not self.project:
            return
        outs = self.project.meta.get("outputs", {})
        sel = outs.get(TR_SRT) or outs.get(SRC_SRT)
        open_folder(self.project.out_dir, sel if sel and os.path.isfile(sel) else None)
