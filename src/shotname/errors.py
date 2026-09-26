"""Exit codes and the exception types the pipeline raises across module lines."""

from __future__ import annotations

#: Bad invocation: a missing API key, a conflicting flag, an unreadable plan.
EXIT_USAGE = 2

#: The target directory could not be read. On macOS this is almost always TCC.
EXIT_PERMISSION = 3

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
