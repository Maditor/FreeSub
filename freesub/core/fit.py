"""Break subtitle lines so they fit a width (the drawn box, or the video), with lines of even length.

measure(text) -> width in pixels is given by the caller (the UI measures with the real font)."""


def _tokens(text: str):
    """Words (with their space) for spaced languages; single characters for Chinese / Japanese / Thai."""
    flat = " ".join(text.replace("\n", " ").split())
    if " " in flat or len(flat) < 2:
        words = flat.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]
    return list(flat)


def _greedy(tokens, measure, limit):
    lines, cur = [], ""
    for t in tokens:
        if cur and measure((cur + t).rstrip()) > limit:
            lines.append(cur.rstrip())
            cur = t
        else:
            cur += t
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def balanced(text: str, measure, n: int):
    """Best split into at most n lines (the widest line as short as possible): (lines, widest)."""
    tokens = _tokens(text)
    if not tokens:
        return [], 0.0
    lo = max(measure(t.strip()) for t in tokens)
    hi = measure("".join(tokens).strip())
    if n <= 1 or hi <= lo:
        return ["".join(tokens).strip()], hi
    for _ in range(18):                      # smallest width limit that still gives <= n lines
        mid = (lo + hi) / 2
        if len(_greedy(tokens, measure, mid)) <= n:
            hi = mid
        else:
            lo = mid
    lines = _greedy(tokens, measure, hi)
    return lines, max(measure(l) for l in lines)


def _words_ok(lines, max_words: int) -> bool:
    if not max_words:
        return True
    for line in lines:
        n = len(line.split()) if " " in line.strip() else len(line.strip()) / 2   # no spaces: 2 characters a word
        if n > max_words:
            return False
    return True


def fit(text: str, measure_for_size, max_w: float, size: int, box_h: float | None = None, max_lines: int = 2,
        max_words: int = 0):
    """Fewest lines (up to max_lines) that fit max_w and have at most max_words words each. In a box the text
    gets smaller with more lines (box height / lines), the same rule the export uses.
    measure_for_size(px) -> measure function for that font size."""
    if not text.strip():
        return text
    best = None
    for n in range(1, max(1, max_lines) + 1):
        px = int(min(size, box_h / (n * 1.25))) if box_h else size
        lines, widest = balanced(text, measure_for_size(max(8, px)), n)
        best = lines
        if widest <= max_w and _words_ok(lines, max_words):
            break
    return "\n".join(best)


def limits_text():
    """(lines per subtitle, words per line) from Style."""
    from ..config import settings
    s = settings()
    return max(1, int(s["sub_max_lines"])), max(2, int(s["sub_max_words"]))


# ------------------------------------------------------------------ fitting a whole file (no window needed)
def limits(project):
    """(widest line in px, box height in px or None, font size in px) for this video, from Style and the box:
    the same numbers the 'Fit the lines to the box' button and the export use."""
    from . import ffmpeg
    from .style import box_region, sub_style
    from ..steps.ocr import region_px
    info = project.meta.get("info") or {}
    if not info.get("width"):
        info = ffmpeg.probe(project.video)
    W, H = info.get("width") or 1280, info.get("height") or 720
    st = sub_style(H)
    region = box_region(project)
    if region:
        _x, _y, bw, bh = region_px(region, W, H)
        return bw - 16, bh, st
    return W - 40, None, st


def file_measurer(family: str, bold: bool):
    """measure_for(px) using the font file itself (Pillow), so it works on the work thread too. Falls back to
    an average letter width when the font cannot be read."""
    path, index = _font_file(family, bold)
    cache = {}

    def measure_for(px):
        if px not in cache:
            fn = None
            if path:
                try:
                    from PIL import ImageFont
                    font = ImageFont.truetype(path, px, index=index)
                    k = 1.04 if bold and not _is_bold_file(path, index) else 1.0      # faux bold is wider
                    fn = (lambda s, font=font, k=k: font.getlength(s) * k)          # noqa: E731
                except Exception:  # noqa: BLE001
                    fn = None
            if fn is None:
                fn = (lambda s, px=px: len(s) * px * (0.6 if bold else 0.55))       # noqa: E731
            cache[px] = fn
        return cache[px]
    return measure_for


def _font_file(family, bold):
    """(file, index in a .ttc) of the regular / bold face of family, or (None, 0)."""
    try:
        from . import fonts
        files, name = fonts.find(family)
        idx = fonts.index()
    except Exception:  # noqa: BLE001
        return None, 0
    best, best_score = None, None
    for path in files:
        for i, f in enumerate(idx.get(path, {}).get("fonts", [])):
            names = [n.lower() for n in f["family"] + f["typo"] + f["full"]]
            if family.lower() not in names and name.lower() not in names:
                continue
            style = " ".join(f["style"]).lower()
            is_bold = "bold" in style
            italic = "italic" in style or "oblique" in style
            score = (italic, is_bold != bold, style not in ("regular", "normal", "book", "roman", "bold"))
            if best_score is None or score < best_score:
                best, best_score = (path, i), score
    return best or (None, 0)


def _is_bold_file(path, index):
    try:
        from . import fonts
        f = fonts.index()[path]["fonts"][index]
        return "bold" in " ".join(f["style"]).lower()
    except Exception:  # noqa: BLE001
        return False


def fit_srt(project, work_name: str) -> int:
    """Break every line of one of the project's .srt files again so it fits (as the button does). Returns
    how many lines changed; the file is written only when something changed."""
    from .subtitle import load_srt, save_srt
    path = project.path(work_name)
    cues = load_srt(path)
    if not cues:
        return 0
    max_w, box_h, st = limits(project)
    measure_for = file_measurer(st["font"], st["bold"])
    n_lines, n_words = limits_text()
    changed = 0
    for c in cues:
        new = fit(c.text, measure_for, max_w, st["size"], box_h, n_lines, n_words)
        if new != c.text:
            c.text = new
            changed += 1
    if changed:
        save_srt(cues, path)
    return changed
