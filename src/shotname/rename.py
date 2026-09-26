"""The tool's single `rename(2)` call site.

Everything that renames a file goes through `rename_file`, and reaches it through
this module rather than by binding it at import time. That is what lets the tests
count renames, fail the nth one, and assert journal-before-rename ordering by
patching one name (AC-035, AC-036, AC-053).

A rename moves a directory entry, which is also why the metadata criteria hold
for free: inode, xattrs, mtime, and st_birthtime all belong to the inode, not to
the name (AC-016, AC-027, AC-058, AC-059).
"""

from __future__ import annotations

from pathlib import Path


def rename_file(old: Path, new: Path) -> None:
    """Rename `old` to `new` without following symlinks or replacing `new`."""
    raise NotImplementedError
