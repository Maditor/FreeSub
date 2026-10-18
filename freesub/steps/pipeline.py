"""The recipe: which steps Export runs for a video, from the saved options.

Every step skips itself when its output is already there (project.json checkpoint), so running again after
a crash or Stop goes on where it ended, and changing only the export options re-runs only the export.
"""
import logging
import os

from ..config import settings
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, TR_SRT, Project
from . import audio, dub, export, ocr, separate, transcribe, translate

log = logging.getLogger(__name__)

# step id -> (label, function(project, control, force))
STEPS = {
    "audio": ("Extract audio", lambda p, c, f: audio.extract(p, c, force=f)),
    "separate": ("Separate voice from music", lambda p, c, f: separate.run(p, c, force=f)),
    "transcribe": ("Speech to subtitles", lambda p, c, f: transcribe.run(p, c, force=f)),
    "ocr": ("Read hard subtitles", lambda p, c, f: ocr.run(p, c, force=f)),
    "translate": ("Translate", lambda p, c, f: translate.run(p, c, force=f)),
    "dub": ("Narration", lambda p, c, f: dub.run(p, c, force=f)),
    "export": ("Export video", lambda p, c, f: export.run(p, c, force=f)),
}


def subtitle_steps(project: Project | None = None) -> list[str]:
    s = settings()
    if project is not None and project.is_done("import") and project.has_subs():
        return []                                  # the user gave us an .srt: start from translation
    if s["subs_source"] == "ocr":
        return ["separate", "ocr"] if s["whisper_separate"] and s["do_voice"] else ["ocr"]
    return ["audio", "separate", "transcribe"] if s["whisper_separate"] else ["audio", "transcribe"]


def speech_steps():
    return subtitle_steps()


# Steps that write the sound files the preview may have open (Windows cannot write a file that is mapped):
# before them, before_write(video) is called (the window sets it; it returns once the files are let go).
WRITES_SOUND = ("audio", "separate", "dub")
before_write = None


def recipe(project: Project | None = None, upto: str | None = None) -> list[str]:
    """All steps Export runs. upto='translate' stops after the subtitles (the 'Subtitles only' button),
    upto='live' is the preview's narration (the mic button), upto='run' everything but the export (Run)."""
    s = settings()
    if upto == "live":
        return _live_recipe(project, s)
    steps = subtitle_steps(project) if s["do_subtitles"] or (project and not project.has_subs()) else []
    if s["do_translate"]:
        steps.append("translate")
    if upto == "translate":
        return steps
    if s["do_voice"] or upto == "dub":
        if s["dub_separate"] and "separate" not in steps and not (project and project.is_done("separate")):
            steps.append("separate")               # voice / music apart: three volumes in the mix
        steps.append("dub")                        # the exported video then has the narration
    if upto in ("dub", "run"):
        return steps
    steps.append("export")
    return steps


def resubs_recipe(project) -> list[str]:
    """The ↻ button on the subtitle table: the subtitles (and translation) made again with the Steps settings."""
    s = settings()
    steps = subtitle_steps(project) if s["do_subtitles"] else []
    if s["do_translate"]:
        steps.append("translate")
    return steps


RESUBS_FORCE = {"transcribe", "ocr", "translate"}


def _live_recipe(project, s) -> list[str]:
    """The mic button: narration heard while it is being made, so it starts as soon as possible. Subtitles
    and a translation the video already has are used as they are (nothing before the narration is run
    again); only what is missing is made first. Voice / music separation comes after the narration, then
    a quick remix with the three volumes."""
    has_subs = project is not None and project.has_subs()
    steps = [] if has_subs else subtitle_steps(project)
    if s["do_translate"] and not (project is not None and project.has(TR_SRT)):
        steps.append("translate")
    steps.append("dub")
    if s["dub_separate"] and "separate" not in steps and not (project is not None and project.is_done("separate")):
        steps += ["separate", "dub"]
    return steps


BACK = ("dub", "export")        # the second half of a video's work (the first: subtitles, translation, separation)


def run_video(video: str, steps, control, on_step=None, force=False, upto=None, part=None) -> Project:
    """steps: list, or None = the saved recipe. on_step(step_id, state, text): start | done | failed | stopped.
    part: 'front' or 'back' = only that half of the recipe (two videos are worked on at once: see lanes())."""
    project = Project(video)
    from ..core.style import video_style
    own = dict(video_style(project), **(project.meta.get("overrides") or {}))
    with settings().overlay(own):    # this video's own look and settings, if any
        if part is not None:
            all_steps = steps if steps is not None else recipe(project, upto)
            steps = [s for s in all_steps if (s in BACK) == (part == "back")]
            if not steps:
                return project
        _run_steps(project, steps, control, on_step, force, upto)
    return project


def total_ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = MEM()
            m.len = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:  # noqa: BLE001
        return 0.0


def lanes(videos: int) -> int:
    """How many videos are worked on at the same time in a batch: 2 = while one video's narration and export
    run (CPU, video encoder), the next one's subtitles are made (graphics card). Never two graphics-card
    steps at once, so it costs no extra video memory. 'auto' picks 2 only on a PC with room for it."""
    want = str(settings()["parallel_videos"])
    if videos < 2 or want == "1":
        return 1
    if want == "2":
        return 2
    cores, ram = os.cpu_count() or 2, total_ram_gb()
    if ram and ram < 7.5 or cores < 4:
        return 1
    from ..core import gpu
    if gpu.cuda_count() > 0:                      # subtitles on the graphics card: the CPU is free for the rest
        return 2
    return 2 if cores >= 8 and ram >= 15 else 1   # all on the CPU: only a big one gains from it


_SUB_FILES = {"transcribe": SRC_SRT, "ocr": SRC_SRT, "translate": TR_SRT}


def _made_files(project, sid):
    """The subtitle file a step writes, with its time before the step (to see whether it wrote it)."""
    name = _SUB_FILES.get(sid)
    if not name:
        return None
    path = project.path(name)
    return name, (os.path.getmtime(path) if os.path.isfile(path) else None)


def _fit_new_lines(project, sid, made):
    """New subtitles from a step: break their lines to fit the box / the video once (as the 'Fit the lines to
    the box' button does), before the next step reads them. Lines a step did not write again are left as they
    are, so edits and earlier fits are kept."""
    if not made or not settings()["auto_fit_lines"]:
        return
    name, before = made
    path = project.path(name)
    if not os.path.isfile(path) or (before is not None and os.path.getmtime(path) == before):
        return
    if name == TR_SRT and project.meta.get("tr_edited"):
        return                                    # the translation is the user's: never touched
    try:
        from ..core.fit import fit_srt
        n = fit_srt(project, name)
    except Exception as e:  # noqa: BLE001        (fitting is a nicety: never stop the work for it)
        log.warning("Could not fit the lines to the box: %s", e)
        return
    if n:
        log.info("Lines fitted to the box: %d (%s)", n, name)
        lang = settings()["target_lang"] if name == TR_SRT else (project.meta.get("source_lang") or "orig")
        try:
            project.publish(name, lang)
        except OSError:
            pass


def _run_steps(project, steps, control, on_step, force, upto):
    if steps is None:
        steps = recipe(project, upto)
    forced = set()
    for sid in steps:
        label, fn = STEPS[sid]
        if on_step:
            on_step(sid, "start", label)
        try:
            control.check()
            if isinstance(force, (set, frozenset, list, tuple)):      # these steps are made again (once each:
                f = sid in force and sid not in forced                 # a later repeat only finishes up)
                forced.add(sid)
            else:
                f = force and sid == steps[-1]
            made = _made_files(project, sid)
            if sid in WRITES_SOUND and before_write is not None:
                before_write(project.video)            # the window lets go of this video's sound files first
            fn(project, control, f)
            _fit_new_lines(project, sid, made)
        except Cancelled:
            if on_step:
                on_step(sid, "stopped", "Stopped")
            raise
        except Exception as e:
            log.exception("%s failed for %s", label, project.video)
            if on_step:
                on_step(sid, "failed", str(e))
            raise
        if on_step:
            on_step(sid, "done", label)
