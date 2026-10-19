"""Find and replace inside subtitle lines (no window code, so it can be tested alone).

Taken from VisionBox's Find / Replace bar: the same options and the same special words.
* Whole word      : the match may not touch a letter, a digit or "_" (letters of any language count).
* Match case      : upper / lower case must be the same.
* Start with      : type the beginning of a line (e.g. "(nam)") and the WHOLE line that starts like that is
                    the match, so one Replace removes it. Always ignores case.
* "^##" as the text to find: every line that is only punctuation ("...", "?!", "-").

A subtitle can have several lines; a match is always inside ONE line of it.
"""
import re
import unicodedata
from dataclasses import dataclass

PUNCT_ONLY = "^##"
SRC, TR = "src", "tr"                       # the two columns: Original, Translation


@dataclass(frozen=True)
class Match:
    row: int                                # subtitle number (0 = first)
    kind: str                               # SRC | TR
    line: int                               # line inside that subtitle
    start: int                              # position inside that line
    end: int

    def key(self):
        """Order of reading: subtitle by subtitle, original before translation, then line, then position."""
        return (self.row, 0 if self.kind == SRC else 1, self.line, self.start)


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def is_word_char(ch: str) -> bool:
    """Letter, digit, "_" or a combining mark (so a Vietnamese letter written in two parts stays in one word)."""
    return bool(ch) and (ch.isalnum() or ch == "_" or unicodedata.category(ch).startswith("M"))


def is_punct_only(line: str) -> bool:
    return bool(line) and any(_is_punct(c) for c in line) and all(c.isspace() or _is_punct(c) for c in line)


def line_matches(text: str, needle: str, case=False, whole=False, start_with=False):
    """[(line, start, end)] for one subtitle's text."""
    out = []
    if not needle or not text:
        return out
    lines = text.split("\n")
    if needle == PUNCT_ONLY:
        return [(i, 0, len(l)) for i, l in enumerate(lines) if is_punct_only(l)]
    if start_with:
        low = needle.lower()
        return [(i, 0, len(l)) for i, l in enumerate(lines) if l.lower().startswith(low)]
    rx = re.compile(re.escape(needle), 0 if case else re.I)
    for i, line in enumerate(lines):
        pos = 0
        while True:
            m = rx.search(line, pos)
            if not m:
                break
            s, e = m.span()
            if whole and (is_word_char(line[s - 1] if s else "") or is_word_char(line[e] if e < len(line) else "")):
                pos = s + 1                 # inside a longer word: look a little further
                continue
            out.append((i, s, e))
            pos = max(e, s + 1)
    return out


def find_all(src_texts, tr_texts, needle: str, kinds=(SRC, TR), case=False, whole=False, start_with=False):
    """Every match in the shown subtitles, in reading order."""
    found = []
    if not needle:
        return found
    for kind, texts in ((SRC, src_texts), (TR, tr_texts)):
        if kind not in kinds:
            continue
        for row, text in enumerate(texts):
            for ln, s, e in line_matches(text, needle, case, whole, start_with):
                found.append(Match(row, kind, ln, s, e))
    found.sort(key=Match.key)
    return found


def replace_in_text(text: str, spans, replacement: str) -> str:
    """spans: [(line, start, end)] of ONE subtitle. Replaced from right to left, so the positions stay right.
    Lines that were touched are tidied: when the replacement is empty the double spaces it leaves are
    closed and a line that became empty is dropped."""
    lines = text.split("\n")
    touched = {}
    for ln, s, e in spans:
        touched.setdefault(ln, []).append((s, e))
    for ln, items in touched.items():
        line = lines[ln]
        for s, e in sorted(items, reverse=True):
            line = line[:s] + replacement + line[e:]
        if not replacement.strip():
            line = re.sub(r"[ \t]{2,}", " ", line).strip()
        lines[ln] = line
    if not replacement.strip():
        lines = [l for i, l in enumerate(lines) if l or i not in touched]
    return "\n".join(lines)
