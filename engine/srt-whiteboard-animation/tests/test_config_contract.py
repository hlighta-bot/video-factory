#!/usr/bin/env python3
"""config_contract 离线单元测试（纯内存，不读写仓库文件、不触网）。"""
from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from types import MappingProxyType
from pathlib import Path

from pipeline.config_contract import (
    MAX_MILLISECONDS,
    MAX_WORDS_PER_MINUTE,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    ConfigError,
    PipelineConfig,
    config_from_mapping,
    default_config,
)


def _snapshot(root: Path) -> list[str]:
    entries: list[str] = []
    for current, dirs, files in os.walk(root):
        for name in sorted(dirs):
            entries.append("D " + str(Path(current, name).relative_to(root)))
        for name in sorted(files):
            entries.append("F " + str(Path(current, name).relative_to(root)))
    return entries


class _TempDirMixin:
    def make_tempdir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()


# ---------------------------------------------------------------------------
# 1. 默认配置
# ---------------------------------------------------------------------------
class DefaultConfigTests(unittest.TestCase):
    def test_default_values(self) -> None:
        cfg = default_config()
        self.assertEqual(cfg.words_per_minute, 135)
        self.assertEqual(cfg.pause_ms, 300)
        self.assertEqual(cfg.safety_buffer_ms, 500)
        self.assertIsNone(cfg.target_duration_ms)
        self.assertIs(cfg.allow_overwrite, False)

    def test_default_field_types(self) -> None:
        cfg = default_config()
        self.assertIs(type(cfg.words_per_minute), int)
        self.assertIs(type(cfg.pause_ms), int)
        self.assertIs(type(cfg.safety_buffer_ms), int)
        self.assertIs(type(cfg.allow_overwrite), bool)

    def test_default_equals_direct_construction(self) -> None:
        self.assertEqual(default_config(), PipelineConfig(words_per_minute=135))

    def test_default_config_returns_equal_but_distinct_objects(self) -> None:
        first, second = default_config(), default_config()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_config_is_frozen_dataclass(self) -> None:
        cfg = default_config()
        self.assertTrue(dataclasses.is_dataclass(cfg))
        self.assertTrue(getattr(type(cfg), "__dataclass_params__").frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.pause_ms = 1  # type: ignore[misc]
        # slots=True：无 __dict__，未声明属性无处存放
        self.assertFalse(hasattr(cfg, "__dict__"))

    def test_config_has_no_instance_dict(self) -> None:
        self.assertFalse(hasattr(default_config(), "__dict__"))

    def test_declared_field_sets(self) -> None:
        self.assertEqual(REQUIRED_FIELDS, ("words_per_minute",))
        self.assertIn("pause_ms", OPTIONAL_FIELDS)
        self.assertIn("allow_overwrite", OPTIONAL_FIELDS)


# ---------------------------------------------------------------------------
# 2. 合法值：最小值 / 正常值 / 可选字段
# ---------------------------------------------------------------------------
class ValidValueTests(unittest.TestCase):
    def test_minimum_positive_wpm_int(self) -> None:
        self.assertEqual(config_from_mapping({"words_per_minute": 1}).words_per_minute, 1)

    def test_positive_fractional_wpm_allowed(self) -> None:
        cfg = config_from_mapping({"words_per_minute": 0.5})
        self.assertEqual(cfg.words_per_minute, 0.5)
        self.assertIs(type(cfg.words_per_minute), float)

    def test_normal_wpm_values(self) -> None:
        for wpm in (135, 150, 150.0, 120.5):
            with self.subTest(wpm=wpm):
                self.assertEqual(config_from_mapping({"words_per_minute": wpm}).words_per_minute, wpm)

    def test_upper_boundary_wpm_accepted(self) -> None:
        cfg = config_from_mapping({"words_per_minute": MAX_WORDS_PER_MINUTE})
        self.assertEqual(cfg.words_per_minute, MAX_WORDS_PER_MINUTE)

    def test_abnormal_scale_wpm_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": MAX_WORDS_PER_MINUTE + 1})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_RANGE")

    def test_zero_milliseconds_accepted(self) -> None:
        cfg = config_from_mapping(
            {"words_per_minute": 135, "pause_ms": 0, "safety_buffer_ms": 0}
        )
        self.assertEqual(cfg.pause_ms, 0)
        self.assertEqual(cfg.safety_buffer_ms, 0)

    def test_target_duration_set_and_unset(self) -> None:
        self.assertIsNone(config_from_mapping({"words_per_minute": 135}).target_duration_ms)
        self.assertIsNone(
            config_from_mapping({"words_per_minute": 135, "target_duration_ms": None}).target_duration_ms
        )
        cfg = config_from_mapping({"words_per_minute": 135, "target_duration_ms": 30500})
        self.assertEqual(cfg.target_duration_ms, 30500)

    def test_allow_overwrite_true_and_false(self) -> None:
        self.assertIs(
            config_from_mapping({"words_per_minute": 135, "allow_overwrite": True}).allow_overwrite,
            True,
        )
        self.assertIs(
            config_from_mapping({"words_per_minute": 135, "allow_overwrite": False}).allow_overwrite,
            False,
        )

    def test_full_valid_mapping(self) -> None:
        cfg = config_from_mapping(
            {
                "words_per_minute": 140,
                "pause_ms": 250,
                "safety_buffer_ms": 800,
                "target_duration_ms": 60000,
                "allow_overwrite": True,
            }
        )
        self.assertEqual(cfg.to_dict()["target_duration_ms"], 60000)
        self.assertIs(cfg.allow_overwrite, True)

    def test_mapping_proxy_accepted(self) -> None:
        cfg = config_from_mapping(MappingProxyType({"words_per_minute": 135}))
        self.assertEqual(cfg.words_per_minute, 135)

    def test_no_implicit_coercion_of_numeric_values(self) -> None:
        cfg = config_from_mapping({"words_per_minute": 135.0})
        self.assertIs(type(cfg.words_per_minute), float)
        cfg2 = config_from_mapping({"words_per_minute": 135, "pause_ms": 0})
        self.assertIs(type(cfg2.pause_ms), int)


# ---------------------------------------------------------------------------
# 3. 入参类型
# ---------------------------------------------------------------------------
class MappingTypeTests(unittest.TestCase):
    def test_none_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping(None)
        self.assertEqual(ctx.exception.code, "E_CONFIG_NOT_MAPPING")

    def test_non_mapping_rejected(self) -> None:
        for bad in ([], (), "", "words_per_minute=135", 123, 1.5, object(), {135}, b"x"):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping(bad)
                self.assertEqual(ctx.exception.code, "E_CONFIG_NOT_MAPPING")

    def test_non_string_key_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, 7: "x"})
        self.assertEqual(ctx.exception.code, "E_CONFIG_KEY_TYPE")
        self.assertIn("int", ctx.exception.message)

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(ConfigError, ValueError))
        with self.assertRaises(ValueError):
            config_from_mapping({})


# ---------------------------------------------------------------------------
# 4. 字段存在性
# ---------------------------------------------------------------------------
class FieldPresenceTests(unittest.TestCase):
    def test_empty_mapping_missing_required_field(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_MISSING")
        self.assertEqual(ctx.exception.field, "words_per_minute")

    def test_missing_required_field_with_other_fields_present(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"pause_ms": 300, "allow_overwrite": True})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_MISSING")

    def test_unknown_field_rejected_not_ignored(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "wtf": 1})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_UNKNOWN")
        self.assertEqual(ctx.exception.field, "wtf")
        self.assertIn("words_per_minute", ctx.exception.message)

    def test_multiple_unknown_fields_listed(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "aaa": 1, "bbb": 2})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_UNKNOWN")
        self.assertIn("aaa", ctx.exception.message)
        self.assertIn("bbb", ctx.exception.message)

    def test_near_miss_field_name_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "word_per_minute": 1})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_UNKNOWN")


# ---------------------------------------------------------------------------
# 5. bool 与数值的区分
# ---------------------------------------------------------------------------
class BoolVersusIntTests(unittest.TestCase):
    def test_bool_wpm_rejected(self) -> None:
        for bad in (True, False):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": bad})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_BOOL")

    def test_bool_milliseconds_rejected(self) -> None:
        for field in ("pause_ms", "safety_buffer_ms"):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": 135, field: True})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_BOOL")
                self.assertEqual(ctx.exception.field, field)

    def test_bool_target_duration_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "target_duration_ms": True})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_BOOL")

    def test_one_is_not_accepted_as_bool_flag(self) -> None:
        for bad in (0, 1, "true", "false", "", None, 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": 135, "allow_overwrite": bad})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_TYPE")
                self.assertEqual(ctx.exception.field, "allow_overwrite")


# ---------------------------------------------------------------------------
# 6. 数值合法性
# ---------------------------------------------------------------------------
class NumericValidationTests(unittest.TestCase):
    def test_nan_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": float("nan")})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_NON_FINITE")

    def test_positive_and_negative_infinity_rejected(self) -> None:
        for bad in (float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": bad})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_NON_FINITE")

    def test_zero_and_negative_wpm_rejected(self) -> None:
        for bad in (0, 0.0, -0.0, -1, -135, -0.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": bad})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_RANGE")

    def test_non_numeric_wpm_rejected(self) -> None:
        for bad in ("135", None, [135], {"v": 135}, b"135"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": bad})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_TYPE")

    def test_negative_milliseconds_rejected(self) -> None:
        for field in ("pause_ms", "safety_buffer_ms"):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": 135, field: -1})
                self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_RANGE")
                self.assertEqual(ctx.exception.field, field)

    def test_float_milliseconds_rejected(self) -> None:
        for field in ("pause_ms", "safety_buffer_ms"):
            for bad in (300.0, 300.5, float("nan"), float("inf")):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ConfigError) as ctx:
                        config_from_mapping({"words_per_minute": 135, field: bad})
                    self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_TYPE")

    def test_string_milliseconds_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "pause_ms": "300"})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_TYPE")

    def test_milliseconds_over_limit_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping(
                {"words_per_minute": 135, "safety_buffer_ms": MAX_MILLISECONDS + 1}
            )
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_RANGE")

    def test_target_duration_zero_is_illegal_not_unset(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "target_duration_ms": 0})
        self.assertEqual(ctx.exception.code, "E_CONFIG_FIELD_RANGE")
        self.assertIn("None", ctx.exception.message)

    def test_target_duration_negative_and_float_rejected(self) -> None:
        for bad, expected_code in ((-500, "E_CONFIG_FIELD_RANGE"), (500.0, "E_CONFIG_FIELD_TYPE")):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    config_from_mapping({"words_per_minute": 135, "target_duration_ms": bad})
                self.assertEqual(ctx.exception.code, expected_code)

    def test_direct_dataclass_construction_also_validated(self) -> None:
        cases = (
            {"words_per_minute": 0},
            {"words_per_minute": 135, "pause_ms": -1},
            {"words_per_minute": 135, "safety_buffer_ms": True},
            {"words_per_minute": 135, "target_duration_ms": 0},
            {"words_per_minute": 135, "allow_overwrite": 1},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ConfigError):
                    PipelineConfig(**kwargs)


# ---------------------------------------------------------------------------
# 7. 错误契约
# ---------------------------------------------------------------------------
class ErrorContractTests(unittest.TestCase):
    def test_error_exposes_code_field_message(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "pause_ms": -1})
        err = ctx.exception
        self.assertEqual(err.code, "E_CONFIG_FIELD_RANGE")
        self.assertEqual(err.field, "pause_ms")
        self.assertIn("pause_ms", err.message)
        self.assertIn("-1", err.message)

    def test_string_representation_contains_code_and_field(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({})
        text = str(ctx.exception)
        self.assertIn("E_CONFIG_FIELD_MISSING", text)
        self.assertIn("words_per_minute", text)

    def test_as_dict_matches_attributes(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "allow_overwrite": 1})
        err = ctx.exception
        self.assertEqual(
            err.as_dict(),
            {"code": err.code, "field": err.field, "message": err.message},
        )

    def test_error_codes_are_distinct_per_failure_kind(self) -> None:
        cases = [
            (lambda: config_from_mapping(None), "E_CONFIG_NOT_MAPPING"),
            (lambda: config_from_mapping({"words_per_minute": 135, 1: 2}), "E_CONFIG_KEY_TYPE"),
            (lambda: config_from_mapping({}), "E_CONFIG_FIELD_MISSING"),
            (lambda: config_from_mapping({"words_per_minute": 135, "x": 1}), "E_CONFIG_FIELD_UNKNOWN"),
            (lambda: config_from_mapping({"words_per_minute": "135"}), "E_CONFIG_FIELD_TYPE"),
            (lambda: config_from_mapping({"words_per_minute": True}), "E_CONFIG_FIELD_BOOL"),
            (lambda: config_from_mapping({"words_per_minute": 0}), "E_CONFIG_FIELD_RANGE"),
            (lambda: config_from_mapping({"words_per_minute": float("nan")}), "E_CONFIG_FIELD_NON_FINITE"),
        ]
        codes = set()
        for call, expected in cases:
            with self.assertRaises(ConfigError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, expected)
            codes.add(expected)
        self.assertEqual(len(codes), len(cases))

    def test_error_message_has_no_env_or_secret_content(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            config_from_mapping({"words_per_minute": 135, "pause_ms": -1})
        message = ctx.exception.message
        self.assertNotIn("SECRET", message)
        self.assertNotIn(os.sep + "home" + os.sep, message)
        self.assertNotIn("\n", message)


# ---------------------------------------------------------------------------
# 8. 不可变性与外部映射隔离
# ---------------------------------------------------------------------------
class ImmutabilityTests(unittest.TestCase):
    def test_mutating_source_mapping_does_not_change_config(self) -> None:
        source = {"words_per_minute": 135, "pause_ms": 300, "target_duration_ms": 1000}
        cfg = config_from_mapping(source)
        source["words_per_minute"] = 999
        source["pause_ms"] = -5
        source["target_duration_ms"] = None
        source["new_key"] = "x"
        self.assertEqual(cfg.words_per_minute, 135)
        self.assertEqual(cfg.pause_ms, 300)
        self.assertEqual(cfg.target_duration_ms, 1000)

    def test_to_dict_returns_independent_copy(self) -> None:
        cfg = default_config()
        first = cfg.to_dict()
        second = cfg.to_dict()
        self.assertIsNot(first, second)
        first["pause_ms"] = 123456
        self.assertEqual(cfg.pause_ms, 300)
        self.assertEqual(second["pause_ms"], 300)

    def test_to_dict_round_trip(self) -> None:
        cfg = config_from_mapping(
            {"words_per_minute": 128.5, "pause_ms": 0, "safety_buffer_ms": 700,
             "target_duration_ms": 30500, "allow_overwrite": True}
        )
        self.assertEqual(config_from_mapping(cfg.to_dict()), cfg)

    def test_to_dict_keys_match_declared_fields(self) -> None:
        self.assertEqual(
            list(default_config().to_dict().keys()),
            ["words_per_minute", "pause_ms", "safety_buffer_ms", "target_duration_ms", "allow_overwrite"],
        )


# ---------------------------------------------------------------------------
# 9. 无副作用
# ---------------------------------------------------------------------------
class NoSideEffectTests(_TempDirMixin, unittest.TestCase):
    def test_calls_create_no_files_or_directories(self) -> None:
        root = self.make_tempdir()
        before = _snapshot(root)
        cfg = default_config()
        config_from_mapping(cfg.to_dict())
        try:
            config_from_mapping({"words_per_minute": 135, "pause_ms": -1})
        except ConfigError:
            pass
        PipelineConfig(words_per_minute=200, target_duration_ms=1000)
        self.assertEqual(_snapshot(root), before)

    def test_engine_tree_untouched_by_import_and_construction(self) -> None:
        engine = Path(__file__).resolve().parents[1]
        pipeline_dir = engine / "scripts" / "pipeline"
        before = sorted(p.name for p in pipeline_dir.iterdir())
        default_config()
        config_from_mapping({"words_per_minute": 135})
        after = sorted(p.name for p in pipeline_dir.iterdir())
        self.assertEqual(after, before)

    def test_module_exposes_no_path_or_env_helpers(self) -> None:
        import pipeline.config_contract as module

        for forbidden in ("load_env", "project_root", "read_config", "from_file", "from_env"):
            self.assertFalse(hasattr(module, forbidden), forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
