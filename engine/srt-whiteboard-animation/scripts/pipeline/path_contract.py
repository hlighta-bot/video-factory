#!/usr/bin/env python3
"""路径安全契约：唯一的根锚定、路径规范化和越界检查（只读，不写文件）。

职责边界
--------
本模块只做"解析 + 校验"，**不创建、不写入、不覆盖、不删除任何文件或目录**，
不读取配置文件、不读 `.env`、不调用生产流水线、不调用 `os.chdir()`。

概念区分（三者不可混为一谈）
----------------------------
* 仓库根目录（video-factory）：由调用方传入或上游自行推导，本模块不猜。
* 引擎根目录（engine/srt-whiteboard-animation）：`engine_root()` 以本文件
  位置锚定（`__file__.parents[2]`），与进程启动目录无关。
* 某个具体项目的输入/输出目录：一律由调用方显式给出 `root` / `allowed_root`。

解析规则
--------
1. 相对路径一律相对 `root` 解析，绝不相对 `Path.cwd()`。
2. 绝对路径保留自身指向语义（不重写、不重定向），但同样要过边界校验。
3. 解析结果统一经 `Path.resolve(strict=False)` 规范化：既消除 `..`，也解析
   已存在部分的符号链接，因此 `link/../x` 与"符号链接指向根外"都无法绕过检查。
4. 边界 = `allowed_root`（若给出）否则 `root`。即默认**所有解析结果必须落在
   给定 root 之内**；如需放行 root 之外的绝对路径，显式传 `allowed_root`。
   这是比"仅当指定 allowed_root 才检查"更严格的安全默认值。
5. 越界一律抛 `PathOutsideAllowedRoot`，不钳制、不回退默认目录、不自动重定向。

API
---
    resolve_path(path, *, root, must_exist=False, expected_type=None, allowed_root=None)
    validate_output_path(path, *, root, allow_overwrite=False, allowed_root=None)
    engine_root() -> Path

异常
----
全部继承 `PathContractError(ValueError)`，各自带稳定 `code` 与可读 `message`，
并记录相关路径，便于定位。子类：

    InvalidPathArgument       E_PATH_ARGUMENT     None/类型/空串/纯空白/内嵌 NUL
    InvalidRootError          E_ROOT_INVALID      root 或 allowed_root 非法
    InvalidPathConstraint     E_CONSTRAINT        must_exist/allow_overwrite 非布尔、
                                                  expected_type 不在 {"file","directory"}
    PathOutsideAllowedRoot    E_OUTSIDE           规范化后越界
    PathNotFound              E_NOT_FOUND         must_exist=True 但不存在
    PathTypeMismatch          E_TYPE_MISMATCH     期望文件却是目录 / 反之 / 输出指向目录
    OutputExistsError         E_OUTPUT_EXISTS     输出已存在且未授权覆盖

TOCTOU 边界（必须明确）
----------------------
`validate_output_path` 只是**预检查**：检查通过不代表后续写入一定安全，
检查与真正写入之间存在竞态窗口。本模块不声称解决 TOCTOU；真正落盘时必须由
写入方使用独立的安全创建/覆盖策略（如 `O_EXCL`、临时文件 + 原子 `rename`）。
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "PathContractError",
    "InvalidPathArgument",
    "InvalidRootError",
    "InvalidPathConstraint",
    "PathOutsideAllowedRoot",
    "PathNotFound",
    "PathTypeMismatch",
    "OutputExistsError",
    "ALLOWED_EXPECTED_TYPES",
    "engine_root",
    "resolve_path",
    "validate_output_path",
]

#: expected_type 的唯一合法取值。
ALLOWED_EXPECTED_TYPES = ("file", "directory")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class PathContractError(ValueError):
    """路径契约错误基类：稳定 code + 可读信息 + 相关路径。"""

    code = "E_PATH"

    def __init__(self, message: str, *, path: object = None, root: object = None) -> None:
        self.message = message
        self.path = path
        self.root = root
        super().__init__("[{}] {}".format(self.code, message))

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "path": self.path, "root": self.root}


class InvalidPathArgument(PathContractError):
    code = "E_PATH_ARGUMENT"


class InvalidRootError(PathContractError):
    code = "E_ROOT_INVALID"


class InvalidPathConstraint(PathContractError):
    code = "E_CONSTRAINT"


class PathOutsideAllowedRoot(PathContractError):
    code = "E_OUTSIDE"


class PathNotFound(PathContractError):
    code = "E_NOT_FOUND"


class PathTypeMismatch(PathContractError):
    code = "E_TYPE_MISMATCH"


class OutputExistsError(PathContractError):
    code = "E_OUTPUT_EXISTS"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _reject_nul(value: str, field: str) -> None:
    if "\x00" in value:
        raise InvalidPathArgument(
            "{} 含内嵌 NUL 字符".format(field), path=value
        )


def _as_path(value: object, field: str) -> Path:
    """把入参收敛为 Path，拒绝 None / bool / 非路径类型 / 空串 / 空白串。"""
    if value is None:
        raise InvalidPathArgument("{} 不得为 None".format(field), path=value)
    if isinstance(value, bool):
        raise InvalidPathArgument(
            "{} 不得为布尔值，实际为 bool".format(field), path=value
        )
    if isinstance(value, bytes):
        raise InvalidPathArgument(
            "{} 不得为 bytes，请传 str 或 pathlib.Path".format(field), path=value
        )
    if not isinstance(value, (str, os.PathLike)):
        raise InvalidPathArgument(
            "{} 必须是 str 或 pathlib.Path，实际为 {}".format(field, type(value).__name__),
            path=value,
        )
    if isinstance(value, str):
        if not value.strip():
            raise InvalidPathArgument("{} 不得为空或纯空白字符串".format(field), path=value)
        _reject_nul(value, field)
    text = os.fspath(value)
    if not isinstance(text, str):
        raise InvalidPathArgument(
            "{} 必须解析为 str 路径，实际为 {}".format(field, type(text).__name__),
            path=value,
        )
    if not text.strip():
        raise InvalidPathArgument("{} 不得为空或纯空白字符串".format(field), path=value)
    _reject_nul(text, field)
    return Path(text)


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidPathConstraint(
            "{} 必须是布尔值，实际为 {}".format(field, type(value).__name__), path=value
        )
    return value


def _require_root(value: object, field: str) -> Path:
    """root / allowed_root 必须是已存在的目录，且以自身位置为锚（非 cwd）。"""
    if value is None:
        raise InvalidRootError("{} 不得为 None".format(field), root=value)
    try:
        candidate = _as_path(value, field)
    except InvalidPathArgument as exc:
        raise InvalidRootError(str(exc.message), root=value) from exc
    resolved = candidate.resolve(strict=False)
    if not resolved.exists():
        raise InvalidRootError("{} 不存在：{}".format(field, resolved), root=str(resolved))
    if not resolved.is_dir():
        raise InvalidRootError("{} 不是目录：{}".format(field, resolved), root=str(resolved))
    return resolved


def _require_expected_type(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidPathConstraint(
            "expected_type 必须是 str 或 None，实际为 {}".format(type(value).__name__),
            path=value,
        )
    if value not in ALLOWED_EXPECTED_TYPES:
        raise InvalidPathConstraint(
            "expected_type 只支持 {}，实际为 {!r}".format(
                " / ".join(ALLOWED_EXPECTED_TYPES), value
            ),
            path=value,
        )
    return value


def _assert_within(resolved: Path, boundary: Path, original: object) -> None:
    if resolved == boundary:
        return
    if not resolved.is_relative_to(boundary):
        raise PathOutsideAllowedRoot(
            "路径越界：{} 不在允许根 {} 之内（规范化后为 {}）".format(
                original, boundary, resolved
            ),
            path=str(resolved),
            root=str(boundary),
        )


def _resolve(raw: Path, root: Path, boundary: Path) -> Path:
    candidate = raw if raw.is_absolute() else (root / raw)
    resolved = candidate.resolve(strict=False)
    _assert_within(resolved, boundary, raw)
    return resolved


# ---------------------------------------------------------------------------
# 根目录
# ---------------------------------------------------------------------------
def engine_root() -> Path:
    """引擎根目录，锚定在本文件位置（`scripts/pipeline/path_contract.py`）。"""
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 输入路径
# ---------------------------------------------------------------------------
def resolve_path(
    path: str | os.PathLike,
    *,
    root: str | os.PathLike,
    must_exist: bool = False,
    expected_type: str | None = None,
    allowed_root: str | os.PathLike | None = None,
) -> Path:
    """解析并校验输入路径，返回规范化的绝对路径；不触碰文件系统内容。

    Args:
        path: 待解析路径；相对路径按 `root` 解析。
        root: 显式解析根目录，必须已存在且为目录。
        must_exist: True 时路径必须存在，否则抛 `PathNotFound`。
        expected_type: None / "file" / "directory"；不匹配抛 `PathTypeMismatch`。
        allowed_root: 允许边界，默认等于 `root`。

    Returns:
        规范化后的绝对 `Path`。

    Raises:
        InvalidPathArgument / InvalidRootError / InvalidPathConstraint /
        PathOutsideAllowedRoot / PathNotFound / PathTypeMismatch
    """
    raw = _as_path(path, "path")
    must_exist = _require_bool(must_exist, "must_exist")
    expected_type = _require_expected_type(expected_type)
    root_path = _require_root(root, "root")
    boundary = root_path if allowed_root is None else _require_root(allowed_root, "allowed_root")

    resolved = _resolve(raw, root_path, boundary)

    if must_exist and not resolved.exists():
        raise PathNotFound("路径不存在：{}".format(resolved), path=str(resolved), root=str(root_path))
    if expected_type == "file" and resolved.exists() and not resolved.is_file():
        raise PathTypeMismatch(
            "期望文件，实际为目录：{}".format(resolved), path=str(resolved), root=str(root_path)
        )
    if expected_type == "directory" and resolved.exists() and not resolved.is_dir():
        raise PathTypeMismatch(
            "期望目录，实际为文件：{}".format(resolved), path=str(resolved), root=str(root_path)
        )
    return resolved


# ---------------------------------------------------------------------------
# 输出路径
# ---------------------------------------------------------------------------
def validate_output_path(
    path: str | os.PathLike,
    *,
    root: str | os.PathLike,
    allow_overwrite: bool = False,
    allowed_root: str | os.PathLike | None = None,
) -> Path:
    """校验输出路径，返回规范化绝对路径；**不创建目录、不写入、不覆盖**。

    默认 `allow_overwrite=False`：目标已存在则抛 `OutputExistsError`。
    `allow_overwrite=True` 时仅放行检查，不执行任何覆盖动作。

    目标父目录缺失不影响校验通过——本模块不负责创建目录。

    Raises:
        InvalidPathArgument / InvalidRootError / InvalidPathConstraint /
        PathOutsideAllowedRoot / PathTypeMismatch / OutputExistsError
    """
    raw = _as_path(path, "path")
    allow_overwrite = _require_bool(allow_overwrite, "allow_overwrite")
    if isinstance(path, str) and path.strip().endswith(("/", "\\")):
        raise InvalidPathArgument(
            "输出路径不得以目录分隔符结尾：{!r}".format(path), path=path
        )
    if raw.name in ("", ".", ".."):
        raise InvalidPathArgument(
            "输出路径必须指向具体文件，当前 basename 为 {!r}".format(raw.name), path=str(raw)
        )

    root_path = _require_root(root, "root")
    boundary = root_path if allowed_root is None else _require_root(allowed_root, "allowed_root")
    resolved = _resolve(raw, root_path, boundary)

    if resolved.exists():
        if resolved.is_dir():
            raise PathTypeMismatch(
                "输出路径指向已存在目录：{}".format(resolved),
                path=str(resolved),
                root=str(root_path),
            )
        if not allow_overwrite:
            raise OutputExistsError(
                "输出文件已存在且未授权覆盖：{}".format(resolved),
                path=str(resolved),
                root=str(root_path),
            )
    return resolved
