"""Find the files of a font family and the name ffmpeg (libass) knows it by.

Why this is needed:
  * ffmpeg's subtitle renderer often does not see fonts installed "for the current user only"
    (%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts), so the export copies the font files next to the .ass (fontsdir);
  * a font has several names: Windows / Qt may show the typographic family ("Montserrat", style SemiBold) or the
    old-style family ("Montserrat SemiBold"), while libass looks fonts up by the old-style family written inside
    the file. The names are read from each file's 'name' table, so the .ass gets a name libass can find.
The index of every font file (names, style) is cached in fonts_index.json and only files that changed are read.
"""
import json
import logging
import os
import struct

from ..config import data_dir

log = logging.getLogger(__name__)
EXTS = (".ttf", ".otf", ".ttc", ".otc")
_REG = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"


# ------------------------------------------------------------------ reading the 'name' table
def _names_at(f, base) -> dict:
    """{nameID: set of names} for the font starting at offset base."""
    f.seek(base)
    head = f.read(12)
    if len(head) < 12:
        return {}
    num = struct.unpack(">H", head[4:6])[0]
    recs = f.read(16 * num)
    name_off = None
    for i in range(num):
        tag, _cs, off, _ln = struct.unpack(">4sIII", recs[16 * i:16 * i + 16])
        if tag == b"name":
            name_off = off
            break
    if name_off is None:
        return {}
    f.seek(name_off)
    _fmt, count, str_off = struct.unpack(">HHH", f.read(6))
    entries = f.read(12 * count)
    out = {}
    for i in range(count):
        pid, eid, _lang, nid, ln, off = struct.unpack(">HHHHHH", entries[12 * i:12 * i + 12])
        if nid not in (1, 2, 4, 16, 17):
            continue
        f.seek(name_off + str_off + off)
        raw = f.read(ln)
        try:
            if pid == 3 or pid == 0:
                s = raw.decode("utf-16-be")
            elif pid == 1 and eid == 0:
                s = raw.decode("mac_roman")
            else:
                continue
        except UnicodeDecodeError:
            continue
        s = s.strip("\x00 ").strip()
        if s:
            out.setdefault(nid, set()).add(s)
    return out


def read_names(path) -> list[dict]:
    """One dict per font in the file: {family, typo, full, style} (lists of names)."""
    try:
        with open(path, "rb") as f:
            tag = f.read(4)
            if tag == b"ttcf":
                f.read(4)
                n = struct.unpack(">I", f.read(4))[0]
                offsets = struct.unpack(f">{n}I", f.read(4 * n))
            else:
                offsets = (0,)
            fonts = []
            for off in offsets:
                nm = _names_at(f, off)
                if nm:
                    fonts.append({"family": sorted(nm.get(1, [])), "typo": sorted(nm.get(16, [])),
                                  "full": sorted(nm.get(4, [])),
                                  "style": sorted(nm.get(17, []) or nm.get(2, []))})
            return fonts
    except (OSError, struct.error, ValueError):
        return []


# ------------------------------------------------------------------ where fonts are
def font_paths() -> list[str]:
    paths = []
    if os.name == "nt":
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
        try:                                      # fonts registered from other folders
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    key = winreg.OpenKey(root, _REG)
                except OSError:
                    continue
                with key:
                    i = 0
                    while True:
                        try:
                            _n, value, _t = winreg.EnumValue(key, i)
                        except OSError:
                            break
                        i += 1
                        if isinstance(value, str) and os.path.isabs(value):
                            paths.append(value)
        except ImportError:
            pass
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts")]
    for d in dirs:
        if os.path.isdir(d):
            for root, _ds, files in os.walk(d, followlinks=True):
                paths += [os.path.join(root, f) for f in files if f.lower().endswith(EXTS)]
    seen, out = set(), []
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and os.path.isfile(p) and p.lower().endswith(EXTS):
            seen.add(k)
            out.append(p)
    return out


def index() -> dict:
    """{path: [font names...]} for every installed font (cached; changed files are read again)."""
    cache_path = os.path.join(data_dir(), "fonts_index.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    out, dirty = {}, False
    for p in font_paths():
        try:
            st = os.stat(p)
        except OSError:
            continue
        stamp = f"{st.st_size}-{int(st.st_mtime)}"
        old = cache.get(p)
        if old and old.get("stamp") == stamp:
            out[p] = old
        else:
            out[p] = {"stamp": stamp, "fonts": read_names(p)}
            dirty = True
    if dirty or len(out) != len(cache):
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        except OSError:
            pass
    return out


# ------------------------------------------------------------------ lookup
def find(family: str) -> tuple[list[str], str]:
    """(files of this family, the family name to write in the .ass). Files are empty when not found."""
    want = (family or "").strip().lower()
    if not want:
        return [], family
    files, picks = [], []
    try:
        idx = index()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not read the installed fonts: %s", e)
        return [], family
    for path, rec in idx.items():
        for font in rec.get("fonts", []):
            names = [n.lower() for n in font["family"] + font["typo"] + font["full"]]
            if want in names:
                if path not in files:
                    files.append(path)
                picks.append(font)
    if not files:
        return [], family
    # the name libass finds: the old-style family of the regular style, else of any match
    def score(font):
        style = " ".join(font["style"]).lower()
        exact = want in [n.lower() for n in font["family"]]
        return (0 if exact else 1, 0 if style in ("regular", "normal", "book", "roman") else 1)
    best = min(picks, key=score)
    exact = [n for n in best["family"] if n.lower() == want]
    name = exact[0] if exact else (best["family"][0] if best["family"] else family)
    # the bold / italic files of that family come too (same old-style family)
    for path, rec in idx.items():
        if path not in files and any(name.lower() in [n.lower() for n in f["family"]] for f in rec.get("fonts", [])):
            files.append(path)
    return files, name


def usable_names() -> set:
    """Lower-case names of every font the export can use (TrueType / OpenType files)."""
    names = set()
    for rec in index().values():
        for font in rec.get("fonts", []):
            names.update(n.lower() for n in font["family"] + font["typo"] + font["full"])
    return names


def font_files(family: str) -> list[str]:
    return find(family)[0]
