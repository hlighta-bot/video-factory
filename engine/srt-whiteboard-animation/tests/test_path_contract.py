#!/usr/bin/env python3
"""path_contract 离线单元测试。

全部用例只使用标准库；任何涉及文件/目录/符号链接的用例都在
`tempfile.TemporaryDirectory()` 内完成，绝不触碰仓库已有文件或 output/。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pipeline.path_contract import (
    InvalidPathArgument,
    InvalidPathConstraint,
    InvalidRootError,
    OutputExistsError,
    PathContractError,
    PathNotFound,
    PathOutsideAllowedRoot,
    PathTypeMismatch,
    engine_root,
    resolve_path,
    validate_output_path,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class _TempDirMixin:
    """把 TemporaryDirectory 的生命周期挂在 TestCase 上。"""

    def make_tempdir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()


class _ChdirMixin:
    def enter_dir(self, target: Path) -> None:
        origin = os.getcwd()
        self.addCleanup(os.chdir, origin)
        os.chdir(target)


# ---------------------------------------------------------------------------
# 1. 相对路径：只能相对显式 root
# ---------------------------------------------------------------------------
class RelativePathTests(_TempDirMixin, _ChdirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        (self.root / "sub" / "deep").mkdir(parents=True)
        (self.root / "sub" / "in.srt").write_text("1\n", encoding="utf-8")

    def test_relative_resolves_against_explicit_root(self) -> None:
        got = resolve_path("sub/in.srt", root=self.root, must_exist=True)
        self.assertEqual(got, self.root / "sub" / "in.srt")
        self.assertTrue(got.is_absolute())

    def test_relative_ignores_process_cwd(self) -> None:
        other = self.make_tempdir()
        expected = resolve_path("sub/in.srt", root=self.root, must_exist=True)
        self.enter_dir(other)
        self.assertNotEqual(Path.cwd(), self.root)
        self.assertEqual(resolve_path("sub/in.srt", root=self.root, must_exist=True), expected)

    def test_relative_same_result_from_repo_root_and_engine_root(self) -> None:
        expected = resolve_path("sub/in.srt", root=self.root, must_exist=True)
        self.enter_dir(REPO_ROOT)
        from_repo = resolve_path("sub/in.srt", root=self.root, must_exist=True)
        self.enter_dir(engine_root())
        from_engine = resolve_path("sub/in.srt", root=self.root, must_exist=True)
        self.assertEqual(from_repo, expected)
        self.assertEqual(from_engine, expected)

    def test_relative_nested_subdirectory(self) -> None:
        got = resolve_path("sub/deep", root=self.root, must_exist=True, expected_type="directory")
        self.assertEqual(got, self.root / "sub" / "deep")

    def test_relative_nonexistent_allowed_without_must_exist(self) -> None:
        got = resolve_path("sub/ghost.txt", root=self.root)
        self.assertEqual(got, self.root / "sub" / "ghost.txt")

    def test_blank_root_rejected(self) -> None:
        for bad_root in ("", "   ", "\t\n"):
            with self.assertRaises(InvalidRootError) as ctx:
                resolve_path("sub/in.srt", root=bad_root)
            self.assertEqual(ctx.exception.code, "E_ROOT_INVALID")

    def test_nonexistent_root_rejected(self) -> None:
        with self.assertRaises(InvalidRootError):
            resolve_path("x.txt", root=self.root / "missing_base")

    def test_root_that_is_a_file_rejected(self) -> None:
        with self.assertRaises(InvalidRootError):
            resolve_path("x.txt", root=self.root / "sub" / "in.srt")


# ---------------------------------------------------------------------------
# 2. 绝对路径：保留指向语义，但同样受边界约束
# ---------------------------------------------------------------------------
class AbsolutePathTests(_TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        self.file = self.root / "abs.txt"
        self.file.write_text("abs", encoding="utf-8")
        self.outside = self.make_tempdir() / "outside.txt"
        self.outside.write_text("out", encoding="utf-8")

    def test_absolute_inside_root_kept_verbatim(self) -> None:
        got = resolve_path(str(self.file), root=self.root, must_exist=True)
        self.assertEqual(got, self.file.resolve())

    def test_absolute_outside_root_rejected_by_default(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot) as ctx:
            resolve_path(str(self.outside), root=self.root, must_exist=True)
        self.assertEqual(ctx.exception.code, "E_OUTSIDE")

    def test_absolute_outside_allowed_when_explicitly_authorised(self) -> None:
        got = resolve_path(str(self.outside), root=self.root, allowed_root=Path("/"))
        self.assertEqual(got, self.outside.resolve())
        self.assertNotEqual(got, self.file)

    def test_absolute_is_not_rewritten_into_root(self) -> None:
        got = resolve_path(str(self.outside), root=self.root, allowed_root=Path("/"))
        self.assertFalse(str(got).startswith(str(self.root) + os.sep))


# ---------------------------------------------------------------------------
# 3. 路径穿越
# ---------------------------------------------------------------------------
class TraversalTests(_TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        (self.root / "sub").mkdir()
        self.sibling = self.root.parent / (self.root.name + "-sibling")
        self.sibling.mkdir()
        self.addCleanup(self.sibling.rmdir)

    def test_single_parent_traversal_rejected(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot) as ctx:
            resolve_path("../outside.txt", root=self.root)
        self.assertEqual(ctx.exception.code, "E_OUTSIDE")

    def test_multi_level_traversal_rejected(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot):
            resolve_path("sub/../../outside.txt", root=self.root)

    def test_traversal_that_escapes_then_returns_is_allowed(self) -> None:
        got = resolve_path("sub/../sub/../ok.txt", root=self.root)
        self.assertEqual(got, self.root / "ok.txt")

    def test_normalised_target_checked_not_raw_string(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot):
            resolve_path("sub/../../" + self.sibling.name + "/x.txt", root=self.root)

    def test_dotdot_inside_filename_is_not_false_positive(self) -> None:
        got = resolve_path("a..b.txt", root=self.root)
        self.assertEqual(got, self.root / "a..b.txt")


# ---------------------------------------------------------------------------
# 4. 符号链接
# ---------------------------------------------------------------------------
class SymlinkTests(_TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        self.outside = self.make_tempdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")
        self.link = self.root / "escape"
        try:
            os.symlink(self.outside, self.link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as exc:  # pragma: no cover
            raise unittest.SkipTest(
                "环境不支持符号链接，跳过（未伪装为通过）：{}: {}".format(type(exc).__name__, exc)
            )

    def test_symlink_to_outside_root_rejected(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot) as ctx:
            resolve_path("escape/secret.txt", root=self.root)
        self.assertEqual(ctx.exception.code, "E_OUTSIDE")

    def test_symlink_traversal_combo_rejected(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot):
            resolve_path("escape/../secret.txt", root=self.root)

    def test_symlink_inside_root_accepted(self) -> None:
        inner = self.root / "inner"
        inner.mkdir()
        (inner / "ok.txt").write_text("ok", encoding="utf-8")
        alias = self.root / "alias"
        os.symlink(inner, alias, target_is_directory=True)
        got = resolve_path("alias/ok.txt", root=self.root, must_exist=True, expected_type="file")
        self.assertEqual(got, (inner / "ok.txt").resolve())


# ---------------------------------------------------------------------------
# 5. 输入校验
# ---------------------------------------------------------------------------
class InputValidationTests(_TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        self.file = self.root / "in.srt"
        self.file.write_text("1\n", encoding="utf-8")
        (self.root / "dir").mkdir()

    def test_existing_file_accepted(self) -> None:
        self.assertEqual(
            resolve_path("in.srt", root=self.root, must_exist=True, expected_type="file"),
            self.file.resolve(),
        )

    def test_missing_input_raises_not_found(self) -> None:
        with self.assertRaises(PathNotFound) as ctx:
            resolve_path("missing.srt", root=self.root, must_exist=True)
        self.assertEqual(ctx.exception.code, "E_NOT_FOUND")
        self.assertIn("missing.srt", ctx.exception.message)

    def test_expected_file_but_got_directory(self) -> None:
        with self.assertRaises(PathTypeMismatch) as ctx:
            resolve_path("dir", root=self.root, must_exist=True, expected_type="file")
        self.assertEqual(ctx.exception.code, "E_TYPE_MISMATCH")

    def test_expected_directory_but_got_file(self) -> None:
        with self.assertRaises(PathTypeMismatch):
            resolve_path("in.srt", root=self.root, must_exist=True, expected_type="directory")

    def test_empty_string_rejected(self) -> None:
        with self.assertRaises(InvalidPathArgument) as ctx:
            resolve_path("", root=self.root)
        self.assertEqual(ctx.exception.code, "E_PATH_ARGUMENT")

    def test_whitespace_only_rejected(self) -> None:
        with self.assertRaises(InvalidPathArgument):
            resolve_path("   \t ", root=self.root)

    def test_none_rejected(self) -> None:
        with self.assertRaises(InvalidPathArgument):
            resolve_path(None, root=self.root)

    def test_non_path_type_rejected(self) -> None:
        for bad in (123, 1.5, ["in.srt"], {"p": 1}, b"in.srt", True):
            with self.assertRaises(InvalidPathArgument, msg="bad={!r}".format(bad)):
                resolve_path(bad, root=self.root)

    def test_embedded_nul_rejected(self) -> None:
        with self.assertRaises(InvalidPathArgument) as ctx:
            resolve_path("bad\x00name", root=self.root)
        self.assertIn("NUL", ctx.exception.message)

    def test_unsupported_expected_type_rejected(self) -> None:
        for bad in ("symlink", "FILE", "", 1):
            with self.assertRaises(InvalidPathConstraint, msg="bad={!r}".format(bad)):
                resolve_path("in.srt", root=self.root, expected_type=bad)

    def test_non_bool_must_exist_rejected(self) -> None:
        with self.assertRaises(InvalidPathConstraint) as ctx:
            resolve_path("in.srt", root=self.root, must_exist="yes")
        self.assertEqual(ctx.exception.code, "E_CONSTRAINT")

    def test_pathlib_and_str_equivalent(self) -> None:
        self.assertEqual(
            resolve_path(Path("in.srt"), root=self.root),
            resolve_path("in.srt", root=str(self.root)),
        )


# ---------------------------------------------------------------------------
# 6. 输出路径与覆盖保护
# ---------------------------------------------------------------------------
class OutputPathTests(_TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()
        self.existing = self.root / "done.mp4"
        self.existing.write_text("ORIGINAL", encoding="utf-8")

    def test_new_output_accepted(self) -> None:
        got = validate_output_path("new.mp4", root=self.root)
        self.assertEqual(got, self.root / "new.mp4")

    def test_existing_output_rejected_by_default(self) -> None:
        with self.assertRaises(OutputExistsError) as ctx:
            validate_output_path("done.mp4", root=self.root)
        self.assertEqual(ctx.exception.code, "E_OUTPUT_EXISTS")
        self.assertIn("done.mp4", ctx.exception.message)

    def test_allow_overwrite_only_returns_path(self) -> None:
        before_stat = self.existing.stat()
        before_text = self.existing.read_text(encoding="utf-8")
        got = validate_output_path("done.mp4", root=self.root, allow_overwrite=True)
        self.assertEqual(got, self.existing.resolve())
        self.assertEqual(self.existing.read_text(encoding="utf-8"), before_text)
        self.assertEqual(self.existing.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(self.existing.stat().st_size, before_stat.st_size)

    def test_output_outside_root_rejected(self) -> None:
        with self.assertRaises(PathOutsideAllowedRoot):
            validate_output_path("../escape.mp4", root=self.root)

    def test_output_existing_directory_rejected(self) -> None:
        (self.root / "outdir").mkdir()
        with self.assertRaises(PathTypeMismatch) as ctx:
            validate_output_path("outdir", root=self.root, allow_overwrite=True)
        self.assertEqual(ctx.exception.code, "E_TYPE_MISMATCH")

    def test_output_trailing_separator_rejected(self) -> None:
        for bad in ("out/", "out\\", "a/b/"):
            with self.assertRaises(InvalidPathArgument, msg="bad={!r}".format(bad)):
                validate_output_path(bad, root=self.root)

    def test_output_root_itself_rejected(self) -> None:
        with self.assertRaises(InvalidPathArgument):
            validate_output_path(".", root=self.root)

    def test_check_does_not_create_directories(self) -> None:
        target = validate_output_path("no/such/dir/out.mp4", root=self.root)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / "no").exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["done.mp4"])

    def test_check_does_not_create_target_file(self) -> None:
        target = validate_output_path("brand_new.mp4", root=self.root)
        self.assertFalse(target.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["done.mp4"])

    def test_non_bool_allow_overwrite_rejected(self) -> None:
        with self.assertRaises(InvalidPathConstraint):
            validate_output_path("new.mp4", root=self.root, allow_overwrite="true")

    def test_output_empty_and_none_rejected(self) -> None:
        for bad in ("", "  ", None):
            with self.assertRaises(InvalidPathArgument, msg="bad={!r}".format(bad)):
                validate_output_path(bad, root=self.root)


# ---------------------------------------------------------------------------
# 7. 异常质量与根锚定
# ---------------------------------------------------------------------------
class ErrorContractTests(_TempDirMixin, _ChdirMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_tempdir()

    def test_error_carries_code_message_and_path(self) -> None:
        with self.assertRaises(PathContractError) as ctx:
            resolve_path("../x", root=self.root)
        err = ctx.exception
        self.assertEqual(err.code, "E_OUTSIDE")
        self.assertIn("越界", err.message)
        self.assertTrue(err.path)
        self.assertEqual(err.root, str(self.root))
        self.assertIn("E_OUTSIDE", str(err))
        self.assertEqual(err.as_dict()["code"], "E_OUTSIDE")

    def test_distinct_error_categories_not_collapsed(self) -> None:
        codes = set()
        cases = [
            (InvalidPathArgument, lambda: resolve_path("", root=self.root)),
            (InvalidRootError, lambda: resolve_path("a", root=self.root / "nope")),
            (InvalidPathConstraint, lambda: resolve_path("a", root=self.root, expected_type="x")),
            (PathOutsideAllowedRoot, lambda: resolve_path("../a", root=self.root)),
            (PathNotFound, lambda: resolve_path("a", root=self.root, must_exist=True)),
        ]
        for expected_exc, call in cases:
            with self.assertRaises(expected_exc) as ctx:
                call()
            codes.add(ctx.exception.code)
        self.assertEqual(len(codes), len(cases))

    def test_all_errors_are_value_error(self) -> None:
        self.assertTrue(issubclass(PathContractError, ValueError))

    def test_engine_root_is_anchored_and_cwd_independent(self) -> None:
        first = engine_root()
        self.assertTrue(first.is_absolute())
        self.assertTrue(first.is_dir())
        self.assertTrue((first / "scripts" / "pipeline" / "path_contract.py").is_file())
        other = self.make_tempdir()
        self.enter_dir(other)
        self.assertEqual(engine_root(), first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
