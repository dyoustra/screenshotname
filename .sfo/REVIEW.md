# Coverage audit — `shotname`

Scope: `.sfo/SPEC.md` and `.sfo/CRITERIA.jsonl` (80 criteria) against the locked
suite in `tests/` (`TESTS.lock.json`, 11 slice files + `tests/support/`). This
audit asks only *what is unchecked*. It does not evaluate whether the code is
correct, and it proposes no edits.

**Clean result on the mechanical check:** every one of the 80 criterion ids
appears in at least one test file, and every test file's docstring accounts for
the ids it covers. There are no orphan criteria and no unreferenced slices. All
findings below are of the other two kinds: a spec requirement that never became a
criterion, or a test that is weaker than the criterion it claims.

A note on reading these: the implementation is mid-build (several modules are
still `raise NotImplementedError`), so the suite *is* the contract. "X passes the
suite" below means a future build with behaviour X would be accepted, not that
the current code does X.

---

## High

### H1. The resolution tier is never tied to the image that is actually sent

`SPEC.md` §What this is: "sends the **downscaled** image plus that OCR text to a
vision model". `settings.Resolution` is documented as "the downscale tier the
image is sent at", and the tier is a first-class run parameter precisely so the
"Pick the tier with my own screenshots" user story works.

What the criteria pin: AC-076 (the flag accepts exactly `standard`/`high` and
defaults to `standard`), AC-043 (the tier is in the cache key), AC-077 (the tier
is printed). That is the whole list.

**Missing:** no criterion and no test asserts that the bytes sent to the model
are downscaled at all, or that `--resolution high` sends more pixels than
`--resolution standard`. `ModelRequest.image_bytes` is only ever asserted for
`custom_id`/`model`/`prompt`; the one direct test of `build_model_requests`
(`test_s09_batch_dispatch.py:186`) passes the literal `b"\x89PNG"`. The cost
estimate reads tier-keyed constants (`cost.IMAGE_TOKENS_PER_TIER`) rather than
anything measured about the payload.

Consequence: a build that ignores the tier entirely — sending the original file
bytes at both tiers — passes the full suite while the estimate and the report
both claim a tier and a cost that differ. The tier-comparison story becomes a
report that says two things happened when one did.

### H2. The OCR bounding-box mechanism behind the app-first rule is unpinned

`SPEC.md` Stack row (OCR) makes this the *reason* for a dependency choice:
"Bounding boxes let title-bar text outrank body text, which is what the app-first
rule needs; human-chosen over the Live Text path for exactly that reason."
`src/shotname/ocr.py` repeats it and credits AC-019. `tests/support/fakes.py`
builds descending bboxes specifically so "AC-019 depends on title-bar text — the
topmost line — outranking body text".

**Missing:** AC-019–AC-022 test `compose_slug(app=..., subject=...)` with the
application *already decided*, and in every end-to-end test the app arrives from
the stubbed model response. No criterion or test asserts that `OcrLine.bbox` or
`OcrLine.confidence` influences any decision, that lines reach the prompt in
top-to-bottom order, or that a title-bar line outranks a body line that names a
different app. `OcrResult.text` and `OcrResult.char_count` have no direct test
either (`char_count` is the quantity AC-044 thresholds on).

Consequence: the structured `OcrResult` could be flattened to an arbitrarily
ordered blob and nothing would fail. The stated justification for choosing
`ocrmac`/`VNRecognizeTextRequest` over Live Text has no acceptance criterion
behind it.

### H3. The plan-replay path is untested for clobbering and for staleness

AC-034 checks that `--apply --plan` executes exactly the recorded renames and
issues zero model requests, and `test_ac034_replaying_a_filtered_plan...` checks
that unrecorded files are left alone. That is all of it.

**Missing two things.** First, clobber safety: AC-027 is described in `SPEC.md`
as "the single assertion that catches every clobbering bug at once", and AC-025 /
AC-026 are the case-folding and pre-existing-file rules — but all four are
exercised **only through the one-pass form** of `--apply`. No test replays a plan
whose `proposed_name` is now occupied by another file, and replay does not re-run
collision resolution (the resolved names are baked into the plan).

Second, staleness: `PlanRecord` carries `content_hash`, and `undo` is required to
verify hashes before touching a file (AC-039). Replay has no equivalent
criterion, and no test edits a file's bytes between writing a plan and replaying
it. A plan written days earlier over a directory that has since changed is the
normal way to use this flag (it is the "See the whole plan" → "Commit" story),
and nothing constrains what happens.

### H4. AC-009 measures the enumerator, not "a run"

AC-009: "a **run** over 50,000 candidate files uses no more than twice the peak
resident memory of a run over 500 candidate files."

The test (`test_s01_enumeration.py:256`) `tracemalloc`s `discover_candidates`
alone against a `SyntheticProbe`, plus a laziness check that the return value is
an `Iterator`. Nothing downstream of discovery is measured — and
`tests/support/faults.py` states the pipeline's shape explicitly: "The pipeline
cannot rename anything until it has every suggestion in hand — collision
resolution (AC-023) compares proposed names across the whole run". Proposals,
plan records, and per-request `image_bytes` are all per-file live state that this
test cannot see.

Consequence: a build whose peak memory grows linearly with corpus size — the
exact failure AC-009 exists to prevent at 4,000+ files — passes AC-009.

---

## Medium

### M1. `corpus_key` — the job-store key — has no test

`batch.corpus_key(settings)` and `JobStore.jobs_for(key)` exist because, per
`SPEC.md` (State location row), batch job ids are "keyed by target directory and
run parameters, which is what makes re-invocation collect rather than
re-submit".

**Missing:** `corpus_key` appears in no test file. AC-070/072/073/075 all
re-invoke the *identical* command, so they only exercise the positive case. The
negative is untested in both directions: a run over a *different* directory, or
the same directory with a different `--model` / `--resolution` /
`--prompt-template`, must not collect the first run's job. A `jobs_for()` that
ignored its argument and returned every recorded job would pass every batch test
while cross-contaminating results between folders — results keyed by content hash
would land in the cache under the wrong model/tier key.

### M2. "Total cost actually incurred" is never distinguished from the estimate

AC-077 requires the completion report to state "the total cost **actually
incurred**", explicitly so two `--sample` runs are comparable.

**Missing:** `test_ac077_report_states_the_model_the_tier_and_the_realised_cost`
hands `build_report` the literal `total_cost_usd=0.42`; the end-to-end test
(`test_ac077_two_sample_runs...`) only asserts `"$" in output`. `cost.realised_cost_usd`
— which takes `endpoint`, i.e. it is where the batch halving of the *realised*
figure lives — is called by no test at all. `FakeTransport` returns
`input_tokens=1200, output_tokens=40` per response and no test reads them back
out of a report.

Consequence: a build that prints the pre-run estimate as the realised cost, or
forgets the batch discount on the realised figure, passes. The number the user is
told to compare tiers on is unconstrained.

### M3. Three of the four supported image formats have no fixture

AC-008 fixes the supported set at JPEG, PNG, GIF, WebP, and the test asserts that
set equality on `SUPPORTED_IMAGE_FORMATS`. But `tests/support/images.py` writes
only PNG (`write_png`/`write_solid_png`/`write_noise_png`) and one BMP, which
exists purely as the *unsupported* case.

**Missing:** no JPEG, GIF, or WebP fixture anywhere. So nothing exercises
`FsProbe.sniff_format` for three of the four formats it must accept, the
selection of `ModelRequest.image_media_type` (never asserted in any test), or
`normalize_extension` end-to-end for a non-`.png` file — AC-016's `.JPEG` case is
a bare string assertion, not a file. A sniffer that recognised only PNG would
silently exclude the JPEG and WebP captures in a real corpus and pass the suite.

### M4. AC-051 tests retry-after, not exponential backoff

AC-051 names two behaviours: exponential backoff, and honouring `retry-after`.
`rate_limit_at(0)` injects exactly **one** 429, and the test asserts
`elapsed >= retry_after` and `max(attempts.values()) == 2`.

**Missing:** no test injects two or more consecutive 429s for the same request,
so nothing observes a growing delay — a fixed `sleep(retry_after)` satisfies the
suite. There is also no criterion bounding the retry count or covering
exhaustion, so "a 429 storm" has no defined terminal behaviour (does the file
become `error`? does the run abort?).

### M5. Refusal and schema-failure handling on the batch path

AC-046 (refusal → `abstain`/`model_declined`) and AC-047 (schema failure →
retry once, then `error`) are both tested with `--sync` only
(`test_s08_abstention.py` passes `--sync` for all four).

**Missing:** no test delivers a refusal or a schema-invalid body as a *batch
result entry*. AC-074 covers only provider-level `errored`/`expired` statuses —
a `SUCCEEDED` entry whose text is prose is a different case and reaches a
different code path. Worse, AC-047's "retried once" has no meaningful
interpretation during batch collection (a retry means a new synchronous call or a
new job), and neither `SPEC.md` nor any criterion says which. This is a genuine
gap in the contract, not just in the tests.

### M6. Nothing bounds OCR concurrency, on either dispatch path

`SPEC.md` Stack row (Concurrency): "a bounded pool is what makes 429 handling and
clean SIGINT tractable. **On the batch path the semaphore governs OCR and job
submission only.**"

AC-052 is scoped to "the synchronous dispatch path" and counts only in-flight
*model requests* (`FakeTransport.max_in_flight`). `FakeOcr` records calls but
never concurrency.

**Missing:** no criterion or test bounds simultaneous OCR operations. On the
batch path — the default for the 4,000-file backlog — the semaphore's *only*
stated job is governing OCR and submission, and neither is asserted. Unbounded
OCR fan-out is also a plausible cause of the AC-009 failure mode in H4.

### M7. Four flags are tested through `RunSettings`, never through the CLI

- AC-003 (`--all-images`), AC-004 (`--recursive`), AC-007 (`--materialize`): the
  tests construct `RunSettings(root=..., all_images=True)` etc. directly and
  drive `discover_candidates`. The flags do appear in
  `test_ac048_...`'s parametrize list, but that test asserts only that abstained
  and errored files are not renamed — nothing about the flags' effect.
- AC-044 (`--min-ocr-chars`): the test asserts `DEFAULT_MIN_OCR_CHARS == 12` and
  the `RunSettings` default. No test passes `--min-ocr-chars` on the command
  line, so nothing checks that a non-default threshold changes which files
  abstain.

Consequence: a wrong option string or a missing keyword in `cli.run`'s
`RunSettings(...)` construction fails no test for these four. Compare `--model`,
`--resolution`, `--seed`, `--concurrency`, `--prompt-template`, `--ollama-host`
and `--poll-interval`, which *are* asserted to reach their effect through the
CLI — the gap is inconsistent rather than deliberate.

### M8. Concurrent cache access has no criterion

`SPEC.md` Stack row (Cache): "SQLite (WAL mode), keyed by content hash … Random
access lookup by hash across runs, **safe under a concurrent worker pool**."

**Missing:** AC-043 covers key composition and a single-threaded round trip
(`test_ac043_cache_round_trips_ocr_and_suggestions`). No criterion pins WAL mode
or asserts that N workers writing suggestions concurrently (default
`--concurrency 8`) neither lose a row nor raise `database is locked`. This is the
kind of defect that appears only at backlog scale, which is the only scale this
tool is for.

### M9. No criterion covers a candidate whose extension is not `[a-z0-9]+`

Format eligibility is decided by content sniffing (AC-008), not by extension, so
a genuine PNG named `capture.pnĝ`, `capture.PNG ` (trailing space), or with no
extension at all is a valid candidate. `normalize_extension` lowercases whatever
suffix is present and `build_filename` carries it through as a fixed tail.

**Missing:** no fixture and no criterion. AC-010's regex requires
`\.[a-z0-9]+$`, so such a file would produce a name that violates AC-010 — and
AC-010's test only feeds `build_filename` extensions that are already clean
(`.png`, `.PNG`, `.jpeg`, `.gif`, `.webp`). The contract does not say whether
these files are skipped, re-extensioned from the sniffed format, or renamed
anyway.

---

## Low

### L1. AC-013's byte limit is only ever exercised with ASCII

AC-013 says "at most 255 **bytes** when encoded as UTF-8". Every input to
`fit_to_byte_limit` and `build_filename` in `test_s03_naming.py` is ASCII, so
`len(name)` and `len(name.encode())` are indistinguishable to the suite. AC-017
makes slugs ASCII, which closes most of the gap — but the *extension* is never
transliterated (see M9), and `fit_to_byte_limit`'s degenerate byte-wise branch
(the `decode("utf-8", "ignore")` path) is reachable only via a long extension and
is unexercised.

### L2. AC-057 is a smoke test, not "the identical pipeline test suite"

AC-057's wording — "the identical pipeline test suite passes against both when
only the backend selection flag is changed" — is satisfied by one test
parametrized over `("", "--local")` that asserts exit 0, plan coverage, and that
some record is a `RENAME`, plus a check that `--local` never reaches the Batch
API. The rest of the suite (journal, collisions, abstention, reporting) runs
cloud-only. The criterion as written implies far broader coverage than exists;
either the criterion is overstated or the parametrization is.

### L3. Two "reports X" clauses are satisfied by a substring

- AC-053 requires a *partial summary* on SIGINT; the test asserts
  `"interrupt" in output.lower()`. No count or per-action figure is required.
- AC-072 requires reporting the in-flight job's *progress*; the test asserts the
  job id appears in the output and that a poll happened. A build that prints the
  id and nothing about state passes.
- AC-063's sample determinism is shown by calling `build_report(..., seed=3)`
  twice. Nothing asserts that the run's `--seed` reaches the report sampler, so
  the report's 20 pairs could be reseeded per invocation.

### L4. The default state directory is never asserted

`SPEC.md` places cache, journal, plans, and job records in
`~/.local/state/shotname/`. The autouse `state_dir` fixture sets
`SHOTNAME_STATE_DIR` for every test, so `settings.state_dir_path`'s default
branch is unexercised and no criterion names the location. Blast radius is
limited — AC-028's `snapshot_dir` comparison would catch state written into the
target directory — but a default of `cwd` or `~/Library` would pass.

### L5. "Undo must work even if the cache DB is corrupt or deleted" has no criterion

`SPEC.md` Stack row (Journal) gives this as the *reason* the journal is JSONL
rather than another SQLite table: "undo must work even if the cache DB is corrupt
or deleted, and a plain-text journal is greppable and auditable by hand". No
criterion states it and no test deletes or truncates `cache.sqlite` before
running `undo`. Two mechanisms were chosen for independence; nothing verifies
they are independent.

### L6. Exclusion reasons are asserted on the `Candidate`, not in `plan.jsonl`

AC-008 requires "a per-file exclusion reason is **recorded in the plan output**",
and AC-006 requires the dataless count in the run report. The AC-008 test asserts
`candidate.excluded is Reason.UNSUPPORTED_FORMAT` on the in-memory object; the
AC-006 summary test calls `format_discovery_summary` directly. AC-029/AC-030
check that plan records carry a `reason` *field* and a legal `action`, but no
test asserts that the BMP's or the dataless file's plan record carries the
specific reason value. (Contrast AC-041, which does assert
`record.reason == Reason.ALREADY_RENAMED.value`.)

### L7. AC-058 silently skips when `/usr/bin/xattr` is absent

`@pytest.mark.skipif(not xattr_available())` turns a missing `xattr` binary into a
pass rather than a failure, so the only check that extended attributes survive
the rename can vanish without anyone noticing. Low severity because the tool is
macOS-only and the binary ships with the OS. Related: `fsutil.birthtime` falls
back to `st_mtime` when `st_birthtime` is absent, which would quietly reduce
AC-059's birthtime assertion to a duplicate of its mtime assertion off-macOS.

### L8. The macOS signals themselves are stubbed by construction

Worth stating so the coverage is not misread: `OverrideProbe` forces
`is_screen_capture` and `SF_DATALESS` per path, and `FakeOcr` replaces
`VisionOcr` everywhere. So AC-002/006/007 assert what the *enumerator* does given
a signal, never that `RealFsProbe` reads `kMDItemIsScreenCapture` or
`stat.st_flags` correctly, and `VisionOcr.recognize` has no test at all. This is
unavoidable for a hermetic suite — `tests/conftest.py` explains the choice — but
it means the spec's claim that `kMDItemIsScreenCapture` is "the real signal"
(SPEC group table, AC-002) rests on unexercised code.

---

## Summary

| Severity | Count | Ids / sections most affected |
|---|---|---|
| High | 4 | AC-076/077 + SPEC downscale claim; AC-019–022 + SPEC OCR row; AC-034 vs AC-025/026/027/039; AC-009 |
| Medium | 9 | AC-070–075 keying; AC-077; AC-008/016; AC-051; AC-046/047; AC-052 + SPEC concurrency row; AC-003/004/007/044; SPEC cache row; AC-008/010/016 |
| Low | 8 | AC-013, AC-057, AC-053/063/072, state dir, SPEC journal row, AC-006/008, AC-058/059, AC-002/006/007 |

The two findings I would act on first are **H1** and **H2**, because both are
requirements the spec argues for at length — the downscale tier and the
bounding-box ranking — that no criterion converts into a check, so neither can
fail. **H3** and **M1** are next: they are the two places where a safety property
that *is* well covered on the primary path (clobber-freedom, submit-once) is
uncovered on a secondary path the user is expected to use routinely.
