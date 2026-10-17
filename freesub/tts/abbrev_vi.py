"""Vietnamese abbreviations read as words by the narration ("v.v." -> "vân vân").

Only the spoken text changes; the subtitles keep the abbreviation. Each rule needs the dot (or a known whole
form), so ordinary words are never touched.
"""
import re

_B = r"(?<![\w.])"          # not inside a word / another abbreviation
_E = r"(?!\w)"

RULES = [
    (rf"{_B}v\.\s?v\.*", "vân vân"),                       # v.v.  v.v...  v. v.
    (rf"{_B}TP\.?\s?HCM{_E}", "thành phố Hồ Chí Minh"),
    (rf"{_B}TP\.?\s?Hồ Chí Minh{_E}", "thành phố Hồ Chí Minh"),
    (rf"{_B}TP\.\s?", "thành phố "),
    (rf"{_B}PGS\.\s?", "phó giáo sư "),
    (rf"{_B}GS\.\s?", "giáo sư "),
    (rf"{_B}TS\.\s?", "tiến sĩ "),
    (rf"{_B}ThS\.\s?", "thạc sĩ "),
    (rf"{_B}BS\.\s?", "bác sĩ "),
    (rf"{_B}KS\.\s?", "kỹ sư "),
    (rf"{_B}SĐT{_E}", "số điện thoại"),
    (rf"{_B}CLB{_E}", "câu lạc bộ"),
    (rf"{_B}THPT{_E}", "trung học phổ thông"),
    (rf"{_B}THCS{_E}", "trung học cơ sở"),
    (rf"{_B}ĐH{_E}", "đại học"),
    (rf"{_B}Ko{_E}|{_B}ko{_E}|{_B}k0{_E}", "không"),
    (r"\s&\s", " và "),
]
_COMPILED = [(re.compile(p, re.IGNORECASE if p.startswith(_B + "v") else 0), r) for p, r in RULES]


def expand(text: str) -> str:
    ended = text.rstrip().endswith(".")
    for rx, rep in _COMPILED:
        text = rx.sub(rep, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if ended and text and text[-1].isalnum():      # "..., v.v." ended the sentence: keep its full stop
        text += "."
    return text
