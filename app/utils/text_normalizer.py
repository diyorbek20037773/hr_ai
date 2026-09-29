"""Non-destructive text normalisation used for matching (never for display)."""

from __future__ import annotations

import re
import unicodedata

# Every apostrophe-like character used in Uzbek Latin (o', g', ...) collapses to ASCII '
_APOSTROPHES = "'`’ʻʼ‘´ʹ′"
_APOSTROPHE_RE = re.compile(f"[{re.escape(_APOSTROPHES)}]")
_SPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT = " .,;:!?\"()[]{}-"


def normalize_text(value: str | None) -> str:
    """trim, NFC, unify apostrophes, collapse whitespace, casefold, strip trailing punctuation."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _APOSTROPHE_RE.sub("'", text)
    text = _SPACE_RE.sub(" ", text).strip()
    text = text.casefold()
    text = text.rstrip(_TRAILING_PUNCT).lstrip(" \"([{")
    return text
