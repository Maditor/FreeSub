"""Names and terms found in a video's subtitles by the AI (Gemini or Cloudflare), for a name list.

The AI reads the original lines (and the current translation, if any) and answers with the names of people,
places, groups and the special words of the story (晶核, 异能...), each with:
* how it is most likely written: speech recognition writes one name several ways (冯北 / 冯飞 / 黄背), the AI
  picks the right one from the context and lists the others it saw,
* how it should be translated (Chinese names in Hán-Việt for Vietnamese: 冯贝 = Phùng Bối),
* a kind (person / place / group / term).
What it answers is checked against the subtitles: an entry none of whose spellings is in them is dropped (made
up), and the entries are sorted by how often they appear. The user picks which ones go into the list.
"""
import json
import logging
import re

from ..config import settings
from ..langs import LANGS, NO_SPACE
from ..core.jobs import Cancelled
from . import ai

log = logging.getLogger(__name__)
BLOCK_CHARS = 9000             # original text per request (long videos are read in several parts)
KINDS = ("person", "place", "group", "term")


class NoAI(Exception):
    """No Gemini / Cloudflare key: this needs one."""


def services() -> list:
    """The AIs that have their key, the one chosen as translator first (the others take over when it fails)."""
    chosen = settings()["translator"]
    order = ([ai.SERVICES[chosen]] if chosen in ai.SERVICES else []) + \
        [svc for k, svc in ai.SERVICES.items() if k != chosen]
    return [svc for svc in order if svc.ready()]


def service():
    """The first AI to ask (NoAI when none has a key)."""
    ready = services()
    if ready:
        return ready[0]
    raise NoAI("Finding names needs Gemini or Cloudflare: put a key in Settings → Translation "
               "(Gemini has a free key).")


def _how_to_translate(src: str, target: str) -> str:
    if target == "vi" and src in ("zh", "yue"):
        return ("Translate Chinese names of people, places and groups into Sino-Vietnamese (Hán-Việt) with capital "
                "letters (冯贝 = Phùng Bối, 齐桦 = Tề Hoa, 雪薇 = Tuyết Vi); translate the special words of the story "
                "into the usual Vietnamese of web novels / donghua (晶核 = tinh hạch, 异能 = dị năng, 丧尸 = zombie).")
    if target == "vi" and src == "ja":
        return ("Write Japanese names in romaji (炭治郎 = Tanjiro); translate the special words into natural Vietnamese.")
    if target == "vi" and src == "ko":
        return ("Write Korean names in the usual romanization (Kim Ji-woo); translate the special words into natural "
                "Vietnamese.")
    return (f"Write names the way {LANGS.get(target, target)} subtitles usually write them (pinyin, romaji, the "
            f"known spelling); translate the special words into natural {LANGS.get(target, target)}.")


def prompt(lines, tr_lines, src: str, target: str) -> str:
    rows = []
    for i, t in enumerate(lines):
        tt = tr_lines[i] if tr_lines and i < len(tr_lines) else ""
        rows.append(f"[{i + 1}] {t}" + (f"  ||  {tt}" if tt else ""))
    return (
        f"These are the subtitles of a video, made by speech recognition from {LANGS.get(src, src) or 'the speech'}"
        + (f", with the current {LANGS.get(target, target)} translation after ||" if tr_lines else "") + ".\n"
        "Find every NAME (people, nicknames used as names, places, shops, groups, teams) and every SPECIAL WORD of "
        "this story (made-up items, powers, ranks, systems, creatures) that a translator must keep consistent.\n"
        "Speech recognition often writes the same name in different ways or with a similar-sounding word "
        "(e.g. 冯北 / 冯飞 / 黄背 for one character, 金盒 for 晶核): use the whole story to decide the most likely "
        "correct written form, and list the other forms you saw in the subtitles.\n"
        "Do NOT include ordinary words, pronouns, titles alone (队长, 老板, 客人), greetings or sound words.\n"
        f"{_how_to_translate(src, target)}\n"
        "Answer with JSON only, no notes: a list of objects\n"
        '[{"original": "<most likely correct form>", "seen": ["<other forms in the subtitles>"], '
        f'"translation": "<in {LANGS.get(target, target)}>", "kind": "person|place|group|term"}}]\n\n'
        "Subtitles:\n" + "\n".join(rows))


def parse(text: str) -> list[dict]:
    """The JSON list in the AI's answer (it may wrap it in ```json fences or add a sentence)."""
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return []
    raw = m.group(0)
    for attempt in (raw, re.sub(r",\s*([\]}])", r"\1", raw)):          # a trailing comma is a common slip
        try:
            data = json.loads(attempt)
            break
        except ValueError:
            data = None
    out = []
    for x in data or []:
        if not isinstance(x, dict):
            continue
        orig = " ".join(str(x.get("original") or "").split())
        if not orig:
            continue
        seen = [" ".join(str(v).split()) for v in (x.get("seen") or []) if str(v).strip()]
        kind = str(x.get("kind") or "term").lower()
        out.append({"original": orig, "seen": [v for v in dict.fromkeys(seen) if v != orig],
                    "translation": " ".join(str(x.get("translation") or "").split()),
                    "kind": kind if kind in KINDS else "term"})
    return out


def _count(text: str, word: str, lang: str) -> int:
    if not word:
        return 0
    if lang in NO_SPACE or not re.match(r"^\w", word):
        return text.count(word)
    return len(re.findall(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, flags=re.I))


def check(found: list[dict], lines, lang: str) -> list[dict]:
    """Merge the parts' answers, drop what is not in the subtitles at all, count, sort (most often first)."""
    text = "\n".join(lines)
    merged: dict[str, dict] = {}
    for x in found:
        key = x["original"]
        if key in merged:
            m = merged[key]
            m["seen"] = list(dict.fromkeys(m["seen"] + x["seen"]))
            m["translation"] = m["translation"] or x["translation"]
        else:
            merged[key] = dict(x)
    out = []
    for x in merged.values():
        x["seen"] = [v for v in x["seen"] if _count(text, v, lang)]          # only forms really in the subtitles
        x["count"] = _count(text, x["original"], lang) + sum(_count(text, v, lang) for v in x["seen"])
        if x["count"]:
            out.append(x)
        else:
            log.info("Name not in the subtitles, dropped: %s", x["original"])
    out.sort(key=lambda x: (-x["count"], KINDS.index(x["kind"])))
    return out


def blocks(lines, size=BLOCK_CHARS):
    """Line ranges [(start, end)] of at most `size` characters each."""
    out, start, n = [], 0, 0
    for i, t in enumerate(lines):
        if n and n + len(t) > size:
            out.append((start, i))
            start, n = i, 0
        n += len(t) + 8                                   # "[123] " and the newline
    if start < len(lines):
        out.append((start, len(lines)))
    return out


def find(lines, tr_lines, src: str, target: str, control=None) -> list[dict]:
    """[{original, seen, translation, kind, count}] for the subtitles `lines` (original text, one per line)."""
    lines = [" ".join((t or "").split()) for t in lines]
    if not any(lines):
        return []
    service()                                    # NoAI when there is no key at all
    ready = services()
    found = []
    for a, b in blocks(lines):
        part_tr = tr_lines[a:b] if tr_lines else None
        found += _ask(ready, prompt(lines[a:b], part_tr, src, target), a, b, control)
    return check(found, lines, src)


def _ask(ready, text, a, b, control):
    """One part, asked to each AI in turn until one answers with a list: Gemini failing (an error, quota, an
    answer that is not the list) hands over to Cloudflare, and the other way round. Every one failing: the last
    error is raised."""
    last = None
    for svc in list(ready):
        if control is not None:
            control.check()
        try:
            answer = svc.chat(text, control=control, max_tokens=4096, temperature=0.2)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("%s could not find the names (%s): asking the next AI", svc.name, e)
            last = e
            ready.remove(svc)                    # not asked again for the next parts
            ready.append(svc)                    # (but kept as the last resort)
            continue
        if not re.search(r"\[[\s\S]*\]", answer or ""):
            log.warning("%s did not answer with a list: asking the next AI", svc.name)
            last = ai.ServiceError(f"{svc.name}: the answer was not a list")
            continue
        got = parse(answer)
        log.info("%s found %d name(s) / term(s) in lines %d-%d", svc.name, len(got), a + 1, b)
        return got
    raise last or ai.ServiceError("no AI answered")
