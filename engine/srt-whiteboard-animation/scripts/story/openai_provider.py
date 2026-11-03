"""OpenAI-compatible Story Provider backed by environment configuration."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .base import StoryProvider
from .config import load_project_env

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRIES = 2

SYSTEM_PROMPT = (
    "You generate a single JSON object matching the project Canonical Story schema exactly: "
    'top-level keys are only "version", "title", and "segments"; each segment object contains only '
    '"text". Version must be "1.0". Return valid JSON only; no markdown fences, no prose, no extra keys. '
    "Write the segments as visually concrete story beats: each line should name a subject, show a visible action, "
    "prefer one main event per segment, and keep sentences short so they can be rendered by the existing pipeline. "
    "Avoid abstract conclusions, motivational filler, invisible feelings, and long compound sentences."
)


class OpenAIChatProvider(StoryProvider):
    """Chat-completions provider that reads configuration from environment variables."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | str | None = None,
        retries: int | str | None = None,
        load_env: bool = True,
    ):
        if load_env:
            load_project_env()
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self._base_url = (base_url if base_url is not None else os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._model = model if model is not None else os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self._timeout = self._parse_timeout(timeout if timeout is not None else os.getenv("OPENAI_TIMEOUT", DEFAULT_TIMEOUT))
        self._retries = self._parse_retries(retries if retries is not None else os.getenv("OPENAI_RETRIES", DEFAULT_RETRIES))

    @staticmethod
    def _parse_timeout(value: float | str | int) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"OPENAI_TIMEOUT must be a positive number of seconds, got: {value!r}") from exc
        if timeout <= 0:
            raise ValueError(f"OPENAI_TIMEOUT must be a positive number of seconds, got: {value!r}")
        return timeout

    @staticmethod
    def _parse_retries(value: int | str) -> int:
        try:
            retries = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"OPENAI_RETRIES must be an integer in [0, 5], got: {value!r}") from exc
        if not 0 <= retries <= 5:
            raise ValueError(f"OPENAI_RETRIES must be an integer in [0, 5], got: {value!r}")
        return retries

    @property
    def name(self) -> str:
        return "openai"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def request_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def generate(self, topic: str) -> str:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is required for openai story provider")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": topic},
            ],
            "temperature": 0,
        }

        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self._retries:
                    raise RuntimeError(f"openai provider HTTP error {exc.code}: {detail[:500]}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self._retries:
                    raise RuntimeError(
                        f"openai provider request timed out or failed after {attempt + 1} attempt(s) "
                        f"(timeout={self._timeout:g}s): {exc}"
                    ) from exc
                last_error = exc
            delay = min(2 ** attempt, 8)
            print(f"[warn] Story API request failed; retrying in {delay}s ({attempt + 1}/{self._retries}): {last_error}")
            time.sleep(delay)

        data = json.loads(raw)
        try:
            return data["choices"][0]["message"]["content"]
        except KeyError as exc:
            raise RuntimeError(f"openai provider returned unexpected response schema: {raw[:500]}") from exc
