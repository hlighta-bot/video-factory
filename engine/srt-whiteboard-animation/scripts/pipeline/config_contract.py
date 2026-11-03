#!/usr/bin/env python3
"""流水线配置契约：不可变配置结构、默认值与严格校验（纯内存，无副作用）。

设计边界
--------
* 只负责 **配置结构与校验**：不读环境变量、不读 `.env`、不读配置文件、
  不推导项目路径、不创建文件或目录、不调用任何外部服务。
* 不重复实现 G4-B `duration_budget` 的时长计算；`target_duration_ms` 等字段
  在这里只是被校验和保存的数值。
* 不做隐式转换：字符串 "135"、`0`/`1` 当作布尔、`300.0` 当作毫秒一律拒绝。

字段与单位
----------
=====================  ==========  ==============================================
字段                    单位        说明
=====================  ==========  ==============================================
words_per_minute      词/分钟     旁白语速。允许正整数或有限正浮点数。
pause_ms              毫秒        单条字幕/场景之间的停顿，非负整数。
safety_buffer_ms      毫秒        时长预算的安全余量，非负整数。
target_duration_ms    毫秒        可选目标总时长；`None` 表示"未设置"。
allow_overwrite       布尔        输出覆盖开关，必须是真正的 bool。
=====================  ==========  ==============================================

默认值来源（保守取向，均可被显式覆盖）
--------------------------------------
* `words_per_minute=135`：沿用既有旁白稿设计时使用的 135 wpm 基准。
* `pause_ms=300`：0.3 s，足够听感停顿又不显著拖长总时长。
* `safety_buffer_ms=500`：0.5 s 余量，用于吸收 TTS 与字幕的累积误差。
* `target_duration_ms=None`：默认不绑定目标时长，避免误触发时长收敛。
* `allow_overwrite=False`：默认拒绝覆盖既有产物。

错误契约
--------
所有错误统一为 `ConfigError(ValueError)`，带稳定 `code`、精确 `field`、
人类可读 `message`，`str()` 形如 `[E_CONFIG_FIELD_RANGE] target_duration_ms: ...`。
错误信息只包含字段名与用户传入的配置值，不含环境变量或任何密钥内容。

稳定错误码
----------
    E_CONFIG_NOT_MAPPING        入参不是 Mapping（含 None）
    E_CONFIG_KEY_TYPE           Mapping 的键不是 str
    E_CONFIG_FIELD_MISSING      缺少必填字段
    E_CONFIG_FIELD_UNKNOWN      出现未声明的字段（不静默忽略）
    E_CONFIG_FIELD_TYPE         类型不符（毫秒传浮点/字符串、布尔传数字等）
    E_CONFIG_FIELD_BOOL         应传数值却传了 bool（bool 是 int 的子类）
    E_CONFIG_FIELD_RANGE        数值越界（<=0 的 wpm、负毫秒、超上限）
    E_CONFIG_FIELD_NON_FINITE   NaN / +inf / -inf
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

__all__ = [
    "ConfigError",
    "PipelineConfig",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "MIN_WORDS_PER_MINUTE",
    "MAX_WORDS_PER_MINUTE",
    "MAX_MILLISECONDS",
    "default_config",
    "config_from_mapping",
]

#: 必填字段：即使其它字段都有默认值，构造配置时也必须显式给出语速。
REQUIRED_FIELDS = ("words_per_minute",)

#: 省略时使用默认值的字段。
OPTIONAL_FIELDS = (
    "pause_ms",
    "safety_buffer_ms",
    "target_duration_ms",
    "allow_overwrite",
)

#: 语速下界（开区间）：0 与负数无物理意义。
MIN_WORDS_PER_MINUTE = 0

#: 语速上界（闭区间）：1000 wpm 已远超人类可听语速，用于拦下明显录错的数量级。
MAX_WORDS_PER_MINUTE = 1000

#: 毫秒字段上界：1 天，与 G4-B 的时长上限保持同一量级。
MAX_MILLISECONDS = 86_400_000


class ConfigError(ValueError):
    """配置契约错误：稳定 code + 字段定位 + 可读信息。"""

    __slots__ = ("code", "field", "message")

    def __init__(self, code: str, field: str, message: str) -> None:
        self.code = code
        self.field = field
        self.message = message
        super().__init__("[{}] {}: {}".format(code, field, message))

    def as_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message}


# ---------------------------------------------------------------------------
# 字段级校验
# ---------------------------------------------------------------------------
def _reject_bool(field: str, value: Any) -> None:
    if isinstance(value, bool):
        raise ConfigError(
            "E_CONFIG_FIELD_BOOL",
            field,
            "{} 必须是数值，实际为 bool（{}）；bool 是 int 的子类，此处不接受".format(field, value),
        )


def _validate_words_per_minute(value: Any) -> int | float:
    _reject_bool("words_per_minute", value)
    if not isinstance(value, (int, float)):
        raise ConfigError(
            "E_CONFIG_FIELD_TYPE",
            "words_per_minute",
            "words_per_minute 必须是 int 或 float，实际为 {}".format(type(value).__name__),
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(
            "E_CONFIG_FIELD_NON_FINITE", "words_per_minute", "words_per_minute 必须是有限数值，实际为 {!r}".format(value)
        )
    if value <= MIN_WORDS_PER_MINUTE:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE",
            "words_per_minute",
            "words_per_minute 必须大于 {}，实际为 {!r}".format(MIN_WORDS_PER_MINUTE, value),
        )
    if value > MAX_WORDS_PER_MINUTE:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE",
            "words_per_minute",
            "words_per_minute 必须不超过 {}，实际为 {!r}".format(MAX_WORDS_PER_MINUTE, value),
        )
    return value


def _validate_milliseconds(field: str, value: Any) -> int:
    _reject_bool(field, value)
    if not isinstance(value, int):
        raise ConfigError(
            "E_CONFIG_FIELD_TYPE",
            field,
            "{} 必须是 int 毫秒值，实际为 {}（{!r}）".format(field, type(value).__name__, value),
        )
    if value < 0:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE", field, "{} 必须为非负整数，实际为 {!r}".format(field, value)
        )
    if value > MAX_MILLISECONDS:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE", field, "{} 必须不超过 {} 毫秒，实际为 {!r}".format(field, MAX_MILLISECONDS, value)
        )
    return value


def _validate_target_duration_ms(value: Any) -> int | None:
    """`None` 表示"未设置"；其它任何值都必须是通过校验的正整数毫秒。"""
    if value is None:
        return None
    _reject_bool("target_duration_ms", value)
    if not isinstance(value, int):
        raise ConfigError(
            "E_CONFIG_FIELD_TYPE",
            "target_duration_ms",
            "target_duration_ms 必须是 int 毫秒值或 None，实际为 {}（{!r}）".format(type(value).__name__, value),
        )
    if value <= 0:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE",
            "target_duration_ms",
            "target_duration_ms 未设置请用 None；若设置则必须是正整数毫秒，实际为 {!r}".format(value),
        )
    if value > MAX_MILLISECONDS:
        raise ConfigError(
            "E_CONFIG_FIELD_RANGE",
            "target_duration_ms",
            "target_duration_ms 必须不超过 {} 毫秒，实际为 {!r}".format(MAX_MILLISECONDS, value),
        )
    return value


def _validate_allow_overwrite(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(
            "E_CONFIG_FIELD_TYPE",
            "allow_overwrite",
            "必须是 bool，实际为 {}（{!r}）；不接受 0/1/字符串等隐式转换".format(
                type(value).__name__, value
            ),
        )
    return value


# ---------------------------------------------------------------------------
# 配置数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """不可变流水线配置。字段在构造时即校验，非法值抛 `ConfigError`。"""

    words_per_minute: int | float
    pause_ms: int = 300
    safety_buffer_ms: int = 500
    target_duration_ms: int | None = None
    allow_overwrite: bool = False

    def __post_init__(self) -> None:
        _validate_words_per_minute(self.words_per_minute)
        _validate_milliseconds("pause_ms", self.pause_ms)
        _validate_milliseconds("safety_buffer_ms", self.safety_buffer_ms)
        _validate_target_duration_ms(self.target_duration_ms)
        _validate_allow_overwrite(self.allow_overwrite)

    def to_dict(self) -> dict:
        """导出为普通 dict（与 `config_from_mapping` 可往返）。"""
        return {item.name: getattr(self, item.name) for item in fields(self)}


#: `PipelineConfig` 声明的全部字段名，用于未知字段检测。
_KNOWN_FIELDS = tuple(item.name for item in fields(PipelineConfig))


def default_config() -> PipelineConfig:
    """返回一份有效的默认配置（`words_per_minute=135`，其余为保守默认值）。"""
    return PipelineConfig(
        words_per_minute=135,
        pause_ms=300,
        safety_buffer_ms=500,
        target_duration_ms=None,
        allow_overwrite=False,
    )


def config_from_mapping(mapping: Mapping) -> PipelineConfig:
    """从显式传入的 Mapping 构造配置，严格校验，不读取任何外部状态。

    Args:
        mapping: 键为 str 的 Mapping。必须包含 `words_per_minute`；
            其余字段省略时取默认值。

    Returns:
        校验通过的 `PipelineConfig`。

    Raises:
        ConfigError: 入参非 Mapping、键非 str、缺必填字段、含未知字段、
            字段类型/布尔/有限性/范围不合法。
    """
    if mapping is None:
        raise ConfigError("E_CONFIG_NOT_MAPPING", "<mapping>", "配置入参不得为 None")
    if not isinstance(mapping, Mapping):
        raise ConfigError(
            "E_CONFIG_NOT_MAPPING",
            "<mapping>",
            "配置入参必须是 Mapping，实际为 {}".format(type(mapping).__name__),
        )

    for key in mapping.keys():
        if not isinstance(key, str):
            raise ConfigError(
                "E_CONFIG_KEY_TYPE",
                repr(key),
                "配置键必须是 str，实际为 {}".format(type(key).__name__),
            )

    unknown = sorted(set(mapping.keys()) - set(_KNOWN_FIELDS))
    if unknown:
        raise ConfigError(
            "E_CONFIG_FIELD_UNKNOWN",
            unknown[0],
            "未识别的配置字段：{}（允许字段：{}）".format(
                ", ".join(unknown), ", ".join(_KNOWN_FIELDS)
            ),
        )

    missing = [name for name in REQUIRED_FIELDS if name not in mapping]
    if missing:
        raise ConfigError(
            "E_CONFIG_FIELD_MISSING",
            missing[0],
            "缺少必填字段：{}".format(", ".join(missing)),
        )

    # 先做字段级校验，再交给 dataclass 构造；__post_init__ 会重复校验一次，
    # 保证直接构造 PipelineConfig(...) 也享有同样契约。
    words_per_minute = _validate_words_per_minute(mapping["words_per_minute"])
    pause_ms = _validate_milliseconds("pause_ms", mapping.get("pause_ms", 300))
    safety_buffer_ms = _validate_milliseconds(
        "safety_buffer_ms", mapping.get("safety_buffer_ms", 500)
    )
    target_duration_ms = _validate_target_duration_ms(mapping.get("target_duration_ms", None))
    allow_overwrite = _validate_allow_overwrite(mapping.get("allow_overwrite", False))

    return PipelineConfig(
        words_per_minute=words_per_minute,
        pause_ms=pause_ms,
        safety_buffer_ms=safety_buffer_ms,
        target_duration_ms=target_duration_ms,
        allow_overwrite=allow_overwrite,
    )
