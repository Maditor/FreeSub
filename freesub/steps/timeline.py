"""The timeline: parts of videos put one after another, made into one video.

A clip is a part [in, out] (seconds) of one video of the list. Clips play one after the other; a time on the
timeline is the sum of the clips before it plus the time inside its clip. Each video keeps its own project
(subtitles, translation, narration, style): the timeline only says which parts are used and in what order, so
moving or trimming a clip never touches the subtitles; they simply follow their clip.

Kept in its project (core/edits.py). Export: every clip's video is made (subtitles, translation, narration:
whatever is switched on), each part is written at one size / frame rate, then they are joined. export_srt():
the same, as one .srt file (each video's lines moved to where its clip is).
"""
import json
import logging
import os
import shutil
import threading
import time
import uuid

from ..config import data_dir, settings
from ..core import ffmpeg
from ..core.jobs import Cancelled
from ..core.project import Project, short_name

log = logging.getLogger(__name__)
MIN_CLIP = 0.2                     # seconds: a clip is never shorter
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(data_dir(), "timeline.json")


class Timeline:
    def __init__(self, clips=None):
        self.clips = clips or []           # [{"id", "video", "in", "out"}]

    # ------------------------------------------------------------ file
    @classmethod
    def load(cls) -> "Timeline":
        try:
            with open(_path(), encoding="utf-8") as f:
                data = json.load(f)
            clips = [c for c in data.get("clips", []) if isinstance(c, dict) and c.get("video")
                     and float(c.get("out", 0)) > float(c.get("in", 0))]
            for c in clips:
                c["in"], c["out"] = float(c["in"]), float(c["out"])
                c.setdefault("id", uuid.uuid4().hex[:8])
            return cls(clips)
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        with _lock:
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"clips": self.clips}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _path())

    def copy(self) -> "Timeline":
        return Timeline([dict(c) for c in self.clips])

    # ------------------------------------------------------------ times
    @staticmethod
    def length(clip) -> float:
        return max(0.0, clip["out"] - clip["in"])

    def total(self) -> float:
        return sum(self.length(c) for c in self.clips)

    def start_of(self, index: int) -> float:
        return sum(self.length(c) for c in self.clips[:index])

    def locate(self, t: float):
        """(clip index, time in that clip's video) for a timeline time; (-1, 0) when empty."""
        if not self.clips:
            return -1, 0.0
        t = max(0.0, t)
        acc = 0.0
        for i, c in enumerate(self.clips):
            n = self.length(c)
            if t < acc + n or i == len(self.clips) - 1:
                return i, c["in"] + min(max(0.0, t - acc), n)
            acc += n
        return len(self.clips) - 1, self.clips[-1]["out"]

    def to_timeline(self, index: int, video_t: float) -> float:
        c = self.clips[index]
        return self.start_of(index) + min(max(0.0, video_t - c["in"]), self.length(c))

    # ------------------------------------------------------------ edits
    def insert(self, index: int, video: str, duration: float, a: float = 0.0, b: float | None = None):
        b = duration if b is None else b
        if b - a < MIN_CLIP:
            return None
        clip = {"id": uuid.uuid4().hex[:8], "video": os.path.abspath(video), "in": round(a, 3), "out": round(b, 3)}
        self.clips.insert(max(0, min(index, len(self.clips))), clip)
        return clip

    def move(self, index: int, to: int):
        if 0 <= index < len(self.clips):
            c = self.clips.pop(index)
            self.clips.insert(max(0, min(to, len(self.clips))), c)

    def trim(self, index: int, a: float | None, b: float | None, duration: float):
        c = self.clips[index]
        if a is not None:
            c["in"] = round(min(max(0.0, a), c["out"] - MIN_CLIP), 3)
        if b is not None:
            c["out"] = round(max(min(duration or b, b), c["in"] + MIN_CLIP), 3)

    def split(self, t: float) -> bool:
        i, vt = self.locate(t)
        if i < 0:
            return False
        c = self.clips[i]
        if vt - c["in"] < MIN_CLIP or c["out"] - vt < MIN_CLIP:
            return False
        second = dict(c, id=uuid.uuid4().hex[:8], **{"in": round(vt, 3)})
        c["out"] = round(vt, 3)
        self.clips.insert(i + 1, second)
        return True

    def remove(self, index: int):
        if 0 <= index < len(self.clips):
            self.clips.pop(index)

    @classmethod
    def whole(cls, videos, duration_of) -> "Timeline":
        """Every video from start to end, in this order (a project whose timeline is empty)."""
        tl = cls()
        for v in videos:
            tl.insert(len(tl.clips), v, duration_of(v) or 0.0)
        return tl

    def videos(self) -> list[str]:
        out = []
        for c in self.clips:
            if c["video"] not in out:
                out.append(c["video"])
        return out


# ---------------------------------------------------------------------------------------------- export
def _frame(clips) -> tuple[int, int, float]:
    """The size and frame rate of the joined video: the first clip's (even numbers), at most the export size."""
    info = ffmpeg.probe(clips[0]["video"])
    w, h = int(info.get("width") or 1280), int(info.get("height") or 720)
    fps = float(info.get("fps") or 30.0) or 30.0
    res = settings()["export_resolution"]
    if res != "source" and int(res) < h:
        w, h = int(round(w * int(res) / h)), int(res)
    return w - w % 2, h - h % 2, min(60.0, max(10.0, round(fps, 3)))


def out_path(clips, name: str = "", ext: str = "") -> str:
    """The joined file: in the first video's output folder, named after the project (and the time)."""
    from ..core.project import _safe
    first = Project(clips[0]["video"])
    base = _safe(name)[:80] if name else f"{short_name(first.video, 4)} timeline"
    return os.path.join(first.out_dir, f"{base} {time.strftime('%Y-%m-%d %H%M')}.{ext or settings()['export_container']}")


def _make_videos(clips, control, upto, on_video=None):
    from . import pipeline
    videos = []
    for c in clips:
        if c["video"] not in videos:
            videos.append(c["video"])
    missing = [v for v in videos if not os.path.isfile(v)]
    if missing:
        raise RuntimeError("Not found: " + ", ".join(os.path.basename(v) for v in missing))
    for k, v in enumerate(videos):
        control.check()
        control.status(f"Project: video {k + 1}/{len(videos)} · {os.path.basename(v)}")
        pipeline.run_video(v, None, control, upto=upto)
        if on_video:
            on_video(v)
    return videos


def export_srt(tl: Timeline, control, name: str = "", on_video=None) -> list[str]:
    """The whole project as one .srt (and one for the translation): each video's lines where its clip is."""
    from ..core.project import SRC_SRT, TR_SRT
    from ..core.subtitle import Cue, load_srt, save_srt
    clips = [dict(c) for c in tl.clips]
    if not clips:
        raise RuntimeError("The project has no videos")
    if not ffmpeg.available():
        raise ffmpeg.FFmpegMissing()
    _make_videos(clips, control, "translate", on_video)
    out = {SRC_SRT: [], TR_SRT: []}
    langs = {SRC_SRT: "", TR_SRT: settings()["target_lang"]}
    base = 0.0
    for c in clips:
        p = Project(c["video"])
        langs[SRC_SRT] = langs[SRC_SRT] or p.meta.get("source_lang") or ""
        for kind in (SRC_SRT, TR_SRT):
            if not p.has(kind):
                continue
            for cue in load_srt(p.path(kind)):
                a, b = max(cue.start, c["in"]), min(cue.end, c["out"])
                if b - a < 0.05:
                    continue                                  # outside the part that is used
                out[kind].append(Cue(round(base + a - c["in"], 3), round(base + b - c["in"], 3), cue.text))
        base += Timeline.length(c)
    written = []
    for kind in (SRC_SRT, TR_SRT):
        if out[kind]:
            path = out_path(clips, name, f"{langs[kind] or 'orig'}.srt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            save_srt(out[kind], path)
            written.append(path)
    if not written:
        raise RuntimeError("No subtitles were made: switch on Subtitles in Steps")
    log.info("Project subtitles exported: %s", ", ".join(written))
    control.progress(1.0, "Exported")
    return written


def export(tl: Timeline, control, on_video=None, name: str = "") -> str:
    """Make every clip's video (the steps switched on, no export), write each part, join them."""
    from . import export as ex
    clips = [dict(c) for c in tl.clips]
    if not clips:
        raise RuntimeError("The timeline is empty: drag videos from the list onto it")
    if not ffmpeg.available():
        raise ffmpeg.FFmpegMissing()
    _make_videos(clips, control, "run", on_video)        # 1) subtitles, translation, narration of each video
    W, H, fps = _frame(clips)
    final = out_path(clips, name)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    work = os.path.join(data_dir(), "timeline_parts")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    container = settings()["export_container"]
    parts = []
    total = 0.0                                          # the joined length (each part at its speed)
    try:
        for i, c in enumerate(clips):                    # 2) each part, the same size and frame rate
            control.check()
            control.status(f"Timeline: part {i + 1}/{len(clips)} · {os.path.basename(c['video'])}")
            p = Project(c["video"])
            from ..core.style import video_style
            out = os.path.join(work, f"part{i:03d}.{container}")
            with settings().overlay(dict(video_style(p), **(p.meta.get("overrides") or {}))):
                ex.run(p, control, segment={"start": c["in"], "end": c["out"], "out": out,
                                            "width": W, "height": H, "fps": fps})
                total += Timeline.length(c) / min(2.0, max(0.25, float(settings()["video_speed"] or 1.0)))
            parts.append(out)
        control.check()
        control.status("Timeline: joining the parts")
        _join(parts, final, work, control, total)
    except Cancelled:
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("Timeline exported: %s (%d parts)", final, len(parts))
    control.progress(1.0, "Exported")
    return final


def _join(parts, final, work, control, dur):
    listing = os.path.join(work, "parts.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
    tmp = final + ".part" + os.path.splitext(final)[1]
    base = ["-f", "concat", "-safe", "0", "-i", listing]
    extra = ["-movflags", "+faststart"] if final.lower().endswith(".mp4") else []
    try:
        ffmpeg.run(base + ["-c", "copy"] + extra + [tmp], duration=dur, control=control, label="Joining")
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001      (parts that do not join as they are: made once more)
        log.warning("Joining as they are failed (%s): encoding once more", e)
        from .export import _video_args, pick_encoder
        ffmpeg.run(base + _video_args(pick_encoder(), settings()["export_quality"]) +
                   ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"] + extra + [tmp],
                   duration=dur, control=control, label="Joining")
    os.replace(tmp, final)
