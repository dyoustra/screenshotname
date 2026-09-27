"""The naming prompt, and its version.

`BUILTIN_PROMPT_VERSION` is part of the model cache key, so editing the built-in
prompt invalidates every cached suggestion without invalidating cached OCR
(AC-043).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import UsageError

#: Bump whenever `BUILTIN_PROMPT` changes, so cached suggestions are invalidated.
BUILTIN_PROMPT_VERSION = "v0"

#: The prompt `--print-prompt` emits and `--prompt-template` replaces (AC-064).
BUILTIN_PROMPT = """\
You are naming a macOS screenshot from what is visibly in it.

You are given the screenshot and the text an on-device OCR pass extracted from it
at full resolution, topmost line first. Trust the OCR text for spelling of proper
nouns; trust the image for layout and for which application is in the foreground.

Answer with a single JSON object and nothing else:

  {"app": "<application name or null>", "subject": "<what it shows>",
   "confidence": <0.0-1.0>}

- "app" is the application the screenshot was taken in, as a human would name it
  ("Slack", "Safari", "Terminal"). Use null when you cannot tell; never guess and
  never write "Unknown".
- "subject" is two to five words saying what this particular capture shows, in
  lowercase. Prefer the concrete proper nouns on screen ("stripe outage thread")
  over generic description ("a chat conversation"). No dates: the filename
  already carries one.
- "confidence" is how sure you are of "subject".
"""


def load_prompt(path: Path | None) -> str:
    """The prompt at `path`, or `BUILTIN_PROMPT` when `path` is None (AC-064)."""
    if path is None:
        return BUILTIN_PROMPT
    try:
        return path.read_text(encoding="utf-8")
    except OSError as unreadable:
        raise UsageError(
            f"cannot read the prompt template at {path}: {unreadable}"
        ) from unreadable


def prompt_version(path: Path | None) -> str:
    """The cache-key version for a prompt: the built-in tag, or a file digest.

    Digesting the template's contents rather than its path is what makes editing
    a template invalidate the suggestions it produced (AC-043).
    """
    if path is None:
        return BUILTIN_PROMPT_VERSION
    digest = hashlib.blake2b(load_prompt(path).encode("utf-8"), digest_size=8)
    return digest.hexdigest()
