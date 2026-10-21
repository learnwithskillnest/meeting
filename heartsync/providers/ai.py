"""Optional AI text provider for writing *suggestions*.

AI_PROVIDER=rules (default) runs entirely locally with templates — no data
leaves the server and no credentials are needed.
AI_PROVIDER=anthropic calls the Claude Messages API when ANTHROPIC_API_KEY is
set. Any failure falls back to the rules provider, so missing or broken AI
credentials never break the product.

Callers only ever pass the member's *own*, non-sensitive profile fields, and
only when the member explicitly asks. Nothing produced here is saved or sent
on anyone's behalf.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from ..config import env, get_settings

log = logging.getLogger("heartsync.ai")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def provider_name() -> str:
    s = get_settings()
    if s.ai_provider == "anthropic" and env("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "rules"


def complete(system: str, prompt: str, max_tokens: int = 400) -> str | None:
    """Return model text, or None if no remote provider is available/working."""
    if provider_name() != "anthropic":
        return None
    body = json.dumps({
        "model": env("AI_MODEL", "claude-haiku-5-5"),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
        "x-api-key": env("ANTHROPIC_API_KEY") or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        return text.strip() or None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        # Never log prompt contents (they contain profile text).
        log.warning("AI provider unavailable, using rules fallback: %s", type(exc).__name__)
        return None
