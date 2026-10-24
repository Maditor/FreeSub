"""Settings: General (look, output folder, ffmpeg), Translation (API keys, models, connection test) and
Advanced (speech model and tuning, video encoder): things most people never change."""
import os
import sys
import threading

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QLineEdit, QMessageBox, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from ..config import data_dir, logs_dir, models_dir, settings, ui_language, user_dir
from ..i18n import LANGS, Keep
from ..core import ffmpeg, models
from ..core.jobs import Cancelled
from ..translate import ai
from .widgets import button, form_row, heading, label, row, tick_row


class _Sig(QObject):
    result = Signal(str, str)      # which label, text


class SettingsDialog(QDialog):
    def __init__(self, parent=None, tab: str = "general"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self.sig = _Sig()
        self.sig.result.connect(self._result)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._translation(), "Translation")
        tabs.addTab(self._downloads(), "Downloads")
        tabs.addTab(self._advanced(), "Advanced")
        tabs.addTab(self._about(), "About")
        tabs.setCurrentIndex({"translation": 1, "downloads": 2, "advanced": 3, "about": 4}.get(tab, 0))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)

    # ------------------------------------------------------------ General
    def _general(self):
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        self.theme = QComboBox()
        for k, t in (("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(t, k)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s["theme"])))
        self.lang = QComboBox()
        self.lang.addItem("Same as Windows", "auto")
        for k, t in LANGS:
            self.lang.addItem(Keep(t), k)
        self.lang.setCurrentIndex(max(0, self.lang.findData(s["language"])))
        self.lang.setToolTip("The app opens again in the new language.")
        self.restart = False
        self.r_video = QRadioButton("Next to each video, in a freesub_<video name> folder")
        self.r_custom = QRadioButton("In one folder:")
        g = QButtonGroup(self)
        g.addButton(self.r_video)
        g.addButton(self.r_custom)
        (self.r_custom if s["output_location"] == "custom" else self.r_video).setChecked(True)
        self.root = QLineEdit(s["output_root"])
        self.root.setPlaceholderText("e.g. D:\\Subtitles")
        self.b_root = button("Browse", self._browse_root)
        self.ff = QLineEdit(s["ffmpeg_path"])
        self.ff.setPlaceholderText("Empty = the ffmpeg folder next to FreeSub, then PATH")
        self.ff_state = label("", muted=True, wrap=True)
        lay.addWidget(form_row("Theme", self.theme))
        lay.addWidget(form_row("Language", self.lang))
        self.title_bar = QComboBox()
        for k, t in ((False, "Borderless"), (True, "Windows")):
            self.title_bar.addItem(t, k)
        self.title_bar.setCurrentIndex(1 if s["native_titlebar"] else 0)
        self.title_bar.setToolTip("Borderless: FreeSub's own title bar. Windows: the usual Windows one. "
                                  "Used after FreeSub is opened again.")
        lay.addWidget(form_row("Title bar", self.title_bar))
        self.ask_close = QCheckBox("Ask before closing FreeSub")
        self.ask_close.setChecked(bool(s["confirm_close"]))
        lay.addWidget(tick_row(self.ask_close))
        self.ask_actions = QCheckBox("Ask before Run and before deleting (clips, videos, projects)")
        self.ask_actions.setChecked("*" not in (s.get("confirm_skip") or []))
        lay.addWidget(tick_row(self.ask_actions))
        lay.addWidget(heading("Where results go"))
        lay.addWidget(self.r_video)
        lay.addWidget(row(self.r_custom, self.root, self.b_root))
        lay.addWidget(heading("ffmpeg"))
        lay.addWidget(row(self.ff, button("Browse", self._browse_ff), button("Check", self._test_ff)))
        lay.addWidget(self.ff_state)
        lay.addWidget(heading("Folders"))
        lay.addWidget(row(button("Work files", lambda: self._open(os.path.join(data_dir(), "projects"))),
                          button("Models", lambda: self._open(models_dir())),
                          button("Log", lambda: self._open(logs_dir())),
                          button("Settings", lambda: self._open(user_dir())), None))
        lay.addStretch(1)
        self._test_ff()
        return w

    # ------------------------------------------------------------ Translation
    def _translation(self):
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(heading("Google Gemini"))
        self.g_key = QLineEdit(s["gemini_key"])
        self.g_key.setEchoMode(QLineEdit.Password)
        self.g_key.setPlaceholderText("API key from aistudio.google.com")
        self.g_model = QComboBox()
        self.g_model.setEditable(True)
        self.g_model.addItems(ai.GEMINI_MODELS)
        self.g_model.setCurrentText(s["gemini_model"])
        self.g_state = label("", muted=True, wrap=True)
        lay.addWidget(form_row("API key", self.g_key))
        lay.addWidget(form_row("Model", self.g_model))
        lay.addWidget(row(self.g_state, None, button("Test", lambda: self._test("gemini"))))
        lay.addWidget(heading("Cloudflare Workers AI"))
        self.c_acc = QLineEdit(s["cloudflare_account"])
        self.c_acc.setPlaceholderText("Account ID")
        self.c_tok = QLineEdit(s["cloudflare_token"])
        self.c_tok.setEchoMode(QLineEdit.Password)
        self.c_tok.setPlaceholderText("API token with Workers AI permission")
        self.c_model = QComboBox()
        self.c_model.setEditable(True)
        self.c_model.addItems(ai.CF_MODELS)
        self.c_model.setCurrentText(s["cloudflare_model"])
        self.c_state = label("", muted=True, wrap=True)
        lay.addWidget(form_row("Account ID", self.c_acc))
        lay.addWidget(form_row("API token", self.c_tok))
        lay.addWidget(form_row("Model", self.c_model))
        lay.addWidget(row(self.c_state, None, button("Test", lambda: self._test("cloudflare"))))
        lay.addSpacing(6)
        self.go_state = label("", muted=True, wrap=True)
        lay.addWidget(heading("Google Translate"))
        lay.addWidget(row(self.go_state, None, button("Test", lambda: self._test("google"))))
        self.fallback = QCheckBox("If a service fails, use the other ones")
        self.fallback.setChecked(bool(s["translate_fallback"]))
        lay.addWidget(tick_row(self.fallback))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------ Advanced
    def _advanced(self):
        from ..steps import export as ex
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        def spin(lo, hi, value, step=1, suffix="", dec=None):
            sp = QDoubleSpinBox() if dec is not None else QSpinBox()
            if dec is not None:
                sp.setDecimals(dec)
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(value)
            sp.setSuffix(suffix)
            return sp

        def combo(items, value):
            c = QComboBox()
            for k, t in items:
                c.addItem(t, k)
            c.setCurrentIndex(max(0, c.findData(value)))
            return c

        lay.addWidget(heading("Speech recognition"))
        self.a_model = QComboBox()
        for name, (_repo, size, note, _l) in models.WHISPER.items():
            ready = " · downloaded" if models.whisper_ready(name) else ""
            self.a_model.addItem(f"{name} · {size} · {note}{ready}", name)
        self.a_model.setCurrentIndex(max(0, self.a_model.findData(s["whisper_model"])))
        self.a_beam = spin(1, 10, int(s["whisper_beam"]))
        self.a_device = combo([("auto", "Auto (NVIDIA GPU if available)"), ("cuda", "NVIDIA GPU (CUDA)"),
                               ("cpu", "CPU")], s["whisper_device"])
        self.a_vad = QCheckBox("Skip music and silence (VAD)")
        self.a_vad.setChecked(bool(s["whisper_vad"]))
        self.a_filt = QCheckBox("Drop made-up lines (\"thanks for watching\"...)")
        self.a_filt.setChecked(bool(s["whisper_filter"]))
        lay.addWidget(form_row("Model", self.a_model, "Downloads on first use"))
        lay.addWidget(form_row("Beam size", self.a_beam, "Higher = a little more accurate, slower"))
        lay.addWidget(form_row("Run on", self.a_device))
        lay.addWidget(tick_row(self.a_vad))
        lay.addWidget(tick_row(self.a_filt))
        lay.addWidget(heading("Separate voice and background"))
        self.a_sep_subs = QCheckBox("Before making subtitles")
        self.a_sep_subs.setToolTip("Clearer recognition in films and anime with music")
        self.a_sep_subs.setChecked(bool(s["whisper_separate"]))
        self.a_sep_dub = QCheckBox("For the narration")
        self.a_sep_dub.setToolTip("Three volumes in Narration: the narration, the background (music, effects) "
                                  "and the original voice. Off: the narration and the whole original sound")
        self.a_sep_dub.setChecked(bool(s["dub_separate"]))
        lay.addWidget(tick_row(self.a_sep_subs))
        lay.addWidget(tick_row(self.a_sep_dub))
        lay.addWidget(heading("Reading hard subtitles (OCR)"))
        self.a_fps = spin(0.5, 10, float(s["ocr_fps"]), 0.5, " frames per second", 1)
        lay.addWidget(form_row("Frames read", self.a_fps, "More = more exact timing, slower"))
        self.a_thorough = QCheckBox("Read every frame (takes longer)")
        self.a_thorough.setToolTip("A little more exact when the picture behind the subtitles is very busy")
        self.a_thorough.setChecked(bool(s["ocr_thorough"]))
        lay.addWidget(tick_row(self.a_thorough))
        lay.addWidget(heading("Batch"))
        self.a_lanes = combo([("auto", "Auto (two when this PC has room)"), ("1", "One at a time"),
                              ("2", "Two at a time")], str(s["parallel_videos"]))
        lay.addWidget(form_row("Videos at once", self.a_lanes,
                               "Two at a time: while one video gets its narration and export, the next one's "
                               "subtitles are made. Faster on most PCs, never more graphics memory. Auto picks "
                               "it on a PC with 4+ cores and 8+ GB of memory (and a graphics card, or 8 cores)"))
        lay.addWidget(heading("Video"))
        self.a_enc = combo([("auto", "Auto (fastest that works)")] + [(e, n) for e, _c, n in ex.ENCODERS],
                           s["export_encoder"])
        self.a_enc_state = label("", muted=True, wrap=True)
        self.a_cont = combo([("mp4", "MP4"), ("mkv", "MKV")], s["export_container"])
        lay.addWidget(form_row("Encoder", row(self.a_enc, button("Check", self._check_encoders))))
        lay.addWidget(self.a_enc_state)
        lay.addWidget(form_row("File type", self.a_cont))
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        return scroll

    # ------------------------------------------------------------ Downloads
    def _download_items(self):
        """Everything FreeSub can download: (key, title, what it is for, size, ready(), download(control), remove)."""
        from ..core import addons, ffmpeg_setup, gpu as gpu_mod
        from ..steps import separate, speakers
        from ..tts import kokoro, voices
        frozen = addons.can_download()
        items = [("ffmpeg", "ffmpeg", "Reads and makes the videos", "100 MB", ffmpeg.available,
                  ffmpeg_setup.install, None)]
        if frozen and gpu_mod.nvidia_name():
            items.append(("gpu", "NVIDIA GPU support", "Speech recognition several times faster on this PC's "
                          "NVIDIA card", addons.SIZES["gpu"], lambda: addons.installed("gpu"),
                          lambda c: addons.install("gpu", c), None))
        for name, (_repo, size, desc, _lang) in models.WHISPER.items():
            items.append((f"whisper:{name}", f"Whisper {name}", desc[:1].upper() + desc[1:], size,
                          lambda n=name: models.whisper_ready(n),
                          lambda c, n=name: models.download_whisper(n, c),
                          lambda n=name: models.remove_whisper(n)))
        items.append(("separate", "Voice separation", "Sets the voice apart from the music (Best quality, "
                      "narration over the original sound)", "64 MB", separate.model_ready, separate.download_model,
                      None))
        items.append(("speakers", "Speaker voices", "Tells the speakers apart (new line when the speaker changes)",
                      speakers.MODEL["size"], speakers.model_ready, speakers.download_model, None))

        def piper_ready():
            return voices.piper_installed() and voices.piper_ready("vi")

        def piper_get(c):
            if not voices.piper_installed() and frozen:
                addons.install("piper", c)
            voices.piper_download("vi", c)
        items.append(("piper", "Piper voice (Vietnamese)", "Offline narration voice",
                      "100 MB" if frozen else "63 MB", piper_ready, piper_get, None))
        items.append(("kokoro", "Kokoro voices (Vietnamese)", "Offline narration voices by a third party "
                      "(see About)", kokoro.SIZE.replace("about ", ""), kokoro.ready, kokoro.download_all, None))
        return items

    def _downloads(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(6)
        self.b_all = button("Download all", self._download_all, primary=True)
        lay.addWidget(row(None, self.b_all))
        lay.addSpacing(4)
        self.dl_rows = {}
        heads = {"ffmpeg": "Needed", "gpu": "Graphics card", f"whisper:{next(iter(models.WHISPER))}":
                 "Speech recognition (Whisper)",
                 "separate": "Sound", "piper": "Narration voices"}
        for key, title, desc, size, _ready, _get, remove in self._download_items():
            if key in heads:
                if key != "ffmpeg":
                    lay.addSpacing(8)
                lay.addWidget(heading(heads[key]))
            name = label(title)
            name.setStyleSheet("font-weight: 600;")
            info = label(f"{desc} · {size}", muted=True, wrap=True)
            text = QWidget()
            text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            tl = QVBoxLayout(text)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(0)
            tl.addWidget(name)
            tl.addWidget(info)
            state = label("", muted=True)
            b_get = button("Download", lambda _c=False, k=key: self._download(k))
            b_del = button("Delete", lambda _c=False, k=key: self._remove(k)) if remove else None
            widgets = [text, state, b_get] + ([b_del] if b_del else [])
            lay.addWidget(row(*widgets))
            self.dl_rows[key] = (state, b_get, b_del)
        lay.addStretch(1)
        self._refresh_downloads()
        runner = getattr(self.parent(), "runner", None)
        if runner is not None:
            runner.finished.connect(self._download_done)
            self.finished.connect(lambda _r: runner.finished.disconnect(self._download_done))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _refresh_downloads(self):
        runner = getattr(self.parent(), "runner", None)
        busy = bool(runner and runner.busy)
        for key, _t, _d, _s, ready, _g, _r in self._download_items():
            if key not in self.dl_rows:
                continue
            state, b_get, b_del = self.dl_rows[key]
            try:
                ok = bool(ready())
            except Exception:  # noqa: BLE001
                ok = False
            downloading = busy and (key == getattr(self, "_dl_key", None) or
                                    (getattr(self, "_dl_key", None) == "*" and not ok))
            state.setText("Downloading…" if downloading else "Downloaded" if ok else "")
            b_get.setVisible(not ok and not downloading)
            b_get.setEnabled(not busy)
            if b_del is not None:
                b_del.setVisible(ok and not busy)
        if hasattr(self, "b_all"):
            self.b_all.setEnabled(not busy and bool(self._missing()))

    def _missing(self):
        out = []
        for item in self._download_items():
            try:
                ok = bool(item[4]())
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                out.append(item)
        return out

    @staticmethod
    def _mb(size: str) -> float:
        try:
            n, unit = size.replace("about ", "").split()[:2]
            return float(n) * (1000 if unit.upper().startswith("G") else 1)
        except ValueError:
            return 0.0

    def _download_all(self):
        runner = getattr(self.parent(), "runner", None)
        todo = self._missing()
        if runner is None or not todo:
            return
        if runner.busy:
            QMessageBox.information(self, "FreeSub", "Wait for the current work to finish (or stop it) first.")
            return
        total = sum(self._mb(i[3]) for i in todo)
        size = f"{total / 1000:.1f} GB" if total >= 1000 else f"{total:.0f} MB"
        if QMessageBox.question(self, "FreeSub", f"Download everything that is missing?\n\n{len(todo)} items, "
                                f"about {size}. This can take a long time.") != QMessageBox.Yes:
            return
        parent = self.parent()

        def work(c):
            failed = []
            for key, title, _d, _s, _ready, get, _r in todo:
                c.check()
                try:
                    get(c)
                    if key == "gpu":
                        from ..core import gpu as gpu_mod
                        from .recipe import gpu_summary
                        gpu_mod.reset()
                        parent.sig.gpu_text.emit(gpu_summary())
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001     (one failure must not stop the rest)
                    failed.append(f"{title}: {e}")
            if failed:
                raise RuntimeError("Not downloaded: " + "; ".join(failed))
        self._dl_key = "*"
        runner.run_task(work, "Downloading everything")
        self._refresh_downloads()

    def _download(self, key):
        runner = getattr(self.parent(), "runner", None)
        item = next((i for i in self._download_items() if i[0] == key), None)
        if runner is None or item is None:
            return
        if runner.busy:
            QMessageBox.information(self, "FreeSub", "Wait for the current work to finish (or stop it) first.")
            return
        _k, title, _d, _s, _ready, get, _r = item

        def work(c):
            get(c)
            if key == "gpu":
                from ..core import gpu as gpu_mod
                from .recipe import gpu_summary
                gpu_mod.reset()
                self.parent().sig.gpu_text.emit(gpu_summary())
        self._dl_key = key
        runner.run_task(work, f"Downloading {title}")
        self._refresh_downloads()

    def _download_done(self, *_):
        self._dl_key = None
        self._refresh_downloads()
        recipe = getattr(self.parent(), "recipe", None)
        if recipe is not None:
            recipe.refresh_voice()                  # the Piper / Kokoro download button there goes away

    def _remove(self, key):
        item = next((i for i in self._download_items() if i[0] == key), None)
        if item is None or item[6] is None:
            return
        if QMessageBox.question(self, "FreeSub", f"Delete {item[1]} from this PC?\n"
                                "It is downloaded again when it is needed.") != QMessageBox.Yes:
            return
        try:
            item[6]()
        except OSError as e:
            QMessageBox.warning(self, "FreeSub", str(e))
        self._refresh_downloads()

    # ------------------------------------------------------------ About
    def _about(self):
        from ..config import APP_NAME, APP_URL, APP_VERSION
        from ..tts import kokoro
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        title = label(f"{APP_NAME} {APP_VERSION}", name="PanelTitle")
        lay.addWidget(title)
        lay.addWidget(label("Subtitles from speech or from the picture, translation, narration and video export. "
                            "Free and open source (MIT licence).", muted=True, wrap=True))
        lay.addWidget(row(button("GitHub page", lambda: QDesktopServices.openUrl(QUrl(APP_URL))), None))
        lay.addWidget(heading("This PC"))
        gpu = getattr(getattr(self.parent(), "recipe", None), "gpu_text", "") or "Checking…"
        lay.addWidget(label(gpu, wrap=True))
        lay.addWidget(label(f"ffmpeg: {ffmpeg.version() or 'not found'}", muted=True, wrap=True))
        lay.addWidget(heading("Speech models"))
        lay.addWidget(label("Models with \"turbo\" in their name are faster but less accurate. Chinese: "
                            "belle-large-v3-zh. Japanese: kotoba-whisper-v2.0. Other languages: large-v3. "
                            "Advanced → Separate voice and background → Before making subtitles makes films and "
                            "anime more accurate.", muted=True, wrap=True))
        lay.addWidget(heading("Narration voices"))
        lay.addWidget(label("Edge: Microsoft's online voices (needs the internet). Piper: offline voices from the "
                            "Piper project. Kokoro: offline Vietnamese voices.", muted=True, wrap=True))
        lay.addWidget(label(kokoro.NOTE, wrap=True))
        lay.addWidget(row(button("Kokoro page", lambda: QDesktopServices.openUrl(QUrl(kokoro.PAGE))), None))
        lay.addWidget(heading("Made with"))
        lay.addWidget(label("faster-whisper (speech), RapidOCR (reading the picture), an MDX-Net model (voice / music "
                            "separation), CAM++ from 3D-Speaker (telling speakers apart), vig2p / sea-g2p "
                            "(Vietnamese phonemes), ffmpeg, Qt for Python. Each is used under its own licence; "
                            "models are downloaded from their authors' pages when first needed.",
                            muted=True, wrap=True))
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _check_encoders(self):
        from ..steps import export as ex
        self.a_enc_state.setText("Checking…")
        self.repaint()
        ok = ex.detect_encoders(refresh=True)
        names = [n for e, _c, n in ex.ENCODERS if ok.get(e)]
        self.a_enc_state.setText("Works here: " + ", ".join(names) if names else "No encoder works: is ffmpeg there?")

    def _store_advanced(self):
        s = settings()
        model, beam = self.a_model.currentData(), self.a_beam.value()
        s.update(whisper_model=model, whisper_beam=beam,
                 whisper_device=self.a_device.currentData(), whisper_vad=self.a_vad.isChecked(),
                 whisper_filter=self.a_filt.isChecked(), ocr_fps=self.a_fps.value(), ocr_thorough=self.a_thorough.isChecked(),
                 parallel_videos=self.a_lanes.currentData(),
                 whisper_separate=self.a_sep_subs.isChecked(), dub_separate=self.a_sep_dub.isChecked(),
                 export_encoder=self.a_enc.currentData(), export_container=self.a_cont.currentData(),
                 translate_fallback=self.fallback.isChecked())

    def _store_keys(self):
        settings().update(gemini_key=self.g_key.text().strip(), gemini_model=self.g_model.currentText().strip(),
                          cloudflare_account=self.c_acc.text().strip(), cloudflare_token=self.c_tok.text().strip(),
                          cloudflare_model=self.c_model.currentText().strip())

    def _test(self, which):
        self._store_keys()
        lab = {"gemini": "g", "cloudflare": "c", "google": "go"}[which]
        getattr(self, f"{lab}_state").setText("Testing…")

        def work():
            if which == "google":
                from ..translate import google
                ok, msg = google.test(settings()["target_lang"])
            else:
                ok, msg = ai.test(which)
            self.sig.result.emit(lab, ("OK. " if ok else "Failed: ") + msg)
        threading.Thread(target=work, daemon=True).start()

    def _result(self, lab, text):
        getattr(self, f"{lab}_state").setText(text)

    # ------------------------------------------------------------ helpers
    def _browse_root(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the results", self.root.text())
        if d:
            self.root.setText(os.path.normpath(d))
            self.r_custom.setChecked(True)

    def _browse_ff(self):
        d = QFileDialog.getExistingDirectory(self, "Folder that has ffmpeg.exe", self.ff.text())
        if d:
            self.ff.setText(os.path.normpath(d))
            self._test_ff()

    def _test_ff(self):
        old = settings().data.get("ffmpeg_path", "")
        settings().data["ffmpeg_path"] = self.ff.text().strip()
        try:
            exe = ffmpeg.find("ffmpeg")
            if exe and ffmpeg.find("ffprobe"):
                self.ff_state.setText(f"Found: {ffmpeg.version() or exe}")
            else:
                self.ff_state.setText("Not found. Choose the folder with ffmpeg.exe and ffprobe.exe, or "
                                      "open FreeSub again to download it." if getattr(sys, "frozen", False) else
                                      "Not found. Run setup.bat, or choose the folder with ffmpeg.exe and ffprobe.exe.")
        finally:
            settings().data["ffmpeg_path"] = old

    def _open(self, path):
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _ok(self):
        root = self.root.text().strip()
        custom = self.r_custom.isChecked()
        if custom and not root:
            self.root.setFocus()
            return
        if custom:
            try:
                os.makedirs(root, exist_ok=True)
            except OSError as e:
                self.ff_state.setText(f"Cannot use that folder: {e}")
                return
        self._store_keys()
        self._store_advanced()
        old_lang = ui_language()
        settings().update(native_titlebar=bool(self.title_bar.currentData()), language=self.lang.currentData(),
                          confirm_close=self.ask_close.isChecked(),
                          confirm_skip=[] if self.ask_actions.isChecked() else ["*"])
        if ui_language() != old_lang:
            self.restart = QMessageBox.question(self, "FreeSub", "Restart FreeSub now to use the new language?") \
                == QMessageBox.Yes
        settings().update(theme=self.theme.currentData(), output_location="custom" if custom else "video",
                          output_root=root, ffmpeg_path=self.ff.text().strip())
        self.accept()
