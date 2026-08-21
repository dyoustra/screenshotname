# Decisions

## Python 3.12 rather than a native Swift CLI
- Chose: Python 3.12, packaged with `uv`, distributed as a `uvx`-runnable tool
- Considered: Swift CLI calling Vision directly; Node CLI shelling out to a `macocr` binary; Rust with `ocrs`
- Why: the two dependencies that carry the tool — `ocrmac` (pyobjc → `VNRecognizeTextRequest`, with per-line confidence and bounding boxes) and the Anthropic SDK — are both strongest in Python. A Swift build would own the Vision layer beautifully and then have to hand-roll the HTTP, retry, and JSON-schema layers. Everything here is I/O-bound glue, so runtime speed is irrelevant.
- blast_radius: structural

## OCR-first, then image + OCR text to the vision model
- Chose: Apple Vision `.accurate` on-device at native resolution, then send the downscaled image *and* the extracted text to the model
- Considered: pixels straight to the VLM; OCR-only naming with no vision model; tiling large screenshots instead of downscaling
- Why: two independent prior implementations landed on this, and the mechanism is clear — a 2880×1800 Retina capture is downscaled to 1456×819 before the model sees it, turning 11px UI text into ~5px mush. OCR happens before that destruction. The image still earns its place: it supplies "this is a Figma canvas" where OCR supplies the proper nouns.
- blast_radius: structural

## Synchronous API with bounded concurrency, not the Batch API
- Chose: async worker pool, default concurrency 8, exponential backoff honouring `retry-after`
- Considered: Anthropic Batch API (50% cheaper, higher throughput ceiling); fully serial processing
- Why: the entire 4000-file job is $6–10, so a 50% saving is ~$4 and does not justify job submission, polling, and lifecycle machinery. At 8 workers and ~2s per file the run finishes in well under an hour. Critically, the sample-and-iterate loop — the thing that actually determines output quality — needs low latency, and Batch would make it unusable.
- blast_radius: structural

## Claude Haiku 4.5 as the default naming model
- Chose: `claude-haiku-4-5-20251001`, with `--model` to override
- Considered: Sonnet 5 as default; high-resolution tier (2576px / 4784 visual tokens) as default
- Why: ~$1.56 per 1000 images on the standard tier. Since OCR carries the text signal, the model's job is coarse scene classification plus synthesis, which is well within Haiku's range. The upgrade path is one flag, so the cheap default costs nothing if it turns out to be wrong.
- blast_radius: local

## Both cloud and local backends behind one interface, cloud as default
- Chose: backend abstraction with an Anthropic implementation (default) and an Ollama implementation (`--local`)
- Considered: cloud-only; local-only; local as the default
- Why: screenshots routinely contain passwords in plaintext fields, 2FA codes, DMs, and client data under NDA — a local path has to be first-class, not retrofitted. Cloud is the default because it produces materially better names and the tool's whole value is name quality. Flagged as a blocking question because reasonable people invert this default.
- blast_radius: structural

## `YYYY-MM-DD-content-slug.ext`, ASCII lowercase kebab
- Chose: date prefix + content slug, lowercase kebab, ASCII-only, ≤255 bytes
- Considered: content-only slugs; full timestamp prefix; snake_case; preserving Unicode in names
- Why: the date prefix is load-bearing, not cosmetic. Per-file naming has no knowledge of the corpus, so a model will independently emit `terminal-output` forty times; the date prefix preserves chronological sort *and* provides most of the uniqueness that stops that collapse from being catastrophic. ASCII-only sidesteps the byte-vs-character limit trap where ~85 CJK characters blow a 255-byte name.
- blast_radius: structural

## Dry-run is the default; `--apply` is required and cannot be configured away
- Chose: no-flag invocation prints the plan and writes `plan.jsonl`, mutating nothing
- Considered: `--dry-run` opt-in; a config file setting to make apply the default
- Why: 4000 renames is a mistake you cannot eyeball and cannot easily reverse by hand. Deliberately excluded a config override so that no environment ever silently makes destruction the default.
- blast_radius: structural

## JSONL journal for undo, SQLite for the cache — two mechanisms, deliberately
- Chose: append-only JSONL `fsync`ed before every rename; separate SQLite (WAL) cache keyed by content hash
- Considered: everything in SQLite; everything in JSONL; no cache, re-derive each run
- Why: undo must work when the cache is corrupt, deleted, or from an incompatible version, so the journal has no moving parts and is greppable by hand. The cache wants random-access lookup by hash under a concurrent worker pool, which is exactly SQLite's job and exactly JSONL's weakness. Writing and `fsync`ing the journal *before* the `rename(2)` means a crash can leave an unused journal entry (harmless) but never an unrecorded rename (unrecoverable).
- blast_radius: structural

## Cache key = content hash + model id + prompt template version; OCR cached separately
- Chose: two cache namespaces — OCR keyed by content hash alone, model output keyed by hash + model + prompt version
- Considered: a single cache key including everything; caching by path and mtime
- Why: you will iterate on the prompt, and OCR results are prompt-independent and comparatively expensive to redo across 4000 files. Splitting the namespaces makes prompt iteration cost only the API calls. Keying by content hash rather than path is what gives resume, idempotency, and correct behaviour when a file moves, from one mechanism.
- blast_radius: structural

## Abstain rather than invent a name; abstained files are left completely untouched
- Chose: low OCR character count AND low image entropy → `action: abstain`, original name preserved
- Considered: always produce a name; name generic files `unclear-<hash>`; move them to a subfolder
- Why: a real screenshot folder contains blank screens, accidental captures, and near-identical bursts. No naming strategy rescues these, and `screen-content.png` is strictly worse than the original timestamp name — it destroys real information and adds none. Knowing when to abstain is most of the perceived quality.
- blast_radius: local

## Deterministic content-hash suffix for collisions, compared case-folded and NFC-normalized
- Chose: 6-hex-char BLAKE2b prefix appended on collision; comparison via NFC + casefold against both proposed names and existing on-disk files
- Considered: numeric `-2`, `-3` counters; appending the capture time; failing the run on collision
- Why: APFS is case-insensitive-but-case-preserving and Unicode-normalizing, so `Stripe-Dashboard.png` and `stripe-dashboard.png` are the same file — a naive check silently clobbers. Content-hash suffixes are stable across runs where counters are not, which is what makes criterion 13 (byte-identical output on re-run) achievable.
- blast_radius: structural

## Identify screenshots by `kMDItemIsScreenCapture`, not by filename
- Chose: Spotlight/xattr attribute as the primary signal, Unicode-aware filename pattern as fallback, `--all-images` to override
- Considered: globbing `Screenshot * at *.png`
- Why: the filename prefix is localized (`Bildschirmfoto`), user-configurable via `defaults write com.apple.screencapture name`, and contains an invisible U+202F that makes naive globs silently match nothing. The metadata attribute is what Finder Stacks itself uses.
- blast_radius: local

## Skip iCloud-dataless files by default; `--materialize` to opt in
- Chose: detect `SF_DATALESS` in `st_flags`, count and report, do not read
- Considered: materialize silently; detect via file size
- Why: since Sonoma an evicted file reports its full size while holding no data, so size checks are useless and `os.path.getsize()` actively lies. Reading one triggers a synchronous download — fanning 8 workers across a few hundred of them stalls the pool or fails offline. Pulling several GB over someone's hotspot without asking is not a default the tool gets to choose.
- blast_radius: local

## `EPERM` on the target directory is a first-class error with a fix, not a traceback
- Chose: exit code 3, print the exact System Settings → Privacy & Security → Full Disk Access path, no stack trace
- Considered: letting the exception propagate; attempting to detect TCC status pre-emptively
- Why: this is the single most likely first-run failure for any macOS file tool, and `sudo` does not fix it — the parent terminal app needs the grant. At that moment the error message *is* the entire user experience.
- blast_radius: local

## State lives in `~/.local/state/shotname/`, not in the target directory
- Chose: XDG-style state directory for journal, cache, and plan files
- Considered: a `.shotname/` folder inside the target directory
- Why: the target directory is likely `~/Desktop` under iCloud sync — dropping a SQLite WAL database into a synced folder invites corruption and sync churn, and clutters the exact folder the tool exists to make legible. A single state location also means `undo` works without remembering which folder you ran against.
- blast_radius: local

## Structured model output via tool-use schema, validated with Pydantic, one retry
- Chose: force a JSON schema response, validate, retry once on failure, then mark `error`
- Considered: free-text response with regex extraction; retry until success
- Why: turns "the model returned a sentence instead of a slug" into a typed, countable failure rather than a silent bad rename. Capping at one retry bounds the cost of a systematically bad prompt — if the schema is wrong, you want the run to report 4000 errors cheaply, not to pay for 12000 calls discovering it.
- blast_radius: local

## Link rewriting and watch-folder mode are out of scope
- Chose: ship the one-shot backlog renamer only
- Considered: bundling an Obsidian/Markdown link rewriter; a `launchd` watch-folder mode
- Why: the backlog is the stated problem and the case existing tools handle worst; FilesDesk and Hazel already do watch-folder well. Link rewriting is a genuinely separate tool with its own risk surface — flagged as a blocking question because the answer changes whether in-place renaming is safe at all.
- blast_radius: external

## Spotlight already finds these files — build for browsing legibility, not search
- Chose: keep building the renamer, with the quality bar set to "is this folder scannable by eye", not "can I find this file"
- Considered: killing the project (Spotlight suffices); building it with searchability as the primary naming objective
- Why: `mdfind` against the corpus returns hits, so the search problem is already solved for free by Live Text indexing. What remains is that 4000 filenames are visually identical, which no index fixes. This resolves the project's largest open risk — that the tool is redundant — and it retargets the naming prompt: consistency and scannability now outrank keyword density.
- decided_by: human (ANSWERS.md)
- blast_radius: structural

## Rename in place; no copy, hardlink, or shadow directory
- Chose: mutate the originals with `rename(2)`, journal as the only safety net
- Considered: hardlinking into `~/Screenshots-named/`; renaming in place and leaving a symlink at the old name
- Why: xattrs, Finder tags, and `st_birthtime` survive for free, and the target folder ends up with one entry per screenshot. Both alternatives produce two visible entries per file, which is precisely the condition the tool exists to eliminate. The cost accepted here is that 4000 irreversible mutations rest entirely on the JSONL journal — which is why the journal is `fsync`ed before every rename rather than after.
- decided_by: human (ANSWERS.md)
- blast_radius: structural

## Nothing references these screenshots, so in-place renaming breaks no links
- Chose: no link-rewriting pass, no referrer detection, no restriction to unreferenced files
- Considered: symlink-at-old-name mode for referenced files; a vault/doc scanner plus rewrite pass
- Why: the corpus is a Desktop junk drawer with no Obsidian, Markdown, Keynote, or repo references pointing into it. This retires the failure mode with the longest fuse — silently broken embeds discovered weeks later — and keeps link rewriting out of scope as a genuinely separate tool.
- decided_by: human (ANSWERS.md)
- blast_radius: external

## Both backends ship in v1, cloud is the default
- Chose: Anthropic backend by default, Ollama behind `--local`, both behind one interface
- Considered: local as the default with `--cloud` opt-in; cloud only; local only
- Why: confirms the pre-existing decision. Name quality is the entire product, and the cloud model is materially better at it, so paying the privacy cost by default is the right trade for this corpus — with the explicit acceptance that by default screen contents go to an API. `--local` stays first-class rather than a footnote because a subset of these captures will contain material that must not leave the machine.
- decided_by: human (ANSWERS.md)
- blast_radius: structural

## Date-prefixed content slug: `2026-01-14-stripe-dashboard-mrr-chart.png`
- Chose: `YYYY-MM-DD-` prefix plus content slug, no capture time
- Considered: including `HHMMSS` for guaranteed uniqueness; content-only names; content-then-date
- Why: confirms the pre-existing decision. The date alone preserves chronological sort and supplies most of the anti-collision pressure; the seconds field would remove the need for hash suffixes but adds six noisy digits to every one of 4000 names to solve a case the deterministic hash suffix already handles.
- decided_by: human (ANSWERS.md)
- blast_radius: structural

## App-first slugs, not subject-first
- Chose: leading segment is the application (`slack-stripe-outage-thread`), subject follows
- Considered: subject-first with the app named only when it disambiguates (the previous default); verbose descriptive names
- Why: **overrides the previous default.** Follows directly from the search question being settled — because Spotlight handles retrieval, the naming scheme is optimized for browsing a sorted folder, and app-first gives a predictable, groupable left edge where subject-first gives an unpredictable one. The known cost is accepted: several hundred names will begin with `safari-`. Two consequences: an app alias map is required so `Google Chrome` and `Chrome` cannot both appear, and when no app is identifiable the segment is omitted rather than filled with a placeholder.
- decided_by: human (ANSWERS.md)
- blast_radius: local

## Meaningless screenshots keep their original names
- Chose: abstain and leave the file completely untouched
- Considered: a generic `unclear` marker name; moving them to an `unsorted/` subfolder
- Why: confirms the pre-existing decision. Accepts a folder mixed between old and new names in exchange for zero wrong names. Renaming to a marker would destroy the timestamp that is the file's only real information, and the subfolder option would turn a renamer into a mover, widening the blast radius past what the journal cleanly reverses.
- decided_by: human (ANSWERS.md)
- blast_radius: local

## Journal, cache, and plan live in `~/.local/state/shotname/`
- Chose: XDG-style state directory outside the target folder
- Considered: a `.shotname/` directory inside the target folder; plan and journal in CWD
- Why: confirms the pre-existing decision. The target is a synced Desktop, and a SQLite WAL database inside it invites sync churn and corruption. One fixed location also means `undo` needs no memory of which folder a run targeted.
- decided_by: human (ANSWERS.md)
- blast_radius: local

## iCloud-dataless files are skipped and reported; `--materialize` opts in
- Chose: detect `SF_DATALESS`, report count and nominal bytes, skip without reading; `--materialize` processes them instead
- Considered: downloading automatically; prompting interactively once
- Why: confirms the pre-existing decision, and confirms that the escape hatch is a flag rather than a prompt. A flag keeps the tool scriptable and keeps the default free of surprise multi-gigabyte downloads; an interactive prompt would break non-tty invocation for the one path most likely to be re-run unattended.
- decided_by: human (ANSWERS.md)
- blast_radius: local
