"""Run parameters: one immutable record describing everything a run was asked to do.

`RunSettings` is deliberately the only place a flag value lives. Two consequences
the criteria depend on: `apply` defaults to `False` with no environment or config
override (AC-031), and `endpoint` is a *derived* property rather than a flag the
dispatcher sets as a side effect (AC-065).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

#: The variable the cloud backend reads its credential from (AC-050).
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

#: Overrides `~/.local/state/shotname/`. The test suite redirects durable state here.
STATE_DIR_ENV_VAR = "SHOTNAME_STATE_DIR"

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_CONCURRENCY = 8
DEFAULT_MIN_OCR_CHARS = 12
DEFAULT_SEED = 0
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2-vision"
DEFAULT_POLL_INTERVAL = 10.0


class Resolution(str, Enum):
    """The downscale tier the image is sent at. Exactly two values (AC-076)."""

    STANDARD = "standard"
    HIGH = "high"


class Endpoint(str, Enum):
    """Which dispatch path a request took. Batch for full runs, sync otherwise."""

    SYNC = "sync"
    BATCH = "batch"


class Backend(str, Enum):
    CLOUD = "cloud"
    LOCAL = "local"


@dataclass(frozen=True)
class RunSettings:
    """Everything one invocation of `shotname run` was asked to do."""

    root: Path
    all_images: bool = False
    recursive: bool = False
    materialize: bool = False
    apply: bool = False
    plan: Path | None = None
    max_cost_usd: float | None = None
    min_ocr_chars: int = DEFAULT_MIN_OCR_CHARS
    sample: int | None = None
    seed: int = DEFAULT_SEED
    sync: bool = False
    wait: bool = False
    poll_interval: float = DEFAULT_POLL_INTERVAL
    concurrency: int = DEFAULT_CONCURRENCY
    local: bool = False
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    model: str = DEFAULT_MODEL
    resolution: Resolution = Resolution.STANDARD
    prompt_template: Path | None = None
    print_prompt: bool = False
    state_dir: Path | None = None

    @property
    def backend(self) -> Backend:
        raise NotImplementedError

    @property
    def endpoint(self) -> Endpoint:
        """The dispatch path this run's settings imply.

        The Batch API is the default for a full cloud run; `--sample`, `--sync`,
        and the local backend all dispatch synchronously (AC-065, AC-066,
        AC-057).
        """
        raise NotImplementedError


def state_dir_path(override: Path | None = None) -> Path:
    """Resolve the durable-state directory: explicit override, env var, or default."""
    raise NotImplementedError
