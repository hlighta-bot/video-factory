#!/usr/bin/env python3
"""统一时长预算模块离线单元测试（标准库 unittest，离线、确定性）。

运行：
    PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests \
        -p "test_duration_budget.py" -v
    PYTHONPATH=scripts .venv/bin/python tests/test_duration_budget.py
"""
from __future__ import annotations

import dataclasses
import unittest

from pipeline.duration_budget import (
    DURATION_STATUSES,
    MAX_DURATION_MS,
    STATUS_LOW_BUFFER,
    STATUS_NO_TARGET,
    STATUS_OVER_TARGET,
    STATUS_PASS,
    DurationBudget,
    DurationBudgetError,
    calculate_duration_budget,
)


def budget(**kwargs):
    return calculate_duration_budget(**kwargs)


class TestSpeechDuration(unittest.TestCase):
    def test_zero_words_zero_pause(self):
        b = budget(word_count=0, words_per_minute=145, pause_ms=0)
        self.assertEqual(b.speech_duration_ms, 0)
        self.assertEqual(b.estimated_duration_ms, 0)
        self.assertEqual(b.status, STATUS_NO_TARGET)

    def test_135_wpm(self):
        b = budget(word_count=59, words_per_minute=135)
        self.assertEqual(b.speech_duration_ms, 26223)  # 26222.22 -> ceil
        self.assertEqual(b.estimated_duration_ms, 26223)

    def test_145_wpm(self):
        b = budget(word_count=59, words_per_minute=145)
        self.assertEqual(b.speech_duration_ms, 24414)  # 24413.79 -> ceil

    def test_155_wpm(self):
        b = budget(word_count=59, words_per_minute=155)
        self.assertEqual(b.speech_duration_ms, 22839)  # 22838.71 -> ceil

    def test_faster_wpm_yields_shorter_speech(self):
        slow = budget(word_count=65, words_per_minute=135)
        fast = budget(word_count=65, words_per_minute=155)
        self.assertLess(fast.speech_duration_ms, slow.speech_duration_ms)

    def test_pauses_accumulated_into_estimated(self):
        b = budget(word_count=59, words_per_minute=145, pause_ms=150 + 250 + 120)
        self.assertEqual(b.pause_ms, 520)
        self.assertEqual(b.estimated_duration_ms, b.speech_duration_ms + 520)

    def test_exact_division_is_not_rounded_up(self):
        b = budget(word_count=90, words_per_minute=180)
        self.assertEqual(b.speech_duration_ms, 30000)  # 恰好 30000 ms

    def test_rounding_is_ceil_not_truncation(self):
        one_word = budget(word_count=1, words_per_minute=135)
        self.assertEqual(one_word.speech_duration_ms, 445)  # 444.44 -> 445
        four_words = budget(word_count=4, words_per_minute=145)
        self.assertEqual(four_words.speech_duration_ms, 1656)  # 1655.17 -> 1656

    def test_float_wpm_uses_decimal_not_binary_float(self):
        b = budget(word_count=30, words_per_minute=137.5)
        self.assertEqual(b.speech_duration_ms, 13091)  # 13090.90... -> 13091

    def test_integer_wpm_accepted(self):
        b = budget(word_count=59, words_per_minute=145)
        self.assertEqual(b.words_per_minute, 145)
        self.assertEqual(b.speech_duration_ms, 24414)


class TestTargetAndStatus(unittest.TestCase):
    def test_no_target_reports_only_estimate(self):
        b = budget(word_count=59, words_per_minute=145, pause_ms=3400)
        self.assertEqual(b.status, STATUS_NO_TARGET)
        self.assertIsNone(b.slack_ms)
        self.assertIsNone(b.target_duration_ms)
        self.assertEqual(b.overrun_ms, 0)
        self.assertFalse(b.has_target)

    def test_safety_buffer_ignored_without_target(self):
        b = budget(word_count=10, words_per_minute=145, safety_buffer_ms=99999)
        self.assertEqual(b.status, STATUS_NO_TARGET)
        self.assertIsNone(b.slack_ms)
        self.assertEqual(b.safety_buffer_ms, 99999)

    def test_exactly_hits_target(self):
        # 145 词 @60wpm = 145000 ms，正好等于目标
        b = budget(word_count=145, words_per_minute=60, target_duration_ms=145000)
        self.assertEqual(b.slack_ms, 0)
        self.assertEqual(b.status, STATUS_PASS)
        self.assertEqual(b.overrun_ms, 0)
        self.assertTrue(b.is_pass)

    def test_ample_buffer_passes(self):
        b = budget(
            word_count=59,
            words_per_minute=145,
            pause_ms=3400,
            target_duration_ms=30500,
            safety_buffer_ms=500,
        )
        self.assertEqual(b.estimated_duration_ms, 27814)
        self.assertEqual(b.slack_ms, 2686)
        self.assertEqual(b.status, STATUS_PASS)

    def test_slack_exactly_equals_minimum_buffer_passes(self):
        # speech 30000，target 30500，safety 500 -> slack == safety -> PASS
        b = budget(
            word_count=90,
            words_per_minute=180,
            target_duration_ms=30500,
            safety_buffer_ms=500,
        )
        self.assertEqual(b.slack_ms, 500)
        self.assertEqual(b.status, STATUS_PASS)

    def test_slack_below_minimum_buffer_is_low_buffer(self):
        b = budget(
            word_count=90,
            words_per_minute=180,
            target_duration_ms=30500,
            safety_buffer_ms=1000,
        )
        self.assertEqual(b.slack_ms, 500)
        self.assertEqual(b.status, STATUS_LOW_BUFFER)
        self.assertFalse(b.is_pass, "LOW_BUFFER 不得被视为通过")
        self.assertEqual(b.overrun_ms, 0)

    def test_over_target_marks_overrun(self):
        b = budget(
            word_count=90,
            words_per_minute=180,
            target_duration_ms=29000,
        )
        self.assertEqual(b.slack_ms, -1000)
        self.assertEqual(b.overrun_ms, 1000)
        self.assertEqual(b.status, STATUS_OVER_TARGET)

    def test_over_target_wins_even_with_safety_buffer(self):
        b = budget(
            word_count=90,
            words_per_minute=180,
            target_duration_ms=29000,
            safety_buffer_ms=5000,
        )
        self.assertEqual(b.status, STATUS_OVER_TARGET)
        self.assertEqual(b.overrun_ms, 1000)

    def test_overrun_positive_only_when_over(self):
        over = budget(word_count=90, words_per_minute=180, target_duration_ms=29000)
        low = budget(
            word_count=90, words_per_minute=180, target_duration_ms=30500,
            safety_buffer_ms=1000,
        )
        passed = budget(word_count=90, words_per_minute=180, target_duration_ms=40000)
        self.assertGreater(over.overrun_ms, 0)
        self.assertEqual(low.overrun_ms, 0)
        self.assertEqual(passed.overrun_ms, 0)

    def test_status_in_known_set(self):
        samples = [
            budget(word_count=1, words_per_minute=145),
            budget(word_count=1, words_per_minute=145, target_duration_ms=10000),
            budget(word_count=1, words_per_minute=145, target_duration_ms=10000,
                   safety_buffer_ms=99999),
            budget(word_count=90, words_per_minute=180, target_duration_ms=1000),
        ]
        for sample in samples:
            self.assertIn(sample.status, DURATION_STATUSES)
            self.assertIsInstance(sample.status, str)


class TestConsistencyAndImmutability(unittest.TestCase):
    def test_units_and_arithmetic_consistency(self):
        b = budget(
            word_count=59,
            words_per_minute=145,
            pause_ms=3400,
            target_duration_ms=30500,
            safety_buffer_ms=500,
        )
        for name in (
            "word_count",
            "speech_duration_ms",
            "pause_ms",
            "estimated_duration_ms",
            "target_duration_ms",
            "slack_ms",
            "safety_buffer_ms",
            "overrun_ms",
        ):
            self.assertIsInstance(getattr(b, name), int, name)
        self.assertEqual(b.estimated_duration_ms, b.speech_duration_ms + b.pause_ms)
        self.assertEqual(b.slack_ms, b.target_duration_ms - b.estimated_duration_ms)
        self.assertEqual(b.overrun_ms, max(0, -b.slack_ms))

    def test_result_is_frozen(self):
        b = budget(word_count=10, words_per_minute=145)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            b.speech_duration_ms = 1  # type: ignore[misc]

    def test_no_shared_mutable_state_between_calls(self):
        first = budget(word_count=10, words_per_minute=145)
        second = budget(word_count=10, words_per_minute=145)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_same_input_same_output_is_deterministic(self):
        def once():
            return budget(
                word_count=77, words_per_minute=143.5, pause_ms=1234,
                target_duration_ms=28000, safety_buffer_ms=250,
            )

        self.assertEqual(once(), once())


class TestInvalidInputs(unittest.TestCase):
    def assert_invalid(self, field, **kwargs):
        with self.assertRaises(DurationBudgetError) as ctx:
            calculate_duration_budget(**kwargs)
        self.assertEqual(ctx.exception.field, field, str(ctx.exception))
        self.assertTrue(str(ctx.exception))

    def test_negative_word_count(self):
        self.assert_invalid("word_count", word_count=-1, words_per_minute=145)

    def test_negative_pause(self):
        self.assert_invalid("pause_ms", word_count=10, words_per_minute=145, pause_ms=-1)

    def test_negative_safety_buffer(self):
        self.assert_invalid(
            "safety_buffer_ms", word_count=10, words_per_minute=145,
            target_duration_ms=10000, safety_buffer_ms=-1,
        )

    def test_zero_wpm(self):
        self.assert_invalid("words_per_minute", word_count=10, words_per_minute=0)

    def test_negative_wpm(self):
        self.assert_invalid("words_per_minute", word_count=10, words_per_minute=-145)

    def test_nan_wpm(self):
        self.assert_invalid(
            "words_per_minute", word_count=10, words_per_minute=float("nan")
        )

    def test_infinite_wpm(self):
        self.assert_invalid(
            "words_per_minute", word_count=10, words_per_minute=float("inf")
        )
        self.assert_invalid(
            "words_per_minute", word_count=10, words_per_minute=float("-inf")
        )

    def test_zero_target(self):
        self.assert_invalid(
            "target_duration_ms", word_count=10, words_per_minute=145,
            target_duration_ms=0,
        )

    def test_negative_target(self):
        self.assert_invalid(
            "target_duration_ms", word_count=10, words_per_minute=145,
            target_duration_ms=-30500,
        )

    def test_float_target_rejected(self):
        self.assert_invalid(
            "target_duration_ms", word_count=10, words_per_minute=145,
            target_duration_ms=30500.5,
        )

    def test_bool_masquerading_as_int(self):
        self.assert_invalid("word_count", word_count=True, words_per_minute=145)
        self.assert_invalid(
            "pause_ms", word_count=10, words_per_minute=145, pause_ms=False
        )
        self.assert_invalid(
            "safety_buffer_ms", word_count=10, words_per_minute=145,
            target_duration_ms=10000, safety_buffer_ms=True,
        )
        self.assert_invalid(
            "target_duration_ms", word_count=10, words_per_minute=145,
            target_duration_ms=True,
        )

    def test_non_numeric_types(self):
        self.assert_invalid("word_count", word_count="59", words_per_minute=145)
        self.assert_invalid("word_count", word_count=None, words_per_minute=145)
        self.assert_invalid("words_per_minute", word_count=10, words_per_minute="145")
        self.assert_invalid("words_per_minute", word_count=10, words_per_minute=None)
        self.assert_invalid(
            "pause_ms", word_count=10, words_per_minute=145, pause_ms="0"
        )

    def test_out_of_range_duration(self):
        with self.assertRaises(DurationBudgetError) as ctx:
            calculate_duration_budget(
                word_count=10 ** 9, words_per_minute=1
            )
        self.assertEqual(ctx.exception.field, "word_count")

    def test_target_above_max_rejected(self):
        self.assert_invalid(
            "target_duration_ms", word_count=10, words_per_minute=145,
            target_duration_ms=MAX_DURATION_MS + 1,
        )

    def test_error_is_value_error_subclass(self):
        with self.assertRaises(ValueError):
            calculate_duration_budget(word_count=-1, words_per_minute=145)


if __name__ == "__main__":
    unittest.main(verbosity=2)
