"""`shotname` — rename screenshots after what is visibly in them.

The package is a thin stack of single-responsibility modules, in dependency
order: `settings` and `errors` at the bottom, then the pure text/filename layer
(`slug`, `naming`, `hashing`, `collisions`), then the I/O seams (`discovery`,
`ocr`, `model`), then the durable state (`cache`, `journal`, `plan`, `batch`),
with `cli` wiring them together.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
