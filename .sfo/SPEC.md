# Spec: `shotname` — screenshot content renamer

## What this is

`shotname` is a macOS command-line tool that renames screenshot files according to what is
visibly in them, turning `Screenshot 2026-01-14 at 3.42.11 PM.png` into
`2026-01-14-stripe-dashboard-mrr-chart.png`. It runs Apple Vision OCR on-device to extract text
at native resolution, then sends the downscaled image plus that OCR text to a vision model to
produce a short content slug. The slug is **app-first** — `2026-01-14-slack-stripe-outage-thread.png`
— because the goal is a folder that is legible when browsed, not one that is searchable:
Spotlight already indexes the text inside these images, so search is solved and consistency of
the leading token is what makes 4000 names scannable. It renames **in place**; the originals are
mutated and the journal is the only safety net. It is built for a one-shot backlog of ~4000 files, which means the
non-AI machinery is the point: dry-run by default, a durable append-only undo journal written
before every rename, a content-hash-keyed cache so re-runs and crash-resumes are nearly free,
deterministic collision resolution that is correct on case-insensitive APFS, and an explicit
abstain path for the large fraction of screenshots that genuinely have nothing to say.

## User stories

- **Try before trusting.** I have 4000 screenshots and no idea whether the names will be any
  good. I run `shotname ~/Desktop --sample 30`, get a table of `old → proposed` for 30 files,
  decide the names are too verbose, edit the prompt template, and run the sample again — having
  changed nothing on disk and spent a few cents.
- **See the whole plan.** I run `shotname ~/Desktop`. It changes nothing, prints the full
  proposed rename for every file, writes `plan.jsonl`, and tells me the estimated API cost and
  how many files it intends to skip and why.
- **Commit.** I run `shotname ~/Desktop --apply`. It shows a progress bar, survives rate limits,
  and reports at the end: renamed 3,204, abstained 691, skipped 105, failed 0.
- **Change my mind.** Two days later I decide the names are wrong. I run `shotname undo` and
  every file is back to its original name, with a report of any file it refused to touch because
  the contents changed since the rename.
- **Crash and resume.** The run dies at file 2,700 because my laptop slept. I re-run the same
  command; it picks up at 2,701 without re-OCRing or re-calling the API for anything already
  done.
- **Keep it off the network.** My screenshots contain client data under NDA. I run
  `shotname ~/Desktop --local`, and nothing leaves the machine.
- **Verify the run.** I can't eyeball 4000 names, so I read the post-run report: distribution of
  name lengths, count of duplicate slugs, abstention rate, and a random sample of 20 to inspect.

## Acceptance criteria

Each item is independently checkable by an automated test against a fixture directory.

### Enumeration and file identification

1. Given a directory containing a file whose name embeds U+202F NARROW NO-BREAK SPACE (e.g.
   `Screenshot 2026-01-14 at 3.42.11 PM.png`), that file appears in the candidate set.
2. Given a file whose Spotlight attribute `kMDItemIsScreenCapture` is true but whose name does
   not begin with `Screenshot` (e.g. `Bildschirmfoto ....png`, or a user-renamed capture), that
   file appears in the candidate set.
3. Given an image in the directory that is not a screen capture (`kMDItemIsScreenCapture` absent
   or false) and does not match the screenshot filename pattern, it is excluded from the
   candidate set; passing `--all-images` includes it.
4. Enumeration is non-recursive by default; `--recursive` includes subdirectories. With
   `--recursive`, symbolic links to directories are not followed.
5. Files with `SF_DATALESS` set in `stat.st_flags` (iCloud-evicted) are counted, reported by
   count and total nominal bytes, and skipped without being read. Passing `--materialize`
   processes them instead.
6. Non-image files and unsupported image formats (anything outside JPEG/PNG/GIF/WebP) are
   excluded with a per-file reason recorded in the plan.

### Name generation and sanitization

7. Every produced filename matches `^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+)$`
   — ASCII lowercase kebab-case, date-prefixed, single extension.
8. The date prefix equals the file's capture date derived from the original filename when
   parseable, else from `st_birthtime`, in the machine's local timezone.
9. No produced filename contains `/` or `:`, for any model output including model output that
   contains them (test: stub the model to return `3:42 PM / dashboard`).
10. Every produced filename is at most 255 **bytes** when UTF-8 encoded, including the
    extension. Truncation happens at a kebab-segment boundary, never mid-word, and never leaves
    a trailing `-`.
11. No produced filename begins with `.` or `-`, and none ends with a space or `.`.
12. The original file extension is preserved (lowercased); `.PNG` becomes `.png` and the file is
    not re-encoded.
13. Given the same image bytes, the same model, and the same prompt template version, two
    separate runs produce byte-identical proposed filenames.

### Collision handling

14. When two different files in the same run would produce the same final name, each gets a
    deterministic 6-character suffix derived from its content hash
    (`2026-01-14-terminal-output-a3f9c1.png`), and re-running produces the same suffixes.
15. Collision detection is case-folded and NFC-normalized: a proposed `stripe-dashboard.png`
    collides with an existing on-disk `Stripe-Dashboard.png` and is disambiguated rather than
    clobbering it.
16. No run ever reduces the number of files in the target directory. Asserted by comparing the
    set of inodes before and after `--apply`.
17. A proposed name identical to a file the tool did not touch (an unrelated pre-existing file)
    is treated as a collision and disambiguated.

### Dry run, apply, and plan output

18. A run without `--apply` leaves every candidate file's name, inode, `mtime`, and `st_flags`
    unchanged, verified by comparing a full directory snapshot before and after.
19. A run without `--apply` writes `plan.jsonl` containing one record per candidate with fields
    `{path, content_hash, action, proposed_name, reason, ocr_char_count, est_cost_usd}`, where
    `action` is one of `rename | abstain | skip | error`.
20. `--apply` performs renames; there is no configuration or environment variable that makes
    renaming the default.
21. Before any rename, the run prints an estimated total API cost; `--max-cost <usd>` aborts
    before the first API call if the estimate exceeds the ceiling, exiting non-zero.

### Journal, undo, and idempotency

22. For every rename, a JSONL record `{run_id, ts, old_path, new_path, content_hash}` is
    appended to the journal and `fsync`ed **before** the `rename(2)` call. Verified by killing
    the process between journal write and rename (injected fault) and asserting the journal
    contains the record while the file still has its old name.
23. `shotname undo` restores the most recent run's renames to their original names and reports
    the count restored. `--run-id <id>` undoes a specific earlier run.
24. `undo` verifies each file's content hash before restoring; a file whose contents changed
    since the rename is left alone, listed in the output, and causes a non-zero exit code.
25. `undo` on a run whose target names are now occupied by other files skips those entries and
    reports them rather than overwriting.
26. Re-running the tool over an already-renamed directory proposes zero renames: files whose
    content hash appears in the journal as already-renamed are skipped with reason
    `already_renamed`.
27. After a `SIGKILL` mid-`--apply`, re-running the identical command makes zero OCR calls and
    zero model calls for files completed in the prior run, and completes the remainder.
28. The cache key includes the content hash, the model identifier, and the prompt template
    version; changing the prompt template invalidates cached model output but **not** cached OCR
    output.

### Abstention

29. A file whose OCR yields fewer than `--min-ocr-chars` (default 12) characters **and** whose
    image entropy is below threshold is marked `abstain` and is not renamed; the reason is
    recorded in the plan.
30. A model response that is a refusal (per the provider's stop reason or a refusal classifier
    on the text) results in `action: abstain` with reason `model_declined`. The run continues
    and the process exit code is unaffected by refusals alone.
31. A model response that fails schema validation is retried once; a second failure yields
    `action: error` for that file and the run continues.
32. Abstained and errored files are never renamed under any flag combination short of an
    explicit `--force-name-abstentions`.

### Failure modes and operational behaviour

33. If the target directory returns `EPERM`, the tool exits with code 3 and prints the literal
    System Settings path for granting Full Disk Access to the parent terminal application, with
    no Python traceback on stdout or stderr.
34. If no API key is present and `--local` was not passed, the tool exits with code 2 and a
    message naming the environment variable, before enumerating any files.
35. HTTP 429 responses trigger exponential backoff honouring the `retry-after` header; a run
    with an injected 429-then-200 sequence completes successfully with no file marked `error`.
36. Concurrency is bounded (default 8, `--concurrency N`); the number of simultaneous in-flight
    model requests never exceeds the configured value, asserted by an instrumented transport.
37. `SIGINT` stops dispatch of new work, allows in-flight renames to finish and journal, prints
    a partial summary, and exits with code 130. A subsequent identical invocation resumes.
38. `--local` makes zero outbound network connections other than to the configured Ollama host,
    asserted by a blocking socket hook in tests.

### Metadata preservation

39. After `--apply`, each renamed file retains its extended attributes, specifically
    `com.apple.metadata:kMDItemIsScreenCapture` and `com.apple.metadata:_kMDItemUserTags`.
40. After `--apply`, each renamed file's `mtime` and `st_birthtime` are unchanged.

### Sampling and reporting

41. `shotname <dir> --sample N` processes a deterministic pseudo-random N-file subset (seeded by
    `--seed`, default fixed) and changes nothing on disk regardless of `--apply`.
42. On completion, the tool prints a report containing: counts per action, the number of distinct
    slugs versus total renames, the count of slugs used more than once, the min/median/max
    proposed name length, and 20 randomly sampled `old → new` pairs.
43. `--prompt-template <path>` loads a user-supplied prompt; the built-in default is written to
    a path printable via `shotname --print-prompt` so it can be copied and edited.

### Slug composition (app-first)

Numbered from 44 to keep the criteria above stably referenceable; these belong logically with
"Name generation and sanitization".

44. The first slug segment after the date prefix is the application visible in the capture, and
    the remaining segments describe the subject: a Slack thread about a Stripe outage becomes
    `2026-01-14-slack-stripe-outage-thread.png`, not `2026-01-14-stripe-outage-slack-thread.png`.
45. App tokens are canonicalized through a built-in alias map before slugification, so that
    `Google Chrome`, `Chrome`, and `Chrome Canary` all yield `chrome`, and `Terminal`, `iTerm2`,
    and `Ghostty` each keep their own distinct token. An app absent from the map is slugified
    from the model's returned app name unchanged. The map is data, not code, and is covered by
    the determinism criterion (13): the same app name always yields the same token.
46. When no application can be identified (a full-screen photo, a cropped region with no window
    chrome, a PDF page), the app segment is omitted and the name is subject-only. The tool never
    emits a placeholder segment such as `unknown-` or `app-`.

## Out of scope

- **Any GUI, menu-bar app, or Finder extension.** CLI only.
- **Watch-folder / continuous renaming of new screenshots as they are captured.** This tool is
  for the existing backlog. (FilesDesk and Hazel already do the ongoing case well.)
- **The Anthropic Batch API.** The synchronous API with bounded concurrency finishes 4000 files
  in well under an hour, and the sample-and-iterate loop needs low latency. The 50% saving on a
  $6–10 job does not justify the polling and job-lifecycle machinery.
- **Rewriting references to the renamed files** in Obsidian vaults, Markdown docs, Notion, or
  git repos. Confirmed: this corpus is an unreferenced Desktop junk drawer, so in-place renaming
  breaks nothing. The undo journal is the mitigation; link rewriting is a separate tool.
- **Writing renamed copies, hardlinks, or symlinks into a second directory.** Renaming is in
  place. No shadow tree, no symlink left at the old name — a second entry per file would defeat
  the legibility goal the tool exists to serve.
- **Non-macOS platforms.** The OCR stage, the screenshot identification signal, and the TCC
  handling are all Apple-specific. A cross-platform OCR fallback (`ocrs`) is noted as a future
  slot but is not built.
- **Content-based deduplication or deleting near-duplicate screenshots.** Near-duplicates are
  detected only insofar as they inform abstention and collision suffixes.
- **Moving files into topic subfolders, tagging, or any organization beyond the filename.**
- **OCR of non-Latin scripts as a quality target.** Apple Vision will do what it does; the
  ASCII-slug output means non-Latin content yields transliterated or generic names.
- **A daemon, server, index, or search interface.** Settled: `mdfind` already finds these files
  by their contents on this machine, so search is not the problem being solved. The tool is
  judged on how legible the folder is to browse, and that is the bar the naming prompt is tuned
  against.

## Stack

**Archetype: single-binary local CLI tool, Python.** Chosen because the two highest-value
dependencies — the Apple Vision OCR binding and the Anthropic SDK — are both best-in-class in
Python, and because the whole tool is I/O-bound glue where Python's cost is irrelevant.

| Slot | Choice | Reason |
|---|---|---|
| Language / runtime | Python 3.12 | Deviation from a Swift-native build: `ocrmac` (pyobjc) exposes per-line confidence and bounding boxes, which a Swift CLI shelling out would have to re-plumb, and the Anthropic SDK is first-class here. |
| Packaging / deps | `uv` + `pyproject.toml`, installed as a `uvx`-runnable tool | Fast, lockfile-backed, single-command install for a personal CLI. |
| CLI framework | Typer | Subcommands (`run`, `undo`, `sample`) and typed flags for free; Click underneath, so testing via `CliRunner` is standard. |
| OCR (stage 1) | `ocrmac` → `VNRecognizeTextRequest` at `.accurate` | On-device, free, native resolution — the decision that makes stage 2 both cheaper and better. Bounding boxes let title-bar text outrank body text. |
| Naming model (stage 2) | Anthropic API, `claude-haiku-4-5-20251001` by default, `--model` to override | ~$1.56/1000 images; the whole backlog is $6–10. Cost is not the constraint, so the default is the cheapest model that is clearly good enough, with an easy upgrade path. |
| Local backend | Ollama (`llama3.2-vision` default, `--ollama-model` to override) behind the same backend interface | Screenshots are among the most privacy-sensitive files on a machine; a local path has to be first-class, not a footnote. |
| Structured output | Tool-use / JSON schema on the model call, validated with Pydantic | Turns "the model returned prose" into a typed retry rather than a regex. |
| Cache | SQLite (WAL mode), keyed by content hash | Random-access lookup by hash across runs, safe under a concurrent worker pool. |
| Journal | Append-only JSONL, `fsync`ed per record | Deviation from putting the journal in SQLite too: undo must work even if the cache DB is corrupt or deleted, and a plain-text journal is greppable and auditable by hand. Two mechanisms, one of which has no moving parts. |
| Hashing | BLAKE2b over file contents, first 6 hex chars for disambiguation suffixes | Fast, and the truncated prefix is stable and short enough for filenames. |
| Concurrency | `asyncio` with a bounded semaphore, default 8 | OCR and API calls are both I/O-bound; a bounded pool is what makes 429 handling and clean `SIGINT` tractable. |
| Testing | `pytest` + `pytest-asyncio`, fixture screenshot corpus committed to the repo, model calls stubbed | Every acceptance criterion above must be a test; the fixture corpus includes a U+202F name, a `.PNG`, a case-collision pair, a near-blank image, and a dataless-flag simulation. |
| Lint / format | `ruff` (lint + format), `mypy --strict` | Standard, fast, no configuration debate. |
