"""Minimal OpenAI-compatible chat client (plain HTTP, stdlib only).

Used by the debate step. Endpoint/key/model come from the `trading` secret:
LLM_API_BASE (e.g. https://api.openai.com/v1), LLM_API_KEY, LLM_MODEL.
"""
from __future__ import annotations

import json
import urllib.request

from .secrets import secret


def chat(messages: list[dict], temperature: float = 0.0,
         timeout: int = 600, max_tokens: int | None = None) -> str:
    base = secret("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")
    key = secret("LLM_API_KEY")
    model = secret("LLM_MODEL", "gpt-4o-mini")
    if not key:
        raise RuntimeError("LLM_API_KEY not configured")
    body: dict = {"model": model, "temperature": temperature, "messages": messages}
    if max_tokens:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"] or ""
