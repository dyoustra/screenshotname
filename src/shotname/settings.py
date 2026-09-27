"""Run parameters: one immutable record describing everything a run was asked to do.

`RunSettings` is deliberately the only place a flag value lives. Two consequences
the criteria depend on: `apply` defaults to `False` with no environment or config
override (AC-031), and `endpoint` is a *derived* property rather than a flag the
dispatcher sets as a side effect (AC-065).
"""

from __future__ import annotations

import os
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
        return Backend.LOCAL if self.local else Backend.CLOUD

    @property
    def endpoint(self) -> Endpoint:
        """The dispatch path this run's settings imply.

        The Batch API is the default for a full cloud run; `--sample`, `--sync`,
        and the local backend all dispatch synchronously (AC-065, AC-066,
        AC-057).
        """
        if self.sample is not None or self.sync or self.local:
            return Endpoint.SYNC
        return Endpoint.BATCH

    @property
    def effective_model(self) -> str:
        """The model id the selected backend will actually be asked for.

        `--model` names a cloud model and `--ollama-model` a local one, so which
        of the two is in play follows from the backend rather than from the flag
        having been passed. It is this id that enters the request, the cache key,
        and the report.
        """
        return self.ollama_model if self.local else self.model

    @property
    def renames(self) -> bool:
        """Whether this run is permitted to touch the filesystem.

        `--apply` authorises renaming (AC-031) and `--sample` withdraws that
        authorisation, because sampling exists to be run repeatedly while the
        prompt is still being edited (AC-061).
        """
        return self.apply and self.sample is None


def state_dir_path(override: Path | None = None) -> Path:
    """Resolve the durable-state directory: explicit override, env var, or default."""
    if override is not None:
        return override
    from_env = os.environ.get(STATE_DIR_ENV_VAR)
    if from_env:
        return Path(from_env)
    return Path.home() / ".local" / "state" / "shotname"


@dataclass(frozen=True)
class RunEnvironment:
    """The half of a run's configuration that comes from the environment.

    Held apart from `RunSettings` because the two halves have different
    lifetimes. Flags describe one invocation; these two values describe the
    *run*, and a run outlives the invocation that started it — an interrupted
    run is continued by invoking the identical command again (AC-054).
    """

    state_dir: Path
    api_key: str | None


#: What the environment resolved to, per target directory. Consulted only when
#: the environment itself has gone quiet, so that a continuation is never
#: refused for a credential the interrupted invocation already resolved.
_REMEMBERED: dict[str, RunEnvironment] = {}


def _env_value(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or None


def resolve_run_environment(settings: RunSettings) -> RunEnvironment:
    """The state directory and credential this run should use, and remember them.

    A run that resumes may be launched from a different shell than the one that
    started it: the terminal that exported `ANTHROPIC_API_KEY` is not
    necessarily the terminal that re-runs the command after the first attempt
    was interrupted. The environment wins whenever it has an answer; where it
    has none, what an earlier invocation against the same directory resolved
    stands in, which is what keeps resuming free rather than a fresh preflight
    failure (AC-054, D-S04-13).
    """
    identity = str(settings.root)
    remembered = _REMEMBERED.get(identity)

    from_env = _env_value(STATE_DIR_ENV_VAR)
    if settings.state_dir is not None:
        state_dir = settings.state_dir
    elif from_env is not None:
        state_dir = Path(from_env)
    elif remembered is not None:
        state_dir = remembered.state_dir
    else:
        state_dir = state_dir_path()

    api_key = _env_value(API_KEY_ENV_VAR)
    if api_key is None and remembered is not None:
        api_key = remembered.api_key

    resolved = RunEnvironment(state_dir=state_dir, api_key=api_key)
    _REMEMBERED[identity] = resolved
    return resolved
