"""bcn schema <tool>: the JSON Schema for a command's envelope.

Returned inside an envelope (as json_schema) because stdout only ever carries
envelopes. A UI can generate types from it rather than guess.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import codes
from ..envelope import SCHEMA_VERSION, Envelope, Fail

HELP = "print the JSON Schema for a command's envelope"

_str_or_null = {"type": ["string", "null"]}
_int_or_null = {"type": ["integer", "null"]}

DIAGNOSTIC = {
    "type": "object",
    "required": ["level", "code", "message"],
    "properties": {
        "level": {"enum": ["error", "warn", "info"]},
        "code": {"enum": sorted(codes.CODES)},
        "topic": _str_or_null, "lang": {"enum": ["en", "zh", None]}, "file": _str_or_null,
        "line": _int_or_null, "slide": _int_or_null, "message": {"type": "string"}, "hint": _str_or_null,
        "data": {"type": "object"},
    },
}
ARTIFACT = {
    "type": "object",
    "required": ["path", "kind", "bytes", "sha256"],
    "properties": {"path": {"type": "string"}, "kind": {"type": "string"}, "bytes": {"type": "integer"},
                   "sha256": {"type": "string"}},
}
LANG_STATE = {
    "type": "object",
    "properties": {
        "stage": {"type": "string"}, "stage_index": {"type": "integer"}, "stages": {"type": "array", "items": {"type": "string"}},
        "stale": {"type": "boolean"}, "stale_steps": {"type": "array", "items": {"type": "string"}},
        "blocked": {"type": "boolean"}, "blockers": {"type": "array", "items": {"type": "object"}},
        "next": _str_or_null, "complete": {"type": "boolean"},
        "diagnostics": {"type": "object", "properties": {k: {"type": "integer"} for k in ("error", "warn", "info")}},
        "steps": {"type": "object"},
    },
}

RESULT_EXTRAS: dict[str, dict[str, Any]] = {
    "validate": {"slides": {"type": "integer"}, "words": {"type": "integer"}, "target_words": {"type": "integer"}},
    "render": {"slides": {"type": "integer"}, "theme": {"type": "string"}, "marp_cli": {"type": "string"},
               "chrome": _str_or_null, "overflow_slides": {"type": "array", "items": {"type": "integer"}},
               "resolution": {"type": "array"}, "safe_bottom": {"type": "integer"}},
    "cues": {"slides": {"type": "integer"}, "min_confidence": {"type": "number"}, "divergence_ratio": {"type": "number"},
             "mistranscriptions": {"type": "integer"}, "unreviewed": {"type": "integer"},
             "manual": {"type": "array"}, "overrides": {"type": "object"}},
    "subtitles": {"cues": {"type": "integer"}, "modified": {"type": "boolean"}, "format": {"type": "string"},
                  "corrections": {"type": "array"}},
    "compose": {"mode": {"enum": ["draft", "full"]}, "subtitles": {"enum": ["sidecar", "burned"]},
                "layout": {"type": "string"}, "resolution": {"type": "array"}, "body_offset": {"type": "number"},
                "bumpers": {"type": "array"}, "duration": {"type": "number"}},
    "package": {"slides": {"type": "integer"}, "duration": {"type": "number"}, "files": {"type": "integer"},
                "body_offset": {"type": "number"}},
    "qa": {"video_minutes": {"type": "number"}},
    "status": {"module": {"type": "string"}, "unit": {"type": "string"}, "code": {"type": "string"}, "path": {"type": "string"},
               "title": _str_or_null, "minutes": _int_or_null, "outcomes": {"type": "array"},
               "in_course_map": {"type": "boolean"}, "has_dir": {"type": "boolean"},
               "hydration": {"enum": ["local", "cloud", "partial", "unknown"]},
               "unreviewed_mistranscriptions": {"type": "integer"}, "en": LANG_STATE, "zh": LANG_STATE,
               "artifacts": {"type": "array", "items": {"type": "object"}}},
    "show": {"en": {"type": ["object", "null"]}, "zh": {"type": ["object", "null"]}, "cues": {"type": "array"},
             "cue_threshold": {"type": "number"}, "render": {"type": "object"}, "media": {"type": "object"},
             "review": {"type": "object"}},
    "review": {"items": {"type": "array"}, "unreviewed": {"type": "integer"}, "stale": {"type": "boolean"}},
    "intake": {"path": {"type": "string"}, "action": {"enum": ["written", "unchanged", "exists_differs", "refused", "would_write"]},
               "diff": {"type": "string"}, "validate_ok": {"type": "boolean"}},
    "translation": {"batch": {"type": "string"}, "files": {"type": "object"}, "validate_ok": {"type": "boolean"},
                    "subtitles_ok": {"type": "boolean"}},
    "doctor": {"pinned": {"type": "string"}, "found": {"type": "string"}, "path": _str_or_null},
    "sync": {"copied": {"type": "integer"}, "to_copy": {"type": "integer"}, "conflict": {"type": "integer"},
             "one_side": {"type": "integer"}, "in_sync": {"type": "integer"}, "check": {"type": "integer"}},
    "ack": {"slides": {"type": "integer"}, "words": {"type": "integer"}, "target_words": {"type": "integer"}},
    "script": {"slides": {"type": "integer"}, "words": {"type": "integer"}, "pdf": {"type": "string"}},
    "qti": {"title": {"type": "string"}, "questions": {"type": "integer"}, "multiple_response": {"type": "integer"},
            "package": _str_or_null},
    "coursemap": {"pdf": _str_or_null},
    "bumpers": {"title": {"type": "string"}, "logo": {"type": "boolean"}, "font_size": {"type": "integer"},
                "intro_seconds": {"type": "number"}, "outro_seconds": {"type": "number"},
                "resolution": {"type": "array"}, "fps": {"type": "number"}},
    "edit": {"slides": {"type": "integer"}, "words": {"type": "integer"}, "target_words": {"type": ["integer", "null"]},
             "written": {"type": "boolean"}, "sha256": {"type": ["string", "null"]}, "current_sha256": {"type": "string"}},
    "diagnostics": {}, "codes": {}, "schema": {},
}

TOP_EXTRAS: dict[str, dict[str, Any]] = {
    "status": {"summary": {"type": "object"}, "verified": {"type": "boolean"}},
    "diagnostics": {"counts": {"type": "object"}, "outstanding": {"type": "array", "items": DIAGNOSTIC}},
    "codes": {"codes": {"type": "array", "items": {"type": "object"}}},
    "schema": {"json_schema": {"type": "object"}},
    "intake": {"topics_found": {"type": "integer"}},
    "translation": {"batch": {"type": "string"}, "folder": {"type": "string"}, "zip": {"type": "string"},
                    "return_to_translator": {"type": "array"}},
    "qa": {"unit_minutes": {"type": "object"}},
    "ack": {"acknowledged": {"type": "object"}},
    "edit": {"validation_ok": {"type": "boolean"}, "counts": {"type": "object"}, "diagnostics_are_validation": {"type": "boolean"}},
    "sync": {"direction": {"enum": ["pull", "push"]}, "dry_run": {"type": "boolean"}, "remote": {"type": "string"},
             "counts": {"type": "object"}, "plan": {"type": "array"}, "ignored": {"type": "array"},
             "last_pull": _str_or_null, "last_push": _str_or_null},
}


def build(tool: str) -> dict[str, Any]:
    if tool not in RESULT_EXTRAS:
        raise Fail("USAGE", f"No command '{tool}'. Choose from {', '.join(sorted(RESULT_EXTRAS))}.")
    result = {
        "type": "object",
        "required": ["topic", "ok", "skipped"],
        "properties": {"topic": {"type": "string"}, "ok": {"type": "boolean"}, "skipped": {"type": "boolean"},
                       **RESULT_EXTRAS[tool]},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"bcn/{tool}/v{SCHEMA_VERSION}",
        "title": f"bcn {tool} envelope",
        "type": "object",
        "required": ["tool", "schema", "target", "ok", "started", "duration_ms", "results", "artifacts", "diagnostics"],
        "properties": {
            "tool": {"const": tool},
            "schema": {"const": SCHEMA_VERSION},
            "target": {"type": "string"},
            "ok": {"type": "boolean"},
            "cancelled": {"type": "boolean"},
            "started": {"type": "string", "format": "date-time"},
            "duration_ms": {"type": "integer"},
            "results": {"type": "array", "items": result},
            "artifacts": {"type": "array", "items": ARTIFACT},
            "diagnostics": {"type": "array", "items": DIAGNOSTIC},
            **TOP_EXTRAS.get(tool, {}),
        },
        "$defs": {
            "progress": {
                "description": "NDJSON on stderr, one object per line.",
                "type": "object",
                "properties": {
                    "event": {"enum": ["progress", "log", "done"]}, "step": {"type": "string"}, "topic": _str_or_null,
                    "item": {"type": "integer"}, "items": {"type": "integer"}, "pct": {"type": "number"},
                    "topic_pct": {"type": ["number", "null"]}, "elapsed_ms": {"type": "integer"},
                    "eta_ms": {"type": "integer"}, "message": {"type": "string"}, "heartbeat": {"type": "boolean"},
                    "cancelled": {"type": "boolean"}, "level": {"type": "string"},
                },
            }
        },
    }


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tool", help="the command whose envelope schema to print")


def run(args: argparse.Namespace, env: Envelope) -> None:
    env.target = args.tool
    env.extra["json_schema"] = build(args.tool)
