# Spec: `shotname` — screenshot content renamer

## What this is

`shotname` is a macOS command-line tool that renames screenshot files according to what is
visibly in them, turning `Screenshot 2026-01-14 at 3.42.11 PM.png` into
`2026-01-14-slack-stripe-outage-thread.png`. It runs Apple Vision OCR on-device to extract text
at native resolution, then sends the downscaled image plus that OCR text to a vision model to
produce a short content slug. The slug is **app-first** — the application is the segment
immediately after the date — because the goal is a folder that is legible when *browsed*, not
one that is searchable: Spotlight on this machine already indexes the text inside these images,
so search is solved and consistency of the leading token is what makes a large folder scannable.
It renames **in place**. The tool is built for a one-shot backlog, and the backlog is large
enough (~4000 files today, but the design treats corpus size as a parameter, not a constant)
that the non-AI machinery is the point: dry-run by default, a durable append-only undo journal
`fsync`ed before every rename, a content-hash-keyed cache so re-runs and crash-resumes are nearly
free, deterministic collision resolution that is correct on case-insensitive APFS, and an
explicit abstain path for the substantial fraction of screenshots that genuinely have nothing to
say.

## User stories

- **Try before trusting.** I have thousands of screenshots and no idea whether the names will be
  any good. I run `shotname ~/Desktop --sample 30`, get a table of `old → proposed` for 30 files,
  decide the names are too verbose, edit the prompt template, and run the sample again — having
  changed nothing on disk and spent a few cents. (AC-060, AC-061, AC-064)
- **See the whole plan.** I run `shotname ~/Desktop`. It changes nothing, prints the proposed
  rename for every file, writes `plan.jsonl`, and tells me the estimated API cost and how many
  files it intends to skip and why. (AC-028 – AC-032)
- **Commit.** I run `shotname ~/Desktop --apply`. It shows progress, survives rate limits, and
  reports at the end how many files it renamed, abstained on, skipped, and errored.
  (AC-031, AC-051, AC-062)
- **Change my mind.** Two days later I decide the names are wrong. I run `shotname undo` and every
  file is back to its original name, with a report of any file it refused to touch because the
  contents changed since the rename. (AC-037 – AC-040)
- **Crash and resume.** The run dies partway through because my laptop slept. I re-run the same
  command; it picks up where it stopped without re-OCRing or re-calling the API for anything
  already done. (AC-042, AC-054)
- **Keep it off the network.** Some screenshots contain client data under NDA. I run
  `shotname ~/Desktop --local` and nothing leaves the machine. (AC-055, AC-056)
- **Don't get surprise-billed or surprise-downloaded.** I see the cost estimate before any request
  goes out, and iCloud-evicted files are counted and skipped rather than silently pulled down over
  a hotspot. (AC-006, AC-007, AC-032, AC-033)
- **Verify the run.** I can't eyeball thousands of names, so I read the post-run report:
  distribution of name lengths, count of duplicate slugs, abstention rate, and a random sample of
  20 pairs to inspect. (AC-062, AC-063)

## Acceptance criteria

The contract lives in `.sfo/CRITERIA.jsonl`, one criterion per line, each independently checkable
by an automated test against a fixture directory. Groups, in build order:

| Group | Ids | What it pins down |
|---|---|---|
| Enumeration and file identification | AC-001 – AC-009 | U+202F-safe matching, `kMDItemIsScreenCapture` as the real signal, recursion, `SF_DATALESS`, memory that does not scale with corpus size |
| Name generation and sanitization | AC-010 – AC-018 | The output regex, date prefix derivation, `/` and `:` stripping, the 255-**byte** limit, extension handling, determinism |
| Slug composition (app-first) | AC-019 – AC-022 | App token first, alias-map canonicalization, no placeholder segment when the app is unknown |
| Collision handling | AC-023 – AC-027 | Deterministic hash suffixes, case-folded + NFC-normalized comparison, never losing a file |
| Dry run, plan output, and apply | AC-028 – AC-034 | Dry-run is the default and cannot be configured away; `plan.jsonl` schema; cost estimate and `--max-cost` |
| Journal, undo, idempotency, resume | AC-035 – AC-043 | Journal-before-rename ordering, `undo` semantics including hash verification, zero-work re-runs |
| Abstention | AC-044 – AC-048 | When the tool declines, and that declining is never a rename |
| Backends, cost, and failure modes | AC-049 – AC-057 | TCC/`EPERM` message, missing key, 429 backoff, bounded concurrency, `SIGINT`, local backend isolation |
| Metadata preservation | AC-058 – AC-059 | xattrs, Finder tags, `mtime`, `st_birthtime` survive the rename |
| Sampling and reporting | AC-060 – AC-064 | Deterministic sampling that never writes, and the verification report |

Two criteria carry most of the safety weight and are worth calling out in prose:

- **AC-035** — the journal record is written *and `fsync`ed* before `rename(2)` is called. This
  ordering is what makes undo trustworthy; the reverse order loses the record for exactly the file
  that was in flight when the process died. AC-036 tests the kill-in-between case explicitly.
- **AC-027** — the set of inodes in the target directory is identical before and after `--apply`.
  This is the single assertion that catches every clobbering bug at once, regardless of which
  sanitization or collision rule failed.

## Out of scope

- **Any GUI, menu-bar app, or Finder extension.** CLI only.
- **Watch-folder / continuous renaming of newly captured screenshots.** This tool is for the
  existing backlog; FilesDesk and Hazel already do the ongoing case well.
- **Rewriting references to renamed files** in Obsidian vaults, Markdown docs, Notion, or git
  repos. Confirmed by the human: this corpus is an unreferenced Desktop junk drawer, so in-place
  renaming breaks nothing. The undo journal is the mitigation; link rewriting is a separate tool.
- **Writing renamed copies, hardlinks, or symlinks into a second directory,** and leaving no
  symlink at the old name. A second entry per file would defeat the legibility goal the tool
  exists to serve.
- **Non-macOS platforms.** The OCR stage, the screenshot-identification signal, and the TCC
  handling are all Apple-specific. A cross-platform OCR fallback (`ocrs`) is a noted future slot,
  not built.
- **Content-based deduplication or deleting near-duplicate screenshots.** Near-duplicates matter
  only insofar as they inform abstention and collision suffixes.
- **Moving files into topic subfolders, tagging, or any organization beyond the filename.**
- **A daemon, server, index, or search interface.** Settled by the human: `mdfind` already finds
  these files by their contents on this machine, so search is not the problem. The tool is judged
  on how legible the folder is to browse.
- **Non-Latin scripts as a quality target.** Apple Vision extracts what it extracts; the ASCII
  slug (AC-017) means non-Latin content yields transliterated or generic names.
- **The Anthropic Batch API, and a corpus-level clustering pass,** pending Q-002 and Q-004. Both
  are deferred by default rather than rejected; either would change the pipeline shape and the
  bill, so they are the human's call.

## Stack

**Archetype: single-binary local CLI tool, Python.** Chosen because the two highest-value
dependencies — the Apple Vision OCR binding and the Anthropic SDK — are both best-in-class in
Python, and because the whole tool is I/O-bound glue where the language's speed is irrelevant.

| Slot | Choice | Reason |
|---|---|---|
| Language / runtime | Python 3.12 | Deviation from a Swift-native build: `ocrmac` (pyobjc) exposes per-line confidence and bounding boxes that a Swift CLI shelling out would have to re-plumb, and the Anthropic SDK is first-class here. |
| Packaging / deps | `uv` + `pyproject.toml`, `uvx`-runnable | Fast, lockfile-backed, one-command install for a personal CLI. |
| CLI framework | Typer | Subcommands (`run`, `undo`) and typed flags for free; Click underneath, so `CliRunner` testing is standard. |
| OCR (stage 1) | `ocrmac` → `VNRecognizeTextRequest` at `.accurate` | On-device, free, native resolution — the decision that makes stage 2 both cheaper and better. Bounding boxes let title-bar text outrank body text. See Q-008 on the Live Text alternative. |
| Naming model (stage 2) | Anthropic API, `claude-haiku-4-5-20251001` default, `--model` override | Cheapest model that is clearly good enough on OCR-assisted input; see Q-001, which is the human's to decide because it is the line item on the bill. |
| Local backend | Ollama (`llama3.2-vision` default, `--ollama-model` override) behind the same backend interface | Screenshots are among the most privacy-sensitive files on a machine; a local path has to be first-class, not a footnote (AC-057). |
| Structured output | Tool-use / JSON schema on the model call, validated with Pydantic | Turns "the model returned prose" into a typed retry (AC-047) rather than a regex. |
| Cache | SQLite (WAL mode), keyed by content hash | Random-access lookup by hash across runs, safe under a concurrent worker pool. |
| Journal | Append-only JSONL, `fsync`ed per record | Deviation from putting the journal in SQLite too: undo must work even if the cache DB is corrupt or deleted, and a plain-text journal is greppable and auditable by hand. Two mechanisms, one with no moving parts. |
| Hashing | BLAKE2b over file contents; first 6 hex chars as the disambiguation suffix | Fast, and the truncated prefix is stable and short enough for filenames. |
| Concurrency | `asyncio` with a bounded semaphore, default 8 | OCR and API calls are both I/O-bound; a bounded pool is what makes 429 handling and clean `SIGINT` tractable. |
| State location | `~/.local/state/shotname/` (cache, journal, plans) | Human-chosen: the target directory stays clean and one journal location serves runs over different folders. |
| Testing | `pytest` + `pytest-asyncio`, fixture corpus in-repo, model calls stubbed | Every criterion in `CRITERIA.jsonl` is a test; the fixture corpus includes a U+202F name, a `.PNG`, a case-collision pair, a near-blank image, and a simulated dataless flag. |
| Lint / format | `ruff` (lint + format), `mypy --strict` | Standard, fast, no configuration debate. |
