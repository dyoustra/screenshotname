# Summary — `shotname` (screenshot content renamer)

## Read this first

**No slice failed and none was skipped.** All 11 slices in `.sfo/SLICES.jsonl` have an
`"ok":true` record in `.sfo/VERIFY.jsonl`, and all 80 criteria in `.sfo/CRITERIA.jsonl`
belong to a slice that passed. There is no unmet criterion to report, and there are no
stubs left in `src/shotname/` — `grep -rn NotImplementedError src/` returns nothing.

That is the whole of the good news, and it is narrower than it sounds. What follows is
what does not work, or is not known to work.

### 1. Nothing in this project has ever touched a real screenshot, a real API, or Apple Vision

The suite is hermetic by design (`D-044`), and `src/shotname/deps.py` is the single seam
that makes it so. Every test replaces it. The consequence is that the four components a
real invocation reaches the world through have **zero executed lines**:

| Component | What it does on a real run | Test coverage |
|---|---|---|
| `ocr.VisionOcr.recognize` | the `ocrmac` → `VNRecognizeTextRequest` call, stage 1 of the pipeline | none |
| `transports.AnthropicTransport` | every cloud model call, synchronous and Batch | none |
| `transports.OllamaTransport` | the entire `--local` path | none |
| `discovery.RealFsProbe` | reads `kMDItemIsScreenCapture` by shelling out to `mdfind` (`D-S01-01`), and `st_flags` for `SF_DATALESS` | none |
| `imaging.encode_for_model` / `MAX_EDGE_PER_TIER` | the downscale the `--resolution` tier means | none (`REVIEW.md` H1) |

`REVIEW.md` says this plainly under "Stubbed by construction": `OverrideProbe` forces
`is_screen_capture` and `SF_DATALESS` per path, `FakeOcr` replaces `VisionOcr`
everywhere. So AC-002, AC-006 and AC-007 assert what the enumerator does *given* a
signal, never that the real probe produces one. **The first time you point this at
`~/Desktop` is the first time that code executes.** Whether it finds your screenshots,
whether OCR returns anything, and whether the names are any good are all open questions
that 187 passing tests do not speak to.

Mitigation that exists: dry-run is the default and is unconfigurable (`D-023`, AC-031),
and `--sample N` never writes even with `--apply` (AC-061). Use both before `--apply`.

### 2. Two requirements the spec argues for at length are checked by no criterion

- **The resolution tier is never tied to the image actually sent** (`REVIEW.md` H1).
  `imaging.py` calls itself "the only place the `--resolution` tier means anything," and
  it appears in no test file. A build that sent original Retina bytes at both tiers
  passes the full suite — while the estimate and the report each state a tier and a
  differing cost. The "pick the tier with my own screenshots" user story rests entirely
  on unverified code, and its output could report a difference where none occurred.
- **The app-first ranking is unpinned** (`REVIEW.md` H2). App-first slugs are the
  project's central quality claim (`D-006`, human-decided, overriding the agent's
  recommendation), and the OCR engine was chosen *specifically* for the per-line bounding
  boxes that make it work (`D-053`, `D-013`). AC-019 – AC-022 all call
  `compose_slug(app=…, subject=…)` with the application already decided, and in every
  end-to-end test the app arrives pre-decided from a stubbed response. Nothing asserts
  that `OcrLine.bbox` or `.confidence` influences any decision. **What passed is the slug
  assembler, not the ranking that makes it correct.**

### 3. `--apply --plan` (plan replay) has no clobber-safety and no staleness check

`REVIEW.md` H3. `pipeline._replay` filters the plan to `Action.RENAME` and applies it. It
does **not** re-run collision resolution (the resolved names were baked into the plan
when it was written) and does **not** compare `PlanRecord.content_hash` against the file
on disk. AC-025, AC-026 and AC-027 — including the one `SPEC.md` calls "the single
assertion that catches every clobbering bug at once" — are exercised **only** through the
one-pass `--apply`. No test replays a plan whose `proposed_name` is now occupied, and no
test edits a file's bytes between writing a plan and replaying it. Replaying a plan
written days earlier is exactly the "see the whole plan → commit and walk away" workflow,
and nothing constrains what happens. Prefer one-pass `--apply`, or re-generate the plan
immediately before replaying it.

### 4. The memory guarantee at backlog scale is not actually verified

AC-009 passed, and it measures the wrong scope (`REVIEW.md` H4). The test iterates
`discover_candidates` in a fresh interpreter per corpus size and compares peak RSS;
nothing downstream is measured. `pipeline._resolve` holds every `Proposal` for the whole
run because collision resolution compares proposed names across the corpus, and plan
records, per-unit outcomes and per-request `image_bytes` are all per-file live state the
test cannot see. A build whose peak memory grows linearly with corpus size — the exact
failure at 4,000+ files that AC-009 exists to prevent — passes AC-009.

### 5. The test contract was modified three times, once flagged by the gate

- **`tamperedTests` is non-empty once.** `S-04` attempt 1 (`2026-09-27T03:05:39Z`) has no
  `failedStep` — the gate never reached a step — and reads `"reason":"the test suite
  changed since it was locked: conftest.py"`, `"tamperedTests":["conftest.py"]`. Per
  `D-S04-11`/`D-S04-13`, the file was a **new repo-root `conftest.py`** the build added to
  supply `ANTHROPIC_API_KEY` and `SHOTNAME_STATE_DIR` defaults; the lock counts a new root
  conftest as a changed suite. The build withdrew it and solved the problem inside
  `src/shotname/settings.py` instead (`D-S04-13`), and attempt 2 passed clean. No
  assertion in `tests/` is recorded as having been altered by a build. Note the gate's
  record does not distinguish the added root file from the locked `tests/conftest.py`; the
  decision log is the only evidence for which one it was, and I could not re-hash the
  tree from this session to confirm.
- **Two edits to the locked suite were made outside the pipeline, by hand, and re-locked.**
  `D-relock-b11`: four lint/type errors in locked test files (import order, a `noqa` on a
  deliberate local-time call, a `Result` annotation) — `test-repair` had locked a suite
  that did not itself pass lint, so *no slice could have passed its gate*; S-01's attempt
  counter was reset. `D-ac009-test`: the AC-009 test was rewritten from `tracemalloc` to
  peak RSS, which is what let S-01 finally pass. Both records claim no assertion changed.
  `tamperedTests` cannot see either one — it compares against the lock, and the lock was
  rewritten. **Neither claim is independently verified anywhere in the run record.**

---

## 1. What this is and how to run it

`shotname` is a macOS-only CLI that renames screenshots in place after their visible
content: `Screenshot 2026-01-14 at 3.42.11 PM.png` → `2026-01-14-slack-stripe-outage-thread.png`.
On-device Apple Vision OCR runs first, then a vision model turns the OCR text plus a
downscaled image into an app-first slug. ~3,800 lines of source, ~4,500 lines of tests.

There is **no README** in the repository; this section is derived from `src/shotname/cli.py`.

Working directory: `/Users/dannyyoustra/.sfo/screenshot-content-renamer-fee869`

### Setup

A virtualenv already exists at `.venv` (CPython 3.13.5 via pyenv) with all dependencies
installed and `shotname` installed as an editable console script, so `.venv/bin/shotname`
works as-is. To rebuild it:

```bash
cd /Users/dannyyoustra/.sfo/screenshot-content-renamer-fee869
uv sync                        # needs network access; uv.lock is committed
```

Everything below can also be written `uv run shotname …`. The system `python3` lacks
`anyascii`, which `slug.py` imports at module scope, so bare `python3` fails at import.

### Credentials and state

```bash
export ANTHROPIC_API_KEY=sk-...      # required unless --local; missing → exit 2
export SHOTNAME_STATE_DIR=/some/dir  # optional; default ~/.local/state/shotname/
```

The state directory holds `cache.sqlite` (content-hash cache), `journal.jsonl` (the undo
journal), `plan.jsonl` and `jobs.jsonl` (batch job ids). The target folder stays clean
(`D-008`).

### The workflow the tool is designed for

```bash
# 1. See the built-in prompt, and optionally edit a copy of it.
.venv/bin/shotname run ~/Desktop --print-prompt > my-prompt.txt

# 2. Sample 30 files synchronously. Writes nothing, even with --apply. Costs cents.
.venv/bin/shotname run ~/Desktop --sample 30
.venv/bin/shotname run ~/Desktop --sample 30 --resolution high        # compare tiers
.venv/bin/shotname run ~/Desktop --sample 30 --model claude-sonnet-5  # compare models
.venv/bin/shotname run ~/Desktop --sample 30 --prompt-template my-prompt.txt

# 3. Full dry run. Renames nothing, prints the cost estimate and every proposed name,
#    writes plan.jsonl into the state directory.
.venv/bin/shotname run ~/Desktop

# 4. Apply. This submits Batch API jobs, prints the job ids and the collecting
#    command, renames nothing, and exits 0.
.venv/bin/shotname run ~/Desktop --apply

# 5. Re-run the identical command to collect results and perform the renames.
#    Submits zero new requests while a job is still in flight (AC-072).
.venv/bin/shotname run ~/Desktop --apply

# Or do 4+5 in one invocation, polling until the jobs finish:
.venv/bin/shotname run ~/Desktop --apply --wait

# Undo the most recent run (or --run-id <id> for an earlier one).
.venv/bin/shotname undo
```

Other flags: `--sync` (full run on the synchronous endpoint instead of Batch),
`--local` (Ollama, default `http://127.0.0.1:11434`, `llama3.2-vision`; always
synchronous), `--plan <path>` (replay a written plan — see gap 3 above),
`--recursive`, `--all-images`, `--materialize` (process iCloud-evicted files rather than
skipping them), `--max-cost USD` (opt-in; **there is no default ceiling**),
`--min-ocr-chars` (default 12), `--concurrency` (default 8), `--seed` (default 0),
`--poll-interval` (default 10s). Defaults: `--model claude-haiku-4-5-20251001`,
`--resolution standard`.

Exit codes: `2` missing API key or usage error, `3` `EPERM` on the target directory (the
message names the Full Disk Access setting), `4` `undo` left files untouched, `130`
`SIGINT`.

### Tests, lint, typecheck

```bash
uv run pytest -q                 # 187 tests
uv run ruff check .
uv run mypy --strict .
```

---

## 2. What was not verified

**"Verified" here means exactly this:** for each of the 11 slices, the `cli-python`
recipe ran `uv sync`, `uv run ruff check .`, `uv run mypy --strict .`, and
`uv run pytest -q tests/test_sNN_*.py`, and each exited 0. Nothing else in this project
attests to anything.

Per-slice record from `.sfo/VERIFY.jsonl`, in the order it ran:

| Slice | Attempts | Outcome |
|---|---|---|
| S-01 Enumeration | 3 failing records, then a pass | failed `lint`, then `test` twice; passed only after the AC-009 test was rewritten out of band (`D-ac009-test`, commit `9cee7da`) |
| S-02 Slug composition | 1 | pass |
| S-03 Filename assembly | 1 | pass |
| S-04 Backends, cost, failure modes | 2 | attempt 1 never reached a step (locked suite changed: `conftest.py`); attempt 2 pass |
| S-05 – S-11 | 1 each | pass |

**Gate steps that never ran.** One record: `S-04` attempt 1, covered above. Every other
failing record carries a `failedStep`, so no attempt was blocked by a missing archetype
recipe, by tests that could not be located, or by a build agent exiting non-zero. The
`cli-python` archetype has a recipe and every record names it.

**The gate's `test` step was scoped to one file per slice.** Each slice's gate ran only
its own `tests/test_sNN_*.py`, so S-01's green record predates the changes S-07 made to
`discovery.py` and `cli.py`. The full suite is reported green — `187 passed in 7.15s` — but
that line comes from the S-11 **build agent's** own transcript
(`.sfo/logs/build-S-11.log`), not from the gate. I could not re-run it: this session's
sandbox declined to execute `.venv/bin/python`, `uv`, and `python3 -c`. **Every number in
this document is read from the run record, not freshly measured.**

**What the recipe does not cover at all:**

- **Real macOS behaviour.** No test executes `mdfind`, `VNRecognizeTextRequest`, or a real
  `st_flags` read. See gap 1.
- **Any network request.** No request has been sent to Anthropic or Ollama, ever. All cost
  figures are arithmetic over a token table, never a measurement.
- **Packaging as a user would receive it.** Nothing verifies `uvx shotname`, a clean-machine
  dependency resolve, or the console script outside this one editable install.
  `D-S01-05` notes `uv sync` could not reach the network during the build.
- **More than one interpreter or machine.** One CPython 3.13.5 on one Mac.
  `pyproject.toml` allows `>=3.12`; nothing was run on 3.12 or 3.14. AC-009's outcome was
  interpreter-dependent under its old implementation (`D-S01-06`), which a single-version
  gate is structurally unable to surface.
- **The real corpus.** Nothing has run against the ~4,000-file backlog, or one real
  screenshot.
- **Runtime, throughput, or spend at scale.** Not timed, not measured, anywhere.
- **Concurrency under load.** No test bounds OCR concurrency on either dispatch path
  (`REVIEW.md` M7), and nothing exercises concurrent SQLite cache writes despite WAL mode
  being the stated reason for choosing SQLite (M9).

**Build provenance worth knowing.** The `build-S-04` stage (commit `cf4b170`, 25 files,
$22.21 across two attempts) also wrote the code for S-05, S-06 and S-08 – S-11; the
commits for those stages touch only `.sfo/` artifacts. The S-11 build agent flagged this
itself. So six slices' gates verified code written before their slice began, rather than
code written to their assertions — the agent's own read is that the modules are
substantive rather than shaped to the tests, but the per-slice isolation the pipeline
implies is not what happened.

**Cost.** $96.38 total across 28 stage invocations. $15.78 of that went to four
`build-S-01` attempts (three of which failed) and $22.21 to two `build-S-04` attempts.

---

## 3. Decisions worth reviewing

### External blast radius — expensive to reverse, or someone else's money

- **`D-032` and `D-047` contradict each other and neither is marked superseded.**
  `D-032` (agent, 20:44) chose synchronous dispatch and explicitly rejected the Batch API;
  `D-047` (human, 21:01) reversed it — Batch for full runs, synchronous for `--sample`.
  `SPEC.md` and the code implement `D-047`. A reader working from `DECISIONS.jsonl` alone
  finds two live, opposed decisions on the most consequential architectural question here.
  Same pattern, smaller stakes: `D-034`/`D-050` (abstention), `D-036`/`D-052` (cost
  ceiling), `D-015`/`D-046` (model default), `D-027`/`D-051` (transliteration) — in each
  pair an agent decision was later re-decided by the human and the original left standing.
- **`D-003` — no link rewriting.** Renaming in place will not update references, on the
  human's confirmation that this corpus is "an unreferenced Desktop junk drawer". If that
  is no longer true, the undo journal is the only mitigation — and it is a file in
  `~/.local/state/shotname/` that has to survive.
- **`D-015` / `D-046` — `claude-haiku-4-5-20251001` at the `standard` tier.** This is the
  entire bill. The mechanism intended to validate the choice before the backlog run is
  `--sample`, whose tier comparison is the one `REVIEW.md` H1 says is unpinned.
- **`D-036` / `D-052` — no default cost ceiling.** `--max-cost` is opt-in. Nothing stands
  between a mistyped path and a large bill except the pre-run estimate (AC-032), which is
  computed from a token table and has never been checked against a real invoice.
- **The budget ceiling was exceeded twice and the pipeline parked itself both times.**
  `D-budget-test-write-9.4` ($10.52 against $9.40) and `D-budget-test-repair-18.5` ($22.25
  against $18.50). Work resumed after each; final spend was $96.38.

### Structural blast radius

- **`D-047` / `D-054` / `D-055` — the two-phase batch run.** A full `--apply` submits jobs,
  records their ids keyed by target directory *and* run parameters, and exits without
  renaming; re-invoking the identical command collects. `custom_id` is the file's content
  hash, so byte-identical duplicates collapse into one paid request. This is the most
  intricate machinery in the project and the default path for the backlog. The key that
  makes re-invocation collect rather than re-submit — `batch.corpus_key` / `jobs_for` —
  **appears in no test** (`REVIEW.md` M1): every batch test re-invokes the *identical*
  command, so nothing checks that a different directory or a different `--model` does not
  collect the first run's jobs.
- **`D-048` — `--apply` does not require a reviewed plan.** The one-pass form can rename
  thousands of files under names no human read. Accepted, with the undo journal as the
  mitigation.
- **`D-S04-13` — a run remembers a resolved API key and state directory per target
  directory, for the life of the process.** Introduced so that AC-054 (resume after
  `SIGINT`) passes when the environment no longer supplies them. The rationale is a real
  user-facing one — the shell that exported the credential is not necessarily the shell a
  resume happens in — but it is product behaviour added in service of a test, and it means
  a credential resolved once is reused for later invocations against the same directory
  within a process. Worth a look because it is not in `SPEC.md`.
- **`D-S07-01` — idempotency depends on the journal file.** A file is recognized as
  already-renamed because `journal.jsonl` says this tool renamed something *to* that path
  (AC-041), checked before every other test including `--all-images`. Delete or lose the
  journal and a second run treats every renamed file as a fresh candidate. The journal is
  also the only thing `undo` reads.
- **`D-019` / `D-020` — the journal is JSONL, fsynced before every `rename(2)`,
  deliberately independent of the SQLite cache.** AC-035 and AC-036 are the two criteria
  `SPEC.md` singles out as carrying the safety weight, and both passed. Two caveats:
  AC-035's test records *global* `os.fsync` calls rather than the journal's descriptor
  (`REVIEW.md` L4), and the stated reason for two mechanisms — "undo must work even if the
  cache DB is corrupt or deleted" — became no criterion, and no test deletes the cache
  before running `undo` (L8).
- **`D-022` — collisions compared case-folded and NFC-normalized** against both other
  proposals and existing on-disk files, because APFS is case-insensitive. Verified on the
  one-pass path only (see gap 3).
- **`D-040` / `D-002` — same-volume `rename(2)`, in place, never copy-delete**, which is
  what preserves xattrs, Finder tags, `mtime` and `st_birthtime` (AC-058, AC-059). AC-058
  skips silently if `/usr/bin/xattr` is missing (`REVIEW.md` L10).
- **`D-S04-01` / `D-S04-02` — `pipeline.py` and the `SIGINT` design.** The run is four
  phases in one module; `KeyboardInterrupt` is caught inside each worker and in the rename
  loop, never `BaseException` (the test fakes raise a `BaseException` subclass on purpose
  to catch a broad handler). Worth reading once if anyone touches the dispatch loop.
- **`D-S01-07` / `D-S01-08` — two config exemptions that exist to carry the test lock.**
  `ruff format` excludes `tests/**` (the formatter's default line length rewrote nine
  locked files); `mypy` gets an `implicit_reexport` override for `typer.testing`. Both mean
  the test tree now sits outside a check the source tree is inside.
- **`D-S01-09` / `D-S01-10` — resolved, no action needed.** A root `conftest.py` shim once
  existed so the gate's pytest argument list could name `tests/__pycache__/*.pyc` paths.
  `D-S01-09` was recorded, then found wrong and reversed by `D-S01-10`. Commit `9cee7da`
  removed the shim and re-locked; there is no root `conftest.py` in the tree today.

### Local, but live behaviour you may want to sanity-check

- **`D-S02-01` / `D-S02-02` — which scripts survive transliteration.** `anyascii` is applied
  only to characters whose Unicode name starts with LATIN, GREEK or CYRILLIC; everything
  else becomes a segment boundary rather than being rendered. So `Café` and `Übergröße`
  fold, while CJK, emoji and arrows are dropped instead of appearing as `RiBenYu` or
  `:tada:`. This decides what a Japanese-UI capture is named.
- **`D-S07-02` / `D-S07-03` — `undo` semantics.** Exit code 4 whenever any entry was left
  alone (changed contents, an occupied original name, a file that moved, a refused
  rename). Records are walked newest-first so a name is freed in the order it was taken,
  and `undo` writes nothing to the journal, so running it twice is a no-op.
- **`D-S04-03` — an unreachable backend aborts the run non-zero** and is never recorded as
  a per-file error, so one bad `--ollama-host` does not produce 4,000 error records.
- **`D-S04-05` / `D-S04-06` — `--local` runs are keyed and priced on the Ollama model**, so
  local output cannot poison a later cloud run's cache, and a local run is priced at zero
  while an *unknown* cloud model is priced at the default model's rate rather than free.
- **`D-S01-02` — entries beginning with `.` are skipped entirely**, so `.DS_Store` does not
  appear as an exclusion record in every plan.
- **`D-S03-02` / `D-S03-04` — truncation never drops the collision suffix**, and a
  well-shaped but impossible date like `2026-13-45` in a filename falls back to
  `st_birthtime` so the folder still sorts chronologically.

---

## 4. Coverage gaps (from `.sfo/REVIEW.md`)

The review audited the locked suite as a contract, after the build finished. Its
mechanical check is clean: all 80 criterion ids appear in the suite, each naming at least
one real test *function*; no orphan criteria, no unreferenced slices, exactly one
conditional skip (AC-058). Every finding is one of two other kinds — a spec requirement
that never became a criterion, or a test weaker than the criterion it claims. Totals: **4
High, 10 Medium, 11 Low.**

The four High findings are gaps 2, 3 and 4 at the top of this document: **H1** the
resolution tier, **H2** the OCR bounding-box ranking, **H3** plan replay, **H4** AC-009's
scope. The review's own priority is H1 and H2 first — both are things `SPEC.md` argues for
at length that no criterion converts into a check, so neither can fail — then H3 and M1,
the two places where a safety property well covered on the primary path is uncovered on a
secondary path the user is expected to use routinely.

Medium, in brief:

- **M1** `batch.corpus_key` / `jobs_for` — the job-store key — appear in no test; only the
  identical-command case is covered, in one direction.
- **M2** "total cost actually incurred" is barely distinguished from the estimate.
  `cost.realised_cost_usd`, where the batch halving of the realised figure lives, is called
  by no test; the end-to-end assertion is `"$" in output`.
- **M3** Three of the four supported image formats have no fixture. `tests/support/images.py`
  writes only PNG plus one BMP (as the unsupported case), so `sniff_format` is never
  exercised for JPEG, GIF or WebP, and `ModelRequest.image_media_type` is never asserted.
- **M4** *(new in this pass)* Cross-run determinism (AC-018) is satisfied only by a cache
  hit: both AC-018 tests are pure-function, and the one test that compares two runs shares
  a state directory, so the model is never re-consulted. No cold-cache comparison exists.
- **M5** AC-051 tests `retry-after`, not exponential backoff — only one 429 is ever
  injected, so a fixed `sleep(retry_after)` passes. Nothing defines terminal behaviour
  under sustained 429s.
- **M6** Refusal (AC-046) and schema-failure (AC-047) handling are tested on `--sync` only.
  A `SUCCEEDED` batch entry containing prose is a different code path with no test, and
  "retried once" has no defined meaning during batch collection — a gap in the contract,
  not just the tests. Batch is the default path for the backlog.
- **M7** Nothing bounds OCR concurrency on either dispatch path, though on the batch path
  governing OCR is the semaphore's only stated job.
- **M8** `--all-images`, `--recursive`, `--materialize` and `--min-ocr-chars` are tested
  through `RunSettings` but never through the CLI, so a wrong option string or a dropped
  keyword in `cli.run` fails no test for these four. (`--ollama-model` has no criterion at
  all.)
- **M9** Concurrent cache access has no criterion; WAL mode is unpinned.
- **M10** No criterion covers a candidate whose extension is not `[a-z0-9]+` — a genuine
  PNG named `capture.pnĝ` or `capture.PNG ` is a valid candidate by content sniffing and
  would produce a name violating AC-010. The contract does not say whether such files are
  skipped, re-extensioned, or renamed anyway.

Low, in one line each: AC-013's byte limit is only ever exercised with ASCII (L1); AC-057's
"identical pipeline test suite against both backends" is one smoke test (L2); AC-053's
partial summary, AC-072's job progress and AC-063's sample seeding are satisfied by
substrings or by not being asserted at all (L3); AC-035's fsync ordering is not tied to the
journal's descriptor (L4); AC-055's socket assertion passes vacuously if nothing opens a
socket (L5); AC-069's payload-size limit is not shown to be wired into submission (L6); the
default state directory is never asserted, since a fixture overrides it everywhere (L7);
"undo must work even if the cache is corrupt" has no criterion (L8); exclusion reasons are
asserted on the in-memory `Candidate` rather than in `plan.jsonl` (L9); AC-058 silently
skips without `/usr/bin/xattr` and `st_birthtime` falls back to `st_mtime` (L10); BLAKE2b is
never pinned as the hash, though changing it would invalidate every cached entry and every
journal `content_hash` (L11).

---

## Bottom line

The gate is green on all 11 slices and 187 tests, the implementation is complete, and the
safety architecture the spec cares most about — dry-run by default, fsync-before-rename,
case-folded collision resolution, inode-set preservation, abstention, undo — is built and
tested. What is not established is that any of it works against a real screenshot, because
the OCR call, both model transports, the Spotlight probe and the downscaler have never
executed.

A reasonable first move, in order: copy two or three dozen real screenshots into a scratch
folder, run `--sample` against it and confirm OCR returns text and the transport answers
at all; then a full dry run and read `plan.jsonl`; then `--apply` on the scratch folder and
check that `undo` restores it; only then point it at `~/Desktop`. Keep
`~/.local/state/shotname/journal.jsonl` — both `undo` and idempotency depend on that one
file.

The three things I would fix before trusting a 4,000-file run: pin the `--resolution` tier
to the bytes actually sent (H1), add a clobber and staleness check to plan replay or stop
using `--plan` (H3), and test `corpus_key` in the negative direction so a second folder
cannot collect the first folder's batch jobs (M1).
