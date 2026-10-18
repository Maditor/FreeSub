"""Step: hard subtitles burned into the picture -> subs.src.srt.

1. ffmpeg reads N frames per second (with the graphics card's decoder when it can), cropped to the subtitle
   area the user drew on the preview.
2. Only the subtitle's own pixels are compared (bright letters with a dark edge around them): a frame with the
   same letters as the last frame read is not read again, even when the picture behind them moves, and a
   frame with no letters is not read at all.
3. RapidOCR (PP-OCRv6, on this PC) reads the text; lines are joined top to bottom.
4. Frames in a row with the same text become one subtitle line with its start / end time.
A checkpoint (ocr.partial.json) lets a stopped run go on from where it ended.
"""
import difflib
import json
import logging
import os
import re
import subprocess
import time

import numpy as np

from ..config import settings
from ..core import ffmpeg
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, Project
from ..core.subtitle import Cue, save_srt
from ..log import step_timer

log = logging.getLogger(__name__)
PARTIAL = "ocr.partial.json"
MAX_W = 1600
_ENGINE = {"e": None, "rec": None}


def available() -> bool:
    try:
        import rapidocr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _engine(rec_only=False):
    key = "rec" if rec_only else "e"
    if _ENGINE[key] is None:
        import rapidocr
        from rapidocr import RapidOCR
        builtin = os.path.join(os.path.dirname(rapidocr.__file__), "models")
        extra = {"Global.use_det": False} if rec_only else {}      # own instance: never mix the two
        _ENGINE[key] = RapidOCR(params={**extra,
            "Global.log_level": "error",
            "Global.use_cls": False,
            "Global.text_score": 0.0,
            "Det.model_path": os.path.join(builtin, "PP-OCRv6_det_small.onnx"),
            "Cls.model_path": os.path.join(builtin, "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
            "Rec.model_path": os.path.join(builtin, "PP-OCRv6_rec_small.onnx"),
            "Det.box_thresh": 0.45,
            "Det.limit_type": "max",          # a subtitle strip is read at its own size (much faster)
        })
    return _ENGINE[key]


def region_px(region, width, height):
    """[x, y, w, h] fractions -> even pixel values inside the frame."""
    x, y, w, h = region
    px = max(0, min(width - 2, int(x * width)))
    py = max(0, min(height - 2, int(y * height)))
    pw = max(2, min(width - px, int(w * width)))
    ph = max(2, min(height - py, int(h * height)))
    return px - px % 2, py - py % 2, pw - pw % 2, ph - ph % 2


def read_text(img_bgr, min_score=0.6) -> tuple[str, float]:
    """(text, mean score) of one cropped frame. The detector finds the text pieces; pieces on the same row
    are then read again as ONE line (a lone "I" or a split word is not lost), rows top to bottom."""
    import cv2
    pad = max(8, img_bgr.shape[0] // 6)          # text touching the edge is often missed without a border
    img = cv2.copyMakeBorder(img_bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    res = _engine()(img)
    boxes = res.boxes if getattr(res, "boxes", None) is not None else []
    items = []
    for box in boxes:
        pts = np.asarray(box)
        x1, y1, x2, y2 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
        items.append([x1, y1, x2, y2])
    if not items:
        return "", 0.0
    items.sort(key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    rows = []
    for b in items:
        cy, hh = (b[1] + b[3]) / 2, b[3] - b[1]
        for r in rows:
            rcy, rh = (r[1] + r[3]) / 2, r[3] - r[1]
            if abs(cy - rcy) < max(hh, rh) * 0.6:
                r[0], r[1], r[2], r[3] = min(r[0], b[0]), min(r[1], b[1]), max(r[2], b[2]), max(r[3], b[3])
                break
        else:
            rows.append(list(b))
    rows.sort(key=lambda r: r[1])
    lines, scores = [], []
    H, W = img.shape[:2]
    for x1, y1, x2, y2 in rows:
        m = int((y2 - y1) * 0.15) + 2
        crop = img[max(0, int(y1) - m):min(H, int(y2) + m), max(0, int(x1) - m):min(W, int(x2) + m)]
        if crop.size == 0:
            continue
        r = _engine(rec_only=True)(crop)
        txt = "".join(getattr(r, "txts", None) or []).strip()
        sc = float(np.mean(getattr(r, "scores", None) or [0.0]))
        if txt and sc >= min_score:
            lines.append(txt)
            scores.append(sc)
    if not lines:
        return "", 0.0
    return "\n".join(lines), sum(scores) / len(scores)


def _cjk(t: str) -> bool:
    return bool(re.search(r"[぀-ヿ㐀-鿿가-힯]", t))


def guess_lang(texts) -> str:
    joined = "".join(texts)
    if re.search(r"[぀-ヿ]", joined):
        return "ja"
    if re.search(r"[가-힯]", joined):
        return "ko"
    if re.search(r"[㐀-鿿]", joined):
        return "zh"
    return "en"


def _norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t.lower())


def text_mask(img_bgr):
    """The subtitle's letters: bright pixels with a dark edge near them (outline, shadow or a dark box).
    Half size: enough to compare, and quick."""
    import cv2
    g = cv2.cvtColor(img_bgr[::2, ::2], cv2.COLOR_BGR2GRAY)
    bright = g >= 175
    dark = (g <= 90).astype(np.uint8)
    near = cv2.dilate(dark, np.ones((5, 5), np.uint8)) > 0
    return bright & near


class Changes:
    """Decides whether a frame must be read: compared with the last frame that WAS read (no slow drift)."""
    SAME = 0.35            # letters' pixels that may differ (the picture behind them moves) and still be the same

    def __init__(self, check_every: int = 6):
        self.ref_mask = None        # the letters of the last frame read
        self.ref_small = None       # its picture (small), for subtitles without a dark edge
        self.min_px = 0
        self.quiet = False          # the last frame read had no text at all
        self.check_every = check_every      # no letters seen: still read one frame in this many (letters
        self._skipped = 0                   # without a dark edge are not missed)
        self.last_small = None              # the frame before, and whether the picture was moving just
        self.was_moving = False             # before this frame (letters coming make THIS frame differ)

    def need_read(self, img) -> bool:
        mask = text_mask(img)
        small = img[::4, ::4].mean(axis=2)
        moving = self.was_moving
        self.was_moving = self.last_small is not None and self.last_small.shape == small.shape and \
            np.abs(small - self.last_small).mean() >= 2.5
        self.last_small = small
        if self.ref_mask is None or mask.shape != self.ref_mask.shape:
            return self._take(mask, small)
        self.min_px = self.min_px or max(25, int(mask.size * 0.002))
        n, n_ref = int(mask.sum()), int(self.ref_mask.sum())
        if n < self.min_px and n_ref < self.min_px:
            # no outlined letters, now or then: an empty strip (read now and then, in case), or letters without
            # an edge (then the old way: the whole strip must look alike)
            if np.abs(small - self.ref_small).mean() < 2.5:
                return False
            if self.quiet and moving:               # a busy picture behind: only now and then (a still
                self._skipped += 1                  # picture that changes = letters came: read at once)
                if self._skipped < self.check_every:
                    return False
            return self._take(mask, small)
        if n < self.min_px or n_ref < self.min_px:
            return self._take(mask, small)          # letters came or went
        union = int((mask | self.ref_mask).sum())
        diff = int((mask ^ self.ref_mask).sum()) / max(1, union)
        if diff < self.SAME:
            return False
        return self._take(mask, small)

    def _take(self, mask, small) -> bool:
        self.ref_mask, self.ref_small = mask, small
        self._skipped = 0
        return True


def same_text(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return na == nb
    return na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8


def build_cues(samples, step: float):
    """samples [(t, text, score)] in time order -> cues; the best-scored reading of each run is kept."""
    cues = []
    run = []
    for smp in samples + [(None, "", 0.0)]:
        t, text, score = smp
        if run and (t is None or not same_text(run[-1][1], text) or t - run[-1][0] > step * 1.6):
            if _norm(run[0][1]):
                # the best reading: the surest, and of two about as sure the longer (a lone "I" is not lost)
                best = max(run, key=lambda r: (round(r[2] * 20), len(r[1])))
                cues.append(Cue(run[0][0], run[-1][0] + step, best[1]))
            run = []
        if t is not None:
            run.append(smp)
    # drop noise: picture details read as one or two letters
    out = []
    for c in cues:
        n = _norm(c.text)
        if len(n) <= 1 or (len(n) <= 3 and c.duration <= step * 2.5):
            continue
        out.append(c)
    return out


def run(project: Project, control, force=False) -> str:
    s = settings()
    region = project.meta.get("ocr_region") or s["ocr_region"]
    if not region:
        raise RuntimeError("Draw the subtitle area on the video first (OCR area button)")
    if not available():
        raise RuntimeError("The OCR engine is not installed: run setup.bat again")
    fps = max(0.5, min(10.0, float(s["ocr_fps"])))
    params = {"region": [round(v, 4) for v in region], "fps": fps}
    if s["ocr_thorough"]:
        params["thorough"] = True
    if not force and project.is_done("ocr", SRC_SRT) and project.step("ocr").get("params") == params:
        project.ensure_published(SRC_SRT, project.meta.get("source_lang") or "")
        control.progress(1.0, "Hard subtitles already read")
        return project.src_srt
    info = ffmpeg.probe(project.video)
    if not info.get("has_video"):
        raise RuntimeError("This file has no picture to read subtitles from")
    project.meta["info"] = info
    W, H, dur = info["width"], info["height"], info["duration"]
    x, y, w, h = region_px(region, W, H)
    scale = min(1.0, MAX_W / w)
    ow, oh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2

    partial = project.path(PARTIAL)
    samples, start_at = [], 0.0
    if not force:
        try:
            with open(partial, encoding="utf-8") as f:
                old = json.load(f)
            if old.get("params") == params:
                samples = [tuple(x) for x in old["samples"]]
                start_at = old.get("done_to", 0.0)
                log.info("Resuming OCR at %.1f s", start_at)
        except (OSError, ValueError):
            pass

    def save_partial(done_to):
        tmp = partial + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"params": params, "samples": samples, "done_to": done_to}, f, ensure_ascii=False)
        os.replace(tmp, partial)

    with step_timer("ocr", project.video):
        project.mark("ocr", "running", params=params)
        try:
            _read(project, control, x, y, w, h, ow, oh, fps, dur, start_at, samples, save_partial)
        except Cancelled:
            project.mark("ocr", "stopped", params=params)
            raise
        except Exception as e:
            project.mark("ocr", "failed", params=params, error=str(e)[:300])
            raise
        cues = build_cues(samples, 1.0 / fps)
        if not cues:
            project.mark("ocr", "failed", params=params, error="no text")
            try:
                os.remove(partial)
            except OSError:
                pass
            raise RuntimeError("No text was found in the subtitle area. Check the area on the video "
                               "(Subtitles → Select the subtitle area), or use 'From speech'.")
        from .transcribe import tidy_timing
        cues = tidy_timing(cues)
        save_srt(cues, project.src_srt)
        lang = guess_lang([c.text for c in cues]) if cues else ""
        project.meta["source_lang"] = lang
        project.meta["ocr_region"] = list(region)
        project.mark("ocr", "done", params=params, file=SRC_SRT, cues=len(cues), lang=lang)
        project.mark("transcribe", "skipped")
        project.publish(SRC_SRT, lang)
        try:
            os.remove(partial)
        except OSError:
            pass
    control.progress(1.0, f"{len(cues)} lines read")
    return project.src_srt


def _read(project, control, x, y, w, h, ow, oh, fps, dur, start_at, samples, save_partial):
    exe = ffmpeg.find("ffmpeg")
    if not exe:
        raise ffmpeg.FFmpegMissing()
    vf = f"fps={fps},crop={w}:{h}:{x}:{y}" + (f",scale={ow}:{oh}" if (ow, oh) != (w, h) else "")
    def start(hw):
        cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error"]
        if hw:
            cmd += ["-hwaccel", "auto"]              # the graphics card decodes (much faster on long videos)
        if start_at > 0:
            cmd += ["-ss", f"{start_at:.3f}"]
        cmd += ["-i", project.video, "-an", "-sn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=0x08000000 if os.name == "nt" else 0)
    size = ow * oh * 3
    p = start(True)
    first = p.stdout.read(size)
    if len(first) < size:                            # the card's decoder refused this file: the usual way
        p.kill()
        p.wait()
        p = start(False)
        first = p.stdout.read(size)
    step = 1.0 / fps
    min_score = float(settings()["ocr_min_score"])
    changes = Changes(check_every=max(2, int(round(fps * 2))))
    thorough = bool(settings()["ocr_thorough"])      # read every frame whose picture changed (slow, the old way)
    prev_small = None
    prev_text, prev_score = "", 0.0
    run_reads, since = 0, 0              # readings of the line on screen, frames since the last one
    k, read_n, t0, last_save = 0, 0, time.time(), time.time()
    try:
        while True:
            buf = first if k == 0 else p.stdout.read(size)
            if len(buf) < size:
                break
            control.check()
            t = start_at + k * step
            k += 1
            img = np.frombuffer(buf, dtype=np.uint8).reshape(oh, ow, 3)
            if thorough:
                small = img[::4, ::4].mean(axis=2)
                new = prev_small is None or np.abs(small - prev_small).mean() >= 2.5
                prev_small = small
            else:
                new = k == 1 or changes.need_read(img)
                if k == 1:
                    changes.need_read(img)
            since += 1
            # the same line: read again while the reading is not sure (busy picture behind the letters), up to
            # 5 readings spread over the line; the best one is kept
            again = not new and prev_text and (run_reads < 3 or run_reads < 5 and prev_score < 0.97) and \
                since >= (1 if run_reads == 1 else 2)
            if new or again:
                text, score = read_text(img, min_score)
                changes.quiet = not text
                read_n += 1
                run_reads = 1 if new else run_reads + 1
                since = 0
                if again and (round(score * 20), len(text)) < (round(prev_score * 20), len(prev_text)):
                    text, score = prev_text, prev_score      # keep the better reading for this frame
            else:
                text, score = prev_text, prev_score          # the same letters (or none): the same text
            prev_text, prev_score = text, score
            samples.append((round(t, 3), text, round(score, 3)))
            if dur:
                speed = (t - start_at) / max(0.1, time.time() - t0)
                control.progress(min(1.0, t / dur), f"Reading hard subtitles {t / dur * 100:.0f}% · "
                                                    f"{speed:.1f}× real time · {read_n} frames read")
            if time.time() - last_save > 20:
                save_partial(t)
                last_save = time.time()
    except Cancelled:
        p.kill()
        save_partial(samples[-1][0] if samples else start_at)
        raise
    finally:
        try:
            p.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        p.wait()
    log.info("OCR: %d samples, %d read with OCR", k, read_n)
