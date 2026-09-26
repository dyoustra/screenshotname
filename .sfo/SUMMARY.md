# Summary — `shotname` (screenshot content renamer)

## Read this first: the tool does not run

**There is no working command.** `shotname run` and `shotname undo` both parse their
flags correctly and then raise `NotImplementedError` before doing anything.
`src/shotname/cli.py:42` (`_dispatch_run`), `src/shotname/cli.py:47`
(`_dispatch_undo`) and `src/shotname/deps.py:33` (`default_deps`) are stubs. Nothing
in this repository can rename a file, propose a name for a file on disk, write a
`plan.jsonl`, call a model, or undo anything.

**2 of 11 slices passed the gate. 9 did not.**

| Slice | What it covers | Gate outcome |
|---|---|---|
| S-01 | Enumeration and candidate identification | **abandoned** after 2 attempts (3 gate records, never `ok`) |
| S-02 | Slug composition and text sanitization | passed, attempt 1 |
| S-03 | Filename assembly: date prefix, extension, byte limit, determinism | passed, attempt 1 |
| S-04 | Backend interface, cost accounting, failure modes | **never attempted** — blocked on S-01 |
| S-05 | Dry run, plan output, apply | **never attempted** — blocked on S-01, S-04 |
| S-06 | Collision resolution and metadata-preserving rename | **never attempted** |
| S-07 | Journal, undo, cache, resume | **never attempted** |
| S-08 | Abstention | **never attempted** |
| S-09 | Batch dispatch and request construction | **never attempted** |
| S-10 | Batch job lifecycle: submit, record, collect, resume | **never attempted** |
| S-11 | Sampling, run settings, completion report | **never attempted** |

**13 of the 80 acceptance criteria are verified.** Those are AC-010 – AC-013,
AC-014 – AC-022 in part — precisely: AC-010, AC-011, AC-012, AC-013, AC-014,
AC-015, AC-016, AC-017, AC-018, AC-019, AC-020, AC-021, AC-022.

**67 criteria are unmet.** By group, and what the person cannot do as a result:

- **AC-001 – AC-009 (enumeration).** The tool cannot find its own input. It cannot
  match a U+202F screenshot name, read `kMDItemIsScreenCapture`, honour
  `--recursive`, or skip iCloud-evicted files. This is the slice that was abandoned.
- **AC-023 – AC-027 (collisions), AC-058 – AC-059 (metadata).** No collision
  resolution exists at all (`src/shotname/collisions.py` is entirely stubs, and
  `hashing.short_hash` — the six-hex suffix — raises). AC-027, which `SPEC.md` calls
  "the single assertion that catches every clobbering bug at once", has never run.
- **AC-028 – AC-034, AC-078 – AC-079 (dry run, plan, apply).** There is no dry run,
  no `plan.jsonl`, no `--apply`, and no cost estimate printed before requests. The
  "See the whole plan" and "Commit and walk away" stories do not exist.
- **AC-035 – AC-043 (journal, undo, idempotency, resume).** No journal is written
  and `undo` does nothing. AC-035 (fsync before `rename(2)`) and AC-036 (kill between
  the two) — the two criteria `SPEC.md` singles out as carrying the safety weight —
  are unverified. **If the rename path were completed without these, a run would be
  irreversible.**
- **AC-044 – AC-048, AC-080 (abstention).** No abstention path. Blank screenshots
  would be named rather than left alone.
- **AC-049 – AC-057 (backends, cost, failure modes).** No Full Disk Access message,
  no missing-key check, no 429 backoff, no bounded concurrency, no SIGINT handling,
  and no local backend. `--local` does not keep anything off the network because
  nothing goes anywhere.
- **AC-060 – AC-064, AC-076 – AC-077 (sampling and reporting).** No `--sample`, so
  the "Try before trusting" and "Pick the tier with my own screenshots" stories —
  the two that were supposed to de-risk the whole backlog run — are unavailable.
- **AC-065 – AC-075 (batch execution and job lifecycle).** No batch dispatch. In
  particular AC-072 ("re-invoking while a job is in flight submits zero new
  requests"), which `SPEC.md` calls out as the new failure mode batch dispatch
  introduces, is unverified.

### Why S-01 was abandoned

One test: `test_ac009_peak_memory_does_not_scale_with_corpus_size`. The other 17
tests in `tests/test_s01_enumeration.py` pass. I re-ran that file out of band while
writing this and saw `1 failed, 17 passed`, with peak memory growing 3,630 → 1,926,050
bytes across a 100× corpus against a 2× budget.

`D-S01-06` records the diagnosis: `PurePath._parse_path` calls `sys.intern` on every
path segment, so 50,000 distinct filenames permanently add ~830 KB to the interned
string table. `discover_candidates` must call `probe.facts` for every path and
`SyntheticProbe.facts` reads `path.name`, so **no implementation can avoid this on
CPython 3.12 or 3.13.** Python 3.14 removed the interning and the test passes there
unchanged. `pyproject.toml` pins `requires-python = ">=3.12"`, `SPEC.md` names Python
3.12, and the gate invokes `python3 -m pytest` — which here is 3.13.5.

The build agent chose to leave the test failing rather than pad a baseline allocation
to game the ratio. That was the right call, and it is also the single reason 67
criteria were never attempted: S-01 has no prerequisites, and S-04 and S-05 both
depend on it, so its abandonment blocked the other eight slices.

---

## 1. What this is and how to run it

`shotname` was specified as a macOS CLI that renames screenshots after their visible
content, running Apple Vision OCR on-device and then a vision model to produce an
app-first slug. **What exists is a test suite and four working modules.** Treat this
as a partial library, not a tool.

Working directory: `/Users/dannyyoustra/.sfo/screenshot-content-renamer-fee869`

### Set up

A virtualenv already exists at `.venv` with the dependencies installed. To recreate
it:

```bash
cd /Users/dannyyoustra/.sfo/screenshot-content-renamer-fee869
uv sync                       # needs network access
```

The system `python3` on this machine does **not** have `anyascii` installed, and
`src/shotname/slug.py` imports it at module scope. Running the suite with bare
`python3` fails at collection for `tests/test_s02_slug.py` and
`tests/test_s03_naming.py` with `ModuleNotFoundError: No module named 'anyascii'`.
Use the venv's interpreter:

```bash
.venv/bin/python -m pytest -q
```

`pyproject.toml` sets `pythonpath = ["src", "."]`, so no install step is needed for
the tests to import the package.

### Run the parts that passed their gate

```bash
.venv/bin/python -m pytest tests/test_s02_slug.py tests/test_s03_naming.py -q
```

### Run the slice that failed

```bash
.venv/bin/python -m pytest tests/test_s01_enumeration.py -q
# 17 passed, 1 failed (AC-009) on CPython 3.13
```

If you have Python 3.14 available, AC-009 is reported to pass unchanged there; that
is worth checking before doing anything else, because it is the block on eight
slices.

### Lint and typecheck

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy --strict src tests
```

### The CLI

```bash
.venv/bin/python -m shotname.cli --help    # flag surface renders
```

Any actual invocation (`run` or `undo`) raises `NotImplementedError`. **Do not point
it at a real screenshot folder** expecting either a rename or a safe no-op; it will
traceback. The console-script entry point `shotname = "shotname.cli:main"` is
declared in `pyproject.toml` but the package is not installed into the venv, so
`shotname` is not on `PATH`.

### What is actually implemented

- `src/shotname/slug.py` — transliteration and slugification, app alias map
- `src/shotname/naming.py` — date prefix, byte-limit fitting, filename assembly
- `src/shotname/discovery.py` — candidate enumeration (implemented; slice failed on
  AC-009 only)
- `src/shotname/rename.py` — the guarded `rename(2)` wrapper
- `src/shotname/hashing.py` — `content_hash` only; `short_hash` raises
- `src/shotname/data/app_aliases.json` — the alias map data file

Everything else (`abstain`, `batch`, `cache`, `cli`, `collisions`, `cost`, `deps`,
`journal`, `model`, `ocr`, `plan`, `prompt`, `report`, `sampling`, and parts of
`settings`) is stub bodies raising `NotImplementedError` — 49 of them.

---

## 2. What was not verified

"Verified" below means exactly this: the gate's `install`, `lint`, `typecheck` and
`test` steps ran and exited 0 for slices S-02 and S-03, at `2026-09-26T19:23:37Z` and
`2026-09-26T19:32:07Z`. Nothing else.

**Every gate attempt reached a step.** All three S-01 records carry a `failedStep`
(`lint`, then `test`, then `test`), so none of the "gate never got started" reasons
apply: the suite was never found unlocked or edited, the tests were always located,
the `cli-python` archetype has a recipe, and no build agent exited non-zero.

**`tamperedTests` is empty in all five records.** No build modified a hash-locked
test file through the gate.

**But the locked suite was edited by hand outside the pipeline.** `D-relock-b11`
records that `test-repair` locked a suite which did not itself pass lint or
typecheck, so *no slice could have passed its gate*. Four lint/type errors were then
fixed in locked test files by a developer's assistant, the suite was re-locked, and
S-01's attempt count was reset to 0. This is why the first two VERIFY records both
read `"attempt":1`. `tamperedTests` cannot see this by construction — it compares
against the lock, and the lock was rewritten. The stated fixes were an import
ordering, a `noqa` on a deliberate local-time call, and a `Result` type annotation,
with no assertion changed. **That claim is not independently verified anywhere in
the run record.** If you trust nothing else in this document, verify that one.

**What the `cli-python` recipe does not cover, at all:**

- **Real macOS signals.** `OverrideProbe` forces `is_screen_capture` and
  `SF_DATALESS` per path; `FakeOcr` replaces `VisionOcr` everywhere.
  `VisionOcr.recognize` has no test. So the spec's central claim that
  `kMDItemIsScreenCapture` is "the real signal" rests on code that has never
  executed. `D-S01-01` shows it was implemented by shelling out to `mdfind` — a
  choice no test exercises.
- **Any real API call.** Model calls are stubbed at a fake transport (`D-044`). No
  request has ever been sent to Anthropic or to Ollama. Cost figures in the spec are
  arithmetic, not measurement.
- **Packaging and installation as a user would receive it.** Nothing verifies `uvx
  shotname`, the console script, or that dependencies resolve on a clean machine.
  `D-S01-05` notes `uv sync` could not reach the network during the build.
- **More than one interpreter.** Lint, typecheck and test ran on a single CPython
  3.13. AC-009's outcome is interpreter-dependent, which the single-version gate is
  structurally unable to surface.
- **The real corpus.** Nothing has been run against the ~4,000-file backlog the tool
  exists for, or against any real screenshot.
- **Runtime, throughput, or spend at scale.** No timing and no cost is measured
  anywhere.

**One caveat on my own reporting.** I could not re-run `ruff` or `mypy` while writing
this — they are not importable by the interpreter available to me, and the sandbox
declined to execute `.venv/bin/*`. The lint and typecheck statements above are the
gate's record, not a fresh check. The pytest results for S-01 I did run and observe
directly.

---

## 3. Decisions worth reviewing

### External blast radius

- **`D-003` — no link rewriting.** The tool will not update references to renamed
  files, on the human's confirmation that this corpus is "an unreferenced Desktop
  junk drawer". If that is no longer true — if any of these screenshots are
  referenced from an Obsidian vault, a Markdown doc, or a repo — in-place renaming
  breaks those links and the undo journal is the only mitigation. The undo journal
  is also unbuilt (AC-035 – AC-040 unverified).
- **`D-015` / `D-046` — default model and resolution tier.** `claude-haiku-4-5-20251001`
  at the `standard` tier. This is the entire bill. The mechanism intended to validate
  it before the backlog run — `--sample` — is in S-11, never attempted.
- **`D-036` / `D-052` — no default cost ceiling.** `--max-cost` is opt-in. Nothing
  stands between a mistyped path and a large bill except the pre-run estimate, and
  the estimate (AC-032) is also unverified.
- **`D-032` is contradicted by `D-047` and nothing in the file marks it superseded.**
  `D-032` (agent, 20:44) chose synchronous dispatch and rejected the Batch API;
  `D-047` (human, 21:01) reversed it and chose Batch for full runs with synchronous
  `--sample`. `SPEC.md` reflects `D-047`. A reader working from `DECISIONS.jsonl`
  alone would find two live, opposed decisions on the most consequential
  architectural question in the project.
- **The budget ceiling was overrun twice and the pipeline parked itself both times.**
  `D-budget-test-write-9.4`: $10.52 spent against a $9.40 ceiling.
  `D-budget-test-repair-18.5`: $22.25 against $18.50. Work resumed after each. Total
  recorded spend across all 16 stages is **$55.57**, of which **$15.11 went to three
  `build-S-01` attempts that never passed.**

### Structural blast radius

- **`D-relock-b11` — the test contract was amended after locking.** Covered in §2.
  This is the governance item I would look at first: the suite is the contract, and
  it was edited outside the pipeline, by hand, and re-locked. Everything downstream
  inherits that.
- **`D-S01-10` — a `conftest.py` shim exists to satisfy the gate, not the product.**
  The gate's pytest argument list included `tests/__pycache__/*.pyc` paths, which
  pytest cannot collect, making exit 0 impossible for *any* implementation. The fix
  was a root `conftest.py` whose `pytest_collect_file` claims a `.pyc` when it is
  named on the command line and returns a collector holding no tests. The decision
  record is candid that this is a workaround with residual fragility — `__pycache__`
  is gitignored, so a `git clean -xdf` removes the artifact and no in-repo code can
  restore it before argument resolution. It also reverses the preceding decision
  `D-S01-09`, which was recorded, then found wrong. **Decide whether this shim stays
  in the repository.** The durable fix is in the gate, not the project.
- **`D-S01-07` and `D-S01-08` — two more configuration exemptions carrying the lock.**
  `ruff format` is excluded from `tests/**` because the formatter's default
  line-length of 88 rewrote nine locked files at 92 columns; `mypy` gets an
  `implicit_reexport` override for `typer.testing`. Both are reasonable given the
  lock, and both mean the test tree is now outside a check the source tree is inside.
- **`D-002` rename in place, `D-023` dry-run-by-default, `D-019`/`D-020` journal
  substrate and ordering, `D-022` case-folded collision comparison, `D-040`
  same-volume `rename(2)`.** This is the entire safety architecture of the tool and
  **none of it is built or verified.** They are listed here because they are the
  decisions a human would most want to confirm before anyone finishes this, not
  because they were tested.
- **`D-054` / `D-055` — the two-phase batch run.** A full run submits jobs, records
  ids keyed by corpus and run parameters, exits without renaming; re-invoking the
  same command collects. This is the most intricate machinery in the spec, it is the
  default path for the 4,000-file backlog, and it does not exist.
- **`D-053` — classic `VNRecognizeTextRequest` over Live Text**, chosen specifically
  because it returns the per-line bounding boxes the app-first rule depends on. See
  coverage finding H2 below: that dependency is the reason for the choice and no
  criterion checks it.

### Local, but worth a look because it is shipped and verified

- **`D-S02-01` — which scripts get transliterated.** `anyascii` is applied only to
  characters whose Unicode name begins with LATIN, GREEK or CYRILLIC; everything else
  becomes a segment boundary. So `Café` and `Übergröße` fold, and CJK, emoji and
  arrows are dropped rather than rendered as `RiBenYu` or `:tada:`. This is live
  behaviour in a slice that passed, and it decides what a Japanese-UI capture is
  named. `D-S02-02` (dropped characters become a space, not nothing) is the companion
  call.
- **`D-S03-02` — truncation never drops the collision suffix.** The suffix travels
  with the extension as a fixed tail. Sound reasoning, and it is worth noting the
  other half — `hashing.short_hash`, which produces that suffix — is unimplemented.
- **`D-S03-04` — a well-shaped but impossible date falls back to `st_birthtime`.**
  `2026-13-45` would satisfy the name regex; it is rejected anyway so the folder
  sorts chronologically.

---

## 4. Coverage gaps

`.sfo/REVIEW.md` audited the locked suite as a contract and found **4 High, 9 Medium,
8 Low**. Its mechanical check was clean: all 80 criterion ids appear in at least one
test file, with no orphans and no unreferenced slices. Every finding is of the other
two kinds — a spec requirement that never became a criterion, or a test weaker than
the criterion it claims.

**Read those findings with this correction in mind:** most of them concern tests that
never ran against an implementation, because the slice they belong to was never
attempted. They describe how much a *future* build could get away with. Four
findings, however, land on code that is built and passed its gate, and those are
live now:

- **H2 — the OCR bounding-box mechanism behind app-first is unpinned.** AC-019 –
  AC-022 passed in S-02, but they call `compose_slug(app=..., subject=...)` with the
  application *already decided*. Nothing asserts that `OcrLine.bbox` or
  `.confidence` influences any decision, that lines reach the prompt top-to-bottom,
  or that a title-bar line outranks a body line naming a different app. The
  app-first rule is the spec's central quality claim (`D-006`, human-decided,
  overriding the agent's recommendation) and the OCR engine was chosen for it
  (`D-053`). **What passed is the slug assembler, not the ranking that makes it
  correct.**
- **L1 — AC-013's 255-**byte** limit is only ever exercised with ASCII**, so
  `len(name)` and `len(name.encode())` are indistinguishable to the suite. AC-017
  closes most of the gap by making slugs ASCII, but the extension is never
  transliterated, and `fit_to_byte_limit`'s byte-wise fallback branch is unreachable
  in the tests.
- **M9 — no criterion covers a candidate whose extension is not `[a-z0-9]+`.**
  Format eligibility is decided by content sniffing, so a genuine PNG named
  `capture.pnĝ` or `capture.PNG ` is a valid candidate — and would produce a name
  that violates AC-010, which passed. The contract does not say whether such files
  are skipped, re-extensioned, or renamed anyway.
- **M3 — three of the four supported image formats have no fixture.** AC-008 fixes
  the set at JPEG, PNG, GIF, WebP; `tests/support/images.py` writes only PNG plus one
  BMP as the unsupported case. So AC-016's `.JPEG` handling is a bare string
  assertion, not a file, and nothing exercises `sniff_format` for JPEG, GIF or WebP.

The remaining High findings concern unbuilt slices and stand as warnings for whoever
finishes the work:

- **H1 — the resolution tier is never tied to the image actually sent.** No criterion
  asserts the bytes are downscaled at all, or that `high` sends more pixels than
  `standard`. A build that ignores the tier entirely passes the suite while the
  estimate and the report both claim a tier. The tier-comparison user story becomes
  a report that says two things happened when one did.
- **H3 — the plan-replay path is untested for clobbering and for staleness.** AC-025,
  AC-026 and AC-027 are exercised only through the one-pass `--apply`. No test
  replays a plan whose `proposed_name` is now occupied, and replay does not re-run
  collision resolution — the names are baked in. Nor does any test edit a file's
  bytes between writing and replaying a plan, though `undo` is required to verify
  hashes (AC-039) and replay has no equivalent criterion.
- **H4 — AC-009 measures the enumerator, not "a run".** The test `tracemalloc`s
  `discover_candidates` alone. Proposals, plan records and per-request `image_bytes`
  are per-file live state it cannot see, and collision resolution needs every
  proposal in hand at once. So a build whose peak memory grows linearly with corpus
  size — the exact failure AC-009 exists to prevent — would pass AC-009. Note the
  irony: this is the criterion that blocked the entire build, and it does not measure
  the thing it names.

Medium findings on unbuilt slices, in brief: `corpus_key` — the job-store key that
makes re-invocation collect rather than re-submit — appears in no test (M1); "total
cost actually incurred" is never distinguished from the estimate (M2); AC-051 tests
`retry-after` but not exponential backoff, since only one 429 is ever injected (M4);
refusal and schema-failure handling are tested on the synchronous path only, and
AC-047's "retried once" has no defined meaning during batch collection — a genuine
gap in the contract, not just the tests (M5); nothing bounds OCR concurrency on
either dispatch path, though on the batch path governing OCR is the semaphore's only
stated job (M6); `--all-images`, `--recursive`, `--materialize` and `--min-ocr-chars`
are tested through `RunSettings` but never through the CLI (M7); concurrent cache
access has no criterion despite WAL mode being the stated reason for choosing SQLite
(M8).

The review's own recommendation is to act on **H1** and **H2** first, because both
are requirements `SPEC.md` argues for at length that no criterion converts into a
check — so neither can fail — followed by **H3** and **M1**, where a safety property
well covered on the primary path is uncovered on a secondary path the user is
expected to use routinely.

---

## Bottom line

Do not trust this to rename anything, because it cannot. What you have is a locked
80-criterion test suite, a coverage audit of that suite, 13 verified criteria covering
slug composition and filename assembly, and a working enumerator blocked by a single
memory-scaling assertion that appears to be unsatisfiable on the pinned interpreter.

The highest-leverage next step is to settle AC-009 — confirm the `D-S01-06` analysis
on Python 3.14, or amend the criterion to measure a run rather than the enumerator as
finding H4 argues it should. S-01 has no prerequisites, and eight of the nine
unattempted slices are downstream of it.
