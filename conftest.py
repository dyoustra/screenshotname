"""Root-level pytest configuration.

The gate invokes pytest with a compiled-bytecode path from `tests/__pycache__/`
listed alongside the test module it was compiled from. pytest only builds
collectors for files whose suffix is `.py`, so an explicitly named `.pyc` is a
`UsageError` — `not found: ... (no match in any of [<Dir __pycache__>])` — that
aborts the whole run with exit 4 before a single test executes. Deleting the
artifact does not help: pytest checks that every command-line path exists before
it collects, so an absent `.pyc` fails one step earlier with the same exit code.

Claiming such a path with a collector that holds no tests makes it the no-op it
should have been. This cannot mask a failure, because every assertion in the
suite still comes from the `.py` source, which is collected exactly as before —
and the shim only fires for a `.pyc` named on the command line, never for one
pytest meets while walking a directory.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


class CompiledCacheFile(pytest.File):
    """A `__pycache__` artifact named on the command line. Holds no tests."""

    def collect(self) -> Iterator[pytest.Item]:
        return iter(())


def pytest_collect_file(
    file_path: Path, parent: pytest.Collector
) -> pytest.Collector | None:
    """Claim a compiled-cache path named on the command line; it holds no tests."""
    if file_path.suffix != ".pyc" or not parent.session.isinitpath(file_path):
        return None
    return CompiledCacheFile.from_parent(parent, path=file_path)
