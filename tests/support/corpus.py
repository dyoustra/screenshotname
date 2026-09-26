"""The fixture corpus `.sfo/SPEC.md` calls for.

One directory containing, per the spec's Testing row: a name carrying U+202F
NARROW NO-BREAK SPACE, a `.PNG`, a case-collision pair, a near-blank image, and
a simulated dataless flag — plus the non-screenshot image AC-003 excludes, the
unsupported format AC-008 excludes, and a nested capture for AC-004.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.support.fakes import OverrideProbe
from tests.support.images import write_bmp, write_noise_png, write_solid_png

#: macOS writes this, not U+0020, between the seconds and the AM/PM marker.
NNBSP = " "

NARROW_SPACE_NAME = f"Screenshot 2026-01-14 at 3.42.11{NNBSP}PM.png"
UPPERCASE_EXT_NAME = "Screenshot 2026-01-15 at 9.01.02 AM.PNG"
LOCALIZED_NAME = "Bildschirmfoto 2026-01-20 um 10.11.12.png"
HAND_RENAMED_NAME = "slack thread from january.png"
PLAIN_IMAGE_NAME = "vacation-photo.png"
UNSUPPORTED_NAME = "Screenshot 2026-01-16 at 1.00.00 PM.bmp"
NEAR_BLANK_NAME = "Screenshot 2026-01-19 at 5.00.00 PM.png"
DATALESS_NAME = "Screenshot 2026-01-17 at 2.00.00 PM.png"
OCCUPIED_NAME = "Stripe-Dashboard.png"
NESTED_NAME = "Screenshot 2026-01-18 at 4.00.00 PM.png"


@dataclass(frozen=True)
class Corpus:
    root: Path
    narrow_space: Path
    uppercase_ext: Path
    localized: Path
    hand_renamed: Path
    plain_image: Path
    unsupported: Path
    near_blank: Path
    dataless: Path
    occupied: Path
    nested: Path

    @property
    def included(self) -> list[Path]:
        """The files a default (non-recursive, cloud) run should act on."""
        return [
            self.narrow_space,
            self.uppercase_ext,
            self.localized,
            self.hand_renamed,
            self.near_blank,
        ]

    def probe(self) -> OverrideProbe:
        return OverrideProbe(
            screen_capture={self.localized, self.hand_renamed},
            not_screen_capture={self.plain_image, self.occupied},
            dataless={self.dataless},
        )


def build_corpus(root: Path) -> Corpus:
    root.mkdir(parents=True, exist_ok=True)
    return Corpus(
        root=root,
        narrow_space=write_noise_png(root / NARROW_SPACE_NAME, seed=1),
        uppercase_ext=write_noise_png(root / UPPERCASE_EXT_NAME, seed=2),
        localized=write_noise_png(root / LOCALIZED_NAME, seed=3),
        hand_renamed=write_noise_png(root / HAND_RENAMED_NAME, seed=4),
        plain_image=write_noise_png(root / PLAIN_IMAGE_NAME, seed=5),
        unsupported=write_bmp(root / UNSUPPORTED_NAME),
        near_blank=write_solid_png(root / NEAR_BLANK_NAME),
        dataless=write_noise_png(root / DATALESS_NAME, seed=6),
        occupied=write_noise_png(root / OCCUPIED_NAME, seed=7),
        nested=write_noise_png(root / "sub" / NESTED_NAME, seed=8),
    )


def screenshot_name(index: int, *, extension: str = "png") -> str:
    """A unique, parseable capture name for bulk corpora.

    The date walks months as well as days so that a corpus of a few hundred
    files still yields a distinct capture date — and therefore a distinct
    proposed name — for every file, keeping collision behaviour out of tests
    that are not about collisions.
    """
    month = index // 28 + 1
    day = index % 28 + 1
    return f"Screenshot 2026-{month:02d}-{day:02d} at 1.00.00{NNBSP}PM.{extension}"


def build_collision_pair(root: Path) -> tuple[Path, Path]:
    """Two captures from the same day with different bytes.

    Same date prefix plus the same model-suggested subject means the same
    proposed filename, which is what the collision criteria need.
    """
    root.mkdir(parents=True, exist_ok=True)
    first = write_noise_png(
        root / f"Screenshot 2026-02-01 at 1.00.00{NNBSP}PM.png", seed=201
    )
    second = write_noise_png(
        root / f"Screenshot 2026-02-01 at 2.30.00{NNBSP}PM.png", seed=202
    )
    return first, second


def build_duplicate_pair(root: Path) -> tuple[Path, Path]:
    """Two captures with byte-identical contents and different capture dates.

    One content hash, so one model request (AC-068), but two files to rename.
    """
    root.mkdir(parents=True, exist_ok=True)
    first = write_noise_png(
        root / f"Screenshot 2026-04-01 at 1.00.00{NNBSP}PM.png", seed=301
    )
    second = root / f"Screenshot 2026-04-02 at 1.00.00{NNBSP}PM.png"
    second.write_bytes(first.read_bytes())
    return first, second


def build_bulk_corpus(root: Path, count: int) -> list[Path]:
    """`count` ordinary screenshots, each with distinct bytes and a distinct date."""
    root.mkdir(parents=True, exist_ok=True)
    return [
        write_noise_png(root / screenshot_name(index), seed=1000 + index)
        for index in range(count)
    ]
