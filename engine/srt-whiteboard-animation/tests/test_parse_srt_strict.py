#!/usr/bin/env python3
"""严格 SRT 解析器离线单元测试（仅标准库 unittest，不触碰生产流水线）。

运行：
    PYTHONPATH=scripts .venv/bin/python tests/test_parse_srt_strict.py -v
    PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests \
        -p "test_parse_srt_strict.py" -v
"""
from __future__ import annotations

import unittest

from pipeline.parse_srt_strict import (
    E_BAD_INDEX,
    E_BAD_TIMESTAMP,
    E_EMPTY_INPUT,
    E_EMPTY_TEXT,
    E_END_NOT_AFTER_START,
    E_INDEX_DUPLICATE,
    E_INDEX_NOT_SEQUENTIAL,
    E_INPUT_TYPE,
    E_MISSING_INDEX,
    E_MISSING_TIMESTAMP,
    E_OVERLAP,
    E_TIME_OUT_OF_RANGE,
    E_TIME_REGRESSION,
    E_UNRECOGNIZED_BLOCK,
    E_UNSUPPORTED_FORMAT,
    E_UNSUPPORTED_MARKUP,
    SRT_ERROR_CODES,
    SrtParseError,
    SubtitleCue,
    parse_srt_strict,
)

SIMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:03,500\n"
    "Hello\n"
    "\n"
    "2\n"
    "00:00:04,000 --> 00:00:06,250\n"
    "World\n"
)


class StrictSrtTestBase(unittest.TestCase):
    def assert_error(self, text, code, *, cue_number=None, **kwargs):
        with self.assertRaises(SrtParseError) as ctx:
            parse_srt_strict(text, **kwargs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        self.assertIn(ctx.exception.code, SRT_ERROR_CODES)
        if cue_number is not None:
            self.assertEqual(ctx.exception.cue_number, cue_number, str(ctx.exception))
        return ctx.exception


class TestHappyPath(StrictSrtTestBase):
    def test_single_cue(self):
        cues = parse_srt_strict("1\n00:00:00,000 --> 00:00:02,000\nOnly line\n")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0], SubtitleCue(1, 0, 2000, "Only line"))
        self.assertIsInstance(cues[0].start_ms, int)
        self.assertIsInstance(cues[0].end_ms, int)
        self.assertEqual(cues[0].duration_ms, 2000)

    def test_multiple_cues(self):
        cues = parse_srt_strict(SIMPLE)
        self.assertEqual([c.index for c in cues], [1, 2])
        self.assertEqual([c.start_ms for c in cues], [1000, 4000])
        self.assertEqual([c.end_ms for c in cues], [3500, 6250])
        self.assertEqual([c.text for c in cues], ["Hello", "World"])

    def test_lf_newlines(self):
        cues = parse_srt_strict(SIMPLE.replace("\r\n", "\n"))
        self.assertEqual(len(cues), 2)

    def test_crlf_newlines_not_merged(self):
        cues = parse_srt_strict(SIMPLE.replace("\n", "\r\n"))
        self.assertEqual(len(cues), 2, "CRLF 不得把整篇合并成一条 cue")
        self.assertEqual(cues[1].text, "World")

    def test_utf8_bom(self):
        cues = parse_srt_strict("\ufeff" + SIMPLE)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].index, 1)

    def test_multiple_blank_lines_between_cues(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n\n   \n\t\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        cues = parse_srt_strict(text)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].index, 2)

    def test_multiline_body_preserved(self):
        text = "1\n00:00:00,000 --> 00:00:02,000\nline one\nline two\nline three\n"
        cues = parse_srt_strict(text)
        self.assertEqual(cues[0].text, "line one\nline two\nline three")

    def test_non_ascii_mixed_text(self):
        text = (
            "1\n00:00:00,000 --> 00:00:02,000\n今天 weather 很好\n"
            "\n"
            "2\n00:00:02,000 --> 00:00:03,000\n日本語テスト\n"
        )
        cues = parse_srt_strict(text)
        self.assertEqual(cues[0].text, "今天 weather 很好")
        self.assertEqual(cues[1].text, "日本語テスト")

    def test_no_trailing_newline(self):
        cues = parse_srt_strict("1\n00:00:00,000 --> 00:00:01,500\nlast")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "last")

    def test_leading_and_trailing_whitespace_ignored(self):
        cues = parse_srt_strict("\n\n  " + SIMPLE + "  \n\n")
        self.assertEqual(len(cues), 2)

    def test_hours_above_two_digits(self):
        cues = parse_srt_strict("1\n100:00:00,000 --> 100:00:01,000\nlong\n")
        self.assertEqual(cues[0].start_ms, 360000000)

    def test_adjacent_cues_touching_not_overlap(self):
        text = (
            "1\n00:00:00,000 --> 00:00:02,000\nA\n"
            "\n"
            "2\n00:00:02,000 --> 00:00:03,000\nB\n"
        )
        cues = parse_srt_strict(text)
        self.assertEqual(len(cues), 2)


class TestTimestampErrors(StrictSrtTestBase):
    def test_bad_timestamp_dot_separator(self):
        self.assert_error(
            "1\n00:00:00.000 --> 00:00:02.000\nA\n", E_BAD_TIMESTAMP, cue_number=1
        )

    def test_bad_timestamp_missing_millis(self):
        self.assert_error("1\n00:00:00 --> 00:00:02\nA\n", E_BAD_TIMESTAMP, cue_number=1)

    def test_bad_timestamp_extra_settings(self):
        self.assert_error(
            "1\n00:00:00,000 --> 00:00:02,000 X1:10 X2:20\nA\n",
            E_BAD_TIMESTAMP,
            cue_number=1,
        )

    def test_minutes_out_of_range(self):
        self.assert_error(
            "1\n00:60:00,000 --> 00:61:00,000\nA\n", E_TIME_OUT_OF_RANGE, cue_number=1
        )

    def test_seconds_out_of_range(self):
        self.assert_error(
            "1\n00:00:60,000 --> 00:01:10,000\nA\n", E_TIME_OUT_OF_RANGE, cue_number=1
        )

    def test_end_before_start(self):
        self.assert_error(
            "1\n00:00:05,000 --> 00:00:03,000\nA\n",
            E_END_NOT_AFTER_START,
            cue_number=1,
        )

    def test_end_equals_start(self):
        self.assert_error(
            "1\n00:00:05,000 --> 00:00:05,000\nA\n",
            E_END_NOT_AFTER_START,
            cue_number=1,
        )

    def test_missing_timestamp_block(self):
        self.assert_error("1\nJust text\n", E_MISSING_TIMESTAMP, cue_number=1)

    def test_missing_timestamp_single_line_block(self):
        self.assert_error("1\n", E_MISSING_TIMESTAMP, cue_number=1)


class TestIndexErrors(StrictSrtTestBase):
    def test_zero_index(self):
        self.assert_error("0\n00:00:00,000 --> 00:00:01,000\nA\n", E_BAD_INDEX)

    def test_duplicate_index(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "1\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(text, E_INDEX_DUPLICATE, cue_number=1)

    def test_non_sequential_index(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "3\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(text, E_INDEX_NOT_SEQUENTIAL, cue_number=3)

    def test_index_not_starting_at_one(self):
        self.assert_error(
            "5\n00:00:00,000 --> 00:00:01,000\nA\n",
            E_INDEX_NOT_SEQUENTIAL,
            cue_number=5,
        )

    def test_non_sequential_allowed_when_disabled(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "7\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        cues = parse_srt_strict(text, require_sequential_indices=False)
        self.assertEqual([c.index for c in cues], [1, 7])

    def test_duplicate_still_rejected_when_sequential_disabled(self):
        text = (
            "2\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(
            text, E_INDEX_DUPLICATE, cue_number=2, require_sequential_indices=False
        )

    def test_missing_index(self):
        self.assert_error("00:00:00,000 --> 00:00:01,000\nA\n", E_MISSING_INDEX)


class TestBodyErrors(StrictSrtTestBase):
    def test_empty_body(self):
        self.assert_error("1\n00:00:00,000 --> 00:00:01,000\n\n", E_EMPTY_TEXT)

    def test_whitespace_only_body(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\n   \n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(text, E_EMPTY_TEXT, cue_number=1)

    def test_missing_body(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(text, E_EMPTY_TEXT, cue_number=1)


class TestOrderingAndOverlap(StrictSrtTestBase):
    def test_time_regression(self):
        text = (
            "1\n00:00:10,000 --> 00:00:12,000\nA\n"
            "\n"
            "2\n00:00:05,000 --> 00:00:06,000\nB\n"
        )
        self.assert_error(text, E_TIME_REGRESSION, cue_number=2)

    def test_overlap_rejected_by_default(self):
        text = (
            "1\n00:00:00,000 --> 00:00:05,000\nA\n"
            "\n"
            "2\n00:00:04,000 --> 00:00:06,000\nB\n"
        )
        self.assert_error(text, E_OVERLAP, cue_number=2)

    def test_overlap_allowed_explicitly(self):
        text = (
            "1\n00:00:00,000 --> 00:00:05,000\nA\n"
            "\n"
            "2\n00:00:04,000 --> 00:00:06,000\nB\n"
        )
        cues = parse_srt_strict(text, allow_overlapping_cues=True)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].start_ms, 4000)


class TestInputAndFormatErrors(StrictSrtTestBase):
    def test_whitespace_only_input(self):
        self.assert_error("   \n\t\n\n", E_EMPTY_INPUT)

    def test_empty_string_input(self):
        self.assert_error("", E_EMPTY_INPUT)

    def test_non_string_input_bytes(self):
        self.assert_error(b"1\n00:00:00,000 --> 00:00:01,000\nA\n", E_INPUT_TYPE)

    def test_non_string_input_none(self):
        self.assert_error(None, E_INPUT_TYPE)

    def test_webvtt_rejected(self):
        self.assert_error(
            "WEBVTT\n\n1\n00:00:00,000 --> 00:00:01,000\nA\n", E_UNSUPPORTED_FORMAT
        )

    def test_html_markup_rejected(self):
        self.assert_error(
            "1\n00:00:00,000 --> 00:00:01,000\n<i>A</i>\n",
            E_UNSUPPORTED_MARKUP,
            cue_number=1,
        )

    def test_ass_override_tag_rejected(self):
        self.assert_error(
            "1\n00:00:00,000 --> 00:00:01,000\n{\\an8}A\n",
            E_UNSUPPORTED_MARKUP,
            cue_number=1,
        )

    def test_unrecognized_block(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "GARBAGE\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
        )
        self.assert_error(text, E_UNRECOGNIZED_BLOCK)


class TestNoPartialResult(StrictSrtTestBase):
    def test_failure_at_third_cue_returns_nothing(self):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nA\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nB\n"
            "\n"
            "3\n00:00:0x,000 --> 00:00:03,000\nC\n"
        )
        with self.assertRaises(SrtParseError) as ctx:
            result = parse_srt_strict(text)
            self.fail("不应返回部分结果：{}".format(result))
        self.assertEqual(ctx.exception.code, E_BAD_TIMESTAMP)
        self.assertEqual(ctx.exception.cue_number, 3)

    def test_error_payload_is_structured(self):
        with self.assertRaises(SrtParseError) as ctx:
            parse_srt_strict("1\n00:00:00,000 --> 00:00:00,000\nA\n")
        payload = ctx.exception.as_dict()
        self.assertEqual(payload["code"], E_END_NOT_AFTER_START)
        self.assertEqual(payload["cue_number"], 1)
        self.assertEqual(payload["block_number"], 1)
        self.assertIn("结束时间", payload["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
