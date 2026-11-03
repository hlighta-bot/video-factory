"""Phase 29 .env configuration smoke test for OpenAI-compatible Story Provider.

Purpose:
- Verify .env loading is used by the existing openai provider.
- Verify shell environment variables still take precedence over .env.
- Verify SenseNova-compatible base URL is normalized into a single /chat/completions endpoint.
- Verify model and timeout values are read from configuration instead of being hardcoded.

Input data:
- Project-root .env file written temporarily by the test.

Output data:
- Console assertions only; no API keys or secrets are written.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import story.config as story_config
from story.openai_provider import OpenAIChatProvider
from story.router import create_story_provider


SCRIPT_START = time.time()
SENSENOVA_BASE_URL = "https://token.sensenova.cn/v1"
SENSENOVA_MODEL = "sensenova-6.8-flash-lite"
SENSENOVA_TIMEOUT = "120"


def _write_env(path: Path, api_key: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"OPENAI_API_KEY={api_key}",
                f"OPENAI_BASE_URL={SENSENOVA_BASE_URL}",
                f"OPENAI_MODEL={SENSENOVA_MODEL}",
                f"OPENAI_TIMEOUT={SENSENOVA_TIMEOUT}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _expect_timeout_error(timeout: object) -> None:
    try:
        OpenAIChatProvider(timeout=timeout, load_env=True)
    except ValueError as exc:
        assert "OPENAI_TIMEOUT must be a positive number" in str(exc)
    else:
        raise AssertionError("invalid OPENAI_TIMEOUT was accepted")


def main() -> int:
    saved_env = {
        key: os.environ.get(key)
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_TIMEOUT")
    }
    project_root = story_config.project_root()
    env_path = project_root / ".env"
    backup_env = env_path.read_text(encoding="utf-8") if env_path.exists() else None

    try:
        _write_env(env_path, "env-file-key")
        for key in saved_env:
            os.environ.pop(key, None)

        provider = create_story_provider("openai")
        assert provider.name == "openai"
        assert provider.model == SENSENOVA_MODEL
        assert provider.timeout == 120.0
        assert provider.request_url == f"{SENSENOVA_BASE_URL}/chat/completions"
        assert provider.request_url.count("/chat/completions") == 1
        assert provider.request_url != f"{SENSENOVA_BASE_URL}/chat/completions/chat/completions"

        os.environ["OPENAI_API_KEY"] = "shell-key"
        shell_provider = OpenAIChatProvider(api_key="shell-key", load_env=True)
        assert shell_provider.model == SENSENOVA_MODEL
        assert shell_provider.timeout == 120.0
        assert shell_provider.request_url == f"{SENSENOVA_BASE_URL}/chat/completions"
        assert shell_provider._api_key == "shell-key"

        os.environ.pop("OPENAI_TIMEOUT", None)
        default_timeout_provider = OpenAIChatProvider(timeout=None, load_env=True)
        assert default_timeout_provider.timeout == 120.0

        _expect_timeout_error("not-a-number")
        _expect_timeout_error(0)

        report = {
            "phase": 29,
            "status": "pass",
            "provider": "openai",
            "base_url": provider.base_url,
            "request_url": provider.request_url,
            "model": provider.model,
            "timeout": provider.timeout,
            "default_timeout": default_timeout_provider.timeout,
            "env_loaded": True,
            "api_key_from_env": provider._api_key == "env-file-key",
            "api_key_from_shell_override": shell_provider._api_key == "shell-key",
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }
        assert not any(key == "api_key" for key in report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if backup_env is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_text(backup_env, encoding="utf-8")
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(f"elapsed_sec={round(time.time() - SCRIPT_START, 3):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
