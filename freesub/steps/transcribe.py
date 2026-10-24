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
import re
import threading
import time

from ..config import settings
from ..core import gpu, models
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, Project
from ..core.subtitle import Cue, save_srt
from ..langs import NO_SPACE
from ..log import step_timer
from ..translate import names
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
# second listen (rescue): speech the voice detector hears that got no line in the first pass
RESCUE_VAD = 0.35       # a lower bar than the first pass: quick or shouted speech scores lower
RESCUE_MIN = 0.5        # seconds of speech with no line before it is listened to again
RESCUE_PAD = 0.25
# nobody says more than this: a piece with more text for its time is made up ("你怎么不听话" ×4 in 1 s)
MAX_CHARS_PER_S = 15        # Chinese / Japanese / Thai... (quick speech is about 8)
MAX_WORDS_PER_S = 7
# a short line found by the second listen must be fairly sure (sounds left in the voice track become words)
SHORT_CHARS, SHORT_WORDS, SHORT_LP = 4, 2, -0.7


def word_cap(text: str, lang: str) -> float:
    """The longest a word can really be said in (seconds). Whisper sometimes stretches a word over speech it
    did not write down (a 20 s line of 8 characters): that hides the missed speech from the second listen."""
    n = len("".join(text.split())) or 1
    return min(2.0, 0.35 + 0.3 * n) if lang in NO_SPACE else min(2.0, 0.4 + 0.12 * n)


def clamp_words(words, lang):
    """Stretched words cut back to a length they can be said in: the first word of a piece keeps its end
    (silence was put before it), the others keep their start. Returns how many were cut."""
    n = 0
    for i, w in enumerate(words):
        cap = word_cap(w["w"], lang)
        if w["e"] - w["s"] > cap:
            n += 1
            if i == 0 and len(words) > 1:
                w["s"] = w["e"] - cap
            else:
                w["e"] = w["s"] + cap
    return n

_MODEL = {"key": None, "model": None}
_HEARD = threading.local()      # what Whisper heard in this run (the 'heard' log), per work thread
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
    """(per line, per subtitle, longest seconds). Counted in words; for languages written without spaces
    (Chinese, Japanese, Thai...) in characters, twice the word count."""
    s = settings()
    per_line = max(2, int(s["sub_max_words"]))
    if lang in NO_SPACE:
        per_line *= 2
    return per_line, per_line * max(1, int(s["sub_max_lines"])), float(s["sub_max_duration"])


def size_of(text: str, lang: str) -> int:
    """Length the limits count: words, or characters for a language without spaces."""
    if lang in NO_SPACE:
        return len("".join(text.split()))
    return len(text.split())


MIN_PIECE = 3          # words (twice as many characters without spaces): a line is not cut shorter than this
PAUSE_BREAK = 1.2      # a pause this long ends a line (when the line already has a few words) ...
LONG_PAUSE = 2.5       # ... and this long always


def split_words(words, lang):
    """Cut a list of timed words into cue-sized chunks: at sentence ends and long pauses first, then what is
    still too long at the best point (a sentence end, a comma, a pause, near the middle), never leaving a
    piece of a word or two hanging (\"I think\" | \"that we should go\") unless a sentence ends there."""
    per_line, max_chars, max_dur = _limits(lang)
    piece = MIN_PIECE * (2 if lang in NO_SPACE else 1)
    # 1) hard breaks: sentence ends followed by a pause, long pauses (a short pause mid-sentence is not one)
    groups, cur = [], []
    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            ends = cur[-1]["w"].strip().endswith(PUNCT_END)
            if gap > LONG_PAUSE or (ends and gap > 0.4) or (gap > PAUSE_BREAK and (
                    ends or size_of(_join(cur), lang) >= piece)):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    # a word or two alone at the start, far before the rest: Whisper pinned the first word of a piece to where the
    # piece starts ("欢" at 15.8 s, "迎光临" at 24 s). It is moved to just before the rest and joins it.
    if len(groups) > 1 and size_of(_join(groups[0]), lang) < piece and \
            not groups[0][-1]["w"].strip().endswith(PUNCT_END) and groups[1][0]["s"] - groups[0][-1]["e"] > PAUSE_BREAK:
        lone, t = groups.pop(0), groups[0][0]["s"]
        for w in reversed(lone):
            d = min(w["e"] - w["s"], word_cap(w["w"], lang), 0.3)
            w["e"], w["s"] = t, t - d
            t = w["s"]
        groups[0] = lone + groups[0]

    # 2) split what is still too long, recursively
    def fits(ws):
        return size_of(_join(ws), lang) <= max_chars and ws[-1]["e"] - ws[0]["s"] <= max_dur

    def cut(ws):
        if len(ws) < 2 or fits(ws):
            return [ws]
        sizes = [size_of(w["w"], lang) for w in ws]          # running sizes (not joining every prefix again)
        total = sum(sizes) or 1
        small = total >= 2 * piece
        best, best_score, left = 1, -1e9, 0
        for i in range(1, len(ws)):
            left += sizes[i - 1]
            prev = ws[i - 1]["w"].strip()
            end = prev.endswith(PUNCT_END)
            score = -abs(2 * left - total) / total
            score += 0.8 if end else 0.35 if prev.endswith(PUNCT_ANY) else 0
            score += 0.3 * min(1.0, max(0.0, ws[i]["s"] - ws[i - 1]["e"]))
            if small and not end and (left < piece or total - left < piece):
                score -= 2.0                                  # a word or two alone: only at a sentence end
            if score > best_score:
                best, best_score = i, score
        return cut(ws[:best]) + cut(ws[best:])

    return [c for g in groups for c in cut(g)]


def rejoin(raw, lang):
    """Whisper hands its text over in pieces (segments) that often end in the middle of a sentence; each
    piece was cut into lines on its own. Here the lines of a sentence that runs over pieces are put together
    again and cut once more as one (split_words), so a line holds whole phrases and the translation sees
    them. Lines with no word times (repeats) are left as they are."""
    out, run = [], []

    def flush():
        if not run:
            return
        if len(run) == 1:
            out.append(run[0])
        else:
            words, lps = [], []
            for c in run:
                words += [{"s": w[0], "e": w[1], "w": w[2]} for w in c["w"]]
                if c.get("lp") is not None:
                    lps.append(c["lp"])
            words.sort(key=lambda w: w["s"])
            lp = sum(lps) / len(lps) if lps else None
            for chunk in split_words(words, lang):
                t = hal.strip_dashes(hal.strip_gender_tags(_join(chunk)))
                if hal.norm(t):
                    out.append({"s": chunk[0]["s"], "e": chunk[-1]["e"], "t": t, "lp": lp,
                                "w": [[w["s"], w["e"], w["w"]] for w in chunk]})
        run.clear()

    for c in raw:
        if not c.get("w"):
            flush()
            out.append(c)
            continue
        if run:
            prev = run[-1]
            gap = c["s"] - prev["e"]
            n = sum(len(x["w"]) for x in run)
            if prev["t"].rstrip().endswith(PUNCT_END) or gap > PAUSE_BREAK or n > 120:
                flush()
        run.append(c)
    flush()
    return out


def wrap(text: str, lang: str) -> str:
    """Break a long cue into balanced lines (at most Lines per subtitle)."""
    per_line, _, _ = _limits(lang)
    text = " ".join(text.split()) if lang not in NO_SPACE else text.replace("\n", "")
    if size_of(text, lang) <= per_line or int(settings()["sub_max_lines"]) < 2:
        return text
    lines = int(settings()["sub_max_lines"])
    if lines >= 3 and size_of(text, lang) > per_line * 2 and lang not in NO_SPACE:
        words = text.split()
        n = -(-len(words) // 3)
        return "\n".join(" ".join(words[i:i + n]) for i in range(0, len(words), n))
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
def _segments_to_cues(seg, lang, offset, drop_interjections, sure_speech=False):
    """One Whisper segment -> zero or more cues (dicts, JSON-safe)."""
    text = hal.strip_dashes(hal.strip_gender_tags(seg.text.strip()))
    if not text:
        _heard_seg(seg, offset, "EMPTY", "Whisper wrote only a voice label / nothing")
        return []
    why = hal.drop_reason(text, seg.avg_logprob, seg.no_speech_prob, seg.compression_ratio, lang,
                          drop_interjections, sure_speech) if settings()["whisper_filter"] else None
    if not why and settings()["whisper_filter"]:
        rate = size_of(text, lang) / max(0.3, seg.end - seg.start)
        if rate > (MAX_CHARS_PER_S if lang in NO_SPACE else MAX_WORDS_PER_S):
            why = f"far too many words for its time ({rate:.0f} per second): made up"
    if why:
        log.info("Dropped (filter) %.1f: %s", seg.start + offset, text[:80])
        _heard_seg(seg, offset, "DROPPED", why)
        return []
    rep = hal.collapse_repeats(text, lang)
    words = [{"s": w.start + offset, "e": w.end + offset, "w": w.word} for w in (seg.words or [])]
    cut = clamp_words(words, lang)
    note = ""
    if words:
        note = f"words {_ts(words[0]['s'])}-{_ts(words[-1]['e'])}"
        if cut:
            note += f" · {cut} stretched word(s) cut back"
    _heard_seg(seg, offset, "KEPT", note)
    if rep or not words:
        return [{"s": seg.start + offset, "e": seg.end + offset, "t": rep or text, "lp": seg.avg_logprob}]
    out = []
    for chunk in split_words(words, lang):
        t = hal.strip_dashes(hal.strip_gender_tags(_join(chunk)))
        if hal.norm(t):
            out.append({"s": chunk[0]["s"], "e": chunk[-1]["e"], "t": t, "lp": seg.avg_logprob,
                        "w": [[round(w["s"], 3), round(w["e"], 3), w["w"]] for w in chunk]})
    return out


def run(project: Project, control, force: bool = False) -> str:
    """Transcribe project.audio into subs.src.srt. Returns the SRT path."""
    _HEARD.rows, _HEARD.skips, _HEARD.pass_name, _HEARD.ran = [], [], "1st", False
    s = settings()
    lang_opt = s["whisper_language"]
    name = models.fit(s["whisper_model"], lang_opt)     # a model that was removed / is for another language
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
    n0 = len(raw)
    raw = rejoin(raw, lang)                         # sentences over Whisper's pieces: whole again
    if len(raw) != n0:
        log.info("Lines rejoined over Whisper's pieces: %d -> %d", n0, len(raw))
    if want_spk:
        raw = _split_speakers(raw, wav, control)
    cues = [Cue(c["s"], c["e"], hal.strip_dashes(hal.strip_gender_tags(c["t"])), {"lp": c.get("lp"), "spk": c.get("spk")})
            for c in raw]
    cues = [c for c in cues if hal.norm(c.text)]       # a line that was only a (nam)/(nữ) label is gone
    cues = hal.drop_loops(cues)
    cues = tidy_timing(cues)
    for c in cues:
        c.text = wrap(c.text, lang)
    _write_heard(project, cues, wav, lang, params)
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
    _HEARD.ran = True
    if state["done_to"] > 0:
        _HEARD.skips = getattr(_HEARD, "skips", []) + [f"resumed at {_ts(state['done_to'])}: lines before it "
                                                       "were heard in an earlier run (not listed)"]
    voice_only = os.path.basename(wav) == "vocals.wav"
    with _WhisperSkips():
        _transcribe_loop(model, device, name, s, audio, total, state, partial_path, lang_opt, control, force_cpu,
                         voice_only)


def _transcribe_loop(model, device, name, s, audio, total, state, partial_path, lang_opt, control, force_cpu,
                     voice_only=False):
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
            initial_prompt=(names.whisper_hint() or None),       # the chosen name list
            vad_filter=bool(s["whisper_vad"]),
            vad_parameters=dict(threshold=float(s["whisper_vad_threshold"]), min_speech_duration_ms=250,
                                min_silence_duration_ms=int(s["whisper_min_silence_ms"]), speech_pad_ms=300),
            # Whisper's own "hallucination near silence" check flags quick speech (very short words) and
            # shouting (unsure words) as made up and skips it; the voice detector already keeps silence out
            hallucination_silence_threshold=None if s["whisper_vad"] else 2.0,
            # the detector said there is speech here: a loud / quick line is not thrown away as "no speech"
            no_speech_threshold=0.8 if s["whisper_vad"] else 0.6,
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
    if not state.get("rescued"):
        _save_partial(partial_path, state)
        _rescue(model, audio, state, control, lang_opt, s, voice_only)
        state["rescued"] = True
    _save_partial(partial_path, state)


# ------------------------------------------------------------------ second listen
def missed_parts(regions, cues, min_len=RESCUE_MIN, pad=0.1):
    """Pieces of the speech regions [(start, end)] that no cue covers (cues: dicts with s/e), each at least
    min_len long."""
    spans = sorted((c["s"] - pad, c["e"] + pad) for c in cues)
    out = []
    for a, b in regions:
        t = a
        for s0, e0 in spans:
            if e0 <= t:
                continue
            if s0 >= b:
                break
            if s0 - t >= min_len:
                out.append((t, s0))
            t = max(t, e0)
            if t >= b:
                break
        if b - t >= min_len:
            out.append((t, b))
    return out


def speech_regions(audio, threshold=RESCUE_VAD, voice_only=False):
    """Where the voice detector hears speech, in seconds. voice_only (the voice separated from the music): also
    where the voice track is loud, because the detector often misses shouting and screaming."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions(threshold=threshold, min_speech_duration_ms=250, min_silence_duration_ms=300,
                      speech_pad_ms=100)
    regions = [(r["start"] / RATE, r["end"] / RATE) for r in get_speech_timestamps(audio, opts)]
    if voice_only:
        regions = merge_regions(regions + loud_regions(audio))
    return regions


def loud_regions(audio, frame=0.05, min_len=0.4, bridge=0.3):
    """Where a voice-only track has sound clearly above its own quiet level."""
    import numpy as np
    n = int(frame * RATE)
    k = len(audio) // n
    if k < 4:
        return []
    fr = audio[:k * n].astype(np.float32).reshape(k, n)
    db = 10 * np.log10(np.mean(fr * fr, axis=1) + 1e-10)
    floor = float(np.percentile(db, 10))
    loud = db > max(-42.0, floor + 15.0)
    out, start, last = [], None, None
    for i, on in enumerate(loud):
        if on:
            if start is None:
                start = i
            elif (i - last - 1) * frame > bridge:
                if (last + 1 - start) * frame >= min_len:
                    out.append((start * frame, (last + 1) * frame))
                start = i
            last = i
    if start is not None and (last + 1 - start) * frame >= min_len:
        out.append((start * frame, (last + 1) * frame))
    return out


def merge_regions(regions, gap=0.2):
    out = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# with no name list, Chinese pieces heard alone tend to come out in traditional characters with no punctuation
ZH_HINT = {"zh": "以下是普通话的句子。"}


def _covered(cue, cues) -> bool:
    """A new line whose middle falls inside a line we have: the same speech heard twice."""
    mid = (cue["s"] + cue["e"]) / 2
    return any(c["s"] <= mid <= c["e"] for c in cues)


def thin(c, lang) -> bool:
    """A line spread over far more time than its words take to say (\"小熊熊,去把门打开\" over 20 s): Whisper
    skipped speech inside it, and its timing cannot be trusted."""
    d = c["e"] - c["s"]
    return d >= 3.0 and size_of(c["t"], lang) / d < (1.3 if lang in NO_SPACE else 0.6)


def _context(cues, t, lang, n=2) -> str:
    """The last lines said before time t: given to Whisper with a piece heard alone, so a shouted 求你了 is
    heard in its sentence (alone it came out as 救你啦)."""
    before = [c["t"] for c in cues if c["e"] <= t + 0.1][-n:]
    text = ("，" if lang == "zh" else "、" if lang == "ja" else " ").join(x.strip() for x in before)
    return text[-80:]


def _prompt(lang, context) -> str | None:
    parts = [names.whisper_hint() or ZH_HINT.get(lang or "", ""), context]
    return " ".join(p for p in parts if p) or None


def _rescue(model, audio, state, control, lang_opt, s, voice_only=False):
    """Speech the first pass gave no line to (quick talk, shouting, talk over loud music) and lines spread
    thin over a long time: each piece is listened to again on its own (no voice detector, the lines before it
    as context), and the lines found are put in their place; a thin line is replaced by what is found inside
    it. A piece that comes back as a made-up line is tried once more without the context.
    Any problem: the first pass is kept as it is."""
    try:
        regions = speech_regions(audio, voice_only=voice_only)
    except Exception as e:  # noqa: BLE001
        log.warning("Second listen skipped (%s)", e)
        return
    lang = state["lang"] or (None if lang_opt == "auto" else lang_opt)
    code = state["lang"] or lang or ""
    suspects = [c for c in state["cues"] if thin(c, code)]
    solid = [c for c in state["cues"] if not thin(c, code)]
    parts = missed_parts(regions, solid)
    if not parts:
        return
    total = len(audio) / RATE
    found, new_all = 0, []
    _HEARD.pass_name = "2nd"
    log.info("Second listen: %d piece(s), %.0f s of speech with no line, %d thin line(s)", len(parts),
             sum(b - a for a, b in parts), len(suspects))

    def listen(piece, a0, prompt, temperature):
        kw = dict(language=lang, task="transcribe", beam_size=max(5, int(s["whisper_beam"])), best_of=5,
                  word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt,
                  vad_filter=False, no_speech_threshold=0.95, hallucination_silence_threshold=None)
        if temperature is not None:
            kw["temperature"] = temperature
        segments, _info = model.transcribe(piece, **kw)
        got, made_up = [], 0
        for seg in segments:
            # a voice is known to be here: only clear noise is thrown away (shouting is often unsure)
            if seg.avg_logprob < -1.5 or (seg.no_speech_prob > 0.85 and seg.avg_logprob < -0.9):
                _heard_seg(seg, a0, "DROPPED", "second listen: unsure (lp < -1.5, or no_speech > 0.85 "
                                               "and lp < -0.9)")
                continue
            # a door, a step, a bump left in the voice track comes back as a word or two Whisper is not sure of
            # ("干杯", "不", "老狗"); real short lines heard again are sure ("看吧" -0.5, "能吃吗" -0.2)
            if size_of(hal.norm(seg.text or ""), code) <= (SHORT_CHARS if code in NO_SPACE else SHORT_WORDS) \
                    and seg.avg_logprob < SHORT_LP:
                _heard_seg(seg, a0, "DROPPED", f"second listen: a short line Whisper is not sure of (lp < {SHORT_LP}): "
                                               "probably a sound heard as a word")
                continue
            cues = _segments_to_cues(seg, code, a0, True, sure_speech=True)
            if not cues:
                made_up += 1
            got += cues
        return got, made_up

    for i, (a, b) in enumerate(parts):
        control.check()
        control.progress((i + 1) / len(parts), f"Listening again to missed speech {i + 1}/{len(parts)}")
        a0, b0 = max(0.0, a - RESCUE_PAD), min(total, b + RESCUE_PAD)
        piece = audio[int(a0 * RATE):int(b0 * RATE)]
        if len(piece) < RATE * 0.3:
            continue
        context = _context(solid + new_all, a, code)
        said = {hal.norm(x) for x in re.split(r"[，、]| ", context) if hal.norm(x)} if code in NO_SPACE else set()
        try:
            got, made_up = listen(piece, a0, _prompt(lang, context), None)
            if not got and made_up:                     # a made-up line: once more, no context, a bit looser
                _HEARD.pass_name = "2nd+"
                got, _ = listen(piece, a0, _prompt(lang, ""), [0.2, 0.4, 0.6])
                _HEARD.pass_name = "2nd"
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Second listen at %.1f s failed (%s)", a, e)
            continue
        new = []
        for c in got:
            if c["e"] <= a0 or c["s"] >= b0 or _covered(c, solid) or _covered(c, new_all) or _covered(c, new):
                _heard_note(c["s"], c["e"], "DROPPED", "second listen: same speech as a line we have", c["t"])
            elif hal.norm(c["t"]) in said:
                _heard_note(c["s"], c["e"], "DROPPED", "second listen: only repeats the line before (context)",
                            c["t"])
            else:
                new.append(c)
        for c in new:
            log.info("Second listen found %.1f: %s", c["s"], c["t"][:80])
        new_all += new
        found += len(new)
    gone = [c for c in suspects if any(n["s"] < c["e"] and n["e"] > c["s"] for n in new_all)]
    for c in gone:
        _heard_note(c["s"], c["e"], "REPLACED", f"spread over {c['e'] - c['s']:.0f} s: replaced by the lines "
                                                "heard again inside it", c["t"])
    state["cues"] = [c for c in state["cues"] if not any(c is g for g in gone)] + new_all
    state["cues"].sort(key=lambda c: c["s"])
    log.info("Second listen: %d line(s) added, %d thin line(s) replaced", found, len(gone))


# ------------------------------------------------------------------ the 'heard' log
def _ts(t: float) -> str:
    m, sec = divmod(max(0.0, t), 60)
    return f"{int(m) // 60:d}:{int(m) % 60:02d}:{sec:04.1f}" if m >= 60 else f"{int(m):02d}:{sec:04.1f}"


def _heard_note(s0, e0, kind, note="", text="", lp=None, nsp=None, cr=None):
    rows = getattr(_HEARD, "rows", None)
    if rows is not None:
        rows.append({"s": s0, "e": e0, "kind": kind, "note": note, "t": text, "lp": lp, "nsp": nsp, "cr": cr,
                     "pass": getattr(_HEARD, "pass_name", "1st")})


def _heard_seg(seg, offset, kind, note=""):
    _heard_note(seg.start + offset, seg.end + offset, kind, note, (seg.text or "").strip(),
                seg.avg_logprob, seg.no_speech_prob, seg.compression_ratio)


class _WhisperSkips(logging.Handler):
    """Whisper's own decisions (a piece skipped as 'no speech', a retry because it was unsure) go to the
    heard log, not to freesub.log."""
    KEEP = ("No speech threshold", "threshold is not met", "VAD filter removed")

    def __enter__(self):
        self.lg = logging.getLogger("faster_whisper")
        self.old = self.lg.level, self.lg.propagate
        self.lg.setLevel(logging.DEBUG)
        self.lg.propagate = False
        self.lg.addHandler(self)
        return self

    def __exit__(self, *a):
        self.lg.removeHandler(self)
        self.lg.setLevel(self.old[0])
        self.lg.propagate = self.old[1]

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if any(k in msg for k in self.KEEP):
            skips = getattr(_HEARD, "skips", None)
            if skips is not None and len(skips) < 500:
                skips.append(msg)


def heard_path(project: Project) -> str:
    from ..config import logs_dir
    return os.path.join(logs_dir(), f"heard - {os.path.splitext(os.path.basename(project.video))[0]}.txt")


def _write_heard(project, cues, wav, lang, params):
    """logs\\heard - <video>.txt: every piece Whisper wrote and what became of it (KEPT / DROPPED and why),
    and the places the voice detector hears speech but no line was made (NO LINE). For finding out whether a
    line was not heard, or heard and thrown away."""
    rows = list(getattr(_HEARD, "rows", None) or [])
    try:
        try:
            regions = speech_regions(audio_step.load(wav), voice_only=bool(params.get("voice_only")))
            gaps = missed_parts(regions, [{"s": c.start, "e": c.end} for c in cues], min_len=0.4, pad=0.05)
        except Exception as e:  # noqa: BLE001
            gaps = []
            log.info("Heard log: voice detector not run (%s)", e)
        for a, b in gaps:
            rows.append({"s": a, "e": b, "kind": "NO LINE", "note": "the voice detector hears speech here, "
                         "no subtitle line covers it", "t": "", "lp": None, "nsp": None, "cr": None, "pass": ""})
        rows.sort(key=lambda r: (r["s"], r["kind"] == "NO LINE"))   # what Whisper wrote, then the gap
        s = settings()
        out = [f"FreeSub: what Whisper heard in {os.path.basename(project.video)}",
               f"model {params.get('model')} · language {lang or '?'} · beam {params.get('beam')} · voice detector "
               f"{'on (' + str(s['whisper_vad_threshold']) + ')' if params.get('vad') else 'off'} · voice separated "
               f"from music: {'yes' if params.get('voice_only') else 'no'} · filter {'on' if s['whisper_filter'] else 'off'}",
               "lp = how sure Whisper is (0 = sure, -1 = unsure)  ns = chance it is not speech  cr = repetition",
               ""]
        if not getattr(_HEARD, "ran", False):
            out += ["(Whisper did not listen again this time: the lines were rebuilt from the words it heard before,",
                    " so only the NO LINE places are listed.)", ""]
        for r in rows:
            num = "" if r["lp"] is None else f"lp {r['lp']:5.2f}  ns {r['nsp']:.2f}  cr {r['cr']:.1f}  "
            head = f"{_ts(r['s'])}-{_ts(r['e'])}  {r['kind']:<8} {r['pass']:<4}  {num}"
            out.append(head + (r["t"] or ""))
            if r["note"]:
                out.append(" " * 26 + "-> " + r["note"])
        skips = getattr(_HEARD, "skips", None) or []
        if skips:
            out += ["", "Whisper's own decisions (times are inside the detector's speech, not video times):"]
            out += ["  " + m for m in skips]
        n = {k: sum(r["kind"] == k for r in rows) for k in ("KEPT", "DROPPED", "NO LINE", "REPLACED")}
        out += ["", f"{n['KEPT']} kept · {n['DROPPED']} dropped · {n['REPLACED']} thin lines replaced · "
                    f"{n['NO LINE']} places with speech and no line · "
                    f"{len(cues)} subtitle lines in the end"]
        path = heard_path(project)
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(out) + "\n")
        log.info("What Whisper heard: %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("Heard log not written (%s)", e)


def forget_words(project: Project):
    """The ↻ button: what Whisper heard last time is forgotten, so the speech is listened to again
    from the start (not only the lines rebuilt from the saved words)."""
    for name in (WORDS, PARTIAL):
        try:
            os.remove(project.path(name))
        except OSError:
            pass


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

