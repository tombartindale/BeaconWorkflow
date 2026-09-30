# Tooling specification

For handing to Claude Code. Describes what to build, not how. Written to be implementable without reference to the rest of the project documents.

---

## 1. Scope

A single offline Python CLI that turns approved topic markdown plus an edited video and its subtitle file into the delivery package for the partner's production team.

Recording and editing happen outside this pipeline. An external editor removes mistakes and delivers a finished video with a sidecar SRT. The tooling never cuts video, never transcribes audio, does not translate, and does not talk to SharePoint in the first version. Files arrive in a local working tree, synced by hand.

**Principles**

- Every command is idempotent. Running it twice on unchanged input produces identical output and no error.
- Every command operates on a path and writes to a predictable location derived from it. No hidden state, no database, no config that changes behaviour silently.
- Source of truth is the topic markdown file. Nothing downstream may contradict it.
- Fail loudly, with the file path and line number. Never write a partial artefact and exit zero.
- No network at runtime at all.

---

## 2. Working tree

```
KV7015/
  course-map.md
  assets.md
  reading-list.md
  assignment-1.md
  U01/
    activity.md
    T01/
      topic.md            approved source, English
      topic.zh.md         returned from translation, later
      assets/             figures referenced by topic.md
      edit/
        master.mp4        finished video, from the external editor
        master.srt        sidecar subtitles, from the external editor
      build/              generated, safe to delete
      out/                delivery package, generated
```

`build/` and `out/` are always reproducible from the rest. Never hand-edit them.

---

## 3. CLI contract

```
bcn validate <path>              structure and content checks
bcn render <path> [--lang en] [--theme NAME]    markdown to slide images and PDF
bcn cues <path>                  match script to SRT, emit the slide cue sheet
bcn subtitles <path>             convert or normalise the supplied SRT
bcn compose <path> [--lang en] [--theme NAME] [--no-bumpers] [--draft] [--burn-subtitles]
                                                                  the delivered video
bcn status <path>                current state of every topic beneath the path
bcn package <path>               assemble the delivery folder
bcn qa <path>                    run every check, emit a report
```

`<path>` may be a topic directory, a unit directory or a module directory. Given a directory above topic level, the command recurses over every topic beneath it and continues past failures, reporting all of them at the end.

Exit codes: `0` success, `1` validation failure, `2` bad usage, `3` missing input, `4` external tool failure.

Global flags: `--human` to render the result as a table instead of JSON, `--quiet`, `--force` to rebuild rather than skip unchanged work, `--theme NAME` to override theme resolution.

**stdout is always JSON.** Every command emits one envelope, always the same shape, whatever it did and whether or not it succeeded. `--human` formats that same object as a table; it never takes a different path through the code, so the two cannot diverge. There is no TTY detection: behaviour does not change because output happens to be piped.

```json
{
  "tool": "cues",
  "schema": 1,
  "target": "KV7015/U01/T01",
  "ok": false,
  "started": "2026-10-02T09:14:03Z",
  "duration_ms": 412,
  "results": [
    {"topic": "KV7015-U01-T01", "ok": false, "skipped": false}
  ],
  "artifacts": [
    {"path": "KV7015/U01/T01/build/cues-report.json", "kind": "report",
     "bytes": 2841, "sha256": "..."}
  ],
  "diagnostics": [
    {"level": "error", "code": "CUE_LOW_CONFIDENCE", "topic": "KV7015-U01-T01",
     "file": "edit/master.srt", "line": null, "slide": 4,
     "message": "Slide 4 boundary matched at 0.38 confidence.",
     "hint": "The edit may have removed the passage the break sat in. Set the timecode by hand."}
  ]
}
```

Rules for the envelope:

- `schema` is an integer, incremented on breaking change. `bcn schema <tool>` prints the JSON Schema for a command's envelope, so a UI can generate types rather than guess.
- Run against a directory, `results` carries one entry per topic and `ok` is true only if every entry is. A single topic failing never aborts the run.
- **Every diagnostic has a stable `code`.** The UI groups, filters and counts by code; `message` is for humans and may be reworded at any time. Codes are `SCREAMING_SNAKE`, namespaced loosely by area: `MD_*` parsing, `CUE_*` matching, `RENDER_*`, `PKG_*`, `QA_*`. Maintain the list in one module so it can be enumerated.
- `level` is `error`, `warn` or `info`. Warnings never change the exit code.
- Exit codes stay as above and always agree with `ok`.
- Nothing is ever written to stdout except the envelope. Logs, progress and anything conversational go to stderr.
- **Every command also writes its envelope to `build/<step>.json`**, or `build/<step>.zh.json` for `--lang zh`. This is what makes state recoverable from the filesystem alone: whether a topic passed validation is a file, not something a UI has to remember. A step's result file is stale if it is older than that step's inputs, and stale means not done.

No command is ever interactive. Nothing prompts, nothing waits on stdin, nothing requires a terminal. A UI will drive these.

**Progress.** Any command that can take more than a second emits newline-delimited JSON progress events on stderr. A UI subscribes to stderr for progress and parses stdout once, at the end, for the result.

```json
{"event":"progress","step":"compose","topic":"KV7015-U01-T09",
 "item":12,"items":90,"pct":13.3,"topic_pct":47.0,
 "elapsed_ms":184000,"eta_ms":1190000,"message":"encoding"}
```

Requirements, driven by `compose` and `render`, which are the slow ones. A composite encode of a twelve-minute topic is minutes, and a module is ninety of them, so a run can be hours.

- **Two levels of progress, always.** `item`/`items`/`pct` track position across the run; `topic_pct` tracks position within the current topic. A UI needs both, and a single overall percentage that only moves once a topic finishes is useless across a ninety-topic run.
- **Real progress, not a spinner.** For encoding, drive `topic_pct` from ffmpeg's own `-progress` output against the known duration rather than estimating. For rendering, count slides.
- **Heartbeat at least every two seconds**, even when nothing has changed. This is how a UI distinguishes slow work from a hung process, and without it someone will kill a job that was fine.
- **ETA** from observed throughput across completed items, not from a fixed per-item estimate. Omit `eta_ms` rather than guess before there is anything to base it on.
- **Cancellable.** On SIGINT, stop cleanly: terminate any child process, delete partial output, emit a final `{"event":"done","cancelled":true}` and exit non-zero. A long run that cannot be stopped from a UI will be killed from a task manager instead, and that is how corrupt outputs happen.
- **Atomic outputs.** Write to a temporary path in the same directory and rename on success. A half-written `draft.mp4` that looks complete to `status` is worse than no file.
- **Resumable.** Without `--force`, skip any topic whose outputs are newer than its inputs, and report it as `{"skipped": true}` in the result. Restarting an interrupted run should cost only the unfinished work.
- **`--jobs N`** runs topics in parallel, default 1. Progress events carry the topic, so a UI can show several at once. Encoding is the case that needs it.

**`status`** returns, in the same envelope, one result per topic carrying which artefacts exist, their modification times, their hydration state, which step should run next, and what is blocking.

**Two depths.** By default `status` stats only: fast enough to run across the whole programme on every UI refresh, and it will not notice a file that exists but is empty or corrupt. `--verify` additionally opens each artefact and reads enough of it to confirm it is what it claims:

- Any file: non-zero length, and readable.
- `topic.md`: front matter parses and at least one slide break is present.
- `.srt`: the first cue parses and its timecodes are well formed.
- `.mp4`: the first bytes carry an `ftyp` box, then `ffprobe` returns a duration greater than zero.
- `.png`: PNG signature present, and the dimensions in the IHDR chunk match the theme's declared resolution.
- `out/`: every file's SHA-256 matches `manifest.json`.

This is a more reliable test of a file's validity than any attribute check, and on a local tree it is cheap enough to run per topic on demand. It is not the default because reading headers for several hundred videos takes real time, and because on a synced tree reading is what triggers downloads. Report each failure as `FS_EMPTY`, `FS_CORRUPT` or `FS_CHECKSUM_MISMATCH` rather than as a generic error, so the UI can distinguish a missing file from a broken one.

**Hydration.** When the root is a synced folder, a file can be listed without its contents being on disk. Detect this from file attributes and never by opening the file, because opening triggers a download that may take minutes or fail offline.

**On macOS**, which is the target platform, OneDrive uses the File Provider framework and dataless files have no attribute as clean as the Windows one. Two signals, in order:

- `os.stat().st_flags` tested against `SF_DATALESS` (`0x40000000`), which macOS sets on dataless File Provider items. This is not in Python's `stat` module and must be defined as a constant. **Verify it behaves as expected against a real cloud-only file on the target machine before relying on it**, since it is not a documented stable interface.
- Failing that, the heuristic that `st_blocks` is zero while `st_size` is not.

Treat the result as advisory rather than authoritative, and never as a reason to open the file to check.

There is no scriptable equivalent of the Windows pin on macOS. "Always Keep on This Device" is a Finder action, so the tooling can report a cloud-only file but cannot fix one. Any remedy is a message to the operator, not an action.

*(For reference, if this ever runs on Windows: `os.scandir` carries attributes from the directory listing at no cost, and `st_file_attributes` can be tested against `FILE_ATTRIBUTE_OFFLINE`, `RECALL_ON_OPEN` at `0x40000` and `RECALL_ON_DATA_ACCESS` at `0x400000`, with pinning available as an action.)*

**Because there is no pin on macOS, the better answer is to keep the programme root off the synced folder entirely.** Work on local disk, and copy into SharePoint deliberately at the points where other people need to see something. That removes placeholders, sync conflict copies and partial writes in one move, at the cost of an explicit copy step. Recommend this as the default arrangement and treat the detection above as a safety net for when someone points the root at a synced folder anyway.

Report per artefact as `"hydration": "local" | "cloud" | "unknown"`. Any command that would read a cloud-only file fails with `FS_NOT_HYDRATED` before starting work, naming the file, rather than blocking on a download inside a ninety-topic run.

`status` reads the filesystem only, never recomputes, and must be fast enough to call on every UI refresh across all eight modules. It is the only command a UI needs to poll.

---

## 4. Data contracts

### topic.md

```markdown
---
topic_id: KV7015-U01-T01
title: Turning a topic into a question
minutes: 12
lang: en
---

# Slide title

- Bullet
- Bullet

> **Say:** Narration for this slide.

---

## Next slide title

> **Say:** Narration for this slide.
```

Rules the parser enforces:

- Front matter contains exactly `topic_id`, `title`, `minutes`, `lang`. No more, no fewer.
- `topic_id` matches `^[A-Z]{2}\d{4}-U\d{2}-T\d{2}$` and equals the directory path.
- Slides are separated by `---` at column zero. The front matter delimiters are not slide breaks.
- Every slide has exactly one `> **Say:**` block, as the last element on that slide.
- A `> **Say:**` block may span multiple lines, each prefixed `> `.
- Images use relative paths into `assets/` and have non-empty alt text.

### cues.csv

```csv
slide,timecode
1,00:00:00.000
2,00:01:12.480
```

One row per slide, ascending, first row always zero. Row count equals slide count in `topic.md`.

### Delivery package, `out/`

```
KV7015-U01-T01.mp4           compose's output: slides, presenter, bumpers baked in
KV7015-U01-T01-s01.png ... -sNN.png
KV7015-U01-T01.en.srt        compose's sidecar, shifted by body_offset
KV7015-U01-T01.cues.csv      the master's own timing, never shifted
KV7015-U01-T01.md            slide source, narration stripped
KV7015-U01-T01.intro.en.mp4, KV7015-U01-T01.outro.en.mp4   if the topic has bumpers
manifest.json
```

`manifest.json` lists every file with size and SHA-256, plus topic id, slide count, duration,
build timestamp and `body_offset` (the intro's duration, in seconds, applied to shift the
delivered SRT: zero if the topic has no intro). The delivered `.mp4` and `.srt` are
compose's; the cue sheet is the untouched master timing compose worked from. Mandarin's
`manifest.zh.json` describes its own `<id>.zh.mp4` (the same presenter footage, its own
slides, bumpers and subtitles) the same way, and does not repeat `<id>.cues.csv`, which is
shared and delivered once by the English package.

---

## 4a. Language handling

Both languages run through the same commands, but they are not symmetrical, and the differences are where the bugs will be.

**Mandarin inherits its timings; it never computes them.** The audio is English. The Mandarin version reuses the English `cues.csv` and the cue timings of the English SRT unchanged. There is no `cues --lang zh`, and nothing may regenerate timings for Mandarin. Everything below exists to make that inheritance safe.

**Parity is the load-bearing check.** Inheriting timings is only valid if `topic.zh.md` has the same number of slides, in the same order, with breaks in the same positions as `topic.md`. A translator who merges two slides silently invalidates every cue after that point. `validate --lang zh` fails on any mismatch in slide count or break position, and this check runs before anything else touches a Mandarin file.

**The Mandarin source has no narration.** Narration is stripped before the markdown goes for translation, because the spoken text is translated once, in the SRT. So `topic.zh.md` has slide content and no `> **Say:**` blocks at all. The rule that every slide carries exactly one narration block applies to English only; for Mandarin the rule inverts, and a `> **Say:**` block in a `.zh.md` file is an error, because it means someone translated the same text twice.

**English-specific checks do not run on Mandarin.** Word counts at 145 words per minute, the deictic phrase list, and the English date patterns are all meaningless against Chinese text. Make the check set a property of the language, not a set of conditionals scattered through the validator. For Mandarin, substitute: a character-count sanity check per slide, Chinese date patterns, and the forbidden-string list in its translated form if one is supplied.

**Overflow checking matters more, not less.** Chinese needs a larger point size for equivalent legibility and breaks lines without spaces, so text that fits in English will overflow. The overflow and safe-area checks in `render` are advisory for English and blocking for Mandarin.

**The translated SRT must match the English one cue for cue.** When the Mandarin SRT comes back, check that it has the same number of cues with the same in and out times as the English. Different text, identical timings. Any divergence means the translator re-timed it, and the Mandarin video will drift against slides that were never re-cued.

**Every command takes `--lang`, and `status` reports per language.** A topic is not done because its English side is. `status` returns the state of both, so a UI can show that fifty topics are complete in English and none in Mandarin.

---

## 5. The tools

**Run order is not build order.** At runtime the sequence is fixed and the tools must enforce it:

```
validate → render                                before recording
                                                  [ record and edit, outside the pipeline ]
cues → subtitles → compose → package → qa         once the edit and SRT arrive
compose --draft                                   optional, any time after cues, for checking only
render --lang zh → compose --lang zh → package --lang zh   optional, once translation returns
```

`compose` sits between `subtitles` and `package` now: package delivers what compose builds,
so it cannot run without a current, non-draft compose. `compose --draft` is still optional
and available any time after `cues`, for checking cue timing quickly; its output is never
delivered and never satisfies `package`'s prerequisite.

Every timecode in the delivery package is measured from the start of the edited video supplied by the editor. If that file is re-cut, its SRT changes too and the cue sheet must be regenerated from the new pair. The tools enforce this by refusing to run against an SRT and a video with mismatched modification times or durations.

Each command refuses to run if its inputs are missing or older than required, and says which step to run first.

### 5.1 `validate`

Parses `topic.md` and reports every violation with line numbers, rather than stopping at the first.

Checks:

- Everything in the data contract above.
- Narration word count within 15% of `minutes` × 145 words.
- Slide count between 6 and 16.
- No slide's narration under 30 words or over 200.
- No date patterns, no strings from a configurable forbidden list (cohort names, tutor names, "semester", "deadline", "next week").
- No deictic phrases in narration: "here on the left", "as you can see", "this arrow", "on screen now". Pattern list in config, since the presenter is recorded as a headshot with no slides in shot.
- Slide title length under 60 characters.
- Every referenced asset file exists.

`--lang zh` applies the Mandarin rule set described in section 4a, and additionally compares the file against its English counterpart for slide parity.

Also validates `course-map.md`, `activity.md` and `assignment-*.md` against their own lighter rules: required headings present, unit and topic ids well formed, outcome references resolvable against the course map.

### 5.2 `render`

Markdown to a numbered PNG sequence at 1920×1080 plus a single PDF of the deck.

- Strips `> **Say:**` blocks before rendering.
- Themes live in `themes/<name>/` in the repo, each holding its CSS, its fonts, and a `theme.toml` declaring slide resolution, the subtitle safe area, and the font stack per language. Anything a renderer or a checker needs to know about the look is declared there, not hard-coded.
- Theme resolution order: `--theme` flag, then `theme` in the module's `module.toml`, then `theme` in `programme.toml`, then `default`. The resolved theme name and the Marp version go in the manifest, so a package records exactly what produced it.
- Because the theme is plain Marp CSS, the same file drives marp-core in a browser. That is what the staff preview page should load, so a preview and a delivery render cannot disagree about the look.
- One theme is expected to serve the whole programme. Per-module override exists for the case where a module genuinely needs different treatment, not as an invitation.
- Embeds fonts for PDF output. The theme must specify a CJK face so that `--lang zh` renders correctly without a second theme.
- Reserves a configurable safe area at the bottom of the frame. Value is not yet fixed: it depends on the partner's composite layout, so make it a theme variable rather than a constant in code.
- After rendering, checks each PNG for text overflow beyond the content box and for content inside the safe area, and fails if found. Do this by rendering with a detectable marker rather than by image analysis if there is a way to; otherwise a pixel check against the safe-area band is acceptable.
- `--lang zh` renders `topic.zh.md` with the same theme and the theme's CJK font stack. It must produce the same number of slides as the English, and fails if not. Overflow and safe-area violations are errors here rather than warnings.

**Renderer is Marp**, via `@marp-team/marp-cli`, pinned to an exact version. Do not substitute.

Practicalities that will otherwise cost a day each:

- **Marp directives never go in the source.** Our front matter is `topic_id`, `title`, `minutes`, `lang` and nothing else. The renderer strips it, prepends a generated front matter block containing `marp: true`, the theme name and the size directive, and writes that to a temp file which is what Marp actually sees. Tooling concerns stay out of files humans edit.
- **Strip the `> **Say:**` blocks before handing the file to Marp**, or they render as blockquotes on the slides. Marp's own presenter notes are HTML comments, which we deliberately do not use because they are invisible in the SharePoint editor.
- **PNG export** is `--images png`, which emits `name.001.png`, `name.002.png` and so on. Rename to the `-s01.png` convention afterwards, and treat a gap or a count mismatch against the parsed slide count as an error.
- **Resolution** comes from the theme, either through the `size` directive or `section` dimensions in the CSS, with `--image-scale` if a 720p theme needs 1080p output. The theme owns this, not the command line.
- **Marp needs Chromium** for PNG and PDF export, via Puppeteer. Pin the browser too and set `CHROME_PATH` explicitly rather than relying on discovery. The official `marpteam/marp-cli` container is a reasonable way to make this reproducible, but the base image will not have CJK fonts, so they have to be added. Note this against the offline requirement: Chromium must be installed ahead of time, not fetched at run time.
- **Local images and fonts** need `--allow-local-files`. Constrain the working directory rather than passing it blindly.
- **Themes are Marp themes**: a CSS file with the `/* @theme name */` header, styling `section`. Register with `--theme-set` pointed at `themes/<name>/`. The `theme.toml` alongside it holds the values the tooling needs that CSS cannot express, namely safe area, expected resolution and the per-language font stack.

**Overflow detection.** Marp will not tell you that text overflowed; it just clips or spills. Render to HTML as well as PNG, then drive the same pinned browser to measure each slide's content bounding box against the frame and against the safe area declared in `theme.toml`. This is more reliable than inspecting pixels and gives a usable error message naming the slide and the element.

### 5.3 `cues`

The heart of the pipeline, and the only genuinely difficult tool.

Input: `topic.md` and `edit/master.srt`.

First, extract the narration: take the `> **Say:**` blocks from `topic.md` in slide order and concatenate them, recording which slide each token came from. This happens in memory. `--dump-narration` writes it to `build/narration.txt` for inspection when a match goes wrong, and is the only reason the intermediate exists.

**Expect a near-exact match.** The presenter reads the script closely and the editor only removes mistakes and retakes, so the surviving speech should track the script almost word for word. Divergence is the exception, not the rule, and the defaults should reflect that: match strictly, and treat a poor match as a fault to report rather than a condition to absorb.

Match the two as a text-to-text alignment over normalised tokens: lowercase, strip punctuation, expand contractions and numerals consistently on both sides. For each slide marker in the script, find the corresponding position in the SRT and take the start time of the cue containing the first surviving word after that marker.

Timing resolution is cue-level, not word-level, because an SRT has no word timings. That is acceptable: slide breaks sit at sentence boundaries, which is where cue boundaries sit too. Do not attempt to interpolate within a cue.

Outputs:

- `<topic>.cues.csv`, one row per slide.
- `build/cues-report.json`, carrying a match confidence per boundary and a list of divergences between script and SRT.

Failure conditions, each with a message that says what to do:

- A slide marker cannot be located with confidence above a configurable threshold. Usually means the edit removed the passage the break sat in; a human picks the timecode.
- Cue timecodes not strictly ascending, or the final cue ending after the video duration, which means the SRT and the video are not a matching pair.
- More than a configurable proportion of the script absent from or differing from the SRT. Set this tight, because heavy divergence almost always means the wrong SRT, the wrong topic, or a recording that needs redoing rather than an unusual edit.

**The divergence list is not a by-product.** Because the recording is meant to track the script closely, every divergence means something, and the report should say which kind it looks like:

- *Cut*: a contiguous span of script absent from the SRT. Expected where a mistake was removed. Benign, but a cut spanning a slide marker needs a human to place that boundary.
- *Mis-transcription*: a short span where the SRT differs from the script but is phonetically close. Almost always the editor's auto-captions mishearing a name, an acronym or a piece of jargon. The partner translates from this SRT, so an error here reaches Mandarin. Report both readings.
- *Paraphrase*: a span of comparable length with different wording. Means the presenter departed from the approved script, which the producer should know about, since the approved script is what was signed off.

This is the cheapest caption proofread available, because the correct text already exists. Classification does not need to be perfect; getting it roughly right is enough to route each item to the right person.

### 5.4 `compose`

Builds the delivered video: the slides composited against the presenter, with the topic's
bumpers baked in, at the partner's delivery quality. This is what `package` copies into
`out/`. `--draft` builds the old fast, small, watermarked, burned-in-subtitle file instead,
for checking cue timing quickly; it is never delivered and never satisfies `package`'s
prerequisite.

Input: `edit/master.mp4`, the rendered slide images, `cues.csv`, and the subtitle file.
`--lang zh` uses the Mandarin slide images, bumpers and subtitle file, composited against
the same presenter footage (Mandarin has no video of its own): it is the only way to find
out whether Chinese text fits the slides and the safe area before anything is delivered.

Build a slide track from the PNGs, each held for the interval given by consecutive rows of the cue sheet and the last one running to the end of the video. Composite the presenter against it according to a layout defined in config, since the partner's composite layout is not yet agreed. Support at least: slide full frame with the presenter as an inset, and slide and presenter side by side.

**Subtitles are not burned in by default.** A sidecar SRT is written instead
(`build/subtitles/<id>.<lang>.delivery.srt`), its every cue shifted forward by the intro's
duration so it lines up with the composed file's own timeline; `edit/master.srt` itself is
only ever read, never written. `--burn-subtitles` burns them into the video instead and
skips the sidecar, for anyone who still wants that.

**Bumpers.** An intro and an outro can be prepended and appended. Paths come from `programme.toml`, overridable per module, and either may be absent. They are transcoded to match the body's resolution and frame rate if they do not already, and their audio is normalised to the body's level rather than left at whatever the source was. `--no-bumpers` skips them, which is what you want while iterating on timings. Delivery is not either/or: package delivers the bumpers baked into the composed video and, beside it, `<id>.intro.<lang>.mp4` / `<id>.outro.<lang>.mp4` as their own files, exactly as before.

**Bumpers must never change the cue sheet.** Every timecode in `cues.csv` is relative to the first frame of the edited master, always, whatever is wrapped around it. `compose` applies the intro duration as an offset internally when it builds the slide track, and does not write shifted timings into `cues.csv`. The intro's duration is recorded as `body_offset`, in the envelope and in the delivered manifest, and is the only place that offset is written down: the sidecar SRT's shift is derived from it, not the other way round. Baking the offset into the cue sheet would mean two files claiming to be the timings, which is precisely the problem this pipeline exists to avoid.

Full mode encodes to `[delivery]`'s resolution, frame rate and codec (§9), the same spec `package` used to transcode the master to; there is no separate transcode step any more, because every full compose is already a from-scratch encode to that spec. `--draft` keeps its own small, fast settings under `[compose]`, unrelated to `[delivery]`.

Output: `build/composed.<lang>.mp4` (`build/composed.mp4` for English) in full mode, `build/draft.<lang>.mp4` under `--draft`. Neither is written directly to `out/`; `package` copies full mode's output there.

**Use `--draft` as the QA step for timings.** Reading a cue sheet tells you nothing about whether a slide changes in the right place. Watching thirty seconds of a draft tells you immediately. Any topic whose cue report contains a low-confidence boundary should be composed with `--draft` and watched before it is delivered.

### 5.5 `subtitles` (thin)

Converts `edit/master.srt` to WebVTT if the partner wants VTT, and normalises line lengths and cue durations to a configurable maximum. No re-timing, ever. If the partner accepts SRT, this tool does nothing and the file passes through untouched.

`--lang zh` takes the returned Mandarin SRT and verifies it against the English: same cue count, same in and out times, different text. Line-length normalisation uses character counts rather than words. A timing difference is an error, not something to correct silently.

### 5.6 `package`

Copies the delivery files into `out/` under the naming convention, strips narration from the markdown copy, writes `manifest.json` with checksums, and verifies that slide image count equals cue sheet row count equals slide count in the source.

The delivered video and subtitle file are `compose`'s output, not `edit/`'s: a current,
non-draft `compose` is a prerequisite (`STEP_PREREQUISITE` if it has not run, is stale, or
was `--draft`), and `package` only ever copies what it produced. `edit/master.mp4` and
`edit/master.srt` are read by `compose`, never directly by `package`, and are never written
by either. Mandarin composes and delivers its own video and subtitle file, built from the
same presenter footage with its own slides, bumpers and subtitles; the cue sheet has no
Mandarin equivalent (same recording, same timing) and is delivered once, by the English
package.

### 5.7 `qa`

Runs `validate` plus the cross-artefact checks that only make sense once everything exists, over a whole module, and writes both a human-readable table and `qa-report.json`.

Cross-artefact checks:

- Every topic in the course map has a directory, and vice versa.
- Every unit has an activity file.
- Every learning outcome in the course map is covered by at least one topic.
- Every asset request marked outstanding blocks the topics that reference it.
- Total video duration per unit within 85 to 110 minutes.
- Every topic that has a video also has slides, subtitles and a cue sheet, all newer than the source markdown.
- Video duration within 15% of the topic's declared `minutes`.
- The SRT's final cue ends within the video duration.
- No unresolved suspected mis-transcriptions outstanding in any `cues-report.json`.
- Where a Mandarin variant exists: slide parity with the English, identical SRT timings, no overflow, and no narration blocks in the `.zh.md` source.

---

## 6. Environment

Python 3.11 or later. `uv` or a virtualenv, your call.

Target platform is macOS on Apple Silicon. Nothing here is platform-specific by design, but that is what it must work on.

External binaries, all pinned and all present before a run starts: ffmpeg, Node with `@marp-team/marp-cli`, and a Chromium build for Marp's export. A startup check verifies each is present and at the expected version, and fails with a clear message rather than part way through a ninety-topic run.

macOS specifics:

- **Case-insensitive filesystem.** APFS is case-insensitive by default, so `kv7015-u01-t01.md` and `KV7015-U01-T01.md` are the same file and the regex checks in section 5.1 will pass on a wrongly-cased name. Compare the name on disk against the expected string exactly, rather than relying on the filesystem to distinguish them, and fail on a mismatch.
- **Enumerate by allowlist, not blocklist.** Within a topic directory the tool knows exactly which filenames it expects: `topic.md`, `topic.zh.md`, `edit/master.mp4`, `edit/master.srt`, `assets/*`, and the generated contents of `build/` and `out/`. Look for those, and ignore everything else rather than trying to filter out junk. A blocklist is a losing game on macOS.
- Anything unexpected in a topic directory is reported once as an `info` diagnostic, so a stray file is visible without being an error. The exception is a sync conflict copy, which is an error, because it means the file someone thinks they edited is not the one being processed.
- The noise to expect, none of which should ever reach the parser: `.DS_Store`, `._*` AppleDouble forks, `Icon\r`, `.localized`, `.Spotlight-V100`, `.fseventsd`, `.TemporaryItems`, `.Trashes`, `__MACOSX`, Office lock files `~$*`, editor backups `*~`, `*.swp`, and OneDrive conflict copies carrying a machine name.
- Run the backend under `launchd` rather than systemd.
- Homebrew ffmpeg is fine, but pin the version; a major bump changing filter behaviour mid-programme is not a problem you want to debug.

No speech models, no GPU, no model cache: nothing in this pipeline processes audio.

Config in one `programme.toml` at the repo root: forbidden word lists, match thresholds, delivery spec, default theme, bumper paths, composite layout. Per-module overrides in an optional `module.toml` beside the course map, limited to theme and bumpers. Slide resolution, safe area and fonts belong to the theme rather than to `programme.toml`, so that a theme is self-contained.

---

## 7. Build order

Build in this order, each usable on its own before the next starts.

Build order differs from run order because the early tools are useful before any recording exists, and because `align` is the hard part and benefits from being attacked with everything else already working.

1. `validate`, because it is useful the moment the first topic file exists and needs nothing else.
2. `render`, because it is the other thing needed before any recording happens. Theme work included.
3. `cues`. Expect the bulk of the effort here, and test it against a real edited topic rather than a synthetic one, because the interesting cases all come from real edits.
4. `compose`, immediately after `cues`. It is how you will tell whether `cues` is actually right, so building it next pays for itself.
5. `package` and `subtitles`.
6. `qa`.

Stop and test against one real topic after step 2 and again after step 5, rather than building the whole thing and finding out.

---

## 8. Out of scope for now

- Any SharePoint or Graph integration. Files are synced by hand.
- Translation of any kind.
- Video editing of any kind. The external editor delivers a finished file; `compose` composites it with slides and bumpers, but never cuts or re-times it.
- Transcription. The editor supplies the SRT.
- Re-timing subtitles. Their timings are authoritative.
- Mandarin subtitle generation. The partner produces it.
- A GUI.

---

## 9. What Claude Code should ask about before starting

- The partner's delivery specification is unconfirmed, so treat every value in section 5.6 as config with a placeholder default.
- The composite layout is unconfirmed, so the safe area in section 5.2 is a theme variable, not a number in code.
- Starting thresholds for section 5.4. Cutting is minimal by design and presenters read closely, so start strict and loosen only if a real edited topic proves it necessary.
- Whether the editor can export word-level timings alongside the SRT. Not required, but it would improve cue precision if it is free.
