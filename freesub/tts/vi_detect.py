"""Find the words a Vietnamese voice would read wrong: English / foreign words and names inside Vietnamese text.

A word is Vietnamese when it carries Vietnamese diacritics, or when (without diacritics) it can still be a
Vietnamese syllable: onset + rhyme from the tables below. "Mike", "Facebook", "Steve", "John" cannot; "Mai",
"anh", "con" can. Capitalised words next to a foreign word belong to the same name ("Tom Cruise", "Iron Man").
"""
import re
import unicodedata

TONE_MARKS = {"̀", "́", "̃", "̉", "̣"}       # huyền sắc ngã hỏi nặng
VN_LETTERS = set("ăâđêôơưĂÂĐÊÔƠƯ")

ONSETS = ["ngh", "ng", "nh", "gh", "gi", "kh", "ph", "th", "tr", "ch", "qu",
          "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x", ""]
RHYMES = set("""
a ac ach ai am an ang anh ao ap at au ay
ă ăc ăm ăn ăng ăp ăt
â âc âm ân âng âp ât âu ây
e ec em en eng eo ep et
ê êch êm ên ênh êp êt êu
i ia ich im in inh ip it iu iêc iêm iên iêng iêp iêt iêu
o oa oac oach oai oam oan oang oanh oao oap oat oay oăc oăm oăn oăng oăp oăt oc oe oen oeo oet oi om on ong
ooc oong op ot
ô ôc ôi ôm ôn ông ôp ôt
ơ ơi ơm ơn ơp ơt
u ua uân uâng uât uây uc uê uêch uênh uêt ui um un ung uôc uôi uôm uôn uông uôt up ut uy uya uych uyên uyêt
uynh uyp uyt uyu
ư ưa ưc ưi ưm ưn ưng ưt ưu ươc ươi ươm ươn ương ươp ươt ươu
y yêm yên yêng yêt yêu
""".split())

# no diacritics, but almost never meant as Vietnamese inside a Vietnamese sentence
ALWAYS_FOREIGN = {"hot", "top", "vip", "pop", "rap", "pin", "set", "net"}
# short English words that are also Vietnamese syllables: English only when stuck between English words
EN_SHORT = {"so", "to", "a", "an", "in", "on", "me", "be", "he", "she", "we", "go", "do", "no", "oh", "my",
            "the", "i", "is", "it", "am", "at", "as", "or", "up", "us", "him", "her", "his", "can", "man", "men"}
WORD = re.compile(r"[A-Za-zÀ-ỹđĐ]+(?:['’][A-Za-z]+)?")


def strip_tones(word: str) -> str:
    d = unicodedata.normalize("NFD", word)
    return unicodedata.normalize("NFC", "".join(c for c in d if c not in TONE_MARKS))


def has_vn_marks(word: str) -> bool:
    d = unicodedata.normalize("NFD", word)
    return any(c in TONE_MARKS for c in d) or any(c in VN_LETTERS for c in word)


def is_vn_syllable(word: str) -> bool:
    w = strip_tones(word.lower())
    if not w or any(c in "fjwz" for c in w):
        return False
    for on in ONSETS:
        if w.startswith(on):
            rest = w[len(on):]
            if rest in RHYMES or (on == "gi" and (rest == "" or "i" + rest in RHYMES)):
                return True
    return False


def is_acronym(word: str) -> bool:
    return 2 <= len(word) <= 6 and word.isupper() and word.isascii()


def foreign_spans(text: str, known=None) -> list[tuple[int, int, str]]:
    """(start, end, word) of each word to respell. `known` = dictionary keys (lower case) that count as
    foreign even when they look like Vietnamese syllables."""
    known = known or set()
    toks = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(text)]
    flags = []
    for s, e, w in toks:
        low = w.lower()
        if has_vn_marks(w):
            flags.append(0)                       # Vietnamese for sure
        elif is_acronym(w) or not is_vn_syllable(w) or low in ALWAYS_FOREIGN or low in known:
            flags.append(2)                       # foreign
        else:
            flags.append(1)                       # could be either
    # a capitalised "could be either" word next to a foreign word is part of the same name
    changed = True
    while changed:
        changed = False
        for i, (s, e, w) in enumerate(toks):
            if flags[i] != 1 or not w[:1].isupper():
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(toks) and flags[j] == 2 and _adjacent(text, toks, min(i, j)):
                    flags[i] = 2
                    changed = True
                    break
    # a lower-case "could be either" word between two foreign words: "I love you so much"
    for i in range(1, len(toks) - 1):
        if flags[i] == 1 and toks[i][2].lower() in EN_SHORT and flags[i - 1] == 2 and flags[i + 1] == 2 and \
                _adjacent(text, toks, i - 1) and _adjacent(text, toks, i):
            flags[i] = 2
    return [t for t, f in zip(toks, flags) if f == 2]


def _adjacent(text, toks, i) -> bool:
    gap = text[toks[i][1]:toks[i + 1][0]]
    return gap.strip(" -") == ""
