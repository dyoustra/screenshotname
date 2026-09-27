# Coverage audit — `shotname`

Scope: `.sfo/SPEC.md` and `.sfo/CRITERIA.jsonl` (80 criteria) against the locked
suite in `tests/` (`TESTS.lock.json`: 11 slice files plus `tests/support/`), with
the now-complete implementation in `src/shotname/` used only to confirm that a
gap is a *missing check* rather than a missing feature. This audit asks what is
unchecked. It evaluates no behaviour for correctness and proposes no edits.

Run state: the build is finished through S-11 and `.sfo/VERIFY.jsonl` records
every slice green. The previous audit at commit `6cb2b27` ran mid-build; since
then the only test change is `9cee7da` (AC-009 re-measured as peak RSS). The
findings below are therefore close to that audit's by construction — the suite it
examined is the suite that shipped. Where re-verification changed a finding, it
is marked.

**The mechanical check is clean.** All 80 criterion ids appear in the suite, and
each one names at least one test *function* (`def test_acNNN_…`), not merely a
section comment or a module docstring. There are no orphan criteria, no
unreferenced slices, and exactly one conditional skip (AC-058, below). Every
finding here is of the other two kinds: a spec requirement that never became a
criterion, or a test weaker than the criterion it claims.

---

## High

### H1. The resolution tier is never tied to the image that is actually sent

`SPEC.md` §What this is: the tool "sends the **downscaled** image plus that OCR
text to a vision model". `imaging.py`'s own docstring calls itself "the only
place the `--resolution` tier means anything," and `MAX_EDGE_PER_TIER` maps
standard→1024px, high→1568px.

What the criteria pin: AC-076 (the flag accepts exactly `standard`/`high`,
defaults to `standard`), AC-043 (the tier is in the cache key), AC-077 (the tier
is printed in the estimate and the report). That is the whole list.

**Missing:** `imaging`, `encode_for_model`, and `MAX_EDGE_PER_TIER` appear in no
test file. No test asserts that the bytes reaching the transport are downscaled
at all, or that `--resolution high` sends more pixels than `--resolution
standard`. `ModelRequest.image_bytes` is only ever asserted on hand-built
requests in `test_s09_batch_dispatch.py:36-44,186-208`, which pass literal
`b"\x00"`/`b"\x89PNG"` payloads. The cost estimate reads the tier-keyed constant
`cost.IMAGE_TOKENS_PER_TIER` rather than anything measured about the payload.

Consequence: a build that ignored the tier entirely — sending original Retina
bytes at both tiers — passes the full suite, while the estimate and the report
both state a tier and a cost that differ between two runs that did the same
thing. The "Pick the tier with my own screenshots" story rests on this.

### H2. The OCR bounding-box mechanism behind the app-first rule is unpinned

`SPEC.md` Stack row (OCR) makes this the *reason* for the dependency choice:
"Bounding boxes let title-bar text outrank body text, which is what the app-first
rule needs; human-chosen over the Live Text path for exactly that reason."
`ocr.py:70-73` implements it — Vision's origin is bottom-left, so lines are
sorted by descending `y` — and credits AC-019. `tests/support/fakes.py:58-74`
builds descending bboxes for the same stated reason.

**Missing:** AC-019–AC-022 exercise `compose_slug(app=…, subject=…)` with the
application *already decided*, and in every end-to-end test the app arrives
pre-decided from the stubbed model response. No criterion or test asserts that
`OcrLine.bbox` or `OcrLine.confidence` influences any decision, that lines reach
the prompt top-to-bottom, or that a title-bar line outranks a body line naming a
different app. `VisionOcr.recognize` — where the sort lives — has no test, and
`OcrResult.text` / `OcrResult.char_count` have no direct test either, though
`char_count` is the quantity AC-044 thresholds on.

Consequence: `OcrResult` could be flattened to an arbitrarily ordered blob and
nothing would fail. The justification for choosing `ocrmac`/`VNRecognizeTextRequest`
over Live Text has no acceptance criterion behind it.

### H3. The plan-replay path is untested for clobbering and for staleness

AC-034 checks that `--apply --plan` runs exactly the recorded renames and issues
zero model requests; `test_ac034_replaying_a_filtered_plan_renames_only_what_it_records`
checks that unrecorded files are left alone. That is all of it.

`pipeline._replay` (line 851) reads the plan, filters to `Action.RENAME`, and
calls `_apply`. It does not re-run collision resolution — the resolved names were
baked into the plan — and it does not compare `PlanRecord.content_hash` against
the file on disk.

**Missing, two things.** First, clobber safety: `SPEC.md` calls AC-027 "the
single assertion that catches every clobbering bug at once", and AC-025/AC-026
are the case-folding and pre-existing-file rules — but all three are exercised
**only through the one-pass form** of `--apply`. No test replays a plan whose
`proposed_name` is now occupied. Second, staleness: `undo` must verify hashes
before touching a file (AC-039); replay has no equivalent criterion and no test
edits a file's bytes between writing a plan and replaying it. A plan written days
earlier is the normal way to use the flag — it is literally the "See the whole
plan" → "Commit and walk away" story — and nothing constrains what happens.

### H4. AC-009 measures the enumerator, not "a run"

AC-009: "a **run** over 50,000 candidate files uses no more than twice the peak
resident memory of a run over 500 candidate files."

*Changed since the previous audit:* `9cee7da` replaced a `tracemalloc` probe with
`resource.getrusage` in a fresh interpreter per corpus size
(`test_s01_enumeration.py:262-295`), which is the right instrument. The
measurement is now sound; the **scope** is not. `_RSS_PROBE` iterates
`discover_candidates` against a `SyntheticProbe` and nothing else.

**Missing:** nothing downstream of discovery is measured. `pipeline._resolve`
(line 601) holds every `Proposal` for the whole run because collision resolution
compares proposed names across the corpus; plan records, per-unit `_Outcome`s,
and per-request `image_bytes` are all live per-file state this test cannot see.

Consequence: a build whose peak memory grows linearly with corpus size — the
exact failure AC-009 exists to prevent at 4,000+ files — passes AC-009.

---

## Medium

### M1. `corpus_key` — the job-store key — has no test

`batch.corpus_key` (line 41) hashes the resolved root plus model, resolution,
prompt template, and the three enumeration flags; `JobStore.jobs_for` filters on
it. Per `SPEC.md` (State location row), job ids are "keyed by target directory
and run parameters, which is what makes re-invocation collect rather than
re-submit".

**Missing:** neither `corpus_key` nor `jobs_for` appears in any test file.
AC-070/072/073/075 all re-invoke the *identical* command, so only the positive
case is covered. The negative is untested in both directions: a run over a
different directory, or the same directory with a different `--model` /
`--resolution` / `--prompt-template`, must not collect the first run's jobs. A
`jobs_for()` that ignored its argument would pass every batch test while
cross-contaminating results between folders — and results keyed by content hash
would then land in the cache under the wrong model/tier key.

### M2. "Total cost actually incurred" is barely distinguished from the estimate

AC-077 requires the report to state "the total cost **actually incurred**",
explicitly so two `--sample` runs are comparable.

**Missing:** `cost.realised_cost_usd` (line 115) — which takes `endpoint`, i.e.
where the batch halving of the *realised* figure lives — is called by no test.
`test_ac077_report_states_…_realised_cost` hands `build_report` the literal
`total_cost_usd=0.42`; the end-to-end test asserts only `"$" in output`; the
strongest real assertion anywhere is `est_cost_usd > 0` per record
(`test_s11_reporting.py:465`). `FakeTransport` returns `input_tokens=1200,
output_tokens=40` per response and no test reads those back out of a report, so
neither the arithmetic nor the batch discount on the realised total is pinned.

Consequence: a build that printed the pre-run estimate as the realised cost, or
dropped the discount from the realised figure, passes.

### M3. Three of the four supported image formats have no fixture

AC-008 fixes the supported set at JPEG, PNG, GIF, WebP, and the test asserts that
set equality on `SUPPORTED_IMAGE_FORMATS`. But `tests/support/images.py` writes
only PNG (`write_png` / `write_solid_png` / `write_noise_png`) and one BMP, which
exists purely as the *unsupported* case.

**Missing:** no JPEG, GIF, or WebP fixture anywhere. Nothing exercises
`sniff_format` for three of the four formats it must accept, nothing asserts
`ModelRequest.image_media_type` at all, and `normalize_extension`'s non-`.png`
cases (AC-016) are bare string assertions rather than files. A sniffer that
recognised only PNG would silently exclude every JPEG and WebP capture in a real
corpus and pass the suite.

### M4. Cross-run determinism (AC-018) is masked by the cache

AC-018: "Two separate **runs** over the same image bytes with the same model
identifier, resolution tier, and prompt-template version produce byte-identical
proposed filenames."

**Missing:** both AC-018 tests are pure-function determinism over `build_filename`
/ `compose_slug` (one in-process, one across `PYTHONHASHSEED` values). Neither
runs the pipeline. The only test that does compare two runs' proposed names is
`test_ac024_re_running_reproduces_the_same_suffixes`, and its second run shares
the per-test state directory with the first — so the content-hash cache answers
every lookup and the model is never re-consulted. No test compares two runs with
a cold cache, which is the only configuration in which the criterion says
anything. The naming of model id, tier, and prompt version in the criterion text
implies exactly that run-level comparison.

### M5. AC-051 tests retry-after, not exponential backoff

AC-051 names two behaviours: exponential backoff, and honouring `retry-after`.
`rate_limit_at(0)` injects exactly **one** 429, and the test asserts
`elapsed >= retry_after` and `max(attempts.values()) == 2`.

**Missing:** no test injects two or more consecutive 429s for one request, so
nothing observes a growing delay — a fixed `sleep(retry_after)` satisfies the
suite. There is also no criterion bounding the retry count or covering
exhaustion, so a sustained 429 has no defined terminal behaviour (does the file
become `error`? does the run abort?).

### M6. Refusal and schema-failure handling on the batch path

AC-046 (refusal → `abstain`/`model_declined`) and AC-047 (schema failure → retry
once, then `error`) are tested with `--sync` only — all four tests in
`test_s08_abstention.py` covering them pass `--sync`.

**Missing:** no test delivers a refusal or a schema-invalid body as a *batch
result entry*. AC-074 covers only provider-level `errored`/`expired` statuses; a
`SUCCEEDED` entry whose text is prose is a different case reaching different
code. And AC-047's "retried once" has no defined meaning during batch collection
— a retry there is either a new synchronous call or a new job, and neither
`SPEC.md` nor any criterion says which. This is a gap in the contract, not only
in the tests. Batch is the default path for the 4,000-file backlog.

### M7. Nothing bounds OCR concurrency, on either dispatch path

`SPEC.md` Stack row (Concurrency): "a bounded pool is what makes 429 handling and
clean `SIGINT` tractable. **On the batch path the semaphore governs OCR and job
submission only.**"

AC-052 is scoped to "the synchronous dispatch path" and counts only in-flight
*model requests* via `FakeTransport.max_in_flight`. `FakeOcr` records calls but
never concurrency.

**Missing:** no criterion and no test bounds simultaneous OCR operations. On the
batch path the semaphore's only stated job is governing OCR and submission, and
neither is asserted there. Unbounded OCR fan-out is also a plausible mechanism
for the AC-009 failure mode in H4.

### M8. Four flags are tested through `RunSettings`, never through the CLI

- AC-003 (`--all-images`), AC-004 (`--recursive`), AC-007 (`--materialize`): the
  tests construct `RunSettings(root=…, all_images=True)` and drive
  `discover_candidates` directly. These flag *strings* do appear in
  `test_ac048_…`'s parametrize list, but that test asserts only that abstained
  and errored files are not renamed — nothing about the flags' effect.
- AC-044 (`--min-ocr-chars`): the test asserts `DEFAULT_MIN_OCR_CHARS == 12` and
  the `RunSettings` default. Nothing passes `--min-ocr-chars` on the command
  line, so nothing checks that a non-default threshold changes which files
  abstain.

Consequence: a wrong option string or a dropped keyword in `cli.run`'s
`RunSettings(...)` construction fails no test for these four. Compare `--model`,
`--resolution`, `--seed`, `--concurrency`, `--prompt-template`, `--ollama-host`
and `--poll-interval`, which *are* asserted to reach their effect through the
CLI. The gap is inconsistent rather than deliberate. (`--ollama-model`, named in
`SPEC.md`'s Local backend row, has no criterion and no test at all.)

### M9. Concurrent cache access has no criterion

`SPEC.md` Stack row (Cache): "SQLite (WAL mode), keyed by content hash … Random
access lookup by hash across runs, **safe under a concurrent worker pool**."

**Missing:** AC-043 covers key composition and a single-threaded round trip. No
criterion pins WAL mode, and nothing asserts that N workers writing suggestions
concurrently at the default `--concurrency 8` neither lose a row nor raise
`database is locked`. This is the class of defect that appears only at backlog
scale, which is the only scale this tool is for.

### M10. No criterion covers a candidate whose extension is not `[a-z0-9]+`

Format eligibility is decided by content sniffing (AC-008), not by extension, so
a genuine PNG named `capture.pnĝ`, `capture.PNG ` (trailing space), or with no
extension at all is a valid candidate. `normalize_extension` lowercases whatever
suffix is present and `build_filename` carries it through as a fixed tail —
extensions are never transliterated, unlike slugs (AC-017).

**Missing:** no fixture and no criterion. AC-010's regex requires `\.[a-z0-9]+$`,
so such a file would produce a name violating AC-010 — and AC-010's test only
feeds `build_filename` extensions that are already clean. The contract does not
say whether these files are skipped, re-extensioned from the sniffed format, or
renamed anyway.

---

## Low

### L1. AC-013's byte limit is only ever exercised with ASCII

AC-013 says "at most 255 **bytes** when encoded as UTF-8". Every input to
`fit_to_byte_limit` and `build_filename` in `test_s03_naming.py` is ASCII, so
`len(name)` and `len(name.encode())` are indistinguishable to the suite. AC-017
makes slugs ASCII, which closes most of the gap — but the extension is never
transliterated (M10), so the byte-wise branch is reachable and unexercised.

### L2. AC-057 is a smoke test, not "the identical pipeline test suite"

AC-057's wording — "the identical pipeline test suite passes against both when
only the backend selection flag is changed" — is satisfied by one test
parametrized over `((), ("--local",))` asserting exit 0, plan coverage, and that
some record is a `RENAME`, plus a check that `--local` never reaches the Batch
API. Journal, collisions, abstention, and reporting all run cloud-only. Either
the criterion is overstated or the parametrization is.

### L3. Three "reports X" clauses are satisfied by a substring

- AC-053 requires a *partial summary* on `SIGINT`; the test asserts
  `"interrupt" in output.lower()`. No count or per-action figure is required.
- AC-072 requires reporting an in-flight job's *progress*; the test asserts the
  job id appears and that a poll happened. Printing the id and nothing about
  state passes.
- AC-063's sample determinism is shown by calling `build_report(…, seed=3)`
  twice. `pipeline._report` does pass `seed=settings.seed`, but no test asserts
  it, so the report's 20 pairs could be reseeded per invocation and nothing
  would notice.

### L4. AC-035's ordering assertion is not tied to the journal fd

AC-035 requires the journal record for a rename to be appended and fsynced
before that file's `rename(2)`. The test records *global* `os.fsync` calls and
asserts each `rename` event is immediately preceded by a `fsync` event — it does
not check that the synced descriptor was the journal's, nor that the record
flushed was the one for the file being renamed. AC-036 does establish the
record↔file pairing, for one file, via the injected kill. Low because the pair of
tests together is convincing; noted because neither one alone says what AC-035
says.

### L5. AC-055's assertion can pass vacuously

`endpoints <= {("127.0.0.1", port)}` is a subset check with no accompanying
assertion that `attempted` is non-empty. Any failure that stops the run before
networking — a preflight error, an exception in the stubbed OCR — yields an
empty set and a green test. The criterion is about which sockets open; the test
cannot distinguish "only the Ollama host" from "nothing at all".

### L6. AC-069's payload-size limit is not shown to be wired into submission

The criterion says chunking is "verified by submitting a corpus that exceeds both
limits". Three unit tests cover `chunk_batch_requests` against the count limit,
the payload limit, and both. The one end-to-end wiring test monkeypatches
`MAX_BATCH_REQUESTS` only; no run exceeds the payload limit. `pipeline` (line
508) does pass both constants, so the risk is narrow, but the criterion's "both"
is half-covered.

### L7. The default state directory is never asserted

`SPEC.md` places cache, journal, plans, and job records in
`~/.local/state/shotname/`. The autouse `state_dir` fixture sets
`SHOTNAME_STATE_DIR` for every test, so `settings.state_dir_path`'s default
branch is never taken and no criterion names the location. Blast radius is
limited — AC-028's `snapshot_dir` comparison would catch state written into the
target directory — but a default of `cwd` or `~/Library` would pass.

### L8. "Undo must work even if the cache DB is corrupt or deleted" has no criterion

`SPEC.md` Stack row (Journal) gives this as the reason the journal is JSONL
rather than another SQLite table: "undo must work even if the cache DB is corrupt
or deleted, and a plain-text journal is greppable and auditable by hand". No
criterion states it, and no test deletes or truncates the cache before running
`undo`. Two mechanisms were chosen for independence; nothing verifies they are
independent.

### L9. Exclusion reasons are asserted on the `Candidate`, not in `plan.jsonl`

AC-008 requires "a per-file exclusion reason is **recorded in the plan output**",
and AC-006 requires the dataless count in the run report. The AC-008 test asserts
`candidate.excluded is Reason.UNSUPPORTED_FORMAT` on the in-memory object; the
AC-006 summary test calls `format_discovery_summary` directly. AC-029/AC-030
check that plan records carry a `reason` *field* and a legal `action`, but no
test asserts the BMP's or the dataless file's plan record carries the specific
reason value. (Contrast AC-041, which does assert
`record.reason == Reason.ALREADY_RENAMED.value`.) Relatedly, AC-006's "skipped
without being opened or read" is verified only against `probe.sniffed` during
discovery; no later open — hashing, OCR — is tracked.

### L10. AC-058 silently skips when `/usr/bin/xattr` is absent

`@pytest.mark.skipif(not xattr_available())` turns a missing binary into a pass,
so the only check that extended attributes survive the rename can vanish
unnoticed. Low, because the tool is macOS-only and the binary ships with the OS.
Related: `fsutil.birthtime` falls back to `st_mtime` when `st_birthtime` is
absent, which would quietly reduce AC-059's birthtime assertion to a duplicate of
its mtime assertion off-macOS.

### L11. BLAKE2b is never pinned as the hash

`SPEC.md` Stack row (Hashing) specifies "BLAKE2b over file contents; first 6 hex
chars as the disambiguation suffix". AC-023 pins the six-char truncation;
no criterion or test pins the algorithm. Changing it would silently invalidate
every cached entry and every journal `content_hash` written by a prior version —
AC-039's undo hash check would then refuse files it should restore.

---

## Stubbed by construction, not a finding

Worth stating so the coverage is not misread. `OverrideProbe` forces
`is_screen_capture` and `SF_DATALESS` per path, and `FakeOcr` replaces
`VisionOcr` everywhere. So AC-002/AC-006/AC-007 assert what the enumerator does
*given* a signal, never that `RealFsProbe` reads `kMDItemIsScreenCapture` or
`stat.st_flags` correctly, and `VisionOcr.recognize` has no test at all. This is
unavoidable for a hermetic suite and `tests/conftest.py` explains the choice —
but it does mean the spec's claim that `kMDItemIsScreenCapture` is "the real
signal" rests on unexercised code, and it is the substrate under H2.

---

## Summary

| Severity | Count | Criteria / spec sections most affected |
|---|---|---|
| High | 4 | SPEC downscale claim vs AC-076/077/043; SPEC OCR row vs AC-019–022; AC-034 vs AC-025/026/027/039; AC-009 |
| Medium | 10 | AC-070–075 keying; AC-077; AC-008/016; AC-018; AC-051; AC-046/047; SPEC concurrency row vs AC-052; AC-003/004/007/044; SPEC cache row; AC-008/010/016 |
| Low | 11 | AC-013, AC-057, AC-053/063/072, AC-035, AC-055, AC-069, state dir, SPEC journal row, AC-006/008, AC-058/059, SPEC hashing row |

The two to act on first remain **H1** and **H2**: both are requirements the spec
argues for at length — the downscale tier and the bounding-box ranking — that no
criterion converts into a check, so neither can fail. **H3** and **M1** are next;
they are the two places where a safety property that is well covered on the
primary path (clobber-freedom, submit-once) is uncovered on a secondary path the
user is expected to use routinely. **M4** is the one finding new to this pass:
the determinism criterion is satisfied only by a cache hit.
