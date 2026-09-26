"""The `shotname` command line: `run` and `undo`.

This module parses flags into a `RunSettings` and hands off. Nothing else — every
decision the flags imply (which endpoint, whether to rename, what the cache key
is) is derived downstream from the settings object, so there is exactly one place
a flag's meaning lives.

`default_deps` is looked up through this module's globals so the test suite can
replace it, which is what keeps the network, Vision, and Spotlight out of the
suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .deps import Deps, default_deps
from .settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MIN_OCR_CHARS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SEED,
    Resolution,
    RunSettings,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Rename screenshots after what is visibly in them.",
)


def _dispatch_run(settings: RunSettings, deps: Deps) -> int:
    """Execute one `run` invocation and return the process exit code."""
    raise NotImplementedError


def _dispatch_undo(run_id: str | None) -> int:
    """Execute one `undo` invocation and return the process exit code."""
    raise NotImplementedError


@app.command()
def run(
    root: Annotated[
        Path,
        typer.Argument(help="Directory of screenshots to rename."),
    ],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually rename. Off by default."),
    ] = False,
    plan: Annotated[
        Path | None,
        typer.Option("--plan", help="Replay renames from a previously written plan."),
    ] = None,
    all_images: Annotated[
        bool,
        typer.Option("--all-images", help="Treat every image as a candidate."),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option("--recursive", help="Descend into subdirectories."),
    ] = False,
    materialize: Annotated[
        bool,
        typer.Option(
            "--materialize", help="Read iCloud-evicted files instead of skipping."
        ),
    ] = False,
    sample: Annotated[
        int | None,
        typer.Option(
            "--sample", help="Process N files synchronously, changing nothing."
        ),
    ] = None,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Seed for sample selection."),
    ] = DEFAULT_SEED,
    max_cost: Annotated[
        float | None,
        typer.Option("--max-cost", help="Abort if the estimate exceeds this many USD."),
    ] = None,
    min_ocr_chars: Annotated[
        int,
        typer.Option(
            "--min-ocr-chars", help="OCR characters below which a capture may abstain."
        ),
    ] = DEFAULT_MIN_OCR_CHARS,
    model: Annotated[
        str,
        typer.Option("--model", help="Naming model identifier."),
    ] = DEFAULT_MODEL,
    resolution: Annotated[
        Resolution,
        typer.Option("--resolution", help="Image detail tier sent to the model."),
    ] = Resolution.STANDARD,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync", help="Dispatch synchronously instead of via the Batch API."
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait", help="Poll submitted jobs to completion in this invocation."
        ),
    ] = False,
    poll_interval: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between job polls."),
    ] = DEFAULT_POLL_INTERVAL,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Maximum simultaneous in-flight requests."),
    ] = DEFAULT_CONCURRENCY,
    local: Annotated[
        bool,
        typer.Option(
            "--local", help="Use the local Ollama backend; nothing leaves the machine."
        ),
    ] = False,
    ollama_host: Annotated[
        str,
        typer.Option("--ollama-host", help="Base URL of the Ollama server."),
    ] = DEFAULT_OLLAMA_HOST,
    ollama_model: Annotated[
        str,
        typer.Option("--ollama-model", help="Local vision model to use."),
    ] = DEFAULT_OLLAMA_MODEL,
    prompt_template: Annotated[
        Path | None,
        typer.Option("--prompt-template", help="Use the prompt at this path."),
    ] = None,
    print_prompt: Annotated[
        bool,
        typer.Option(
            "--print-prompt", help="Write the built-in prompt to stdout and exit."
        ),
    ] = False,
) -> None:
    """Propose names for the screenshots in ROOT, renaming only with --apply."""
    settings = RunSettings(
        root=root,
        all_images=all_images,
        recursive=recursive,
        materialize=materialize,
        apply=apply,
        plan=plan,
        max_cost_usd=max_cost,
        min_ocr_chars=min_ocr_chars,
        sample=sample,
        seed=seed,
        sync=sync,
        wait=wait,
        poll_interval=poll_interval,
        concurrency=concurrency,
        local=local,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        model=model,
        resolution=resolution,
        prompt_template=prompt_template,
        print_prompt=print_prompt,
    )
    raise typer.Exit(_dispatch_run(settings, default_deps(settings)))


@app.command()
def undo(
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Undo this run instead of the most recent one."),
    ] = None,
) -> None:
    """Restore the renames of a recorded run to their original filenames."""
    raise typer.Exit(_dispatch_undo(run_id))


def main() -> None:
    """Console-script entry point."""
    app()
