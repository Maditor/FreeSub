"""The recipe column: what Export does, as cards (Subtitles, Translate, Voice). The video options are asked
when Export is pressed (export_dialog.py); the subtitle look is in the left column (style_panel.py).

Every option is saved the moment it changes, so next time a video is added, pressing Export runs the same
recipe. Only the Export / Subtitles-only buttons start work.
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..config import settings
from ..core import addons, gpu
from ..langs import LANGS
from ..tts import voices
from .panels.voice import DictionaryDialog, Speaker
from .widgets import Card, Collapsible, Segmented, button, form_row, label, row

TARGETS = ["vi", "en", "ja", "ko", "zh", "th", "id", "fr", "es", "de", "ru", "pt"]
SERVICE_TEXT = {"google": "Google Translate (free, no key)", "gemini": "Gemini (AI, API key)",
                "cloudflare": "Cloudflare Workers AI (API key)"}


def _combo(items, value):
    c = QComboBox()
    for k, t in items:
        c.addItem(t, k)
    c.setCurrentIndex(max(0, c.findData(value)))
    return c


def _spin(lo, hi, value, step=1, suffix="", dec=None):
    w = QDoubleSpinBox() if dec is not None else QSpinBox()
    if dec is not None:
        w.setDecimals(dec)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    return w


def _save(key):
    return lambda v: settings().update(**{key: v})


class Recipe(QWidget):
    export_requested = Signal(str)          # "export" | "subs"
    area_requested = Signal()               # draw the OCR area on the preview
    keys_requested = Signal()               # open Settings → Translation
    download_whisper = Signal(str)
    download_voice = Signal()
    changed = Signal()                      # an option the preview shows changed
    mix_changed = Signal()                  # a narration volume changed (the live preview follows)
    narration_preview = Signal()            # make the narration of this video and listen to it

    def __init__(self):
        super().__init__()
        s = settings()
        self.speaker = Speaker(self)

        self.subs_card = Card("cc", "Subtitles", self._subs_ui(), s["do_subtitles"])
        self.tr_card = Card("translate", "Translate", self._tr_ui(), s["do_translate"])
        self.voice_card = Card("mic", "Narration", self._voice_ui(), s["do_voice"], opened=s["do_voice"])
        for card, key in ((self.subs_card, "do_subtitles"), (self.tr_card, "do_translate"),
                          (self.voice_card, "do_voice")):
            card.toggled.connect(lambda v, k=key: (settings().update(**{k: v}), self.refresh()))

        cards = QWidget()
        cl = QVBoxLayout(cards)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(10)
        for c in (self.subs_card, self.tr_card, self.voice_card):
            cl.addWidget(c)
        cl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(cards)

        self.scope = _combo([("selected", "Selected video"), ("all", "All videos")], "selected")
        self.scope.setToolTip("Which videos Export works on")
        self.b_subs = button("Subtitles only", lambda: self.export_requested.emit("subs"))
        self.b_subs.setToolTip("Make the .srt files (and the translation), then open their folder")
        self.b_export = button("Export", lambda: self.export_requested.emit("export"), primary=True)
        self.b_export.setObjectName("Big")
        self.plan = label("", muted=True, wrap=True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(scroll, 1)
        # the plan gets the whole width (the scope combo lives in the window's command bar)
        from PySide6.QtWidgets import QSizePolicy
        self.plan.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.buttons_row = row(self.b_subs, self.b_export)
        lay.addWidget(self.plan)
        lay.addWidget(self.buttons_row)
        self.refresh()

    # ================================================================ Subtitles
    def _subs_ui(self):
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.source = Segmented([("speech", "From speech"), ("ocr", "From the picture (OCR)")], s["subs_source"])
        self.source.changed.connect(self._source)
        lay.addWidget(self.source)

        # --- speech
        self.speech_box = QWidget()
        sl = QVBoxLayout(self.speech_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self.lang = _combo([("auto", "Auto detect")] + [(k, f"{v} ({k})") for k, v in LANGS.items()],
                           s["whisper_language"])
        self.quality = QComboBox()
        self.gpu_text = ""
        self.hint = QLineEdit(s["whisper_prompt"])
        self.hint.setPlaceholderText("Names to spell right, e.g. Tanjiro, Nezuko")
        sl.addWidget(form_row("Language", self.lang, "Language spoken in the video"))
        sl.addWidget(form_row("Quality", self.quality))
        sl.addWidget(form_row("Names", self.hint, "Optional: names and words Whisper should spell right"))
        self.speakers = QCheckBox("New line when the speaker changes")
        self.speakers.setToolTip("Tells the voices apart: two people in one line become two lines, and each "
                                 "person keeps one narration voice. Downloads a 28 MB model on first use.")
        self.speakers.setChecked(bool(s["whisper_speakers"]))
        sl.addWidget(self.speakers)
        lay.addWidget(self.speech_box)

        # --- OCR
        self.ocr_box = QWidget()
        ol = QVBoxLayout(self.ocr_box)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        self.b_area = button("Select the subtitle area on the video", self.area_requested.emit)
        self.area_state = label("", muted=True, wrap=True)
        ol.addWidget(self.b_area)
        ol.addWidget(self.area_state)
        lay.addWidget(self.ocr_box)

        self.separate = QCheckBox("Separate the voice from music first")
        self.separate.setToolTip("Clearer recognition in films and anime with music")
        self.separate.setChecked(bool(s["whisper_separate"]))
        lay.addWidget(self.separate)

        self.sync_quality()
        self.lang.currentIndexChanged.connect(self._lang)
        self.quality.currentIndexChanged.connect(self._quality)
        self.hint.editingFinished.connect(lambda: settings().update(whisper_prompt=self.hint.text().strip()))
        self.speakers.toggled.connect(_save("whisper_speakers"))
        self.separate.toggled.connect(lambda v: (settings().update(whisper_separate=v), self.refresh()))
        self._source(s["subs_source"], save=False)
        return w

    def _source(self, v, save=True):
        if save:
            settings().update(subs_source=v)
        self.speech_box.setVisible(v == "speech")
        self.ocr_box.setVisible(v == "ocr")
        self.changed.emit()
        self.refresh()

    def set_area_state(self, region, video_size=None):
        if region:
            x, y, w, h = region
            self.area_state.setText(f"Area set: {w * 100:.0f}% x {h * 100:.0f}% of the picture, "
                                    f"{y * 100:.0f}% from the top. Draw again to change it.")
        else:
            self.area_state.setText("No area yet: press the button, then drag a box around the subtitles.")

    def preset(self, q):
        special = {"ja": "kotoba-whisper-v2.0", "vi": "phowhisper-large"}.get(self.lang.currentData())
        return {"fast": dict(model="large-v3-turbo", beam=1),
                "balanced": dict(model="large-v3-turbo", beam=5),
                "best": dict(model=special or "large-v3", beam=5)}.get(q)

    def sync_quality(self):
        """Fill the Quality list ('Custom' only when a model was picked in Settings → Advanced)."""
        q = settings()["whisper_quality"]
        self.quality.blockSignals(True)
        self.quality.clear()
        items = [("fast", "Fast"), ("balanced", "Balanced"), ("best", "Best")]
        if q == "custom":
            items.append(("custom", f"Custom · {settings()['whisper_model']}"))
        for k, txt in items:
            self.quality.addItem(txt, k)
        self.quality.setCurrentIndex(max(0, self.quality.findData(q)))
        self.quality.blockSignals(False)
        self._quality_text()

    def _quality(self):
        q = self.quality.currentData()
        p = self.preset(q)
        if p:
            settings().update(whisper_quality=q, whisper_model=p["model"], whisper_beam=p["beam"])
        self.sync_quality()
        self.refresh()

    def _quality_text(self):
        q = self.quality.currentData()
        lang = self.lang.currentData()
        self.quality.setToolTip({
            "fast": "Quick draft.", "balanced": "Good for most videos.",
            "best": "Slowest, most accurate: " + ("kotoba-whisper (Japanese)." if lang == "ja" else
                                                  "PhoWhisper (Vietnamese)." if lang == "vi" else "large-v3."),
            "custom": "Model and options from Settings → Advanced."}.get(q, ""))

    def _lang(self):
        settings().update(whisper_language=self.lang.currentData())
        if self.quality.currentData() == "best":
            self._quality()
        self._quality_text()

    def refresh_model(self):
        """Kept for the main window (called after a download); the model choice lives in Settings now."""
        self.sync_quality()

    def set_gpu_text(self, text):
        self.gpu_text = text                     # shown in Settings → About

    # ================================================================ Translate
    def _tr_ui(self):
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.service = _combo(list(SERVICE_TEXT.items()), s["translator"])
        self.target = _combo([(k, LANGS.get(k, k)) for k in TARGETS], s["target_lang"])
        self.key_state = label("", muted=True, wrap=True)
        self.b_keys = button("API keys", self.keys_requested.emit)
        self.context = QPlainTextEdit(s["translate_context"])
        self.context.setPlaceholderText("About the video (AI only): names, who is who, tone…")
        self.context.setFixedHeight(64)
        self.glossary = QPlainTextEdit(s["translate_glossary"])
        self.glossary.setPlaceholderText("One per line:  original = translation\ne.g.  炭治郎 = Tanjiro")
        self.glossary.setFixedHeight(64)
        lay.addWidget(form_row("Service", self.service))
        lay.addWidget(form_row("Into", self.target))
        # keys live in Settings → Translation (no key line / button here)
        more = QWidget()
        ml = QVBoxLayout(more)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        ml.addWidget(label("Film context", muted=True))
        ml.addWidget(self.context)
        ml.addWidget(label("Names and terms", muted=True))
        ml.addWidget(self.glossary)
        lay.addWidget(Collapsible("Context and names", more))
        self.service.currentIndexChanged.connect(lambda: (settings().update(translator=self.service.currentData()),
                                                          self.refresh()))
        self.target.currentIndexChanged.connect(lambda: (settings().update(target_lang=self.target.currentData()),
                                                         self.refresh()))
        self._ctx_timer = QTimer(self)
        self._ctx_timer.setSingleShot(True)
        self._ctx_timer.setInterval(600)
        self._ctx_timer.timeout.connect(lambda: settings().update(
            translate_context=self.context.toPlainText(), translate_glossary=self.glossary.toPlainText()))
        self.context.textChanged.connect(self._ctx_timer.start)
        self.glossary.textChanged.connect(self._ctx_timer.start)
        return w

    def refresh_keys(self):
        from ..translate import ai
        svc = self.service.currentData()
        if svc == "google":
            self.key_state.setText("No key needed.")
        elif ai.SERVICES[svc].ready():
            self.key_state.setText("Key saved.")
        else:
            self.key_state.setText("No key yet: Google is used until you add one.")

    # ================================================================ Voice
    def _voice_ui(self):
        s = settings()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.engine = _combo([("edge", "Microsoft Edge voices (online, free)"),
                              ("piper", "Piper (offline, on this PC)"),
                              ("kokoro", "Kokoro · Vietnamese (offline, third-party)")], s["tts_engine"])
        self.female = QComboBox()
        self.male = QComboBox()
        self.b_piper = button("Download the Piper voice (63 MB)", self.download_voice.emit)
        self.b_piper.setToolTip("Kokoro voices are made by a third party: see Settings → About")
        self.speed = _spin(50, 200, int(s["tts_speed"]), 5, " %")
        self.one = QCheckBox("One voice for all")
        self.one.setToolTip("Read every line with the same voice: the one in the row above. Untick it for a "
                            "women's and a men's voice (in testing: some lines may get the wrong voice)")
        self.split_note = label("Women's / men's voices are in testing: a line may get the wrong voice. Fix it "
                                "in the Voice column of the subtitle table.", muted=True, wrap=True)
        self.one.setChecked(s["voice_mode"] != "split")
        self.respell = QCheckBox("Read foreign names the Vietnamese way")
        self.respell.setChecked(bool(s["tts_respell"]))
        self.dubvol = _spin(0, 200, int(s["dub_volume"]), 5, " %")
        self.bg = _spin(0, 200, int(s["dub_bg_volume"]), 5, " %")
        self.voicevol = _spin(0, 200, int(s["dub_voice_volume"]), 5, " %")
        self.duck = QCheckBox("Lower the original voice while the narration speaks")
        self.duck.setChecked(bool(s["dub_duck"]))
        self.split = QCheckBox("Split the original voice from the music")
        self.split.setToolTip("Needed to set the original voice apart from the music and effects. "
                              "Without it, both follow the Background volume.")
        self.split.setChecked(bool(s["dub_separate"]))
        self.b_hear = button("Preview narration", self.narration_preview.emit)
        self.b_hear.setToolTip("Make the narration of the selected video (no export) and play it in the preview, "
                               "with these volumes. Change them while it plays.")
        self.test = QLineEdit(s.get("tts_test_text") or "Anh có thấy Mike đâu không? Nó đang xem Netflix.")
        self.b_listen = button("Listen")
        self.b_listen.setToolTip("Hear this sentence with the women's or the men's voice")
        self.listen_menu = QMenu(self.b_listen)
        self.listen_menu.aboutToShow.connect(self._fill_listen_menu)
        self.b_listen.setMenu(self.listen_menu)
        self.b_dict = button("Dictionary", lambda: DictionaryDialog(self.speaker, self).exec())
        self.listen_state = label("", muted=True, wrap=True)
        self.test.setCursorPosition(0)             # show the start of the sentence, not its end
        self.listen_state.hide()                   # only while there is something to say
        self.speaker.state.connect(lambda t: (self.listen_state.setText(t), self.listen_state.setVisible(bool(t))))
        lay.addWidget(form_row("Voice", self.engine))
        self.female_row = form_row("Women", self.female)
        self.male_row = form_row("Men", self.male)
        lay.addWidget(self.female_row)
        lay.addWidget(self.male_row)
        lay.addWidget(self.split_note)
        lay.addWidget(self.b_piper)
        from PySide6.QtWidgets import QSizePolicy
        self.speed.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        speed_row = row(self.speed, self.one, spacing=12)
        lay.addWidget(form_row("Speed", speed_row))
        lay.addWidget(self.respell)
        lay.addWidget(row(self.test, self.b_listen, self.b_dict))
        lay.addWidget(self.listen_state)
        lay.addWidget(label("Sound mix", muted=True))
        lay.addWidget(form_row("Narration", self.dubvol, "The narration voice"))
        lay.addWidget(form_row("Background", self.bg, "Music and effects of the video"))
        lay.addWidget(form_row("Original voice", self.voicevol, "The voices of the video"))
        lay.addWidget(self.duck)
        lay.addWidget(self.split)
        # hearing the narration: the mic button on the video (no button here)
        self.engine.currentIndexChanged.connect(lambda: (settings().update(tts_engine=self.engine.currentData()),
                                                         self.refresh_voice()))
        self.female.currentIndexChanged.connect(lambda: self._voice_pick("female", self.female))
        self.male.currentIndexChanged.connect(lambda: self._voice_pick("male", self.male))
        self.speed.valueChanged.connect(_save("tts_speed"))
        self.one.toggled.connect(lambda v: (settings().update(voice_mode="one" if v else "split"), self.refresh_voice()))
        self.respell.toggled.connect(_save("tts_respell"))
        for wdg, key in ((self.dubvol, "dub_volume"), (self.bg, "dub_bg_volume"), (self.voicevol, "dub_voice_volume")):
            wdg.valueChanged.connect(lambda v, k=key: (settings().update(**{k: v}), self.mix_changed.emit()))
        self.duck.toggled.connect(lambda v: (settings().update(dub_duck=v), self.mix_changed.emit()))
        self.split.toggled.connect(lambda v: (settings().update(dub_separate=v), self.refresh_mix_state(None)))
        self.test.editingFinished.connect(lambda: settings().update(tts_test_text=self.test.text()))
        self.refresh_voice()
        self.refresh_mix_state(False)
        return w

    def refresh_mix_state(self, has_split):
        """has_split: the selected video has its voice and music apart (None = unknown)."""
        if has_split is not None:
            self._has_split = has_split
        split = getattr(self, "_has_split", False)
        self.voicevol.setEnabled(split or self.split.isChecked())
        tip = ("The voices of the video" if split or self.split.isChecked() else
               "Needs 'Split the original voice from the music': without it the whole original sound follows "
               "Background")
        self.voicevol.setToolTip(tip)

    def _voice_pick(self, who, combo):
        eng = self.engine.currentData()
        if combo.currentData() and eng in ("edge", "kokoro") and not getattr(self, "_filling", False):
            settings().update(**{f"tts_{eng}_{who}": combo.currentData()})

    def _fill_voices(self, eng):
        from ..tts import kokoro
        s = settings()
        self._filling = True
        try:
            for combo, who, g in ((self.female, "female", "f"), (self.male, "male", "m")):
                combo.clear()
                if eng == "kokoro":
                    items = [(k, lab) for k, (lab, gg) in kokoro.VOICES.items() if gg == g] + \
                            [(k, lab) for k, (lab, gg) in kokoro.VOICES.items() if gg != g]
                else:
                    items = voices.EDGE["vi"]
                for k, t in items:
                    combo.addItem(t, k)
                combo.setCurrentIndex(max(0, combo.findData(s.get(f"tts_{eng}_{who}"))))
        finally:
            self._filling = False

    def refresh_voice(self):
        from ..tts import kokoro
        eng = self.engine.currentData()
        named = eng in ("edge", "kokoro")
        one = settings()["voice_mode"] != "split"
        self.female_row.setVisible(named)
        self.male_row.setVisible(named and not one)
        self.split_note.setVisible(named and not one)
        lab = self.female_row.findChild(QLabel)
        if lab is not None:                        # one voice: the women's row holds the voice for every line
            lab.setText("All lines" if one else "Women")
        self.one.setVisible(named)
        if named:
            self._fill_voices(eng)
        if eng == "piper":
            engine_missing = not voices.piper_installed() and addons.can_download()
            need = engine_missing or not voices.piper_ready("vi")
            self.b_piper.setText("Download Piper (100 MB)" if engine_missing else "Download the Piper voice (63 MB)")
        elif eng == "kokoro":
            need = not kokoro.ready()
            self.b_piper.setText(f"Download the Kokoro voices ({kokoro.SIZE})")
        else:
            need = False
        self.b_piper.setVisible(need)
        self.b_listen.setEnabled(not need)

    # ================================================================ summary
    def refresh(self):
        if not hasattr(self, "b_export"):          # still being built
            return
        s = settings()
        src = "OCR" if s["subs_source"] == "ocr" else f"Speech · {s['whisper_quality'].capitalize()}"
        self.subs_card.set_summary(src)
        self.tr_card.set_summary(f"{s['translator'].capitalize()} → {LANGS.get(s['target_lang'], s['target_lang'])}")
        self.voice_card.set_summary({"edge": "Edge", "piper": "Piper", "kokoro": "Kokoro"}.get(s["tts_engine"], ""))
        self.refresh_keys()
        steps = []
        if s["do_subtitles"]:
            steps.append("subtitles")
        if s["do_translate"]:
            steps.append("translate")
        if s["export_target"] == "srt":
            steps.append(".srt files")
        else:
            if s["do_voice"]:
                steps.append("narration")
            steps.append("export")
        text = "Export runs: " + " → ".join(steps)
        if s["export_target"] != "srt" and s["do_translate"] and s["export_which"] == "original":
            text += "\nNote: the video gets the original subtitles (Export → Show), not the translation."
        self.plan.setText(text)
        self.plan.setToolTip("Steps already finished for a video are not done again.")

    def _fill_listen_menu(self):
        """Women / Men, with the voice each one uses now."""
        m = self.listen_menu
        m.clear()
        one = settings()["tts_engine"] == "piper"         # Piper: one voice for everybody
        rows = (("f", "Women", self.female), ("m", "Men", self.male))
        if settings()["voice_mode"] != "split" and not one:
            rows = (("f", "All lines", self.female),)
        for g, title, combo in rows:
            name = combo.currentText().split(" (")[0] if not one else "Piper"
            m.addAction(f"{title} · {name}",
                        lambda g=g: self.speaker.say(self.test.text(), self.respell.isChecked(), g))

    def set_batch_mode(self, on: bool):
        """The run buttons are in the window's command bar now: the recipe only shows what will run."""
        self.buttons_row.setVisible(False)

    def set_busy(self, busy):
        for b in (self.b_export, self.b_subs, self.b_piper, self.b_hear):
            b.setEnabled(not busy)
        if not busy:
            self.refresh()


def gpu_summary() -> str:
    n = gpu.cuda_count()
    name = gpu.nvidia_name()
    if n:
        return f"GPU: {name or 'NVIDIA'} (CUDA)"
    if name:
        if addons.can_download():
            return f"Found {name}. Speech recognition uses the CPU until GPU support is downloaded (Settings → Downloads)."
        return f"Found {name}, but the CUDA libraries are missing: run setup.bat again. Using the CPU."
    return "No NVIDIA GPU found: Whisper runs on the CPU."
