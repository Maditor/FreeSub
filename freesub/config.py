"""Settings stored in Documents\\FreeSub Data\\settings.json (with the logs)."""
import contextlib
import json
import os
import sys
import threading
import time

APP_NAME = "FreeSub"


def _version() -> str:
    """The version is kept in one place: the VERSION file (build.bat asks for it before each build)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "1.0.0"
    except OSError:
        return "1.0.0"


APP_VERSION = _version()
APP_URL = "https://github.com/Maditor/FreeSub"


def app_dir() -> str:
    """Folder of FreeSub.exe (frozen build) or of main.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(rel: str) -> str:
    """Bundled read-only files (assets) in both source and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None) or app_dir()
    return os.path.join(base, rel)


def documents_dir() -> str:
    """The user's Documents folder (also when Windows moved it, e.g. to OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                            ("d", ctypes.c_ubyte * 8)]
            docs = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(docs), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path and os.path.isdir(path):
                    return path
        except Exception:  # noqa: BLE001
            pass
    path = os.path.join(os.path.expanduser("~"), "Documents")
    return path if os.path.isdir(path) else os.path.expanduser("~")


def user_dir() -> str:
    """Documents\\FreeSub Data: settings.json, logs\\ and lang\\ (things a person may want to open or keep).
    A FREESUB_DATA environment variable can point somewhere else."""
    path = os.environ.get("FREESUB_DATA") or os.path.join(documents_dir(), "FreeSub Data")
    os.makedirs(path, exist_ok=True)
    return path


def logs_dir() -> str:
    path = os.path.join(user_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def data_dir() -> str:
    """%LOCALAPPDATA%\\FreeSub (models and caches live here)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def models_dir() -> str:
    path = os.path.join(data_dir(), "models")
    os.makedirs(path, exist_ok=True)
    return path


DEFAULTS = {
    # general
    "theme": "system",              # system | light | dark
    "language": "auto",             # auto (the Windows language) | en | vi
    "output_location": "video",     # video = .srt / exported video next to each video, custom = output_root
    "output_root": "",
    "ffmpeg_path": "",              # empty = ffmpeg\bin next to the app, then PATH
    "window_geometry": "",
    "native_titlebar": False,
    "confirm_skip": [],             # actions whose question was answered "Don't ask again" ("*" = all)
    "confirm_close": True,          # ask before the window closes (a click on X by mistake)       # True = the usual Windows title bar instead of FreeSub's own
    "window_state": "",
    "last_open_dir": "",
    "log_open": False,
    # speech -> subtitles (faster-whisper)
    "whisper_quality": "balanced",  # (old: Steps now picks the model itself, whisper_model)
    "whisper_separate": False,      # separate the voice from music / effects before Whisper
    "whisper_model": "large-v3-turbo",
    "whisper_language": "auto",
    "whisper_device": "auto",       # auto | cuda | cpu
    "whisper_compute": "auto",      # auto | int8_float16 | float16 | int8 | float32
    "whisper_beam": 5,
    "whisper_vad": True,
    "whisper_vad_threshold": 0.5,
    "whisper_min_silence_ms": 500,
    "whisper_filter": True,         # drop hallucinated lines (credits, "thanks for watching"...)
    "whisper_speakers": True,       # cut a line where the speaker changes (speaker voice prints)
    "whisper_prompt": "",           # (older FreeSub: moved into a name list)
    "names_list": "",               # the chosen name list (translate/names.py), "" = none
    "sub_max_chars": 42,            # (old) characters per line: now sub_max_words
    "sub_max_words": 8,             # words per line (Chinese / Japanese: twice as many characters)
    "sub_max_lines": 2,
    "sub_max_duration": 6.0,        # seconds
    "sub_min_duration": 0.7,
    "sub_gap": 0.08,                # minimum gap kept between two cues
    # recipe: what Export runs
    "do_subtitles": True,
    "subs_source": "speech",        # speech | ocr
    "do_translate": True,
    "do_voice": False,
    "do_export": True,
    # OCR (hardsub)
    "ocr_fps": 3.0,                 # frames read per second
    "ocr_region": None,             # [x, y, w, h] fractions of the frame: the subtitle area / box (last drawn)
    "ocr_min_score": 0.6,
    "ocr_thorough": False,          # read every changed frame (the old, slow way) instead of each line a few times
    "sub_box": True,                # show the subtitles inside the drawn box (blurred, darker picture under it)
    "sub_box_always": False,        # keep the box on screen the whole video (not only while a line shows)
    "video_speed": 1.0,             # playback speed 0.25 - 2: the preview plays at it and the export is made at it
    "ocr_box_opacity": 20,          # % how strongly the box colour covers the picture
    "sub_box_color": "#000000",
    "box_region": None,             # the box moved away from the OCR area (dragged on the video); None = same
    "ocr_box_blur": 10,             # px blur of the picture under the box
    # translation
    "translator": "google",         # google | gemini | cloudflare
    "translate_fallback": True,     # try the other services when one fails
    "target_lang": "vi",
    "translate_style": "general",   # translate/styles.py: the guide the AI follows (setting, address, terms)
    "translate_context": "",        # (old) about the film: moved into a translation style on first start
    "translate_glossary": "",       # "source = target" lines
    "translate_batch": 30,          # lines per AI request
    "gemini_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    "cloudflare_account": "",
    "cloudflare_token": "",
    "cloudflare_model": "@cf/google/gemma-4-26b-a4b-it",
    # narration (voice-over read by Edge / Piper)
    "tts_engine": "edge",           # edge | piper
    "tts_edge_female": "vi-VN-HoaiMyNeural",
    "tts_edge_male": "vi-VN-NamMinhNeural",
    "tts_kokoro_female": "ngoc_huyen",  # Kokoro (third-party voices, downloaded on request)
    "tts_kokoro_male": "tuan_ngoc",
    "tts_speed": 100,
    "tts_respell": True,
    "kokoro_threads": 0,            # CPU threads for Kokoro voices (0 = automatic: one per core)
    "dub_max_speedup": 1.5,         # (old, not used any more)
    "dub_max_speed": 1.25,          # (old: a factor, replaced by dub_max_pct)
    "dub_max_pct": 125,             # narration: never read faster than this, in % of normal speed (like tts_speed);
                                    # timing.py moves lines first, only then reads faster
    "dub_join": True,               # (old: now follows dub_auto)
    "dub_auto": True,               # narration: speed between tts_speed and dub_max_pct, short lines joined and the
                                    # subtitles follow the reading; off: every line at tts_speed, nothing joined
    "parallel_videos": "auto",      # batch: videos worked on at the same time: auto | 1 | 2
    "auto_fit_lines": True,         # new subtitles: lines broken once to fit the box / video (like the button)
    "voice_mode": "one",            # narration: one voice for every line | split (women / men, beta)
    "dub_sync_subs": True,          # narration on: the subtitles follow the times the lines are read at
    "dub_volume": 100,              # %
    "dub_bg_volume": 100,           # % music and effects (or the whole original sound without separation)
    "dub_voice_volume": 100,        # % the original voice (needs voice separation)
    "dub_duck": True,               # the original voice goes down while the narration speaks
    "dub_separate": True,           # split voice / music so the three volumes can be set apart
    # export
    "export_subs": "hard",          # always burned in (an own track / no subtitles were dropped)
    "export_scope": "each",         # each video on its own | the timeline as one video
    "export_target": "video",
    "queue_view": "list",           # list | grid (the video list on the left)       # video | srt (Export → Subtitles only: the .srt files, no video)
    "export_which": "translation",  # translation | original | both
    "export_resolution": "source",  # source | 2160 | 1440 | 1080 | 720 | 480
    "export_quality": "high",       # best | high | medium | small
    "export_encoder": "auto",       # auto | nvenc | qsv | amf | x264
    "export_container": "mp4",      # mp4 | mkv
    # subtitle style (the preview shows it exactly as it is burned in)
    "sub_font": "Arial",
    "sub_size": 5.0,                # % of the video height
    "sub_bold": True,
    "sub_color": "#ffffff",
    "sub_outline_size": "normal",   # none | thin | normal | thick
    "sub_outline": "#000000",
    "sub_margin": 6.0,              # % from the bottom (when there is no box)
    "sub_pos_x": 0.5,               # centre of the lines, fraction of the width (dragged on the video)
    "open_folder_after": True,
    # preview
    "preview_volume": 80,
    "preview_show_subs": True,
    "preview_translation": True,    # the video shows the translation (False: the original, to compare)
}


class Settings:
    def __init__(self, path: str | None = None):
        if path is None:
            path = os.path.join(user_dir(), "settings.json")
            old = os.path.join(app_dir(), "settings.json")
            if not os.path.isfile(path) and os.path.isfile(old):       # older versions kept it next to the app
                try:
                    import shutil
                    shutil.copyfile(old, path)
                except OSError:
                    path = old
        self.path = path
        self.data = dict(DEFAULTS)
        self._lock = threading.Lock()
        self._tl = threading.local()          # per-thread overlay: one video's own settings while it runs
        self.ui_style = {}                    # the window's video's own style (read on the window's thread)
        self.changed_at = 0.0                 # time.monotonic() of the last change that changes what is made
        self.on_change = None                 # the window: told (keys) after a change made on its thread
        self.style_sink = None                # the window: where a style change of that video goes
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.data.update({k: v for k, v in saved.items()})
                if "dub_max_pct" not in saved and "dub_max_speed" in saved:    # the old factor -> a % (nearest 5)
                    try:
                        pct = float(self.data["tts_speed"]) * float(saved["dub_max_speed"])
                        self.data["dub_max_pct"] = int(max(50, min(200, round(pct / 5) * 5)))
                    except (TypeError, ValueError):
                        pass
        except (OSError, ValueError):
            pass

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass

    def _over(self, key):
        over = getattr(self._tl, "over", None)
        if over and key in over:
            return True, over[key]
        if self.ui_style and key in self.ui_style and threading.current_thread() is threading.main_thread():
            return True, self.ui_style[key]
        return False, None

    def __getitem__(self, key):
        hit, v = self._over(key)
        return v if hit else self.data.get(key, DEFAULTS.get(key))

    def get(self, key, default=None):
        hit, v = self._over(key)
        return v if hit else self.data.get(key, DEFAULTS.get(key, default))

    @contextlib.contextmanager
    def overlay(self, values: dict | None):
        """Inside the block (on this thread only) these values win over the shared settings."""
        prev = getattr(self._tl, "over", None)
        self._tl.over = dict(prev or {}, **(values or {}))
        try:
            yield
        finally:
            self._tl.over = prev

    def snapshot(self) -> dict:
        """All the shared settings as they are now (a copy): a video is made with these from start to end."""
        import copy
        with self._lock:
            return copy.deepcopy(dict(DEFAULTS, **self.data))

    def current_overlay(self):
        """This thread's overlay (for a helper thread that works for the same video)."""
        return getattr(self._tl, "over", None)

    def adopt(self, over):
        """A helper thread (e.g. the narration's voice makers) uses the overlay of the thread it works for."""
        self._tl.over = dict(over) if over else None

    def update(self, **kw):
        main = threading.current_thread() is threading.main_thread()
        if main:
            changed = [k for k, v in kw.items() if self.get(k) != v or self.data.get(k) != v]
        over = getattr(self._tl, "over", None)
        if over:                                   # a running video changes a value: it sees it too
            for k in kw:
                if k in over:
                    over[k] = kw[k]
        style = {k: v for k, v in kw.items() if k in STYLE_KEYS}
        if style and self.style_sink is not None and threading.current_thread() is threading.main_thread():
            if self.style_sink(style):            # kept by the video (not the shared settings)
                kw = {k: v for k, v in kw.items() if k not in STYLE_KEYS}
        if kw:
            with self._lock:
                self.data.update(kw)
            self.save()
        if main and changed:
            if set(changed) - UI_KEYS:
                self.changed_at = time.monotonic()
            if self.on_change is not None:
                try:
                    self.on_change(changed)
                except Exception:  # noqa: BLE001
                    pass


# How the subtitles look: each video keeps its own (project.json "style"); the shared values are what a video
# that was never styled shows (and what Sync writes to every open video).
STYLE_KEYS = ("sub_font", "sub_size", "sub_bold", "sub_color", "sub_outline_size", "sub_outline", "sub_margin",
              "sub_pos_x", "sub_max_words", "sub_max_lines", "sub_max_duration", "sub_box", "sub_box_always",
              "sub_box_color", "ocr_box_opacity", "ocr_box_blur", "video_speed")

# Settings that do not change what is made (the window, questions, keys, preview): changing them while videos
# are being made does not make those videos out of date.
UI_KEYS = frozenset({"confirm_close", "confirm_skip", "language", "last_open_dir", "log_open", "native_titlebar",
                     "open_folder_after", "parallel_videos", "preview_show_subs", "preview_translation", "preview_volume", "queue_view",
                     "theme", "window_geometry", "window_state", "tts_test_text", "gemini_key", "cloudflare_token",
                     "cloudflare_account", "video_speed", "ffmpeg_path"})

SETTINGS = None


def settings() -> Settings:
    global SETTINGS
    if SETTINGS is None:
        SETTINGS = Settings()
    return SETTINGS


def ui_language() -> str:
    """The interface language: the setting, or for "auto" Vietnamese on a Vietnamese Windows, else English."""
    lang = settings()["language"]
    if lang in ("en", "vi"):
        return lang
    try:
        from PySide6.QtCore import QLocale
        if QLocale.system().language() == QLocale.Vietnamese:
            return "vi"
        for name in QLocale.system().uiLanguages():
            if name.lower().startswith("vi"):
                return "vi"
    except Exception:  # noqa: BLE001
        pass
    return "en"
