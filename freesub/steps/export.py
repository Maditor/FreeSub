"""Step: the final video with ffmpeg.

* Video encoder: NVIDIA NVENC, Intel Quick Sync, AMD AMF or CPU x264 (auto = the first that really works
  here, tested once with a tiny encode and remembered).
* Quality: Best / High / Medium / Small; resolution: source or smaller (never upscaled).
* Subtitles: burned in (hard), as a separate track (soft) or none; translation, original or both.
* Hard subtitles read with OCR: the area they were read from becomes a dialogue box (blurred + tinted)
  while a line is shown, and the translation is written inside it.
* Sound: the narration mix when the Narration step ran, else the original sound.
"""
import json
import logging
import os
import re
import subprocess

from ..config import data_dir, settings
from ..core import ffmpeg
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, TR_SRT, Project
from ..core.style import box_region, sub_style
from ..core.subtitle import load_srt, to_ass
from ..log import step_timer
from .ocr import region_px

log = logging.getLogger(__name__)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ENCODERS = [("nvenc", "h264_nvenc", "NVIDIA NVENC"), ("qsv", "h264_qsv", "Intel Quick Sync"),
            ("amf", "h264_amf", "AMD AMF"), ("x264", "libx264", "CPU (x264)")]
QUALITY = {  # x264 crf, nvenc cq, qsv global_quality, amf qp, x264 preset
    "best": (18, 19, 20, 20, "slow"),
    "high": (21, 23, 23, 23, "medium"),
    "medium": (24, 27, 26, 26, "fast"),
    "small": (28, 31, 30, 30, "veryfast"),
}
QUALITY_TEXT = {"best": "Best (big file)", "high": "High", "medium": "Medium", "small": "Small file"}
RESOLUTIONS = [("source", "Same as the video"), ("2160", "4K (2160p)"), ("1440", "1440p"), ("1080", "1080p"),
               ("720", "720p"), ("480", "480p")]


# ------------------------------------------------------------------ encoders
def _cache_path():
    return os.path.join(data_dir(), "encoders.json")


def detect_encoders(refresh=False) -> dict:
    """{id: True/False} for every encoder, by encoding one tiny clip with each (cached per ffmpeg)."""
    exe = ffmpeg.find("ffmpeg")
    if not exe:
        return {}
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
        if not refresh and cache.get("ffmpeg") == exe and cache.get("mtime") == os.path.getmtime(exe):
            return cache["ok"]
    except (OSError, ValueError, KeyError):
        pass
    ok = {}
    for eid, codec, _name in ENCODERS:
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=25",
               "-frames:v", "10", "-c:v", codec, "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NO_WINDOW)
            ok[eid] = r.returncode == 0
        except Exception:  # noqa: BLE001
            ok[eid] = False
    log.info("Video encoders: %s", ok)
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"ffmpeg": exe, "mtime": os.path.getmtime(exe), "ok": ok}, f)
    except OSError:
        pass
    return ok


def pick_encoder() -> str:
    want = settings()["export_encoder"]
    ok = detect_encoders()
    if want != "auto" and ok.get(want):
        return want
    for eid, _c, _n in ENCODERS:
        if ok.get(eid):
            return eid
    return "x264"


def _video_args(enc: str, quality: str) -> list:
    crf, cq, gq, qp, preset = QUALITY.get(quality, QUALITY["high"])
    if enc == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    if enc == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(gq)]
    if enc == "amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp)]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


# ------------------------------------------------------------------ helpers
def _intervals(cues, gap=0.4):
    out = []
    for c in cues:
        if not c.text.strip():
            continue
        if out and c.start - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], c.end)
        else:
            out.append([c.start, c.end])
    return out


def _filter_arg(script_path: str) -> list:
    """Long filter graphs go through a file (Windows command lines are limited to 32k characters)."""
    m = re.search(r"version (?:n)?(\d+)", ffmpeg.version() or "")
    major = int(m.group(1)) if m else 6
    return ["-/filter_complex", script_path] if major >= 7 else ["-filter_complex_script", script_path]


def _keep_shift(cues, a, b):
    """Cues of the kept part [a, b], moved to start at 0 (lines cut in half are clipped)."""
    from ..core.subtitle import Cue
    out = []
    for c in cues or []:
        if c.end <= a or c.start >= b:
            continue
        out.append(Cue(max(c.start, a) - a, min(c.end, b) - a, c.text))
    return out


def _remove_map(cues, a, b):
    """Cues after removing [a, b]: later lines move back by (b - a); lines inside the removed part go."""
    from ..core.subtitle import Cue
    gap = b - a
    out = []
    for c in cues or []:
        if c.end <= a:
            out.append(Cue(c.start, c.end, c.text))
        elif c.start >= b:
            out.append(Cue(c.start - gap, c.end - gap, c.text))
        elif c.start < a:
            out.append(Cue(c.start, a, c.text))
        elif c.end > b:
            out.append(Cue(a, c.end - gap, c.text))
    return out


def _subs_for(project: Project, which: str):
    """(translation cues or None, original cues or None)."""
    tr = load_srt(project.tr_srt) if project.has(TR_SRT) else None
    src = load_srt(project.src_srt) if project.has(SRC_SRT) else None
    if which == "original":
        return None, src
    if which == "both":
        return tr, src
    return tr or src, None


# ------------------------------------------------------------------ the step
def run(project: Project, control, force=False) -> str:
    s = settings()
    info = ffmpeg.probe(project.video)
    if not info.get("has_video"):
        raise RuntimeError("This file has no picture: there is no video to export")
    W, H, dur = info["width"], info["height"], info["duration"]
    container = s["export_container"]
    out = project.out_video(container)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part." + container
    project.ensure()

    enc = pick_encoder()
    quality = s["export_quality"]
    mode = "hard"                       # subtitles are always burned in (older settings may say soft / none)
    main, extra = _subs_for(project, s["export_which"])
    synced = False
    if s["do_voice"] and s["dub_sync_subs"] and s["export_which"] != "original":
        from .dub import follow_narration, narrated_times
        times = narrated_times(project)          # the subtitles follow the narration (moved / joined lines)
        if times:
            if extra and main and len(extra) == len(main):        # the original under it, line for line
                extra = follow_narration(extra, times)
            main = follow_narration(main, times)
            synced = True
    if not (main or extra):
        raise RuntimeError("There are no subtitles for this video yet, so the video would come out without any. "
                           "Switch on Subtitles (and Translate) in Steps.")
    box_area = box_region(project) if mode == "hard" else None      # subtitles inside the drawn box

    # sound: the narration mix when Narration is on, else the original
    audio_file = project.path("mix.wav") if s["do_voice"] else None
    if audio_file and not project.has("mix.wav"):
        raise RuntimeError("Narration is on but this video has no narration yet. Run Export again, or switch "
                           "Narration off to keep the original sound.")
    if s["export_which"] != "original" and not project.has(TR_SRT) and s["do_translate"]:
        log.warning("No translation for this video: the original subtitles are used")

    # cut: keep only [a, b] (fast: the inputs are cut while reading) or remove [a, b] (trim + join)
    cut = project.cut
    seek, remove = [], None
    if cut:
        a, b = max(0.0, cut["start"]), min(dur or cut["end"], cut["end"])
        if cut["mode"] == "keep":
            seek = ["-ss", f"{a:.3f}", "-to", f"{b:.3f}"]
            main, extra = _keep_shift(main, a, b), _keep_shift(extra, a, b)
            dur = b - a
        else:
            remove = (a, b)
            dur = max(0.1, dur - (b - a))

    inputs = seek + ["-i", project.video]
    if audio_file:
        inputs += seek + ["-i", audio_file]
    graph, vlabel = [], "0:v"
    work = project.folder

    # 1) dialogue box over the area the hard subtitles were read from
    if box_area and main:
        x, y, w, h = region_px(box_area, W, H)
        spans = _intervals(main)
        if spans:
            # boxblur refuses a radius over half the box (a quarter for colour): keep it inside
            blur = max(0, int(s["ocr_box_blur"]))
            luma = max(1, min(blur, min(w, h) // 2 - 1))
            chroma = max(1, min(blur, min(w, h) // 4 - 1))
            alpha = max(0.0, min(1.0, float(s["ocr_box_opacity"]) / 100))
            enable = "" if s["sub_box_always"] else \
                ":enable='" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans) + "'"
            blur_f = f"boxblur={luma}:2:{chroma}:2," if blur else ""
            graph.append(f"[0:v]split[base][cut];[cut]crop={w}:{h}:{x}:{y},{blur_f}"
                         f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{alpha:.2f}:t=fill[box];"
                         f"[base][box]overlay={x}:{y}{enable}[boxed]")
            vlabel = "boxed"

    # 2) burned-in subtitles (ASS made here: font, size, colours, position)
    soft_files = []
    if mode == "hard" and (main or extra):
        st = sub_style(H)
        size, outline = st["size"], st["outline"]
        tracks = []
        if main:
            tr = {"name": "Main", "cues": main, "font": st["font"], "bold": st["bold"], "size": size,
                  "color": st["color"], "outline_color": st["outline_color"], "outline": outline,
                  "align": 2, "margin_v": st["margin_v"]}
            if box_area:
                x, y, w, h = region_px(box_area, W, H)
                lines = max(1, max(c.text.count("\n") + 1 for c in main))
                tr.update(align=5, pos=(x + w / 2, y + h / 2), margin_l=x + 8, margin_r=W - x - w + 8,
                          size=max(10, min(size, int(h / (lines * 1.25)))))
            tracks.append(tr)
        if extra:
            tracks.append({"name": "Original", "cues": extra, "font": st["font"], "bold": st["bold"],
                           "size": int(size * 0.75), "color": "#e6e6e6", "outline_color": st["outline_color"],
                           "outline": outline * 0.8, "align": 8 if box_area else 2,
                           "margin_v": st["margin_v"] + (0 if box_area else int(size * 2.6))})
        ass = os.path.join(work, "burn.ass")
        fonts, ass_font = _fonts_dir(work, st["font"])
        for tr in tracks:
            tr["font"] = ass_font                 # the name libass finds inside the font file
        with open(ass, "w", encoding="utf-8") as f:
            f.write(to_ass(W, H, tracks))
        opt = ":fontsdir=fonts" if fonts else ""
        graph.append(f"[{vlabel}]subtitles=burn.ass{opt}[subbed]" if vlabel != "0:v"
                     else f"[0:v]subtitles=burn.ass{opt}[subbed]")
        vlabel = "subbed"
    elif mode == "soft":
        for cues, lang_key in ((main, "main"), (extra, "orig")):
            if cues:
                p = os.path.join(work, f"soft_{lang_key}.srt")
                from ..core.subtitle import save_srt
                save_srt(_remove_map(cues, *remove) if remove else cues, p)
                lang = s["target_lang"] if lang_key == "main" and project.has(TR_SRT) and s["export_which"] != "original" \
                    else (project.meta.get("source_lang") or "und")
                soft_files.append((p, lang))

    # 3) size
    res = s["export_resolution"]
    if res != "source" and int(res) < H:
        src_label = f"[{vlabel}]" if vlabel != "0:v" else "[0:v]"
        graph.append(f"{src_label}scale=-2:{int(res)}:flags=lanczos[sized]")
        vlabel = "sized"

    # 4) remove a part: cut the picture and the sound in two and join what is left
    stream = project.meta.get("audio_stream")
    alabel = "1:a" if audio_file else (f"0:{stream}" if stream is not None else "0:a:0") \
        if info.get("audio_streams") else None
    if remove:
        a, b = remove
        parts = [(0.0, a)] if a > 0.05 else []
        if not info.get("duration") or b < info["duration"] - 0.05:
            parts.append((b, None))
        n = len(parts)
        v_in = f"[{vlabel}]" if vlabel != "0:v" else "[0:v]"
        chains, outs = [], []
        vsplit = [f"[vc{i}]" for i in range(n)]
        chains.append(f"{v_in}split={n}{''.join(vsplit)}" if n > 1 else f"{v_in}null[vc0]")
        if alabel:
            asplit = [f"[ac{i}]" for i in range(n)]
            chains.append(f"[{alabel}]asplit={n}{''.join(asplit)}" if n > 1 else f"[{alabel}]anull[ac0]")
        for i, (p0, p1) in enumerate(parts):
            rng = f"start={p0:.3f}" + (f":end={p1:.3f}" if p1 is not None else "")
            chains.append(f"[vc{i}]trim={rng},setpts=PTS-STARTPTS[vt{i}]")
            outs.append(f"[vt{i}]")
            if alabel:
                chains.append(f"[ac{i}]atrim={rng},asetpts=PTS-STARTPTS[at{i}]")
                outs.append(f"[at{i}]")
        chains.append(f"{''.join(outs)}concat=n={n}:v=1:a={1 if alabel else 0}[vcut]" + ("[acut]" if alabel else ""))
        graph.append(";".join(chains))
        vlabel = "vcut"
        alabel = "[acut]" if alabel else None

    args = inputs[:]
    for p, _lang in soft_files:
        args += ["-i", p]
    if graph:
        script = os.path.join(work, "export_filter.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(";".join(graph))
        args += _filter_arg(script)
        args += ["-map", f"[{vlabel}]"]
    else:
        args += ["-map", "0:v:0"]
    if alabel:
        args += ["-map", alabel, "-c:a", "aac", "-b:a", "192k"]
    first_sub = 1 + (1 if audio_file else 0)
    for k, (_p, lang) in enumerate(soft_files):
        args += ["-map", f"{first_sub + k}:0", f"-metadata:s:s:{k}", f"language={_iso3(lang)}"]
    if soft_files:
        args += ["-c:s", "mov_text" if container == "mp4" else "srt"]
    args += _video_args(enc, quality) + ["-pix_fmt", "yuv420p"]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    args += [tmp]

    params = {"enc": enc, "q": quality, "res": res, "mode": mode, "which": s["export_which"],
              "dub": bool(audio_file), "box": box_area, "always": bool(s["sub_box_always"]), "cut": cut,
              "sync": synced}
    with step_timer("export", project.video):
        project.mark("export", "running", params=params)
        try:
            _run_in(work, args, dur, control, f"Exporting ({dict((e, n) for e, _c, n in ENCODERS)[enc]})")
        except Cancelled:
            _rm(tmp)
            project.mark("export", "stopped", params=params)
            raise
        except Exception as e:
            _rm(tmp)
            if enc != "x264" and "ffmpeg failed" in str(e):
                log.warning("Export with %s failed (%s): trying the CPU encoder", enc, e)
                args = [a for a in args]
                i = args.index("-c:v")
                j = args.index("-pix_fmt")
                args[i:j] = _video_args("x264", quality)
                _run_in(work, args, dur, control, "Exporting (CPU)")
            else:
                project.mark("export", "failed", params=params, error=str(e)[:300])
                raise
        os.replace(tmp, out)
        project.meta.setdefault("outputs", {})["video"] = out
        project.mark("export", "done", params=params, file=out)
    control.progress(1.0, "Exported")
    return out


def _fonts_dir(work, family):
    """Copy the chosen font's files to <work>/fonts for ffmpeg. Returns (found, name to use in the .ass)."""
    import shutil
    from ..core.fonts import find
    d = os.path.join(work, "fonts")
    shutil.rmtree(d, ignore_errors=True)
    files, name = find(family)
    if not files:
        log.warning("Font %r was not found among the installed fonts: ffmpeg picks a similar one", family)
        return False, family
    os.makedirs(d, exist_ok=True)
    for f in files:
        try:
            shutil.copyfile(f, os.path.join(d, os.path.basename(f)))
        except OSError as e:
            log.warning("Could not copy font %s: %s", f, e)
    log.info("Font %s (as %r): %s", family, name, ", ".join(os.path.basename(f) for f in files))
    return True, name


def _run_in(cwd, args, dur, control, label):
    old = os.getcwd()
    os.chdir(cwd)                     # burn.ass is found by its plain name (no path escaping needed)
    try:
        ffmpeg.run(args, duration=dur, control=control, label=label)
    finally:
        os.chdir(old)


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


_ISO3 = {"vi": "vie", "en": "eng", "ja": "jpn", "ko": "kor", "zh": "chi", "th": "tha", "id": "ind", "fr": "fre",
         "es": "spa", "de": "ger", "ru": "rus", "pt": "por", "it": "ita"}


def _iso3(lang: str) -> str:
    return _ISO3.get(lang, "und")
