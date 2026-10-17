"""Japanese names in romaji -> Vietnamese reading ("Sasuke" -> Xa-xư-kê, "Kakashi" -> Ka-ka-si).

English rules would read "Naruto" as Na-ru-tâu; Vietnamese viewers say the romaji syllable by syllable.
A word is treated as romaji only when it splits fully into Japanese syllables AND is clearly Japanese
(3+ syllables, or shi / chi / tsu / ky / ry / long vowels), so "Mike" or "Jake" stay English.
"""
import re

_ON = {"k": "c", "g": "g", "s": "x", "sh": "s", "ch": "ch", "j": "gi", "z": "d", "t": "t", "ts": "x", "d": "đ",
       "n": "n", "h": "h", "f": "ph", "b": "b", "p": "p", "m": "m", "y": "d", "r": "r", "w": "", "v": "v", "": ""}
_YO = {"ky": "k", "gy": "g", "ny": "n", "hy": "h", "by": "b", "py": "p", "my": "m", "ry": "r"}
_VW = {"a": "a", "i": "i", "u": "u", "e": "ê", "o": "ô"}
_MORA = re.compile(r"(ky|gy|ny|hy|by|py|my|ry|sh|ch|ts|[kgsztdnhfbpmyrwjv])?([aiueo])|(n)(?![aiueoy])|([kstpgdbcm])(?=\4)")
STRONG = re.compile(r"shi|chi|tsu|ky|ry|ou|uu|aa|ii|ei")


def morae(word: str):
    w = word.lower().replace("ō", "ou").replace("ū", "uu").replace("-", "")
    pos, out = 0, []
    while pos < len(w):
        m = _MORA.match(w, pos)
        if not m or m.end() == pos:
            return None
        out.append(m.groups())
        pos = m.end()
    return out


def is_romaji(word: str) -> bool:
    ms = morae(word)
    if not ms:
        return False
    vowels = sum(1 for _c, v, _n, _q in ms if v)
    return vowels >= 3 or bool(STRONG.search(word.lower()))


def _k(o: str, v: str) -> str:
    if o == "c" and v[:1] in ("i", "ê"):
        return "k"
    if o == "g" and v[:1] in ("i", "ê"):
        return "gh"
    return o


def respell(word: str) -> str:
    ms = morae(word)
    if not ms:
        return ""
    syl = []             # [onset, vowel, coda]
    for cons, vow, n, gem in ms:
        if n:                                   # syllabic n -> coda of the previous syllable
            if syl and not syl[-1][2]:
                syl[-1][2] = "n"
            else:
                syl.append(["", "", "n"])
            continue
        if gem:                                 # double consonant "kk", "tt": a stop on the previous syllable
            if syl and not syl[-1][2]:
                syl[-1][2] = {"k": "c", "c": "c", "t": "t", "s": "t", "p": "p", "g": "c", "d": "t", "b": "p", "m": "m"}[gem]
            continue
        cons = cons or ""
        # long vowels: "ou", "uu", "ei", "aa" -> one syllable
        if not cons and syl and syl[-1][1] and not syl[-1][2]:
            prev = syl[-1][1]
            if (prev, vow) in (("ô", "u"), ("u", "u"), ("iu", "u"), ("ê", "i"), ("a", "a"), ("i", "i"),
                               ("ô", "o"), ("ê", "e")):
                continue
            glide = {("a", "i"): "ai", ("u", "i"): "ui", ("ô", "i"): "ôi", ("ư", "i"): "ưi"}.get((prev, vow))
            if glide:
                syl[-1][1] = glide
                continue
        if cons in _YO:
            o = _ON.get(_YO[cons], "")
            if vow == "u":
                syl.append([o, "iu", ""])
            else:
                syl.append([o, "i", ""])
                syl.append(["", _VW[vow], ""])
            continue
        o = _ON.get(cons, "")
        v = _VW[vow]
        if vow == "u" and cons in ("s", "ts", "z"):
            v = "ư"                              # Sasuke -> Xa-xư-kê
        if cons == "w":
            v = {"a": "oa", "o": "ô"}.get(vow, v)
        if cons == "j" and v.startswith("i"):
            o, v = "gi", v[1:]                    # "ji" -> gi, not gii
        if cons == "y" and vow == "u":
            o, v = "d", "iu"                      # Yuki -> Diu-ki
        syl.append([o, v, ""])
    parts = []
    for o, v, c in syl:
        if v == "" and c == "n":
            parts.append("n")
            continue
        if v == "ê" and c == "c":
            c = "ch"
        if v == "i" and c == "c":
            c = "ch"
        s = (_k(o, v) + v + c) if not (o == "gi" and not v) else "gi" + c
        if c in ("c", "ch", "t", "p"):
            from .ipa_vi import SAC, put_tone
            s = put_tone(s, SAC)
        parts.append(s)
    text = "-".join(parts)
    return text[:1].upper() + text[1:] if word[:1].isupper() else text
