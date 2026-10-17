"""Subtitle translation prompt for the AI services: numbered lines in, numbered lines out (same count)."""
import re

from ..langs import name as lang_name


def build(lines, src, target, before=(), after=(), context="", glossary="", genders=None):
    """lines: texts to translate; before: [(source, translation)] already done; after: next source lines."""
    S = lang_name(src) if src and src != "auto" else "the original language"
    T = lang_name(target)
    parts = [f"You are a professional film subtitle translator. Translate {S} subtitles into natural, "
             f"spoken {T}."]
    if context.strip():
        parts.append("About this video (from the viewer: names, relationships, tone):\n" + context.strip()[:2000])
    if glossary.strip():
        parts.append("Always translate these names / terms exactly like this:\n" + glossary.strip()[:2000])
    if before:
        parts.append("Earlier lines, for context only (do NOT output them):\n" +
                     "\n".join(f"- {s}  =>  {t}" for s, t in before))
    who = {"f": " (female voice)", "m": " (male voice)"}
    numbered = "\n".join(f"[{i + 1}]{who.get((genders or [None] * len(lines))[i] or '', '')} {l}"
                         for i, l in enumerate(lines))
    parts.append(f"Lines to translate:\n{numbered}")
    if after:
        parts.append("Next lines, for context only (do NOT translate them):\n" + "\n".join(f"- {s}" for s in after))
    rules = [
        f"Output exactly {len(lines)} lines, in the same order, each starting with its number: [1] ..., [2] ...",
        "One input line = one output line. Never merge, split, skip or add lines. A sentence that runs over "
        "two lines is split at the same point in the translation.",
        f"Write short, natural spoken {T} like professional subtitles, not word-for-word. Keep each line about "
        "as long as the original so it can be read in time.",
        "The text comes from speech recognition or OCR and may have mistakes: guess the most plausible meaning.",
        "Keep the tone, emotion and punctuation style (..., ?!, !!).",
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
