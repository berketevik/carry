"""Best-effort secret masking for text Carry is about to persist.

This is a reduction of risk, not a guarantee. The patterns below catch common
token shapes; a novel credential format, a split secret or an unusual encoding
will pass through. Any surface that shows "secrets are masked" must say
best-effort, and the audit of these limitations is a release task.
"""
import re

PATTERNS = (
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.I)),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
    ("github", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z._\-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}")),
    ("assignment", re.compile(r"(?i)\b(password|secret|api[_-]?key|token)\s*[:=]\s*\S+")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

PLACEHOLDER = "***MASKED***"


def mask(text):
    """Return (masked_text, categories_found)."""
    if not text:
        return text or "", []
    found = []
    for name, pattern in PATTERNS:
        text, count = pattern.subn(PLACEHOLDER, text)
        if count:
            found.append(name)
    return text, found
