"""Google Translate without a key (the endpoint Chrome's translate extension uses), from LazyK.

Lines go in batches (one request for many lines), the same line is never sent twice (cache), a slow answer
is asked again on a fresh connection, and lines Google left half untranslated are repaired.
"""
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout

import requests

from ..core.jobs import Cancelled

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
CODES = {"zh": "zh-CN", "zh-TW": "zh-TW", "he": "iw", "": "auto"}
_rest_until = {}  # endpoint -> time it may be used again (blocked: 429/403 -> 5 min)
_session = None
_session_lock = threading.Lock()
_cache = OrderedDict()  # (text, sl, tl) -> translation: the same line is never sent twice
_cache_lock = threading.Lock()
CACHE_MAX = 5000
BATCH_CHARS = 1800   # characters per request: a whole page usually goes in ONE request
_last_used = [0.0]   # when Google last answered: an idle connection is reopened before the next page
_batch_ok = [True]   # False once the endpoint refused several lines in one request: one per line then


def _sess():
    """One shared session: the connection to Google stays open between lines, so a translation does not
    pay for a new TCP + TLS handshake every time (usually 100-300 ms)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = _new_session()
        return _session


def _reset_session():
    global _session
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass
        _session = None


class TranslateError(Exception):
    pass


def _first_text(data):
    """The translated string, whatever shape the endpoint answered with."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if "sentences" in data:
            return "".join(s.get("trans", "") for s in data["sentences"])
        return data.get("text") or data.get("translatedText") or ""
    if isinstance(data, list) and data:
        return data[0] if all(isinstance(x, str) for x in data) else _first_text(data[0])
    return ""


def _prepare(text):
    """One clean line; ALL CAPS comic lettering becomes sentence case (translates much better)."""
    t = re.sub(r"(?<=[　-鿿＀-￯])\s*\n\s*(?=[　-鿿＀-￯])", "", str(text or ""))
    t = re.sub(r"\s*\n\s*", " ", t).strip()
    letters = [c for c in t if c.isascii() and c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        t = re.sub(r"(^|[.!?…]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t.lower())
        t = re.sub(r"\bi\b", "I", t)
    return t


HEDGE_S = 0.9  # no answer by then: the same request again on a new connection, the first answer wins
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="gt")


def _new_session():
    from requests.adapters import HTTPAdapter
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=4))
    return s


def _get_once(name, url, params, sess=None):
    for attempt in range(2):
        try:
            r = (sess or _sess()).get(url, params=params, timeout=8)
        except requests.RequestException as e:
            # a kept-open connection the server already closed fails once: retry at once on a fresh one
            if sess is None:
                _reset_session()
            if attempt:
                raise TranslateError("Google Translate: network error") from e
            continue
        if r.status_code in (403, 429):
            _rest_until[name] = time.time() + 300
        if not r.ok:
            raise TranslateError(f"Google Translate: HTTP {r.status_code}")
        _last_used[0] = time.time()
        try:
            return r.json()
        except ValueError:
            return r.text
    raise TranslateError("Google Translate: no answer")


def _get(name, url, params):
    """One request. Now and then Google (or a stale kept-open connection) takes seconds to answer while
    a new request is answered at once: after HEDGE_S the request is sent again on a fresh connection and
    whichever answers first is used."""
    global _session
    if _rest_until.get(name, 0) > time.time():
        raise TranslateError(f"{name} is resting after a block")
    first = _pool.submit(_get_once, name, url, params)
    try:
        return first.result(timeout=HEDGE_S)
    except FutureTimeout:
        pass
    fresh = _new_session()
    second = _pool.submit(_get_once, name, url, params, fresh)
    log.info("Google slow to answer: asked again on a new connection")
    pending, err = {first, second}, None
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for f in done:
            try:
                res = f.result()
            except TranslateError as e:
                err = e
                continue
            if f is second:  # the new connection works: keep it for the next pages
                with _session_lock:
                    old, _session = _session, fresh
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
            return res
    raise err or TranslateError("Google Translate: no answer")


def _one(text, sl, tl):
    try:
        return _first_text(_get("google", "https://clients5.google.com/translate_a/t",
                                {"client": "dict-chrome-ex", "sl": sl, "tl": tl, "q": text}))
    except TranslateError as e:
        log.info("Chrome endpoint failed (%s), trying client=gtx", e)
        data = _get("google-gtx", "https://translate.googleapis.com/translate_a/single",
                    {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text})
        if isinstance(data, list) and data and isinstance(data[0], list):
            return "".join(p[0] for p in data[0] if p and p[0])
        return _first_text(data)


def _batch(texts, sl, tl):
    """Several lines in ONE request (repeated q=): one round trip for a whole page instead of one per
    bubble. Raises TranslateError when the answer does not have one translation per line."""
    params = [("client", "dict-chrome-ex"), ("sl", sl), ("tl", tl)] + [("q", t) for t in texts]
    data = _get("google", "https://clients5.google.com/translate_a/t", params)
    if isinstance(data, list) and len(data) == len(texts):
        out = [d if isinstance(d, str) else _first_text(d) for d in data]
        if all(isinstance(x, str) for x in out):
            return out
    raise TranslateError(f"batch answer has a different shape ({type(data).__name__}, "
                         f"{len(data) if isinstance(data, list) else '-'} for {len(texts)})")


def _groups(idx, texts):
    """Split line indexes into requests of at most BATCH_CHARS characters."""
    groups, cur, size = [], [], 0
    for i in idx:
        n = len(texts[i]) + 3
        if cur and size + n > BATCH_CHARS:
            groups.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        groups.append(cur)
    return groups


def _translate_many(todo, texts, sl, tl):
    """Translations for the lines in `todo` (indexes into texts), as few requests as possible."""
    if len(todo) == 1:
        return [_one(texts[todo[0]], sl, tl)]
    if _batch_ok[0] and _rest_until.get("google", 0) <= time.time():
        groups = _groups(todo, texts)
        try:
            if len(groups) == 1:
                parts = [_batch([texts[i] for i in groups[0]], sl, tl)]
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(groups))) as ex:
                    parts = list(ex.map(lambda g: _batch([texts[i] for i in g], sl, tl), groups))
            return [t for part in parts for t in part]
        except TranslateError as e:
            if "shape" in str(e):
                _batch_ok[0] = False  # this endpoint does not batch: stop trying for this session
            log.info("Google batch failed (%s), one request per line", e)
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(lambda i: _one(texts[i], sl, tl), todo))


_WORD = re.compile(r"[A-Za-z][A-Za-z']*")
_SENT_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'“(]?[A-Z])|(?<=[。！？])\s*")  # "case... to" stays one


def _left_untranslated(src, out, tl):
    """True when Google copied part of a Latin-script source into the translation unchanged (it does
    that now and then for a second sentence): four source words in a row appear in the output."""
    if tl.lower().startswith("en") or not out:
        return False
    words = [w.lower() for w in _WORD.findall(src)]
    if len(words) < 4:
        return False
    o = " " + " ".join(w.lower() for w in _WORD.findall(out)) + " "
    return any(" " + " ".join(words[k:k + 4]) + " " in o for k in range(len(words) - 3))


def _gtx(text, sl, tl):
    data = _get("google-gtx", "https://translate.googleapis.com/translate_a/single",
                {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text})
    if isinstance(data, list) and data and isinstance(data[0], list):
        return "".join(p[0] for p in data[0] if p and p[0])
    return _first_text(data)


def _repair(src, out, sl, tl):
    """Translate `src` sentence by sentence (one request), then any sentence still left as it was with
    the other endpoint. Returns the best translation found."""
    sents = [x for x in _SENT_END.split(src) if x.strip()]
    try:
        if len(sents) > 1:
            parts = _batch(sents, sl, tl) if _batch_ok[0] else [_one(x, sl, tl) for x in sents]
        else:
            parts = [_gtx(src, sl, tl)]
        for k, (a, b) in enumerate(zip(sents, parts)):
            if _left_untranslated(a, b, tl) or not b.strip():
                try:
                    parts[k] = _gtx(a, sl, tl) or b
                except TranslateError:
                    pass
        fixed = " ".join(p.strip() for p in parts if p.strip())
    except TranslateError as e:
        log.info("Google: could not re-translate an untranslated part (%s)", e)
        return out
    if fixed and not _left_untranslated(src, fixed, tl):
        log.info("Google left part of a line untranslated: fixed sentence by sentence")
        return fixed
    return fixed if fixed and len(fixed) >= len(out) * 0.5 else out


def code(lang: str) -> str:
    return CODES.get(lang or "", lang or "auto")


def translate(lines, source_lang, target_lang, cancel=None):
    """lines -> translated lines (same length; an empty line stays empty). Language codes: ja, en, vi..."""
    sl = code(source_lang)
    tl = code(target_lang)
    texts = [_prepare(l) for l in lines]
    todo = [i for i, t in enumerate(texts) if t]
    out = [""] * len(lines)
    if not todo:
        return out
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    with _cache_lock:
        for i in todo:
            hit = _cache.get((texts[i], sl, tl))
            if hit:
                out[i] = hit
                _cache.move_to_end((texts[i], sl, tl))
    todo = [i for i in todo if not out[i]]
    if todo:
        t0 = time.time()
        got = _translate_many(todo, texts, sl, tl)
        got = [_repair(texts[i], t, sl, tl) if _left_untranslated(texts[i], t, tl) else t
               for i, t in zip(todo, got)]
        log.info("Google: %d line(s) in %.2fs", len(todo), time.time() - t0)
        with _cache_lock:
            for i, t in zip(todo, got):
                out[i] = (t or "").strip()
                if out[i]:
                    _cache[(texts[i], sl, tl)] = out[i]
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    if not any(out):
        raise TranslateError("Google Translate returned nothing")
    return out


def test(target="vi"):
    """Translate one sample sentence. Returns (ok, message)."""
    _rest_until.clear()
    try:
        out = translate(["What a nice day today!"], "en", target)[0]
        return True, f"Google: {out}"
    except Exception as e:  # noqa: BLE001
        return False, f"Google: {str(e)[:200]}"
