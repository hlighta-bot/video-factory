import json
import shutil
import subprocess
import sys
from pathlib import Path

from tts.router import create_tts_provider
from renderers.router import create_renderer


ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# TTS 配置
# ============================================================

TTS_VOICE = "zh-CN-XiaoxiaoNeural"
TTS_RATE = "-10%"
TTS_PITCH = "-1Hz"


# ============================================================
# 基础工具
# ============================================================

def run(cmd):
    print()
    print(">>>", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def parse_srt(srt_path):

    text = Path(srt_path).read_text(
        encoding="utf-8"
    )

    blocks = []

    for block in text.strip().split("\n\n"):

        lines = [
            x.strip()
            for x in block.splitlines()
            if x.strip()
        ]

        if len(lines) < 3:
            continue

        number = lines[0]
        timing = lines[1]

        subtitle = " ".join(lines[2:])

        start, end = [
            x.strip()
            for x in timing.split("-->")
        ]

        blocks.append({
            "id": int(number),
            "start": start,
            "end": end,
            "text": subtitle
        })

    return blocks


def srt_time_to_seconds(value):

    value = value.replace(",", ".")

    h, m, s = value.split(":")

    return (
        int(h) * 3600
        + int(m) * 60
        + float(s)
    )


# ============================================================
# TTS
# ============================================================

def generate_all_tts(provider, subtitles, tts_dir):

    tts_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    audio_files = []

    for subtitle in subtitles:

        index = subtitle["id"]

        text = subtitle["text"]

        output = (
            tts_dir
            / f"{index:03d}.mp3"
        )

        print()
        print(
            f"[TTS] {index}: {text}"
        )

        provider.synthesize(
            text,
            output
        )

        audio_files.append({
            "id": index,
            "start": subtitle["start"],
            "end": subtitle["end"],
            "text": text,
            "file": output
        })

    return audio_files


# ============================================================
# TTS → 按 SRT 时间轴合成完整音频
# ============================================================

def build_audio_track(
    audio_files,
    output_audio
):

    if not audio_files:
        raise RuntimeError(
            "没有可用的 TTS 音频"
        )

    filters = []

    inputs = []

    for index, item in enumerate(audio_files):

        start_seconds = srt_time_to_seconds(
            item["start"]
        )

        delay_ms = round(
            start_seconds * 1000
        )

        inputs.extend([
            "-i",
            str(item["file"])
        ])

        filters.append(
            f"[{index}:a]"
            f"adelay={delay_ms}|{delay_ms}"
            f"[a{index}]"
        )

    mix_inputs = "".join(
        f"[a{i}]"
        for i in range(len(audio_files))
    )

    filters.append(
        f"{mix_inputs}"
        f"amix="
        f"inputs={len(audio_files)}:"
        f"duration=longest:"
        f"dropout_transition=0"
        f"[out]"
    )

    filter_complex = ";".join(filters)

    run([
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_audio)
    ])


# ============================================================
# 白板 + 音频 + 字幕
# ============================================================

def build_final_video(
    whiteboard_video,
    audio_file,
    srt_file,
    output_video
):

    # FFmpeg subtitles filter
    #
    # Linux / WSL 路径通常可以直接使用。
    # 将单引号转义，避免路径中有特殊字符。
    subtitle_path = str(
        srt_file
    ).replace(
        "\\",
        "/"
    ).replace(
        "'",
        r"\'"
    )

    subtitle_filter = (
        f"subtitles='{subtitle_path}'"
    )

    run([
        "ffmpeg",
        "-y",

        "-i",
        str(whiteboard_video),

        "-i",
        str(audio_file),

        "-vf",
        subtitle_filter,

        "-map",
        "0:v:0",
        "-map",
        "1:a:0",

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "18",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-pix_fmt",
        "yuv420p",

        "-shortest",

        str(output_video)
    ])


# ============================================================
# Main Pipeline
# ============================================================

def main(
    srt_path,
    image_path,
    output_dir,
    tts_name="edge",
    renderer_name="whiteboard",
):

    srt_path = Path(
        srt_path
    ).resolve()

    image_path = Path(
        image_path
    ).resolve()

    output_dir = Path(
        output_dir
    ).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    if not srt_path.exists():
        raise FileNotFoundError(
            srt_path
        )

    if not image_path.exists():
        raise FileNotFoundError(
            image_path
        )

    env_py = (
        ROOT
        / ".venv"
        / "bin"
        / "python"
    )

    if not env_py.exists():
        raise RuntimeError(
            "项目 .venv 不存在，请先执行 "
            "python scripts/prepare_env.py"
        )

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "没有找到 ffmpeg"
        )

    print("=" * 60)
    print("VIDEO FACTORY PIPELINE")
    print("=" * 60)

    # ========================================================
    # 1. SRT → Scene
    # ========================================================

    subtitles = parse_srt(
        srt_path
    )

    if not subtitles:
        raise RuntimeError(
            f"SRT 中没有找到有效字幕: "
            f"{srt_path}"
        )

    scene_json = (
        output_dir
        / "scene.json"
    )

    scene = {
        "sceneId": output_dir.name,
        "image": str(image_path),
        "srt": str(srt_path),
        "subtitles": subtitles,
        "subtitleCount": len(subtitles)
    }

    scene_json.write_text(
        json.dumps(
            scene,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        f"[ok] SRT → scene: "
        f"{len(subtitles)} subtitles"
    )

    # ========================================================
    # 2. Scene → Annotation
    # ========================================================

    annotation = (
        output_dir
        / f"{image_path.stem}.annotation.json"
    )

    run([
        str(env_py),
        str(
            ROOT
            / "scripts"
            / "auto_annotation.py"
        ),
        str(image_path),
        str(srt_path),
        str(annotation)
    ])

    # ========================================================
    # 3. Annotation → Preview
    # ========================================================

    preview = (
        output_dir
        / f"{image_path.stem}-preview.png"
    )

    run([
        str(env_py),
        str(
            ROOT
            / "scripts"
            / "render_annotation_preview.py"
        ),
        str(image_path),
        str(annotation),
        str(preview)
    ])

    print(
        f"[ok] preview: {preview}"
    )

    # ========================================================
    # 4. Annotation → Whiteboard MP4
    # ========================================================

    whiteboard_video = (
        output_dir
        / f"{image_path.stem}-whiteboard.mp4"
    )

    print()
    print("-" * 60)
    print(f"Renderer : {renderer_name}")
    print("-" * 60)

    renderer = create_renderer(renderer_name)

    render_scene = {
        "image": str(image_path),
        "annotation": str(annotation),
    }

    renderer.render(
        render_scene,
        whiteboard_video
    )

    print(
        f"[ok] whiteboard: "
        f"{whiteboard_video}"
    )

    # ========================================================
    # 5. SRT → TTS
    # ========================================================

    tts_dir = (
        output_dir
        / ".tts"
    )

    print()
    print("-" * 60)
    print(f"TTS Provider : {tts_name}")
    print("-" * 60)

    print(
        f"Voice : {TTS_VOICE}"
    )

    print(
        f"Rate  : {TTS_RATE}"
    )

    print(
        f"Pitch : {TTS_PITCH}"
    )

    provider = create_tts_provider(
        tts_name,
        voice=TTS_VOICE,
        rate=TTS_RATE,
        pitch=TTS_PITCH,
    )

    audio_files = generate_all_tts(
        provider,
        subtitles,
        tts_dir
    )

    # ========================================================
    # 6. TTS → 完整音频
    # ========================================================

    audio_track = (
        output_dir
        / f"{image_path.stem}-voice.m4a"
    )

    print()
    print("-" * 60)
    print("BUILD AUDIO TRACK")
    print("-" * 60)

    build_audio_track(
        audio_files,
        audio_track
    )

    print(
        f"[ok] audio: "
        f"{audio_track}"
    )

    # ========================================================
    # 7. Whiteboard + Voice + Subtitles
    # ========================================================

    final_video = (
        output_dir
        / f"{image_path.stem}-final.mp4"
    )

    print()
    print("-" * 60)
    print("FINAL VIDEO")
    print("-" * 60)

    build_final_video(
        whiteboard_video,
        audio_track,
        srt_path,
        final_video
    )

    print()
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)

    print(
        f"SRT        : {srt_path}"
    )

    print(
        f"Image      : {image_path}"
    )

    print(
        f"Scene      : {scene_json}"
    )

    print(
        f"Annotation : {annotation}"
    )

    print(
        f"Preview    : {preview}"
    )

    print(
        f"Whiteboard : {whiteboard_video}"
    )

    print(
        f"Voice      : {audio_track}"
    )

    print(
        f"FINAL MP4  : {final_video}"
    )


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    if len(sys.argv) < 4:

        print()

        print(
            "用法:"
        )

        print(
            "  python scripts/video_pipeline.py "
            "<SRT> <IMAGE> <OUTPUT_DIR> [--tts NAME] [--renderer NAME]"
        )

        print()

        print(
            "例如:"
        )

        print(
            "  $ENV_PY scripts/video_pipeline.py "
            "output/my-rocket/rocket.srt "
            "output/my-rocket/rocket.jpg "
            "output/my-rocket"
        )

        print(
            "  $ENV_PY scripts/video_pipeline.py "
            "output/my-rocket/rocket.srt "
            "output/my-rocket/rocket.jpg "
            "output/my-rocket --tts edge --renderer whiteboard"
        )

        sys.exit(1)

    # Parse --tts and --renderer arguments (defaults: edge, whiteboard)
    tts_name = "edge"
    renderer_name = "whiteboard"

    for i in range(4, len(sys.argv)):
        if sys.argv[i] == "--tts" and i + 1 < len(sys.argv):
            tts_name = sys.argv[i + 1]
        if sys.argv[i] == "--renderer" and i + 1 < len(sys.argv):
            renderer_name = sys.argv[i + 1]

    main(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        tts_name,
        renderer_name,
    )