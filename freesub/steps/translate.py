"""Step: subs.src.srt -> subs.tr.srt (same number of lines and the same timing).

Services: Google Translate (no key, default), Gemini or Cloudflare (AI, keys in Settings). AI services get
blocks of lines with the lines before / after, the film context and the glossary. When a service fails
(quota, key, network) the next one is used; lines an AI left out are filled in by Google.
A checkpoint (translate.partial.json) lets a stopped run go on.
"""
import hashlib
import json
import logging
import os
import re

from ..config import settings
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, TR_SRT, Project
from ..core.subtitle import Cue, load_srt, save_srt
from ..log import step_timer
from ..translate import ai, google
from ..translate import prompt as P
from ..translate import names, styles
from .hallucination import norm
from .transcribe import wrap

log = logging.getLogger(__name__)
PARTIAL = "translate.partial.json"
CONTEXT_BEFORE = 6
CONTEXT_AFTER = 3
SENT_END = tuple("。．.!！?？…")


def _block_end(cues, texts, i, n, batch):
    """End of the block that starts at i: at most `batch` lines, and when possible cut where a sentence ends
    (closing punctuation or a pause of 1 s or more) so a sentence is not torn between two requests."""
    end = min(n, i + batch)
    if end >= n:
        return n
    for e in range(end, i + max(1, batch // 2), -1):
        if texts[e - 1].rstrip().endswith(SENT_END) or cues[e].start - cues[e - 1].end >= 1.0:
            return e
    return end


def order() -> list[str]:
    """Services to use, the chosen one first."""
    s = settings()
    first = s["translator"]
    rest = [x for x in ("gemini", "cloudflare", "google") if x != first]
    if not s["translate_fallback"]:
        return [first]
    return [first] + [x for x in rest if x == "google" or ai.SERVICES[x].ready()]


def _genders(project, cues):
    try:
        with open(project.path("subs.src.json"), encoding="utf-8") as f:
            side = json.load(f)
    except (OSError, ValueError):
        return [None] * len(cues)
    by_start = {round(x["start"], 1): x.get("gender") for x in side}
    return [by_start.get(round(c.start, 1)) for c in cues]


def _flat(text: str) -> str:
    return " ".join(text.split())


def run(project: Project, control, force=False) -> str:
    s = settings()
    if not project.has(SRC_SRT):
        raise RuntimeError("No subtitles to translate yet")
    target = s["target_lang"]
    src_lang = project.meta.get("source_lang") or "auto"
    if src_lang == target:
        log.info("Subtitles are already in %s: copying them", target)
    cues = load_srt(project.src_srt)
    texts = [_flat(c.text) for c in cues]
    style = styles.get(s["translate_style"])
    params = {"target": target, "first": s["translator"], "n": len(cues), "style": style["id"],
              "style_text": hashlib.sha1(style["text"].encode("utf-8")).hexdigest()[:10],
              "glossary": names.glossary()[:200]}
    if not force and project.has(TR_SRT) and project.meta.get("tr_edited"):
        project.ensure_published(TR_SRT, target)
        control.progress(1.0, "Keeping your edited translation")
        return project.tr_srt
    if not force and project.is_done("translate", TR_SRT) and project.step("translate").get("params") == params \
            and os.path.getmtime(project.tr_srt) >= os.path.getmtime(project.src_srt):
        project.ensure_published(TR_SRT, target)
        control.progress(1.0, "Already translated")
        return project.tr_srt

    partial = project.path(PARTIAL)
    out = [""] * len(cues)
    try:
        with open(partial, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("params") == params and len(old.get("lines", [])) == len(cues) and not force:
            out = old["lines"]
            log.info("Resuming translation: %d/%d lines done", sum(map(bool, out)), len(out))
    except (OSError, ValueError):
        pass

    def save_partial():
        tmp = partial + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"params": params, "lines": out}, f, ensure_ascii=False)
        os.replace(tmp, partial)

    with step_timer("translate", project.video):
        project.mark("translate", "running", params=params)
        try:
            if src_lang == target:
                out = texts[:]
            else:
                _translate(project, control, cues, texts, out, src_lang, target, save_partial)
        except Cancelled:
            save_partial()
            project.mark("translate", "stopped", params=params)
            raise
        except Exception as e:
            save_partial()
            project.mark("translate", "failed", params=params, error=str(e)[:300])
            raise
        from .hallucination import strip_dashes
        # dialogue dashes ("- Ăn được không?") only when the original line has them itself
        out = [t if (c.text or "").lstrip().startswith(("-", "–", "—")) else strip_dashes(t)
               for c, t in zip(cues, out)]
        result = [Cue(c.start, c.end, wrap(t or c.text, target)) for c, t in zip(cues, out)]
        save_srt(result, project.tr_srt)
        used = project.meta.get("_used_services", [])
        project.meta["tr_edited"] = False
        project.mark("translate", "done", params=params, file=TR_SRT, lines=len(result), services=used)
        project.publish(TR_SRT, target)
        try:
            os.remove(partial)
        except OSError:
            pass
    control.progress(1.0, f"{len(result)} lines translated")
    return project.tr_srt


def _translate(project, control, cues, texts, out, src, target, save_partial):
    s = settings()
    style = styles.get(s["translate_style"])["text"]
    gloss = names.glossary()                    # the chosen name list (Steps → Subtitles)
    pairs = P.glossary_pairs(gloss)
    if target == "vi":                          # the shipped guides are for Vietnamese: their fixed terms too
        mine = {a for a, _b in pairs}
        pairs = sorted(pairs + [p for p in styles.term_pairs(style) if p[0] not in mine], key=lambda p: -len(p[0]))
    genders = _genders(project, cues)
    services = order()
    used = []
    n = len(texts)
    batch = max(5, int(s["translate_batch"]))
    i = 0
    while i < n:
        control.check()
        j = _block_end(cues, texts, i, n, batch)
        if all(out[i:j]):
            i = j
            continue
        idx = list(range(i, j))
        block = [texts[k] for k in idx]
        got = None
        while services and got is None:
            svc = services[0]
            control.progress(i / n, f"Translating {i}/{n} lines · {svc.capitalize()}")
            try:
                if svc == "google":
                    got = _google(block, src, target, pairs, [cues[k] for k in idx], [genders[k] for k in idx])
                else:
                    before = [(texts[k], out[k]) for k in range(max(0, i - CONTEXT_BEFORE), i) if out[k]]
                    after = texts[idx[-1] + 1: idx[-1] + 1 + CONTEXT_AFTER]
                    got = _ai(svc, block, src, target, before, after, style,
                              gloss, [genders[k] for k in idx], control)
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("%s failed: %s", svc, e)
                services.pop(0)
                if not services:
                    raise RuntimeError(f"Translation failed: {e}") from e
                control.status(f"{svc.capitalize()} failed ({e}). Using {services[0].capitalize()}.")
        if svc not in used:
            used.append(svc)
        for k, t in zip(idx, got):
            out[k] = t
        save_partial()
        i = j
    project.meta["_used_services"] = used


JOIN_GAP = 0.6       # Google: lines this close, the first without a sentence end, are one sentence ...
JOIN_LINES = 4       # ... of at most this many lines
CLAUSE = (",", "，", "、", ";", "；", ":", "：", "-", "–", "—")


def sentence_groups(cues, texts, genders=None):
    """Lines that are one sentence (\"I think that\" + \"we should go now.\"): lists of indexes."""
    groups, cur = [], []
    for k, t in enumerate(texts):
        if cur:
            p = cur[-1]
            other = genders and genders[p] and genders[k] and genders[p] != genders[k]
            if (not texts[p].strip() or not t.strip() or texts[p].rstrip().endswith(SENT_END) or other
                    or cues[k].start - cues[p].end > JOIN_GAP or len(cur) >= JOIN_LINES):
                groups.append(cur)
                cur = []
        cur.append(k)
    if cur:
        groups.append(cur)
    return groups


def share(text, sizes, lang):
    """Spread one translated sentence back over its lines, in order, each getting a part as long as its
    original line's share (a comma near the place is a better cut). None when it can not be shared."""
    from ..langs import NO_SPACE
    chars = lang in NO_SPACE
    toks = list(text.replace(" ", "")) if chars else text.split()
    n = len(sizes)
    if len(toks) < n:
        return None
    total = float(sum(sizes)) or 1.0
    cuts, acc, last = [], 0.0, 0
    for k in range(n - 1):
        acc += sizes[k]
        want = round(acc / total * len(toks))
        lo, hi = last + 1, len(toks) - (n - 1 - k)            # every line keeps at least one word
        want = max(lo, min(hi, want))
        best = want
        for d in (0, -1, 1, -2, 2):                            # a comma close by
            j = want + d
            if lo <= j <= hi and toks[j - 1].endswith(CLAUSE):
                best = j
                break
        cuts.append(best)
        last = best
    parts, a = [], 0
    for b in cuts + [len(toks)]:
        parts.append(("" if chars else " ").join(toks[a:b]))
        a = b
    return parts


def _google(lines, src, target, pairs, cues=None, genders=None):
    if pairs:                                   # names / terms are put in before Google sees the line
        lines = [put_names(l, pairs) for l in lines]
    out = _google_lines(lines, src, target, cues, genders)
    return [spaced_names(t, pairs) for t in out] if pairs else out


def put_names(line: str, pairs) -> str:
    """The names / terms of the list put into a line in place of the originals. Next to a letter or a digit
    (Chinese / Japanese have no spaces: 可兑换积分100) the translation gets a space on each side, or Google
    glues it to its neighbours ('tích phân100')."""
    for a, b in pairs:
        if a not in line:
            continue
        parts = line.split(a)
        out = parts[0]
        for nxt in parts[1:]:
            left = " " if out and not out[-1].isspace() and _glues(out[-1], b[:1]) else ""
            right = " " if nxt and not nxt[0].isspace() and _glues(b[-1:], nxt[0]) else ""
            out += left + b + right + nxt
        line = out
    return line


def _glues(x: str, y: str) -> bool:
    """Two characters that would read as one word if put side by side: both letters / digits and not of
    the same script (a Chinese character next to a Latin letter), or a digit next to a letter."""
    if not (x.isalnum() and y.isalnum()):
        return False
    if x.isdigit() != y.isdigit():
        return True
    return _cjk(x) != _cjk(y)


def _cjk(c: str) -> bool:
    return "\u2e80" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" or "\uf900" <= c <= "\ufaff"


def spaced_names(text: str, pairs) -> str:
    """A name / term of the list glued to a number in the translation ('tích phân100', '20tích phân')
    gets its space back."""
    for _a, b in pairs:
        if b and b in text:
            e = re.escape(b)
            if b[-1:].isalpha():
                text = re.sub(e + r"(?=\d)", lambda m: m.group(0) + " ", text)
            if b[:1].isalpha():
                text = re.sub(r"(?<=\d)" + e, lambda m: " " + m.group(0), text)
    return text


def _google_lines(lines, src, target, cues=None, genders=None):
    sl = src if src != "auto" else "auto"
    if not cues or len(cues) != len(lines):
        return google.translate(lines, sl, target)
    # a sentence over a few lines is translated whole (a few words alone come back wrong), then shared
    from ..langs import NO_SPACE
    from .transcribe import size_of
    groups = sentence_groups(cues, lines, genders)
    joined = [("" if src in NO_SPACE else " ").join(lines[k].strip() for k in g) for g in groups]
    got = google.translate(joined, sl, target)
    out = [""] * len(lines)
    alone = []
    for g, t in zip(groups, got):
        parts = [t] if len(g) == 1 else share(t, [max(1, size_of(lines[k], src)) for k in g], target)
        if parts is None or not t.strip():
            alone += g
            continue
        for k, p in zip(g, parts):
            out[k] = p
    if alone:                                   # too short to share: line by line, as before
        for k, t in zip(alone, google.translate([lines[k] for k in alone], sl, target)):
            out[k] = t
    return out


def _ask(client, text, block, control):
    got = P.parse(client.chat(text, control=control), len(block))
    return [P.clean(t, src) for t, src in zip(got, block)]


def _repeats(block, got, prev=("", "")):
    """Lines whose translation just repeats the line before (the sources differ): a sign the AI copied
    instead of translating."""
    bad = []
    for k, t in enumerate(got):
        s0, t0 = (block[k - 1], got[k - 1]) if k else prev
        n, n0 = norm(t), norm(t0)
        if not n or not n0 or norm(block[k]) == norm(s0):
            continue
        if n == n0 or (len(n) >= 12 and n in n0) or (len(n0) >= 12 and n0 in n):
            bad.append(k)
    return bad


def _ai(svc, block, src, target, before, after, style, glossary, genders, control):
    client = ai.SERVICES[svc]
    text = P.build(block, src, target, before, after, "", glossary, genders, style=style)
    got = _ask(client, text, block, control)
    missing = [k for k, t in enumerate(got) if not t]
    if missing:
        log.warning("%s left out %d line(s), asking again", client.name, len(missing))
        again = _ask(client, text + "\n\nYour previous answer left out some lines. Output ALL "
                     f"{len(block)} numbered lines. Each line must be the translation of its own input line, "
                     "never a copy of a neighbouring line.", block, control)
        got = [g or a for g, a in zip(got, again)]
    prev = before[-1] if before else ("", "")
    dups = _repeats(block, got, prev)
    if dups:
        log.warning("%s repeated %d line(s), asking again for those", client.name, len(dups))
        lines = [block[k] for k in dups]
        fix = P.build(lines, src, target, (), (), "", glossary, [genders[k] for k in dups], style=style)
        fixed = _ask(client, fix + "\n\nThese lines come from different places in the video. Translate each "
                     "one by itself; never copy the translation of another line.", lines, control)
        for k, t in zip(dups, fixed):
            got[k] = t
        for k in _repeats(block, got, prev):     # still a copy: leave it empty, Google fills it in below
            got[k] = ""
    missing = [k for k, t in enumerate(got) if not t]
    if missing:                                  # still missing: Google fills them in
        filled = google.translate([block[k] for k in missing], src, target)
        for k, t in zip(missing, filled):
            got[k] = t
    return got
