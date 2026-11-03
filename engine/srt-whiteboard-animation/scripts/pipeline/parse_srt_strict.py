#!/usr/bin/env python3
"""严格 SRT 解析与校验（仅标准库，不接入现有流水线）。

职责单一：把 SRT 文本解析为 SubtitleCue 列表，任何格式问题都以
SrtParseError（含错误代码、cue 序号/块号、可读描述）显式抛出，
不静默跳过坏块、不返回部分成功结果。

支持的 SRT 子集（故意保持窄口径）：
  - 换行：LF / CRLF（\r\n）/ 单独 \r 统一归一为 LF；
  - 编码：可选 UTF-8 BOM（仅文件开头）；
  - 块结构：序号行 + 时间戳行 + 至少一行非空正文；
  - 时间戳：HH:MM:SS,mmm --> HH:MM:SS,mmm（小时 2 位以上，
    分/秒 00-59，毫秒固定 3 位，逗号分隔）；
  - cue 之间允许任意多个空行（空行可含空白字符）；
  - 正文内部换行原样保留。

明确不支持并要求显式报错：WebVTT、ASS/SSA、HTML/ASS 标签、
时间戳行附带坐标或设置项等变体。

用法示例：
    from pipeline.parse_srt_strict import parse_srt_strict, SrtParseError

    cues = parse_srt_strict(open("a.srt", encoding="utf-8-sig").read())
    try:
        cues = parse_srt_strict(text)
    except SrtParseError as exc:
        print(exc.code, exc.cue_number, str(exc))
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "SubtitleCue",
    "SrtParseError",
    "SRT_ERROR_CODES",
    "parse_srt_strict",
]

# ---------------------------------------------------------------------------
# 错误代码
# ---------------------------------------------------------------------------
E_INPUT_TYPE = "E_INPUT_TYPE"
E_EMPTY_INPUT = "E_EMPTY_INPUT"
E_UNSUPPORTED_FORMAT = "E_UNSUPPORTED_FORMAT"
E_UNSUPPORTED_MARKUP = "E_UNSUPPORTED_MARKUP"
E_UNRECOGNIZED_BLOCK = "E_UNRECOGNIZED_BLOCK"
E_MISSING_INDEX = "E_MISSING_INDEX"
E_BAD_INDEX = "E_BAD_INDEX"
E_INDEX_DUPLICATE = "E_INDEX_DUPLICATE"
E_INDEX_NOT_SEQUENTIAL = "E_INDEX_NOT_SEQUENTIAL"
E_MISSING_TIMESTAMP = "E_MISSING_TIMESTAMP"
E_BAD_TIMESTAMP = "E_BAD_TIMESTAMP"
E_TIME_OUT_OF_RANGE = "E_TIME_OUT_OF_RANGE"
E_END_NOT_AFTER_START = "E_END_NOT_AFTER_START"
E_EMPTY_TEXT = "E_EMPTY_TEXT"
E_TIME_REGRESSION = "E_TIME_REGRESSION"
E_OVERLAP = "E_OVERLAP"

SRT_ERROR_CODES = frozenset(
    {
        E_INPUT_TYPE,
        E_EMPTY_INPUT,
        E_UNSUPPORTED_FORMAT,
        E_UNSUPPORTED_MARKUP,
        E_UNRECOGNIZED_BLOCK,
        E_MISSING_INDEX,
        E_BAD_INDEX,
        E_INDEX_DUPLICATE,
        E_INDEX_NOT_SEQUENTIAL,
        E_MISSING_TIMESTAMP,
        E_BAD_TIMESTAMP,
        E_TIME_OUT_OF_RANGE,
        E_END_NOT_AFTER_START,
        E_EMPTY_TEXT,
        E_TIME_REGRESSION,
        E_OVERLAP,
    }
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
_TS = r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})"
_TIME_LINE = re.compile(
    r"^" + _TS + r"[ \t]*-->[ \t]*" + _TS + r"$"
)
_INDEX_LINE = re.compile(r"^\d+$")
_MARKUP = re.compile(r"<[^<>]*>|\{\\[^{}]*\}")


@dataclass(frozen=True)
class SubtitleCue:
    """单条字幕。时间一律为整数毫秒，内部不使用浮点数。"""

    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or self.index <= 0:
            raise ValueError("SubtitleCue.index 必须为正整数")
        if not isinstance(self.start_ms, int) or not isinstance(self.end_ms, int):
            raise ValueError("SubtitleCue 时间必须为整数毫秒")
        if self.end_ms <= self.start_ms:
            raise ValueError("SubtitleCue.end_ms 必须严格大于 start_ms")


class SrtParseError(ValueError):
    """SRT 解析/校验失败。code 用于程序判定，message 供人阅读。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        cue_number: int | None = None,
        block_number: int | None = None,
        line_number: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.cue_number = cue_number
        self.block_number = block_number
        self.line_number = line_number
        super().__init__(self.__str__())

    def __str__(self) -> str:
        where = []
        if self.cue_number is not None:
            where.append("cue {}".format(self.cue_number))
        if self.block_number is not None:
            where.append("block {}".format(self.block_number))
        if self.line_number is not None:
            where.append("line {}".format(self.line_number))
        prefix = "[{}]".format(self.code)
        if where:
            prefix = "{} ({})".format(prefix, ", ".join(where))
        return "{} {}".format(prefix, self.message)

    def as_dict(self) -> dict:
        """结构化错误信息，便于上游日志/接口使用。"""
        return {
            "code": self.code,
            "message": self.message,
            "cue_number": self.cue_number,
            "block_number": self.block_number,
            "line_number": self.line_number,
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    if not isinstance(text, str):
        raise SrtParseError(
            E_INPUT_TYPE,
            "输入必须是 str，实际为 {}".format(type(text).__name__),
        )
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\x00" in text:
        raise SrtParseError(E_UNSUPPORTED_FORMAT, "输入包含 NUL 字符，疑似二进制内容")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _fail(
    code: str,
    message: str,
    *,
    cue_number: int | None = None,
    block_number: int | None = None,
    line_number: int | None = None,
) -> None:
    raise SrtParseError(
        code,
        message,
        cue_number=cue_number,
        block_number=block_number,
        line_number=line_number,
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def parse_srt_strict(
    text: str,
    *,
    require_sequential_indices: bool = True,
    allow_overlapping_cues: bool = False,
) -> list[SubtitleCue]:
    """严格解析 SRT 文本。

    Args:
        text: SRT 原文（允许 LB/CRLF、可选 BOM、首尾空白）。
        require_sequential_indices: 为 True 时要求序号从 1 开始连续递增；
            为 False 时允许任意正整数序号，但重复序号仍视为错误。
        allow_overlapping_cues: 为 True 时允许时间区间重叠/起点回退；
            默认拒绝。

    Returns:
        list[SubtitleCue]：按文件顺序排列，时间均为整数毫秒。

    Raises:
        SrtParseError: 任一校验失败。失败时不返回部分结果。
    """
    text = _normalize(text)
    if text.strip() == "":
        _fail(E_EMPTY_INPUT, "输入为空或仅含空白字符")

    blocks = _split_blocks(text)
    if not blocks:
        _fail(E_EMPTY_INPUT, "输入不含任何 cue 块")

    first_line = blocks[0].split("\n", 1)[0].strip().upper()
    if first_line.startswith("WEBVTT"):
        _fail(
            E_UNSUPPORTED_FORMAT,
            "检测到 WebVTT 头，本模块仅支持 SRT",
            block_number=1,
            line_number=1,
        )

    cues: list[SubtitleCue] = []
    seen_indices: set[int] = set()
    prev_cue: SubtitleCue | None = None

    for block_number, block in enumerate(blocks, start=1):
        lines = block.split("\n")

        # --- 序号 ---
        head = lines[0].strip()
        if not _INDEX_LINE.match(head):
            if any("-->" in line for line in lines):
                _fail(
                    E_MISSING_INDEX,
                    "cue 缺少数字序号（首行不是序号，但块内含时间戳行）",
                    cue_number=len(cues) + 1,
                    block_number=block_number,
                )
            _fail(
                E_UNRECOGNIZED_BLOCK,
                "无法识别的非空文本块：{}".format(head[:40]),
                block_number=block_number,
            )

        index = int(head)
        if index <= 0:
            _fail(
                E_BAD_INDEX,
                "序号必须为正整数，实际为 {}".format(index),
                block_number=block_number,
            )
        if index in seen_indices:
            _fail(
                E_INDEX_DUPLICATE,
                "序号重复：{}".format(index),
                cue_number=index,
                block_number=block_number,
            )
        if require_sequential_indices:
            expected = len(cues) + 1
            if index != expected:
                _fail(
                    E_INDEX_NOT_SEQUENTIAL,
                    "序号不连续：期望 {}，实际 {}".format(expected, index),
                    cue_number=index,
                    block_number=block_number,
                )
        seen_indices.add(index)

        # --- 时间戳行 ---
        if len(lines) < 2:
            _fail(
                E_MISSING_TIMESTAMP,
                "cue {} 缺少时间戳行".format(index),
                cue_number=index,
                block_number=block_number,
            )
        ts_line = lines[1].strip()
        if "-->" not in ts_line:
            _fail(
                E_MISSING_TIMESTAMP,
                "cue {} 第二行不是时间戳行".format(index),
                cue_number=index,
                block_number=block_number,
            )
        match = _TIME_LINE.match(ts_line)
        if match is None:
            _fail(
                E_BAD_TIMESTAMP,
                "时间戳格式非法（应为 HH:MM:SS,mmm --> HH:MM:SS,mmm）：{}".format(
                    ts_line[:60]
                ),
                cue_number=index,
                block_number=block_number,
            )

        groups = match.groups()
        # groups: (h1, m1, s1, ms1, h2, m2, s2, ms2)；分、秒必须落在 00-59
        for pos in (1, 2, 5, 6):
            if int(groups[pos]) > 59:
                _fail(
                    E_TIME_OUT_OF_RANGE,
                    "时间戳分/秒越界（{}）：{}".format(groups[pos], ts_line[:60]),
                    cue_number=index,
                    block_number=block_number,
                )
        start_ms = _to_ms(*groups[0:4])
        end_ms = _to_ms(*groups[4:8])
        if end_ms <= start_ms:
            _fail(
                E_END_NOT_AFTER_START,
                "结束时间必须严格晚于开始时间（{} <= {}）".format(end_ms, start_ms),
                cue_number=index,
                block_number=block_number,
            )

        # --- 正文 ---
        body_lines = lines[2:]
        body = "\n".join(body_lines).strip("\n")
        if body.strip() == "":
            _fail(
                E_EMPTY_TEXT,
                "cue {} 没有有效字幕正文".format(index),
                cue_number=index,
                block_number=block_number,
            )
        if _MARKUP.search(body):
            _fail(
                E_UNSUPPORTED_MARKUP,
                "正文包含 HTML/ASS 标记，本模块不解析格式标签",
                cue_number=index,
                block_number=block_number,
            )

        # --- 时间顺序 / 重叠 ---
        if prev_cue is not None and not allow_overlapping_cues:
            if start_ms < prev_cue.start_ms:
                _fail(
                    E_TIME_REGRESSION,
                    "时间顺序倒退：起点 {} < 上一条起点 {}".format(
                        start_ms, prev_cue.start_ms
                    ),
                    cue_number=index,
                    block_number=block_number,
                )
            if start_ms < prev_cue.end_ms:
                _fail(
                    E_OVERLAP,
                    "时间区间重叠：起点 {} < 上一条结束 {}".format(
                        start_ms, prev_cue.end_ms
                    ),
                    cue_number=index,
                    block_number=block_number,
                )

        cue = SubtitleCue(index=index, start_ms=start_ms, end_ms=end_ms, text=body)
        cues.append(cue)
        prev_cue = cue

    return cues
