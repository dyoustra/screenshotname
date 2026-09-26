"""The naming prompt, and its version.

`BUILTIN_PROMPT_VERSION` is part of the model cache key, so editing the built-in
prompt invalidates every cached suggestion without invalidating cached OCR
(AC-043).
"""

from __future__ import annotations

from pathlib import Path

#: Bump whenever `BUILTIN_PROMPT` changes, so cached suggestions are invalidated.
BUILTIN_PROMPT_VERSION = "v0"

#: The prompt `--print-prompt` emits and `--prompt-template` replaces (AC-064).
BUILTIN_PROMPT = ""


def load_prompt(path: Path | None) -> str:
    """The prompt at `path`, or `BUILTIN_PROMPT` when `path` is None (AC-064)."""
    raise NotImplementedError


def prompt_version(path: Path | None) -> str:
    """The cache-key version for a prompt: the built-in tag, or a file digest."""
    raise NotImplementedError
