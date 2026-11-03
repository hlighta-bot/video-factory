# 使用指南：从文案到白板动画视频

这个项目支持两条主路径生成视频：

1. **Story 自动流程**：从主题或文案生成 Story JSON，再生成 SRT，最后渲染成 MP4。
2. **手动图片流程**：直接指定 SRT 和图片，生成单幕白板动画。

两条路径都最终输出：
- `scene.json`
- 中间音频/标注等产物
- 最终 MP4

## 先决条件

- 已安装 Python
- 系统 `PATH` 上可用 `ffmpeg`、`ffprobe` 和 `espeak-ng`（或 `espeak`）
- 首次使用前先初始化项目环境

```bash
python3 scripts/prepare_env.py --provider mock --tts audio8
python3 scripts/prepare_env.py --check --provider mock --tts audio8
```

`prepare_env.py` 会安装视频流水线的 Python 依赖，并检查系统命令。默认语音使用本机 Audio8 TTS（经 tts-hub），无需安装 Edge SDK；Audio8 与 tts-hub 服务会在首次合成时按接入规格自动启动。Gemini provider 需要额外的 `google-genai`：运行 `python3 scripts/prepare_env.py --provider gemini`。若希望 Audio8 不可用时自动降级到本地 eSpeak-NG，可显式指定 `--tts auto`；只使用离线合成语音指定 `--tts espeak`。`--tts edge` 仍可用于显式选择旧服务。

Ubuntu/WSL 缺少系统工具时可先运行：

```bash
sudo apt-get update
sudo apt-get install ffmpeg espeak-ng
```

要调用在线模型，把 `.env.example` 复制为 `.env`（已有 `.env` 时不要覆盖），填入 `OPENAI_API_KEY` 或 `GEMINI_API_KEY`。OpenAI-compatible provider 使用 Python 标准库发请求；Gemini 还需要按上面的命令安装 SDK。

之后优先使用项目内的虚拟环境：

```bash
.venv/bin/python
```

## 推荐路径：Story 自动流程

适合“我只有主题/文案，想要一条成片”的场景。

如果从主题开始，可以用一个命令串起 Story 生成、SRT 和视频制作：

```bash
.venv/bin/python scripts/story_from_topic.py \
  --topic "一个关于火箭发射的简短故事" \
  --provider openai \
  --output-dir output/my-video \
  --renderer auto \
  --tts audio8 \
  --image-provider sensenova
```

`--provider` 也支持 `gemini`；`mock` 适合不调用在线模型的流程演练。输出目录必须为空，以免覆盖已有产物。

先确认本机完整链路可启动时，可用 `--provider mock --tts espeak` 生成本地演练成片。要生成 AI 文案，选择 `openai` 或 `gemini`，并在项目 `.env` 中填入对应 API key；环境模板见 `.env.example`。

默认使用 Audio8 TTS。需能访问本机 Audio8（默认 `127.0.0.1:8024`）和 tts-hub（默认 `127.0.0.1:18084`）；缺服务时会尝试运行本机启动脚本，且请求禁用 Edge 回退。可通过 `AUDIO8_SERVICE_URL`、`TTS_HUB_URL`、`AUDIO8_VOICE` 配置服务和音色。`--tts auto` 会在 Audio8 不可用时回退到 eSpeak；`--tts espeak` 可显式选择离线语音，声音较机械。Audio8 不支持 pitch，运行清单会注明该限制。

`--image-provider sensenova` 会调用 SenseNova U1.5 Lite 为每个分镜单独生成一张 16:9 插画，保存在输出目录的 `.images/`，并使用电影式镜头运动渲染。它需要 `.env` 中现有的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` SenseNova 配置，也可用 `SENSENOVA_API_KEY` / `SENSENOVA_BASE_URL` 单独配置；模型可通过 `SENSENOVA_IMAGE_MODEL` 覆盖。每个分镜会产生一次图像 API 请求；不传此参数时仍使用本地程序化画面和素材解析。

若文案生成或 SenseNova 出图遇到临时超时，文案和图像 API 默认最多重试 2 次，超时时间默认 300 秒，可通过 `.env` 中的 `OPENAI_TIMEOUT`、`OPENAI_RETRIES`、`SENSENOVA_IMAGE_TIMEOUT`、`SENSENOVA_IMAGE_RETRIES` 调整。图像阶段中断后，使用原命令并添加 `--resume`，会读取已有 `story.json` 和 SRT，校验并复用 `.images/` 中已完成的图片，只继续缺少的分镜；视频或 TTS 阶段已有产物时仍应选择新的输出目录。

```bash
.venv/bin/python scripts/story_from_topic.py \
  --topic "写一个介绍七仙女的故事" \
  --provider openai \
  --output-dir output/my-video1 \
  --renderer auto \
  --tts audio8 \
  --image-provider sensenova \
  --resume
```

### 1) 生成 Story JSON

```bash
.venv/bin/python scripts/generate_story.py \
  --topic "一个关于火箭发射的简短故事" \
  --provider mock \
  --output output/my-story.json
```

生成结果示例：

```json
{
  "version": "1.0",
  "title": "Rocket Launch",
  "segments": [
    { "text": "The rocket ignites." },
    { "text": "It rises into space." }
  ]
}
```

如果你已经有自己的文案，也可以直接手写同格式的 `story.json`。

### 2) Story → SRT → MP4

```bash
.venv/bin/python scripts/story_pipeline.py \
  output/my-story.json \
  --output output/my-video \
  --renderer auto \
  --tts audio8
```

这个命令会做三件事：

- 校验 Story JSON
- 生成确定性 SRT
- 调用视频管线渲染最终 MP4

常见输出文件会出现在 `--output` 指向的目录中，例如：

- `output/my-video/scene.json`
- `output/my-video/.images/scene-01.png` (SenseNova 选项开启时)
- `output/my-video/whiteboard.mp4`
- `output/my-video/final.mp4`

> 如果只想要画面，不想带语音，可以加 `--render-only`。

## 可选路径：手动图片流程

适合“我已经有字幕和图片，只想渲染单幕”的场景。

### 1) 如果你已经有 SRT

先检查一下分镜建议：

```bash
.venv/bin/python scripts/parse_srt.py input.srt \
  --target-sec 30 \
  --min-sec 25 \
  --max-sec 35
```

这个命令会输出：
- 每条字幕的起止时间
- 建议分镜场景
- 每个场景的 `sceneDurationMs`

### 2) 直接渲染带图白板动画

```bash
.venv/bin/python scripts/video_pipeline.py \
  input.srt \
  --image assets/whiteboard/demo.png \
  --output output/single-scene \
  --renderer whiteboard \
  --tts audio8
```

这会让系统：
- 读取 SRT
- 使用你提供的图片
- 自动标注
- 渲染白板动画
- 合成最终 MP4

如果你已经有标注，也可以复用对应流程产物，不需要重写整条管线。

## 资产与画面的两种模式

### 模式 A：自动资产

如果你不传 `--image`，系统会走自动资产解析和自动标注链路。

典型命令：

```bash
.venv/bin/python scripts/story_pipeline.py \
  output/my-story.json \
  --output output/my-video \
  --renderer auto \
  --tts audio8
```

适合：
- 快速出片
- 没有单独准备线稿图
- 想直接用本地素材库和自动注释生成结果

### 模式 B：手动图片

如果你传了 `--image`，系统会进入手动图片流程。

典型命令：

```bash
.venv/bin/python scripts/story_pipeline.py \
  output/my-story.json \
  --output output/my-video \
  --image assets/whiteboard/demo.png \
  --renderer auto \
  --tts audio8
```

适合：
- 你已经准备好了线稿图
- 想保留特定构图和视觉风格

## 常用参数说明

`story_pipeline.py`

- `--output`：输出目录
- `--renderer`：渲染器，默认 `auto`
- `--tts`：TTS 提供商，默认 `audio8`；也可指定 `auto`（失败后切换到 eSpeak）、`edge` 或 `espeak`
- `--image-provider`：`none`（默认）或 `sensenova`，为每个场景调用 SenseNova 生成插画
- `--asset-root`：额外资产根目录，可重复传多次
- `--render-only`：只渲染画面，不做音频合成
- `--srt-output`：自定义 SRT 文件名或路径

`video_pipeline.py`

- `--output`：输出目录
- `--renderer`：渲染器，默认 `auto`
- `--tts`：TTS 提供商，默认 `audio8`；也可指定 `auto`（失败后切换到 eSpeak）、`edge` 或 `espeak`
- `--image-provider`：`none`（默认）或 `sensenova`，为每个场景调用 SenseNova 生成插画
- `--image`：手动指定图片
- `--asset-root`：额外资产根目录，可重复传多次
- `--render-only`：只渲染画面

默认 `audio8` 要求本机 Audio8 和 tts-hub 可用；启动失败或合成失败时任务会报错并写入失败清单，不会静默改用 Edge。`auto` 会回退到本机 eSpeak-NG，并在日志中提示。本地 eSpeak 需要系统安装 `espeak-ng`（或 `espeak`）和 `ffmpeg`。

每次运行还会在 `run-manifest.json` 记录实际 TTS 后端、场景文件是否完整保留全部 SRT 字幕，以及最终 MP4、音视频轨道、音画结束差、字幕时间轴冲突、泛化画面主体和抽帧检查结果。时间轴、泛化主体或画面抽样发现异常时会标记 `review_required`，不阻断成片生成。输出目录的 `review/contact-sheet.jpg` 提供 5 个时间点的成片预览，`review/report.json` 记录帧亮度/对比度。当前检查不评价剧情语义正确性、镜头连续性或审美。

## 一条最短可用命令

如果你只想快速生成一条视频，可以直接用下面这条：

```bash
.venv/bin/python scripts/story_pipeline.py \
  output/my-story.json \
  --output output/my-video \
  --renderer auto \
  --tts audio8
```

## 常见结果文件

生成后重点检查这些文件：

- `scene.json`
- `*.annotation.json`
- `*.mp4`

如果输出里有报错，通常先检查：
- Story JSON 是否合法
- SRT 是否为空
- 图片路径是否存在
- `ffmpeg` 是否在系统上可用
- 是否使用了项目内的 `.venv/bin/python`

## 建议的出片顺序

1. 先用 `generate_story.py` 生成 Story
2. 再用 `story_pipeline.py` 走自动流程出片
3. 如果结果不满意，再考虑提供 `--image` 手动指定图片
4. 对单幕需求，直接用 `video_pipeline.py` 配合 SRT + 图片

## 目录约定

```text
srt-whiteboard-animation/
├── assets/          # 素材与标注
├── output/          # 生成产物
├── scripts/         # 运行时脚本
└── tests/           # 测试脚本
```

如果你要长期维护这个项目，建议：
- 运行时脚本放 `scripts/`
- 测试脚本放 `tests/`
- 生成结果统一放 `output/`
