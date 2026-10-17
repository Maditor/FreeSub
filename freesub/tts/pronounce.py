"""Make Vietnamese text sayable by a Vietnamese voice: foreign words and names are replaced by how Vietnamese
speakers say them ("Mike" -> "Mai cơ"), so the whole sentence keeps ONE voice.

Lookup order for each foreign word (or multi-word name):
  1. your corrections                     (pronunciations.json, "user")
  2. built-in list                        (loanwords_vi.txt: ok -> ô-kê, Google -> gu-gồ ...)
  3. AI readings learned earlier          (pronunciations.json, "ai")
  4. letters for acronyms                 (FBI -> ép-bi-ai)
  5. Japanese romaji rules                (Sasuke -> Xa-xư-kê)
  6. English pronunciation rules          (espeak IPA -> Vietnamese, ipa_vi.py)
Words met by the rules are remembered ("auto") so they show up in the dictionary window to be corrected,
and can be sent to an AI in one batch later (ask_ai) for a better reading.
"""
import json
import logging
import os
import re
import threading
import time

from ..config import data_dir
from . import ipa_vi, romaji_vi
from .vi_detect import foreign_spans, is_acronym, is_vn_syllable, strip_tones

log = logging.getLogger(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
LETTERS = {"a": "ây", "b": "bi", "c": "xi", "d": "đi", "e": "i", "f": "ép", "g": "gi", "h": "ếch", "i": "ai",
           "j": "giây", "k": "kây", "l": "eo", "m": "em", "n": "en", "o": "âu", "p": "pi", "q": "kiu", "r": "a",
           "s": "ét", "t": "ti", "u": "diu", "v": "vi", "w": "đáp-bờ-liu", "x": "ích", "y": "oai", "z": "dét"}
SOURCES = ("user", "builtin", "ai", "letters", "romaji", "rules", "none")
MAX_AUTO = 5000


def _key(text: str) -> str:
    return " ".join(re.sub(r"[-‐–]", " ", text.lower()).split())


def load_builtin() -> dict:
    out = {}
    try:
        with open(os.path.join(HERE, "loanwords_vi.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() and v.strip():
                        out[_key(k)] = v.strip()
    except OSError as e:
        log.warning("Cannot read the built-in pronunciation list: %s", e)
    return out


def valid_reading(reading: str) -> bool:
    """Every syllable must be a Vietnamese syllable (so the Vietnamese voice can say it)."""
    parts = [p for p in re.split(r"[\s\-]+", reading.strip()) if p]
    return bool(parts) and all(is_vn_syllable(p) for p in parts)


class Pronouncer:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(data_dir(), "pronunciations.json")
        self.lock = threading.RLock()
        self.builtin = load_builtin()
        self.data = {"user": {}, "ai": {}, "auto": {}}
        self._dirty = False
        self.load()

    # ------------------------------------------------------------ storage
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            for k in self.data:
                if isinstance(d.get(k), dict):
                    self.data[k] = {_key(a): str(b) for a, b in d[k].items()}
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            if not self._dirty:
                return
            auto = self.data["auto"]
            if len(auto) > MAX_AUTO:
                for k in list(auto)[: len(auto) - MAX_AUTO]:
                    auto.pop(k, None)
            tmp = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1, sort_keys=True)
                os.replace(tmp, self.path)
                self._dirty = False
            except OSError as e:
                log.warning("Cannot save %s: %s", self.path, e)

    def set_user(self, word: str, reading: str):
        with self.lock:
            k = _key(word)
            if reading.strip():
                self.data["user"][k] = reading.strip()
            else:
                self.data["user"].pop(k, None)
            self._dirty = True
        self.save()

    def forget(self, word: str):
        with self.lock:
            k = _key(word)
            for part in ("user", "ai", "auto"):
                self.data[part].pop(k, None)
            self._dirty = True
        self.save()

    # ------------------------------------------------------------ lookup
    def phrase_keys(self) -> set:
        return {k for d in (self.data["user"], self.builtin, self.data["ai"]) for k in d}

    def lookup(self, word: str, remember=True) -> tuple[str, str]:
        """(reading, source) for one foreign word or name."""
        k = _key(word)
        for src, table in (("user", self.data["user"]), ("builtin", self.builtin), ("ai", self.data["ai"])):
            if k in table:
                return table[k], src
        if is_acronym(word):
            return "-".join(LETTERS.get(c, c) for c in word.lower()), "letters"
        if romaji_vi.is_romaji(word):
            r, src = romaji_vi.respell(word), "romaji"
        else:
            r, src = ipa_vi.respell(word), "rules"
        if not r:
            return word, "none"
        if remember:
            with self.lock:
                if self.data["auto"].get(k) != r:
                    self.data["auto"][k] = r
                    self._dirty = True
        return r, src

    def respell_text(self, text: str, for_display=False):
        """Vietnamese text -> (text to speak, [(original, reading, source), ...])."""
        # dictionary words that are also Vietnamese ("no", "hi") are NOT forced: only words found foreign
        spans = foreign_spans(text)
        if not spans:
            return text, []
        phrases = sorted((k for k in self.phrase_keys() if " " in k), key=lambda k: -len(k.split()))
        out, changes, i, last = [], [], 0, 0
        while i < len(spans):
            s, e, w = spans[i]
            # longest known multi-word name starting here ("iron man", "oh my god")
            took = 0
            for ph in phrases:
                n = len(ph.split())
                if i + n <= len(spans):
                    seg = text[s:spans[i + n - 1][1]]
                    gaps_ok = all(text[spans[j][1]:spans[j + 1][0]].strip(" -") == ""
                                  for j in range(i, i + n - 1))
                    if gaps_ok and _key(seg) == ph:
                        reading, src = self.lookup(seg)
                        out.append(text[last:s])
                        out.append(self._fmt(reading, for_display, seg))
                        changes.append((seg, reading, src))
                        last, took = spans[i + n - 1][1], n
                        break
            if took:
                i += took
                continue
            reading, src = self.lookup(w)
            out.append(text[last:s])
            out.append(self._fmt(reading, for_display, w) if src != "none" else w)
            changes.append((w, reading, src))
            last = e
            i += 1
        out.append(text[last:])
        self.save()
        return "".join(out), changes

    @staticmethod
    def _fmt(reading: str, for_display: bool, original: str = "") -> str:
        if original[:1].isupper() and not original.isupper():
            reading = reading[:1].upper() + reading[1:]       # names stay names
        return reading if for_display else reading.replace("-", " ")

    # ------------------------------------------------------------ AI
    def pending_for_ai(self, limit=60) -> list[str]:
        """Words the rules guessed that no AI or user has read yet."""
        with self.lock:
            return [k for k in self.data["auto"]
                    if k not in self.data["user"] and k not in self.data["ai"] and k not in self.builtin][:limit]

    def ask_ai(self, chat, words=None) -> int:
        """chat(prompt) -> str (any LLM). Learns readings for the pending words; returns how many were kept."""
        words = words or self.pending_for_ai()
        if not words:
            return 0
        prompt = (
            "Bạn là chuyên gia phiên âm cho giọng đọc tiếng Việt. Với mỗi từ hoặc tên nước ngoài dưới đây, "
            "hãy viết cách người Việt thường đọc nó, bằng chữ quốc ngữ có đủ dấu thanh, các âm tiết nối bằng "
            "dấu gạch nối, mỗi âm tiết phải là một âm tiết tiếng Việt viết đúng chính tả. Tên tiếng Nhật đọc theo "
            "romaji (Sasuke -> Xa-xư-kê). Tên tiếng Anh đọc theo cách quen thuộc ở Việt Nam (Michael -> Mai-cồn, "
            "Google -> Gu-gồ).\n"
            "Chỉ trả về một JSON object dạng {\"từ\": \"cách đọc\"}, không giải thích.\n\n"
            + "\n".join(words))
        t0 = time.time()
        reply = chat(prompt) or ""
        m = re.search(r"\{.*\}", reply, re.S)
        if not m:
            raise RuntimeError("AI reply has no JSON")
        data = json.loads(m.group(0))
        kept = 0
        with self.lock:
            for w, r in data.items():
                k = _key(str(w))
                r = str(r).strip()
                if k in words and r and valid_reading(r):
                    self.data["ai"][k] = r
                    kept += 1
                elif k in words:
                    log.info("AI reading rejected for %r: %r", w, r)
            self._dirty = True
        self.save()
        log.info("AI pronunciations: %d/%d kept in %.1f s", kept, len(words), time.time() - t0)
        return kept

    # ------------------------------------------------------------ table for the dictionary window
    def rows(self) -> list[tuple[str, str, str]]:
        """(word, reading, source) of everything known, user first."""
        seen, out = set(), []
        with self.lock:
            for src in ("user", "ai", "auto"):
                for k, v in sorted(self.data[src].items()):
                    if k not in seen:
                        seen.add(k)
                        out.append((k, v, "rules" if src == "auto" else src))
        for k, v in sorted(self.builtin.items()):
            if k not in seen:
                out.append((k, v, "builtin"))
        return out


_ONE = {"p": None}


def pronouncer() -> Pronouncer:
    if _ONE["p"] is None:
        _ONE["p"] = Pronouncer()
    return _ONE["p"]


__all__ = ["Pronouncer", "pronouncer", "valid_reading", "strip_tones", "SOURCES"]
