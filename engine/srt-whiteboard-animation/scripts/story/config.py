"""Minimal project-local .env loader for Story Provider configuration.

Purpose:
- Load project-root .env values without introducing a new dependency.
- Preserve existing shell environment values, so exported variables still win.
- Provide a small project root resolver for tests and provider code.

Input data:
- video-factory/engine/srt-whiteboard-animation/.env

Output data:
- Updated os.environ entries for variables missing from the current environment.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_env_path() -> Path:
    return project_root() / ".env"


def load_project_env(**_kwargs) -> bool:
    """Load project-local .env values without overriding existing shell variables."""

    env_path = project_env_path()
    if not env_path.exists():
        return False

    loaded_any = False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded_any = True
    return loaded_any
