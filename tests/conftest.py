"""Suite-wide fixtures.

Two things are true of every test here:

1. `shotname` writes its cache, journal, plans, and batch job records to
   `~/.local/state/shotname/`. An autouse fixture redirects that at a temporary
   directory so no test can see another test's durable state — or the real
   machine's.
2. Nothing reaches the network, the Vision framework, or Spotlight unless a
   test asks for it. `shotname.deps.default_deps` is the single constructor for
   the filesystem probe, the OCR engine, and the model transport; the `inject`
   fixture replaces it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.deps import Deps, default_deps
from shotname.discovery import FsProbe
from shotname.model import Transport
from shotname.ocr import Ocr
from shotname.settings import API_KEY_ENV_VAR, STATE_DIR_ENV_VAR, RunSettings

Inject = Callable[..., None]


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV_VAR, str(directory))
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-ant-test-key")
    yield directory


@pytest.fixture
def corpus_root(tmp_path: Path) -> Path:
    directory = tmp_path / "shots"
    directory.mkdir()
    return directory


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def inject(monkeypatch: pytest.MonkeyPatch) -> Inject:
    """Replace some or all of the run's collaborators.

    Anything not named keeps the real implementation, which is what lets AC-055
    and AC-056 exercise the genuine Ollama transport while still stubbing OCR.
    """
    real_default_deps = default_deps

    def _inject(
        *,
        probe: FsProbe | None = None,
        ocr: Ocr | None = None,
        transport: Transport | None = None,
    ) -> None:
        def factory(settings: RunSettings) -> Deps:
            if probe is not None and ocr is not None and transport is not None:
                return Deps(probe=probe, ocr=ocr, transport=transport)
            # Only reach for the real collaborators when one is not overridden,
            # so a fully stubbed test never constructs an API client.
            base = real_default_deps(settings)
            return Deps(
                probe=base.probe if probe is None else probe,
                ocr=base.ocr if ocr is None else ocr,
                transport=base.transport if transport is None else transport,
            )

        monkeypatch.setattr("shotname.deps.default_deps", factory)
        # Guard against `from .deps import default_deps` at the call site.
        monkeypatch.setattr("shotname.cli.default_deps", factory, raising=False)

    return _inject
