"""English word -> how a Vietnamese speaker would say it, written in Vietnamese ("Steve" -> "Xờ-típ").

The English pronunciation comes from espeak-ng (bundled inside piper-tts, offline). Its IPA is cut into
syllables that Vietnamese allows: consonant clusters get their own short "ờ" syllable, final consonants
Vietnamese does not have are replaced (s/th -> t, f/v -> p, l -> n), stop-final syllables take the sắc tone.
The spelling of the word nudges vowels the way Vietnamese readers expect ("John" -> Giôn, not Gian).
"""
import logging
import re
import threading
import unicodedata

log = logging.getLogger(__name__)

VOWELS = ["aɪ", "eɪ", "oʊ", "əʊ", "aʊ", "ɔɪ", "ɪə", "eə", "ɛə", "ʊə",
          "i", "ɪ", "ᵻ", "e", "ɛ", "æ", "a", "ɑ", "ɐ", "ɒ", "ɔ", "ʌ", "ə", "ɚ", "ɜ", "ʊ", "u", "o"]
V = {"aɪ": "ai", "eɪ": "ây", "oʊ": "ô", "əʊ": "ô", "aʊ": "ao", "ɔɪ": "oi", "ɪə": "ia", "eə": "e", "ɛə": "e",
     "ʊə": "ua", "i": "i", "ɪ": "i", "ᵻ": "i", "e": "ê", "ɛ": "e", "æ": "a", "a": "a", "ɑ": "a", "ɐ": "a",
     "ɒ": "o", "ɔ": "ô", "ʌ": "â", "ə": "ơ", "ɚ": "ơ", "ɜ": "ơ", "ʊ": "u", "u": "u", "o": "ô"}
ONSET = {"p": "p", "b": "b", "t": "t", "d": "đ", "k": "c", "ɡ": "g", "g": "g", "f": "ph", "v": "v", "θ": "th",
         "ð": "đ", "s": "x", "z": "d", "ʃ": "s", "ʒ": "gi", "h": "h", "m": "m", "n": "n", "ŋ": "ng", "l": "l",
         "ɹ": "r", "r": "r", "j": "d", "w": "w", "dʒ": "gi", "tʃ": "ch", "ɾ": "t", "ʔ": "", "x": "kh"}
CODA = {"p": "p", "t": "t", "k": "c", "m": "m", "n": "n", "ŋ": "ng", "s": "t", "θ": "t", "ʃ": "t", "f": "p",
        "v": "p", "b": "p", "d": "t", "ɡ": "c", "g": "c", "l": "l", "dʒ": "ch", "tʃ": "ch", "z": "t", "ɾ": "t",
        "ʒ": "t", "ð": "t", "x": "c"}
CONS = sorted(set(ONSET) | {"ɹ", "r"}, key=len, reverse=True)
TOKENS = sorted(VOWELS + CONS + ["ˈ", "ˌ"], key=len, reverse=True)
DIPH = ("ai", "ây", "ao", "oi")
STOPS = ("p", "t", "c", "ch")
SAC, HUYEN = "́", "̀"
VN_VOWELS = "aăâeêioôơuưy"

_ESPEAK = {"p": None, "lock": threading.Lock(), "tried": False}


def available() -> bool:
    return _phonemizer() is not None


def _phonemizer():
    if not _ESPEAK["tried"]:
        _ESPEAK["tried"] = True
        try:
            from piper.phonemize_espeak import EspeakPhonemizer
            _ESPEAK["p"] = EspeakPhonemizer()
        except Exception as e:  # noqa: BLE001
            log.info("espeak (piper-tts) not available, English words use the dictionary only: %s", e)
    return _ESPEAK["p"]


def ipa(word: str) -> list[str]:
    p = _phonemizer()
    if p is None:
        return []
    with _ESPEAK["lock"]:
        raw = "".join("".join(s) for s in p.phonemize("en", word))     # British English: closest to how
    s = unicodedata.normalize("NFC", raw).replace("ː", "")               # Vietnamese read English
    out, i = [], 0
    while i < len(s):
        for t in TOKENS:
            if s.startswith(t, i):
                out.append(t)
                i += len(t)
                break
        else:
            i += 1
    return out


# ------------------------------------------------------------------ Vietnamese spelling helpers
def put_tone(syl: str, tone: str) -> str:
    """Place a tone mark the way Vietnamese spelling does (modern style)."""
    if not tone:
        return syl
    base = syl
    idx = [i for i, c in enumerate(base) if c.lower() in VN_VOWELS]
    if not idx:
        return syl
    start = 0
    if base.lower().startswith(("qu", "gi")) and len(idx) > 1 and idx[0] == 1:
        idx = idx[1:]
    vs = "".join(base[i].lower() for i in idx)
    # vowel with a hat / horn takes the mark; else the 2nd of a glide pair (oa, oe, uy, uê) or before a coda
    pick = None
    for k, i in enumerate(idx):
        if base[i].lower() in "âêôơưă":
            pick = i
    if pick is None:
        ends_vowel = idx[-1] == len(base) - 1
        if len(idx) == 1:
            pick = idx[0]
        elif vs[:2] in ("oa", "oe", "uy", "ue") or not ends_vowel:
            pick = idx[1] if len(idx) > 1 else idx[0]
        else:
            pick = idx[0]
    del start
    return unicodedata.normalize("NFC", base[:pick + 1] + tone + base[pick + 1:])


def _fix_onset(o: str, nuc: str) -> str:
    front = nuc[:1] in ("i", "e", "ê", "y")
    if o == "c" and front:
        return "k"
    if o == "g" and front:
        return "gh"
    if o == "ng" and front:
        return "ngh"
    return o


def _join(o: str, n: str, c: str):
    """onset + nucleus + coda as valid Vietnamese spelling, or None if the coda cannot stay."""
    if o == "w" and n[:1] in ("u", "ô", "o"):      # "wood" -> út, "won" -> ôn
        o = ""
    if o == "w":                                   # w + vowel -> glide: oa, oe, uy, uơ
        o, n = "", {"a": "oa", "e": "oe", "i": "uy", "ê": "uê", "ơ": "uơ", "ây": "uây", "ai": "oai"}.get(n, "u" + n)
        if n == "uô":
            n = "ô"
    if o == "gi" and n.startswith("i"):
        n = n[1:] or "i"
        if n == "i":
            return "gi" + c if c else "gi"
    if o == "d" and n.startswith("u"):              # "you" -> diu
        n = "i" + n
    if c == "l":                                     # final l: "mail" -> meo, "Bill" -> Bin
        c = {"e": "o", "ê": "u", "ây": "o"}.get(n, "n")
        if n == "ây":
            n = "e"
        if c in ("o", "u"):
            return _fix_onset(o, n) + n + c
    if n == "ây" and c in ("p", "t", "c", "ch"):
        n = "ê"
    if n in DIPH and c:
        if c in ("p", "t", "c", "ch"):
            return None                              # "Mike" -> Mai-cơ (the coda gets its own syllable)
        if n == "ây":
            n = "ê"                                  # "James" -> Giêm
        else:
            c = ""
    if n == "ia" and c:
        n = "iê"
    if n == "ua" and c:
        n = "uô"
    if c == "c" and n in ("i", "ê"):
        c = "ch"
    if c == "ng" and n in ("i", "ê"):
        c = "nh"
    if n == "ơ" and c in ("c", "ng", "ch", "nh"):
        n = "â"
    if n == "o" and c in ("ch", "nh"):
        n = "ô"
    if c == "ch" and n not in ("a", "i", "ê"):
        c = "c"
    return _fix_onset(o, n) + n + c


def _hints(word: str, n_syl: int):
    """Vowel letters of the word per syllable (only when they line up): ('o', 'r') = 'o' followed by r."""
    low = word.lower()
    groups = [(m.group(0), low[m.end():m.end() + 1]) for m in re.finditer(r"[aeiouy]+", low)]
    if len(groups) > n_syl and low.endswith(("e", "es", "ed")) and groups[-1][0] == "e":
        groups = groups[:-1]
    if groups and groups[0][0] == "y" and len(groups) > n_syl:
        groups = groups[1:]
    return groups if len(groups) == n_syl else [None] * n_syl


def _nucleus(v: str, hint, coda_is_stop: bool, coda_l: bool = False) -> str:
    n = V[v]
    if not hint:
        return n
    g, nxt = hint
    if v in ("ɑ", "ɒ", "ɔ"):
        if "o" in g:
            return "o" if coda_is_stop else "ô"
        if "a" in g and v != "ɔ":
            return "a"
    if v == "ʌ" and g == "o":
        return "ơ"                                   # love -> lớp, mother -> mơ-...
    if v in ("ə", "ɐ", "ɚ"):
        if nxt == "r" or v == "ɚ":
            return "ơ"
        if coda_l:
            return "ơ"                                   # Marvel -> Ma-vơn, not Ma-veo
        return {"a": "a", "o": "ô", "i": "i", "y": "i", "u": "u", "e": "e"}.get(g[-1], n)
    return n


def respell(word: str) -> str:
    """'Steve' -> 'Xờ-típ'. Empty string when espeak is not available."""
    ph = ipa(word)
    if not ph:
        return ""
    syls, cons, stress = [], [], 0
    for t in ph:
        if t in ("ˈ", "ˌ"):
            stress = 2 if t == "ˈ" else 1
            continue
        if t in V:
            if cons[-1:] == ["j"] and len(cons) >= 2:
                if t in ("u", "ʊ", "ʊə"):          # "tube" -> túp, "news" -> niu
                    cons = cons[:-1]
                else:                              # "Daniel" -> Đa-ni-ơn
                    if syls and len(cons) >= 3 and cons[0] in CODA and not syls[-1]["coda"]:
                        syls[-1]["coda"].append(cons.pop(0))
                    syls.append({"onset": cons[:-1], "stress": 0, "v": "i", "coda": []})
                    cons = []
            if syls and len(cons) >= 2 and cons[0] in CODA and not syls[-1]["coda"]:
                syls[-1]["coda"].append(cons.pop(0))
            syls.append({"onset": cons, "stress": stress, "v": t, "coda": []})
            cons, stress = [], 0
        elif t in ONSET or t in ("ɹ", "r"):
            cons.append(t)
    if not syls:
        return ""
    syls[-1]["coda"].extend(cons)

    # double letters keep both sides: Anna -> An-na, Potter -> Pót-tơ, Mickey -> Mích-ki
    low = word.lower()
    doubled = {c for c, cc in (("n", "nn"), ("m", "mm"), ("t", "tt"), ("p", "pp"), ("k", "ck"), ("k", "cc"))
               if cc in low}
    for a, b in zip(syls, syls[1:]):
        if not a["coda"] and len(b["onset"]) == 1 and b["onset"][0] in doubled and a["v"] not in ("aɪ", "eɪ", "aʊ", "ɔɪ", "oʊ", "əʊ"):
            a["coda"].append(b["onset"][0])

    hints = _hints(word, len(syls))
    out = []

    def o_of(s):
        for c in s["onset"][:-1]:
            out.append(put_tone(ONSET.get(c, "") + "ơ", HUYEN))
        return ONSET.get(s["onset"][-1], "") if s["onset"] else ""

    for k, s in enumerate(syls):
        cd = next((c for c in s["coda"] if c not in ("ɹ", "r")), "")
        if cd in ("s", "z") and (s["v"] in ("aɪ", "eɪ", "oʊ", "əʊ", "aʊ", "ɔɪ") or
                                 (s["v"] in ("ə", "ɚ", "ɐ") and k == len(syls) - 1 and low.endswith("rs"))):
            cd = ""
        if cd == "l" and s["v"] in ("ə", "ɐ") and k == len(syls) - 1 and low.endswith("le"):
            out.append(put_tone(_join(o_of(s), "ô", ""), HUYEN))     # Apple -> Áp-pồ, Google -> Gu-gồ
            continue
        o = o_of(s)                                         # clusters: "Steve" -> Xờ-típ
        coda = CODA.get(cd, "")
        nuc = _nucleus(s["v"], hints[k], coda in STOPS, cd == "l")
        syl = _join(o, nuc, coda)
        tail = []
        if syl is None:
            syl = _join(o, nuc, "")
            tail.append(_join(ONSET.get(cd, ""), "ơ", "") or "")
        tone = SAC if syl.endswith(STOPS) else ""
        out.append(put_tone(syl, tone))
        out += [t for t in tail if t]
    text = "-".join(x for x in out if x)
    return text[:1].upper() + text[1:] if word[:1].isupper() else text
