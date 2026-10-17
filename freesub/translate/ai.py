"""AI translators: Google Gemini and Cloudflare Workers AI (keys in Settings), like LazyK / Japo.

Both take a prompt and return text. Busy (429 / 503) is retried with a short wait; a used-up quota or a
rejected key raises QuotaError / KeyError_ so the caller can switch to the next service.
"""
import logging
import re
import time

import requests

from ..config import settings
from ..core.jobs import Cancelled

log = logging.getLogger(__name__)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CF_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3-flash-preview",
                 "gemini-2.5-flash", "gemini-2.5-flash-lite"]
CF_MODELS = ["@cf/google/gemma-4-26b-a4b-it", "@cf/qwen/qwen3.8-27b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"]
THINKING = [{"thinkingBudget": 0}, {"thinkingLevel": "low"}, None]   # models accept different knobs


class ServiceError(Exception):
    pass


class QuotaError(ServiceError):
    """Out of quota / rate limited for a long time: use another service."""


class KeyError_(ServiceError):
    """Missing or rejected key."""


def _wait(control, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if control is not None:
            control.check()
        time.sleep(0.2)


def _clean(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.I).strip()


def _detail(r) -> str:
    try:
        return r.text[:300]
    except Exception:  # noqa: BLE001
        return ""


def _raise_for(name, r):
    d = _detail(r).lower()
    if r.status_code in (401, 403):
        raise KeyError_(f"{name}: key rejected ({r.status_code})")
    if r.status_code == 429 and any(w in d for w in ("quota", "exhausted", "exceeded", "neurons", "daily", "4006")):
        raise QuotaError(f"{name}: quota used up")
    raise ServiceError(f"{name}: HTTP {r.status_code} {_detail(r)[:120]}")


class Gemini:
    name = "Gemini"

    def __init__(self):
        self._thinking = {}

    def ready(self) -> bool:
        return bool(settings()["gemini_key"].strip())

    def chat(self, prompt, control=None, max_tokens=8192, temperature=0.4) -> str:
        s = settings()
        key = s["gemini_key"].strip()
        if not key:
            raise KeyError_("Gemini: no API key (Settings → Translation)")
        first = s["gemini_model"] or GEMINI_MODELS[0]
        models = [first] + [m for m in GEMINI_MODELS if m != first]
        busy = net = 0
        while models:
            if control is not None:
                control.check()
            model = models[0]
            idx = self._thinking.get(model, 0)
            gen = {"temperature": temperature, "maxOutputTokens": max_tokens}
            if THINKING[idx]:
                gen["thinkingConfig"] = THINKING[idx]
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen,
                    "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in (
                        "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]}
            try:
                r = requests.post(GEMINI_URL.format(model=model), json=body, timeout=120,
                                  headers={"x-goog-api-key": key})
            except requests.RequestException as e:
                net += 1
                if net > 2:
                    raise ServiceError(f"Gemini: network error ({e})") from e
                _wait(control, 3)
                continue
            low = _detail(r).lower()
            if r.status_code == 400 and "thinking" in low and idx + 1 < len(THINKING):
                self._thinking[model] = idx + 1
                continue
            if r.status_code in (401, 403) or (r.status_code == 400 and "api key" in low):
                raise KeyError_("Gemini: the API key was rejected")
            if r.status_code in (404, 429) or (r.status_code in (500, 502, 503, 504) and busy >= 1):
                # model missing, out of quota or busy: free-tier limits are per model, try the next one
                log.info("Gemini %s: HTTP %s, trying the next model", model, r.status_code)
                models.pop(0)
                busy = 0
                if models and s["gemini_model"] != models[0]:
                    settings().update(gemini_model=models[0])
                continue
            if r.status_code in (500, 502, 503, 504):
                busy += 1
                if control is not None:
                    control.status("Gemini busy, retrying in 4 s…")
                _wait(control, 4)
                continue
            if not r.ok:
                raise ServiceError(f"Gemini: HTTP {r.status_code} {_detail(r)[:120]}")
            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                raise ServiceError(f"Gemini: no answer ({(data.get('promptFeedback') or {}).get('blockReason')})")
            parts = (cands[0].get("content") or {}).get("parts", [])
            out = _clean("".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")))
            if out:
                return out
            raise ServiceError(f"Gemini: empty answer ({cands[0].get('finishReason')})")
        raise QuotaError("Gemini: every model is out of quota or busy")


class Cloudflare:
    name = "Cloudflare"

    def ready(self) -> bool:
        s = settings()
        return bool(s["cloudflare_account"].strip() and s["cloudflare_token"].strip())

    def chat(self, prompt, control=None, max_tokens=8192, temperature=0.4) -> str:
        s = settings()
        acc, tok = s["cloudflare_account"].strip(), s["cloudflare_token"].strip()
        if not (acc and tok):
            raise KeyError_("Cloudflare: no account ID / token (Settings → Translation)")
        first = s["cloudflare_model"] or CF_MODELS[0]
        models = [first] + [m for m in CF_MODELS if m != first]
        model = models.pop(0)
        extra = {"chat_template_kwargs": {"enable_thinking": False}}
        for attempt, pause in enumerate((0, 3, 8, 15, 20)):
            if pause:
                if control is not None:
                    control.status(f"Cloudflare busy, waiting {pause} s…")
                _wait(control, pause)
            body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}], **extra}
            try:
                r = requests.post(CF_URL.format(account=acc), headers={"Authorization": f"Bearer {tok}"},
                                  json=body, timeout=120)
            except requests.RequestException as e:
                if attempt >= 2:
                    raise ServiceError(f"Cloudflare: network error ({e})") from e
                continue
            if r.status_code == 400 and extra and any(w in r.text.lower() for w in
                                                      ("chat_template_kwargs", "enable_thinking", "additional")):
                extra = {}
                continue
            if r.status_code in (429, 500, 503) and not any(w in _detail(r).lower() for w in ("neurons", "4006", "daily")):
                continue
            if r.status_code in (400, 404) and "model" in _detail(r).lower() and models:
                log.info("Cloudflare %s not available, trying %s", model, models[0])
                model = models.pop(0)
                continue
            if not r.ok:
                _raise_for("Cloudflare", r)
            data = r.json()
            res = data.get("result", data)
            out = ""
            if isinstance(res, dict):
                ch = res.get("choices") or data.get("choices")
                if ch:
                    msg = ch[0].get("message") or {}
                    out = msg.get("content") or ""
                    if isinstance(out, list):
                        out = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in out)
                else:
                    out = res.get("response") or ""
            out = _clean(out)
            if out:
                return out
            raise ServiceError("Cloudflare: empty answer")
        raise ServiceError("Cloudflare: busy, try again later")


SERVICES = {"gemini": Gemini(), "cloudflare": Cloudflare()}


def test(name: str) -> tuple[bool, str]:
    svc = SERVICES[name]
    try:
        out = svc.chat("Translate to Vietnamese, answer with the translation only: What a nice day today!",
                       max_tokens=200)
        return True, f"{svc.name}: {out[:80]}"
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
