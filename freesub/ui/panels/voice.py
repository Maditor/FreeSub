"""Listening to a voice (Speaker) and the pronunciation dictionary window."""
import logging
import os
import tempfile
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from ...i18n import tr
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...config import settings
from ...tts import voices
from ...tts.pronounce import pronouncer
from ..widgets import button, label, row

log = logging.getLogger(__name__)

SAMPLE = ("Anh có thấy Mike đâu không? Nó đang xem Netflix với Jennifer ở New York. "
          "Naruto và Sasuke cũng tới, OK?")
SOURCE_NAMES = {"user": "Yours", "builtin": "Built-in", "ai": "AI", "rules": "Rules", "letters": "Letters",
                "romaji": "Romaji", "none": "-"}


class _Sig(QObject):
    done = Signal(str, str)        # wav/mp3 path or "", error


class Speaker(QObject):
    """Synthesise on a thread, play with Qt Multimedia."""
    state = Signal(str)            # text for the status label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.player.setAudioOutput(self.out)
        self.sig = _Sig()
        self.sig.done.connect(self._play)
        self.busy = False
        self.tmp = os.path.join(tempfile.gettempdir(), "freesub_listen")
        os.makedirs(self.tmp, exist_ok=True)
        self.n = 0

    def say(self, text: str, respell=True, gender="f"):
        """gender: 'f' = the women's voice, 'm' = the men's voice (Piper has one voice for both)."""
        if settings().get("voice_mode", "one") != "split":
            gender = "f"
        if self.busy or not text.strip():
            return
        s = settings()
        engine = s.get("tts_engine", "edge")
        speed = int(s.get("tts_speed", 100))
        from ...tts.abbrev_vi import expand
        text = expand(text)
        spoken = pronouncer().respell_text(text)[0] if respell else text
        self.n += 1
        if engine == "piper":
            if not voices.piper_installed():
                self.state.emit("Piper is not installed: run setup.bat again.")
                return
            if not voices.piper_ready("vi"):
                self.state.emit("Download the Vietnamese voice first.")
                return
            path = os.path.join(self.tmp, f"say{self.n % 4}.wav")
            fn = lambda: voices.piper_synth(spoken, "vi", path, speed)       # noqa: E731
        elif engine == "kokoro":
            from ...tts import kokoro
            if not kokoro.g2p_installed():
                self.state.emit("The Kokoro text reader is not installed: run setup.bat again.")
                return
            if not kokoro.ready():
                self.state.emit("Download the Kokoro voices first.")
                return
            path = os.path.join(self.tmp, f"say{self.n % 4}.wav")
            voice = (s.get("tts_kokoro_male") or "tuan_ngoc") if gender == "m" else \
                (s.get("tts_kokoro_female") or "ngoc_huyen")
            fn = lambda: kokoro.synth(spoken, voice, path, speed)       # noqa: E731
        else:
            if not voices.edge_installed():
                self.state.emit("edge-tts is not installed: run setup.bat again.")
                return
            path = os.path.join(self.tmp, f"say{self.n % 4}.mp3")
            voice = (s.get("tts_edge_male") or "vi-VN-NamMinhNeural") if gender == "m" else \
                (s.get("tts_edge_female") or "vi-VN-HoaiMyNeural")
            fn = lambda: voices.edge_synth(spoken, voice, path, speed)       # noqa: E731
        self.busy = True
        self.player.stop()
        self.player.setSource(QUrl())
        self.state.emit("Making the voice…")

        def work():
            try:
                fn()
                self.sig.done.emit(path, "")
            except Exception as e:  # noqa: BLE001
                log.warning("Listen failed: %s", e)
                self.sig.done.emit("", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _play(self, path, err):
        self.busy = False
        if err:
            self.state.emit(f"Could not make the voice: {err}")
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.state.emit("Playing")

    def stop(self):
        self.player.stop()
        self.state.emit("")


class DictionaryDialog(QDialog):
    def __init__(self, speaker: Speaker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pronunciation dictionary")
        self.resize(640, 620)
        self.speaker = speaker
        self.p = pronouncer()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.kind = QComboBox()
        for k, t in (("", "All"), ("user", "Yours"), ("rules", "From rules"), ("ai", "AI"), ("builtin", "Built-in")):
            self.kind.addItem(t, k)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Word", "Read as", "From"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.count = label("", muted=True)

        b_add = button("Add", self.add)
        b_del = button("Remove", self.remove)
        b_listen = button("Listen", self.listen, primary=True)
        b_close = button("Close", self.accept)

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.kind)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Double-click \"Read as\" to correct a word. Write it as it should sound, in Vietnamese "
                            "syllables joined by \"-\" (e.g. Mai-cơ, Phây-búc). Your version always wins.",
                            muted=True, wrap=True))
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(row(self.count, None, b_add, b_del, b_listen, b_close))

        self.search.textChanged.connect(self.fill)
        self.kind.currentIndexChanged.connect(self.fill)
        self.table.itemChanged.connect(self._edited)
        self.fill()

    def fill(self):
        q = self.search.text().strip().lower()
        kind = self.kind.currentData()
        rows = [r for r in self.p.rows() if (not q or q in r[0] or q in r[1].lower()) and (not kind or r[2] == kind)]
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for i, (w, r, src) in enumerate(rows):
            a = QTableWidgetItem(w)
            a.setFlags(a.flags() & ~Qt.ItemIsEditable)
            b = QTableWidgetItem(r)
            c = QTableWidgetItem(tr(SOURCE_NAMES.get(src, src)))
            c.setFlags(c.flags() & ~Qt.ItemIsEditable)
            if src == "user":
                c.setForeground(self.palette().highlight())
            self.table.setItem(i, 0, a)
            self.table.setItem(i, 1, b)
            self.table.setItem(i, 2, c)
        self.table.blockSignals(False)
        self.count.setText(f"{len(rows)} words")

    def _edited(self, item):
        if item.column() != 1:
            return
        word = self.table.item(item.row(), 0).text()
        self.p.set_user(word, item.text())
        self.table.blockSignals(True)
        self.table.item(item.row(), 2).setText(tr(SOURCE_NAMES["user"]))
        self.table.blockSignals(False)

    def add(self):
        word, ok = QInputDialog.getText(self, "Add a word", "Word or name (as written):")
        if not ok or not word.strip():
            return
        guess = self.p.lookup(word.strip(), remember=False)[0]
        reading, ok = QInputDialog.getText(self, "Add a word", f"How to read \"{word.strip()}\":", text=guess)
        if ok and reading.strip():
            self.p.set_user(word.strip(), reading.strip())
            self.search.setText(word.strip().lower())
            self.fill()

    def remove(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        words = [self.table.item(r, 0).text() for r in rows]
        builtin = [w for w in words if self.table.item(rows[words.index(w)], 2).text() == tr(SOURCE_NAMES["builtin"])]
        if builtin and len(builtin) == len(words):
            QMessageBox.information(self, "FreeSub", "Built-in words cannot be removed; edit them to use your "
                                                     "own reading instead.")
            return
        for w in words:
            self.p.forget(w)
        self.fill()

    def listen(self):
        r = self.table.currentRow()
        if r >= 0:
            self.speaker.say(self.table.item(r, 1).text().replace("-", " "), respell=False)
