#!/usr/bin/env python3
"""统一时长预算（旁白预计时长 / 停顿 / 目标时长 / 安全缓冲），仅标准库。

计算契约
--------
单位：一律整数毫秒（ms），对外字段名以 `_ms` 结尾。
旁白预计时长（speech_duration_ms）::

    speech_duration_ms = ceil(word_count / words_per_minute * 60_000)

舍入：**向上取整（ceil）**，用 Decimal + ROUND_CEILING 精确计算，
不经二进制浮点，保证同一输入在任何平台得到同一位整数。
0 词时恰为 0 ms。

总预计时长（estimated_duration_ms）::

    estimated_duration_ms = speech_duration_ms + pause_ms

目标与缓冲（仅当提供 target_duration_ms 时）::

    slack_ms   = target_duration_ms - estimated_duration_ms
    overrun_ms = max(0, -slack_ms)

`slack_ms` 与"渲染后剩余缓冲"是同一个量，只保留 `slack_ms` 一个字段，
不重复设计 buffer_after_render。

状态机
------
NO_TARGET    未设置目标时长：只报告 estimated_duration_ms，slack_ms=None。
OVER_TARGET  slack_ms < 0：预计超时，overrun_ms 为正数。
LOW_BUFFER   未超时，但 slack_ms < safety_buffer_ms：**不算通过**。
PASS         未超时且 slack_ms >= safety_buffer_ms（slack_ms=0 且
             safety_buffer_ms=0 时为 PASS）。

词数口径
--------
本模块只接收 `word_count`，不做分词、不引入语言假设：
英文按空格切分、中文按字/词切分、混合语言加权等规则属于独立文本度量
模块的职责，后续如需统一由上游传入。

用法
----
    from pipeline.duration_budget import calculate_duration_budget

    b = calculate_duration_budget(
        word_count=59,
        words_per_minute=145,
        pause_ms=3400,
        target_duration_ms=30500,
        safety_buffer_ms=500,
    )
    b.status            # "PASS" / "LOW_BUFFER" / "OVER_TARGET" / "NO_TARGET"
    b.estimated_duration_ms, b.slack_ms, b.overrun_ms
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING

__all__ = [
    "DurationBudget",
    "DurationBudgetError",
    "calculate_duration_budget",
    "STATUS_NO_TARGET",
    "STATUS_PASS",
    "STATUS_LOW_BUFFER",
    "STATUS_OVER_TARGET",
    "DURATION_STATUSES",
    "MAX_DURATION_MS",
    "MS_PER_MINUTE",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MS_PER_MINUTE = 60_000
#: 单次预算允许的最大时长（24 小时），超出一律视为输入越界而非正常业务。
MAX_DURATION_MS = 86_400_000

STATUS_NO_TARGET = "NO_TARGET"
STATUS_PASS = "PASS"
STATUS_LOW_BUFFER = "LOW_BUFFER"
STATUS_OVER_TARGET = "OVER_TARGET"

DURATION_STATUSES = frozenset(
    {STATUS_NO_TARGET, STATUS_PASS, STATUS_LOW_BUFFER, STATUS_OVER_TARGET}
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class DurationBudgetError(ValueError):
    """时长预算输入非法。`field` 指出出错的参数名。"""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__("[{}] {}".format(field, message))


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------
def _require_int(name: str, value: object, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DurationBudgetError(
            name, "必须是整数，实际为 {}".format(type(value).__name__)
        )
    if value < minimum:
        raise DurationBudgetError(name, "不得小于 {}，实际为 {}".format(minimum, value))
    if maximum is not None and value > maximum:
        raise DurationBudgetError(name, "不得大于 {}，实际为 {}".format(maximum, value))
    return value


def _require_positive_wpm(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DurationBudgetError(
            "words_per_minute",
            "必须是数字，实际为 {}".format(type(value).__name__),
        )
    try:
        normalized = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - 仅在异常对象输入时触发
        raise DurationBudgetError("words_per_minute", "无法解析为十进制数") from exc
    if not normalized.is_finite():
        raise DurationBudgetError("words_per_minute", "必须是有限数值，不接受 NaN / inf")
    if normalized <= 0:
        raise DurationBudgetError("words_per_minute", "必须为正数，实际为 {}".format(value))
    return value


def _speech_ms(word_count: int, words_per_minute: float) -> int:
    """ceil(word_count / wpm * 60000)，用 Decimal 精确求上界，不用二进制浮点。"""
    exact = Decimal(word_count) * MS_PER_MINUTE / Decimal(str(words_per_minute))
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DurationBudget:
    """一次时长预算的不可变结果。全部时长为整数毫秒。"""

    word_count: int
    words_per_minute: float
    speech_duration_ms: int
    pause_ms: int
    estimated_duration_ms: int
    target_duration_ms: int | None
    slack_ms: int | None
    safety_buffer_ms: int
    status: str
    overrun_ms: int = 0

    @property
    def has_target(self) -> bool:
        return self.target_duration_ms is not None

    @property
    def is_pass(self) -> bool:
        """只有 PASS 才算通过；LOW_BUFFER 不算通过。"""
        return self.status == STATUS_PASS


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def calculate_duration_budget(
    *,
    word_count: int,
    words_per_minute: float,
    pause_ms: int = 0,
    target_duration_ms: int | None = None,
    safety_buffer_ms: int = 0,
) -> DurationBudget:
    """计算旁白时长预算。

    Args:
        word_count: 旁白词数，非负整数（不接受 bool）。
        words_per_minute: 语速，正有限数。
        pause_ms: 显式停顿累计毫秒，非负整数。
        target_duration_ms: 目标总时长毫秒，可选；提供时必须为正整数。
        safety_buffer_ms: 最低安全缓冲毫秒，非负整数；仅在提供目标时长时有意义。

    Returns:
        DurationBudget，不可变，所有时长字段为整数毫秒。

    Raises:
        DurationBudgetError: 任一输入非法（ValueError 子类）。
    """
    word_count = _require_int("word_count", word_count, minimum=0)
    pause_ms = _require_int("pause_ms", pause_ms, minimum=0, maximum=MAX_DURATION_MS)
    safety_buffer_ms = _require_int(
        "safety_buffer_ms", safety_buffer_ms, minimum=0, maximum=MAX_DURATION_MS
    )
    words_per_minute = _require_positive_wpm(words_per_minute)
    if target_duration_ms is not None:
        target_duration_ms = _require_int(
            "target_duration_ms", target_duration_ms, minimum=1, maximum=MAX_DURATION_MS
        )

    speech_duration_ms = _speech_ms(word_count, words_per_minute)
    estimated_duration_ms = speech_duration_ms + pause_ms
    if estimated_duration_ms > MAX_DURATION_MS:
        raise DurationBudgetError(
            "word_count",
            "预计时长 {} ms 超出允许上限 {} ms".format(
                estimated_duration_ms, MAX_DURATION_MS
            ),
        )

    if target_duration_ms is None:
        return DurationBudget(
            word_count=word_count,
            words_per_minute=words_per_minute,
            speech_duration_ms=speech_duration_ms,
            pause_ms=pause_ms,
            estimated_duration_ms=estimated_duration_ms,
            target_duration_ms=None,
            slack_ms=None,
            safety_buffer_ms=safety_buffer_ms,
            status=STATUS_NO_TARGET,
            overrun_ms=0,
        )

    slack_ms = target_duration_ms - estimated_duration_ms
    if slack_ms < 0:
        status = STATUS_OVER_TARGET
        overrun_ms = -slack_ms
    elif slack_ms < safety_buffer_ms:
        status = STATUS_LOW_BUFFER
        overrun_ms = 0
    else:
        status = STATUS_PASS
        overrun_ms = 0

    return DurationBudget(
        word_count=word_count,
        words_per_minute=words_per_minute,
        speech_duration_ms=speech_duration_ms,
        pause_ms=pause_ms,
        estimated_duration_ms=estimated_duration_ms,
        target_duration_ms=target_duration_ms,
        slack_ms=slack_ms,
        safety_buffer_ms=safety_buffer_ms,
        status=status,
        overrun_ms=overrun_ms,
    )
