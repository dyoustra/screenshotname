"""The smoke results protocol: one JSON line per check to $SFO_SMOKE_RESULTS."""

from __future__ import annotations

import json
import os


def report(seam: str, check: str, level: str, detail: str, cost_usd: float | None = None) -> None:
    line: dict[str, object] = {"seam": seam, "check": check, "level": level, "detail": detail}
    if cost_usd is not None:
        line["costUsd"] = cost_usd
    target = os.environ.get("SFO_SMOKE_RESULTS")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
