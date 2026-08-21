# Research: CLI that renames screenshots by their content

**Idea:** A CLI that renames ~4000 macOS screenshots named `Screenshot 2026-01-14 at 3.42.11 PM.png`
into something describing what's actually in them.

**Status:** complete.

**One-paragraph summary:** This exists, repeatedly — six-plus OSS implementations and at least
two shipping commercial products, one of which ([FilesDesk](https://filesdesk.app/), $40
one-time) does almost exactly this. More pointedly, macOS 26 already indexes the text inside
your screenshots for Spotlight, so if the goal is *finding* things, `mdfind` may solve it for
free. The consensus architecture is settled: **Apple Vision OCR on-device first, then feed the
image plus the OCR text to a small vision model**. API cost is a non-issue (~$6–10 for all 4000
files, half that via the Batch API). The hard parts nobody handles well are: generic-name
collapse across a large corpus, knowing when to abstain, undo/resumability at 4000 irreversible
renames, and a pile of macOS-specific landmines (an invisible U+202F in every screenshot
filename, TCC blocking `~/Desktop`, iCloud dataless files that lie about their size).

---

## 1. Prior art (this has been built, several times)

This is a well-trodden idea. At least a dozen implementations exist, ranging from 200-line
scripts to commercial apps. None of them are obviously "the" answer for 4000 files, which is
the interesting gap.

### Open-source, directly on point

| Project | Stack | Gets right | Falls short |
|---|---|---|---|
| [cpbotha/ai-screenshot-namer](https://github.com/cpbotha/ai-screenshot-namer) | Python, macOS Vision OCR + LLaVA (Ollama) or GPT-4o | The key architectural insight: run **macOS-native OCR first** and feed the extracted text *alongside* the image into the VLM. Vision models are mediocre at reading dense UI text; the OCR text carries the signal. Supports local-only operation. | Single-file-at-a-time ergonomics; no batching, no concurrency, no resume. Written for a personal workflow ([author's writeup](https://vxlabs.com/2024/05/25/ai-screenshot-renamer-with-ollama-llava-gpt-4o-and-macos-ocr/)). |
| [jftuga/claude-image-renamer](https://github.com/jftuga/claude-image-renamer) | Claude vision + OCR | Same OCR+vision hybrid. Notably **sanitizes the macOS screenshot filename's special Unicode** — the space in `3.42.11 PM` is a U+202F NARROW NO-BREAK SPACE on recent macOS, which breaks naive globbing. Emits lowercase_underscore names, i.e. optimizes for searchability rather than prose. | API-only (cost at 4000 files matters); script-scale. |
| [ozgrozer/ai-renamer](https://github.com/ozgrozer/ai-renamer) | Node CLI, Ollama / LM Studio | The most "productized" of the OSS options: `npx ai-renamer /images`, case-style flags (kebab/camel/snake), handles video via ffmpeg, auto-selects a model. Zero API cost. | General-purpose, not screenshot-aware — no OCR pre-pass, so it leans on the VLM alone to read UI text, which is where quality drops. |
| [octrow/ollama-rename-files](https://github.com/octrow/ollama-rename-files), [PedroLopes/ai-rename-files](https://github.com/PedroLopes/ai-rename-files) | Python, Ollama | Local, no tokens, no API. | Thin wrappers; little handling of collisions/undo/scale. |
| [rsimai/screen-ai](https://github.com/rsimai/screen-ai) | Ollama vision | CLI + GUI; framed as analysis (OCR, anomaly-finding, Q&A) not just renaming. | Not a bulk renamer. |
| [robertknight/ocrs](https://github.com/robertknight/ocrs) | Rust OCR lib + CLI | Not a renamer — an OCR engine. Relevant because it's explicitly tuned to need far less preprocessing than Tesseract and calls out screenshots as a target. Good cross-platform fallback if macOS Vision isn't available. | OCR only; you supply the naming logic. |

Also on PyPI: [`mac-batch-screenshot-renamer-ai`](https://pypi.org/project/mac-batch-screenshot-renamer-ai) —
same idea packaged for exactly this use case.

**Reading so far:** the *concept* is solved many times over. What nobody in this list handles
well is the thing that actually matters at 4000 files: throughput, cost control, resumability,
collision handling, and being able to undo the whole thing.

### The uncomfortable finding: macOS may already solve your actual problem

If the *reason* you want better filenames is "I can't find the screenshot I'm looking for,"
macOS already indexes the text inside your images and you don't need this tool.

Spotlight has ingested Live Text (OCR) output and Visual Look Up object labels into its indexes
for several macOS releases, and per [Howard Oakley's testing](https://eclecticlight.co/2025/08/13/how-to-search-spotlight-for-live-text-and-objects-in-images/),
on **macOS Tahoe 26 this works for image files outside the Photos library** — i.e. a plain
`~/Desktop/Screenshot ....png`. You are on macOS 26.5, so this applies to you today.

Test it before writing a line of code:

```sh
mdfind -onlyin ~/Desktop 'kMDItemTextContent == "*stripe*"c'
mdls "Screenshot 2026-01-14 at 3.42.11 PM.png" | grep -i -e textcontent -e keyword
```

Caveats that keep this from being a full answer, from the same source:
- Oakley describes the behaviour as **"unreliable and highly variable"** — coverage depends on
  what got indexed on that volume, and re-indexing 4000 old files isn't guaranteed.
- It's search-only. It does nothing for *browsing* in Finder, for a screenshot you want to
  attach to an email, or for the pile-of-mud feeling of 4000 identical-looking filenames.
- It's macOS-only and invisible — you can't audit or correct what it extracted.

**Honest verdict:** if the goal is "find things," try `mdfind` first — it's free and instant.
If the goal is "make the folder legible when I look at it," renaming is the right tool and
Spotlight doesn't help. Worth deciding which one you actually want before building.

---

## 2. What a good implementation would use

### Stage 1 — OCR: Apple Vision, on-device

Nearly every screenshot's meaning is in its text. Extract it locally first; it's free, fast,
private, and it makes stage 2 both cheaper and more accurate.

- **[`ocrmac`](https://github.com/straussmaximilian/ocrmac)** (Python) wraps `VNRecognizeTextRequest`
  via pyobjc. Returns text, per-line confidence, and bounding boxes. Bounding boxes matter more
  than they sound: they let you weight text by position, so the window title or the tab bar at
  the top of a screenshot can outrank a wall of body text.
- Alternatives: **[`macocr`](https://github.com/MatthiasWinkelmann/macocr)** (Swift CLI, 10.15+),
  **[`apple-vision-utils`](https://pypi.org/project/apple-vision-utils)** (CLI over images + PDFs).
- Use `VNRequestTextRecognitionLevel.accurate` — [Apple's docs](https://developer.apple.com/documentation/vision/vnrequesttextrecognitionlevel/accurate)
  note it is slower but more comprehensive. At 4000 images run once, offline, in parallel, the
  extra time is irrelevant and the quality difference on small UI text is not.
- Note: since Sonoma, the newer **Live Text** path reportedly outperforms classic VisionKit OCR
  — worth benchmarking both on a sample of your own screenshots rather than assuming.
- **Cross-platform fallback:** [`ocrs`](https://github.com/robertknight/ocrs) (Rust) — explicitly
  designed to need far less preprocessing than Tesseract and names screenshots as a target case.
  Choose it over Tesseract, which was built for scanned documents and does comparatively badly
  on anti-aliased UI text, dark mode, and low-contrast chrome.

**Why OCR-first rather than sending the pixels straight to a VLM:** this is the single design
decision that separates the good implementations from the mediocre ones, and both
[ai-screenshot-namer](https://github.com/cpbotha/ai-screenshot-namer) and
[claude-image-renamer](https://github.com/jftuga/claude-image-renamer) independently landed on
it. Vision models are downsampled before they see your image (see below), which is exactly the
operation that destroys 11px UI text. OCR happens at native resolution.

### Stage 2 — naming: a small vision model, with the OCR text in the prompt

Send the (downscaled) image *plus* the OCR text and ask for a filename. The image supplies
"this is a Figma canvas" / "this is a terminal" / "this is a bank statement"; the OCR supplies
the proper nouns that make the name searchable.

Concrete numbers from [Anthropic's vision docs](https://platform.claude.com/docs/en/build-with-claude/vision):

- Token cost is `⌈width/28⌉ × ⌈height/28⌉` visual tokens (28×28px patches).
- Two resolution tiers: **standard** caps at 1568px long edge / 1568 visual tokens;
  **high-resolution** (Claude 4.7 and later) caps at 2576px / 4784 tokens. Oversized images are
  downscaled automatically, which caps cost — but also silently shrinks your text.
- A Retina screenshot at 3840×2160 lands at **1560 tokens** on the standard tier.
- At Claude Haiku 4.5's $1/M input tokens, that's roughly **$1.56 per thousand images**.

**So the whole 4000-file job is on the order of $6–10 of input tokens, and half that via the
[Batch API's](https://platform.claude.com/docs/en/build-with-claude/batch-processing) 50%
discount.** Cost is not the constraint here — which is worth knowing up front, because it means
you should optimize for *quality and safety*, not for shaving pennies with a worse model.

Other hard limits from the same doc, all of which you will hit at 4000 files:
- Max **8000×8000 px**, max **10 MB base64** per image, **32 MB** per request.
- Max 100 images/request (200k-context models) or 600 otherwise — **but** past 20 images in one
  request a stricter per-image dimension cap kicks in and rejects anything over ~2000px with an
  `invalid_request_error` mentioning "many-image requests". Batching many screenshots into one
  call is a trap; one image per request (fanned out concurrently) is simpler and safer.
- Formats: JPEG, PNG, GIF, WebP only.
- **Claude does not read image metadata** — EXIF/xattr context must be put in the text prompt.
- **Claude will refuse to name people in images** ([AUP](https://www.anthropic.com/legal/aup)).
  If your screenshots include Zoom grids or LinkedIn profiles, expect refusals on some fraction
  of the batch; your pipeline needs a "model declined" path that isn't a crash.

**Local-only alternative:** Ollama with a vision model (LLaVA, Qwen-VL, Moondream) as
[ai-renamer](https://github.com/ozgrozer/ai-renamer) does. Zero cost, zero data egress — the
right default if your screenshots contain work credentials, private messages, or client data.
Given screenshots are among the most privacy-sensitive files on a machine, offering local as a
first-class mode (not an afterthought) is the correct call. Expect noticeably worse names.

---

## 3. Gotchas — the things that actually bite people

These are ordered by how likely each one is to ruin your afternoon.

### 3.1 The filename contains an invisible Unicode character

`Screenshot 2026-01-14 at 3.42.11 PM.png` — the space before `PM` is **U+202F NARROW NO-BREAK
SPACE** (UTF-8 `e2 80 af`), not a normal space. Since macOS Sonoma this trips up a remarkable
amount of software:

- Glob patterns and regexes written with a normal space **silently fail to match**
  ([discussion](https://core.trac.wordpress.org/ticket/63585)).
- Obsidian couldn't attach screenshots at all because of URL-encoding of this char
  ([bug](https://forum.obsidian.md/t/unable-to-attach-screenshot-images-in-macos-14-0-when-wikilinks-turned-off-urlencode-u-202f-char/68410)).
- The rename tool `detox` had a [bug](https://github.com/dharple/detox/issues/116) where it
  didn't replace it.

**Implication:** match screenshot files with a Unicode-aware pattern (`\s` / `\p{Zs}`, or
normalize first), not `"Screenshot * at *.png"`. And don't trust your own test glob — verify
the byte sequence. This is also why `claude-image-renamer` calls out sanitizing it explicitly.

Also: don't rely on the *filename* to identify screenshots at all. It's localized (German
macOS writes `Bildschirmfoto…`), and users change the prefix via `defaults write com.apple.screencapture name`.
The robust signal is the extended attribute / Spotlight attribute `kMDItemIsScreenCapture`
(and `kMDItemScreenCaptureType`), which Finder Stacks itself uses.

### 3.2 Filesystem naming limits are byte-based, not character-based

Per [Howard Oakley on APFS names](https://eclecticlight.co/2024/03/25/apfs-directories-and-names/):

- The limit is **255 bytes**, not 255 characters. A descriptive name with emoji, accents, or
  CJK hits the wall well before 255 visible characters. If your model emits a Japanese UI
  description, you can blow the limit with ~85 characters.
- **`:` is forbidden in Finder, `/` is forbidden in Terminal** — they're path separators in
  Classic Mac OS and POSIX respectively. APFS itself accepts both. A model asked to name a
  screenshot of a clock will happily return `3:42 PM dashboard`, which becomes a file Finder
  displays wrong. Strip both, always.
- Leading `.` hides the file. Leading `-` makes it look like a CLI flag to every subsequent
  command. Trailing spaces/dots get silently mangled.
- APFS is **case-insensitive but case-preserving** by default: `Stripe-Dashboard.png` and
  `stripe-dashboard.png` collide. Your dedup check must be case-folded, and — because APFS
  normalizes Unicode — should also NFC-normalize before comparing.

### 3.3 Full Disk Access / TCC will block you before you read a single pixel

Since Mojave, `~/Desktop`, `~/Documents`, and `~/Downloads` are TCC-protected. A CLI run from
Terminal gets "Operation not permitted" — **`sudo` does not help.** The parent app (Terminal,
iTerm, VS Code) must be granted Full Disk Access in System Settings → Privacy & Security, and
then [everything it spawns inherits that access](https://lapcatsoftware.com/articles/FullDiskAccess.html).

**Implication for the CLI:** detect `EPERM` on the target directory and print the exact
System Settings path to fix it, rather than emitting a stack trace. This is the #1 first-run
failure for any macOS file tool, and the error message is the whole user experience.

Note the flip side, which is worth being honest about in your README: granting Terminal FDA
grants it to *every* unsandboxed process you run from it, permanently. You're asking the user
to make a broad security concession to rename some PNGs.

### 3.4 iCloud Desktop & Documents sync — files that aren't really there

If "Desktop & Documents Folders" sync is on (very common, and the default nudge during setup),
some fraction of 4000 screenshots will be **dataless**. Per [Oakley](https://eclecticlight.co/2023/10/25/macos-sonoma-has-changed-icloud-drive-radically/)
and [Michael Tsai's roundup](https://mjtsai.com/blog/2023/10/27/icloud-drive-switches-to-dataless-files/),
since Sonoma an evicted file keeps its attributes and *reports its full size* while holding no
data extents. So:

- `os.path.getsize()` lies to you. Size checks won't detect it.
- The correct test is `SF_DATALESS` in `stat.st_flags` (via `stat`/`getattrlist`).
- Reading the file triggers a **synchronous network download**. Fan out 32 workers over 4000
  files and you'll stall on network I/O, or fail entirely offline.
- **`brctl download`/`evict` were removed in Sonoma 14+ with no replacement**
  ([icloud-tools](https://github.com/icanhasjonas/icloud-tools) exists to fill the gap).

**Implication:** check `SF_DATALESS` up front, report how many files need materializing, and
let the user decide — don't silently pull 8 GB down over a hotspot.

### 3.5 Renaming breaks every reference to the file

This is the one that causes real damage and is invisible until weeks later. Those screenshots
are probably linked from somewhere: Obsidian/Bear notes (`![[Screenshot 2026-01-14...png]]`),
Notion uploads, Markdown docs, Keynote decks, Slack drafts, git repos, `README` images. Rename
the file and every one of those links dies silently.

Mitigations, in order of value:
1. **A durable undo log.** Write `old path → new path → content hash` as JSONL *before* each
   rename, and ship `undo` as a first-class subcommand. Not "you can restore from Time Machine."
2. `--dry-run` as the **default**, requiring an explicit `--apply`. At 4000 files, one bad
   prompt tweak is a catastrophe you cannot eyeball.
3. Optionally leave a symlink at the old name, or offer `--copy-to` instead of in-place rename.

### 3.6 Metadata you will destroy if you're careless

Screenshots carry extended attributes worth preserving: `com.apple.metadata:kMDItemIsScreenCapture`,
`kMDItemScreenCaptureType`, Finder tags (`com.apple.metadata:_kMDItemUserTags`), and Finder
comments. A rename via `os.rename` on the same volume preserves xattrs; a read-write-delete
"rename" (which is what naive implementations do when moving into a subfolder on another
volume) does not. Also: don't touch `mtime` — for screenshots, the timestamp *is* real
information and it's what sorts the folder chronologically.

### 3.7 Model-side operational limits

- **Rate limits** are per-organization RPM/ITPM/OTPM by usage tier; 4000 concurrent-ish
  requests will hit them. Handle HTTP 429 with exponential backoff honoring the
  `retry-after` header, and cap concurrency (~8–16) rather than firing everything at once.
- The **[Batch API](https://platform.claude.com/docs/en/build-with-claude/batch-processing)**
  is the better fit for this workload: 50% cheaper, much higher throughput ceiling, and
  asynchronous — which suits a job you kick off and check on later. It also forces you to
  design for resumability, which you need anyway.
- **Refusals**: expect a nonzero rate from screenshots containing identifiable people, and
  from anything the AUP classifier flags. Budget a fallback (OCR-only name, or leave unchanged).
- Sending screenshots to a third-party API means **exfiltrating whatever was on your screen** —
  passwords in plaintext fields, 2FA codes, private DMs, client data under NDA. Worth an
  explicit consent step and a `--local` mode, not a footnote.

---

## 4. Commercial products that already do this

| Product | Model | Gets right | Falls short |
|---|---|---|---|
| **[FilesDesk](https://filesdesk.app/blog/auto-rename-screenshots-mac)** | $8/mo, or **$40 one-time lifetime** with BYO-key / local Ollama; free tier of 15 credits | The closest thing to a finished version of your idea. Watch Folder renames each screenshot within seconds of capture. Their example output — `2026-04-22_stripe-dashboard-mrr-chart.png` — is exactly the right naming convention (date prefix preserves sort order, content slug makes it findable). Cross-platform, macOS 12+. | GUI app, not a CLI — no piping, no scripting, no version control of your naming rules. Watch-folder-first: it's built for *new* screenshots, and a 4000-file backlog is the awkward case. Subscription or lifetime purchase. |
| **[Hazel](https://www.unixtutorial.org/automating-macos-screenshots-with-hazel/)** (~$42) | Rule engine; **v6 added built-in OCR** | Mature, trusted, deeply integrated with macOS. Handles the whole file-lifecycle problem, not just naming. OCR + pattern rules are genuinely powerful for *predictable* documents — [invoices and receipts](https://eshop.macsales.com/blog/97662-how-to-automate-invoice-receipt-screenshots-using-hazel/) are the classic win. | Rules are pattern-matching, not comprehension. Great at "if OCR contains 'Invoice', name it `Invoice-<date>`"; useless for the open-ended "what is this screenshot *about*" question, which is the actual ask. |
| CleanShot X / [Shottr](https://shottr.cc) / [better-shot](https://github.com/KartikLabhshetwar/better-shot) | capture tools | Some include region OCR; better-shot is open-source, local-first, no telemetry. | They name files at capture time by template (app name, window title, date) — not by content. Window title is actually a surprisingly good cheap signal, and free. |
| [Renamer.ai](https://www.alternativeto.net/software/renamer-ai/about/), [Zush](https://zushapp.com/blog/best-ai-file-renamer-tools-2026) | SaaS renamers | Polished; handle mixed file types. | Generic file renamers; not screenshot-aware, cloud-first. |

**Buy-vs-build read:** at $40 one-time, FilesDesk is cheaper than a weekend of your time and
solves the *ongoing* problem well. Build the CLI if you want (a) the one-time 4000-file
backlog handled with full auditability and undo, (b) local-only processing with no vendor,
or (c) something scriptable that composes with the rest of your tooling. Those are real
reasons — but "no good tool exists" is not one of them.

---

## 5. What makes this harder than it looks

The OCR-and-a-model part is a weekend. These are the parts that aren't.

**1. "What's in it" is not well-defined, and the model doesn't know which answer you want.**
A screenshot of a Slack thread about a Stripe outage: is that `slack-thread.png`,
`stripe-outage-discussion.png`, `alice-incident-escalation.png`, or `payment-failures-jan14.png`?
All are correct descriptions. Only one matches how *you* will search for it in six months.
There is no ground truth, and this is why the OSS projects all feel almost-but-not-quite right —
they encode someone else's retrieval instinct. Practically: make the prompt a user-editable
template, and let the user run a 30-file sample, eyeball it, and iterate before committing 4000.

**2. Generic-name collapse.** Ask a model to name 4000 screenshots independently and you will
get `terminal-output.png` forty times, `github-pull-request.png` sixty times, and
`safari-browser-window.png` a hundred times. Each is individually reasonable and collectively
useless — you've replaced 4000 files distinguished by timestamp with 4000 files distinguished
by nothing. This is the real quality problem, and it's structural: per-file naming has no
knowledge of the corpus. Mitigations: keep the date prefix (`2026-01-14-...`) so uniqueness and
sort order survive regardless; detect low-entropy names and re-prompt with "be more specific";
or do a corpus-level pass that clusters near-duplicates first. The
[obsidian-llm-wiki collision bug](https://github.com/green-dalii/obsidian-llm-wiki/issues/155)
is the same failure mode in a different domain — slugify with no collision-awareness, second
write silently clobbers the first. Deterministic disambiguation (short hash suffix) is the
standard fix, and note that on case-insensitive APFS your collision check must be case-folded
([relevant paper on case-sensitivity collisions](https://arxiv.org/pdf/2211.16735)).

**3. A large fraction of screenshots are genuinely meaningless.** Real screenshot folders
contain: accidental captures, three near-identical shots of the same window one second apart,
a cropped error dialog with six words in it, a black screen, a QR code. No naming strategy
rescues these. A good tool recognizes low-information images (short OCR output, low visual
entropy, near-duplicate hash of the previous file) and *declines to rename* rather than
inventing `screen-content.png`. Knowing when to abstain is most of the perceived quality.

**4. Retina downscaling destroys the very text you need.** A 2880×1800 Retina capture is
downscaled to 1456×819 on the standard tier — half-size, and 11px UI text becomes ~5px and
illegible. Anthropic's own docs warn that resizing "might make text less legible" and that
lossy compression "can make text difficult to read." This is precisely why OCR-first is not
optional. If you do send pixels, consider tiling large screenshots rather than downscaling,
or use a high-resolution-tier model (2576px) — at ~3× the visual tokens.

**5. Idempotency and re-runs.** You will run this more than once — the first pass will have a
bad prompt. So: names must be stable for unchanged input (cache OCR + model output keyed by
content hash, so re-runs are nearly free); a second run must not re-describe an already-renamed
file into `stripe-dashboard-chart-dashboard.png`; and the tool must be resumable after a crash
at file 2,700 without redoing 2,700 API calls. A content-hash-keyed cache plus a JSONL journal
gives you resume, undo, idempotency, and an audit trail from one mechanism.

**6. Throughput.** 4000 files × (Vision OCR + one API round-trip) is not something to do
serially — at ~2s each that's over two hours. You need a bounded worker pool, backoff on 429,
progress reporting, and graceful Ctrl-C. Or the Batch API, which trades latency for throughput
and cost and is arguably the right shape for a one-shot backlog job.

**7. Verification is genuinely hard.** How do you know the run went well? You can't review 4000
names. You need cheap proxies: distribution of name lengths, count of duplicate slugs, count of
abstentions, a random sample of 20 with thumbnails. Without this you're shipping a tool whose
output quality is unmeasurable — which, at 4000 irreversible renames, is the actual risk.

---

## 6. Recommendation

Build it, but build the *boring* parts properly — those are what's missing from the prior art,
not the AI part.

The shape that follows from the research:

```
screenshot-rename ~/Desktop --sample 30        # try it on 30 files, print a table, change nothing
screenshot-rename ~/Desktop                    # dry-run by default: full plan to stdout + plan.jsonl
screenshot-rename ~/Desktop --apply            # execute, journal every rename
screenshot-rename undo                         # restore from the journal
```

- **Pipeline:** enumerate (Unicode-safe, `kMDItemIsScreenCapture`, skip `SF_DATALESS`) → hash →
  cache lookup → Apple Vision OCR → abstain-check → VLM with OCR text in prompt → sanitize →
  collision-resolve → journal → rename.
- **Naming convention:** `YYYY-MM-DD-content-slug.png`, lowercase-kebab, ASCII, ≤ ~100 bytes.
  Date prefix keeps chronological sort and guarantees a uniqueness anchor.
- **Non-negotiables:** dry-run default, undo journal, content-hash cache, `--local` mode via
  Ollama, and a first-run TCC check with an actionable message.

**Before writing any of it:** spend ten minutes running `mdfind` against your existing
screenshots (§1). If Spotlight's Live Text index already finds what you need, the honest answer
is that macOS shipped this and you can skip the project.

---

## Sources

- [cpbotha/ai-screenshot-namer](https://github.com/cpbotha/ai-screenshot-namer) · [author's writeup](https://vxlabs.com/2024/05/25/ai-screenshot-renamer-with-ollama-llava-gpt-4o-and-macos-ocr/)
- [jftuga/claude-image-renamer](https://github.com/jftuga/claude-image-renamer)
- [ozgrozer/ai-renamer](https://github.com/ozgrozer/ai-renamer) · [octrow/ollama-rename-files](https://github.com/octrow/ollama-rename-files) · [PedroLopes/ai-rename-files](https://github.com/PedroLopes/ai-rename-files) · [rsimai/screen-ai](https://github.com/rsimai/screen-ai) · [mac-batch-screenshot-renamer-ai](https://pypi.org/project/mac-batch-screenshot-renamer-ai)
- [robertknight/ocrs](https://github.com/robertknight/ocrs) · [straussmaximilian/ocrmac](https://github.com/straussmaximilian/ocrmac) · [macocr](https://github.com/MatthiasWinkelmann/macocr) · [apple-vision-utils](https://pypi.org/project/apple-vision-utils)
- [VNRecognizeTextRequest](https://developer.apple.com/documentation/vision/vnrecognizetextrequest) · [recognition levels](https://developer.apple.com/documentation/vision/vnrequesttextrecognitionlevel/accurate)
- [Anthropic vision docs](https://platform.claude.com/docs/en/build-with-claude/vision) · [batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing) · [AUP](https://www.anthropic.com/legal/aup)
- Eclectic Light Co: [Spotlight & Live Text](https://eclecticlight.co/2025/08/13/how-to-search-spotlight-for-live-text-and-objects-in-images/) · [Spotlight sorcery](https://eclecticlight.co/2025/08/10/last-week-on-my-mac-spotlight-sorcery/) · [APFS names](https://eclecticlight.co/2024/03/25/apfs-directories-and-names/) · [APFS limits](https://eclecticlight.co/2019/08/12/hitting-the-limits-of-apfs-is-both-easy-and-confusing/) · [Sonoma iCloud Drive](https://eclecticlight.co/2023/10/25/macos-sonoma-has-changed-icloud-drive-radically/)
- [Michael Tsai: dataless files](https://mjtsai.com/blog/2023/10/27/icloud-drive-switches-to-dataless-files/) · [icloud-tools](https://github.com/icanhasjonas/icloud-tools)
- U+202F: [WordPress Trac #63585](https://core.trac.wordpress.org/ticket/63585) · [Obsidian bug](https://forum.obsidian.md/t/unable-to-attach-screenshot-images-in-macos-14-0-when-wikilinks-turned-off-urlencode-u-202f-char/68410) · [detox #116](https://github.com/dharple/detox/issues/116) · [U+202F](https://www.compart.com/en/unicode/U+202F)
- TCC/FDA: [Lapcat Software](https://lapcatsoftware.com/articles/FullDiskAccess.html) · [Apple Dev Forums: Rules for Full Disk Access](https://developer.apple.com/forums/thread/107546)
- [FilesDesk](https://filesdesk.app/) · [screenshot auto-rename](https://filesdesk.app/blog/auto-rename-screenshots-mac) · [Hazel screenshot automation](https://www.unixtutorial.org/automating-macos-screenshots-with-hazel/) · [Hazel OCR invoices](https://eshop.macsales.com/blog/97662-how-to-automate-invoice-receipt-screenshots-using-hazel/) · [better-shot](https://github.com/KartikLabhshetwar/better-shot) · [Renamer.ai OSS lineup](https://renamer.ai/open-source-ai-file-renamer)
- Collisions: [obsidian-llm-wiki #155](https://github.com/green-dalii/obsidian-llm-wiki/issues/155) · [case-sensitivity collisions (arXiv)](https://arxiv.org/pdf/2211.16735)

---

*Note: local verification (`mdfind` against your actual screenshots, checking the U+202F byte
sequence in real filenames) was blocked by sandbox permissions in this session. Those two checks
are worth running yourself first — §1 in particular could change the decision to build.*
