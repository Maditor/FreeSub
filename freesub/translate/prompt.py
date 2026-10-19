"""Subtitle translation prompt for the AI services: numbered lines in, numbered lines out (same count)."""
import re

from ..langs import name as lang_name


def build(lines, src, target, before=(), after=(), context="", glossary="", genders=None, style=""):
    """lines: texts to translate; before: [(source, translation)] already done; after: next source lines."""
    S = lang_name(src) if src and src != "auto" else "the original language"
    T = lang_name(target)
    parts = [f"You are a professional film subtitle translator. Translate {S} subtitles into natural, "
             f"spoken {T}."]
    if style.strip():
        parts.append("Translation style guide for this video (follow it; lines written \"original = "
                     "translation\" are fixed terms):\n" + style.strip()[:6000])
    if context.strip():
        parts.append("About this video (from the viewer: names, relationships, tone):\n" + context.strip()[:2000])
    if glossary.strip():
        parts.append("Always translate these names / terms exactly like this:\n" + glossary.strip()[:2000])
    if before:
        parts.append("Earlier lines, for context only (do NOT output them and do NOT repeat their translation):\n" +
                     "\n".join(f"- {s}  =>  {t}" for s, t in before))
    who = {"f": " (female voice)", "m": " (male voice)"}
    numbered = "\n".join(f"[{i + 1}]{who.get((genders or [None] * len(lines))[i] or '', '')} {l}"
                         for i, l in enumerate(lines))
    parts.append(f"Lines to translate:\n{numbered}")
    if after:
        parts.append("Next lines, for context only. They are NOT part of the lines to translate: never translate, copy or mention them:\n" + "\n".join(f"- {s}" for s in after))
    rules = [
        f"Output exactly {len(lines)} lines, in the same order, each starting with its number: [1] ..., [2] ...",
        "One input line = one output line. Never merge, split, skip or add lines. If one sentence is cut over "
        "several lines, each output line holds only the translation of its own line, in order: never repeat "
        "words already used in the previous line and never move words from one line to another.",
        f"Write short, natural spoken {T} like professional subtitles, not word-for-word. Keep each line about "
        "as long as the original so it can be read in time.",
        "The text comes from speech recognition or OCR and may have mistakes: guess the most plausible meaning.",
        "Keep the tone, emotion and punctuation style (..., ?!, !!).",
        "The marks '(male voice)' / '(female voice)' in front of a line only tell you who is speaking. Never "
        "write them, or a translation of them such as '(nam)', '(nữ)', '(giọng nam)', in your output.",
        "No notes, explanations, alternative translations, stage directions or sound descriptions, and nothing "
        "in (parentheses) or [brackets] unless the original line itself has them. Each output line is only the "
        "spoken words.",
        f"Output ONLY the numbered {T} lines, no notes, no original text.",
    ]
    if target == "vi":
        rules.append("Vietnamese pronouns: choose tôi/tao/tớ/mình/anh/em/cậu/mày/ông/bà/con/cháu... from the "
                     "speakers' age, relationship and mood (the voice gender helps), and keep them consistent "
                     "with the earlier lines.")
    parts.append("Rules:\n" + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules)))
    return "\n\n".join(parts)


def parse(text: str, n: int) -> list[str]:
    clean = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.I)
    clean = re.sub(r"```[a-z]*", "", clean)
    found = {}
    for raw in clean.split("\n"):
        m = re.match(r"^\s*[\[(]?(\d{1,3})[\])\.:：]\s*(.*)$", raw)
        if not m:
            continue
        k = int(m.group(1))
        if 1 <= k <= n and k not in found:
            found[k] = re.sub(r"^\((?:fe)?male voice\)\s*", "", m.group(2).strip())
    if not found:
        plain = [l.strip() for l in clean.split("\n") if l.strip()]
        return [plain[i] if i < len(plain) else "" for i in range(n)] if len(plain) == n else [""] * n
    return [found.get(i + 1, "") for i in range(n)]


_NOTE = re.compile(r"\s*(?:\([^()\n]{1,200}\)|（[^（）\n]{1,200}）|\[[^\[\]\n]{1,200}\]|【[^【】\n]{1,200}】|\*[^*\n]{1,80}\*)")


def clean(t: str, src: str = "") -> str:
    """Safety net for what the AI sends back: leading voice labels like '(nam)' are removed, and so are notes
    in () [] or *...* that the original line does not have itself ('(cười)', '(Ghi chú: ...)')."""
    from ..steps.hallucination import strip_gender_tags
    t = strip_gender_tags((t or "").strip())
    if t and not re.search(r"[(（\[【*]", src or ""):
        t = _NOTE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t)
    return re.sub(r"\s+([,.!?…])", r"\1", t).strip()


def glossary_pairs(glossary: str) -> list[tuple[str, str]]:
    out = []
    for line in (glossary or "").splitlines():
        for sep in ("=", "->", "→", "\t"):
            if sep in line:
                a, b = line.split(sep, 1)
                if a.strip() and b.strip():
                    out.append((a.strip(), b.strip()))
                break
    return sorted(out, key=lambda p: -len(p[0]))
