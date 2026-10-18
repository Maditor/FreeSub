"""Step: subtitles -> narration (voice-over) audio mix.wav, ready for the export.

For every line:
  * foreign names / words are respelled for the Vietnamese voice (tts/pronounce.py),
  * a voice is picked from the guessed speaker gender (Edge: female / male voice),
  * the clip is made (Edge online, Piper / Kokoro offline) and cached in dub/ (a rerun reuses it),
  * readings are joined, moved a little and only then read faster (timing.py), never faster than
    dub_max_speed.
The clips are laid on a voice track (dub.wav) and mixed with the video's own sound, which is turned down to
dub_orig_volume while a line is read and comes back between lines (like TV voice-over).
"""
import hashlib
import time
import threading
import json
import logging
import os
import sys
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..config import settings
from ..core import ffmpeg
from ..core.jobs import Cancelled
from ..core.project import TR_SRT, Project
from ..core.subtitle import load_srt
from ..log import step_timer
from ..tts import voices
from ..tts.pronounce import pronouncer

log = logging.getLogger(__name__)
RATE = 44100
DUB = "dub.wav"
MIX = "mix.wav"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _genders(project, cues):
    try:
        with open(project.path("subs.src.json"), encoding="utf-8") as f:
            side = json.load(f)
        by_start = {round(x["start"], 1): x.get("gender") for x in side}
        return [by_start.get(round(c.start, 1)) for c in cues]
    except (OSError, ValueError):
        return [None] * len(cues)


def _decode(path, tempo=1.0) -> np.ndarray:
    """Any audio file -> mono float32 at RATE (optionally sped up with atempo)."""
    exe = ffmpeg.find("ffmpeg")
    af = []
    t = tempo
    while t > 2.0:                       # atempo takes 0.5..2 per stage
        af.append("atempo=2.0")
        t /= 2.0
    if abs(t - 1.0) > 0.01:
        af.append(f"atempo={t:.4f}")
    cmd = [exe, "-v", "error", "-i", path] + (["-af", ",".join(af)] if af else []) + \
          ["-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(f"Cannot read voice clip: {out.stderr.decode(errors='replace')[:200]}")
    return np.frombuffer(out.stdout, dtype=np.float32)


def _trim_silence(a: np.ndarray, thr=0.01) -> np.ndarray:
    idx = np.where(np.abs(a) > thr)[0]
    if not len(idx):
        return a[:0]
    pad = int(0.03 * RATE)
    return a[max(0, idx[0] - pad):min(len(a), idx[-1] + pad)]


class Voice:
    def __init__(self, folder):
        s = settings()
        self.engine = s["tts_engine"]
        self.folder = folder
        self.respell = bool(s["tts_respell"]) and s["target_lang"] == "vi"
        self.speed = int(s["tts_speed"])
        if self.engine == "piper":
            if not voices.piper_installed():
                raise RuntimeError("Piper is not installed: press Download Piper in Narration, or choose the Edge voice"
                                   if getattr(sys, "frozen", False) else
                                   "Piper is not installed: run setup.bat again, or choose the Edge voice")
            if not voices.piper_ready("vi"):
                raise RuntimeError("Download the Vietnamese Piper voice first (Voice section)")
        elif self.engine == "kokoro":
            from ..tts import kokoro
            if not kokoro.g2p_installed():
                raise RuntimeError("The Kokoro text reader (vig2p) is not installed: run setup.bat again")
            if not kokoro.ready():
                raise RuntimeError("Download the Kokoro voices first (Narration section)")
        elif not voices.edge_installed():
            raise RuntimeError("edge-tts is not installed: run setup.bat again")

    def voice_for(self, gender):
        s = settings()
        if s["voice_mode"] != "split":
            gender = "f"                         # one voice for every line: the voice chosen first
        if self.engine == "piper":
            return "piper-vi"
        if self.engine == "kokoro":
            return "kokoro-" + (s["tts_kokoro_male"] if gender == "m" else s["tts_kokoro_female"])
        return s["tts_edge_male"] if gender == "m" else s["tts_edge_female"]

    def clip(self, text, gender, speed) -> str:
        """Make (or reuse) the clip for one line; returns its file."""
        if settings()["target_lang"] == "vi":
            from ..tts.abbrev_vi import expand
            from ..tts.numbers_vi import say
            text = say(expand(text))              # "v.v." -> "vân vân", 3.000 -> "ba nghìn" (subtitles keep them)
        spoken = pronouncer().respell_text(text)[0] if self.respell else text
        spoken = " ".join(spoken.split())
        voice = self.voice_for(gender)
        key = hashlib.sha1(f"{voice}|{speed}|{spoken}".encode("utf-8")).hexdigest()[:16]
        ext = "mp3" if self.engine == "edge" else "wav"
        path = os.path.join(self.folder, f"{key}.{ext}")
        with _key_lock(key):                      # the same line twice: made once, the other thread waits
            if os.path.isfile(path) and os.path.getsize(path) > 100:
                return path
            return self._make(spoken, voice, speed, path, ext)

    def _make(self, spoken, voice, speed, path, ext) -> str:
        tmp = f"{path}.{threading.get_ident()}.part.{ext}"
        if self.engine == "piper":
            voices.piper_synth(spoken, "vi", tmp, speed)
        elif self.engine == "kokoro":
            from ..tts import kokoro
            kokoro.synth(spoken, voice[len("kokoro-"):], tmp, speed)
        else:
            last = None
            for _ in range(3):                    # the free online service fails now and then
                try:
                    voices.edge_synth(spoken, voice, tmp, speed)
                    last = None
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
            if last:
                raise last
        _replace(tmp, path)
        return path


_locks: dict = {}
_locks_guard = threading.Lock()


def _key_lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _replace(tmp, path):
    """os.replace, waiting a little when Windows still holds the file (antivirus, indexer)."""
    for k in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 19:
                raise
            time.sleep(0.15)


def voices_sig(project) -> str:
    """A short signature of the women's / men's voice of every line (a changed mark -> narration again)."""
    from .genders import read_side
    g = "".join((x.get("gender") or "-")[0] for x in read_side(project))
    return hashlib.sha1(g.encode()).hexdigest()[:12]


def track_params(src, n, project=None) -> dict:
    """What the voice track depends on: when these change, the narration is made again."""
    s = settings()
    return {"engine": s["tts_engine"], "f": s["tts_edge_female"], "m": s["tts_edge_male"],
            "kf": s["tts_kokoro_female"], "km": s["tts_kokoro_male"],
            "speed": s["tts_speed"], "respell": s["tts_respell"], "max": s["dub_max_speed"],
            "join": bool(s["dub_join"]), "timing": 2, "one": s["voice_mode"] != "split",
            "voices": voices_sig(project) if project is not None and s["voice_mode"] == "split" else "",
            "srt": os.path.getmtime(src), "n": n}


def track_fresh(project) -> bool:
    """dub.wav exists and was made from the current subtitles and voice settings."""
    src = project.tr_srt if project.has(TR_SRT) else project.src_srt
    if not (project.has(DUB) and os.path.isfile(src)):
        return False
    n = len([c for c in load_srt(src) if c.text.strip()])
    return project.step("dub").get("params") == track_params(src, n, project)


def run(project: Project, control, force=False) -> str:
    s = settings()
    src = project.tr_srt if project.has(TR_SRT) else project.src_srt
    if not os.path.isfile(src):
        raise RuntimeError("No subtitles to narrate yet")
    cues = [c for c in load_srt(src) if c.text.strip()]
    params = track_params(src, len(cues), project)
    mix = mix_params(project)
    old = project.step("dub")
    same_track = not force and old.get("params") == params and project.has(DUB)
    if same_track and project.is_done("dub", MIX) and old.get("mix") == mix:
        control.progress(1.0, "Narration already made")
        return project.path(MIX)
    if same_track:                     # only the volumes changed: mix again, the voice track is kept
        info = project.meta.get("info") or ffmpeg.probe(project.video)
        duration = max(info.get("duration") or 0, cues[-1].end if cues else 0)
        with step_timer("dub", project.video):
            control.progress(None, "Mixing the narration again with the new volumes…")
            _mix(project, control, project.path(DUB), cues, duration)
            project.mark("dub", "done", params=params, mix=mix, file=MIX, lines=len(cues))
        control.progress(1.0, "Narration mixed")
        return project.path(MIX)
    project.ensure()
    folder = project.path("dub")
    os.makedirs(folder, exist_ok=True)
    voice = Voice(folder)
    genders = [None] * len(cues) if s["voice_mode"] != "split" else _genders(project, cues)
    info = project.meta.get("info") or ffmpeg.probe(project.video)
    duration = max(info.get("duration") or 0, cues[-1].end if cues else 0)

    with step_timer("dub", project.video):
        project.mark("dub", "running", params=params)
        try:
            track = _voice_track(project, control, voice, cues, genders, duration, os.path.getmtime(src))
            control.progress(None, "Mixing the voice with the background…")
            _mix(project, control, track, cues, duration)
            mix = mix_params(project)
        except Cancelled:
            project.mark("dub", "stopped", params=params)
            raise
        except Exception as e:
            project.mark("dub", "failed", params=params, error=str(e)[:300])
            raise
        project.mark("dub", "done", params=params, mix=mix, file=MIX, lines=len(cues))
    control.progress(1.0, f"Narration made for {len(cues)} lines")
    return project.path(MIX)


LIVE = "dub_live"      # each finished line as its own small file, so the preview can play the narration live


def live_name(i, start, slot0, slot1) -> str:
    """<line>_<start ms>_<slot from ms>_<slot to ms>.wav: the preview reads the times from the name."""
    return f"{i:05d}_{int(start * 1000)}_{int(slot0 * 1000)}_{int(slot1 * 1000)}.wav"


def parse_live_name(name):
    """(line, start, slot from, slot to) in seconds, or None."""
    try:
        i, a, s0, s1 = name[:-4].split("_")
        return int(i), int(a) / 1000, int(s0) / 1000, int(s1) / 1000
    except ValueError:
        return None


def clear_live(project):
    import shutil
    shutil.rmtree(project.path(LIVE), ignore_errors=True)


def _write_live(folder, name, a):
    path = os.path.join(folder, name)
    tmp = path + ".part"
    try:
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        _replace(tmp, path)
    except OSError as e:                          # only the live preview misses this line
        log.debug("Live line not written: %s", e)


PLACED = "dub.json"     # where each reading was put: [[start, end], ...] (ducking, preview)


def placed_spans(project, cues=None):
    """The times the narration speaks (seconds). Old projects without dub.json: the subtitle lines."""
    try:
        with open(project.path(PLACED), encoding="utf-8") as f:
            return [tuple(x) for x in json.load(f)["spans"]]
    except (OSError, ValueError, KeyError):
        return [(c.start, c.end) for c in (cues or [])]


HOLD = 0.4         # a subtitle stays this long after its reading ...
MIN_SHOW = 1.2     # ... and at least this long in all


def narrated_times(project):
    """{line index in the narrated .srt: (start, end)}: the subtitles' times moved to follow the narration
    (a line is shown from when it is read, a moment longer after it, never over the next one).
    None when there is no narration for the current subtitles (not made, or the subtitles changed since)."""
    src = project.tr_srt if project.has(TR_SRT) else project.src_srt
    try:
        with open(project.path(PLACED), encoding="utf-8") as f:
            data = json.load(f)
        if not os.path.isfile(src) or data.get("srt") != os.path.getmtime(src):
            return None
        said = data["lines"]
    except (OSError, ValueError, KeyError):
        return None
    cues = load_srt(src)
    idx = [i for i, c in enumerate(cues) if c.text.strip()]
    if len(idx) != len(said) or any(x is None for x in said):
        return None
    read = {i: said[j] for j, i in enumerate(idx)}
    starts = sorted((read[i][0], i) for i in idx)
    nxt = {i: (starts[k + 1][0] if k + 1 < len(starts) else None) for k, (_t, i) in enumerate(starts)}
    out = {}
    for i in idx:
        a, b = read[i]
        end = max(b + HOLD, a + MIN_SHOW)
        if nxt[i] is not None:
            end = min(end, nxt[i] - 0.04)
        out[i] = (a, max(end, a + 0.3))
    return out


def follow_narration(cues, times):
    """The cues with the narration's times (same order as the narrated .srt); unchanged when times is None
    or the cues do not match it line for line."""
    if not times or not cues:
        return cues
    from ..core.subtitle import Cue
    if max(times) >= len(cues):
        return cues
    return [Cue(times[i][0], times[i][1], c.text, dict(c.extra)) if i in times else c
            for i, c in enumerate(cues)]


def _voice_track(project, control, voice, cues, genders, duration, stamp=None) -> str:
    """Reading by reading from the start (see timing.py for how they are joined and placed). Readings are
    made ahead in parallel (Edge) at normal speed; each is then placed after the previous one, read again
    faster only when it still does not fit, laid on the track and saved on its own in dub_live/ so the
    preview can play the narration while the rest is being made."""
    from collections import deque

    from .timing import Grouper, Scheduler
    s = settings()
    base = int(s["tts_speed"])
    n = len(cues)
    workers = 4 if voice.engine == "edge" else 1
    total = int((duration + 3.0) * RATE)
    path = project.path(DUB)
    live = project.path(LIVE)
    clear_live(project)
    os.makedirs(live, exist_ok=True)
    acc = np.memmap(project.path("dub.f32"), dtype=np.float32, mode="w+", shape=(total,))
    grouper = Grouper(cues, genders, duration, voice.engine, base, join=bool(s["dub_join"]))
    sched = Scheduler(float(s["dub_max_speed"]), duration)
    spans, stats = [], {"joined": 0, "faster": 0, "late": 0}
    lines = [None] * n                 # when each subtitle line is actually read (for the subtitles to follow)

    def reading(u):
        control.check()
        clip = voice.clip(u.text, u.gender, base)
        return clip, _trim_silence(_decode(clip))

    pending = deque()
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        k = 0
        while pending or not grouper.done():
            while len(pending) < workers * 2 and not grouper.done():
                u = grouper.next()
                pending.append((u, ex.submit(reading, u)))
            u, fut = pending.popleft()
            clip, a = fut.result()
            control.check()
            length = len(a) / RATE
            grouper.measured(u.text, length)
            start, speed = sched.place(u, length, grouper.ahead(u.next_start))
            if speed > 1.02:
                want = length / speed                        # the time it has
                if speed >= 1.08:
                    try:                                     # a faster reading sounds better than squeezing
                        clip2 = voice.clip(u.text, u.gender, int(round(base * speed * 1.02)))
                        a2 = _trim_silence(_decode(clip2))
                        if len(a2):
                            clip, a = clip2, a2
                    except Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.info("Faster voice failed (%s): squeezing the clip", e)
                rest = len(a) / RATE / max(0.1, want)
                if rest > 1.02:                              # what is left: squeeze, a little
                    a = _trim_silence(_decode(clip, tempo=min(1.2, rest)))
                stats["faster"] += 1
            if len(u.lines) > 1:
                stats["joined"] += len(u.lines) - 1
            if start - u.start > 0.5:
                stats["late"] += 1
            a0 = int(start * RATE)
            a1 = min(total, a0 + len(a))
            if a1 > a0:
                acc[a0:a1] += a[:a1 - a0]          # full level: the narration volume is applied when mixing
            end = start + len(a) / RATE
            sched.ended(end)
            spans.append([round(start, 3), round(end, 3)])
            from .timing import syllables
            weights = [syllables(cues[k].text) for k in u.lines]     # a joined reading: share it by length
            t, total_w = start, float(sum(weights))
            for k, wgt in zip(u.lines, weights):
                t1 = t + (end - start) * wgt / total_w
                lines[k] = [round(t, 3), round(t1, 3)]
                t = t1
            _write_live(live, live_name(u.lines[0], start, u.slot0, u.slot1), a)
            k = u.lines[-1] + 1
            control.progress(k / n * 0.95, f"Making voice {k}/{n} · {voice.engine.capitalize()}")
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    log.info("Narration timing: %d lines joined, %d readings faster, %d started late", stats["joined"],
             stats["faster"], stats["late"])
    with open(project.path(PLACED), "w", encoding="utf-8") as f:
        json.dump({"spans": spans, "lines": lines, "srt": stamp}, f)
    acc.flush()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        step = RATE * 30
        for k in range(0, total, step):
            w.writeframes((np.clip(acc[k:k + step], -1, 1) * 32767).astype(np.int16).tobytes())
    del acc
    try:
        os.remove(project.path("dub.f32"))
    except OSError:
        pass
    return path


DUCK = 0.3              # how far the original voice goes down while the narration speaks
VOICE = "voice.wav"     # the original voice alone (original sound - music.wav), for the live preview


def mix_params(project) -> dict:
    s = settings()
    return {"narr": s["dub_volume"], "bg": s["dub_bg_volume"], "voice": s["dub_voice_volume"],
            "duck": bool(s["dub_duck"]), "music": project.has("music.wav")}


def mix_levels() -> dict:
    """The three volumes (0..2) and the ducking, read from the settings (the preview uses the same)."""
    s = settings()
    return {"narr": float(s["dub_volume"]) / 100, "bg": float(s["dub_bg_volume"]) / 100,
            "voice": float(s["dub_voice_volume"]) / 100, "duck": DUCK if s["dub_duck"] else 1.0}


def original_sound(project, control=None, duration=0.0) -> str:
    """orig44.wav: the video's sound at 44.1 kHz stereo, read the same way as for voice separation so that
    orig44 - music.wav is exactly the original voice."""
    path = project.path("orig44.wav")
    if not os.path.isfile(path):
        args = ["-i", project.video, "-vn", "-sn", "-dn"]
        stream = project.meta.get("audio_stream")
        args += ["-map", f"0:{stream}" if stream is not None else "0:a:0"]
        args += ["-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le", path + ".part.wav"]
        ffmpeg.run(args, duration=duration, control=control, label="Reading the original sound")
        os.replace(path + ".part.wav", path)
    return path


def preview_sources(project):
    """What the preview needs to play the mix live: ({"narr", "bg", "voice"}, narration lines) or (None, [])."""
    if not (project.has(DUB) and project.has("orig44.wav")):
        return None, []
    music = project.has("music.wav") and project.has(VOICE)
    src = {"narr": project.path(DUB),
           "bg": project.path("music.wav") if music else project.path("orig44.wav"),
           "voice": project.path(VOICE) if music else None}
    srt = project.tr_srt if project.has(TR_SRT) else project.src_srt
    cues = [c for c in load_srt(srt) if c.text.strip()] if os.path.isfile(srt) else []
    spans = placed_spans(project, cues)
    return src, spans


def duck_curve(spans, k, n, duck, ramp, ordered=False):
    """Gain (n samples from sample k): 1 between lines, `duck` while a narration line plays, smooth ramps.
    ordered: spans are sorted by start (the live preview: only the lines near k are looked at, a long video
    has thousands and this runs many times a second)."""
    g = np.ones(n, dtype=np.float32)
    if duck >= 1.0:
        return g
    if ordered and len(spans) > 32:
        import bisect
        first = max(0, bisect.bisect_left(spans, (k - ramp - 60 * 48000, 0)))     # no line is a minute long
        near = []
        for a, e in spans[first:]:
            if a - ramp > k + n:
                break
            near.append((a, e))
        spans = near
    for a, e in spans:
        if e + ramp < k or a - ramp > k + n:
            continue
        for p0, p1, up in ((a - ramp, a, False), (a, e, None), (e, e + ramp, True)):
            lo, hi = max(p0, k), min(p1, k + n)
            if hi <= lo:
                continue
            idx = np.arange(lo, hi)
            if up is None:
                val = np.full(len(idx), duck, dtype=np.float32)
            else:
                frac = (idx - p0) / max(1, p1 - p0)
                val = (duck + (1 - duck) * frac) if up else (1 - (1 - duck) * frac)
            g[lo - k:hi - k] = np.minimum(g[lo - k:hi - k], val)
    return g


def _mix(project, control, track, cues, duration):
    """mix.wav = narration + background + original voice, each at its own volume.
    With voice separation (music.wav): background = music.wav, original voice = original sound - music.wav.
    Without it the two cannot be told apart: the whole original sound follows the Background volume.
    The original voice (or the whole original sound) goes down while a narration line plays (ducking)."""
    lv = mix_levels()
    orig = original_sound(project, control, duration)
    music = project.path("music.wav") if project.has("music.wav") else None
    out = project.path(MIX)
    tmp = out + ".part.wav"
    vtmp = project.path(VOICE) + ".part.wav"
    ramp = int(0.15 * RATE)
    spans = [(int(a * RATE), int(b * RATE)) for a, b in placed_spans(project, cues)]
    files = [wave.open(orig, "rb"), wave.open(track, "rb")]
    m = wave.open(music, "rb") if music else None
    w = wave.open(tmp, "wb")
    vw = wave.open(vtmp, "wb") if music else None
    try:
        for f in [w] + ([vw] if vw else []):
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(RATE)
        b, v = files
        ch = b.getnchannels()
        n = b.getnframes()
        step = RATE * 20

        def stereo(f, cnt):
            x = np.frombuffer(f.readframes(cnt), dtype=np.int16).astype(np.float32) / 32768
            c = f.getnchannels()
            x = x.reshape(-1, c)
            if c == 1:
                x = np.repeat(x, 2, axis=1)
            x = x[:, :2]
            if len(x) < cnt:
                x = np.pad(x, ((0, cnt - len(x)), (0, 0)))
            return x

        for k in range(0, n, step):
            control.check()
            cnt = min(step, n - k)
            o = stereo(b, cnt) if ch else None
            narr = np.frombuffer(v.readframes(cnt), dtype=np.int16).astype(np.float32) / 32768
            if len(narr) < cnt:
                narr = np.pad(narr, (0, cnt - len(narr)))
            g = duck_curve(spans, k, cnt, lv["duck"], ramp)
            if m is not None:
                mu = stereo(m, cnt)
                voice = o - mu
                x = mu * lv["bg"] + voice * (lv["voice"] * g)[:, None]
                vw.writeframes((np.clip(voice, -1, 1) * 32767).astype(np.int16).tobytes())
            else:
                x = o * (lv["bg"] * g)[:, None]
            x = x + (narr[:cnt] * lv["narr"])[:, None]
            w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
            control.progress(min(1.0, (k + cnt) / max(1, n)), "Mixing the narration")
    finally:
        for f in files + [m, w, vw]:
            if f is not None:
                f.close()
    os.replace(tmp, out)
    if music:
        os.replace(vtmp, project.path(VOICE))
    return out
