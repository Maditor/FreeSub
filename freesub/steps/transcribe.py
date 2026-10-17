"""Step: audio.wav -> subs.src.srt with faster-whisper.

* GPU (CUDA) when an NVIDIA card is usable, else CPU; a CUDA error mid-way switches to the CPU and goes on.
* Checkpoint: the cues found so far are saved to transcribe.partial.json every ~20 s, so a stopped or
  crashed run continues from the last cue instead of starting again.
* Long Whisper segments are cut at word timestamps (punctuation and pauses first), lines are wrapped,
  hallucinated lines are dropped (hallucination.py) and the timing is cleaned up.
* Speakers (speakers.py): a line is cut where the voice changes, and every line gets a speaker number.
  The words with their times are kept (transcribe.words.json), so switching this on or off later only redoes
  the cutting, not Whisper.
* The guessed gender of each speaker goes to subs.src.json (used by translation and narration later).
"""
import json
import logging
import os
import threading
import time

from ..config import settings
from ..core import gpu, models
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, Project
from ..core.subtitle import Cue, save_srt
from ..langs import NO_SPACE
from ..log import step_timer
from . import audio as audio_step
from . import hallucination as hal
from .voice_gender import classify

log = logging.getLogger(__name__)
PARTIAL = "transcribe.partial.json"
WORDS = "transcribe.words.json"
SIDE = "subs.src.json"
RATE = 16000
PUNCT_END = tuple("。．.!！?？…")
PUNCT_ANY = PUNCT_END + tuple("、，,;；:：")

_MODEL = {"key": None, "model": None}
_MODEL_LOCK = threading.Lock()


# ------------------------------------------------------------------ model
def load_model(name: str, device_choice: str, control=None, force_cpu=False):
    """Load (or reuse) the Whisper model; downloads it first if needed."""
    if not models.whisper_ready(name):
        if control is not None:
            control.status(f"Downloading Whisper {name}…")
        models.download_whisper(name, control)
    device, compute = gpu.whisper_device("cpu" if force_cpu else device_choice)
    s = settings()
    if s["whisper_compute"] not in ("auto", "", None):
        compute = s["whisper_compute"]
        if device == "cpu" and "float16" in compute:
            compute = "int8"
    key = (name, device, compute)
    with _MODEL_LOCK:
        if _MODEL["key"] == key:
            return _MODEL["model"], device
        _MODEL.update(key=None, model=None)
        from faster_whisper import WhisperModel
        if control is not None:
            control.status(f"Loading Whisper {name} on {device.upper()}…")
            control.progress(None, f"Loading Whisper {name} on {device.upper()}…")
        t0 = time.perf_counter()
        try:
            m = WhisperModel(models.whisper_dir(name), device=device, compute_type=compute,
                             cpu_threads=max(2, (os.cpu_count() or 4) - 2) if device == "cpu" else 0)
        except Exception as e:  # noqa: BLE001
            if device != "cuda":
                raise
            log.warning("Whisper on GPU failed (%s): using the CPU", e)
            if control is not None:
                control.status(f"GPU unavailable ({e}). Using the CPU.")
            device, compute = "cpu", "int8"
            m = WhisperModel(models.whisper_dir(name), device="cpu", compute_type="int8",
                             cpu_threads=max(2, (os.cpu_count() or 4) - 2))
            key = (name, device, compute)
        log.info("Whisper %s loaded on %s/%s in %.1f s", name, device, compute, time.perf_counter() - t0)
        _MODEL.update(key=key, model=m)
        return m, device


def unload_model():
    with _MODEL_LOCK:
        _MODEL.update(key=None, model=None)


# ------------------------------------------------------------------ text shaping
def _join(words) -> str:
    return "".join(w["w"] for w in words).strip()


def _limits(lang):
    s = settings()
    per_line = int(s["sub_max_chars"])
    if lang in NO_SPACE:
        per_line = max(8, round(per_line * 0.45))
    return per_line, per_line * max(1, int(s["sub_max_lines"])), float(s["sub_max_duration"])


def split_words(words, lang):
    """Cut a list of timed words into cue-sized chunks."""
    per_line, max_chars, max_dur = _limits(lang)
    # 1) hard breaks: long pauses and sentence ends followed by a pause
    groups, cur = [], []
    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            if gap > 1.2 or (cur[-1]["w"].strip().endswith(PUNCT_END) and gap > 0.4):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    # 2) split what is still too long at the best point (punctuation, pause, balanced halves), recursively
    def fits(ws):
        return len(_join(ws)) <= max_chars and ws[-1]["e"] - ws[0]["s"] <= max_dur

    def cut(ws):
        if len(ws) < 2 or fits(ws):
            return [ws]
        total = len(_join(ws)) or 1
        best, best_score = 1, -1e9
        for i in range(1, len(ws)):
            left = len(_join(ws[:i]))
            prev = ws[i - 1]["w"].strip()
            score = -abs(2 * left - total) / total
            score += 0.45 if prev.endswith(PUNCT_END) else 0.25 if prev.endswith(PUNCT_ANY) else 0
            score += 0.3 * min(1.0, max(0.0, ws[i]["s"] - ws[i - 1]["e"]))
            if score > best_score:
                best, best_score = i, score
        return cut(ws[:best]) + cut(ws[best:])

    return [c for g in groups for c in cut(g)]


def wrap(text: str, lang: str) -> str:
    """Break a long cue into two balanced lines."""
    per_line, _, _ = _limits(lang)
    text = " ".join(text.split()) if lang not in NO_SPACE else text.replace("\n", "")
    if len(text) <= per_line:
        return text
    mid = len(text) // 2
    if lang in NO_SPACE:
        best = None
        for d in range(0, mid // 2 + 1):          # punctuation near the middle, else the middle
            for i in (mid + d, mid - d):
                if 0 < i < len(text) and text[i - 1] in "、，,。！？!?…":
                    best = i
                    break
            if best:
                break
        i = best or mid
        return text[:i] + "\n" + text[i:]
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    i = min(spaces, key=lambda p: abs(p - mid))
    return text[:i] + "\n" + text[i + 1:]


def tidy_timing(cues):
    s = settings()
    min_d, gap = float(s["sub_min_duration"]), float(s["sub_gap"])
    cues.sort(key=lambda c: c.start)
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else None
        if c.duration < min_d:
            c.end = c.start + min_d if nxt is None else min(c.start + min_d, max(c.end, nxt - gap))
        if nxt is not None and c.end > nxt - gap:
            c.end = max(c.start + 0.2, nxt - gap)
    return cues


# ------------------------------------------------------------------ the step
def _segments_to_cues(seg, lang, offset, drop_interjections):
    """One Whisper segment -> zero or more cues (dicts, JSON-safe)."""
    text = seg.text.strip()
    if settings()["whisper_filter"] and not hal.keep_segment(text, seg.avg_logprob, seg.no_speech_prob,
                                                             seg.compression_ratio, lang, drop_interjections):
        log.info("Dropped (filter) %.1f: %s", seg.start + offset, text[:80])
        return []
    rep = hal.collapse_repeats(text, lang)
    words = [{"s": w.start + offset, "e": w.end + offset, "w": w.word} for w in (seg.words or [])]
    if rep or not words:
        return [{"s": seg.start + offset, "e": seg.end + offset, "t": rep or text, "lp": seg.avg_logprob}]
    out = []
    for chunk in split_words(words, lang):
        t = _join(chunk)
        if hal.norm(t):
            out.append({"s": chunk[0]["s"], "e": chunk[-1]["e"], "t": t, "lp": seg.avg_logprob,
                        "w": [[round(w["s"], 3), round(w["e"], 3), w["w"]] for w in chunk]})
    return out


def run(project: Project, control, force: bool = False) -> str:
    """Transcribe project.audio into subs.src.srt. Returns the SRT path."""
    s = settings()
    name = s["whisper_model"]
    lang_opt = s["whisper_language"]
    out = project.src_srt
    sep = bool(s["whisper_separate"]) and project.has("vocals.wav")
    forced = models.whisper_lang(name)
    if forced and lang_opt not in ("auto", forced):
        log.warning("%s only knows '%s': using it instead of '%s'", name, forced, lang_opt)
    if forced:
        lang_opt = forced
    params = {"model": name, "lang": lang_opt, "vad": bool(s["whisper_vad"]), "beam": int(s["whisper_beam"]),
              "voice_only": sep}
    from .speakers import VERSION as SPK_VERSION
    want_spk = bool(s["whisper_speakers"])
    spk_mark = SPK_VERSION if want_spk else 0
    wav = project.path("vocals.wav") if sep else project.audio
    same_params = project.is_done("transcribe", SRC_SRT) and project.step("transcribe").get("params") == params
    if force and same_params and project.has(WORDS):
        # made again with the same Whisper settings: the words heard are the same, so only the lines are
        # built again from them (quick); edits of the old lines are dropped
        try:
            with open(project.path(WORDS), encoding="utf-8") as f:
                state = json.load(f)
            with step_timer("transcribe", project.video):
                return _finish(project, control, state, lang_opt, params, want_spk, wav)
        except (OSError, ValueError, KeyError) as e:
            log.warning("Could not reuse the saved words (%s): transcribing again", e)
    same = not force and same_params
    if same and (project.step("transcribe").get("speakers_v") or 0) == spk_mark:
        project.ensure_published(SRC_SRT, project.meta.get("source_lang") or "")
        control.progress(1.0, "Subtitles already made")
        return out
    if same and project.has(WORDS):                 # only the speaker option changed: Whisper is not run again
        try:
            with open(project.path(WORDS), encoding="utf-8") as f:
                state = json.load(f)
            with step_timer("transcribe", project.video):
                return _finish(project, control, state, lang_opt, params, want_spk, wav)
        except (OSError, ValueError, KeyError) as e:
            log.warning("Could not reuse the saved words (%s): transcribing again", e)
    if not project.has("audio.wav"):
        raise RuntimeError("Extract the audio first")
    if bool(s["whisper_separate"]) and not sep:
        log.warning("Voice separation is on but vocals.wav is missing: using the full soundtrack")

    partial_path = project.path(PARTIAL)
    state = {"params": params, "lang": "", "done_to": 0.0, "cues": []}
    if not force:
        try:
            with open(partial_path, encoding="utf-8") as f:
                old = json.load(f)
            if old.get("params") == params:
                state = old
                log.info("Resuming transcription at %.1f s (%d cues so far)", state["done_to"], len(state["cues"]))
        except (OSError, ValueError):
            pass

    with step_timer("transcribe", project.video):
        project.mark("transcribe", "running", params=params)
        try:
            _transcribe(project, control, state, partial_path, lang_opt,
                        project.path("vocals.wav") if sep else project.audio)
        except Cancelled:
            _save_partial(partial_path, state)
            project.mark("transcribe", "stopped", resume_at=round(state["done_to"], 1))
            raise
        except Exception as e:
            _save_partial(partial_path, state)
            project.mark("transcribe", "failed", error=str(e)[:300])
            raise

        return _finish(project, control, state, lang_opt, params, want_spk, wav)


def _finish(project, control, state, lang_opt, params, want_spk, wav) -> str:
    """Cues found by Whisper -> cut by speaker, cleaned, wrapped -> subs.src.srt (+ subs.src.json)."""
    out = project.src_srt
    partial_path = project.path(PARTIAL)
    lang = state["lang"] or (lang_opt if lang_opt != "auto" else "")
    raw = state["cues"]
    try:
        tmp = project.path(WORDS) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, project.path(WORDS))
    except OSError:
        pass
    if want_spk:
        raw = _split_speakers(raw, wav, control)
    cues = [Cue(c["s"], c["e"], c["t"], {"lp": c.get("lp"), "spk": c.get("spk")}) for c in raw]
    cues = hal.drop_loops(cues)
    cues = tidy_timing(cues)
    for c in cues:
        c.text = wrap(c.text, lang)
    if not cues:
        project.mark("transcribe", "failed", params=params, error="no speech")
        try:
            os.remove(partial_path)
        except OSError:
            pass
        raise RuntimeError("No speech was found in this video. If it has burned-in subtitles, "
                           "use 'From the picture (OCR)'.")
    control.progress(None, "Guessing speaker voices…")
    from . import genders
    spk = [c.extra.get("spk") for c in cues] if want_spk else None
    guess, f0 = genders.auto_genders(wav, cues, spk)
    save_srt(cues, out)
    with open(project.path(SIDE), "w", encoding="utf-8") as f:
        json.dump([{"start": round(c.start, 3), "end": round(c.end, 3), "gender": g, "auto": g, "how": "auto",
                    "f0": round(h, 1) if h else None, "speaker": c.extra.get("spk")}
                   for c, g, h in zip(cues, guess, f0)], f, ensure_ascii=False)
    if genders.load_marks(project):                 # marks you made earlier still teach the new lines
        try:
            genders.apply_marks(project, control)
        except Exception as e:  # noqa: BLE001
            log.warning("Voice marks not applied: %s", e)
    project.meta["source_lang"] = lang
    n_spk = len({c.extra.get("spk") for c in cues if c.extra.get("spk") is not None})
    from .speakers import VERSION as SPK_VERSION
    project.mark("transcribe", "done", params=params, file=SRC_SRT, cues=len(cues), lang=lang,
                 speakers=want_spk, speakers_v=SPK_VERSION if want_spk else 0, voices=n_spk)
    project.publish(SRC_SRT, lang)
    try:
        os.remove(partial_path)
    except OSError:
        pass
    control.progress(1.0, f"{len(cues)} subtitles")
    return out


def _save_partial(path, state):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _transcribe(project, control, state, partial_path, lang_opt, wav):
    s = settings()
    name = s["whisper_model"]
    audio = audio_step.load(wav)
    total = len(audio) / RATE
    if total < 0.3:
        return
    force_cpu = False
    model, device = load_model(name, s["whisper_device"], control)
    drop_inter = True
    while state["done_to"] < total - 0.5:
        control.check()
        start = state["done_to"]
        piece = audio[int(start * RATE):]
        lang = state["lang"] or (None if lang_opt == "auto" else lang_opt)
        kw = dict(
            language=lang, task="transcribe", beam_size=int(s["whisper_beam"]),
            best_of=max(1, int(s["whisper_beam"])), word_timestamps=True,
            condition_on_previous_text=False,       # avoids long repetition loops
            initial_prompt=(s["whisper_prompt"] or None),
            vad_filter=bool(s["whisper_vad"]),
            vad_parameters=dict(threshold=float(s["whisper_vad_threshold"]), min_speech_duration_ms=250,
                                min_silence_duration_ms=int(s["whisper_min_silence_ms"]), speech_pad_ms=300),
            hallucination_silence_threshold=2.0,
        )
        device_label = device.upper()
        control.status(f"Transcribing with {name} on {device_label}…")
        t_last_save = time.time()
        t0 = time.time()
        try:
            segments, info = model.transcribe(piece, **kw)
            if not state["lang"]:
                state["lang"] = info.language or ""
                if lang_opt == "auto":
                    log.info("Detected language: %s (%.0f%%)", info.language, (info.language_probability or 0) * 100)
            for seg in segments:
                control.check()
                new = _segments_to_cues(seg, state["lang"], start, drop_inter)
                state["cues"].extend(new)
                state["done_to"] = max(state["done_to"], start + seg.end)
                frac = state["done_to"] / total
                speed = (state["done_to"] - start) / max(0.1, time.time() - t0)
                control.progress(frac, f"Transcribing {frac * 100:.0f}% · {speed:.1f}× real time · "
                                       f"{len(state['cues'])} lines · {device_label}")
                if time.time() - t_last_save > 20:
                    _save_partial(partial_path, state)
                    t_last_save = time.time()
            state["done_to"] = total          # the rest is silence
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if device == "cuda" and not force_cpu:
                log.warning("GPU error during transcription (%s): continuing on the CPU", e)
                control.status(f"GPU error ({e}). Continuing on the CPU…")
                unload_model()
                force_cpu = True
                model, device = load_model(name, "cpu", control, force_cpu=True)
                continue
            raise
    _save_partial(partial_path, state)


def _split_speakers(raw, wav, control):
    """Cut the cues where the voice changes (speakers.py). Any problem: the cues stay as they are."""
    from . import speakers
    try:
        if not speakers.model_ready():
            control.status(f"Downloading the speaker model ({speakers.MODEL['size']})…")
            speakers.download_model(control)
        words = [w for c in raw for w in (c.get("w") or [])]
        if not words or not os.path.isfile(wav):
            return raw
        control.status("Telling the voices apart…")
        labels = speakers.label_words(audio_step.load(wav), words, speakers.Embedder(), control)
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("Speakers could not be told apart (%s): lines are not cut by speaker", e)
        return raw
    per, i = [], 0
    for c in raw:
        n = len(c.get("w") or [])
        per.append(labels[i:i + n])
        i += n
    out = speakers.split_by_speaker(raw, per, lambda ws: _join([{"w": w[2]} for w in ws]))
    n_spk = len({c.get("spk") for c in out if c.get("spk") is not None})
    log.info("Speakers: %d voice(s); %d lines -> %d lines", n_spk, len(raw), len(out))
    return out


def _speaker_genders(cues):
    """One gender per speaker (the majority of their lines), so a person keeps the same narration voice."""
    votes = {}
    for c in cues:
        spk, g = c.extra.get("spk"), c.extra.get("gender")
        if spk is not None and g:
            v = votes.setdefault(spk, {"f": 0.0, "m": 0.0})
            v[g] += c.end - c.start
    for c in cues:
        v = votes.get(c.extra.get("spk"))
        if v and max(v.values()) > 0:
            c.extra["gender"] = max(v, key=v.get)


def _genders(wav, cues):
    if not os.path.isfile(wav):
        return
    for c in cues:
        a = audio_step.load(wav, c.start, c.end)
        g = classify(a) if len(a) else None
        if g:
            c.extra["gender"] = g


def import_translation(project: Project, path: str) -> str:
    """Use an existing .srt as the translation (kept as it is: Translate does not replace it). A video with no
    subtitles yet gets it as its original too, so the table has lines to show."""
    from ..core.subtitle import load_srt
    from ..core.project import TR_SRT
    cues = load_srt(path)
    if not cues:
        raise RuntimeError("No subtitles found in this file")
    project.ensure()
    if not project.has(SRC_SRT):
        save_srt(cues, project.src_srt)
        from .ocr import guess_lang
        project.meta["source_lang"] = guess_lang([c.text for c in cues])
        project.mark("import", "done", file=SRC_SRT, source=os.path.abspath(path), cues=len(cues))
        project.mark("transcribe", "skipped")
    save_srt(cues, project.tr_srt)
    project.meta["tr_edited"] = True              # Translate keeps it
    project.mark("translate", "done", file=TR_SRT, source=os.path.abspath(path), cues=len(cues))
    project.publish(TR_SRT, settings()["target_lang"])
    return project.tr_srt


def import_srt(project: Project, path: str) -> str:
    """Use an existing .srt as the source subtitles (starts the work at the translation step)."""
    from ..core.subtitle import load_srt
    cues = load_srt(path)
    if not cues:
        raise RuntimeError("No subtitles found in this file")
    project.ensure()
    save_srt(cues, project.src_srt)
    from .ocr import guess_lang
    project.meta["source_lang"] = guess_lang([c.text for c in cues])
    project.mark("import", "done", file=SRC_SRT, source=os.path.abspath(path), cues=len(cues))
    project.mark("transcribe", "skipped")
    return project.src_srt

