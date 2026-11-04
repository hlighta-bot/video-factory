"""Film-level visual setting ("visual bible") reused by every scene prompt.

Problem this solves:
    SenseNova is called once per storyboard scene, and each prompt only
    described that scene's subject and action. Nothing carried character
    appearance, costume, era or cultural style across scenes, so the same film
    could open with Western-looking immortals and continue with Chinese-looking
    characters.

Contract:
    One ``VisualSetting`` per film. It is either

    * ``auto``   - derived once from the narration (title + subtitle lines) via
                   the OpenAI-compatible chat endpoint, with a deterministic
                   heuristic fallback when the API is unavailable;
    * ``<path>`` - a hand written / previously generated JSON file, reused as
                   is so re-runs stay byte-identical;
    * ``none``   - legacy behavior, no film-level block injected.

    ``VisualSetting.render(scene_text)`` produces the block that is prepended to
    every scene prompt. Characters are matched against the current scene text so
    each prompt only restates the cast that actually appears, using exactly the
    same wording every time.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

VISUAL_SETTING_FILENAME = "visual-setting.json"

_CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

DEFAULT_ART_STYLE = (
    "polished storybook editorial illustration; painterly texture, layered depth, "
    "deliberate composition, rich but natural colors, soft cinematic lighting"
)
DEFAULT_PROHIBITIONS = (
    "no text, letters, captions, subtitles, labels, borders, logos or watermarks; "
    "no modern clothing, modern devices or contemporary props; "
    "no mixing of cultures within one framing"
)

_SETTING_SYSTEM_PROMPT = (
    "You are the art director of a narrated illustration film. Read the film title and "
    "narration lines and return ONE canonical visual setting that will be reused verbatim in "
    "every scene prompt, so the cast and culture can never drift between scenes.\n"
    "Rules:\n"
    "1. Infer the culture of the source material from the narration itself. If the narration is "
    "Chinese (Chinese mythology, folklore, history, idioms, or Chinese proper names), then EVERY "
    "human character must be explicitly described as Chinese / East Asian with classical Chinese "
    "costume and hairstyle. Never describe such characters with Western facial features, blond or "
    "brown hair, blue eyes, Western dress or Victorian/medieval European costume.\n"
    "2. Keep one single era and one single art style for the whole film.\n"
    "3. Describe each named character with 1-2 concrete, generation-friendly English sentences "
    "covering face/ethnicity, hair/hair-style, costume and color notes. Use the same character "
    "names as the narration.\n"
    "4. Return strict JSON only, no markdown fences, with exactly these keys: "
    '"culture", "era", "art_style", "palette", "environment", "prohibitions", "characters". '
    '"characters" is an array of objects with keys "name", "aliases" (array of strings), '
    '"description", "costume", "color_notes".'
)


@dataclass
class CharacterSheet:
    """Canonical look of one character, reused in every scene it appears in."""

    name: str
    description: str = ""
    costume: str = ""
    color_notes: str = ""
    aliases: list = field(default_factory=list)

    def render(self) -> str:
        parts = [self.description.strip()]
        if self.costume.strip():
            parts.append("costume: " + self.costume.strip())
        if self.color_notes.strip():
            parts.append("colors: " + self.color_notes.strip())
        body = "; ".join(part for part in parts if part)
        return f"{self.name}: {body}" if body else self.name

    def matches(self, text: str) -> bool:
        haystack = (text or "").lower()
        if not haystack:
            return False
        for token in [self.name, *self.aliases]:
            needle = str(token or "").strip().lower()
            if needle and needle in haystack:
                return True
        return False


@dataclass
class VisualSetting:
    """Film-wide visual contract injected into every scene prompt."""

    culture: str = ""
    era: str = ""
    art_style: str = DEFAULT_ART_STYLE
    palette: str = ""
    environment: str = ""
    prohibitions: str = DEFAULT_PROHIBITIONS
    characters: list = field(default_factory=list)
    source: str = "auto"

    # ------------------------------------------------------------------ helpers
    def matching_characters(self, scene_text: str, limit: int = 3):
        """Characters referenced by the scene text (falls back to the lead cast)."""
        matched = [c for c in self.characters if c.matches(scene_text)]
        if not matched:
            matched = list(self.characters)[:limit]
        return matched[:limit]

    def render(self, scene_text: str = "", max_characters: int = 3) -> str:
        """Return the reusable prompt block for one scene."""
        lines = ["FILM-WIDE VISUAL SETTING (identical in every scene; never contradict it):"]
        if self.culture:
            lines.append("- Culture & casting: " + self.culture)
        if self.era:
            lines.append("- Era & world: " + self.era)
        if self.art_style:
            lines.append("- Art style: " + self.art_style)
        if self.palette:
            lines.append("- Palette & lighting: " + self.palette)
        if self.environment:
            lines.append("- Environment language: " + self.environment)
        cast = self.matching_characters(scene_text, max_characters)
        if cast:
            lines.append(
                "- Character sheet (reuse these exact looks and outfits): "
                + " | ".join(item.render() for item in cast)
            )
        if self.prohibitions:
            lines.append("- Forbidden: " + self.prohibitions)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["characters"] = [asdict(c) if isinstance(c, CharacterSheet) else c for c in self.characters]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "VisualSetting":
        characters = [
            item if isinstance(item, CharacterSheet) else CharacterSheet(**item)
            for item in (data.get("characters") or [])
        ]
        return cls(
            culture=str(data.get("culture") or ""),
            era=str(data.get("era") or ""),
            art_style=str(data.get("art_style") or DEFAULT_ART_STYLE),
            palette=str(data.get("palette") or ""),
            environment=str(data.get("environment") or ""),
            prohibitions=str(data.get("prohibitions") or DEFAULT_PROHIBITIONS),
            characters=characters,
            source=str(data.get("source") or "auto"),
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> "VisualSetting":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        setting = cls.from_dict(data)
        setting.source = str(path)
        return setting


# ---------------------------------------------------------------- construction
def _looks_chinese(text: str) -> bool:
    return len(_CJK_RE.findall(text or "")) >= 4


def heuristic_visual_setting(title: str, lines, *, source: str = "heuristic") -> VisualSetting:
    """Deterministic fallback when no chat endpoint is available."""
    corpus = " ".join([str(title or "")] + [str(line or "") for line in lines])
    if _looks_chinese(corpus):
        culture = (
            "Chinese classical mythology / Chinese folklore. Every human figure is Chinese "
            "(East Asian facial features, black hair, no Western features)."
        )
        era = "pre-modern imperial China; undefined dynasty, classical clothing and architecture"
        palette = "muted ink-and-wash neutrals with restrained red, jade-green and gold accents"
        return VisualSetting(
            culture=culture,
            era=era,
            art_style=DEFAULT_ART_STYLE,
            palette=palette,
            environment="classical Chinese settings: temple halls, tiled roofs, silk drapery, "
            "willow trees, cloud-and-mist heavens",
            prohibitions=DEFAULT_PROHIBITIONS
            + "; no Western clothing, no European architecture, no mixed-ethnic casting",
            characters=[
                CharacterSheet(
                    name="all human characters",
                    description="Chinese people with East Asian facial features and black hair",
                    costume="classical Chinese robes (hanfu style) appropriate to the era",
                    color_notes="restrained silk tones: white, pale blue, jade green, cinnabar red",
                    aliases=[],
                )
            ],
            source=source,
        )
    return VisualSetting(
        culture="consistent single-culture cast; keep ethnicity, age range and facial features "
        "identical across scenes",
        era="one coherent historical or timeless setting shared by all scenes",
        art_style=DEFAULT_ART_STYLE,
        palette="muted natural palette with a small number of accent colors reused in every scene",
        environment="simple, readable environments that share one architectural and material language",
        prohibitions=DEFAULT_PROHIBITIONS,
        characters=[],
        source=source,
    )


def _parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def generate_visual_setting(title, lines, *, chat=None, model_note: str = "") -> VisualSetting:
    """Derive one film-wide visual setting from the narration.

    Args:
        title: film/story title.
        lines: narration lines (subtitle texts).
        chat: callable ``chat(system_prompt, user_prompt) -> str``. When omitted
            the project's OpenAI-compatible provider is used if configured.

    Returns:
        VisualSetting; falls back to :func:`heuristic_visual_setting` when the
        chat endpoint is unavailable or returns unusable JSON.
    """
    lines = [str(item) for item in (lines or [])]
    user_prompt = (
        f"Film title: {title or '(untitled)'}\n"
        f"Narration ({len(lines)} lines):\n" + "\n".join(f"- {line}" for line in lines[:80])
    )

    if chat is None:
        try:
            chat = _default_chat()
        except Exception as exc:  # configuration missing -> heuristic
            print(f"[warn] visual setting: no chat endpoint ({exc}); using heuristic defaults")
            chat = None

    if chat is not None:
        try:
            raw = chat(_SETTING_SYSTEM_PROMPT, user_prompt)
            data = _parse_json_object(raw)
            setting = VisualSetting.from_dict(data)
            setting.source = "auto" + (f" ({model_note})" if model_note else "")
            if not setting.culture or not setting.era:
                raise ValueError("visual setting missing culture/era")
            if _looks_chinese(" ".join(lines)) and _mentions_western(setting):
                print("[warn] visual setting mentioned Western features for Chinese source; "
                      "keeping the generated setting but flag the prompts for review")
            return setting
        except Exception as exc:
            print(f"[warn] visual setting generation failed ({exc}); using heuristic defaults")

    return heuristic_visual_setting(title, lines)


def _mentions_western(setting: VisualSetting) -> bool:
    corpus = " ".join(
        [setting.culture, setting.era]
        + [c.description + " " + c.costume for c in setting.characters]
    ).lower()
    return any(
        token in corpus
        for token in ("western", "european", "caucasian", "blond", "blue eyes", "victorian", "medieval")
    )


DEFAULT_VISUAL_SETTING_PROVIDERS = "zhipu,gemini,openai"


def _build_chat_provider(name: str):
    """Instantiate a chat-capable provider by short name."""
    if name == "zhipu":
        from story.zhipu_provider import ZhipuChatProvider

        provider = ZhipuChatProvider(load_env=True)
        key = getattr(provider, "_api_key", "")
        if not key:
            raise RuntimeError("ZHIPU_API_KEY not configured")
        return provider
    if name == "gemini":
        from story.gemini_provider import GeminiStoryProvider

        provider = GeminiStoryProvider(load_env=True)
        key = getattr(provider, "_api_key", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        return provider
    if name == "openai":
        from story.openai_provider import OpenAIChatProvider

        provider = OpenAIChatProvider(load_env=True)
        key = getattr(provider, "_api_key", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY / SENSENOVA_API_KEY not configured")
        return provider
    raise RuntimeError(f"unknown visual setting provider: {name}")


def _default_chat():
    """Return a chat callable that walks a provider fallback chain.

    Chain order comes from ``VISUAL_SETTING_PROVIDER`` (comma separated, default
    ``zhipu,gemini,openai``). Each provider is tried in turn so one busy or
    unreachable endpoint does not drop the film back to heuristic defaults.
    """
    import os

    raw = (
        os.getenv("VISUAL_SETTING_PROVIDER")
        or f"zhipu,{os.getenv('STORY_PROVIDER', '')},openai"
    )
    chain: list[str] = []
    for item in str(raw).split(","):
        name = item.strip().lower()
        if name and name not in chain:
            chain.append(name)
    if not chain:
        chain = ["openai"]

    def _chat(system_prompt: str, user_prompt: str) -> str:
        errors: list[str] = []
        for name in chain:
            try:
                provider = _build_chat_provider(name)
                return provider.chat(system_prompt, user_prompt)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                errors.append(f"{name}: {exc}")
                print(f"[warn] visual setting provider {name} failed: {exc}")
        raise RuntimeError("; ".join(errors) or "no visual setting provider available")

    return _chat


def load_or_build_visual_setting(
    mode,
    title,
    lines,
    *,
    output_dir=None,
    chat=None,
) -> "VisualSetting | None":
    """Resolve the ``--visual-setting`` CLI value into a VisualSetting.

    Args:
        mode: ``None``/``auto``/``none``/``<path>``.
        title: film title used for auto derivation.
        lines: narration lines used for auto derivation.
        output_dir: when given, generated settings are persisted to
            ``<output_dir>/.visual/visual-setting.json`` for review and reuse.
    """
    key = str(mode or "auto").strip()
    if key.lower() == "none":
        return None
    if key.lower() != "auto":
        path = Path(key)
        if not path.exists():
            raise FileNotFoundError(f"--visual-setting file not found: {path}")
        setting = VisualSetting.load(path)
        print(f"Visual setting: loaded {path}")
        return setting

    setting = generate_visual_setting(title, lines, chat=chat)
    if output_dir is not None:
        target = Path(output_dir) / ".visual" / VISUAL_SETTING_FILENAME
        setting.save(target)
        print(f"Visual setting: {target} ({setting.source})")
    for line in setting.render().splitlines():
        print("  " + line)
    return setting
