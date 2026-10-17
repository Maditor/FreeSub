"""Women's or men's voice for each subtitle line: guessed from the audio, corrected by the user's marks.

Automatic guess (when the subtitles are made):
  * voice pitch per 25 ms frame with the YIN method (more robust than a plain autocorrelation peak), on the
    voice alone (vocals.wav) when the voice was separated from the music;
  * frames are pooled per speaker when speakers were told apart (one decision per person, from all of their
    speech), else per line;
  * the women / men boundary adapts to the video: when the pitches form two clear groups the boundary sits
    between them, otherwise 165 Hz (with an "unsure" band around it).

The user's marks (the ♀/♂ column of the subtitle table, voices.json) win, and teach the rest:
  * a line of the same speaker as a marked line gets its mark;
  * otherwise the voice print (speakers.py, CAM++) of each line is compared with the marked lines: a line
    that sounds like a marked one takes its gender; with marks of both kinds, the nearer group wins;
  * otherwise the pitch boundary learned from the marked lines is used.
Result in subs.src.json: "gender" (used by translation and narration), "auto", "how" (you / learned / auto).
"""
import json
import logging
import os

import numpy as np

from ..core.project import SRC_SRT, Project
from ..core.subtitle import load_srt
from . import audio as audio_step

log = logging.getLogger(__name__)
SIDE = "subs.src.json"
MARKS = "voices.json"
PRINTS = "voices.npz"        # voice print of each line (cache)
RATE = 16000
SPLIT_HZ = 165.0
UNSURE = 0.06                # +-6 % around the boundary: unsure (when the boundary is not learned)
SAME_VOICE = 0.45            # voice prints this similar: the same person
NEAR_MARGIN = 0.04           # nearer group must be at least this much nearer
CLEAR = 1.12                 # a pitch 12 % or more away from the boundary decides by itself


# ------------------------------------------------------------------ pitch
def pitch_frames(a: np.ndarray, sr=RATE) -> np.ndarray:
    """Pitch (Hz) of every voiced frame (YIN, 60-450 Hz)."""
    frame, hop = int(0.04 * sr), int(0.0125 * sr)
    if a is None or len(a) < frame * 2:
        return np.zeros(0, dtype=np.float32)
    n = 1 + (len(a) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    fr = a[idx].astype(np.float64)
    energy = (fr ** 2).mean(axis=1)
    keep = energy > max(np.percentile(energy, 35), 1e-6)
    fr = fr[keep]
    if len(fr) < 3:
        return np.zeros(0, dtype=np.float32)
    fr = fr - fr.mean(axis=1, keepdims=True)
    w = frame // 2
    tau_min, tau_max = int(sr / 450), min(w - 1, int(sr / 60))
    # difference function d(tau) = r(0) + r_tau(0) - 2 r(tau), via FFT autocorrelation
    head = np.fft.rfft(fr[:, :w], 2 * frame, axis=1)          # r(tau) = sum over j < w of x[j] x[j + tau]
    full = np.fft.rfft(fr, 2 * frame, axis=1)
    ac = np.fft.irfft(np.conj(head) * full, axis=1)[:, :w]
    sq = np.cumsum(fr ** 2, axis=1)
    e0 = sq[:, w - 1:w]                                       # energy of the first w samples
    shifted = sq[:, w - 1 + np.arange(w)] - np.concatenate([np.zeros((len(fr), 1)), sq[:, :w - 1]], axis=1)
    d = e0 + shifted - 2 * ac
    d[:, 0] = 0
    cum = np.cumsum(d[:, 1:], axis=1) / np.arange(1, w)[None, :]
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] / np.maximum(cum, 1e-12)
    seg = cmnd[:, tau_min:tau_max]
    below = seg < 0.15
    first = np.where(below.any(axis=1), below.argmax(axis=1), seg.argmin(axis=1))
    best = seg[np.arange(len(seg)), first]
    tau = (first + tau_min).astype(np.float64)
    rows = np.arange(len(seg))
    inner = (first > 0) & (first < seg.shape[1] - 1)            # parabolic interpolation: a finer period
    a_, b_, c_ = (seg[rows[inner], first[inner] - 1], seg[rows[inner], first[inner]],
                  seg[rows[inner], first[inner] + 1])
    den = a_ - 2 * b_ + c_
    tau[inner] += np.where(np.abs(den) > 1e-12, 0.5 * (a_ - c_) / np.where(den == 0, 1, den), 0.0)
    voiced = best < 0.3
    f0 = sr / tau[voiced]
    return f0.astype(np.float32)


def _median(values):
    return float(np.median(values)) if len(values) else None


def boundary(units):
    """units: [(median Hz, weight)]. Returns (boundary Hz, learned?) where learned = two clear groups."""
    pts = [(np.log(f), w) for f, w in units if f]
    if len(pts) >= 2:
        x = np.array([p for p, _ in pts])
        w = np.array([q for _, q in pts], dtype=np.float64)
        lo, hi = np.percentile(x, 15), np.percentile(x, 85)
        for _ in range(30):                                   # weighted 2-means in log Hz
            a = np.abs(x - lo) <= np.abs(x - hi)
            if a.all() or (~a).all():
                break
            lo, hi = np.average(x[a], weights=w[a]), np.average(x[~a], weights=w[~a])
        f_lo, f_hi = np.exp(lo), np.exp(hi)
        # two real groups: far enough apart (a fifth or more) and on the two sides of the usual boundary
        if f_hi / max(f_lo, 1) > 1.35 and f_lo < 185 and f_hi > 150:
            return float(np.sqrt(f_lo * f_hi)), True
    return SPLIT_HZ, False


def decide(f0, split, learned):
    if not f0:
        return None
    if learned:
        return "f" if f0 >= split else "m"
    if f0 >= split * (1 + UNSURE):
        return "f"
    if f0 <= split * (1 - UNSURE):
        return "m"
    return None


def voice_file(project) -> str | None:
    for name in ("vocals.wav", "audio.wav"):
        if project.has(name):
            return project.path(name)
    return None


def auto_genders(wav, cues, speakers=None):
    """Guess for each cue -> (genders, per-line pitch). speakers: speaker id per cue (or None)."""
    n = len(cues)
    pitches = [np.zeros(0, dtype=np.float32)] * n
    if wav and os.path.isfile(wav):
        for i, c in enumerate(cues):
            try:
                pitches[i] = pitch_frames(audio_step.load(wav, c.start, c.end))
            except Exception:  # noqa: BLE001
                pass
    line_f0 = [_median(p) for p in pitches]
    speakers = speakers or [None] * n
    groups = {}
    for i, s in enumerate(speakers):
        groups.setdefault(("s", s) if s is not None else ("l", i), []).append(i)
    unit_f0 = {k: _median(np.concatenate([pitches[i] for i in v])) for k, v in groups.items()}
    weights = {k: sum(len(pitches[i]) for i in v) for k, v in groups.items()}
    split, learned = boundary([(unit_f0[k], weights[k]) for k in groups])
    out = [None] * n
    for k, v in groups.items():
        g = decide(unit_f0[k], split, learned)
        for i in v:
            out[i] = g
    log.info("Voices: boundary %.0f Hz (%s), %d women's / %d men's lines", split,
             "from this video" if learned else "default", out.count("f"), out.count("m"))
    return out, line_f0


# ------------------------------------------------------------------ marks and what they teach
def _key(start: float) -> str:
    return str(int(round(start * 1000)))


def load_marks(project) -> dict:
    try:
        with open(project.path(MARKS), encoding="utf-8") as f:
            return dict(json.load(f).get("marks", {}))
    except (OSError, ValueError):
        return {}


def save_marks(project, marks: dict):
    project.ensure()
    tmp = project.path(MARKS) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"marks": marks}, f)
    os.replace(tmp, project.path(MARKS))


def read_side(project) -> list:
    try:
        with open(project.path(SIDE), encoding="utf-8") as f:
            side = json.load(f)
        return side if isinstance(side, list) else []
    except (OSError, ValueError):
        return []


def _write_side(project, side):
    tmp = project.path(SIDE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)
    os.replace(tmp, project.path(SIDE))


def _prints(project, cues, wav, control=None):
    """Voice print of every line (cached in voices.npz), or None when the model or the audio is missing."""
    from . import speakers
    starts = np.array([round(c.start, 3) for c in cues])
    try:
        data = np.load(project.path(PRINTS))
        if data["starts"].shape == starts.shape and np.allclose(data["starts"], starts) and \
                data.get("wav", np.array("")).item() == os.path.basename(wav):
            return data["embs"]
    except Exception:  # noqa: BLE001
        pass
    if not speakers.model_ready():
        try:
            speakers.download_model(control)
        except Exception as e:  # noqa: BLE001
            log.info("No voice prints (%s): marks teach by speaker and pitch only", e)
            return None
    emb = speakers.Embedder()
    full = audio_step.load(wav)
    out = np.zeros((len(cues), 0), dtype=np.float32)
    rows = []
    for c in cues:
        a = full[int(c.start * RATE):int(c.end * RATE)]
        if len(a) < int(speakers.MIN_PRINT * RATE):           # too short: a little of what is around it
            mid = int((c.start + c.end) / 2 * RATE)
            half = int(speakers.MIN_PRINT * RATE / 2)
            a = full[max(0, mid - half):mid + half]
        try:
            rows.append(emb(a) if len(a) >= RATE // 4 else None)
        except Exception:  # noqa: BLE001
            rows.append(None)
    dim = next((len(r) for r in rows if r is not None), 0)
    if not dim:
        return None
    out = np.stack([r if r is not None else np.zeros(dim, dtype=np.float32) for r in rows]).astype(np.float32)
    try:
        np.savez(project.path(PRINTS), starts=starts, embs=out, wav=np.array(os.path.basename(wav)))
    except OSError:
        pass
    return out


def apply_marks(project, control=None) -> list:
    """Work out every line's voice from the guesses and the marks; written to subs.src.json.
    Returns [(gender, how)] per line of subs.src.srt."""
    if not project.has(SRC_SRT):
        return []
    cues = load_srt(project.src_srt)
    side = read_side(project)
    if not side:                     # subtitles from OCR or an .srt of yours: guess from the audio now
        wav = voice_file(project)
        guess, f0 = auto_genders(wav, cues) if wav else ([None] * len(cues), [None] * len(cues))
        side = [{"start": round(c.start, 3), "end": round(c.end, 3), "gender": g, "auto": g, "how": "auto",
                 "f0": round(h, 1) if h else None} for c, g, h in zip(cues, guess, f0)]
    by_start = {_key(x.get("start", -1)): x for x in side}
    rows = []
    for c in cues:
        x = by_start.get(_key(c.start)) or {"start": round(c.start, 3), "end": round(c.end, 3)}
        x.setdefault("auto", x.get("gender") if x.get("how") in (None, "auto") else x.get("auto"))
        rows.append(x)
    marks = load_marks(project)
    marked = {i: marks[_key(c.start)] for i, c in enumerate(cues) if _key(c.start) in marks}
    n = len(cues)
    final = [(r.get("auto"), "auto") for r in rows]
    if marked:
        # 1) the same speaker as a marked line
        by_spk = {}
        for i, g in marked.items():
            s = rows[i].get("speaker")
            if s is not None:
                by_spk.setdefault(s, []).append(g)
        spk_g = {s: max(set(v), key=v.count) for s, v in by_spk.items()}
        # 2) voice prints
        wav = voice_file(project)
        embs = _prints(project, cues, wav, control) if wav else None
        if embs is not None:
            ok = np.linalg.norm(embs, axis=1) > 0
            sims = embs @ embs.T
        # 3) pitch boundary learned from the marks
        f_hz = [rows[i].get("f0") for i, g in marked.items() if g == "f" and rows[i].get("f0")]
        m_hz = [rows[i].get("f0") for i, g in marked.items() if g == "m" and rows[i].get("f0")]
        learned_split = float(np.sqrt(np.median(f_hz) * np.median(m_hz))) if f_hz and m_hz else None
        fs = [i for i, g in marked.items() if g == "f"]
        ms = [i for i, g in marked.items() if g == "m"]
        if learned_split:
            split = learned_split
        else:                                         # the boundary of this video (as for the guesses)
            split, _ = boundary([(r.get("f0"), 1) for r in rows if r.get("f0")])
        for i in range(n):
            if i in marked:
                final[i] = (marked[i], "you")
                continue
            s = rows[i].get("speaker")
            if s is not None and s in spk_g:          # the same person as a marked line
                final[i] = (spk_g[s], "learned")
                continue
            f0 = rows[i].get("f0")
            how = "learned" if learned_split else "auto"
            if f0 and abs(np.log(f0 / split)) >= np.log(CLEAR):   # clearly high or low: the pitch says it
                g = "f" if f0 >= split else "m"
                final[i] = (g, how if learned_split or g != rows[i].get("auto") else "auto")
                continue
            if embs is not None and ok[i]:            # close to the boundary: whose voice does it sound like
                sf = max((sims[i, j] for j in fs if ok[j]), default=-1.0)
                sm = max((sims[i, j] for j in ms if ok[j]), default=-1.0)
                best, g = (sf, "f") if sf >= sm else (sm, "m")
                if best >= SAME_VOICE:
                    final[i] = (g, "learned")
                    continue
                if fs and ms:
                    cf = embs[[j for j in fs if ok[j]]].mean(axis=0) if any(ok[j] for j in fs) else None
                    cm = embs[[j for j in ms if ok[j]]].mean(axis=0) if any(ok[j] for j in ms) else None
                    if cf is not None and cm is not None:
                        x, y = float(embs[i] @ cf), float(embs[i] @ cm)
                        if abs(x - y) >= NEAR_MARGIN:
                            final[i] = ("f" if x > y else "m", "learned")
                            continue
            if learned_split and f0:
                final[i] = ("f" if f0 >= learned_split else "m", "learned")
    for r, (g, how) in zip(rows, final):
        r["gender"], r["how"] = g, how
    _write_side(project, rows)
    return final


def table_rows(project, cues, marks=True) -> list:
    """[(gender, how)] for the subtitle table's rows (matched by start time). marks=False: what a line is
    without its own mark (learned from the other marks, or guessed)."""
    by_start = {_key(x.get("start", -1)): x for x in read_side(project)}
    mk = load_marks(project) if marks else {}
    out = []
    for c in cues:
        k = _key(c.start)
        if k in mk:
            out.append((mk[k], "you"))
            continue
        x = by_start.get(k) or {}
        if not marks and x.get("how") == "you":
            out.append((x.get("auto"), "auto"))
            continue
        out.append((x.get("gender"), x.get("how") or "auto"))
    return out


def move_mark(project, old_start: float, new_start: float):
    marks = load_marks(project)
    g = marks.pop(_key(old_start), None)
    if g:
        marks[_key(new_start)] = g
        save_marks(project, marks)


def set_mark(project: Project, start: float, gender):
    """gender: 'f', 'm' or None (back to the guess)."""
    marks = load_marks(project)
    k = _key(start)
    if gender:
        marks[k] = gender
    else:
        marks.pop(k, None)
    save_marks(project, marks)
