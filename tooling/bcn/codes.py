"""Every diagnostic code bcn can emit, in one place so it can be enumerated.

Codes are stable. Messages are for humans and may be reworded at any time; the
UI groups, filters and counts by code. Add new codes here and nowhere else.
"""

from __future__ import annotations

# code -> (default level, one-line description)
CODES: dict[str, tuple[str, str]] = {
    # --- usage and environment -------------------------------------------
    "USAGE": ("error", "The command was invoked incorrectly."),
    "ROOT_NOT_FOUND": ("error", "No programme.toml found at or above the path."),
    "PATH_OUTSIDE_ROOT": ("error", "A path resolves outside the programme root."),
    "CONFIG_INVALID": ("error", "programme.toml or module.toml could not be read or has invalid values."),
    "TOOL_MISSING": ("error", "An external binary is not installed where expected."),
    "TOOL_VERSION": ("error", "An external binary is not at the pinned version."),
    "TOOL_FAILED": ("error", "An external binary exited with an error."),
    "CANCELLED": ("error", "The run was cancelled."),
    "INTERNAL": ("error", "An unexpected error inside bcn. This is a bug."),
    # --- filesystem ------------------------------------------------------
    "FS_MISSING": ("error", "A required input file does not exist."),
    "FS_EMPTY": ("error", "A file exists but is zero length."),
    "FS_CORRUPT": ("error", "A file exists but its contents are not what its name claims."),
    "FS_CHECKSUM_MISMATCH": ("error", "A packaged file does not match manifest.json."),
    "FS_NOT_HYDRATED": ("error", "A file is a cloud-only placeholder and its contents are not on disk."),
    "FS_SYNC_CONFLICT": ("error", "A sync conflict copy sits beside the real file."),
    "FS_WRONG_CASE": ("error", "A file or directory name differs from the expected name only by case."),
    "FS_UNEXPECTED_FILE": ("info", "A file the tooling does not expect is present; it is ignored."),
    "FS_UNSTABLE": ("warn", "A file was modified in the last few seconds and may still be syncing."),
    "STEP_PREREQUISITE": ("error", "A previous step has not been run, or its output is stale."),
    # --- markdown and content --------------------------------------------
    "MD_FRONT_MATTER_MISSING": ("error", "topic.md does not begin with a front matter block."),
    "MD_FRONT_MATTER_KEYS": ("error", "Front matter keys are not exactly topic_id, title, minutes, lang."),
    "MD_FRONT_MATTER_VALUE": ("error", "A front matter value is malformed."),
    "MD_TOPIC_ID_FORMAT": ("error", "topic_id does not match the required pattern."),
    "MD_TOPIC_ID_PATH": ("error", "topic_id does not match the directory the file lives in."),
    "MD_NO_SLIDES": ("error", "No slide content found."),
    "MD_SAY_MISSING": ("error", "A slide has no > **Say:** narration block."),
    "MD_SAY_MULTIPLE": ("error", "A slide has more than one > **Say:** block."),
    "MD_SAY_NOT_LAST": ("error", "A > **Say:** block is not the last element on its slide."),
    "MD_SAY_IN_ZH": ("error", "A Mandarin source file contains a > **Say:** block."),
    "MD_SLIDE_COUNT": ("error", "Slide count is outside the allowed range."),
    "MD_WORD_COUNT": ("error", "Total narration length is outside tolerance for the declared minutes."),
    "MD_SLIDE_WORDS": ("error", "One slide's narration is too short or too long."),
    "MD_TITLE_LENGTH": ("error", "A slide title is too long."),
    "MD_SLIDE_TITLE_MISSING": ("warn", "A slide has no heading."),
    "MD_DATE": ("error", "Narration or slide text contains a date."),
    "MD_FORBIDDEN": ("error", "Text contains a forbidden string."),
    "MD_DEICTIC": ("error", "Narration refers to something on screen."),
    "MD_IMAGE_ALT": ("error", "An image has empty alt text."),
    "MD_IMAGE_PATH": ("error", "An image path is not a relative path into assets/."),
    "MD_ASSET_MISSING": ("error", "A referenced asset file does not exist."),
    "MD_ZH_CHARS": ("error", "A Mandarin slide carries more characters than the sanity limit."),
    "MD_PARITY_COUNT": ("error", "Mandarin slide count differs from the English."),
    "MD_PARITY_BREAK": ("error", "A Mandarin slide break sits in a different position from the English."),
    "MD_EN_INVALID": ("error", "The English source must validate before the Mandarin can be checked against it."),
    # --- module documents ------------------------------------------------
    "DOC_MISSING": ("error", "A module document is missing."),
    "DOC_HEADING_MISSING": ("error", "A required heading is missing from a module document."),
    "DOC_ID_FORMAT": ("error", "A unit, topic or outcome id is malformed."),
    "DOC_OUTCOME_UNKNOWN": ("error", "A document references a learning outcome not in the course map."),
    "DOC_DUPLICATE_ID": ("error", "An id appears more than once."),
    "DOC_TOPIC_NOT_IN_MAP": ("error", "A topic is not listed in the course map."),
    # --- render ----------------------------------------------------------
    "RENDER_FAILED": ("error", "Marp did not produce the expected output."),
    "RENDER_SLIDE_COUNT": ("error", "Rendered slide count differs from the parsed slide count."),
    "RENDER_OVERFLOW": ("warn", "Content overflows the slide's content box. Blocking for Mandarin."),
    "RENDER_SAFE_AREA": ("warn", "Content sits inside the subtitle safe area. Blocking for Mandarin."),
    "RENDER_THEME": ("error", "The theme is missing or its theme.toml is invalid."),
    "RENDER_RESOLUTION": ("error", "A rendered PNG is not at the theme's declared resolution."),
    # --- cues ------------------------------------------------------------
    "CUE_LOW_CONFIDENCE": ("error", "A slide boundary could not be located with enough confidence."),
    "CUE_MID_CUE": ("warn", "A slide boundary falls part way through a subtitle cue."),
    "CUE_SRT_ORDER": ("error", "SRT cue timecodes are not strictly ascending."),
    "CUE_SRT_PAST_END": ("error", "The final SRT cue ends after the video does."),
    "CUE_SRT_SHORT": ("warn", "The final SRT cue ends well before the video does."),
    "CUE_PAIR_MISMATCH": ("error", "The SRT and the video do not look like a matching pair."),
    "CUE_HEAVY_DIVERGENCE": ("error", "Too much of the script is absent from or differs from the SRT."),
    "CUE_CUT": ("info", "A span of script is absent from the SRT."),
    "CUE_CUT_AT_BOUNDARY": ("error", "A cut spans a slide boundary; a human must place it."),
    "CUE_MISTRANSCRIPTION": ("warn", "The SRT probably mishears the script here."),
    "CUE_PARAPHRASE": ("warn", "The presenter departed from the approved script."),
    "CUE_INSERTION": ("info", "The SRT contains words that are not in the script."),
    "CUE_SHEET_INVALID": ("error", "cues.csv is malformed or disagrees with the slide count."),
    "SRT_PARSE": ("error", "The SRT could not be parsed."),
    "SRT_ZH_CUE_COUNT": ("error", "The Mandarin SRT has a different number of cues from the English."),
    "SRT_ZH_TIMING": ("error", "A Mandarin SRT cue has different timings from the English."),
    "SRT_ZH_UNTRANSLATED": ("warn", "A Mandarin SRT cue has the same text as the English."),
    "SRT_LINE_LENGTH": ("info", "A subtitle line was rewrapped to the configured maximum."),
    "SRT_CUE_DURATION": ("warn", "A subtitle cue is longer than the configured maximum; timings are never changed."),
    "SRT_CORRECTION": ("info", "A reviewed mis-transcription correction was applied to the subtitle text."),
    # --- compose ---------------------------------------------------------
    "COMPOSE_BUMPER": ("warn", "A bumper is configured but could not be used."),
    # --- package ---------------------------------------------------------
    "PKG_COUNT_MISMATCH": ("error", "Slide image count, cue sheet rows and source slide count disagree."),
    "PKG_TRANSCODED": ("info", "The video was transcoded to the delivery specification."),
    # --- review ----------------------------------------------------------
    "REVIEW_UNKNOWN_ITEM": ("error", "No suspected mis-transcription with that id."),
    # --- intake ----------------------------------------------------------
    "INTAKE_NO_TOPICS": ("error", "No topic front matter was found in the pasted text."),
    "INTAKE_NOT_IN_MAP": ("error", "The pasted topic is not in the course map."),
    "INTAKE_EXISTS_DIFFERS": ("error", "The topic file already exists with different content."),
    "INTAKE_UNCHANGED": ("info", "The topic file already exists with identical content."),
    "INTAKE_WRITTEN": ("info", "The topic file was written."),
    # --- translation -----------------------------------------------------
    "XL_NOT_READY": ("error", "A topic is not ready to export for translation."),
    "XL_EXPORTED": ("info", "A topic was exported for translation."),
    "XL_IMPORTED": ("info", "A translated file was placed."),
    "XL_UNKNOWN_FILE": ("warn", "A returned file does not name a known topic."),
    # --- qa --------------------------------------------------------------
    "QA_TOPIC_NO_DIR": ("error", "A topic in the course map has no directory."),
    "QA_DIR_NOT_IN_MAP": ("error", "A topic directory is not in the course map."),
    "QA_ACTIVITY_MISSING": ("error", "A unit has no activity.md."),
    "QA_OUTCOME_UNCOVERED": ("error", "A learning outcome is not covered by any topic."),
    "QA_ASSET_OUTSTANDING": ("error", "A topic references an outstanding asset request."),
    "QA_UNIT_DURATION": ("error", "A unit's total video duration is outside the allowed range."),
    "QA_ARTEFACT_MISSING": ("error", "A topic with a video lacks slides, subtitles or a cue sheet."),
    "QA_ARTEFACT_STALE": ("error", "An artefact is older than the source markdown."),
    "QA_VIDEO_DURATION": ("error", "Video duration is outside tolerance for the declared minutes."),
    "QA_UNREVIEWED_MISTRANSCRIPTION": ("error", "A suspected mis-transcription has not been reviewed."),
    "QA_ZH_OVERFLOW": ("error", "The Mandarin render reports overflow."),
}

LEVELS = ("error", "warn", "info")


def level_of(code: str) -> str:
    return CODES[code][0]


def describe(code: str) -> str:
    return CODES[code][1]
