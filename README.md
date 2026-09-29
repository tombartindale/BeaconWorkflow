# Beacon workflow

Two applications, built from the specs in [spec/](spec/):

- **`bcn`** ([tooling/](tooling/)): an offline Python CLI that turns approved topic markdown, an edited video and its SRT into the delivery package for the partner. Specified in [spec/tooling-spec.md](spec/tooling-spec.md).
- **Beacon UI** ([ui/](ui/)): a local web app over `bcn`. It shows what is done, blocked and next across the whole programme, and runs `bcn` as jobs. Specified in [spec/ui-spec.md](spec/ui-spec.md).

The UI contains no pipeline logic. It calls `bcn` and renders the envelopes it returns.

## Setup (macOS, Apple Silicon)

```sh
brew install ffmpeg            # pinned to 7.1.x; bcn refuses other versions
scripts/setup.sh               # venv, pinned Marp (npm ci), pinned Chrome for Testing, then bcn doctor
scripts/demo.sh /tmp/beacon-demo
tooling/.venv/bin/beacon-ui --root /tmp/beacon-demo   # http://127.0.0.1:8420
```

Setup is the only step that uses the network. Pinned versions live in [tooling/bcn/tools.py](tooling/bcn/tools.py) and [tooling/node/package.json](tooling/node/package.json): Node 22, marp-cli 4.5.1, marp-core 4.4.0, puppeteer-core 24.43.1 (the version marp-cli itself uses), and Chrome for Testing 154.0.8037.57. `bcn doctor` checks all of them.

The demo leaves two things for a person to do, which shows the UI's main loop:
- **T02:** an edit cut across the break between slides 4 and 5, so the boundary could not be placed. Open the topic, watch the draft, and use **set to playhead** in the cue table.
- **T01:** two auto-caption mishearings ("Peek oh" for PICO, "like it" for Likert) wait in **Diagnostics → Mis-transcriptions**.

A sample Mandarin return is in `translation/returned/sample/`. Slide 5 is deliberately overloaded, so a Mandarin render fails with a safe-area violation.

Run the tests with `cd tooling && .venv/bin/python -m pytest`.

## bcn

```
bcn validate|render|cues|subtitles|compose|package|qa|status <path> [--lang en|zh] [--human] [--quiet] [--force] [--theme NAME] [--jobs N]
bcn show|diagnostics|review|intake|translation <path> ...   # used by the UI; see --help
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
| `build/` | Step results, `slides/{en,zh}/`, `deck.{en,zh}.pdf`, `<id>.cues.csv`, `cues-report.json`, `subtitles/`, `draft.mp4` |
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

## Beacon UI

`beacon-ui --root PATH [--port 8420]`. It uses the Python standard library only, with vanilla JS and no build step, so it runs offline.

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
