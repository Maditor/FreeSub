"""Interface language.

The English text stays in the code. For another language the words come from freesub/lang/<lang>.json,
shipped with FreeSub: a key is the English text, the value its translation. "{a}", "{b}"… stand for the
parts that change (a number, a file name): "{a} videos": "{a} video". Text with no translation stays English.

install() is called once, before the window is made: it wraps the Qt calls that put text on screen
(labels, buttons, menus, tooltips, message boxes…), so the rest of the code needs no change.
Text the user typed (subtitles in the table, names) is never touched.
"""
import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

LANGS = (("en", "English"), ("vi", "Tiếng Việt"))        # each in its own language (see Keep)
_words: dict[str, str] = {}
_patterns: list[tuple[re.Pattern, list[str], str]] = []
_cache: dict[str, str] = {}
_lock = threading.Lock()
_lang = "en"
SPLITS = ("\n", " · ", " → ")
STATICS = {"question", "information", "warning", "critical", "getOpenFileName", "getOpenFileNames",
           "getSaveFileName", "getExistingDirectory", "getText", "getItem", "getInt", "getDouble"}
_CODE = re.compile(r"^(.+) \(([a-z]{2,3}(?:-[A-Za-z]{2,4})?)\)$")    # "English (en)"
_FIELD = re.compile(r"\{([a-z])\}")


class Keep(str):
    """Text shown as it is in every language (e.g. "English" in the language list)."""


def language() -> str:
    return _lang


def bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "lang"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v.strip()
                and not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        log.warning("Strings file %s not used: %s", path, e)
        return {}


def load(lang: str):
    """Read the strings for lang (the file shipped with FreeSub)."""
    global _lang
    _words.clear(); _patterns.clear(); _cache.clear()
    _lang = lang
    if lang == "en":
        return
    words = _read(bundled_dir() / f"{lang}.json")
    for k, v in words.items():
        if _FIELD.search(k):
            names = _FIELD.findall(k)
            if len(re.sub(r"\{[a-z]\}|[^A-Za-z]", "", k)) < 2:
                continue                                  # nothing but blanks: would match anything
            bits = _FIELD.split(k)                        # literal, name, literal, name, …
            rx = "^" + "".join(re.escape(b) if i % 2 == 0 else "(.+?)" for i, b in enumerate(bits)) + "$"
            try:
                _patterns.append((re.compile(rx, re.S), names, v))
            except re.error:
                continue
        else:
            _words[k] = v
    # the more fixed words a pattern has, the sooner it is tried ("{a} lines read" before "{a} lines")
    _patterns.sort(key=lambda p: -len(_FIELD.sub("", p[0].pattern)))
    log.info("Interface language: %s (%d strings)", lang, len(words))



def tr(s, _depth=0):
    """The text in the interface language (unchanged if there is no translation)."""
    if _lang == "en" or not isinstance(s, str) or isinstance(s, Keep) or not s.strip():
        return s
    hit = _words.get(s)
    if hit is not None:
        return hit
    with _lock:
        hit = _cache.get(s)
    if hit is not None:
        return hit
    out = _translate(s, _depth)
    if out is None:
        out = s
    if len(_cache) < 20000:
        with _lock:
            _cache[s] = out
    return out


def _fill(m, names, value, depth):
    parts = {n: (tr(g, depth + 1) if depth < 4 else g) for n, g in zip(names, m.groups())}
    return _FIELD.sub(lambda f: parts.get(f.group(1), f.group(0)), value)


def _translate(s: str, depth: int):
    core = s.strip()
    if core != s:                                         # keep the spaces around it
        t = tr(core, depth + 1) if depth < 4 else core
        if t == core:
            return None
        i = s.index(core)
        return s[:i] + t + s[i + len(core):]
    later = None
    for rx, names, value in _patterns:
        m = rx.match(s)
        if m:
            if any(" · " in g for g in m.groups()):   # "2 ticked · 1 exported": the pieces first
                later = later or (m, names, value)
                continue
            return _fill(m, names, value, depth)
    if later is not None and " · " in s and depth < 4:
        pieces = s.split(" · ")
        done = [tr(p, depth + 1) for p in pieces]
        if done != pieces:
            return " · ".join(done)
    if later is not None:
        return _fill(*later, depth)
    m = _CODE.match(s)
    if m and depth < 4:
        t = tr(m.group(1), depth + 1)
        return f"{t} ({m.group(2)})" if t != m.group(1) else None
    if depth < 4:
        for sep in SPLITS:
            if sep in s:
                pieces = s.split(sep)
                done = [tr(p, depth + 1) for p in pieces]
                if done != pieces:
                    return sep.join(done)
                return None
    return None


# ---------------------------------------------------------------- the Qt calls that put text on screen

def _wrap(cls, name, which=(0,), lists=False, first=False):
    """cls.name(...) with its text arguments (positions in which) translated."""
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_fs_tr", False):
        return
    static = isinstance(cls.__dict__.get(name), staticmethod) or name in STATICS
    which = tuple(i if static else i + 1 for i in which)    # instance calls: args[0] is the widget itself

    def call(*args, **kw):
        a = list(args)
        for i in which:
            if i < len(a):
                if isinstance(a[i], str):
                    a[i] = tr(a[i])
                    if first:                     # addItem("Text", "data"): the data stays as it is
                        break
                elif lists and isinstance(a[i], (list, tuple)):
                    a[i] = [tr(x) if isinstance(x, str) else x for x in a[i]]
        for k in ("text", "title", "caption", "label", "filter", "toolTip"):
            if isinstance(kw.get(k), str):
                kw[k] = tr(kw[k])
        return orig(*a, **kw)

    call._fs_tr = True
    setattr(cls, name, staticmethod(call) if static else call)


def _first_texts(args):
    """Translate the leading str arguments of a constructor (QPushButton("Go", parent)…)."""
    return [tr(a) if isinstance(a, str) else a for a in args]


def _subclass(module, name):
    base = getattr(module, name)
    if getattr(base, "_fs_tr", False):
        return

    def __init__(self, *args, **kw):
        base.__init__(self, *_first_texts(args), **kw)

    cls = type(name, (base,), {"__init__": __init__, "_fs_tr": True, "__module__": base.__module__})
    setattr(module, name, cls)


def _patch_qt():
    from PySide6 import QtGui, QtWidgets as W

    # constructors: QLabel("Text"), QPushButton("Go"), QAction("Open", parent), QMenu("Title")…
    for n in ("QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QToolButton", "QMenu"):
        _subclass(W, n)
    _subclass(QtGui, "QAction")
    # instance calls
    _wrap(W.QLabel, "setText")
    _wrap(W.QAbstractButton, "setText")
    _wrap(W.QGroupBox, "setTitle")
    _wrap(W.QWidget, "setToolTip")
    _wrap(W.QWidget, "setWindowTitle")
    _wrap(W.QLineEdit, "setPlaceholderText")
    _wrap(W.QPlainTextEdit, "setPlaceholderText")
    _wrap(W.QComboBox, "addItem", which=(0, 1), first=True)
    _wrap(W.QComboBox, "insertItem", which=(1, 2), first=True)
    _wrap(W.QComboBox, "setItemText", which=(1,))
    _wrap(W.QComboBox, "addItems", which=(0,), lists=True)
    _wrap(W.QComboBox, "setPlaceholderText")
    _wrap(W.QMenu, "addAction", which=(0, 1), first=True)
    _wrap(W.QMenu, "addMenu", which=(0, 1), first=True)
    _wrap(W.QMenu, "addSection", which=(0, 1), first=True)
    _wrap(W.QMenu, "setTitle")
    _wrap(QtGui.QAction, "setText")
    _wrap(QtGui.QAction, "setToolTip")
    _wrap(W.QTabWidget, "addTab", which=(1, 2), first=True)
    _wrap(W.QTabWidget, "insertTab", which=(2, 3), first=True)
    _wrap(W.QTabWidget, "setTabText", which=(1,))
    _wrap(W.QTabWidget, "setTabToolTip", which=(1,))
    _wrap(W.QTableWidget, "setHorizontalHeaderLabels", which=(0,), lists=True)
    _wrap(W.QTreeWidget, "setHeaderLabels", which=(0,), lists=True)
    _wrap(W.QMessageBox, "setText")
    _wrap(W.QMessageBox, "setInformativeText")
    for n in ("question", "information", "warning", "critical"):
        _wrap(W.QMessageBox, n, which=(1, 2))           # (parent, title, text, …)
    for n in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        _wrap(W.QFileDialog, n, which=(1, 3))           # (parent, caption, dir, filter)
    for n in ("getText", "getItem", "getInt", "getDouble"):
        _wrap(W.QInputDialog, n, which=(1, 2))          # (parent, title, label, …)
    _wrap(W.QProgressDialog, "setLabelText")
    _subclass(W, "QGraphicsSimpleTextItem")
    _wrap(W.QGraphicsSimpleTextItem, "setText")
    _wrap(W.QGraphicsTextItem, "setPlainText")
    _wrap(W.QGraphicsScene, "addText", first=True)
    _wrap(W.QAbstractSpinBox, "setSpecialValueText")
    _wrap(W.QSpinBox, "setSuffix")
    _wrap(W.QDoubleSpinBox, "setSuffix")
    _wrap(W.QSpinBox, "setPrefix")


QT_WORDS = {                                             # Qt's own buttons and menus (no Vietnamese file ships with Qt)
    "vi": {"OK": "OK", "Cancel": "Hủy", "Yes": "Có", "No": "Không", "Close": "Đóng", "Apply": "Áp dụng",
           "Save": "Lưu", "Open": "Mở", "Discard": "Bỏ", "Reset": "Đặt lại", "Help": "Trợ giúp", "Abort": "Hủy bỏ",
           "Retry": "Thử lại", "Ignore": "Bỏ qua", "Yes to All": "Có tất cả", "No to All": "Không tất cả",
           "Undo": "Hoàn tác", "Redo": "Làm lại", "Cut": "Cắt", "Copy": "Sao chép", "Paste": "Dán",
           "Delete": "Xóa", "Select All": "Chọn tất cả", "Show Details...": "Xem chi tiết…",
           "Hide Details...": "Ẩn chi tiết…", "Restore Defaults": "Mặc định"},
}
_translator = None


def _qt_translator(lang):
    from PySide6.QtCore import QCoreApplication, QTranslator
    words = QT_WORDS.get(lang, {})

    class _Words(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):
            if not source:
                return ""
            amp = "&" in source
            key = source.replace("&", "")
            t = words.get(key) or _words.get(key)
            if not t:
                return ""                                 # "" = no translation, Qt keeps its English
            return ("&" + t) if amp and "&" not in t else t

        def isEmpty(self):
            return False

    global _translator
    _translator = _Words()
    QCoreApplication.installTranslator(_translator)


def install(lang: str):
    """Use lang for the interface. Call after QApplication is made, before the window (and the ui modules)."""
    load(lang)
    if lang != "en":
        _patch_qt()
        try:
            _qt_translator(lang)
        except Exception as e:  # noqa: BLE001
            log.warning("Qt words not translated: %s", e)
