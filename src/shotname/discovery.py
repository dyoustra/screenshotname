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

import os
import re
import stat as stat_module
import subprocess
import unicodedata
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

#: Leading bytes are enough to identify every format in the supported set.
_HEADER_BYTES = 16

_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

#: The separators macOS has been observed to put between the parts of a capture
#: name. U+202F is the one current releases write before AM/PM (AC-001); U+00A0
#: turns up in names written by older releases and by some third-party tools.
_SPACE = "[   ]"

_SCREENSHOT_NAME = re.compile(
    rf"^Screenshot{_SPACE}\d{{4}}-\d{{2}}-\d{{2}}{_SPACE}at{_SPACE}"
    rf"\d{{1,2}}\.\d{{2}}\.\d{{2}}({_SPACE}?[AP]M)?",
    re.IGNORECASE,
)

#: `mdfind` on an unindexed volume answers in milliseconds; the timeout is a
#: guard against a wedged `mds`, not a normal-path budget.
_MDFIND_TIMEOUT = 15.0


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
        return bool(self.st_flags & SF_DATALESS)


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


def _normalized(path: Path) -> str:
    """A comparison key that survives HFS/APFS Unicode normalization drift.

    `os.scandir` hands back the name as stored, while Spotlight answers in NFC;
    an accented filename otherwise looks like two different files.
    """
    return unicodedata.normalize("NFC", str(path))


class _ScreenCaptureIndex:
    """Spotlight's `kMDItemIsScreenCapture` set, cached one directory at a time.

    Asking `mdls` per file costs a subprocess per file, which on a few thousand
    captures is minutes of pure overhead. One `mdfind` answers for a whole
    directory instead. Only the most recently queried directory is retained, so
    the cache is bounded by one directory rather than by the corpus (AC-009) —
    which is free in practice because the walk finishes each directory before
    moving to the next.
    """

    def __init__(self) -> None:
        self._directory: Path | None = None
        self._captures: frozenset[str] = frozenset()

    def contains(self, path: Path) -> bool:
        directory = path.parent
        if directory != self._directory:
            self._directory = directory
            self._captures = _query_screen_captures(directory)
        return _normalized(path) in self._captures


def _query_screen_captures(directory: Path) -> frozenset[str]:
    """The screen captures Spotlight knows about directly inside `directory`.

    A Spotlight query is advisory: an unindexed volume, a disabled index, or a
    missing `mdfind` all mean "no answer", never "not a screenshot" — the
    filename pattern is still consulted afterwards.
    """
    try:
        completed = subprocess.run(
            [
                "mdfind",
                "-0",
                "-onlyin",
                str(directory),
                "kMDItemIsScreenCapture == 1",
            ],
            capture_output=True,
            timeout=_MDFIND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    found = (Path(os.fsdecode(raw)) for raw in completed.stdout.split(b"\0") if raw)
    # `-onlyin` searches the whole subtree; keeping only this directory's own
    # entries is what bounds the cache to one directory.
    return frozenset(_normalized(path) for path in found if path.parent == directory)


class RealFsProbe:
    """The production probe: `os.scandir`, `lstat`, `mdfind`, and magic bytes."""

    def __init__(self) -> None:
        self._captures = _ScreenCaptureIndex()

    def walk(self, root: Path, *, recursive: bool) -> Iterator[Path]:
        """Yield files under `root`, never following directory symlinks (AC-005)."""
        pending = [root]
        while pending:
            directory = pending.pop()
            subdirectories: list[Path] = []
            with os.scandir(directory) as entries:
                # Sorting one directory's listing keeps the plan reproducible
                # run to run; `scandir` order is the directory's own order and
                # is not stable across filesystems.
                for entry in sorted(entries, key=lambda item: item.name):
                    if entry.name.startswith("."):
                        continue
                    # Symlinks are never followed and never yielded: a link to a
                    # directory would revisit files reachable another way, and a
                    # link to a file would rename the link rather than the
                    # capture it points at (AC-005).
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            subdirectories.append(Path(entry.path))
                        continue
                    yield Path(entry.path)
            pending.extend(reversed(subdirectories))

    def facts(self, path: Path) -> FileFacts:
        status = path.lstat()
        is_symlink = stat_module.S_ISLNK(status.st_mode)
        is_dir = stat_module.S_ISDIR(status.st_mode)
        return FileFacts(
            path=path,
            size=status.st_size,
            st_flags=getattr(status, "st_flags", 0),
            mtime=status.st_mtime,
            birthtime=getattr(status, "st_birthtime", status.st_mtime),
            inode=status.st_ino,
            is_symlink=is_symlink,
            is_dir=is_dir,
            is_screen_capture=(
                not is_dir and not is_symlink and self._captures.contains(path)
            ),
        )

    def sniff_format(self, path: Path) -> str | None:
        """The image format from the file's leading bytes, or None if unknown.

        Reads a header rather than trusting the extension, because the corpus
        contains `.png` files that are not PNGs and a `.PNG` that is.
        """
        try:
            with path.open("rb") as handle:
                header = handle.read(_HEADER_BYTES)
        except OSError:
            return None
        for prefix, image_format in _MAGIC_PREFIXES:
            if header.startswith(prefix):
                return image_format
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "webp"
        return None


def matches_screenshot_name(name: str) -> bool:
    """Whether `name` looks like a macOS screenshot filename.

    Tolerates U+202F as well as U+0020 before the AM/PM marker (AC-001).
    """
    return _SCREENSHOT_NAME.match(name) is not None


def discover_candidates(
    settings: RunSettings, *, probe: FsProbe
) -> Iterator[Candidate]:
    """Walk `settings.root` and classify each file, lazily (AC-009).

    Every inspected file is yielded, included or not, because the plan records a
    reason per file. The order of the three tests is load-bearing: sniffing the
    format is the only step that opens the file, so it has to come after the
    dataless check for AC-006 to hold.
    """
    for path in probe.walk(settings.root, recursive=settings.recursive):
        facts = probe.facts(path)

        is_screenshot = facts.is_screen_capture or matches_screenshot_name(path.name)
        if not (is_screenshot or settings.all_images):
            yield Candidate(path, facts, included=False, excluded=Reason.NOT_SCREENSHOT)
            continue

        if facts.is_dataless and not settings.materialize:
            yield Candidate(path, facts, included=False, excluded=Reason.DATALESS)
            continue

        if probe.sniff_format(path) not in SUPPORTED_IMAGE_FORMATS:
            yield Candidate(
                path, facts, included=False, excluded=Reason.UNSUPPORTED_FORMAT
            )
            continue

        yield Candidate(path, facts, included=True, excluded=None)


def summarize(candidates: Iterable[Candidate]) -> DiscoveryStats:
    """Fold a candidate stream into the counts the report needs."""
    inspected = 0
    included = 0
    dataless_skipped = 0
    dataless_bytes = 0
    for candidate in candidates:
        inspected += 1
        if candidate.included:
            included += 1
        elif candidate.excluded is Reason.DATALESS:
            dataless_skipped += 1
            dataless_bytes += candidate.facts.size
    return DiscoveryStats(
        inspected=inspected,
        included=included,
        dataless_skipped=dataless_skipped,
        dataless_bytes=dataless_bytes,
    )


def format_discovery_summary(stats: DiscoveryStats) -> str:
    """The human-readable line stating the dataless count and nominal bytes."""
    noun = "file" if stats.dataless_skipped == 1 else "files"
    return (
        f"inspected {stats.inspected} files, {stats.included} candidates; "
        f"skipped {stats.dataless_skipped} dataless (iCloud-evicted) {noun} "
        f"totalling {stats.dataless_bytes} nominal bytes"
    )
