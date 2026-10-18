"""Subtitle look in pixels for a picture of a given height. The preview and the export both use this, so
what the preview shows is what gets burned in."""
from ..config import settings

OUTLINE = {"none": 0.0, "thin": 0.6, "normal": 1.0, "thick": 1.7}
# what one line can have of its own (the timeline: click a line, then Style); the rest belongs to the video
LINE_KEYS = ("sub_font", "sub_size", "sub_bold", "sub_color", "sub_outline_size", "sub_outline", "sub_margin",
             "sub_pos_x", "sub_box", "sub_box_color", "ocr_box_opacity", "ocr_box_blur")
# plus "box_region": where this line's box is (dragged while the line is chosen)


class _Merged:
    def __init__(self, base, over):
        self.base, self.over = base, over or {}

    def __getitem__(self, k):
        return self.over[k] if k in self.over else self.base[k]


def sub_style(height: float, look: dict | None = None) -> dict:
    """look: one line's own values (LINE_KEYS), over the video's style."""
    s = _Merged(settings(), look)
    size = max(10, int(height * float(s["sub_size"]) / 100))
    k = OUTLINE.get(s["sub_outline_size"], 1.0)
    return {
        "font": (s["sub_font"] or "Arial").strip(),
        "bold": bool(s["sub_bold"]),
        "size": size,
        "color": s["sub_color"],
        "outline_color": s["sub_outline"],
        "outline": max(1.0, size / 14 * k) if k else 0.0,
        "margin_v": int(height * float(s["sub_margin"]) / 100),
        "pos_x": min(0.95, max(0.05, float(s["sub_pos_x"]))),
    }


def box_color(look=None) -> str:
    c = (_Merged(settings(), look)["sub_box_color"] or "#000000").strip()
    return c if len(c) == 7 and c.startswith("#") else "#000000"


def box_look(look=None, video_area=None) -> dict:
    """One line's box: {on, area, color, opacity (0-1), blur (px)}; the line's own values over the video's."""
    m = _Merged(settings(), look)
    area = (look or {}).get("box_region") or video_area
    return {"on": bool(m["sub_box"]) and bool(area), "area": list(area) if area else None,
            "color": box_color(look), "opacity": max(0.0, min(1.0, float(m["ocr_box_opacity"]) / 100)),
            "blur": max(0, int(m["ocr_box_blur"]))}


def box_region(project=None):
    """The drawn box [x, y, w, h] (fractions) when the subtitles go in a box, else None. The same area is the
    one OCR reads from: drawn once, used for both."""
    s = settings()
    if not s["sub_box"]:
        return None
    return box_area(project)


DEFAULT_BOX = [0.10, 0.82, 0.80, 0.12]       # 10 % in from each side, 12 % of the height, near the bottom


def box_area(project=None):
    """Where this video's box is: where it was drawn or dragged, else the default place. Each video keeps its
    own; the area OCR reads is separate, so moving the box never makes the subtitles be read again."""
    meta = project.meta if project is not None else {}
    region = meta.get("box_region")
    return list(region) if region else list(DEFAULT_BOX)


def line_looks(project) -> dict:
    """{line index (str): its own look} of this video's subtitle lines."""
    # one look for the whole project now: looks saved per line by an older FreeSub are not used
    return {}


def attach_looks(cues, project):
    """Put each line's own look in cue.extra["look"] (lines are counted in the original .srt's order)."""
    looks = line_looks(project)
    if looks:
        for i, c in enumerate(cues or []):
            lk = looks.get(str(i))
            if lk:
                c.extra["look"] = dict(lk)
    return cues


def look_moves(look) -> bool:
    return bool(look) and ("sub_pos_x" in look or "sub_margin" in look)


def video_style(project) -> dict:
    """This video's own look (project.json "style"); missing values follow the shared settings."""
    st = project.meta.get("style") if project is not None else None
    return dict(st) if isinstance(st, dict) else {}
