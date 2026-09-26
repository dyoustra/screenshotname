# Build plan: `shotname`

11 slices, 80 criteria, 5–9 criteria each. This file explains why the cuts are where they
are, so that someone reading it cold — months later, probably because a slice failed and they
are working out what that failure took down with it — can see the shape of the decision.

## Build order

| Slice | Name | Criteria | Prerequisites |
|---|---|---|---|
| S-01 | Enumeration and candidate identification | 9 | — |
| S-02 | Slug composition and text sanitization | 7 | — |
| S-03 | Filename assembly: date prefix, extension, byte limit, determinism | 6 | S-02 |
| S-04 | Backend interface, cost accounting, and failure modes | 9 | S-01 |
| S-05 | Dry run, plan output, and apply | 9 | S-01, S-03, S-04 |
| S-06 | Collision resolution and metadata-preserving rename | 7 | S-05 |
| S-07 | Journal, undo, cache, and resume | 9 | S-05 |
| S-08 | Abstention | 6 | S-04, S-05 |
| S-09 | Batch dispatch and request construction | 5 | S-04, S-05 |
| S-10 | Batch job lifecycle: submit, record, collect, resume | 6 | S-07, S-09 |
| S-11 | Sampling, run settings, and completion report | 7 | S-04, S-05 |

## Where the cuts are, and why

**The two pure-function slices come first because they need nothing.** S-02 and S-03 are the
only slices with no dependency on the filesystem, the model, or each other's runtime state, and
S-02 has no prerequisite at all. They are re-cut from the spec's `Name generation and
sanitization` (9) plus `Slug composition (app-first)` (4), which would otherwise have been a
13-criterion slab and a 4-criterion runt. The seam is *text → tokens* versus *tokens →
filename*: S-02 owns transliteration (AC-017), stripping `/` and `:` (AC-012), the
leading/trailing character rules (AC-015), and the app-first ordering and alias map
(AC-019 – AC-022); S-03 owns everything that turns a token list into a real filename — the date
prefix (AC-011), extension lowercasing (AC-016), the 255-**byte** fit and its
segment-boundary truncation (AC-013, AC-014), the output regex the whole thing must satisfy
(AC-010), and run-to-run determinism (AC-018). AC-010 lives with assembly rather than with
slugification because it constrains the assembled name, not the slug.

**S-01 is left whole at 9.** Enumeration is a single coherent module — the U+202F-safe pattern,
`kMDItemIsScreenCapture`, recursion and symlink safety, `SF_DATALESS`, format filtering, and the
generator-not-list memory property (AC-009). AC-009 in particular is a property of how the whole
enumerator is written, so splitting the group would split the assertion from its subject.

**S-04 before S-05 because the plan cannot be priced without the backend.** The dry-run slice has
to print a cost estimate before any request goes out (AC-032) and enforce `--max-cost`
(AC-033), and both need the cost model and the backend abstraction that S-04 builds. S-04 also
carries the two flag/environment preflight failures (AC-049 EPERM, AC-050 missing key), the
synchronous-path concurrency and 429 behaviour, `SIGINT`, and the local-backend isolation
(AC-055 – AC-057) — that is, everything about *reaching a model* and *failing to*, which is one
subject.

**S-05 is the hinge.** It builds the dry-run default that cannot be configured away (AC-031),
the `plan.jsonl` schema (AC-029, AC-030), both forms of `--apply` (AC-078 one-pass, AC-034
replay-a-plan), and the rename call site. Five later slices depend on it, which is deliberate:
after S-05 the pipeline runs end to end, and everything downstream is a property *of* that
pipeline rather than a new stage in it. It is also the widest blast radius in the plan — if S-05
fails its gate, S-06 through S-11 all skip. That concentration is real coupling, not an artefact
of the cut: there is no way to test "the inode set is unchanged after `--apply`" without
`--apply`.

**S-06 folds the spec's 2-criterion `Metadata preservation` group into collision handling.** Both
are properties of the rename step rather than of name generation: the hash suffixes and the
case-folded, NFC-normalized existence check (AC-023 – AC-026) decide what name `rename(2)` is
called with, and the xattr/`mtime`/`st_birthtime` criteria (AC-058, AC-059) assert what survives
it. AC-027 — the inode set is identical before and after — sits here as the slice's backstop
assertion, and is the single test that catches any clobbering bug regardless of which rule broke.

**S-07 keeps the journal, undo, the cache, and resume together** because they are one durability
story told from four directions. AC-035 (journal record `fsync`ed *before* `rename(2)`) is the
ordering that makes AC-036 (killed in between) reconcilable and AC-037 – AC-040 (undo, including
hash verification and occupied-name refusal) trustworthy; AC-042 and AC-043 are resume, which is
the same journal plus the content-hash cache read back. Splitting undo from the journal would put
the reader and the writer of the same file in different slices.

**S-08 (abstention) depends on S-05 but nothing else depends on it.** Abstention is a terminal
verdict — it adds an action value and the guarantee that the action never leads to a rename
(AC-045, AC-048) — so it is safe to sequence after the pipeline works and before the batch work
begins. AC-080 is here because it is a negative boundary on the same decision: low model
confidence alone is never a reason to abstain.

**The spec's 11-criterion batch group is split at submit-versus-collect.** S-09 is the part that
is true of a request before it leaves: which endpoint it goes to (AC-065, AC-066), the halved
estimate (AC-067), the `custom_id`-is-the-content-hash rule that also dedupes identical bytes
(AC-068), and chunking to stay under the per-job limits (AC-069). S-10 is the part that is true
of a job after it exists: recording ids and printing the collect command (AC-070), `--wait`
(AC-071), re-invocation reporting progress and submitting nothing (AC-072), re-invocation
ingesting results (AC-073), errored and expired entries (AC-074), and `SIGINT` while polling
leaving the jobs alone (AC-075). The split is worth the extra invocation because S-10 holds the
most expensive bug in the tool: AC-072 failing means an impatient second run pays for the whole
backlog twice. S-10 depends on S-07 as well as S-09 — not for ordering tidiness, but because
AC-073 ingests batch results *into the cache*, which S-07 builds.

**S-11 last, absorbing the spec's 2-criterion `Model and resolution tier` group.** Sampling and
the report are the observation surface: they need actions to count (S-05) and realised cost to
report (S-04). `--model` and `--resolution` (AC-076) join them because AC-077 — the estimate and
the report both state model and tier, and the report states cost actually incurred — is the
criterion that makes two `--sample` runs comparable, which is the entire purpose of those two
flags. Reporting last also means it reports on machinery that already exists rather than being
retrofitted.

## Prerequisites that were deliberately *not* declared

A prerequisite that is not genuinely required only serialises the build and widens the fallout
when a slice fails, so these were left off on purpose:

- **S-02 and S-03 do not require S-01.** Slugification and assembly are pure functions over
  strings and `stat` results; their tests construct inputs directly and never enumerate a
  directory.
- **S-06 does not list S-03 or S-01.** It reaches them transitively through S-05, and listing
  them again would not change what is skipped.
- **S-09 does not list S-07.** Building and submitting batch requests needs the backend and the
  plan, not the journal. Only collection (S-10) needs the cache.
- **S-11 does not list S-06, S-07, or S-08.** The report counts whatever actions the plan
  contains; it does not need collisions, undo, or abstention to be working to be correct about
  the actions it was handed.

## Note on cost

`ESTIMATE.jsonl` puts the remaining build at **$17 – $48**. The recorded ceiling in
`BUDGET.json` is $9.40, and `COST.jsonl` shows the stages through `clarify` have already spent
about $9.39 of it. The build as sliced does not fit that ceiling and is not close to fitting it;
whoever runs it should either raise the ceiling knowingly or stop here. The estimate is not
padded to be safe, and it is not shaved to be palatable.
