"""Gemini Story Provider backed by the official google-genai SDK."""

from __future__ import annotations

import os

from .base import StoryProvider
from .config import load_project_env
from .openai_provider import SYSTEM_PROMPT


DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1024


class GeminiStoryProvider(StoryProvider):
    """Story provider that returns raw Gemini response text."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        client: object | None = None,
        load_env: bool = True,
    ):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "Gemini provider requires google-genai. Run "
                "python scripts/prepare_env.py --provider gemini"
            ) from exc

        if load_env:
            load_project_env()
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        self._model = model if model is not None else os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self._temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
        self._max_output_tokens = (
            DEFAULT_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
        )
        self._types = types
        self._client = client if client is not None else genai.Client(api_key=self._api_key or None)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    def generate(self, topic: str) -> str:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is required for gemini story provider")

        request = self._types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=topic,
                config=request,
            )
        except Exception as exc:
            raise RuntimeError(f"gemini provider error: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("gemini provider returned empty response")
        return text
