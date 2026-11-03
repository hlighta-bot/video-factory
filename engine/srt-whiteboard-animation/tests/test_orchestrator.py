#!/usr/bin/env python3
"""T1-T17 tests for G4-E Orchestrator (see G4-E authorization, 2026-10-21)."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
_ENGINE_SCRIPTS = _REPO / "scripts"
_ENGINE_PIPELINE = _ENGINE_SCRIPTS / "pipeline"

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_ENGINE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SCRIPTS))

import orchestrator  # noqa: E402
from orchestrator import orchestrate  # noqa: E402
from pipeline.config_contract import default_config  # noqa: E402
from pipeline.parse_srt_strict import parse_srt_strict  # noqa: E402

# T1 authoritative constants:
#   canonical = json.dumps(default_config().to_dict(), sort_keys=True,
#                          separators=(",", ":"), allow_nan=False,
#                          ensure_ascii=False)
#   hash      = sha256(canonical.encode("utf-8")).hexdigest()
KNOWN_CANONICAL_JSON = (
    '{"allow_overwrite":false,"pause_ms":300,"safety_buffer_ms":500,'
    '"target_duration_ms":null,"words_per_minute":135}'
)
KNOWN_FIXED_HASH = "8b89d260c6b9ea2dd255b7c030a982403b619236905a952519db046df0972c5d"

VALID_SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello world\n\n"
    "2\n00:00:02,500 --> 00:00:04,000\nSecond cue.\n"
)

CONTRACT_FILES = (
    _ENGINE_PIPELINE / "parse_srt_strict.py",
    _ENGINE_PIPELINE / "duration_budget.py",
    _ENGINE_PIPELINE / "config_contract.py",
    _ENGINE_PIPELINE / "path_contract.py",
)

FORBIDDEN_TOP_FIELDS = {"path", "argv", "traceback", "secret"}
FORBIDDEN_SUBSTRINGS = ("SECRET", "TOKEN", "API_KEY", "/root/", "C:\\\\Users")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(report: dict, name: str) -> dict:
    for s in report["stages"]:
        if s.get("name") == name:
            return s
    raise AssertionError("stage {} not found".format(name))


def _full_cfg(**overrides):
    d = {"words_per_minute": 135, "pause_ms": 300, "safety_buffer_ms": 500,
         "target_duration_ms": None, "allow_overwrite": False}
    d.update(overrides)
    return d


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.dont_write_bytecode = True

    # -- T1: fixed default config hash ----------------------------------------
    def test_T1_default_config_hash_is_fixed(self) -> None:
        cfg = default_config()
        canonical = json.dumps(
            cfg.to_dict(), sort_keys=True, separators=(",", ":"),
            allow_nan=False, ensure_ascii=False,
        )
        self.assertEqual(canonical, KNOWN_CANONICAL_JSON)
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(), KNOWN_FIXED_HASH,
        )
        report = orchestrate(VALID_SRT)
        self.assertEqual(report["config_hash"], KNOWN_FIXED_HASH)

    # -- T2: canonical encoding is sorted + compact --------------------------
    def test_T2_canonical_json_encoding_is_sorted_and_compact(self) -> None:
        self.assertEqual(
            orchestrator._config_hash(default_config()), KNOWN_FIXED_HASH,
        )
        report = orchestrate(VALID_SRT, word_count=10)
        self.assertEqual(report["config_hash"], KNOWN_FIXED_HASH)

    # -- T3: happy path ------------------------------------------------------
    def test_T3_happy_path_returns_all_stages_and_passed(self) -> None:
        report = orchestrate(VALID_SRT, word_count=42)
        self.assertEqual(report["overall_status"], "passed")
        self.assertEqual(len(report["stages"]), 4)
        self.assertEqual([s["name"] for s in report["stages"]],
                         ["S0", "S1", "S2", "S3"])
        self.assertEqual(_stage(report, "S0")["status"], "passed")
        self.assertEqual(_stage(report, "S1")["status"], "skipped")
        self.assertEqual(_stage(report, "S2")["status"], "passed")
        self.assertEqual(_stage(report, "S3")["status"], "passed")
        self.assertEqual(_stage(report, "S2")["cue_count"], 2)

    # -- T4: ConfigError cases ----------------------------------------------
    def test_T4_config_errors_propagate_as_failed_validation(self) -> None:
        cases = [
            ("E_CONFIG_FIELD_BOOL", {"words_per_minute": True}),
            ("E_CONFIG_FIELD_BOOL", _full_cfg(pause_ms=True)),
            ("E_CONFIG_FIELD_BOOL", _full_cfg(safety_buffer_ms=True)),
            ("E_CONFIG_FIELD_BOOL", _full_cfg(target_duration_ms=True)),
            ("E_CONFIG_FIELD_TYPE", _full_cfg(allow_overwrite=1)),
            ("E_CONFIG_FIELD_TYPE", {"words_per_minute": "fast"}),
            ("E_CONFIG_FIELD_TYPE", _full_cfg(pause_ms=1.5)),
            ("E_CONFIG_FIELD_NON_FINITE", {"words_per_minute": float("nan")}),
            ("E_CONFIG_FIELD_NON_FINITE", {"words_per_minute": float("inf")}),
            ("E_CONFIG_FIELD_RANGE", {"words_per_minute": 0}),
            ("E_CONFIG_FIELD_RANGE", {"words_per_minute": 1001}),
            ("E_CONFIG_FIELD_RANGE", _full_cfg(pause_ms=-1)),
            ("E_CONFIG_FIELD_RANGE", _full_cfg(target_duration_ms=0)),
            ("E_CONFIG_FIELD_RANGE", _full_cfg(target_duration_ms=-5)),
            ("E_CONFIG_NOT_MAPPING", 42),
            ("E_CONFIG_NOT_MAPPING", "s"),
            ("E_CONFIG_NOT_MAPPING", []),
            ("E_CONFIG_NOT_MAPPING", set()),
            ("E_CONFIG_FIELD_MISSING", {}),
            ("E_CONFIG_FIELD_UNKNOWN", {"words_per_minute": 135, "bogus": 1}),
        ]
        for code, cfg in cases:
            with self.subTest(code=code, cfg=cfg):
                report = orchestrate(VALID_SRT, config=cfg)  # type: ignore[arg-type]
                self.assertEqual(report["overall_status"], "failed_validation")
                self.assertGreaterEqual(len(report["errors"]), 1)
                self.assertEqual(report["errors"][0]["code"], code)
                self.assertEqual(_stage(report, "S0")["status"], "failed")

    def test_T4_non_mapping_config_rejected(self) -> None:
        for bad in (42, [], "s", set()):
            with self.subTest(bad=bad):
                report = orchestrate(VALID_SRT, config=bad)  # type: ignore[arg-type]
                self.assertEqual(report["overall_status"], "failed_validation")
                self.assertEqual(report["errors"][0]["code"], "E_CONFIG_NOT_MAPPING")

    def test_T4_config_none_uses_default(self) -> None:
        report = orchestrate(VALID_SRT, config=None)
        self.assertEqual(report["overall_status"], "passed")
        self.assertEqual(report["config_hash"], KNOWN_FIXED_HASH)

    # -- T5: S1 always skipped ----------------------------------------------
    def test_T5_S1_is_always_skipped_with_reason(self) -> None:
        for kwargs in ({}, {"word_count": 5},
                       {"config": {"words_per_minute": 200}}):
            with self.subTest(kwargs=kwargs):
                report = orchestrate(VALID_SRT, **kwargs)
                s1 = _stage(report, "S1")
                self.assertEqual(s1["status"], "skipped")
                self.assertEqual(s1["reason"],
                                 "no_path_arguments_in_current_interface")
                self.assertNotIn("path", s1)
                self.assertNotIn("argv", s1)

    def test_T5_orchestrator_does_not_import_path_contract(self) -> None:
        src = Path(orchestrator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from pipeline.path_contract", src)
        self.assertNotIn("import pipeline.path_contract", src)

    # -- T6: SRT parse failure ----------------------------------------------
    def test_T6_srt_parse_error_yields_failed_validation(self) -> None:
        report = orchestrate("1\nnot-a-timestamp\n")
        self.assertEqual(report["overall_status"], "failed_validation")
        s2 = _stage(report, "S2")
        self.assertEqual(s2["status"], "failed")
        self.assertIn("code", s2["error"])
        self.assertIn("message", s2["error"])
        self.assertEqual(report["errors"][0]["code"], s2["error"]["code"])

    # -- T7: DurationBudgetError --------------------------------------------
    def test_T7_duration_budget_error_yields_failed_validation(self) -> None:
        for bad_wc in (-1, True):
            with self.subTest(wc=bad_wc):
                report = orchestrate(VALID_SRT, word_count=bad_wc)
                self.assertEqual(report["overall_status"], "failed_validation")
                s3 = _stage(report, "S3")
                self.assertEqual(s3["status"], "failed")
                self.assertEqual(s3["error"]["code"], "E_DURATION_BUDGET")
                self.assertEqual(report["errors"][0]["code"], "E_DURATION_BUDGET")

    # -- T8: Unexpected Exception -> failed_internal -------------------------
    def test_T8_unexpected_exception_yields_failed_internal(self) -> None:
        with mock.patch.object(
            orchestrator, "parse_srt_strict",
            side_effect=RuntimeError("boom"),
        ):
            report = orchestrate(VALID_SRT, word_count=5)
        self.assertEqual(report["overall_status"], "failed_internal")
        self.assertEqual(report["errors"][0]["code"], "E_INTERNAL")
        self.assertEqual(report["errors"][0]["type"], "RuntimeError")

    # -- T9: BaseException subclasses propagate ------------------------------
    def test_T9_base_exception_subclasses_propagate(self) -> None:
        for exc_class in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc_class.__name__):
                with mock.patch.object(
                    orchestrator, "parse_srt_strict",
                    side_effect=exc_class(),
                ):
                    with self.assertRaises(exc_class):
                        orchestrate(VALID_SRT, word_count=5)

    # -- T10: report structure + UTC shape ----------------------------------
    def test_T10_report_structure_and_utc_shape(self) -> None:
        report = orchestrate(VALID_SRT, word_count=10)
        for key in ("generated_at_utc", "overall_status", "config_hash",
                     "stages", "errors"):
            self.assertIn(key, report)
        self.assertRegex(
            report["generated_at_utc"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )
        self.assertEqual(report["overall_status"], "passed")
        self.assertEqual(len(report["errors"]), 0)

    # -- T11: strict JSON-serializable ---------------------------------------
    def test_T11_report_is_strictly_json_serializable(self) -> None:
        report = orchestrate(VALID_SRT, word_count=10)
        encoded = json.dumps(report, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(
            sorted(decoded.keys()),
            ["config_hash", "errors", "generated_at_utc", "overall_status",
             "stages"],
        )

    # -- T12: config=None and config=Mapping ---------------------------------
    def test_T12_config_none_and_mapping_paths(self) -> None:
        r_none = orchestrate(VALID_SRT)
        r_map = orchestrate(VALID_SRT, config={"words_per_minute": 135})
        r_obj = orchestrate(VALID_SRT, config=default_config())
        self.assertEqual(r_none["config_hash"], KNOWN_FIXED_HASH)
        self.assertEqual(r_map["config_hash"], KNOWN_FIXED_HASH)
        self.assertEqual(r_obj["config_hash"], KNOWN_FIXED_HASH)
        r_alt = orchestrate(
            VALID_SRT, config={"words_per_minute": 135, "pause_ms": 400},
        )
        self.assertNotEqual(r_alt["config_hash"], KNOWN_FIXED_HASH)

    # -- T13: word_count=None => S3 skipped ----------------------------------
    def test_T13_word_count_none_skips_S3(self) -> None:
        s3 = _stage(orchestrate(VALID_SRT), "S3")
        self.assertEqual(s3["status"], "skipped")
        self.assertEqual(s3["reason"], "word_count_not_provided")
        self.assertNotIn("business_status", s3)
        self.assertIsNone(s3["estimated_speech_duration_ms"])
        self.assertIsNone(s3["estimated_total_duration_ms"])
        self.assertIsNone(s3["slack_ms"])
        self.assertEqual(s3["overrun_ms"], 0)

    # -- T14: S3 passed fields -----------------------------------------------
    def test_T14_s3_passed_fields(self) -> None:
        s3 = _stage(orchestrate(VALID_SRT, word_count=42), "S3")
        self.assertEqual(s3["status"], "passed")
        self.assertEqual(s3["business_status"], "NO_TARGET")
        self.assertIsInstance(s3["estimated_speech_duration_ms"], int)
        self.assertIsInstance(s3["estimated_total_duration_ms"], int)
        self.assertIsNone(s3["slack_ms"])
        self.assertEqual(s3["overrun_ms"], 0)
        self.assertGreater(s3["estimated_total_duration_ms"], 0)

    # -- T14b: OVER_TARGET has negative slack + positive overrun -------------
    def test_T14b_over_target_has_negative_slack_and_positive_overrun(self) -> None:
        s3 = _stage(
            orchestrate(VALID_SRT,
                        config={"words_per_minute": 135,
                                "target_duration_ms": 1000},
                        word_count=42),
            "S3",
        )
        self.assertEqual(s3["status"], "passed")
        self.assertEqual(s3["business_status"], "OVER_TARGET")
        self.assertIsInstance(s3["slack_ms"], int)
        self.assertLess(s3["slack_ms"], 0)
        self.assertGreater(s3["overrun_ms"], 0)
        self.assertEqual(s3["overrun_ms"], -s3["slack_ms"])

    # -- T15: no forbidden fields or secrets ---------------------------------
    def test_T15_no_forbidden_fields_or_secrets_in_report(self) -> None:
        for kwargs in ({}, {"word_count": 42},
                       {"config": {"words_per_minute": 200}}):
            report = orchestrate(VALID_SRT, **kwargs)
            flat = json.dumps(report, ensure_ascii=False)
            for bad in FORBIDDEN_TOP_FIELDS:
                self.assertNotIn(bad, report.keys())
            for needle in FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(needle, flat)
            self.assertNotIn("Traceback (most recent call last", flat)

    # -- T16: contract module hashes stable ----------------------------------
    def test_T16_contract_module_hashes_stable_during_test_run(self) -> None:
        before = {p.name: _sha256(p) for p in CONTRACT_FILES}
        orchestrate(VALID_SRT)
        orchestrate(VALID_SRT, word_count=42)
        orchestrate("1\nbad\n")
        orchestrate(VALID_SRT,
                    config={"words_per_minute": 200}, word_count=5)
        orchestrate(VALID_SRT, word_count=0)
        after = {p.name: _sha256(p) for p in CONTRACT_FILES}
        self.assertEqual(before, after)

    # -- T17: no forbidden imports or network calls --------------------------
    def test_T17_no_forbidden_imports_in_orchestrator_source(self) -> None:
        src = Path(orchestrator.__file__).read_text(encoding="utf-8")
        forbidden = (
            "import socket", "import urllib", "import http.client",
            "from http", "import ssl", "import subprocess",
            "from subprocess", "import ftplib", "import smtplib",
            "import media", "ffmpeg", "PIL", "cv2",
        )
        for needle in forbidden:
            self.assertNotIn(needle, src)

    def test_T17_no_network_or_subprocess_calls_during_orchestrate(self) -> None:
        called: list = []

        def _boom(name: str):
            def _fake(*args, **kwargs):
                called.append(name)
                raise AssertionError("forbidden call: " + name)
            return _fake

        with mock.patch("subprocess.run", _boom("subprocess.run")), \
             mock.patch("subprocess.Popen", _boom("subprocess.Popen")), \
             mock.patch("subprocess.call", _boom("subprocess.call")), \
             mock.patch("subprocess.check_output", _boom("subprocess.check_output")), \
             mock.patch("subprocess.check_call", _boom("subprocess.check_call")), \
             mock.patch("os.system", _boom("os.system")), \
             mock.patch("os.popen", _boom("os.popen")), \
             mock.patch("socket.socket", _boom("socket.socket")), \
             mock.patch("socket.getaddrinfo", _boom("socket.getaddrinfo")), \
             mock.patch("socket.create_connection",
                        _boom("socket.create_connection")), \
             mock.patch("urllib.request.urlopen",
                        _boom("urllib.request.urlopen")), \
             mock.patch("urllib.request.Request",
                        _boom("urllib.request.Request")), \
             mock.patch("http.client.HTTPConnection",
                        _boom("http.client.HTTPConnection")), \
             mock.patch("ssl.create_default_context",
                        _boom("ssl.create_default_context")):
            orchestrate(VALID_SRT, word_count=42)
            orchestrate("1\nbad\n")
            orchestrate(VALID_SRT,
                        config={"words_per_minute": 200}, word_count=5)
        self.assertEqual(called, [])

    # -- additional regression: hash excludes timestamp ----------------------
    def test_config_hash_excludes_generated_at_utc(self) -> None:
        r1 = orchestrate(VALID_SRT)
        import time as _time
        _time.sleep(0.02)
        r2 = orchestrate(VALID_SRT)
        self.assertEqual(r1["config_hash"], r2["config_hash"])
        self.assertEqual(len(r1["config_hash"]), 64)

    def test_config_mapping_mutated_after_call_does_not_affect_config(self) -> None:
        mapping = {"words_per_minute": 200}
        hash1 = orchestrate(VALID_SRT, config=mapping)["config_hash"]
        mapping["words_per_minute"] = 999
        hash2 = orchestrate(VALID_SRT, config=mapping)["config_hash"]
        self.assertNotEqual(hash1, hash2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
