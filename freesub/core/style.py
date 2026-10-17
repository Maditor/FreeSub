"""Subtitle look in pixels for a picture of a given height. The preview and the export both use this, so
what the preview shows is what gets burned in."""
from ..config import settings

OUTLINE = {"none": 0.0, "thin": 0.6, "normal": 1.0, "thick": 1.7}


def sub_style(height: float) -> dict:
    s = settings()
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
    }


def box_region(project=None):
    """The drawn box [x, y, w, h] (fractions) when the subtitles go in a box, else None. The same area is the
    one OCR reads from: drawn once, used for both."""
    s = settings()
    if not s["sub_box"]:
        return None
    region = (project.meta.get("ocr_region") if project is not None else None) or s["ocr_region"]
    return list(region) if region else None
