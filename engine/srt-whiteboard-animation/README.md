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

要调用在线模型，把 `.env.example` 复制为 `.env`（已有 `.env` 时不要覆盖），填入 `OPENAI_API_KEY` 或 `GEMINI_API_KEY`，编故事再填 `ZHIPU_API_KEY`（`glm-4.7-flash` 免费）。OpenAI-compatible provider 使用 Python 标准库发请求；Gemini 还需要按上面的命令安装 SDK。

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

`--provider` 也支持 `gemini` 和 `zhipu`；`mock` 适合不调用在线模型的流程演练。`zhipu` 走智谱开放平台，默认文本模型 `glm-4.7-flash`（200K 上下文，输入/输出/缓存均免费），需在 `.env` 填 `ZHIPU_API_KEY`。`--provider` 的默认值取 `STORY_PROVIDER`，未设置时为 `mock`；本项目 `.env` 设 `STORY_PROVIDER=gemini`，即编故事默认使用 `gemini-3.1-flash-lite`（改用智谱时把该值改成 `zhipu` 即可）。输出目录必须为空，以免覆盖已有产物。

先确认本机完整链路可启动时，可用 `--provider mock --tts espeak` 生成本地演练成片。要生成 AI 文案，选择 `openai` 或 `gemini`，并在项目 `.env` 中填入对应 API key；环境模板见 `.env.example`。

默认使用 Audio8 TTS。需能访问本机 Audio8（默认 `127.0.0.1:8024`）和 tts-hub（默认 `127.0.0.1:18084`）；缺服务时会尝试运行本机启动脚本，且请求禁用 Edge 回退。可通过 `AUDIO8_SERVICE_URL`、`TTS_HUB_URL`、`AUDIO8_VOICE` 配置服务和音色。`--tts auto` 会在 Audio8 不可用时回退到 eSpeak；`--tts espeak` 可显式选择离线语音，声音较机械。Audio8 不支持 pitch，运行清单会注明该限制。

`--image-provider` 可选 `sensenova`、`zhipu`（CogView-3-Flash）或 `auto`，为每个分镜单独生成一张 16:9 插画，保存在输出目录的 `.images/`，并使用电影式镜头运动渲染。CLI 默认值取 `IMAGE_PROVIDER`，本项目 `.env` 设 `IMAGE_PROVIDER=zhipu`，即出图默认使用 CogView-3-Flash；显式传 `none` 则退回本地程序化画面和素材解析。

- `sensenova`：SenseNova U1.5 Lite，原生 2048x1152 16:9。需要 `.env` 中现有的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` SenseNova 配置，也可用 `SENSENOVA_API_KEY` / `SENSENOVA_BASE_URL` 单独配置；模型可通过 `SENSENOVA_IMAGE_MODEL` 覆盖。
- `zhipu`：智谱 CogView-3-Flash（免费档），请求 1440x720 后居中裁切到 1280x720 以匹配 16:9。需要 `ZHIPU_API_KEY`（`open.bigmodel.cn` 开放平台），可用 `ZHIPU_IMAGE_MODEL`、`ZHIPU_IMAGE_SIZE`、`ZHIPU_IMAGE_CROP`（设为 `off` 保留 2:1 原图）、`ZHIPU_BASE_URL`、`ZHIPU_IMAGE_TIMEOUT`、`ZHIPU_IMAGE_RETRIES` 覆盖。
- `auto`：先试 `sensenova`，若**首帧生成失败**（缺 key、超时、报错）则整体降级到 `zhipu`。成片一旦接受第一张图就锁定该 provider，后续分镜失败会直接报错，绝不中途混用两个模型，避免全片画风撕裂。

每个分镜会产生一次图像 API 请求。失败降级时 `run-manifest.json` 的 `image_generation` 段会记录 `requested_provider`、最终 `provider`、`model`、`size`、`candidates` 与每次尝试的 `attempts`，以及每张图的实际 `width` / `height`。

若文案生成或出图遇到临时超时，文案和图像 API 默认最多重试 2 次；SenseNova 超时默认 300 秒（`OPENAI_TIMEOUT`、`OPENAI_RETRIES`、`SENSENOVA_IMAGE_TIMEOUT`、`SENSENOVA_IMAGE_RETRIES`），智谱超时默认 180 秒（`ZHIPU_IMAGE_TIMEOUT`、`ZHIPU_IMAGE_RETRIES`）。图像阶段中断后，使用原命令并添加 `--resume`，会读取已有 `story.json` 和 SRT，校验并复用 `.images/` 中已完成的图片，只继续缺少的分镜；视频或 TTS 阶段已有产物时仍应选择新的输出目录。

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
- `--tts`：TTS 提供商；不传时由 `--voice-profile` 决定（`auto` → 中文走 Audio8、英文走 Edge）。也可显式指定 `audio8`、`auto`（失败后切换到 eSpeak）、`edge`、`espeak` 或 `piper`
- `--voice-profile`：配音档案，默认 `auto`；英文成片建议 `en-us-narrator`（美式纪录片男声）、`en-us-warm-female`、`en-gb-narrator`；离线可用 `en-us-local-narrator` / `en-gb-local-narrator`（Piper）
- `--voice` / `--voice-rate` / `--voice-pitch`：覆盖档案里的音色、语速（如 `-4%`）、音高（如 `-2Hz`）
- `--list-voices`：列出全部配音档案后退出
- `--visual-setting`：全片视觉设定，`auto`（默认，按 SRT 自动生成）| `off` | 已有的 JSON 文件路径
- `--image-provider`：`none` | `sensenova` | `zhipu` | `auto`（首帧失败时 sensenova→zhipu 降级并锁定单一 provider），默认取 `.env` 的 `IMAGE_PROVIDER`（本项目为 `zhipu`，即 CogView-3-Flash），为每个场景生成插画
- `--asset-root`：额外资产根目录，可重复传多次
- `--render-only`：只渲染画面，不做音频合成
- `--srt-output`：自定义 SRT 文件名或路径

`video_pipeline.py`

- `--output`：输出目录
- `--renderer`：渲染器，默认 `auto`
- `--tts`：TTS 提供商；不传时由 `--voice-profile` 决定（`auto` → 中文走 Audio8、英文走 Edge）。也可显式指定 `audio8`、`auto`（失败后切换到 eSpeak）、`edge`、`espeak` 或 `piper`
- `--voice-profile`：配音档案，默认 `auto`；英文成片建议 `en-us-narrator`（美式纪录片男声）、`en-us-warm-female`、`en-gb-narrator`；离线可用 `en-us-local-narrator` / `en-gb-local-narrator`（Piper）
- `--voice` / `--voice-rate` / `--voice-pitch`：覆盖档案里的音色、语速（如 `-4%`）、音高（如 `-2Hz`）
- `--list-voices`：列出全部配音档案后退出
- `--visual-setting`：全片视觉设定，`auto`（默认，按 SRT 自动生成）| `off` | 已有的 JSON 文件路径
- `--image-provider`：`none` | `sensenova` | `zhipu` | `auto`（首帧失败时 sensenova→zhipu 降级并锁定单一 provider），默认取 `.env` 的 `IMAGE_PROVIDER`（本项目为 `zhipu`，即 CogView-3-Flash），为每个场景生成插画
- `--image`：手动指定图片
- `--asset-root`：额外资产根目录，可重复传多次
- `--render-only`：只渲染画面

默认 `audio8` 要求本机 Audio8 和 tts-hub 可用；启动失败或合成失败时任务会报错并写入失败清单，不会静默改用 Edge。`auto` 会回退到本机 eSpeak-NG，并在日志中提示。本地 eSpeak 需要系统安装 `espeak-ng`（或 `espeak`）和 `ffmpeg`。

每次运行还会在 `run-manifest.json` 记录实际 TTS 后端、场景文件是否完整保留全部 SRT 字幕，以及最终 MP4、音视频轨道、音画结束差、字幕时间轴冲突、泛化画面主体和抽帧检查结果。时间轴、泛化主体或画面抽样发现异常时会标记 `review_required`，不阻断成片生成。输出目录的 `review/contact-sheet.jpg` 提供 5 个时间点的成片预览，`review/report.json` 记录帧亮度/对比度。当前检查不评价剧情语义正确性、镜头连续性或审美。

## 配音档案与全片视觉一致性

英文成片的两个常见问题——声音不像欧美旁白、逐幕出图导致人物外观/文化风格漂移——由下面两层机制解决。

**1) 配音档案（`--voice-profile`）**

| 档案 | 语言/后端 | 音色 | 适用 |
| --- | --- | --- | --- |
| `en-us-narrator`（英文默认） | en-US / Edge | en-US-AndrewNeural | 纪录片、科普、故事旁白，欧美受众接受度最高 |
| `en-us-warm-female` | en-US / Edge | en-US-AvaNeural | 童话、口播、温暖叙事 |
| `en-us-bright-female` | en-US / Edge | en-US-AriaNeural | 短视频快节奏开场 |
| `en-gb-narrator` | en-GB / Edge | en-GB-RyanNeural | 历史文化类叙述 |
| `en-us-local-narrator` | en-US / Piper | en_US-ryan-high | 无网络时离线合成 |
| `en-gb-local-narrator` | en-GB / Piper | en_GB-northern_english_male-medium | 离线 + 英式口音 |
| `zh-cn-natural`（中文默认） | zh-CN / Audio8 | default | 中文成片，保持历史默认 |
| `zh-cn-narrator` | zh-CN / Edge | zh-CN-XiaoxiaoNeural | 显式指定时才用 |

`auto` 按字幕语种选择：中文 → `zh-cn-natural`（Audio8，与历史成片一致），英文 → `en-us-narrator`。Edge 走公共接口，偶发返回空音频时会自动重试 3 次，仍失败则报错并提示改用 Piper 离线档案。

**2) 全片视觉设定（`--visual-setting`）**

生成第一张分镜图之前，流水线会先基于整条 SRT 推导一份全片视觉设定并落盘到 `<输出目录>/.visual/visual-setting.json`，内容包括：

- `culture` / `era`：文化归属与时代（如「中国汉族古典神话」）；
- `art_style` / `palette`：画风与配色；
- `characters`：角色表（外观、发型、服饰、用色逐项固定，同一角色每幕复用同一段描述）；
- `forbidden`：禁止项（如「禁止西方面孔、金发、蓝眼、维多利亚或中世纪欧式服装」）。

这份设定会被前置注入每一幕的图像提示词，并附带「整片一致、不得违背」的硬约束，因此七仙女、凡人、天神在不同幕之间保持同一套面孔、服饰与时代感。

```bash
# 英文成片：美式旁白 + 自动推导的全片视觉设定
.venv/bin/python scripts/video_pipeline.py story.srt \
  --output output/story \
  --image-provider sensenova \
  --voice-profile en-us-narrator \
  --visual-setting auto

# 复用一份手工编辑过的视觉设定
.venv/bin/python scripts/video_pipeline.py story.srt \
  --output output/story --image-provider sensenova \
  --visual-setting output/story/.visual/visual-setting.json
```

说明：逐幕独立生成时，即使提示词完全一致，模型仍可能出现细节漂移。当前 SenseNova U1.5 Lite 是纯文生图接口，不支持角色参考图；若需要更强一致性，可改用手工编辑的 `visual-setting.json` 压缩描述自由度，或换用支持 image-to-image / 参考图的图像 provider 并把角色设定图作为参考输入。

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
