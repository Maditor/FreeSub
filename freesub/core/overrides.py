"""Settings one video can have on its own (Batch → the gear on a video). They are kept in its project.json
("overrides") and win over the shared settings only while that video runs (Settings.overlay)."""
from ..langs import LANGS

TARGETS = ["vi", "en", "ja", "ko", "zh", "th", "id", "fr", "es", "de", "ru", "pt"]


def fields():
    """[(title, [(label, {setting: value, ...}), ...])]: each choice sets one or more settings."""
    from ..steps import export as ex
    from . import models
    return [
        ("Subtitles from", [("Speech", {"subs_source": "speech"}),
                            ("The picture (OCR)", {"subs_source": "ocr"})]),
        ("Spoken language", [("Auto detect", {"whisper_language": "auto"})] +
         [(f"{v} ({k})", {"whisper_language": k}) for k, v in LANGS.items()]),
        ("Speech model", [(n, {"whisper_model": n}) for n in models.WHISPER]),
        ("Translate", [("Off", {"do_translate": False})] +
         [(f"Into {LANGS.get(k, k)}", {"do_translate": True, "target_lang": k}) for k in TARGETS]),
        ("Narration", [("Off", {"do_voice": False}), ("On", {"do_voice": True})]),
        ("Subtitles shown", [("Translation", {"export_which": "translation"}),
                             ("Original", {"export_which": "original"}),
                             ("Translation + original", {"export_which": "both"})]),
        ("Text size", [(f"{v:g} %", {"sub_size": float(v)}) for v in (3, 3.5, 4, 4.5, 5, 5.5, 6, 7, 8, 10)]),
        ("Box", [("On", {"sub_box": True}), ("Off", {"sub_box": False})]),
        ("Video size", [(t, {"export_resolution": k}) for k, t in ex.RESOLUTIONS]),
        ("Video quality", [(t, {"export_quality": k}) for k, t in ex.QUALITY_TEXT.items()]),
    ]


def describe(over: dict) -> str:
    """'Translate: Into English · Narration: On' for a tooltip."""
    if not over:
        return ""
    parts = []
    for title, choices in fields():
        for text, values in choices:
            if values and all(over.get(k) == v for k, v in values.items()):
                parts.append(f"{title}: {text}")
                break
    return " · ".join(parts)
