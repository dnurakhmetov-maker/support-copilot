"""LLM provider abstraction. Owner: Tech Lead.

Config (env / .env):
  LLM_PROVIDER   gemini | openai_compat | none     (default: gemini)
  LLM_MODEL      e.g. gemini-2.5-flash-lite         (default)
  LLM_API_KEY    provider key (never commit)
  LLM_BASE_URL   for openai_compat (Groq, OpenRouter, local Ollama ...)
  LLM_TIMEOUT_S  per-request timeout, default 4.0 (p95 budget is 5s)
  LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M  USD per 1M tokens, for cost reporting.
      Defaults are placeholders — set them from the provider's current price page.

Only stdlib HTTP — no SDK dependency.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    model: str

    @property
    def cost_usd(self) -> float:
        pin = float(os.getenv("LLM_PRICE_IN_PER_M", "0.10"))
        pout = float(os.getenv("LLM_PRICE_OUT_PER_M", "0.40"))
        return self.tokens_in / 1e6 * pin + self.tokens_out / 1e6 * pout


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except Exception as e:  # timeout, DNS, proxy
        raise LLMError(f"{type(e).__name__}: {e}") from e


def complete_json(system: str, user: str, max_tokens: int = 700) -> LLMResult:
    """One call, JSON output. Raises LLMError on any failure (caller degrades to needs_human)."""
    provider = os.getenv("LLM_PROVIDER", "gemini")
    model = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")
    key = os.getenv("LLM_API_KEY", "")
    timeout = float(os.getenv("LLM_TIMEOUT_S", "4.0"))
    if provider == "none":
        raise LLMError("LLM disabled (LLM_PROVIDER=none)")
    if not key:
        raise LLMError("LLM_API_KEY is not set")
    t0 = time.time()

    if provider == "gemini":
        gen_cfg: dict = {"temperature": 0.2, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
        if os.getenv("LLM_THINKING_BUDGET", "0") != "":
            gen_cfg["thinkingConfig"] = {"thinkingBudget": int(os.getenv("LLM_THINKING_BUDGET", "0"))}
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": gen_cfg},
            {"x-goog-api-key": key}, timeout)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as e:
            raise LLMError(f"bad response: {str(data)[:300]}") from e
        u = data.get("usageMetadata", {})
        return LLMResult(text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                         time.time() - t0, model)

    if provider == "openai_compat":
        base = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        data = _post(f"{base}/chat/completions",
                     {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                     {"Authorization": f"Bearer {key}"}, timeout)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"bad response: {str(data)[:300]}") from e
        u = data.get("usage", {})
        return LLMResult(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), time.time() - t0, model)

    raise LLMError(f"unknown LLM_PROVIDER={provider}")
