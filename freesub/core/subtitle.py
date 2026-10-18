"""Subtitle cues: read / write SRT, timestamps."""
import os
import re
from dataclasses import dataclass, field


@dataclass
class Cue:
    start: float            # seconds
    end: float
    text: str
    extra: dict = field(default_factory=dict)   # e.g. {"gender": "f", "words": [...]} (not written to SRT)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def fmt_time(t: float, sep: str = ",") -> str:
    """12.5 -> '00:00:12,500'."""
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def fmt_clock(t: float) -> str:
    """Short clock for the UI: 1:02:03.4 / 2:03.4."""
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


_TS = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?")
_ARROW = re.compile(r"(\d+:\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)\s*-->\s*(\d+:\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)")


def parse_time(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + (int((ms or "0").ljust(3, "0")) / 1000)


def read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_srt(text: str) -> list[Cue]:
    """Tolerant SRT reader: missing / wrong numbers, '.' instead of ',', extra blank lines."""
    cues = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, n = 0, len(lines)
    while i < n:
        m = _ARROW.search(lines[i])
        if not m:
            i += 1
            continue
        start, end = parse_time(m.group(1)), parse_time(m.group(2))
        i += 1
        body = []
        while i < n and lines[i].strip() != "":
            # a following cue without a blank line in between
            if _ARROW.search(lines[i]) and body and body[-1].strip().isdigit():
                body.pop()
                break
            body.append(lines[i].rstrip())
            i += 1
        cues.append(Cue(start, max(end, start), "\n".join(body).strip()))
    cues.sort(key=lambda c: c.start)
    return cues


def load_srt(path: str) -> list[Cue]:
    return parse_srt(read_text(path))


def to_srt(cues: list[Cue]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text.strip()}\n")
    return "\n".join(out)


def save_srt(cues: list[Cue], path: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(to_srt(cues))
    os.replace(tmp, path)


def cue_at(cues: list[Cue], t: float) -> int:
    """Index of the cue shown at time t, or -1 (binary search on start times)."""
    lo, hi = 0, len(cues) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if cues[mid].start <= t:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best >= 0 and cues[best].start <= t < cues[best].end:
        return best
    return -1


# ------------------------------------------------------------------ ASS (burned-in subtitles)
def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#ffffff").lstrip("#")
    if len(h) != 6:
        h = "ffffff"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_text(text: str) -> str:
    t = text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    return t.replace("\r", "").replace("\n", "\\N")


def to_ass(width: int, height: int, tracks: list[dict]) -> str:
    """tracks: [{"name", "cues", "font", "size" (px), "color", "outline_color", "outline" (px),
    "align" (2 = bottom centre, 5 = middle), "margin_v", "margin_l", "margin_r", "pos": (x, y) or None}]"""
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
            "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"]
    events = ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def style_line(name, tr, align):
        bold = -1 if tr.get("bold", True) else 0
        if tr.get("box") is not None:            # opaque box behind each line (BorderStyle 3)
            border, edge = 3, _ass_color("#000000", int(round(255 * (1 - tr["box"]))))
            width, shadow = tr.get("box_pad", 6), 0.0
        else:
            border, edge = 1, _ass_color(tr.get("outline_color", "#000000"))
            width, shadow = tr.get("outline", 2), tr.get("shadow", 0.5)
        return (f"Style: {name},{tr.get('font', 'Arial')},{int(tr.get('size', 40))},"
                f"{_ass_color(tr.get('color', '#ffffff'))},&H000000FF,{edge},&H64000000,{bold},0,0,0,100,100,0,0,"
                f"{border},{width:.1f},{shadow:.1f},{align},"
                f"{int(tr.get('margin_l', 20))},{int(tr.get('margin_r', 20))},{int(tr.get('margin_v', 30))},1")

    for i, tr in enumerate(tracks):
        name = tr.get("name") or f"S{i}"
        looks = tr.get("looks") or {}               # lines with a look of their own: a style each
        look_names = {}
        for k, lk in looks.items():
            key = tuple(sorted((a, b) for a, b in lk.items() if a not in ("pos", "an")))
            if key not in look_names:
                look_names[key] = f"{name}L{len(look_names) + 1}"
                head.append(style_line(look_names[key], dict(tr, **{a: b for a, b in lk.items()
                                                                     if a not in ("pos", "an")}),
                                       tr.get("align", 2)))
        head.append(style_line(name, tr, tr.get("align", 2)))
        pos = tr.get("pos")
        tag = f"{{\\an{tr.get('align', 5)}\\pos({pos[0]:.0f},{pos[1]:.0f})}}" if pos else ""
        for k, c in enumerate(tr["cues"]):
            if not c.text.strip():
                continue
            style, t = name, tag
            lk = looks.get(k)
            if lk:
                style = look_names[tuple(sorted((a, b) for a, b in lk.items() if a not in ("pos", "an")))]
                if lk.get("pos"):
                    t = f"{{\\an{lk.get('an', 2)}\\pos({lk['pos'][0]:.0f},{lk['pos'][1]:.0f})}}"
            events.append(f"Dialogue: {i},{_ass_time(c.start)},{_ass_time(c.end)},{style},,0,0,0,,{t}{_ass_text(c.text)}")
    return "\n".join(head + events) + "\n"
