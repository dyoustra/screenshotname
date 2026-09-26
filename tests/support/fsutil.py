"""Filesystem observation helpers: directory snapshots and extended attributes."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EntrySnapshot:
    """Everything AC-028 requires to be unchanged by a dry run."""

    name: str
    inode: int
    mtime: float
    st_flags: int
    size: int


def snapshot_dir(root: Path) -> dict[str, EntrySnapshot]:
    snapshot: dict[str, EntrySnapshot] = {}
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            continue
        stat = entry.lstat()
        snapshot[entry.name] = EntrySnapshot(
            name=entry.name,
            inode=stat.st_ino,
            mtime=stat.st_mtime,
            st_flags=getattr(stat, "st_flags", 0),
            size=stat.st_size,
        )
    return snapshot


def inode_set(root: Path) -> set[int]:
    """The assertion behind AC-027: identical inodes means nothing was lost."""
    return {entry.lstat().st_ino for entry in root.iterdir() if not entry.is_dir()}


def birthtime(path: Path) -> float:
    stat = path.lstat()
    return float(getattr(stat, "st_birthtime", stat.st_mtime))


# Extended attributes are set through /usr/bin/xattr rather than os.setxattr,
# which CPython only provides on Linux.
XATTR_BIN = shutil.which("xattr")


def xattr_available() -> bool:
    return XATTR_BIN is not None


def set_xattr(path: Path, name: str, value: str) -> None:
    assert XATTR_BIN is not None
    subprocess.run([XATTR_BIN, "-w", name, value, str(path)], check=True)


def get_xattr(path: Path, name: str) -> str | None:
    assert XATTR_BIN is not None
    result = subprocess.run(
        [XATTR_BIN, "-p", name, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))
