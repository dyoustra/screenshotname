# Questions

## Blocking

### Have you run `mdfind` against your screenshots yet — does Spotlight already find them?

macOS 26 indexes Live Text inside plain image files. If your actual goal is "find the screenshot
about Stripe," this may already work for free, and the honest answer is not to build. Run
`mdfind -onlyin ~/Desktop 'kMDItemTextContent == "*stripe*"c'` before answering.

- [ ] A — Spotlight finds them fine; my real problem is *browsing* a folder of 4000 identical names — build the renamer for legibility, not search
- [ ] B — Spotlight finds nothing / is unreliable on my old files — build the renamer, and searchability is the primary quality bar for the naming prompt
- [ ] C — Spotlight works well enough; don't build this — kills the project, costs nothing
- [ ] Other: ______

### Rename in place, or write a renamed copy/link into a new directory?

This determines whether the tool mutates your originals at all, and it changes the undo story
from "reverse the journal" to "delete the output tree."

- [ ] A — Rename in place — what you asked for; fastest, preserves xattrs and Finder tags for free, but 4000 irreversible mutations backed only by the journal
- [ ] B — Hardlink into `~/Screenshots-named/` with new names — originals untouched, zero extra disk, undo is `rm -rf` on the new tree; but you now have two folders and Finder shows both
- [ ] C — Rename in place *and* leave a symlink at the old name — nothing that links to the old filename breaks; but doubles the file count in the folder, which defeats the "make it legible" goal
- [ ] Other: ______

### Are these screenshots linked from anywhere — Obsidian, Markdown docs, git repos, Keynote?

If yes, in-place renaming silently breaks every one of those links and you won't notice for
weeks. This is the failure mode with the longest fuse.

- [ ] A — No, they're a junk drawer on the Desktop; nothing references them — proceed with in-place rename, journal is sufficient
- [ ] B — Yes, some are embedded in notes — I'd want option C above (symlink), or the tool restricted to files with no known referrer
- [ ] C — Yes, and I want the tool to also rewrite the links — significant added scope: a vault/doc scanner and a rewrite pass, currently out of scope
- [ ] Other: ______

### Cloud model, local model, or both in v1?

Screenshots are the most privacy-sensitive files on a machine — passwords in plaintext fields,
2FA codes, DMs, client data. Building both backends behind one interface is maybe a day of extra
work; building one and retrofitting the other is more.

- [ ] A — Both, cloud default — best names out of the box, `--local` available when it matters; you must accept that by default your screen contents go to an API
- [ ] B — Both, local default — safe by default, `--cloud` is an explicit opt-in per run; noticeably worse names unless you opt in every time
- [ ] C — Cloud only — simpler build, better names, no Ollama dependency; no private-data escape hatch
- [ ] D — Local only — nothing leaves the machine ever; expect meaningfully worse names and a slower run on 4000 files
- [ ] Other: ______

## Preference

### Naming convention — is `2026-01-14-stripe-dashboard-mrr-chart.png` right?

Default chosen: date prefix + content slug. The date prefix isn't decoration — it preserves
chronological sort and acts as a uniqueness anchor so that forty independently-generated
`terminal-output` names don't collapse into each other.

- [ ] A — `2026-01-14-stripe-dashboard-mrr-chart.png` *(default)* — sorts chronologically, findable, collision-resistant
- [ ] B — `2026-01-14-154211-stripe-dashboard.png` — keeps the capture time, so uniqueness never needs a hash suffix; longer and noisier
- [ ] C — `stripe-dashboard-mrr-chart.png` — cleanest to read, but loses chronological sort and makes generic-name collapse much worse
- [ ] D — `stripe-dashboard-mrr-chart-2026-01-14.png` — content first for alphabetical grouping by topic; breaks date sort
- [ ] Other: ______

### What should the slug describe — the app, or the subject?

There is no ground truth here. A Slack thread about a Stripe outage is equally honestly named
`slack-thread`, `stripe-outage-discussion`, or `payment-failures-jan14`. Only one matches how
you'll search in six months. Default chosen: subject-first with app as context when it adds
signal.

- [ ] A — Subject-first: proper nouns and specifics, app named only when it disambiguates *(default)* — most searchable, occasionally guesses the wrong subject
- [ ] B — App-first: `slack-stripe-outage-thread` — very predictable and consistent, but 300 files start with `safari-`
- [ ] C — Verbose/descriptive: full-sentence-ish names up to the byte limit — most information, ugly folder
- [ ] Other: ______

### What happens to the ~15-25% of screenshots that are genuinely meaningless?

Blank screens, accidental captures, three near-identical shots one second apart, a six-word error
dialog. Default chosen: leave them completely alone — abstaining is most of the perceived
quality.

- [ ] A — Leave untouched with their original name *(default)* — mixed folder of old and new names, but zero wrong names
- [ ] B — Rename with a generic marker: `2026-01-14-unclear.png` — folder is uniform, but you've destroyed the timestamp-in-name and gained nothing
- [ ] C — Move them to an `unsorted/` subfolder — cleans the main folder; but this tool now moves files, not just names them, which widens the blast radius
- [ ] Other: ______

### Where do the journal, cache, and plan files live?

Default chosen: `~/.local/state/shotname/` — keeps the target directory clean and survives you
moving files around.

- [ ] A — `~/.local/state/shotname/` *(default)* — target dir stays clean; journal is findable in one place across runs on different folders
- [ ] B — A `.shotname/` directory inside the target folder — self-contained and portable with the folder; clutters `~/Desktop` and confuses iCloud sync
- [ ] C — `~/.local/state/` for cache, plan/journal written to CWD — journal is right where you ran it; easy to lose
- [ ] Other: ______

### iCloud-evicted (dataless) files — skip or download?

If Desktop & Documents sync is on, some fraction of the 4000 hold no local data and reading them
triggers a synchronous download. Default chosen: skip and report, so the tool never silently
pulls gigabytes over a hotspot.

- [ ] A — Skip and report the count and total size *(default)* — no surprise downloads; those files never get renamed unless you re-run with a flag
- [ ] B — Materialize them automatically — complete coverage in one run; may pull several GB and will stall the worker pool on network I/O
- [ ] C — Prompt interactively once with the count and size, then proceed either way — best UX, but makes the tool non-scriptable in that path
- [ ] Other: ______
