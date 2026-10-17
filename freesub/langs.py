"""Languages: code -> English name (UI, AI prompts later)."""

LANGS = {
    "en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "tl": "Filipino", "hi": "Hindi",
    "fr": "French", "es": "Spanish", "pt": "Portuguese", "de": "German", "it": "Italian",
    "nl": "Dutch", "ru": "Russian", "uk": "Ukrainian", "pl": "Polish", "tr": "Turkish",
    "ar": "Arabic", "sv": "Swedish", "cs": "Czech", "hu": "Hungarian", "el": "Greek",
}
# languages written without spaces between words (line length is counted differently)
NO_SPACE = {"ja", "zh", "th", "lo", "my", "km", "yue"}


def name(code: str) -> str:
    return LANGS.get(code, code or "?")
