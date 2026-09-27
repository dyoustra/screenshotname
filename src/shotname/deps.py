"""The three seams a run reaches the world through, and the one place they are built.

Keeping construction in a single function is what makes the suite total: a test
replaces `default_deps` and from then on nothing can open a socket, call into
Vision, or query Spotlight by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .discovery import FsProbe, RealFsProbe
from .model import Transport
from .ocr import Ocr, VisionOcr
from .settings import API_KEY_ENV_VAR, RunSettings
from .transports import AnthropicTransport, OllamaTransport


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
    transport: Transport
    if settings.local:
        transport = OllamaTransport(
            host=settings.ollama_host, model=settings.ollama_model
        )
    else:
        transport = AnthropicTransport(api_key=os.environ.get(API_KEY_ENV_VAR))
    return Deps(probe=RealFsProbe(), ocr=VisionOcr(), transport=transport)
