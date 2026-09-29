# Beacon workflow

Two applications, built from the specs in [spec/](spec/):

- **`bcn`** ([tooling/](tooling/)): an offline Python CLI that turns approved topic markdown, an edited video and its SRT into the delivery package for the partner. Specified in [spec/tooling-spec.md](spec/tooling-spec.md).
- **Beacon UI** ([ui/](ui/)): a local web app over `bcn`. It shows what is done, blocked and next across the whole programme, and runs `bcn` as jobs. Specified in [spec/ui-spec.md](spec/ui-spec.md).

The UI contains no pipeline logic. It calls `bcn` and renders the envelopes it returns.

## Setup (macOS, Apple Silicon)

```sh
brew install ffmpeg            # pinned to 7.1.x; bcn refuses other versions
nvm install 22                 # Node 22.22+; Marp is pinned to major 22 and the UI uses the same Node
scripts/setup.sh               # venv, pinned Marp (npm ci), pinned Chrome for Testing, the UI (npm ci, build), then bcn doctor
scripts/demo.sh /tmp/beacon-demo
ui/server/bin/beacon-ui --root /tmp/beacon-demo   # http://127.0.0.1:8420
```

Setup is the only step that uses the network. Pinned versions live in [tooling/bcn/tools.py](tooling/bcn/tools.py) and [tooling/node/package.json](tooling/node/package.json): Node 22, marp-cli 4.5.1, marp-core 4.4.0, puppeteer-core 24.43.1 (the version marp-cli itself uses), and Chrome for Testing 154.0.8037.57. `bcn doctor` checks all of them.

The demo leaves two things for a person to do, which shows the UI's main loop:
- **T02:** an edit cut across the break between slides 4 and 5, so the boundary could not be placed. Open the topic, watch the draft, and use **set to playhead** in the cue table.
- **T01:** two auto-caption mishearings ("Peek oh" for PICO, "like it" for Likert) wait in **Diagnostics → Mis-transcriptions**.

A sample Mandarin return is in `translation/returned/sample/`. Slide 5 is deliberately overloaded, so a Mandarin render fails with a safe-area violation.

Run the tests with `cd tooling && .venv/bin/python -m pytest` (bcn) and `cd ui && npm test` (UI backend).

## bcn

```
bcn validate|render|script|bumpers|cues|subtitles|compose|package|qa|status <path> [--lang en|zh] [--human] [--quiet] [--force] [--theme NAME] [--jobs N]
bcn show|diagnostics|review|intake|translation <path> ...   # used by the UI; see --help
bcn qti <unit|module|activity.md> [--force]                  # unit quizzes as QTI 2.1 packages for an LMS
bcn schema <tool> | bcn codes | bcn doctor
```

The CLI contract follows the spec:
- stdout carries exactly one JSON envelope.
- stderr carries NDJSON progress, with a heartbeat at least every 2 s.
- Each step writes its result to `build/<step>.json`.
- Outputs are written atomically, and a re-run skips any topic whose outputs are current.
- SIGINT stops cleanly.
- `status` never writes anything.

Exit codes are 0 OK, 1 validation, 2 usage, 3 missing input, 4 tool failure, and **5 cancelled**. The spec lists no code for cancellation, so 5 is an addition.

### Additions the spec implies but does not name

| Command | Why it exists |
| --- | --- |
| `show` | The topic page needs the parsed script (slides, narration, word counts), cues and media paths. The UI must not parse markdown, so bcn does it. |
| `diagnostics` | Collects every outstanding diagnostic from current step results, for the Diagnostics view. |
| `review` | Records accept/correct decisions on suspected mis-transcriptions in the topic's `review.json`. |
| `cues --set N=HH:MM:SS.mmm` | Places a boundary by hand when `cues` cannot. It is recorded in `review.json`. |
| `intake --from FILE` | The UI's paste box: identify, check against the course map, place, validate. It never overwrites a file that differs; it returns a diff instead. |
| `translation --export / --import` | The batch round trip. Export is all or nothing. Import runs `validate --lang zh` and `subtitles --lang zh` on the whole batch and lists everything to send back to the translator. |
| `doctor`, `codes` | Tool check and the full list of diagnostic codes. |

### Files in a topic folder

| Path | What |
| --- | --- |
| `topic.md`, `topic.zh.md`, `assets/` | Sources |
| `edit/master.mp4`, `edit/master.srt`, `edit/master.zh.srt` | From the editor; the Mandarin SRT comes from the translator |
| `review.json` | Human decisions: hand-placed cues and mis-transcription rulings. It is content, so it lives outside `build/`. |
| `translation.json` | Record of exports, which drives the Mandarin "out for translation" stage |
| `build/` | Step results, `slides/{en,zh}/`, `deck.{en,zh}.pdf`, `<id>.cues.csv`, `cues-report.json`, `subtitles/`, `bumpers/`, `draft.mp4` |
| `out/` | The delivery package, with `manifest.json` (and `manifest.zh.json`) |

**Freshness is judged by modification time.** Copy trees with `cp -Rp` or `rsync -a`. A plain `cp -R` makes everything look stale.

### Document formats (not defined by the spec, so defined here)

`course-map.md` has one `## Learning outcomes` list, then one `## Uxx Title` section per unit, each holding a table:

```markdown
## Learning outcomes

- **LO1** Formulate a focused, answerable research question.

## U01 Asking questions

| Topic | Title | Minutes | Outcomes |
| --- | --- | --- | --- |
| T01 | Turning a topic into a question | 12 | LO1 |
```

`assets.md` is a table with `Asset`, `Topic` and `Status` columns. Any status other than delivered, done, received, complete or closed counts as outstanding and blocks the listed topics.

`activity.md` and `assignment-*.md` are checked for their required headings and for `LOn` references the course map can resolve. The required headings are configurable in `programme.toml` under `[documents]`.

### Editing scripts

**Edit** on a topic's Script pane, or **edit line N** next to any script problem, opens the script in an editor. Problems are checked as you type and listed beside it; click one to jump to its line. ⌘S saves. Saving goes through `bcn edit`, which:

- refuses to overwrite if the file changed on disk after the editor opened it, e.g. through a OneDrive pull; you can then overwrite deliberately or reload
- keeps the replaced version in the topic's `.history/` folder (the last 30), which is not synced
- validates straight away, so the stage and problems update

Unsaved edits survive leaving the page in the same browser tab.

### Recording script

`bcn script <topic|unit|module>`, or **Recording script** on a topic page, writes the narration alone for the presenter:

- `build/script.en.pdf`: large print, one block per slide with a divider, page numbers.
- `build/script.en.html` and `build/script.en.txt`: the same words for a teleprompter.

It needs only `topic.md`, not a passing validate.

**Teleprompter.** The **Teleprompter** button on a topic page opens the narration full screen in a new tab. It runs in the browser, so nothing needs installing, and it always shows the current script. Space plays and pauses, with a 3-2-1 countdown from the top. ↑/↓ change the speed (5 is about 145 words a minute). +/− change the text size. ←/→ or PageUp/PageDown jump between slides, which is what most presentation clickers send. M mirrors the text for a beam-splitter rig, F goes full screen, and Esc leaves. The text scrolls past a reading line a third of the way down, and settings are remembered in that browser.

For a separate teleprompter app we suggest [QPrompt](https://qprompt.app/), which is free and open source (GPLv3) with a signed macOS build: open or paste the `.html` file into it. The slide headings show where each slide starts.

### Intro and outro

`bcn bumpers <topic|unit|module> [--lang zh]`, **Intro/outro** on a topic page, or **bumpers** in a module's bulk actions, makes each topic's title card and turns it into two videos:

- `build/bumpers/<id>.card.<lang>.png`: the topic title from that language's front matter, under the logo, at the theme's slide resolution. A long title wraps and shrinks to fit.
- `build/bumpers/<id>.intro.<lang>.mp4` and `<id>.outro.<lang>.mp4`: the cards, with a silent stereo track. The intro starts on its first frame and fades out to black; the outro fades in from black and out again. Both are at the size and frame rate in `[delivery]`.

The look is part of the theme, under `[bumper]` in `theme.toml`: logo file, colours, durations and fade. The logo sits in the theme directory. Until one is set, every run warns `BUMPER_NO_LOGO`.

Once a topic has bumpers, `compose` wraps the draft in them in place of the programme-wide `[bumpers]` files, and `package` delivers them as separate files beside the master. Neither the master nor the cue sheet is changed. If the title changes, the bumpers go stale: compose skips them, and package refuses to run until `bcn bumpers` is run again.

### Quizzes for the LMS

`bcn qti <unit|module|programme>` exports every unit quiz as a QTI 2.1 package: `<module>/build/qti/<module>-<unit>-quiz.zip`, ready for the LMS's QTI import. A quiz is a unit's `activity.md` with `type: quiz` in its front matter, written the way the content producer writes it:

```markdown
## Question 1

The question, over as many lines or paragraphs as it needs.

- (a) An option → Feedback shown when this option is chosen.
- (b) The right answer ✔ → Correct. Why it is right.
```

- **Options:** ✔ (or ✓) marks a correct option, and → (or `->`) starts its feedback. One correct option makes a single-choice question; several make a "choose all that apply" question, scored all or nothing.
- **In the package:** options stay in the order written, because feedback may refer to them by letter. Each question scores 1 point. Bold and italic come through.
- **Errors block the export:** a question with no correct answer or fewer than two options gets no package, and an older package for that unit is removed. Missing feedback is only a warning.
- **`bcn validate`** reports the same problems, so they show in Diagnostics before anyone exports.

The packages validate against the IMS QTI 2.1 and Content Packaging schemas. Try a first import into the target LMS before relying on it, since each LMS handles feedback slightly differently.

### Overriding a check

Editorial checks (dates, forbidden words, deictic phrases, word counts, slide count, title length) can be overridden two ways. Structural checks (narration blocks, front matter, assets, Mandarin parity) cannot.

- **One finding at a time:** **Acknowledge** on the topic or Diagnostics page, or `bcn ack <topic> --fingerprint FP --note "why"`. The decision is stored in the topic's `review.json` with who, when and why, and the finding becomes info, so validation passes. It is tied to the finding's text, so if the script changes and the text goes, it lapses and anything new is flagged again. **Undo**, or `--clear`, withdraws it.
- **For the whole programme:** set a check's level in `programme.toml`. Warnings never block.

  ```toml
  [validate]
  date_weekdays = false        # default: "Friday" is not treated as a date

  [validate.severity]
  MD_DATE = "warn"             # "error", "warn", "info" or "off"
  ```

### Themes

A theme is `themes/<name>/` (under the programme root, or the bundled [tooling/themes/](tooling/themes/)). It holds a Marp CSS file plus `theme.toml`, which declares:
- the slide resolution
- the subtitle safe area
- a font stack per language
- font files

The default theme bundles Noto Sans SC (SIL OFL), so Mandarin slides and burned-in subtitles never depend on the fonts installed on the build machine. The renderer injects the safe area and fonts into the CSS as variables. The CSS and the overflow checker therefore can't disagree.

Overflow is measured, not guessed. A Node script renders the deck with the same pinned marp-core and Chrome and checks every block element against the content box and the safe area. It names the slide and the element. Overflow is a warning in English and an error in Mandarin.

### Cues

`cues` aligns normalised script tokens to SRT tokens: lowercased, punctuation stripped, contractions expanded, numerals spelled out. Each slide starts at the start of the cue holding the first surviving word after its break.

Confidence per boundary drops for:
- a poorly matched neighbourhood around the break
- words cut at the start of the slide
- a cut spanning the break (heavy penalty)
- a break that falls mid-cue

Divergences are classified as cut, mis-transcription (short and phonetically close), paraphrase, or insertion. Nearby fragments are merged, so a reworded sentence counts as one paraphrase.

The defaults are strict: `min_confidence = 0.8`, `max_divergence = 0.10`. A low-confidence boundary still gets a best-guess `cues.csv`, so the draft can be composed and watched, but the step fails until a person resolves it.

## Starting it for real

```sh
./start.sh           # checks tools, shows what is waiting in OneDrive, starts the UI and opens it
./start.sh --pull    # the same, pulling from OneDrive first
```

The local working copy is `working_area/` in this folder. It is created and filled from the live OneDrive folder on the first run. `BEACON_REMOTE`, `BEACON_ROOT` and `BEACON_PORT` override the defaults.

## Syncing with OneDrive

The pipeline runs on a **local working copy**. The shared OneDrive folder is where content arrives and where other people look, and `bcn sync` moves files between the two on request, never automatically. The UI's **Sync** page does the same thing with buttons.

```sh
# First time: create a local root that points at the shared folder, and pull into it.
bcn sync working_area --pull --init --remote "~/Library/CloudStorage/OneDrive-…/BEACON Content Production - General/Module Development"

bcn sync working_area --pull --dry-run     # what would come in (never downloads)
bcn sync working_area --pull
bcn sync working_area --push               # local script edits, review decisions, finished packages
```

- **Names:** the team's flat names (`KV7016/KV7016-U01-T01.md`) map to the pipeline layout (`KV7016/U01/T01/topic.md`). The nested layout is recognised too. Files that map to nothing, such as the Word module specs, are listed and left alone. The full mapping is at the top of [tooling/bcn/sync.py](tooling/bcn/sync.py).
- **What goes where:** pull brings scripts, Mandarin scripts, course maps, activities, asset requests, assignments, reading lists, assets, review decisions, and the editor's `<id>.mp4` / `<id>.srt` / `<id>.zh.srt`. Push sends the same text files back, never the media, plus finished packages into `<module>/delivery/<id>/`.
- **Conflicts:** `sync-state.json` in the local root records each file as it was at the last sync. A file changed on only one side is copied. A file changed on both sides is a conflict and nothing is overwritten until you choose (`--prefer remote|local --only <path>`, or the buttons on the Sync page).
- **Deletions never sync.** A file on one side only is reported, not removed.
- **Pulled files get the current time** as their modification time, so anything built from an older version shows as stale.
- **Downloads:** only a real pull downloads cloud-only files, and only the ones it needs. A dry run never does.

## Beacon UI

`ui/server/bin/beacon-ui --root PATH [--port 8420]`. A Node backend (TypeScript, Fastify) and a Vue 3 + Quasar front end, in one npm workspace under [ui/](ui/): `shared/` holds the types (bcn's envelopes are generated from `bcn schema` with `npm run gen:envelopes`), `server/` the backend, `app/` the front end. Setup builds it; after that it runs offline.

- **Developing:** run the backend (`./start.sh`, or `npm run dev:server -- --root PATH`), then `npm run dev:app` in `ui/` for the front end with hot reload; it passes `/api` and `/files` through to the backend on port 8420.
- **Checking:** `npm test` runs the backend's tests. The API contract tests in `ui/server/test/contract/` hold the backend to fixed answers for every route, recorded against `example/`. `npm run typecheck` checks both halves.
- **Electron** is not set up yet. The backend is a library (`createServer()` in `ui/server/src/index.ts`) so a desktop build can start it the same way; see the spec, §8 and §9.

**Single user, bound to 127.0.0.1, no authentication.** Requests with a foreign Host or Origin header are refused, and file access is confined to the programme root, symlinks resolved. If more than one person needs it, that is a different, hosted application: decide that deliberately.

- The UI's own SQLite file (job history and preferences only) lives in `~/Library/Application Support/BeaconUI/`, never in the programme root. Deleting it loses no pipeline state.
- Status comes from `bcn status`. It is polled, refreshed after every job, and refreshed when the tree changes (after 2 s without further change, so half-synced files are not read).
- Jobs run `bcn` as subprocesses: up to two at once, never two on the same topic. Progress is bcn's NDJSON relayed as-is over SSE. Cancel sends SIGINT. Jobs that were running when the backend stopped are marked interrupted.
- To run it permanently, see [scripts/com.beacon.ui.plist](scripts/com.beacon.ui.plist) (launchd).

## Open decisions

These are placeholders in config until someone confirms them:

- **Partner delivery spec:** `[delivery]` in `programme.toml`. Transcoding is off; when on, both checksums are recorded.
- **Composite layout and subtitle safe area:** `[compose]` in `programme.toml`, and `safe_area.bottom` (216 px) in `theme.toml`.
- **Subtitle format:** `[subtitles] format = "srt"`. VTT can be switched on.
- **Cue thresholds:** tune against a real edited topic. Everything here was tested on synthetic media.
- **Word-level timings from the editor:** not used. Cue-level resolution is what the spec asks for.
- **`SF_DATALESS` hydration detection:** implemented but unverified. Check it against a real cloud-only OneDrive file on the target machine, as the spec requires.
- **UI questions (spec §9):** who else needs access, and whether editors get intake without jobs. Both are still undecided; the UI is single-user as built.
