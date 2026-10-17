"""Step: split the soundtrack into voice and background (music + effects) before speech recognition.

Music and sound effects are the main reason Whisper mishears or makes up lines in films and anime, so the
'Best' quality runs Whisper on the voice alone. The background track (music.wav) is kept for dubbing later.

Model: MDX-Net "Kim Vocal 2" (from the Ultimate Vocal Remover public models), run with ONNX Runtime
(DirectML on the dedicated graphics card, else CPU). The spectrogram code is plain numpy.
Files in the project folder:
    vocals.wav   16 kHz mono voice (what Whisper hears)
    music.wav    44.1 kHz stereo background without the voice
"""
import logging
import os
import time
import wave

import numpy as np

from ..config import models_dir
from ..core import dxgi, ffmpeg
from ..core.jobs import Cancelled
from ..core.models import download
from ..core.project import Project
from ..log import step_timer

log = logging.getLogger(__name__)

MODELS = {
    "Kim_Vocal_2": {
        "url": "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
        "size": "64 MB", "n_fft": 7680, "dim_f": 3072, "dim_t": 256, "compensate": 1.009,
    },
}
DEFAULT = "Kim_Vocal_2"
RATE = 44100
HOP = 1024
VOCALS = "vocals.wav"
MUSIC = "music.wav"
BATCH = 2


def model_path(name=DEFAULT) -> str:
    return os.path.join(models_dir(), "separation", name + ".onnx")


def model_ready(name=DEFAULT) -> bool:
    return os.path.isfile(model_path(name))


def download_model(name=DEFAULT, control=None):
    dest = model_path(name)
    if os.path.isfile(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    def prog(got, total):
        if control is not None:
            control.progress(got / total if total else None, f"Voice separation model · {got / 1e6:.0f} MB")
    download(MODELS[name]["url"], dest, progress=prog, control=control)


# ------------------------------------------------------------------ ONNX session
_SESSION = {"key": None, "s": None, "device": ""}


_GPU_LOST = {"lost": False}   # the graphics card was reset (Windows "GPU hung"): CPU until FreeSub restarts


def _session(name, use_gpu=True):
    use_gpu = use_gpu and not _GPU_LOST["lost"]
    key = (name, use_gpu)
    if _SESSION["key"] == key:
        return _SESSION["s"], _SESSION["device"]
    import onnxruntime as ort
    providers, device = ["CPUExecutionProvider"], "CPU"
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if use_gpu and "DmlExecutionProvider" in ort.get_available_providers():
        gid = dxgi.best_id()
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = [("DmlExecutionProvider", {"device_id": str(gid or 0)}), "CPUExecutionProvider"]
        cards = {g["id"]: g["name"] for g in dxgi.adapters()}
        device = f"GPU ({cards.get(gid or 0, 'DirectML')})"
    elif use_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
        providers, device = ["CUDAExecutionProvider", "CPUExecutionProvider"], "GPU (CUDA)"
    try:
        s = ort.InferenceSession(model_path(name), so, providers=providers)
    except Exception as e:  # noqa: BLE001
        if len(providers) == 1:
            raise
        log.warning("Separation on the GPU failed (%s): using the CPU", e)
        s = ort.InferenceSession(model_path(name), ort.SessionOptions(), providers=["CPUExecutionProvider"])
        device = "CPU"
    _SESSION.update(key=key, s=s, device=device)
    return s, device


# ------------------------------------------------------------------ STFT (same layout as the MDX models expect)
class Spec:
    def __init__(self, n_fft, dim_f, dim_t):
        self.n_fft, self.dim_f, self.dim_t = n_fft, dim_f, dim_t
        self.n_bins = n_fft // 2 + 1
        self.chunk = HOP * (dim_t - 1)
        n = np.arange(n_fft)
        self.window = (0.5 - 0.5 * np.cos(2 * np.pi * n / n_fft)).astype(np.float32)   # periodic Hann

    def stft(self, x):
        """x [B, 2, chunk] -> [B, 4, dim_f, dim_t]  (L.re, L.im, R.re, R.im)."""
        pad = self.n_fft // 2
        xp = np.pad(x, ((0, 0), (0, 0), (pad, pad)), mode="reflect")
        idx = np.arange(self.dim_t)[:, None] * HOP + np.arange(self.n_fft)[None, :]
        frames = xp[:, :, idx] * self.window                      # [B, 2, T, n_fft]
        spec = np.fft.rfft(frames, axis=-1)                        # [B, 2, T, F]
        spec = spec.transpose(0, 1, 3, 2)[:, :, :self.dim_f]      # [B, 2, F, T]
        out = np.stack([spec.real, spec.imag], axis=2)            # [B, 2, 2, F, T]
        return out.reshape(x.shape[0], 4, self.dim_f, self.dim_t).astype(np.float32)

    def istft(self, s):
        """[B, 4, dim_f, dim_t] -> [B, 2, chunk]."""
        b = s.shape[0]
        s = s.reshape(b, 2, 2, self.dim_f, self.dim_t)
        spec = np.zeros((b, 2, self.n_bins, self.dim_t), dtype=np.complex64)
        spec[:, :, :self.dim_f] = s[:, :, 0] + 1j * s[:, :, 1]
        frames = np.fft.irfft(spec.transpose(0, 1, 3, 2), n=self.n_fft, axis=-1) * self.window   # [B,2,T,n_fft]
        pad = self.n_fft // 2
        total = HOP * (self.dim_t - 1) + self.n_fft
        out = np.zeros((b, 2, total), dtype=np.float32)
        norm = np.zeros(total, dtype=np.float32)
        w2 = self.window ** 2
        for t in range(self.dim_t):
            a = t * HOP
            out[:, :, a:a + self.n_fft] += frames[:, :, t]
            norm[a:a + self.n_fft] += w2
        out /= np.maximum(norm, 1e-8)
        return out[:, :, pad:pad + self.chunk]


# ------------------------------------------------------------------ wav helpers
class WavReader:
    def __init__(self, path):
        self.w = wave.open(path, "rb")
        self.n = self.w.getnframes()
        self.ch = self.w.getnchannels()

    def read(self, start, count):
        """[2, count] float32, zeros outside the file."""
        out = np.zeros((2, count), dtype=np.float32)
        a, b = max(0, start), min(self.n, start + count)
        if b > a:
            self.w.setpos(a)
            x = np.frombuffer(self.w.readframes(b - a), dtype=np.int16).astype(np.float32) / 32768.0
            x = x.reshape(-1, self.ch).T
            if self.ch == 1:
                x = np.vstack([x, x])
            out[:, a - start:a - start + x.shape[1]] = x[:2]
        return out

    def close(self):
        self.w.close()


def _wav_writer(path):
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(RATE)
    return w


def _to_int16(x):
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16).T.tobytes()


# ------------------------------------------------------------------ the step
def run(project: Project, control, force=False, name=DEFAULT, use_gpu=True) -> str:
    """Make vocals.wav (+ music.wav). Returns the vocals path."""
    out_v, out_m = project.path(VOCALS), project.path(MUSIC)
    if not force and project.is_done("separate", VOCALS) and project.step("separate").get("model") == name:
        control.progress(1.0, "Voice already separated")
        return out_v
    if not model_ready(name):
        control.status("Downloading the voice separation model…")
        download_model(name, control)
    project.ensure()
    info = project.meta.get("info") or ffmpeg.probe(project.video)
    duration = info.get("duration") or 0
    mix = project.path("mix44.part.wav")
    v44 = project.path("vocals44.part.wav")
    m_tmp = project.path("music.part.wav")
    with step_timer("separate", project.video):
        project.mark("separate", "running", model=name)
        try:
            args = ["-i", project.video, "-vn", "-sn", "-dn"]
            stream = project.meta.get("audio_stream")
            args += ["-map", f"0:{stream}" if stream is not None else "0:a:0"]
            args += ["-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le", mix]
            ffmpeg.run(args, duration=duration, control=control, label="Reading the soundtrack")
            device = _separate(mix, v44, m_tmp, name, use_gpu, control)
            control.progress(None, "Preparing the voice for speech recognition…")
            ffmpeg.run(["-i", v44, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_v], duration=duration,
                       control=control, label="Preparing voice")
            os.replace(m_tmp, out_m)
        except Cancelled:
            project.mark("separate", "stopped", model=name)
            raise
        except Exception as e:
            project.mark("separate", "failed", model=name, error=str(e)[:300])
            raise
        finally:
            for p in (mix, v44, m_tmp):
                try:
                    os.remove(p)
                except OSError:
                    pass
        project.mark("separate", "done", model=name, file=VOCALS, music=MUSIC, device=device)
    control.progress(1.0, "Voice separated")
    return out_v


def _separate(src, out_vocals, out_music, name, use_gpu, control) -> str:
    m = MODELS[name]
    spec = Spec(m["n_fft"], m["dim_f"], m["dim_t"])
    session, device = _session(name, use_gpu)
    inp = session.get_inputs()[0].name
    # one piece per GPU call keeps every call short: Windows resets a card whose call takes too long
    batch = 1 if "GPU" in device else BATCH
    trim = m["n_fft"] // 2
    gen = spec.chunk - 2 * trim
    r = WavReader(src)
    wv, wm = _wav_writer(out_vocals), _wav_writer(out_music)
    total = r.n
    t0 = time.time()
    warned = False
    control.status(f"Separating the voice on {device}…")
    try:
        pos = 0
        while pos < total:
            control.check()
            starts = [pos + k * gen for k in range(batch) if pos + k * gen < total]
            mixes = np.stack([r.read(s - trim, spec.chunk) for s in starts])          # [B, 2, chunk]
            try:
                pred = session.run(None, {inp: spec.stft(mixes)})[0]
            except Exception as e:  # noqa: BLE001
                if "GPU" not in device:
                    raise
                # e.g. 887A0006 "The GPU will not respond to more commands" (Windows reset the card): go on
                # from this very piece on the CPU
                log.warning("The graphics card stopped during voice separation (%s). Going on with the CPU "
                            "from %.0f%%.", str(e)[-120:], 100 * pos / max(1, total))
                _GPU_LOST["lost"] = True
                _SESSION.update(key=None, s=None, device=None)
                session, device = _session(name, False)
                inp = session.get_inputs()[0].name
                batch = BATCH
                control.status(f"The graphics card stopped: going on with the CPU from {100 * pos / total:.0f}%…")
                continue
            voc = spec.istft(pred)[:, :, trim:trim + gen] * m["compensate"]
            for k, s in enumerate(starts):
                n = min(gen, total - s)
                v = voc[k][:, :n]
                orig = mixes[k][:, trim:trim + n]
                wv.writeframes(_to_int16(v))
                wm.writeframes(_to_int16(orig - v))
            pos = starts[-1] + gen
            done = min(pos, total) / total
            speed = (min(pos, total) / RATE) / max(0.1, time.time() - t0)
            slow = ""
            if speed < 2 and time.time() - t0 > 30 and "GPU" in device:
                slow = " · slow: on battery, or another app using the graphics card?"
                if not warned:
                    warned = True
                    log.warning("Separation is slow on %s (%.1f× real time, usually 10× or more). The laptop may "
                                "be on battery / power saving, or another program is using the graphics card.",
                                device, speed)
            control.progress(done, f"Separating voice {done * 100:.0f}% · {speed:.1f}× real time · {device}{slow}")
    finally:
        r.close()
        wv.close()
        wm.close()
    log.info("Separation on %s: %.1f s for %.1f s of audio", device, time.time() - t0, total / RATE)
    return device
