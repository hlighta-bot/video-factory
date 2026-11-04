"""Zhipu (BigModel / GLM) OpenAI-compatible Story Provider.

Reads its own environment block so the project can keep an existing
OpenAI-compatible provider (e.g. SenseNova) configured at the same time.

Input data (project .env or exported shell vars):
- ZHIPU_API_KEY        required, BigModel API key
- ZHIPU_BASE_URL       default https://open.bigmodel.cn/api/paas/v4
- ZHIPU_TEXT_MODEL     default glm-4.7-flash
- ZHIPU_VISION_MODEL   default glm-4.6v-flash (reserved for vision helpers)
- ZHIPU_TIMEOUT        default follows OPENAI_TIMEOUT (300s)
- ZHIPU_RETRIES        default follows OPENAI_RETRIES (2)

Output data:
- OpenAIChatProvider-compatible instance pointed at the GLM text endpoint.
"""

from __future__ import annotations

import os

from .config import load_project_env
from .openai_provider import DEFAULT_RETRIES, DEFAULT_TIMEOUT, OpenAIChatProvider

DEFAULT_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_ZHIPU_TEXT_MODEL = "glm-4.7-flash"
DEFAULT_ZHIPU_VISION_MODEL = "glm-4.6v-flash"


class ZhipuChatProvider(OpenAIChatProvider):
    """GLM chat provider, wire-compatible with the OpenAI chat schema."""

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
        # Load the shared .env first so OPENAI_* defaults stay available.
        if load_env:
            load_project_env()
        super().__init__(
            api_key=api_key if api_key is not None else os.getenv("ZHIPU_API_KEY", ""),
            base_url=(
                base_url
                if base_url is not None
                else os.getenv("ZHIPU_BASE_URL", DEFAULT_ZHIPU_BASE_URL)
            ),
            model=(
                model
                if model is not None
                else os.getenv("ZHIPU_TEXT_MODEL", DEFAULT_ZHIPU_TEXT_MODEL)
            ),
            timeout=(
                timeout
                if timeout is not None
                else os.getenv(
                    "ZHIPU_TIMEOUT", os.getenv("OPENAI_TIMEOUT", DEFAULT_TIMEOUT)
                )
            ),
            retries=(
                retries
                if retries is not None
                else os.getenv(
                    "ZHIPU_RETRIES", os.getenv("OPENAI_RETRIES", DEFAULT_RETRIES)
                )
            ),
            load_env=False,
        )

    @property
    def name(self) -> str:
        return "zhipu"

    def _request(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        """Fail with a Zhipu-specific message before delegating to the base class."""
        if not self._api_key:
            raise RuntimeError("ZHIPU_API_KEY is required for zhipu story provider")
        return super()._request(system_prompt, user_prompt, temperature)
