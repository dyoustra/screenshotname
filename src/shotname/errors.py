"""Exit codes and the exception types the pipeline raises across module lines."""

from __future__ import annotations

from pathlib import Path

from .settings import API_KEY_ENV_VAR

#: Bad invocation: a missing API key, a conflicting flag, an unreadable plan.
EXIT_USAGE = 2

#: The target directory could not be read. On macOS this is almost always TCC.
EXIT_PERMISSION = 3

#: `undo` left at least one file alone: changed contents, or an occupied name.
EXIT_UNDO_INCOMPLETE = 4

#: SIGINT. The shell convention is 128 + SIGINT.
EXIT_INTERRUPTED = 130


class ShotnameError(Exception):
    """Base class for every error the tool reports without a traceback."""

    exit_code: int = 1


class UsageError(ShotnameError):
    exit_code = EXIT_USAGE


class PermissionDeniedError(ShotnameError):
    exit_code = EXIT_PERMISSION


class RateLimitedError(ShotnameError):
    """HTTP 429. `retry_after` carries the provider's retry-after header."""

    def __init__(self, *, retry_after: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class BackendUnavailableError(ShotnameError):
    """A configured backend host could not be reached."""


class SchemaInvalidError(ShotnameError):
    """A model response did not validate against the name-suggestion schema."""


#: Where a human actually grants the permission, spelled as the UI spells it.
FULL_DISK_ACCESS_PANE = "System Settings > Privacy & Security > Full Disk Access"

#: What to do about a missing credential, naming the variable to set (AC-050).
API_KEY_HINT = (
    f"no API key found: set {API_KEY_ENV_VAR} in the environment, "
    "or pass --local to name screenshots with a local Ollama model instead. "
    "Nothing was read and nothing was renamed."
)


def full_disk_access_message(root: Path) -> str:
    """The whole user experience of the most likely first-run failure (AC-049).

    macOS TCC protects `~/Desktop`, `~/Documents`, and `~/Downloads`, and `sudo`
    does not help: the permission belongs to the terminal application that
    launched this process, not to the user.
    """
    return (
        f"macOS refused permission to read {root}.\n"
        f"Grant Full Disk Access to the terminal application you ran this from, in:\n"
        f"    {FULL_DISK_ACCESS_PANE}\n"
        "Then quit and reopen that application and run the command again."
    )
