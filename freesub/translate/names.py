"""Name lists: the names and words of one series, each with how it is written in the original and (if wanted)
in the translation.

    Tanjiro            ->  heard right by Whisper
    炭治郎 = Tanjiro   ->  heard right, and always translated as Tanjiro

One list is chosen in Steps → Subtitles (none = no list). The original side helps Whisper spell them; the
pairs go to the translator (the AI follows them; with Google they are put in before the line is translated).
Kept in Documents\\FreeSub Data\\name_lists.json.
"""
import json
import os
import threading
import time

from ..config import settings, user_dir

FILE = "name_lists.json"
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(user_dir(), FILE)


def _load() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        lists = data.get("lists") if isinstance(data, dict) else None
        if isinstance(lists, list):
            return [x for x in lists if isinstance(x, dict) and x.get("id")]
    except (OSError, ValueError):
        pass
    return []


def _save(lists: list[dict]):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"lists": lists}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def _clean(pairs) -> list[list[str]]:
    out = []
    for p in pairs or []:
        if isinstance(p, (list, tuple)) and p:
            src = " ".join(str(p[0]).split())
            dst = " ".join(str(p[1]).split()) if len(p) > 1 else ""
            if src:
                out.append([src, dst])
    return out


def all_lists() -> list[dict]:
    """[{id, name, pairs: [[original, translation], ...]}]"""
    migrate()
    with _lock:
        return [{"id": x["id"], "name": x.get("name") or "Names", "pairs": _clean(x.get("pairs"))}
                for x in _load()]


def get(list_id: str) -> dict | None:
    return next((x for x in all_lists() if x["id"] == list_id), None) if list_id else None


def save_all(lists: list[dict]):
    """Write every list (the editor saves them all at once)."""
    with _lock:
        _save([{"id": x["id"], "name": (x.get("name") or "Names").strip(), "pairs": _clean(x.get("pairs"))}
               for x in lists])


def new_id() -> str:
    return f"names_{int(time.time() * 1000)}"


def migrate():
    """Older FreeSub had a Names box (Whisper) and a Names and terms box (translation): they become one list."""
    s = settings()
    hint, gloss = (s.get("whisper_prompt") or "").strip(), (s.get("translate_glossary") or "").strip()
    if not (hint or gloss):
        return
    pairs = []
    for line in gloss.splitlines():
        if "=" in line:
            a, b = line.split("=", 1)
            pairs.append([a.strip(" -•*\t"), b.strip()])
        elif line.strip():
            pairs.append([line.strip(), ""])
    known = {p[0] for p in pairs}
    for name in hint.replace(";", ",").split(","):
        if name.strip() and name.strip() not in known:
            pairs.append([name.strip(), ""])
    with _lock:
        lists = _load()
        lid = new_id()
        from ..i18n import tr
        lists.append({"id": lid, "name": tr("My names"), "pairs": _clean(pairs)})
        _save(lists)
    s.update(whisper_prompt="", translate_glossary="", names_list=lid)


# ---------------------------------------------------------------------------------------------- use
def chosen() -> dict | None:
    return get(settings().get("names_list") or "")


def whisper_hint() -> str:
    """The originals of the chosen list, for Whisper (it spells them right)."""
    lst = chosen()
    if not lst:
        return ""
    return ", ".join(p[0] for p in lst["pairs"])[:600]


def glossary() -> str:
    """'original = translation' lines of the chosen list, for the translator."""
    lst = chosen()
    if not lst:
        return ""
    return "\n".join(f"{a} = {b}" for a, b in lst["pairs"] if b)
