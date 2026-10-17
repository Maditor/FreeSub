"""Who speaks when: split subtitle lines where the speaker changes.

Two people talking quickly often end up in one Whisper segment ("Are you coming? No, I'm tired."). Here every
stretch of speech between short pauses gets a voice print (a speaker embedding from the CAM++ model, Chinese +
English trained, 28 MB, runs on the CPU with onnxruntime). The prints are grouped by voice (agglomerative
clustering on cosine similarity), every word gets its speaker, and a line is cut where the speaker changes.
Each line also keeps its speaker number (subs.src.json) for later steps.
"""
import logging
import os

import numpy as np

from ..config import models_dir
from ..core.models import download

log = logging.getLogger(__name__)
RATE = 16000
MODEL = {"name": "campplus_zh_en", "size": "28 MB",
         "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
         "sha": "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"}
VERSION = 2              # bump when the cutting rules change: lines are cut again (Whisper is not run)
SAME = 0.33           # average cosine similarity above which two groups are the same voice
MIN_PRINT = 0.6        # seconds: shorter pieces are too short for a reliable voice print


def model_path() -> str:
    return os.path.join(models_dir(), "speakers", MODEL["name"] + ".onnx")


def model_ready() -> bool:
    return os.path.isfile(model_path())


def download_model(control=None):
    dest = model_path()
    if os.path.isfile(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    def prog(got, total):
        if control is not None:
            control.progress(got / total if total else None, f"Speaker model · {got / 1e6:.0f} MB")
    download(MODEL["url"], dest, sha=MODEL["sha"], progress=prog, control=control)


# ------------------------------------------------------------------ features (Kaldi-style 80-bin fbank)
_MEL = None


def _mel_banks(n_fft=512, n_mels=80, low=20.0, high=8000.0):
    global _MEL
    if _MEL is not None:
        return _MEL
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731
    lo, hi = mel(low), mel(high)
    centers = np.linspace(lo, hi, n_mels + 2)
    fft_mel = mel(np.arange(n_fft // 2) * RATE / n_fft)
    banks = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(n_mels):
        l, c, r = centers[m], centers[m + 1], centers[m + 2]
        up = (fft_mel - l) / (c - l)
        down = (r - fft_mel) / (r - c)
        banks[m, :n_fft // 2] = np.maximum(0.0, np.minimum(up, down))
    _MEL = banks
    return banks


def fbank(wave: np.ndarray) -> np.ndarray:
    """[T, 80] log mel filterbank, 25 ms frames every 10 ms (like Kaldi / the model's training)."""
    flen, shift, n_fft = 400, 160, 512
    if len(wave) < flen:
        wave = np.pad(wave, (0, flen - len(wave)))
    n = 1 + (len(wave) - flen) // shift
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    fr = wave[idx].astype(np.float64)
    fr -= fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] * (1 - 0.97), fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(fr * win, n_fft, axis=1)) ** 2
    feats = np.log(np.maximum(spec @ _mel_banks().T, 1.1920929e-07)).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


class Embedder:
    def __init__(self):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(model_path(), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, wave: np.ndarray) -> np.ndarray:
        e = self.sess.run(None, {self.inp: fbank(wave)[None]})[0][0]
        return e / (np.linalg.norm(e) + 1e-9)


# ------------------------------------------------------------------ grouping voices
def cluster(embs: np.ndarray, threshold: float) -> np.ndarray:
    """Agglomerative clustering, average linkage on cosine similarity. Returns a label per row."""
    n = len(embs)
    if n == 0:
        return np.zeros(0, dtype=int)
    S = (embs @ embs.T).astype(np.float64)
    np.fill_diagonal(S, -np.inf)
    size = np.ones(n)
    alive = np.ones(n, dtype=bool)
    label = np.arange(n)
    best = S.argmax(axis=1)
    best_v = S[np.arange(n), best]
    while True:
        best_v[~alive] = -np.inf
        a = int(best_v.argmax())
        if best_v[a] < threshold:
            break
        b = int(best[a])
        if a == b or not alive[b]:
            row = S[a].copy()
            row[~alive] = -np.inf
            best[a] = int(row.argmax())
            best_v[a] = row[best[a]]
            continue
        row = (S[a] * size[a] + S[b] * size[b]) / (size[a] + size[b])
        row[a] = -np.inf
        S[a], S[:, a] = row, row
        S[b], S[:, b] = -np.inf, -np.inf
        size[a] += size[b]
        alive[b] = False
        label[label == b] = a
        # update the best partner of rows that pointed at a or b, and of a itself
        for i in np.where(alive & ((best == a) | (best == b)))[0].tolist() + [a]:
            r = S[i].copy()
            r[~alive] = -np.inf
            best[i] = int(r.argmax())
            best_v[i] = r[best[i]]
        better = alive & (S[:, a] > best_v)
        best[better] = a
        best_v[better] = S[better, a]
    _, out = np.unique(label, return_inverse=True)
    return out


def _units(words):
    """Stretches of speech between short pauses (a change of speaker nearly always falls on a pause);
    long stretches are cut every ~2.5 s at a word boundary."""
    units, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w[0] - words[cur[-1]][1]
            prev = words[cur[-1]][2].strip()
            if gap >= 0.15 or prev.endswith(("?", "!", ".", "？", "！", "。")) or \
                    w[1] - words[cur[0]][0] > 2.5:
                units.append(cur)
                cur = []
        cur.append(i)
    if cur:
        units.append(cur)
    return units


def label_words(audio: np.ndarray, words, embedder, control=None):
    """words: [(start, end, text)] in time order -> a speaker number per word (0, 1, ...)."""
    units = _units(words)
    prints, strong = [], []
    total = len(units)
    for k, u in enumerate(units):
        if control is not None and k % 20 == 0:
            control.check()
            control.progress(k / max(1, total), f"Telling the voices apart {k}/{total}")
        s, e = words[u[0]][0], words[u[-1]][1]
        dur = e - s
        if dur < 0.3:
            prints.append(None)
            continue
        if dur < MIN_PRINT:                                    # too short alone: take a little around it
            mid = (s + e) / 2
            s, e = mid - MIN_PRINT / 2, mid + MIN_PRINT / 2
        a, b = max(0, int((s - 0.05) * RATE)), min(len(audio), int((e + 0.05) * RATE))
        if b - a < int(0.25 * RATE):
            prints.append(None)
            continue
        prints.append(embedder(audio[a:b]))
        if dur >= MIN_PRINT:
            strong.append(k)
    unit_label = [None] * len(units)
    if strong:
        labels = cluster(np.stack([prints[k] for k in strong]), SAME)
        # a "speaker" heard for under 3 s in all is more likely noise: it joins the closest real one
        durs = {}
        for k, l in zip(strong, labels):
            durs[l] = durs.get(l, 0) + words[units[k][-1]][1] - words[units[k][0]][0]
        cents = {l: np.mean([prints[k] for k, m in zip(strong, labels) if m == l], axis=0) for l in durs}
        big = [l for l in durs if durs[l] >= 3.0] or list(durs)
        for l in cents:
            cents[l] /= np.linalg.norm(cents[l]) + 1e-9
        for k, l in zip(strong, labels):
            unit_label[k] = l if l in big else max(big, key=lambda m: float(prints[k] @ cents[m]))
        for k, p in enumerate(prints):                         # short pieces: the closest voice
            if unit_label[k] is None and p is not None:
                unit_label[k] = max(big, key=lambda m: float(p @ cents[m]))
        # number the speakers in order of first appearance: 0, 1, 2...
        order = {}
        for l in unit_label:
            if l is not None and l not in order:
                order[l] = len(order)
        unit_label = [order.get(l) if l is not None else None for l in unit_label]
    # pieces with no voice print take their neighbour's speaker
    last = None
    for k in range(len(units)):
        if unit_label[k] is None:
            unit_label[k] = last
        last = unit_label[k]
    nxt = 0
    for k in range(len(units) - 1, -1, -1):
        if unit_label[k] is None:
            unit_label[k] = nxt
        nxt = unit_label[k]
    out = [0] * len(words)
    for k, u in enumerate(units):
        for i in u:
            out[i] = unit_label[k]
    return out


SENTENCE_END = ("?", "!", ".", "…", "？", "！", "。", "”", '"')


def can_cut(words, i, pause=0.2) -> bool:
    """A line may only be cut before word i at the end of a sentence or at a clear pause: people do not take
    turns in the middle of "Do you speak | English?"."""
    prev = words[i - 1][2].strip()
    return prev.endswith(SENTENCE_END) or words[i][0] - words[i - 1][1] >= pause


def split_by_speaker(cues, word_labels, join, min_part=0.6):
    """cues: [{"s","e","t","w":[(s,e,text),...]}]; word_labels: one list per cue. Returns new cue dicts, cut
    where the speaker changes (only where a cut is allowed, see can_cut), each with "spk"."""
    out = []
    for c, labels in zip(cues, word_labels):
        words = c.get("w") or []
        if not words or not labels:
            out.append(dict(c, spk=None))
            continue
        runs = []                                   # [label, first, last]
        for i, l in enumerate(labels):
            if runs and runs[-1][0] == l:
                runs[-1][2] = i
            else:
                runs.append([l, i, i])
        # a change of speaker in the middle of a sentence is a mistake: the cut moves to the closest sentence end / pause nearby ("Do you speak English? | Yes…")
        fixed = [list(runs[0])]
        for r in runs[1:]:
            r = list(r)
            prev = fixed[-1]
            if not can_cut(words, r[1]):
                ahead = next((j for j in range(r[1] + 1, r[2] + 1) if can_cut(words, j)), None)
                back = next((j for j in range(r[1] - 1, prev[1], -1) if can_cut(words, j)), None)
                if ahead is not None and (back is None or ahead - r[1] <= r[1] - back):
                    prev[2], r[1] = ahead - 1, ahead
                elif back is not None:
                    prev[2], r[1] = back - 1, back
                else:                                # no place to cut: the longer part gives its speaker
                    long_prev = words[prev[2]][1] - words[prev[1]][0] >= words[r[2]][1] - words[r[1]][0]
                    fixed[-1] = [prev[0] if long_prev else r[0], prev[1], r[2]]
                    continue
            fixed.append(r)
        runs = [r for r in fixed if r[1] <= r[2]]
        merged = []
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
        # a very short run (one quick word) is more likely a mistake: it joins its neighbour
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for j, (l, a, b) in enumerate(runs):
                # a short reply that is a whole sentence ("No.", "Yes!") is a real turn: it stays
                whole = (a == 0 or can_cut(words, a)) and (b == len(words) - 1 or can_cut(words, b + 1))
                if words[b][1] - words[a][0] < min_part and not whole:
                    k = j - 1 if j > 0 else j + 1
                    lo, hi = min(runs[k][1], a), max(runs[k][2], b)
                    runs[k] = [runs[k][0], lo, hi]
                    del runs[j]
                    changed = True
                    break
            merged = []
            for r in runs:
                if merged and merged[-1][0] == r[0]:
                    merged[-1][2] = r[2]
                else:
                    merged.append(r)
            runs = merged
        if len(runs) == 1:
            out.append(dict(c, spk=runs[0][0]))
            continue
        for l, a, b in runs:
            ws = words[a:b + 1]
            text = join(ws)
            if text:
                out.append({"s": ws[0][0], "e": ws[-1][1], "t": text, "lp": c.get("lp"), "w": ws, "spk": l})
    return out
