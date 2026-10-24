"""The recipe column: what Export does, as cards (Subtitles, Translate, Voice). The video options are asked
when Export is pressed (export_dialog.py); the subtitle look is in the left column (style_panel.py).

Every option is saved the moment it changes, so next time a video is added, pressing Export runs the same
recipe. Only the Export / Subtitles-only buttons start work.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLabel, QLineEdit, QMenu,
                               QSizePolicy,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..core import models
from ..config import settings
from ..core import addons, gpu
from ..langs import LANGS
from ..tts import voices
from .panels.voice import DictionaryDialog, Speaker
from ..i18n import Keep, tr
from .widgets import Card, IconButton, MenuPicker, Segmented, button, form_row, heading, label, row, tick_row, wait_cursor

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
    sync_changed = Signal()                 # "Subtitles follow the narration" was switched here

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
        self.plan.setVisible(False)                 # (what will run: shown by the steps themselves)
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
        sl.addWidget(form_row("Language", self.lang, "Language spoken in the video"))
        sl.addWidget(form_row("Model", self.quality, "Speech recognition model (details in Settings → Advanced)"))
        self.speakers = QCheckBox("New line when the speaker changes")
        self.speakers.setToolTip("Tells the voices apart: two people in one line become two lines, and each "
                                 "person keeps one narration voice. Downloads a 28 MB model on first use.")
        self.speakers.setChecked(bool(s["whisper_speakers"]))
        sl.addWidget(tick_row(self.speakers))
        lay.addWidget(self.speech_box)

        # --- OCR
        self.ocr_box = QWidget()
        ol = QVBoxLayout(self.ocr_box)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        self.b_area = button("Select the subtitle area on the video", self.area_requested.emit)
        self.area_state = label("", muted=True, wrap=True)
        ol.addWidget(self.b_area)
        self.area_state.setVisible(False)
        lay.addWidget(self.ocr_box)

        # the name list: under Quality (speech) or under the area button (OCR): Whisper spells them right, the
        # translation keeps them
        self.b_names = MenuPicker()
        self.b_names.setToolTip("Names and words of this series: Whisper spells them right, and the translation "
                                "uses yours (original = translation)")
        self.names_menu = QMenu(self.b_names)
        self.names_menu.aboutToShow.connect(self._fill_names_menu)
        self.b_names.setMenu(self.names_menu)
        self.b_names.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.b_names_edit = IconButton("edit", "Edit the name lists", size=18)
        self.b_names_edit.clicked.connect(self._edit_names)
        self.names_row = form_row("Names", row(self.b_names, self.b_names_edit, spacing=4))
        self._speech_lay, self._ocr_lay = sl, ol
        self._show_names()


        self.sync_quality()
        self.lang.currentIndexChanged.connect(self._lang)
        self.quality.currentIndexChanged.connect(self._quality)
        self.speakers.toggled.connect(_save("whisper_speakers"))
        self._source(s["subs_source"], save=False)
        return w

    def _source(self, v, save=True):
        if save:
            settings().update(subs_source=v)
        self.speech_box.setVisible(v == "speech")
        self.ocr_box.setVisible(v == "ocr")
        if v == "speech":                               # Names: right under Quality
            self._speech_lay.insertWidget(2, self.names_row)
        else:
            self._ocr_lay.addWidget(self.names_row)
        self.changed.emit()
        self.refresh()

    def set_area_state(self, region, video_size=None):
        if region:
            x, y, w, h = region
            self.area_state.setText(f"Area set: {w * 100:.0f}% x {h * 100:.0f}% of the picture, "
                                    f"{y * 100:.0f}% from the top. Draw again to change it.")
        else:
            self.area_state.setText("No area yet: press the button, then drag a box around the subtitles.")

    def sync_quality(self):
        """Fill the Model list with the models for the chosen language (names only: what each one is good at is
        in Settings → Advanced). A model that does not fit the language any more becomes the default."""
        lang = self.lang.currentData()
        s = settings()
        name = models.fit(s["whisper_model"], lang)
        if name != s["whisper_model"]:
            s.update(whisper_model=name)
        self.quality.blockSignals(True)
        self.quality.clear()
        for n in models.for_language(lang):
            self.quality.addItem(n, n)
        self.quality.setCurrentIndex(max(0, self.quality.findData(name)))
        self.quality.blockSignals(False)

    def _quality(self):
        name = self.quality.currentData()
        if name:
            settings().update(whisper_model=name)
        self.sync_quality()
        self.refresh()

    def _lang(self):
        settings().update(whisper_language=self.lang.currentData())
        self.sync_quality()

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
        self._move_old_context()
        self.b_style = MenuPicker()
        self.b_style.setToolTip("How the AI translates: setting, how people address each other, fixed terms "
                                "(Gemini / Cloudflare follow all of it; Google uses only the fixed terms)")
        self.style_menu = QMenu(self.b_style)
        self.style_menu.aboutToShow.connect(self._fill_style_menu)
        self.b_style.setMenu(self.style_menu)
        self.b_style_edit = IconButton("edit", "Edit the translation styles", size=18)
        self.b_style_edit.clicked.connect(self._edit_styles)
        self._show_style()
        lay.addWidget(form_row("Service", self.service))
        lay.addWidget(form_row("Into", self.target))
        # keys live in Settings → Translation (no key line / button here); names: Steps → Subtitles
        self.b_style.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(form_row("Translation style", row(self.b_style, self.b_style_edit, spacing=4)))
        self.service.currentIndexChanged.connect(lambda: (settings().update(translator=self.service.currentData()),
                                                          self.refresh()))
        self.target.currentIndexChanged.connect(lambda: (settings().update(target_lang=self.target.currentData()),
                                                         self.refresh()))
        return w

    # ---------------------------------------------------------------- name lists
    def _show_names(self):
        from ..translate import names
        lst = names.get(settings()["names_list"])
        if lst is None and settings()["names_list"]:
            settings().update(names_list="")             # the list was deleted
        self.b_names.setText(Keep(lst["name"]) if lst else tr("No list"))

    def _fill_names_menu(self):
        from ..translate import names
        m = self.names_menu
        m.clear()
        cur = settings()["names_list"]
        a = m.addAction("No list")
        a.setCheckable(True)
        a.setChecked(not cur)
        a.triggered.connect(lambda: self._pick_names(""))
        lists = names.all_lists()
        if lists:
            m.addSeparator()
        for x in lists:
            a = m.addAction(Keep(f"{x['name']}  ({len(x['pairs'])})"))
            a.setCheckable(True)
            a.setChecked(x["id"] == cur)
            a.triggered.connect(lambda _c=False, lid=x["id"]: self._pick_names(lid))
        m.addSeparator()
        m.addAction("New list…", self._edit_names)

    def _pick_names(self, lid):
        settings().update(names_list=lid)
        self._show_names()
        self.changed.emit()

    subtitle_source = None              # the window: () -> (original lines, translated lines, language, video name)

    def _edit_names(self):
        from .names_dialog import NamesDialog
        with wait_cursor():
            dlg = NamesDialog(self, settings()["names_list"], self.subtitle_source)
            if not dlg.work:
                dlg._new()                                  # no list yet: start one
        if dlg.exec():
            settings().update(names_list=dlg.picked)
        self._show_names()
        self.changed.emit()

    # ---------------------------------------------------------------- translation styles
    def _move_old_context(self):
        """Older FreeSub had a free 'film context' box: its text becomes a style of the user's own."""
        old = settings()["translate_context"].strip()
        if old:
            from ..translate import styles
            sid = styles.add(tr("My notes"), old)
            settings().update(translate_context="", translate_style=sid)

    def _show_style(self):
        from ..translate import styles
        st = styles.get(settings()["translate_style"])
        if st["id"] != settings()["translate_style"]:
            settings().update(translate_style=st["id"])
        name = st["name"] if st["renamed"] else tr(st["name"])
        self.b_style.setText(Keep(name))

    def _fill_style_menu(self):
        from ..translate import styles
        m = self.style_menu
        m.clear()
        cur = settings()["translate_style"]
        for st in styles.all_styles():
            name = st["name"] if st["renamed"] else tr(st["name"])
            a = m.addAction(Keep(name))
            a.setCheckable(True)
            a.setChecked(st["id"] == cur)
            a.triggered.connect(lambda _c=False, sid=st["id"]: self._pick_style(sid))

    def _pick_style(self, sid):
        settings().update(translate_style=sid)
        self._show_style()
        self.changed.emit()

    def _edit_styles(self):
        from .styles_dialog import StylesDialog
        with wait_cursor():
            dlg = StylesDialog(self, settings()["translate_style"])
        dlg.exec()
        self._show_style()
        self.changed.emit()

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
        self.maxspeed = _spin(self.speed.value(), 200, int(s["dub_max_pct"]), 5, " %")   # never under the minimum
        self.auto = QCheckBox("Adjust the reading speed and subtitles automatically")
        self.auto.setToolTip("Read faster or slower between the minimum and maximum speed, join short lines, "
                             "and move the subtitles with the voice")
        self.auto.setChecked(bool(s.get("dub_auto", True)))
        self.one = _combo([("one", "One voice for all"),
                           ("split", "Women's and men's voices (testing)")], s["voice_mode"])
        self.one.setToolTip("Women's and men's voices: a line may get the wrong voice. Fix it in the Voice "
                            "column of the subtitle table")
        self.split_note = label("Women's / men's voices are in testing: a line may get the wrong voice. Fix it "
                                "in the Voice column of the subtitle table.", muted=True, wrap=True)
        self.respell = QCheckBox("Read with the dictionary")
        self.respell.setToolTip("Words in the Dictionary (and foreign names) are read the way written there")
        self.respell.setChecked(bool(s["tts_respell"]))
        self.dubvol = _spin(0, 200, int(s["dub_volume"]), 5, " %")
        self.bg = _spin(0, 200, int(s["dub_bg_volume"]), 5, " %")
        self.voicevol = _spin(0, 200, int(s["dub_voice_volume"]), 5, " %")
        self.duck = QCheckBox("Lower the original voice while the narration speaks")
        self.duck.setChecked(bool(s["dub_duck"]))
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
        def group(text, first=False):            # one group per job: accent heading + line (widgets.heading)
            h = heading(text)
            if first:
                h.layout().setContentsMargins(0, 0, 0, 0)
            lay.addWidget(h)
        group("Voice", first=True)
        lay.addWidget(form_row("Engine", self.engine))
        self.one_row = form_row("Readers", self.one)
        lay.addWidget(self.one_row)
        self.female_row = form_row("Women", self.female)
        self.male_row = form_row("Men", self.male)
        lay.addWidget(self.female_row)
        lay.addWidget(self.male_row)
        self.split_note.setVisible(False)
        lay.addWidget(self.b_piper)
        from PySide6.QtWidgets import QSizePolicy
        group("Timing")
        self.speed.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(tick_row(self.auto))
        self.speed_row = form_row("Minimum speed", self.speed)
        self.max_row = form_row("Maximum speed", self.maxspeed, "Never faster than this (100 % = normal speed)")
        lay.addWidget(self.speed_row)
        lay.addWidget(self.max_row)
        self._auto_rows()
        group("Pronunciation")
        lay.addWidget(tick_row(self.respell))
        lay.addWidget(row(self.test, self.b_listen, self.b_dict))
        lay.addWidget(self.listen_state)
        group("Sound mix")
        lay.addWidget(tick_row(self.duck))
        lay.addWidget(form_row("Narration", self.dubvol, "The narration voice"))
        self.bg_row = form_row("Background", self.bg)
        self.voice_row = form_row("Original voice", self.voicevol, "The voices of the video")
        lay.addWidget(self.bg_row)
        lay.addWidget(self.voice_row)
        # hearing the narration: the mic button on the video (no button here)
        self.engine.currentIndexChanged.connect(lambda: (settings().update(tts_engine=self.engine.currentData()),
                                                         self.refresh_voice()))
        self.female.currentIndexChanged.connect(lambda: self._voice_pick("female", self.female))
        self.male.currentIndexChanged.connect(lambda: self._voice_pick("male", self.male))
        self.speed.valueChanged.connect(_save("tts_speed"))
        self.maxspeed.valueChanged.connect(_save("dub_max_pct"))
        self.speed.valueChanged.connect(self.maxspeed.setMinimum)       # the maximum follows the minimum up
        self.auto.toggled.connect(self._auto_toggled)
        self.one.currentIndexChanged.connect(lambda: (settings().update(voice_mode=self.one.currentData()),
                                                      self.refresh_voice()))
        self.respell.toggled.connect(_save("tts_respell"))
        for wdg, key in ((self.dubvol, "dub_volume"), (self.bg, "dub_bg_volume"), (self.voicevol, "dub_voice_volume")):
            wdg.valueChanged.connect(lambda v, k=key: (settings().update(**{k: v}), self.mix_changed.emit()))
        self.duck.toggled.connect(lambda v: (settings().update(dub_duck=v), self.mix_changed.emit()))
        self.test.editingFinished.connect(lambda: settings().update(tts_test_text=self.test.text()))
        self.refresh_voice()
        self.refresh_mix_state(False)
        return w

    def _auto_rows(self):
        """Automatic: a minimum and a maximum speed. Off: one reading speed, set by hand."""
        on = self.auto.isChecked()
        from PySide6.QtWidgets import QLabel
        lb = self.speed_row.findChild(QLabel)
        lb.setText("Minimum speed" if on else "Reading speed")
        tip = "How fast every line is read to begin with (100 % = normal speed)" if on else \
            "Every line is read at this speed (100 % = normal speed)"
        lb.setToolTip(tip)
        self.speed.setToolTip(tip)
        self.max_row.setVisible(on)

    def _auto_toggled(self, on):
        """One switch for three things: speed between min and max, short lines joined, subtitles follow the
        voice (the headset button of the subtitle table follows too; it can still be switched on its own)."""
        settings().update(dub_auto=on, dub_join=on, dub_sync_subs=on)
        self._auto_rows()
        self.sync_changed.emit()

    def refresh_dub_options(self):
        """The headset button of the subtitle table was switched: it only changes which times are shown, the
        automatic switch stays as it is."""

    def refresh_mix_state(self, _has_split=None):
        """Settings → Advanced → Separate voice and background → For the narration: on = three volumes
        (narration, background, original voice); off = two (narration, the whole original sound)."""
        from PySide6.QtWidgets import QLabel
        apart = bool(settings()["dub_separate"])
        self.voice_row.setVisible(apart)
        lb = self.bg_row.findChild(QLabel)
        lb.setText("Background" if apart else "Original sound")
        tip = "Music and effects of the video" if apart else "The whole sound of the video"
        lb.setToolTip(tip)
        self.bg.setToolTip(tip)

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

        lab = self.female_row.findChild(QLabel)
        if lab is not None:                        # one voice: the women's row holds the voice for every line
            lab.setText("All lines" if one else "Women")
        self.one_row.setVisible(named)
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
        self.refresh_dub_options()

    # ================================================================ summary
    def refresh(self):
        if not hasattr(self, "b_export"):          # still being built
            return
        s = settings()
        self.refresh_mix_state()                     # Settings → Advanced may have changed the separation
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
        for b in (self.b_piper, self.b_hear):          # Export / Subtitles stay: they add to the run
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
