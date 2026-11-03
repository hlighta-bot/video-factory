"""Canonical Story Generator 包，负责 prompt -> validated Story JSON。"""

from .base import StoryProvider
from .config import load_project_env
from .generator import StoryGenerationError, generate_story
from .gemini_provider import GeminiStoryProvider
from .mock_provider import MockStoryProvider
from .openai_provider import OpenAIChatProvider
from .router import create_story_provider

load_project_env()

__all__ = [
    "StoryProvider",
    "StoryGenerationError",
    "generate_story",
    "load_project_env",
    "MockStoryProvider",
    "OpenAIChatProvider",
    "GeminiStoryProvider",
    "create_story_provider",
]
