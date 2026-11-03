#!/usr/bin/env python3
"""
流式笔迹动画 - 环境引导脚本

职责：
  1. 在 skill 目录下建立隔离的 Python 虚拟环境（已存在则复用）
  2. 核对运行所需的第三方库是否可导入
  3. 自动补齐缺失的库
  4. 末行打印 ENV_PY=<解释器路径>，供上层调用方捕获

用法：
  python prepare_env.py --provider mock --tts audio8  # 准备默认语音流程
  python prepare_env.py --provider gemini          # 额外安装 Gemini SDK
  python prepare_env.py --tts edge                 # 额外安装 Edge TTS SDK
  python prepare_env.py --check                    # 仅探测，缺东西就以非零码退出
"""
from __future__ import annotations

import os
import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# skill 根目录 = 本脚本向上两级
SKILL_ROOT = Path(__file__).resolve().parent.parent
VENV_ROOT = SKILL_ROOT / ".venv"

# 解释器导入名 -> pip 安装名
DEPS: dict[str, str] = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "av": "av",  # PyAV：纯 pip 安装的 H.264 编码，无需系统 ffmpeg
    "PIL": "Pillow",  # render_annotation_preview.py 画区域编号预览图（含中文标签）
}

PROVIDER_DEPS: dict[str, tuple[str, str]] = {
    "gemini": ("google.genai", "google-genai"),
}

TTS_DEPS: dict[str, tuple[str, str]] = {
    "edge": ("edge_tts", "edge-tts"),
}


def interpreter_path() -> Path:
    """虚拟环境里的 python 可执行文件位置（跨平台）。"""
    if sys.platform.startswith("win"):
        return VENV_ROOT / "Scripts" / "python.exe"
    return VENV_ROOT / "bin" / "python"


def ensure_venv(check_only: bool) -> Path:
    py = interpreter_path()
    if VENV_ROOT.exists() and py.exists():
        print(f"[ok] 复用现有虚拟环境: {VENV_ROOT}")
        return py

    if check_only:
        print(f"[err] 虚拟环境尚未建立: {VENV_ROOT}")
        sys.exit(1)

    print(f"[..] 建立虚拟环境: {VENV_ROOT}")
    venv.create(str(VENV_ROOT), with_pip=True)
    print("[ok] 虚拟环境就绪")
    return py


def can_import(py: Path, import_name: str) -> bool:
    probe = subprocess.run(
        [str(py), "-c", f"import {import_name}"],
        capture_output=True,
    )
    return probe.returncode == 0


def install(py: Path, packages: list[str]) -> bool:
    if not packages:
        return True
    print(f"[..] 安装依赖: {', '.join(packages)}")
    res = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", *packages],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"[err] 安装失败:\n{res.stderr}")
        return False
    print("[ok] 依赖安装完成")
    return True


def _check_system_commands() -> bool:
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe (install FFmpeg tools)")
    if shutil.which("espeak-ng") is None and shutil.which("espeak") is None:
        missing.append("espeak-ng (or espeak), required for offline TTS fallback")

    if missing:
        for name in missing:
            print(f"[miss] system command: {name}")
        print("Install the missing system tools and ensure they are available on PATH.")
        return False

    print("[ok] ffmpeg, ffprobe, and offline TTS command")
    return True


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Prepare/check the local video-factory runtime")
    parser.add_argument("--check", action="store_true", help="Check only; do not create the venv or install Python packages")
    parser.add_argument("--provider", choices=("mock", "openai", "gemini"), default="mock", help="Story provider used by this setup")
    parser.add_argument(
        "--tts",
        choices=("audio8", "auto", "edge", "espeak"),
        default="audio8",
        help="TTS provider used by this setup",
    )
    args = parser.parse_args(argv)

    check_only = args.check

    py = ensure_venv(check_only)

    dependencies = dict(DEPS)
    optional_provider = PROVIDER_DEPS.get(args.provider)
    if optional_provider:
        dependencies[optional_provider[0]] = optional_provider[1]
    optional_tts = TTS_DEPS.get(args.tts)
    if optional_tts:
        dependencies[optional_tts[0]] = optional_tts[1]

    missing: list[str] = []
    for import_name, pip_name in dependencies.items():
        if can_import(py, import_name):
            print(f"[ok] {pip_name}")
        else:
            print(f"[miss] {pip_name}")
            missing.append(pip_name)

    if missing:
        if check_only:
            print(f"\n缺 {len(missing)} 个依赖: {', '.join(missing)}")
            sys.exit(1)
        if not install(py, missing):
            sys.exit(1)

    if not _check_system_commands():
        sys.exit(1)

    # 末行：供调用方捕获的约定输出
    print(f"\nENV_PY={py}")


if __name__ == "__main__":
    main()
