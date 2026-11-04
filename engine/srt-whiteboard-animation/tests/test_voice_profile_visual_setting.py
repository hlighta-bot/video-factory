#!/usr/bin/env python3
"""回归测试：语音档案（--voice-profile）与全片视觉设定（--visual-setting）。

覆盖：
    * auto 语音档案按字幕语种选择英文/中文音色
    * CLI 覆盖项（voice/rate/pitch）与非法档案名
    * 全片视觉设定的启发式兜底、序列化往返、角色匹配
    * build_scene_image_prompt 是否把视觉设定注入到每一幕提示词
    * SenseNova prompt_extend 默认关闭（否则会冲掉固定人物设定）

运行：
    PYTHONPATH=scripts .venv/bin/python tests/test_voice_profile_visual_setting.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tts.voice_profiles import (
    DEFAULT_VOICE_PROFILE,
    detect_language,
    resolve_voice_profile,
    voice_profile_names,
    voice_profiles,
)
from visual_setting import (
    CharacterSheet,
    VisualSetting,
    _looks_chinese,
    heuristic_visual_setting,
)


def test_detect_language():
    assert detect_language("七仙女下凡来到人间") == "zh"
    assert detect_language("The seven fairies descended to the mortal world") == "en"
    assert detect_language("") == "en"


def test_auto_profile_follows_narration_language():
    assert DEFAULT_VOICE_PROFILE == "auto"
    zh = resolve_voice_profile("auto", sample_text="董永在槐树下遇见七仙女")
    en = resolve_voice_profile("auto", sample_text="Dong Yong met the seventh fairy")
    # Chinese keeps the legacy Audio8 default (unchanged behaviour for zh videos)
    assert zh.name == "zh-cn-natural", zh
    assert zh.provider == "audio8", zh
    assert en.name == "en-us-narrator", en
    assert en.language.startswith("en")
    assert en.provider == "edge"
    assert en.voice == "en-US-AndrewNeural"


def test_profile_overrides_and_errors():
    profile = resolve_voice_profile("en-us-narrator", voice="en-US-AvaNeural", rate="-2%", pitch="-4Hz")
    assert profile.voice == "en-US-AvaNeural"
    assert profile.rate == "-2%"
    assert profile.pitch == "-4Hz"
    untouched = resolve_voice_profile("en-gb-narrator")
    assert untouched.rate == "-4%"
    try:
        resolve_voice_profile("no-such-voice")
    except ValueError as exc:
        assert "Unknown voice profile" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown profile")


def test_profile_catalog():
    names = voice_profile_names()
    assert "en-us-narrator" in names and "zh-cn-narrator" in names
    assert "en-us-local-narrator" in names
    listing = voice_profiles()
    assert len(listing) == len(names)
    for profile in listing:
        assert profile.language and profile.provider and profile.voice


def test_heuristic_visual_setting_is_chinese_for_chinese_narration():
    assert _looks_chinese("七仙女与董永的传说")
    setting = heuristic_visual_setting("七仙女", ["七仙女下凡", "董永在槐树下相会"])
    assert "Chinese" in setting.culture
    block = setting.render("七仙女下凡")
    assert "FILM-WIDE VISUAL SETTING" in block
    assert "Culture & casting" in block
    assert "Chinese" in block
    assert "text" in block.lower()


def test_visual_setting_roundtrip_and_character_matching():
    setting = VisualSetting(
        culture="Chinese classical mythology",
        era="imperial China",
        art_style="ink illustration",
        palette="jade and cinnabar",
        environment="temple halls",
        prohibitions="no text",
        characters=[
            CharacterSheet(
                name="七仙女",
                description="Chinese immortal maiden with East Asian features and black hair",
                costume="flowing white and pink hanfu with silk ribbons",
                color_notes="white, pale pink, jade",
                aliases=["seventh fairy"],
            ),
            CharacterSheet(
                name="董永",
                description="young Chinese farmer",
                costume="coarse indigo hanfu",
                aliases=["dong yong"],
            ),
        ],
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "visual-setting.json"
        setting.save(path)
        loaded = VisualSetting.load(path)
        assert loaded.to_dict()["characters"] == setting.to_dict()["characters"]
        assert loaded.culture == setting.culture and inverted_ok(loaded, setting)

    matched = setting.matching_characters("董永在田里耕作")
    assert [c.name for c in matched] == ["董永"]

    fallback = setting.matching_characters("一片云雾飘过")
    assert [c.name for c in fallback] == ["七仙女", "董永"]

    block = setting.render("the seventh fairy descends")
    assert "七仙女" in block
    assert "hanfu" in block


def inverted_ok(loaded, original):
    return loaded.era == original.era and loaded.palette == original.palette


def test_scene_prompt_injects_visual_setting():
    from image_generation.sensenova import build_scene_image_prompt

    scene = type(
        "SceneStub",
        (),
        {
            "narration": "七仙女从云端缓缓落下，凡人抬头仰望",
            "subtitles": [],
            "visual": {
                "visual_plan": {
                    "semantic": {
                        "subject": "七仙女",
                        "objects": ["cloud", "槐树"],
                        "environment": "village",
                        "action": "descend",
                    }
                }
            },
        },
    )()

    plain = build_scene_image_prompt(scene)
    assert plain.startswith("Create one beautiful wide 16:9 illustration")
    assert "FILM-WIDE VISUAL SETTING" not in plain

    setting = heuristic_visual_setting("七仙女", [scene.narration])
    injected = build_scene_image_prompt(scene, setting)
    assert "FILM-WIDE VISUAL SETTING" in injected
    assert "Chinese" in injected
    assert injected.index("FILM-WIDE VISUAL SETTING") < injected.index("Create one beautiful wide 16:9")
    assert "七仙女从云端缓缓落下" in injected


def test_prompt_extend_defaults_to_false():
    from image_generation.sensenova import SenseNovaImageProvider

    try:
        provider = SenseNovaImageProvider(load_env=False, api_key="test-key", base_url="https://example.invalid/v1")
    except RuntimeError:
        return
    assert provider.prompt_extend is False


def main():
    tests = [
        test_detect_language,
        test_auto_profile_follows_narration_language,
        test_profile_overrides_and_errors,
        test_profile_catalog,
        test_heuristic_visual_setting_is_chinese_for_chinese_narration,
        test_visual_setting_roundtrip_and_character_matching,
        test_scene_prompt_injects_visual_setting,
        test_prompt_extend_defaults_to_false,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print("ALL OK")


if __name__ == "__main__":
    main()
