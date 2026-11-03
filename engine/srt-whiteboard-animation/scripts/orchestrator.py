#!/usr/bin/env python3
"""G4-E Orchestrator: minimal, deterministic, side-effect-free.

Coordinates S0 (config), S1 (path), S2 (SRT parse), S3 (duration budget)
and returns a single JSON-serializable report dict.

Public API
    orchestrate(srt_text, config=None, word_count=None) -> dict

Stage statuses: "passed" / "skipped" / "failed". S1 is always "skipped"
because this version of orchestrate() exposes no path arguments (R3-1).

Error handling
    ConfigError / SrtParseError / DurationBudgetError -> failed_validation.
    Any other Exception -> failed_internal with code E_INTERNAL.
    BaseException subclasses (KeyboardInterrupt, SystemExit, GeneratorExit)
    are NOT caught.

config_hash
    SHA-256 of the canonical JSON of the PipelineConfig dict:
    json.dumps(cfg.to_dict(), sort_keys=True, separators=(",", ":"),
    allow_nan=False, ensure_ascii=False).encode("utf-8"). The
    generated_at_utc field is NOT part of the hash.

Imports policy
    Standard library plus the four pipeline.* contract modules. The
    path contract is NOT imported (S1 is skipped by design). No media,
    subprocess, network, or CLI modules.

Authoritative reference: G4-E implementation authorization (2026-10-21).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from pipeline.config_contract import (
    ConfigError,
    PipelineConfig,
    config_from_mapping,
    default_config,
)
from pipeline.duration_budget import (
    DurationBudgetError,
    calculate_duration_budget,
)
from pipeline.parse_srt_strict import (
    SrtParseError,
    parse_srt_strict,
)

S1_SKIP_REASON = "no_path_arguments_in_current_interface"
S3_SKIP_REASON = "word_count_not_provided"
E_DURATION_BUDGET = "E_DURATION_BUDGET"
E_INTERNAL = "E_INTERNAL"
E_CONFIG_NOT_MAPPING = "E_CONFIG_NOT_MAPPING"

OVERALL_PASSED = "passed"
OVERALL_FAILED_VALIDATION = "failed_validation"
OVERALL_FAILED_INTERNAL = "failed_internal"
STAGE_PASSED = "passed"
STAGE_SKIPPED = "skipped"
STAGE_FAILED = "failed"


def _config_hash(cfg: PipelineConfig) -> str:
    canonical = json.dumps(
        cfg.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_config(config: object) -> PipelineConfig:
    if config is None:
        return default_config()
    if isinstance(config, PipelineConfig):
        return config
    if isinstance(config, Mapping):
        return config_from_mapping(config)
    raise ConfigError(
        E_CONFIG_NOT_MAPPING,
        "config",
        "config must be a Mapping or PipelineConfig, got {}".format(
            type(config).__name__
        ),
    )


def _duration_error_dict(exc: Exception) -> dict:
    return {
        "code": E_DURATION_BUDGET,
        "message": str(exc),
        "field": getattr(exc, "field", None),
    }


def _s1_stage() -> dict:
    return {
        "name": "S1",
        "title": "path",
        "status": STAGE_SKIPPED,
        "reason": S1_SKIP_REASON,
    }


def _s3_skipped() -> dict:
    return {
        "status": STAGE_SKIPPED,
        "reason": S3_SKIP_REASON,
        "word_count": None,
        "estimated_speech_duration_ms": None,
        "estimated_total_duration_ms": None,
        "slack_ms": None,
        "overrun_ms": 0,
    }


def _run_s3(word_count: int | None, cfg: PipelineConfig) -> tuple[dict, Exception | None]:
    if word_count is None:
        return _s3_skipped(), None
    try:
        budget = calculate_duration_budget(
            word_count=word_count,
            words_per_minute=cfg.words_per_minute,
            pause_ms=cfg.pause_ms,
            target_duration_ms=cfg.target_duration_ms,
            safety_buffer_ms=cfg.safety_buffer_ms,
        )
    except DurationBudgetError as exc:
        return (
            {
                "status": STAGE_FAILED,
                "word_count": word_count,
                "error": _duration_error_dict(exc),
            },
            exc,
        )
    return {
        "status": STAGE_PASSED,
        "word_count": word_count,
        "business_status": budget.status,
        "estimated_speech_duration_ms": budget.speech_duration_ms,
        "estimated_total_duration_ms": budget.estimated_duration_ms,
        "estimated_duration_ms": budget.estimated_duration_ms,
        "slack_ms": budget.slack_ms,
        "overrun_ms": budget.overrun_ms,
        "speech_duration_ms": budget.speech_duration_ms,
        "pause_ms": budget.pause_ms,
        "target_duration_ms": budget.target_duration_ms,
        "safety_buffer_ms": budget.safety_buffer_ms,
        "words_per_minute": budget.words_per_minute,
    }, None


def _finish_report(stages: list[dict], errors: list[dict], cfg, internal=False) -> dict:
    if errors:
        overall = OVERALL_FAILED_INTERNAL if internal else OVERALL_FAILED_VALIDATION
    else:
        overall = OVERALL_PASSED
    return {
        "generated_at_utc": _utc_now_iso(),
        "overall_status": overall,
        "config_hash": _config_hash(cfg) if cfg is not None else None,
        "stages": stages,
        "errors": errors,
    }


def _orchestrate_impl(srt_text, config=None, word_count=None) -> dict:
    stages: list[dict] = []
    errors: list[dict] = []

    try:
        cfg = _coerce_config(config)
    except ConfigError as exc:
        stages.append({
            "name": "S0",
            "title": "config",
            "status": STAGE_FAILED,
            "error": {
                "code": exc.code,
                "field": exc.field,
                "message": exc.message,
            },
        })
        errors.append(exc.as_dict())
        return _finish_report(stages, errors, cfg=None)

    stages.append({
        "name": "S0",
        "title": "config",
        "status": STAGE_PASSED,
        "config_keys": list(cfg.to_dict().keys()),
    })

    stages.append(_s1_stage())

    try:
        cues = parse_srt_strict(srt_text)
    except SrtParseError as exc:
        stages.append({
            "name": "S2",
            "title": "srt_parse",
            "status": STAGE_FAILED,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "cue_number": exc.cue_number,
                "block_number": exc.block_number,
                "line_number": exc.line_number,
            },
        })
        errors.append(exc.as_dict())
        return _finish_report(stages, errors, cfg=cfg)

    stages.append({
        "name": "S2",
        "title": "srt_parse",
        "status": STAGE_PASSED,
        "cue_count": len(cues),
        "total_duration_ms": (cues[-1].end_ms if cues else 0),
    })

    s3_result, s3_err = _run_s3(word_count, cfg)
    stages.append({"name": "S3", "title": "duration_budget", **s3_result})
    if s3_err is not None:
        errors.append(_duration_error_dict(s3_err))
        return _finish_report(stages, errors, cfg=cfg)
    return _finish_report(stages, errors, cfg=cfg)


def orchestrate(srt_text: str, config=None, word_count: int | None = None) -> dict:
    """Run S0/S1/S2/S3 and return a JSON-serializable report dict.

    Args:
        srt_text: Raw SRT text.
        config: Mapping or PipelineConfig; None means default_config().
        word_count: Optional positive int. None => S3 skipped.

    Exception policy: BaseException subclasses propagate unchanged.
    Any other Exception becomes a failed_internal report with code E_INTERNAL.
    """
    try:
        return _orchestrate_impl(srt_text, config=config, word_count=word_count)
    except ConfigError as exc:
        return _finish_report([], [exc.as_dict()], None)
    except SrtParseError as exc:
        return _finish_report([], [exc.as_dict()], None)
    except DurationBudgetError as exc:
        return _finish_report([], [_duration_error_dict(exc)], None)
    except Exception as exc:  # noqa: BLE001 - documented fallback
        return _finish_report(
            [],
            [{"code": E_INTERNAL, "type": type(exc).__name__, "message": str(exc)}],
            None,
            internal=True,
        )
