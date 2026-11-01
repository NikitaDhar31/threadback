"""Mask secrets (API keys, tokens, passwords) before text is saved or shown to Claude.

Chats and commit messages often contain pasted keys. Everything ThreadBack writes to
~/.threadback goes through store.save, and everything it reads from a folder goes
through digest.build; both call redact() so a secret never lands in either.

Best effort: well-known key formats plus `name = value` pairs whose name says secret.
A random string with no recognizable shape or label can still get through.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

# Whole-match secrets: the shape alone says what it is.
_TOKENS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\bsk-ant-[A-Za-z0-9_\-]{20,}",                     # Anthropic
    r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}",      # OpenAI
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}",       # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bglpat-[A-Za-z0-9_\-]{20,}",                      # GitLab
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",                    # AWS access key id
    r"\bAIza[0-9A-Za-z_\-]{35}",                         # Google API key
    r"\bya29\.[0-9A-Za-z_\-]{20,}",                      # Google OAuth token
    r"\bxox[abposr]-[A-Za-z0-9\-]{10,}",                 # Slack
    r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}",    # Stripe
    r"\bhf_[A-Za-z0-9]{30,}",                            # Hugging Face
    r"\bnpm_[A-Za-z0-9]{36}\b",                          # npm
    r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",  # JWT
]
_TOKEN_RE = re.compile("|".join(f"(?:{t})" for t in _TOKENS))

# Keep the label, mask the value.
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9_\-.=+/]{16,}")
_URL_AUTH_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")
_SECRET_NAME = (r"[A-Za-z0-9_.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|"
                r"access[_\-]?key|private[_\-]?key|client[_\-]?secret|credential)[A-Za-z0-9_.\-]*")
_ASSIGN_RE = re.compile(
    rf"(?i)(\b{_SECRET_NAME}[\"']?\s*(?::|=|=>)\s*)(\"[^\"\s]{{6,}}\"|'[^'\s]{{6,}}'|[^\s,;&\"'`)\]}}]{{6,}})")


def redact(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    text = _TOKEN_RE.sub(MASK, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _URL_AUTH_RE.sub(lambda m: f"{m.group(1)}{MASK}{m.group(2)}", text)
    text = _ASSIGN_RE.sub(lambda m: m.group(1) + (m.group(2) if MASK in m.group(2) else MASK), text)
    return text


def redact_obj(obj):
    """redact() every string inside dicts and lists (keys are left alone)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj
