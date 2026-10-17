"""Step: subs.src.srt -> subs.tr.srt (same number of lines and the same timing).

Services: Google Translate (no key, default), Gemini or Cloudflare (AI, keys in Settings). AI services get
blocks of lines with the lines before / after, the film context and the glossary. When a service fails
(quota, key, network) the next one is used; lines an AI left out are filled in by Google.
A checkpoint (translate.partial.json) lets a stopped run go on.
"""
import json
import logging
import os

from ..config import settings
from ..core.jobs import Cancelled
from ..core.project import SRC_SRT, TR_SRT, Project
from ..core.subtitle import Cue, load_srt, save_srt
from ..log import step_timer
from ..translate import ai, google
from ..translate import prompt as P
from .transcribe import wrap

log = logging.getLogger(__name__)
PARTIAL = "translate.partial.json"
CONTEXT_BEFORE = 6
CONTEXT_AFTER = 3


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
    params = {"target": target, "first": s["translator"], "n": len(cues),
              "context": s["translate_context"][:200], "glossary": s["translate_glossary"][:200]}
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
    pairs = P.glossary_pairs(s["translate_glossary"])
    genders = _genders(project, cues)
    services = order()
    used = []
    n = len(texts)
    batch = max(5, int(s["translate_batch"]))
    i = 0
    while i < n:
        control.check()
        if all(out[i:i + batch]):
            i += batch
            continue
        idx = list(range(i, min(n, i + batch)))
        block = [texts[k] for k in idx]
        got = None
        while services and got is None:
            svc = services[0]
            control.progress(i / n, f"Translating {i}/{n} lines · {svc.capitalize()}")
            try:
                if svc == "google":
                    got = _google(block, src, target, pairs)
                else:
                    before = [(texts[k], out[k]) for k in range(max(0, i - CONTEXT_BEFORE), i) if out[k]]
                    after = texts[idx[-1] + 1: idx[-1] + 1 + CONTEXT_AFTER]
                    got = _ai(svc, block, src, target, before, after, s["translate_context"],
                              s["translate_glossary"], [genders[k] for k in idx], control)
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
        i += batch
    project.meta["_used_services"] = used


def _google(lines, src, target, pairs):
    if pairs:                                   # names / terms are put in before Google sees the line
        fixed = []
        for l in lines:
            for a, b in pairs:
                l = l.replace(a, b)
            fixed.append(l)
        lines = fixed
    return google.translate(lines, src if src != "auto" else "auto", target)


def _ai(svc, block, src, target, before, after, context, glossary, genders, control):
    client = ai.SERVICES[svc]
    text = P.build(block, src, target, before, after, context, glossary, genders)
    got = P.parse(client.chat(text, control=control), len(block))
    missing = [k for k, t in enumerate(got) if not t]
    if missing:
        log.warning("%s left out %d line(s), asking again", client.name, len(missing))
        again = P.parse(client.chat(text + "\n\nYour previous answer left out some lines. Output ALL "
                                    f"{len(block)} numbered lines.", control=control), len(block))
        got = [g or a for g, a in zip(got, again)]
        missing = [k for k, t in enumerate(got) if not t]
    if missing:                                  # still missing: Google fills them in
        filled = google.translate([block[k] for k in missing], src, target)
        for k, t in zip(missing, filled):
            got[k] = t
    return got
