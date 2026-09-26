"""The three seams a run reaches the world through, and the one place they are built.

Keeping construction in a single function is what makes the suite total: a test
replaces `default_deps` and from then on nothing can open a socket, call into
Vision, or query Spotlight by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from .discovery import FsProbe
from .model import Transport
from .ocr import Ocr
from .settings import RunSettings


@dataclass(frozen=True)
class Deps:
    """The filesystem probe, the OCR engine, and the model transport."""

    probe: FsProbe
    ocr: Ocr
    transport: Transport


def default_deps(settings: RunSettings) -> Deps:
    """Build the production collaborators `settings` calls for.

    The transport is the cloud one unless `--local` selected Ollama; both satisfy
    the same protocol, which is what AC-057 checks.
    """
    raise NotImplementedError
