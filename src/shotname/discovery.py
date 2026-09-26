"""Enumeration and candidate identification.

Three macOS facts drive this module. Screenshot filenames embed U+202F NARROW
NO-BREAK SPACE rather than U+0020 (AC-001); the authoritative signal is Spotlight's
`kMDItemIsScreenCapture`, not the filename, so localized and hand-renamed captures
still count (AC-002); and an iCloud-evicted file has `SF_DATALESS` in
`stat.st_flags`, where reading it would silently pull the bytes down (AC-006).

`FsProbe` is the seam for all three, because none of them can be simulated on an
ordinary temporary file.

Discovery yields lazily, so peak memory is a function of the pipeline's window
rather than of corpus size (AC-009).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .plan import Reason
from .settings import RunSettings

#: `stat.st_flags` bit macOS sets on a dataless (iCloud-evicted) file.
SF_DATALESS = 0x4000_0000

#: The image formats the tool will process. Exactly four (AC-008).
SUPPORTED_IMAGE_FORMATS: frozenset[str] = frozenset({"jpeg", "png", "gif", "webp"})


@dataclass(frozen=True)
class FileFacts:
    """Everything one `stat` plus one Spotlight query tells us about a path."""

    path: Path
    size: int
    st_flags: int
    mtime: float
    birthtime: float
    inode: int
    is_symlink: bool
    is_dir: bool
    is_screen_capture: bool

    @property
    def is_dataless(self) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class Candidate:
    """One inspected file, and whether the run will act on it.

    Excluded candidates are still yielded: the plan records a per-file exclusion
    reason for every file it inspected (AC-008, AC-029).
    """

    path: Path
    facts: FileFacts
    included: bool
    excluded: Reason | None


@dataclass(frozen=True)
class DiscoveryStats:
    """The counts the run report quotes back (AC-006)."""

    inspected: int
    included: int
    dataless_skipped: int
    dataless_bytes: int


class FsProbe(Protocol):
    """The filesystem seam: traversal, per-path facts, and format sniffing."""

    def walk(self, root: Path, *, recursive: bool) -> Iterator[Path]: ...

    def facts(self, path: Path) -> FileFacts: ...

    def sniff_format(self, path: Path) -> str | None: ...


class RealFsProbe:
    """The production probe: `os.scandir`, `lstat`, `mdls`, and magic bytes."""

    def walk(self, root: Path, *, recursive: bool) -> Iterator[Path]:
        """Yield files under `root`, never following directory symlinks (AC-005)."""
        raise NotImplementedError

    def facts(self, path: Path) -> FileFacts:
        raise NotImplementedError

    def sniff_format(self, path: Path) -> str | None:
        """The image format from the file's leading bytes, or None if unknown."""
        raise NotImplementedError


def matches_screenshot_name(name: str) -> bool:
    """Whether `name` looks like a macOS screenshot filename.

    Tolerates U+202F as well as U+0020 before the AM/PM marker (AC-001).
    """
    raise NotImplementedError


def discover_candidates(settings: RunSettings, *, probe: FsProbe) -> Iterator[Candidate]:
    """Walk `settings.root` and classify each file, lazily (AC-009)."""
    raise NotImplementedError


def summarize(candidates: Iterable[Candidate]) -> DiscoveryStats:
    """Fold a candidate stream into the counts the report needs."""
    raise NotImplementedError


def format_discovery_summary(stats: DiscoveryStats) -> str:
    """The human-readable line stating the dataless count and nominal bytes."""
    raise NotImplementedError
