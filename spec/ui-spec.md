# UI specification

Companion to `tooling-spec.md`. To be built after the CLI, against the CLI, and never around it.

---

## 1. Purpose

One screen that answers, across eight modules and roughly 640 topics in two languages: what is done, what is blocked, what is next, and what needs a human.

Without it, that state lives in a folder tree and a producer's head. The CLI can already tell you everything; it just can't tell you all of it at once.

**Non-goals.** The UI contains no pipeline logic. It does not parse markdown, does not know what a valid topic file looks like, does not compute progress, and does not decide which step runs next. It calls `bcn` and renders the envelopes. Every time that boundary is crossed, the two implementations start to disagree and the UI becomes the thing that lies to you.

---

## 2. Architecture

A local web application: a Node backend on the build machine that shells out to `bcn`, and a browser front end served from it. It is built so it can be packaged as an Electron app later without rewriting either half. Not a TUI.

- The backend is the only thing that runs `bcn`. It queues jobs, enforces a concurrency limit, and streams progress.
- The front end talks to the backend over HTTP for state and actions, and an SSE stream for progress.
- Single user, bound to localhost, no authentication in version one. Say so in the README rather than half-implementing auth.
- `bcn` stays a Python CLI. The UI's move to Node changes nothing about the pipeline, and the UI still never imports or reimplements any of it.

**If more than one person needs it**, that is a different application: hosted, authenticated, and with a real answer to two people running `package` on the same topic at once. Do not drift into it. Decide it deliberately.

### 2.1 Stack

Off-the-shelf parts throughout. The UI's value is in what it shows, not in its plumbing, so nothing here is hand-rolled where a maintained library already does the job.

| Part | Choice | Why |
| --- | --- | --- |
| Language | TypeScript, both halves | One set of types for the API, `bcn`'s envelopes and job records, shared by the front end and backend so they cannot drift apart silently |
| Front end | Vue 3 with **Quasar** | A full component set (tables, dialogs, tabs, notifications, tooltips, progress, file drop) with its own styling, dark mode and icons. Quasar CLI builds the same project as a browser SPA now and as an Electron app later |
| Styling | Quasar's components and utility classes | No Tailwind. It overlaps with Quasar's utility classes and fights its component styles. The house palette is set once as Quasar brand colours. The only custom CSS is for things no component covers: the module grid cells, the video timeline and the teleprompter |
| Routing and state | Vue Router (hash history), Pinia | Standard with Quasar. Hash URLs keep `#/topic/…` links working |
| Backend | Node 22 LTS, **Fastify** | Small, fast, typed, with a plugin model for static files and SSE |
| Persistence | SQLite through `better-sqlite3` | The same small file and schema as before (section 3). Works under Electron once rebuilt for it |
| Tests | Vitest | For both halves, including API contract tests against a fixture programme root (`example/`) |

### 2.2 Shape of the code

```
ui/
  package.json      npm workspaces: shared, server, app
  shared/           TypeScript types only: API requests and responses, envelopes, jobs, prefs, SSE events
  server/           Fastify backend. Exports createServer({ root, bcn, dataDir, port }) and never starts itself on import
    bin/beacon-ui   CLI entry: parses --root, --port, --bcn, --data-dir, then calls createServer
  app/              Quasar project: src/ is the front end, src-electron/ is added only when Electron is
```

The backend being a library with a thin CLI on top is what makes Electron cheap later. The Electron main process calls the same `createServer()` on a free localhost port and opens a window on it. The front end does not know or care which one started it.

### 2.3 The HTTP API is the contract

The Node backend serves the same routes, request bodies, responses and SSE events as the Python backend it replaces, as listed in [the current server](../ui/beacon_ui/server.py). The front end can then be built against either backend, and contract tests can run the same requests against both during the move and require identical results, apart from timestamps and ids.

The rules that live in the backend move across unchanged:

- Host and Origin checks refuse any request that is not from localhost.
- Every file path from the front end is resolved beneath the root, and a path that escapes it, including through a symlink, is refused.
- Job commands and arguments are checked against an allowlist before anything runs.
- Jobs run `bcn` with `spawn`, never through a shell. Cancel sends SIGINT.
- `bcn` is found in the same order as before: `--bcn`, `$BCN`, `tooling/.venv/bin/bcn`, then `PATH`.
- The tree watcher polls file stats, restricted to the names the pipeline cares about, rather than using `fs.watch`. File events from OneDrive and other cloud folders are unreliable, and a watcher that misses changes is worse than a slow one.

---

## 3. State model

**The data source is one local directory.** The backend is configured with a single programme root containing the module folders, and everything it reads or writes is beneath it. No second root, no browsing outside it, no absolute paths from the front end. A path that resolves outside the root, including through a symlink, is rejected rather than followed.

On startup, check the root exists, holds `programme.toml`, contains at least one module, and is writable. Fail with a clear message rather than presenting an empty dashboard that looks like nothing has been produced yet.

**The filesystem is the truth.** The UI holds no separate record of what is done. State comes from `bcn status --json`, which reads the tree and reports, per topic and per language, what exists, what is stale, what runs next and what is blocking.

- Poll `status` for the whole programme on a timer, and immediately after any job finishes. This is the shallow depth, which stats only.
- Run `status --verify` on a single topic when its page is opened, and across a wider scope only when asked. That is what catches an empty or corrupt file, and it is too slow to poll.
- Also watch the tree and trigger an immediate refresh on change, debounced by a second or two. Files will arrive by sync rather than by anything the UI did, and a dashboard that takes thirty seconds to notice a file someone just dropped in feels broken.
- Cache the result in memory with a short TTL so that navigating between views does not re-scan the tree.
- Never write pipeline state into the UI's own database. A topic that is complete must look complete after the database is deleted.

The only things the UI persists, in a small SQLite file:

- Job history: what was run, when, by whom, exit code, duration, the envelope.
- User preferences: default theme, concurrency, which columns are shown.

Anything that must survive as part of the content, such as acknowledging a diagnostic, is written into the topic folder by the CLI, not into the UI's database.

### 3.1 If the root is a synced folder

Likely, given the SharePoint library, and it introduces three failure modes the UI should handle rather than be confused by.

**Cloud placeholders.** With Files On-Demand, a file can exist in the directory listing while its contents are not on disk. `stat` succeeds, the size may be reported, and reading it either stalls on a download or fails when offline.

The CLI detects this and reports `hydration` per artefact, so the UI renders it rather than working it out. Show it three ways:

- On the module grid, a cloud-only topic is visibly distinct from a complete one. A tree that is 90% not on disk should be obvious on the landing screen, not discovered when a job fails.
- On the topic view, per artefact, so it is clear whether it is the video or the source that is missing locally.
- As a warning, not a fix. On macOS there is no scriptable way to pin a file; "Always Keep on This Device" is a Finder action. The UI can name the affected folder and tell the operator what to do, and should not pretend to a button that would silently trigger hundreds of downloads instead.

Warn at startup if a significant share of the root is cloud-only. A pipeline that processes a placeholder produces convincing rubbish, so this is worth being noisy about.

**The simpler answer is to keep the programme root on local disk** and copy into SharePoint at the points where other people need to see something. That removes this whole class of problem, and the detection above becomes a safety net rather than a routine concern.

**Sync conflict copies.** Sync clients resolve collisions by leaving a second file with a machine name appended. These parse fine and are not the file anyone means. `status` should surface anything matching that pattern as a blocking problem for its topic, not skip it and not process it.

**Partial writes during sync.** A file being written by the sync client can be read half-complete. Before acting on a file, check it has been unmodified for a couple of seconds, and skip it this cycle if not.

None of these are exotic. All three will happen in the first month.

### 3.2 Stage model

Every stage is decided by files on disk, never by anything the UI remembers. Each has an output that either exists and is newer than its inputs, or does not.

**English**

| Stage | Satisfied by |
| --- | --- |
| Planned | Listed in `course-map.md`, no `topic.md` |
| Drafted | `topic.md` |
| Validated | `build/validate.json`, passing, newer than `topic.md` |
| Rendered | Slide PNGs and deck PDF, newer than `topic.md` |
| Recorded | `edit/master.mp4` and `edit/master.srt` |
| Cued | `cues.csv`, newer than both the SRT and `topic.md` |
| Packaged | `out/` with a `manifest.json` that verifies |

**Mandarin**

| Stage | Satisfied by |
| --- | --- |
| Not sent | No export record naming this topic |
| Out for translation | An export record, no returned files |
| Returned | `topic.zh.md` and the Mandarin SRT |
| Parity checked | `build/validate.zh.json`, passing, newer than both sources |
| Rendered | Mandarin slide PNGs, newer than `topic.zh.md` |
| Packaged | Mandarin package present and verifying |

Two further axes sit on top of the stage, and both need to be visible rather than collapsed into it:

- **Stale.** Outputs exist but are older than their inputs. A topic can be at Cued and stale because someone corrected the script afterwards. Stale is not the same as incomplete and should not look the same.
- **Blocked.** An error diagnostic outstanding, an outstanding asset request, or a cloud-only file. A blocked topic needs a person; an incomplete one just needs the next command.

`compose` is deliberately absent. A draft is a checking aid, not a stage, and treating it as one would imply topics are unfinished without it.

---

## 4. Views

### 4.1 Programme

The landing screen. One row per module, each showing a stacked progress bar across pipeline stages and two counts, English and Mandarin. Above it, four numbers: topics complete, topics blocked, jobs running, diagnostics outstanding.

A module row is clickable and nothing else. Resist putting actions here; bulk operations belong one level down where the scope is visible.

### 4.2 Module

A grid: units down, topics across, one cell per topic. Each cell is split, English on one side and Mandarin on the other, each side showing its stage from section 3.2. A topic finished in English and untranslated reads at a glance, and so does a module where translation has silently not started.

Stage is shown by position in the sequence rather than by colour alone, since eight stages is more than colour can carry. Colour is reserved for the two overlays: stale and blocked. A stale cell is marked whatever stage it reached, because a topic that looks done and is built from an old script is the most expensive thing on the board.

Filters for stage, language, stale and blocked. Bulk actions on the current selection: validate, render, cues, compose, package. A bulk action states what it will do and to how many topics before it runs.

### 4.3 Topic

Everything about one topic on one page, in three panes that can be shown side by side or stacked.

**Script preview.** The markdown rendered, grouped slide by slide, with each slide's narration shown beneath its slide content and visually distinct from it. Per-slide word count and running total against the target for the topic's declared minutes, with anything outside the tolerance marked. This is the reading view, and it is what someone uses to check a script before recording.

**Slide preview.** The rendered PNGs as a strip, with a lightbox and keyboard navigation. Where a Mandarin render exists, English and Mandarin sit side by side at the same index, which is the fastest way to see that Chinese text has overflowed a box that English fitted. Overlay the theme's safe area on demand, and mark any slide that `render` flagged for overflow.

**Video player.** The master, or the draft composite where one exists, with:

- Cue markers on the timeline, one per slide, from `cues.csv`.
- The slide image for the current position shown beside the player, so a mistimed cue is obvious rather than inferred.
- Click a marker to seek; step to next and previous cue from the keyboard.
- The SRT loaded as a subtitle track, switchable between English and Mandarin.
- Playback rate control, because checking timings at 2x is most of the work.

This pane is the verification tool for the whole pipeline. If a cue is four seconds late, nothing else in the UI will tell you, and ten seconds here will.

Alongside the panes: every artefact with its modification time and staleness, this topic's diagnostics with codes and locations, and actions to run any step or force a re-run.

### 4.4 Diagnostics

A programme-wide list of everything `validate`, `cues`, `render` and `qa` have reported, grouped by code rather than by topic, because failures cluster and fixing a class of them at once is how the work actually goes.

Each group shows the count, the affected topics, and a bulk re-run once fixed. Filter by level, module, language and code. Suspected mis-transcriptions from `cues` get their own tab: both readings side by side, accept or correct, because that is a proofreading task rather than an engineering one and it has its own rhythm.

### 4.5 Jobs

Running and recent jobs. Per job: what, on what, elapsed, ETA, two progress bars for run and current topic, a live log tail from the NDJSON stream, and cancel.

A job that has not emitted a heartbeat for ten seconds is shown as stalled rather than running.

---

## 5. Jobs

- Every action enqueues a job. Nothing runs synchronously in a request.
- One queue, with a configurable concurrency limit passed through to `--jobs`.
- Jobs are cancellable, and cancelling sends SIGINT so the CLI can clean up after itself rather than being killed.
- Progress comes from the CLI's NDJSON on stderr, relayed to the browser unchanged. The UI does not estimate anything the CLI has not told it.
- The envelope on stdout is stored whole against the job. When something goes wrong three weeks later, the exact output is still there.
- The queue survives a backend restart: jobs that were running are marked interrupted, not silently lost.

---

## 6. Intake

The gap in the current process is moving content out of Claude and into the tree, by hand, roughly a hundred times per module.

Give the UI a paste box. Paste the block Claude produced, and the backend:

1. Parses out the front matter to identify the topic.
2. Refuses if the topic is not in the course map, or if the file exists and differs, offering a diff rather than overwriting.
3. Writes the file to the right path.
4. Runs `validate` immediately and shows the result in place.

That turns each collection from navigate-create-paste-save-check into paste-and-look. Accept a whole unit at once, splitting on topic boundaries, since Claude emits a unit per block.

This is the highest-value feature in the UI after the topic view. It is also the only place the UI writes content, so keep the writing narrow: identify, check, place, validate. No editing.

---

## 7. Translation round trip

Two actions, because this is a batch handover rather than a per-topic one.

**Export.** For a selected scope, produce a folder or zip containing the English SRT and the narration-stripped slide markdown for each topic, plus a manifest listing what is in it. Record the export against those topics so the UI knows they are out for translation.

**Import.** Take the returned files, place them as `topic.zh.md` and the Mandarin SRT, then immediately run `validate --lang zh` and `subtitles --lang zh` across the batch and present the failures as one list. Slide parity and SRT timing mismatches are the two things that will come back wrong, and they need to be visible as a batch so they can go back to the translator in one message rather than eight.

---

## 8. Build order

Version one was built in this order, on a Python standard-library backend and a front end with no framework: read-only status views, then the topic view, jobs, intake, diagnostics, the draft player, and the translation round trip.

It is now being rebuilt on the stack in section 2.1. The Python UI keeps working until the new one matches it, so the order is:

1. **Shared types and contract tests.** Write the types in `ui/shared/` from what the Python backend actually returns, and a Vitest suite that drives its API against `example/`. This pins down behaviour before anything is rewritten.
2. **Node backend.** Port it in the same order the data flows: `bcn` runner, SQLite store, event feed, status cache and tree watcher, job queue, then the routes. It is done when the contract tests pass against both backends.
3. **Quasar front end, read-only.** App shell and navigation, programme, module, topic (without the player), document and settings views.
4. **Actions.** Jobs page, running jobs from the module and topic views, diagnostics with acknowledge, intake.
5. **The heavy views.** Draft player with cues on the timeline, script editor, teleprompter, translation, sync.
6. **Cut-over.** `start.sh` and `setup.sh` switch to the Node UI, and the Python `ui/beacon_ui` package is deleted in the same change.
7. **Electron,** only when it is wanted: add Quasar's Electron mode, start `createServer()` from the main process, and settle the packaging question in section 9.

Steps 1 and 2 are where the risk is. After that the front end can be built one view at a time against a backend that already works.

---

## 9. Open questions

- Whether anyone other than the producer needs access, which decides localhost versus hosted and is much cheaper to answer now than to retrofit.
- Whether editorial staff should have the intake box without the ability to run jobs, which is a simple role split if it is designed in and awkward if it is not.
- Whether the UI should show the SharePoint library at all, or stay purely a view of the local tree. Staying local is simpler and probably right for version one.
- What an Electron build ships. Packaging the UI is straightforward. Packaging what it runs is not: `bcn` needs Python, the pinned Marp toolchain, Chrome, ffmpeg and the fonts, which is several hundred megabytes and differs by platform. The options are an app that expects `scripts/setup.sh` to have been run (simple, and fine for one machine), or one that bundles the toolchain (self-contained, and a much larger job). Decide this before starting step 7, not during it.
