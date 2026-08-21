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
It renames **in place**. Because the workload is a one-shot backlog rather than an interactive
service, the full run dispatches its model calls through the **Anthropic Batch API** — half the
price and no rate-limit ceiling — while `--sample` stays on the synchronous endpoint so that
iterating on the prompt does not mean waiting on a job. That split is the one place where the
pipeline is not a straight stream: a full run submits jobs, records their ids, and collects the
results on a later invocation of the same command (or immediately, with `--wait`).
The tool is built for that one-shot backlog, and the backlog is large
enough (~4000 files today, but the design treats corpus size as a parameter, not a constant)
that the non-AI machinery is the point: dry-run by default, a durable append-only undo journal
`fsync`ed before every rename, a content-hash-keyed cache so re-runs and crash-resumes are nearly
free, deterministic collision resolution that is correct on case-insensitive APFS, and an
explicit abstain path for the substantial fraction of screenshots that genuinely have nothing to
say.

## User stories

- **Try before trusting.** I have thousands of screenshots and no idea whether the names will be
  any good. I run `shotname ~/Desktop --sample 30`, get a table of `old → proposed` for 30 files
  in under a minute because the sample path is synchronous, decide the names are too verbose, edit
  the prompt template, and run the sample again — having changed nothing on disk and spent a few
  cents. (AC-060, AC-061, AC-064, AC-065)
- **Pick the tier with my own screenshots.** Rather than reason about it in the abstract, I run the
  same 30-file sample at `--resolution standard` and again at `--resolution high`, and once more
  with `--model claude-sonnet-5`. Each run tells me the model, the tier, and what it actually cost,
  so I choose the default for the backlog from three real comparisons costing cents.
  (AC-076, AC-077, AC-060)
- **See the whole plan.** I run `shotname ~/Desktop`. It changes nothing, prints the proposed
  rename for every file, writes `plan.jsonl`, and tells me the estimated API cost and how many
  files it intends to skip and why. (AC-028 – AC-032, AC-067)
- **Commit and walk away.** I run `shotname ~/Desktop --apply`. It submits the batch, prints the
  job ids and the command that collects them, and exits without touching a file. When I come back
  I re-run the identical command: it ingests the results, renames, and reports how many files it
  renamed, abstained on, skipped, and errored. If I would rather sit and watch, I pass `--wait`.
  (AC-031, AC-070 – AC-073, AC-062)
- **Change my mind.** Two days later I decide the names are wrong. I run `shotname undo` and every
  file is back to its original name, with a report of any file it refused to touch because the
  contents changed since the rename. (AC-037 – AC-040)
- **Crash and resume.** The run dies partway through because my laptop slept. I re-run the same
  command; it picks up where it stopped without re-OCRing or re-calling the API for anything
  already done, and a batch job that was in flight when the process died is collected rather than
  re-submitted. (AC-042, AC-054, AC-075)
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
| Dry run, plan output, and apply | AC-028 – AC-034, AC-078, AC-079 | Dry-run is the default and cannot be configured away; `plan.jsonl` schema; both the one-pass and replay-a-plan forms of `--apply`; `--max-cost` is opt-in with no built-in ceiling |
| Journal, undo, idempotency, resume | AC-035 – AC-043 | Journal-before-rename ordering, `undo` semantics including hash verification, zero-work re-runs |
| Abstention | AC-044 – AC-048, AC-080 | When the tool declines, that declining is never a rename, and low model confidence alone is not a reason to decline |
| Backends, cost, and failure modes | AC-049 – AC-057 | TCC/`EPERM` message, missing key, 429 backoff, bounded concurrency, `SIGINT`, local backend isolation |
| Batch execution and job lifecycle | AC-065 – AC-075 | Batch for full runs and synchronous for `--sample`, the 50% discount in the estimate, job chunking, submit/collect/`--wait`, errored entries, and never losing or double-submitting a job |
| Model and resolution tier selection | AC-076 – AC-077 | `--model` and `--resolution` defaults, and reporting the settings and the realised cost so samples are comparable |
| Metadata preservation | AC-058 – AC-059 | xattrs, Finder tags, `mtime`, `st_birthtime` survive the rename |
| Sampling and reporting | AC-060 – AC-064 | Deterministic sampling that never writes, and the verification report |

Two criteria carry most of the safety weight and are worth calling out in prose:

- **AC-035** — the journal record is written *and `fsync`ed* before `rename(2)` is called. This
  ordering is what makes undo trustworthy; the reverse order loses the record for exactly the file
  that was in flight when the process died. AC-036 tests the kill-in-between case explicitly.
- **AC-027** — the set of inodes in the target directory is identical before and after `--apply`.
  This is the single assertion that catches every clobbering bug at once, regardless of which
  sanitization or collision rule failed.
- **AC-072** — re-invoking the command while a job is in flight submits zero new requests. Batch
  dispatch decouples "I ran the command" from "the work finished," which introduces a failure mode
  the synchronous design did not have: an impatient second invocation that pays for the whole
  backlog twice. Recorded job ids are what make re-invocation the safe way to check on a run.

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
- **Any corpus-level pass to fight generic-name collapse.** Settled by the human: v1 does per-file
  naming only and relies on the date prefix and hash suffixes for uniqueness. The folder will
  contain dozens of `terminal-output` names differing only by date; that is accepted for v1. The
  duplicate-slug count in the completion report (AC-062) is the instrument that says whether it
  became bad enough to warrant the re-prompt pass or the near-duplicate clustering pass later.
- **Abstaining on low model confidence or thin one-segment names.** Settled by the human: the
  abstention line sits at clearly-empty images only (AC-044, AC-080). A thin name is better than a
  folder that is visibly half-renamed.
- **Unicode filenames.** Settled by the human: slugs are transliterated ASCII (AC-017), so a
  Japanese-UI capture yields a romanized or generic name rather than a faithful one.
- **A `--max-cost` ceiling that applies unless overridden.** Settled by the human: the guard is
  opt-in (AC-079). Nothing stands between a mistyped path and a large bill except the cost estimate
  printed before the first request.
- **The Live Text OCR path.** Settled by the human: stage 1 is classic `VNRecognizeTextRequest` at
  `.accurate` via `ocrmac`, whose per-line bounding boxes the app-first rule depends on. Live Text
  may extract more from dark-mode captures; that is a future swap behind the same interface, not a
  v1 benchmark exercise.

## Stack

**Archetype: single-binary local CLI tool, Python.** Chosen because the two highest-value
dependencies — the Apple Vision OCR binding and the Anthropic SDK — are both best-in-class in
Python, and because the whole tool is I/O-bound glue where the language's speed is irrelevant.

| Slot | Choice | Reason |
|---|---|---|
| Language / runtime | Python 3.12 | Deviation from a Swift-native build: `ocrmac` (pyobjc) exposes per-line confidence and bounding boxes that a Swift CLI shelling out would have to re-plumb, and the Anthropic SDK is first-class here. |
| Packaging / deps | `uv` + `pyproject.toml`, `uvx`-runnable | Fast, lockfile-backed, one-command install for a personal CLI. |
| CLI framework | Typer | Subcommands (`run`, `undo`) and typed flags for free; Click underneath, so `CliRunner` testing is standard. |
| OCR (stage 1) | `ocrmac` → `VNRecognizeTextRequest` at `.accurate` | On-device, free, native resolution — the decision that makes stage 2 both cheaper and better. Bounding boxes let title-bar text outrank body text, which is what the app-first rule needs; human-chosen over the Live Text path for exactly that reason. |
| Naming model (stage 2) | Anthropic API, `claude-haiku-4-5-20251001` and `--resolution standard` as the starting point, both overridable per run | Human-chosen: rather than fix the tier up front, the starting point is the cheap one and `--sample` decides. This makes model id and resolution tier first-class run parameters — part of the cache key (AC-043), part of the cost estimate, and printed in the report (AC-077) — instead of constants. |
| Dispatch | Batch API for full runs, synchronous for `--sample` and `--sync`, both behind the same backend interface | Human-chosen: the backlog is one-shot and latency-insensitive, so the 50% discount and the absent rate-limit ceiling are close to free; the sample loop is latency-*bound*, so it stays synchronous. The cost is job-lifecycle code — submit, persist ids, poll, reconcile by `custom_id` (AC-065 – AC-075). |
| Local backend | Ollama (`llama3.2-vision` default, `--ollama-model` override) behind the same backend interface | Screenshots are among the most privacy-sensitive files on a machine; a local path has to be first-class, not a footnote (AC-057). |
| Structured output | Tool-use / JSON schema on the model call, validated with Pydantic | Turns "the model returned prose" into a typed retry (AC-047) rather than a regex. |
| Cache | SQLite (WAL mode), keyed by content hash | Random-access lookup by hash across runs, safe under a concurrent worker pool. |
| Journal | Append-only JSONL, `fsync`ed per record | Deviation from putting the journal in SQLite too: undo must work even if the cache DB is corrupt or deleted, and a plain-text journal is greppable and auditable by hand. Two mechanisms, one with no moving parts. |
| Hashing | BLAKE2b over file contents; first 6 hex chars as the disambiguation suffix | Fast, and the truncated prefix is stable and short enough for filenames. |
| Concurrency | `asyncio` with a bounded semaphore, default 8 | OCR is I/O-bound on every path, and on the synchronous dispatch path so are the API calls; a bounded pool is what makes 429 handling and clean `SIGINT` tractable. On the batch path the semaphore governs OCR and job submission only. |
| State location | `~/.local/state/shotname/` (cache, journal, plans, batch job records) | Human-chosen: the target directory stays clean and one journal location serves runs over different folders. Batch job ids live here too, keyed by target directory and run parameters, which is what makes re-invocation collect rather than re-submit. |
| Testing | `pytest` + `pytest-asyncio`, fixture corpus in-repo, model calls stubbed at an instrumented transport that fakes both the synchronous and the batch endpoints | Every criterion in `CRITERIA.jsonl` is a test; the fixture corpus includes a U+202F name, a `.PNG`, a case-collision pair, a near-blank image, and a simulated dataless flag. The transport is the seam that lets AC-065 assert *which* endpoint was used and AC-074 inject an errored batch entry. |
| Lint / format | `ruff` (lint + format), `mypy --strict` | Standard, fast, no configuration debate. |
