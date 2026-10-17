"""Subtitle table under the preview: time, original, translation. Edit a cell to fix a line; changes are saved
to the project (and the .srt in the output folder) a moment later. Click a line to jump the video there."""
import logging
import os

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core.project import SRC_SRT, TR_SRT, Project
from ..core.subtitle import Cue, cue_at, fmt_clock, load_srt, parse_time, save_srt
from .queue import open_folder
from .widgets import IconButton, label, row, section

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
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(C_VOICE, QHeaderView.Fixed)
        self.table.setColumnWidth(C_VOICE, 52)
        self.table.horizontalHeaderItem(C_VOICE).setToolTip(
            "Women's or men's narration voice (in testing). Click a line to set it: mark a line or two of each "
            "voice and the other lines that sound like them follow")
        h.setSectionResizeMode(C_SRC, QHeaderView.Stretch)
        h.setSectionResizeMode(C_TR, QHeaderView.Stretch)
        self.voices = []                   # [(gender, how)] per row: how = you | learned | auto
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.b_voice = IconButton("voice_time", "", checkable=True)
        from ..config import settings
        self.b_voice.setChecked(bool(settings()["dub_sync_subs"]))
        self.b_voice.toggled.connect(self._voice_toggled)
        self.voice_times = None      # {row: (start, end)} when the narration read the lines (None: no narration)
        self._voice_tip()
        lay.addWidget(row(section("Subtitles"), self.count, None, self.b_fit, self.b_voice, self.b_redo,
                          self.b_import, self.b_open))
        self.b_redo.clicked.connect(self.redo_requested.emit)
        self.b_import.clicked.connect(self._import_menu)
        lay.addWidget(self.table, 1)
        self.hint = label("No subtitles yet. Press Export (the video, or only the subtitles), or use your own .srt.", muted=True, wrap=True)
        lay.addWidget(self.hint)
        self.filler = QWidget()          # takes the room of the table while there is none: the header stays on top
        lay.addWidget(self.filler, 1)
        self.table.cellClicked.connect(self._clicked)
        self.table.itemChanged.connect(self._edited)
        self.b_open.clicked.connect(self.open_output)
        self.b_fit.clicked.connect(self.fit_lines)
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
        self._voice_tip()
        if changed and on:
            self._refill_later()

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
        self._flush()
        self.project = Project(video) if video else None
        self.src = load_srt(self.project.src_srt) if self.project and self.project.has(SRC_SRT) else []
        tr = load_srt(self.project.tr_srt) if self.project and self.project.has(TR_SRT) else []
        if tr and len(tr) != len(self.src):          # match by start time when the counts differ
            starts = {round(c.start, 1): c for c in tr}
            tr = [starts.get(round(c.start, 1), Cue(c.start, c.end, "")) for c in self.src]
        self.tr = tr
        self._read_voices()
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
        self.table.blockSignals(False)
        n = len(self.src)
        self.count.setText(f"{n} line{'s' if n != 1 else ''}" + (" · translated" if any(c.text for c in self.tr) else ""))
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

    def shown_cues(self, prefer="translation"):
        """Cues for the preview: the translation where there is one, else the original; with the narration's
        times when those are shown."""
        if prefer == "translation" and self.tr:
            cues = [Cue(s.start, s.end, (t.text or s.text)) for s, t in zip(self.src, self.tr)]
        elif not self.voice_mode():
            return self.src
        else:
            cues = [Cue(s.start, s.end, s.text) for s in self.src]
        if self.voice_mode():
            for i, c in enumerate(cues):
                if i in self.voice_times:
                    c.start, c.end = self.voice_times[i]
        return cues

    def highlight(self, t: float):
        if not self.src or not self.isVisible() or self.table.state() == QAbstractItemView.EditingState:
            return
        i = cue_at(self.shown_cues("original") if self.voice_mode() else self.src, t)
        if i >= 0 and i != self.table.currentRow():
            self.table.blockSignals(True)
            self.table.selectRow(i)
            self.table.scrollToItem(self.table.item(i, 0), QAbstractItemView.PositionAtCenter)
            self.table.blockSignals(False)

    # ------------------------------------------------------------ edits
    def _clicked(self, r, c):
        if r >= len(self.src):
            return
        if c == C_VOICE:
            self._cycle_voice(r)
            return
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

    def _edited(self, item):
        r, c = item.row(), item.column()
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
        self._save_timer.start()
        self.cues_changed.emit()

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
        """Break every line (original and translation) again so it fits the box / the video width."""
        p = self.project
        if not p or not self.src:
            return
        from PySide6.QtGui import QFontMetricsF
        from ..core import ffmpeg
        from ..core.fit import fit
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
                fonts[px] = lambda s, fm=fm: fm.horizontalAdvance(s)
            return fonts[px]
        changed = 0
        for cues, key in ((self.src, "src"), (self.tr, "tr")):
            for c in cues:
                new = fit(c.text, measure_for, max_w, st["size"], box_h)
                if new != c.text:
                    c.text = new
                    changed += 1
                    self._dirty.add(key)
        self._fill()
        self._save()
        self.cues_changed.emit()
        where = "the box" if region else "the video width"
        self.count.setText(self.count.text() + f" · {changed} line{'s' if changed != 1 else ''} fitted to {where}")
        log.info("Lines fitted to %s (%d px wide): %d changed", where, max_w, changed)

    def open_output(self):
        if not self.project:
            return
        outs = self.project.meta.get("outputs", {})
        sel = outs.get(TR_SRT) or outs.get(SRC_SRT)
        open_folder(self.project.out_dir, sel if sel and os.path.isfile(sel) else None)
