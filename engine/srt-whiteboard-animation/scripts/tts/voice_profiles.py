"""Narration voice presets shared by every CLI entrypoint.

Purpose:
    Give the pipeline one named, reviewable voice contract instead of a hard
    coded voice id. ``--voice-profile`` selects a preset; ``--voice`` still
    overrides the raw engine voice id for one-off experiments.

Presets are ordered by target audience:
    * ``en-*`` presets target English-speaking (US/UK) audiences with the
      measured, low-pitched delivery familiar from documentary narration.
    * ``zh-*`` presets keep the historical Mandarin narration behavior.
    * ``*-local`` presets run fully offline through Piper (no network).

``auto`` (the default) inspects the first subtitle line and picks the English
preset for Latin-script text and the Mandarin preset for CJK text, so the same
command keeps working for both audiences without silently reading Chinese text
in an English voice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

DEFAULT_VOICE_PROFILE = "auto"

AUTO_EN_PROFILE = "en-us-narrator"
# Chinese keeps the historical Audio8 default so existing Chinese videos
# sound exactly as they did before the voice-profile layer was added.
AUTO_ZH_PROFILE = "zh-cn-natural"

_CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class VoiceProfile:
    """A single narration voice preset."""

    name: str
    label: str
    language: str
    provider: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    notes: str = ""

    def with_overrides(self, *, voice=None, rate=None, pitch=None, provider=None) -> "VoiceProfile":
        """Return a copy with explicit CLI overrides applied."""
        changes = {}
        if voice:
            changes["voice"] = voice
        if rate:
            changes["rate"] = rate
        if pitch:
            changes["pitch"] = pitch
        if provider:
            changes["provider"] = provider
        return replace(self, **changes) if changes else self


VOICE_PROFILES = {
    # ---------------- English (target audience: US/UK) ----------------
    "en-us-narrator": VoiceProfile(
        name="en-us-narrator",
        label="美式男声旁白（纪录片口吻，默认英文）",
        language="en-US",
        provider="edge",
        voice="en-US-AndrewNeural",
        rate="-4%",
        pitch="-2Hz",
        notes="语速略慢、音高略低，接近欧美纪录片/科普旁白的沉稳质感",
    ),
    "en-us-warm-female": VoiceProfile(
        name="en-us-warm-female",
        label="美式温暖女声旁白",
        language="en-US",
        provider="edge",
        voice="en-US-AvaNeural",
        rate="-3%",
        pitch="-1Hz",
        notes="适合故事、童话、口播类内容",
    ),
    "en-us-bright-female": VoiceProfile(
        name="en-us-bright-female",
        label="美式明亮女声旁白",
        language="en-US",
        provider="edge",
        voice="en-US-AriaNeural",
        rate="-2%",
        pitch="+0Hz",
        notes="更轻快，适合短视频快节奏开场",
    ),
    "en-gb-narrator": VoiceProfile(
        name="en-gb-narrator",
        label="英式男声旁白（BBC 口吻）",
        language="en-GB",
        provider="edge",
        voice="en-GB-RyanNeural",
        rate="-4%",
        pitch="-2Hz",
        notes="英式发音，适合历史、文化类叙述",
    ),
    "en-us-local-narrator": VoiceProfile(
        name="en-us-local-narrator",
        label="美式男声（Piper 本地离线）",
        language="en-US",
        provider="piper",
        voice="en_US-ryan-high",
        rate="+0%",
        pitch="+0Hz",
        notes="无网络时使用；Piper 本地音色，自然度低于 Edge",
    ),
    "en-gb-local-narrator": VoiceProfile(
        name="en-gb-local-narrator",
        label="英式男声（Piper 本地离线）",
        language="en-GB",
        provider="piper",
        voice="en_GB-northern_english_male-medium",
        rate="+0%",
        pitch="+0Hz",
        notes="无网络时使用；英格兰北部口音",
    ),
    # ---------------- Chinese (historical behavior) ----------------
    "zh-cn-narrator": VoiceProfile(
        name="zh-cn-narrator",
        label="中文女声旁白（Edge 晓晓）",
        language="zh-CN",
        provider="edge",
        voice="zh-CN-XiaoxiaoNeural",
        rate="-10%",
        pitch="-1Hz",
        notes="项目历史默认音色，保持向后兼容",
    ),
    "zh-cn-natural": VoiceProfile(
        name="zh-cn-natural",
        label="中文自然度优先（本地 Audio8）",
        language="zh-CN",
        provider="audio8",
        voice="default",
        rate="-10%",
        pitch="+0Hz",
        notes="本地 Audio8 服务，不支持 pitch",
    ),
}


def voice_profile_names():
    """Return sorted preset names for CLI help output."""
    return sorted(VOICE_PROFILES)


def voice_profiles():
    """Return presets ordered by name (used by ``--list-voices``)."""
    return [VOICE_PROFILES[name] for name in voice_profile_names()]


def detect_language(text: str) -> str:
    """Return ``zh`` when CJK characters dominate, else ``en``."""
    sample = str(text or "")
    if not sample.strip():
        return "en"
    cjk = len(_CJK_RE.findall(sample))
    letters = len(re.findall(r"[A-Za-z]", sample))
    if cjk and cjk >= letters * 0.3:
        return "zh"
    return "en"


def resolve_voice_profile(
    name=None,
    *,
    sample_text=None,
    voice=None,
    rate=None,
    pitch=None,
    provider=None,
) -> VoiceProfile:
    """Resolve a preset name (or ``auto``) into a concrete VoiceProfile.

    Raises:
        ValueError: unknown preset name.
    """
    key = (name or DEFAULT_VOICE_PROFILE).strip()
    if key == "auto":
        key = AUTO_ZH_PROFILE if detect_language(sample_text or "") == "zh" else AUTO_EN_PROFILE
    profile = VOICE_PROFILES.get(key)
    if profile is None:
        raise ValueError(
            "Unknown voice profile %r. Available: auto, " % name + ", ".join(voice_profile_names())
        )
    return profile.with_overrides(voice=voice, rate=rate, pitch=pitch, provider=provider)


def rate_to_length_scale(rate: str) -> float:
    """Convert an Edge-style rate string (``-10%``) into Piper length_scale."""
    text = str(rate or "").strip().replace("%", "")
    try:
        percent = float(text)
    except ValueError:
        return 1.0
    scale = 1.0 + percent / 100.0
    return round(1.0 / scale, 4) if scale > 0 else 1.0


def provider_fallback(name: str, *, language: str) -> str:
    """Map an unavailable provider to the closest working alternative.

    Edge needs a network round trip; when it fails the English presets fall back
    to the local Piper voices and the Mandarin preset falls back to the local
    Audio8 service, so a run never dies just because TTS cannot reach the cloud.
    """
    if name != "edge":
        return name
    return "audio8" if language.lower().startswith("zh") else "piper"
