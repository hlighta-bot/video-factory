# video-factory 总体计划

> 本文件是项目**唯一的进度事实来源**。每条结论都标注核实方式：
> `已由当前代码或命令输出确认` / `仍需测试验证` / `当前证据不足`。
> 不得为了让计划看起来完整而虚构测试结果。

- **项目根目录**：`/home/hqb/projects/video-factory`
- **主要代码目录**：`engine/srt-whiteboard-animation/`
- **最近一次更新**：2026-11-02 JST（补充天空/水域主体构图与泛化主体复核）
- **当前阶段**：本地 mock Story → Edge TTS → MP4 流程通过；常见主题语义落到具体程序化画面，未知主体会提示复核
- **当前任务**：提升常见主题的画面语义覆盖
- **下一步**：继续补充常见中英文主体/动作与对应画面；安全续跑延后

---

## 1. 项目目标与总体架构

目标：把当前工具集建设为可重复运行、模块化、易维护的自动视频生产工具。

目标链路：

```
主题输入 → Story 生成 → SRT 生成 → Scene 规划 → 视觉素材选择
       → 渲染器选择 → TTS + 字幕合成 → 场景合并 → 最终 MP4
```

两条渲染路线同时保留，暂不提前废弃任何一条：

- **白板/线稿动画**：`render_stream_whiteboard.py`（经 `renderers/whiteboard.py` 包装）
- **cinematic 电影场景**：`renderers/cinematic.py`

实际主入口为 `scripts/video_pipeline.py` 的 `main()`，按 `image_path` 是否为 `None` 分成三条分支（`已由当前代码或命令输出确认`）：

| 行号 | 分支 | 入口条件 | 输出 |
|---|---|---|---|
| 565 | 旧版单场景白板 | `image_path is not None` | 单场景，结束于 679 行 `return` |
| 681 | 多场景统一管线 | `representation.kind == "procedural_svg" or cinematic_mode` | 逐场景 clip + 合并 |
| 801 | 新白板桥接 | 其余情况，且 `selected_renderer_name == "whiteboard"` | 单场景（`scene = planned_scenes[0]`） |

## 2. 实施阶段与顺序

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 工作区盘点与计划文档建立 | `已验证`（阶段 0 完成） |
| 1A | 白板多场景本地素材路径回归测试 | `已新增`（测试已落盘并运行；测试 FAIL，证明 4.3 缺陷仍存在） |
| 1B | 修复白板多场景本地素材路径（阶段 1A 之后） | `已验证`（`video_pipeline.py` 两补丁 + 测试清理 bug 修正 + stub 补齐；目标测试 PASS，7 个相关回归 PASS） |
| 2 | 修复 cinematic 字幕与标题 | `标题截断单元回归通过；最终字幕曾验证通过` |
| 3 | 处理素材解析回归与测试基础设施 | `语义优先级回归通过；资产解析旧测试与导入路径仍待处理` |
| 4 | 建立可重复的端到端最小流程 | `mock Story → auto TTS（模拟 Edge 故障）→ 白板 → MP4 通过，所有产物在临时目录` |
| 5 | 决定产品方向并完善自动化 | `暂缓` |
| F | 嫦娥 14 幕已验收视频配旁白与成片 | `已有成片产物`（逐句音频由离线流程预先生成；不等同于流水线自动 TTS） |
| G | TTS 自动回退（Edge → 离线 eSpeak-NG） | `8 项回归及模拟 Edge 故障的完整成片 smoke 通过；Edge 网络未调用` |
| H | 最终成片抽帧联系表与基础视觉异常检查 | `10 项回归及完整成片 smoke 通过；抽样 5 帧，无黑帧/纯色异常` |
| I | 首次运行依赖与 CLI 启动路径 | `mock/Gemini 预检通过；无 Gemini SDK 时 mock 路由仍可启动` |
| J | Edge TTS 默认与分镜语义匹配 | `Edge 成片通过；英文大小写与火箭太空意图回归通过` |
| K | 常见自然主题语义与程序化画面 | `中英文云朵/鸟/船主体、天空/海面环境、动作及构图回归通过` |
| L | 未识别主体的复核标记 | `scene.json 泛化主体会标记 review_required，但不阻断出片` |

## 3. 已完成且经过验证的工作

### 3.1 画面抖动修复（cinematic zoompan）—— `已验证`

`scripts/renderers/cinematic.py`：

- `ZOOMPAN_SUPERSAMPLE = 2`，zoompan 在 2560×1440 执行，再 `scale=1280:720:flags=lanczos` 缩回
- 链路顺序：`scale=1920:-1` → `zoompan` → `format=yuv444p` → `lanczos 下采样` → `drawtext` → `fade` → `format=yuv420p`

已核实的证据：

1. 本机 FFmpeg 6.1.1-3ubuntu5 的 zoompan 表达式变量中，`time` **有效**（exit 0），而 `t`、`n`、`out`、`pos`、`start_t` **全部无效**（exit 234，`Undefined constant`）。代码用 `time` 而非 `t` 是针对该版本的必要正确选择。
2. 水平黑→白渐变（1.5x 宽，125 帧）驱动真实 filtergraph：`x='(iw-iw/zoom)*(time/5)'` 均值 120.30 → 133.44（delta +13.15，117 个不同值）；`x='0'` 基线 delta=0.00；`(on-1)/124` 帧计数法 delta=+13.15。证明 `time` 随帧线性递增并驱动真实运动，不是"解析通过但静默退化为常量"。
3. `CinematicRenderer.render()` 端到端（4 种风格，纯色背景，输出至 `/tmp`）：全部 1280×720 `yuv420p`，`nb_frames=62` = `max(round(2.5×25),25)`，时长契约保持。
4. 预览 `output/codex-jitter-preview-20261018/scene-02-03-preview.mp4`：h264 1280×720 `yuv420p` 404 帧 16.16s，规格一致。

### 3.2 多场景渲染与音画同步基础 —— `已验证`

以验收 SRT `output/my-greatwall-auto-test/my-story.srt`（6 cues）实测：

- `plan_srt_grouped(target=8000, min=4000, max=12000)` → **2 个场景**：scene-01（cues 1–3，9900ms）、scene-02（cues 4–6，10100ms）；覆盖 `sorted(ids) == [1..6]`
- `plan_render_duration_ms(scenes, final_end_ms=20250)` → `[10150, 10100]`，合计 20250 = `final_end_ms`。**相邻场景以全局时间轴无间隙衔接**（scene-01 绝对 0–9900，scene-02 绝对 10150–20250）
- `build_final_video` 已**移除 `-shortest`**，使用 `-map 0:v:0 -map 1:a:0`

### 3.3 字幕同步 —— `已验证`（注意：见 4.1 的表述修正）

字幕在 `build_final_video`（第 791–797 行）经 `subtitles=...:force_style=CINEMATIC_SUBTITLE_STYLE` 施加于**已合并的 render.mp4**，使用 SRT **全局时间轴**。

端到端实验（全部输出至 `/tmp`，3s + 10.15s 两个 clip concat 合并 → 13.16s → 施加含 cue 1 (0–2.8s) 与 cue 2 (10.15–13.35s) 的 SRT），逐帧像素统计：

| 全局 t | uniq 字节 | 含义 |
|---|---|---|
| 1.0s / 2.5s | 131 | 字幕 1 已渲染 |
| 3.5s / 5.0s / 8.0s | 1 | 间隙，无字幕 |
| 11.0s / 12.5s / 13.0s | 143 | 字幕 2 已渲染 |

结论：**最终成片字幕存在且时间轴正确**，与 SRT 一致。

## 4. 尚未解决的问题及其证据

### 4.1 标题截取前 8 个字符 —— `已由当前代码或命令输出确认`

`scripts/renderers/cinematic.py` 第 133 行：

```python
if narration:
    return narration[:8]
```

实测：narration `"秦始皇下令连接北方各段城墙。"`（14 字）→ `_scene_title()` 返回 `"秦始皇下令连接北"`（**8 字**），句子被截断在名词中部。第 134 行对 `subtitles[0]["text"]` 同样截断 8 字。

**注意**：`visual.title` 优先于 narration（第 128–130 行），传入完整 title 则不截断。

**与"cinematic 字幕丢失"表述的关系**：本阶段的端到端实验**未复现字幕丢失**。字幕由最终合并层负责（见 3.3）。若早期报告提到"字幕丢失"，需澄清它指的是：
- (a) 单场景 clip 内不含字幕 → 成立，但这是设计（字幕在合并层加）；或
- (b) `--render-only` 模式下只有 render.mp4、不含字幕 → **需确认**；或
- (c) 最终成片缺字幕 → 本次实验**未复现**。

### 4.2 场景局部时间轴 vs 全局时间轴 —— `已由当前代码或命令输出确认`

`CinematicRenderer.render()`（第 160–165 行）只看 `scene.duration_ms`，**不读取 `scene.subtitles` 的时间戳**；`drawtext` 的 alpha 表达式用**局部** `t`，`fade` 也是局部。

- scene-02 局部时长 10.15s，其首条字幕在全局 10150ms、局部时间 0ms。因此**局部时间与全局时间无固定偏移**，逐场景渲染**不需要额外时间偏移**；绝对定位由 `plan_render_duration_ms()` 在 clip 时长层面完成（见 3.2）。
- 标题在局部 `t=0` 淡入、局部 `t=duration-fade` 淡出（`fade_filter(10.15, 0.35)` → `fade=t=in:st=0:d=0.350` / `fade=t=out:st=9.800:d=0.350`）。

**风险**：若未来有人把字幕也画进单场景 clip，则必须做 `start_ms - scene_start_ms` 偏移；当前不做，故不构成缺陷。

### 4.3 白板多场景路径无法完成 —— `已由阶段 1B 修复`（缺陷仍存在分析如下，供回溯参考）

三个互相叠加的缺陷：

1. **第 536 行**：`scene = planned_scenes[0]`。旧白板分支（801 行）只拿到**第一个**场景，其余场景从不经过白板桥接。
2. **第 801–804 行**：新白板分支要求 `selected_renderer_name == "whiteboard"`，但第 681 行起的统一管线对逐场景项走 `resolve_visual_representation()`。实测本地素材命中（`assets/rocket.png` 存在）时返回 `kind="local_asset"`，落入统一管线的循环，`create_renderer("whiteboard").render()` 第 60 行访问 `scene.visual["annotation"]`。
3. **annotation 从未生成**：统一管线（681 行）逐场景循环只写 `visual = item_payload`（来自 `resolve_visual_representation`），`_local_asset_payload()` 仅在 annotation 已存在时才带上它。实测命中后 `payload = {'image': 'rocket.png', 'subject': 'rocket'}`，**无 annotation**。白板渲染器必然 `KeyError: 'annotation'`。

对比：旧分支（806–809 行）确实经 `auto_annotate_bridge()` + `bridge_whiteboard()` 产出 annotation（实测 `rocket.annotation.json` 生成成功），但只对 `planned_scenes[0]` 执行一次。

附带缺陷：`resolve_assets()` 在 `output/phase14-test/assets/rocket.png` 为 16 字节损坏 PNG 时，PIL 报 `IHDR chunk shall be first`，`auto_annotation.py:114` 抛 `FileNotFoundError`。该 fixture 本身损坏。

### 4.4 `tests/test_asset_resolver.py` 断言失败 —— `已由当前代码或命令输出确认`（未运行测试本身）

按项目约定用纯函数只读复刻了该测试的每条断言（不写 `output/`）：

| 断言 | 结果 |
|---|---|
| `spec["renderer"] == "whiteboard"` | PASS |
| `{"type":"rocket","role":"subject"} in spec["elements"]` | PASS |
| `{"type":"environment","value":"sky"} in spec["elements"]` | **FAIL**（实际 `value="scene"`） |
| `resolved.assets["subject"]["status"] == "resolved"` | PASS（路径指向 `rocket.png`） |
| `resolved.assets["environment"]["status"] == "resolved"` | **FAIL**（`missing`，requested=`"scene"`） |
| `resolved.missing == []` | **FAIL**（`['scene']`） |
| missing 场景两条 status | PASS |
| `first == second` 确定性 | PASS |

**根因**：`scripts/scene/visual_spec.py:54–58`

```python
semantic = visual_plan.get("semantic") or {}
plan_subject = str(visual_plan.get("subject", "scene") or "scene")
semantic_subject = str(semantic.get("subject", plan_subject) or plan_subject)
subject = semantic_subject if plan_subject == "scene" else plan_subject
```

`build_visual_plan("rocket_launch", "rocket launch")` 产出的 `semantic` 是 `extract_visual_semantic("rocket launch")` 的结果，其 `subject="scene"`、`environment="scene"`（`visual_semantic.py` 的 `_pick_subject`/`_pick_environment` 词表**不含英文 `rocket`/`sky`**，只覆盖中文与特定词如 `长城`/`富士山`/`panda`/`新干线`/`山`）。而 `_elements_from_plan` 直接采用 `semantic` 值，覆盖了 `plan.subject`/`plan.environment` 里的正确值（`rocket`/`sky`，来自 `PLANS_BY_INTENT`）。

**分类**：`测试期望变化` 为主、`生产代码回归` 为次。
- 时间证据：测试文件 mtime **2026-10-02**，`visual_plan.py`/`visual_semantic.py`/`visual_spec.py` mtime **2026-10-07**。`resolver-report.json`（mtime 2026-10-06）显示当时 environment 仍为 `resolved`、`missing: ["nonexistent_object","void"]`，与当前行为不一致。
- 但 `visual_semantic.py` 的覆盖行为**语义上有缺陷**：`PLANS_BY_INTENT` 里已确定的 subject/environment 被自由文本抽取的泛化值覆盖，应改为 semantic 仅在 plan 为默认值时兜底。
- **不涉及环境问题**；`当前证据不足` 的点：这些文件全部为未跟踪新文件，无法从 git 历史确认 10-07 之前的实现，因此"回归"只能由文件 mtime 与 10-06 的报告间接推断。

### 4.5 现有测试素材与成片可用性 —— `已由当前代码或命令输出确认`

| 路径 | 状态 |
|---|---|
| `output/my-greatwall-auto-test/my-story.srt` | 可用，6 cues，全局 0–20250ms |
| `output/my-greatwall-auto-test/scene.json` | **单 dict**（非数组），旧版单场景 run 遗留 |
| `output/phase1-fix-test/scene.json` | 数组，2 场景；但产物 mtime **2026-10-18 07:39**（晚于本轮任务 07:24 起始），说明**期间被其他运行覆盖过** |
| `output/phase1-fix-test/*-scene-01.mp4` / `-scene-02.mp4` | 存在（20869B / 20172B），为真实渲染产物 |
| `output/phase14-test/assets/{rocket,sky}.png` | **损坏 fixture**（各 16 字节，PIL 拒绝） |
| `output/codex-jitter-preview-20261018/scene-02-03-preview.mp4` | 可用，规格已核实（见 3.1） |

可复用性结论：验收 SRT 可作为阶段 2 的固定输入；**不可**依赖 `output/phase1-fix-test/` 的既有产物做判断（已被覆盖）。

### 4.6 测试工具与依赖 —— `已由当前代码或命令输出确认`

- `.venv`（Python 3.13）**未安装 pytest**：`No module named pytest`；`pip list` 中无 pytest，系统亦无 `pytest` 可执行文件。
- `requirements*.txt` / `pyproject.toml` / `setup.py` **均不存在**。
- `tests/README.md` 给出的运行方式是**直接执行脚本**，不是 pytest：
  `PYTHONPATH=scripts .venv/bin/python tests/test_asset_resolver.py`
- 测试文件中均存在 `def main() -> int` 与 `if __name__ == "__main__"`，与该约定一致。
- 本轮未安装任何依赖（遵守保护规则）。

**风险提示**：任何以 `pytest` 为入口的验收命令在本环境都会直接 exit 1，且 CI 同样会失败。

### 4.7 工作区保护状态 —— `已由当前代码或命令输出确认`

- git 顶层目录 `/home/hqb/projects/video-factory`
- `.gitignore` 处于 **add/add 冲突**（stage 2 `9776e30`、stage 3 `83972fa`，无共同祖先），`git ls-files -u` 显示两条 staged 条目。`.gitignore.ours` / `.gitignore.theirs` 为未跟踪。
  **注意**：从 `engine/srt-whiteboard-animation/` 子目录执行 `git ls-files -u` 会返回**空**（路径被限定），不能据此判断冲突不存在。
- 本次**未触碰** `.gitignore*` 任何文件，未执行 reset/clean/stash/commit。
- `README.md`（工作区根）为已修改状态（`M `，已暂存）；未查看内容，非本任务范围。
- 大量未跟踪文件，且 **untracked 文件不会出现在普通 `git diff` 中**：`scripts/renderers/cinematic.py`、`scripts/renderers/selector.py`、`scripts/scene/`、`scripts/story/`、`scripts/renderers/capabilities.py|manim.py|svg.py`、`tests/`、`assets/`、`scripts/generate_story.py`、`scripts/inspect_*.py`、`scripts/story_*.py` 全部为 untracked。
- `git diff --stat` 中仅 `scripts/video_pipeline.py` 是已跟踪的改动（+587 / −279）；`git diff -- scripts/renderers/cinematic.py scripts/renderers/selector.py` 输出为空，原因即 untracked。

## 5. 测试结果与验收状态

| 项目 | 状态 | 说明 |
|---|---|---|
| cinematic zoompan 修复 | `已验证` | 见 3.1，含帧级运动真实性验证 |
| 多场景时长契约 | `已验证` | 见 3.2，无间隙衔接，合计等于 final_end_ms |
| 最终成片字幕同步 | `已验证` | 见 3.3，逐帧像素证据 |
| 标题 8 字截取 | `已实现，待验证`→ 已确认为缺陷 | 见 4.1，需修复后重验 |
| 白板多场景渲染 | `阻塞` | 见 4.3，annotation 缺失 + 分支互斥 |
| `test_asset_resolver.py` | `阻塞` | 见 4.4，3 条断言失败（未运行测试本身） |
| `test_scene_loss_fix.py` 是否真正断言逐场景渲染 | `已由当前代码或命令输出确认`： **否** | 见 5.1 |
| 白板多场景本地素材路径回归测试 | `已新增，测试 FAIL 证实缺陷` | 见 5.2，阶段 1A |
| pytest 可用性 | `阻塞` | 见 4.6 |

### 5.1 `tests/test_scene_loss_fix.py` 的断言强度 —— 明确结论

该测试**没有**断言"每个场景分别调用渲染器"。实际只检查了三类数据/文件存在性：

1. `test_planner_keeps_all_scenes`：`plan_srt_grouped` 场景数、时长为正、字幕 id 覆盖 `[1..6]`
2. `test_build_final_video_no_shortest`：mock `video_pipeline.run`，断言 cmd 无 `-shortest`、含 `-map 0:v:0 1:a:0`
3. `test_main_scene_json_contains_all_scenes`：mock 掉 TTS / audio / `build_final_video`，断言 `scene.json` 是数组、场景数 ≥2、字幕总数 ==6、`*-render.mp4` 存在且非空

第 143 行 `clips = sorted(TEST_OUTPUT.glob("*.mp4"))` **赋值后从未使用**（无断言）。文件中**没有**任何 `mock`/`patch`/`Mock` 拦截 `create_renderer` 或 `renderer.render`（grep 确认）；渲染器**未**被 mock，真实 svg 渲染器会跑，但调用次数从未被计数。

因此该测试在"场景数据丢失"与"逐场景渲染丢失"之间无法区分：即使某场景的 `renderer.render()` 抛异常或未被调用，只要 `merge_scenes_main` 产出一个非空 mp4，测试仍会通过。
对比：`tests/test_cinematic_renderer.py::test_main_cinematic_3scenes`（第 232–233 行）**确实**断言了 `len(clips) == 3`，覆盖更强。

### 5.2 `tests/test_video_pipeline_multiscene_asset_path.py` —— 阶段 1A 执行结果

新增回归测试文件，闭合 5.1 记录的断言强度缺口。**测试未通过，且失败模式精确复现 4.3 记录的白板多场景缺陷**。

运行方式：`PYTHONPATH=scripts .venv/bin/python tests/test_video_pipeline_multiscene_asset_path.py`（遵守项目约定，无 pytest）。

真实执行记录（2026-10-18 08:54 JST）：

- 退出码：**1**
- 前置探测 `probe_local_asset_representation()`：`scenes=3`，全部 `kind="local_asset"`，全部 `has_annotation=False`，`bridge_whiteboard(scene)` 抛 `ValueError: Whiteboard bridge requires visual.image`。三场景的 payload 键均为 `['environment','image','subject']`，**无 annotation**。
- `renderer.render()` 调用次数 = **1**（应为 3）；`scene.json` 场景数 = **3**；`merge` 输入数量 = **0**（应为 3）。
- 唯一成功渲染的场景：`scene-01 clip=out-whiteboard.mp4 duration_ms=4500 annotation_exists=True`。此即 4.3 缺陷 1：`planned_scenes[0]` 走旧白板桥接（806–809 行），annotation 被生成；其余场景**被静默跳过**。
- 失败断言：`期望每个计划场景都被渲染一次: rendered 1, planned 3; rendered ids=['scene-01']`。

**注意：本次运行未触发 4.3 缺陷 3（`KeyError: 'annotation'`）**。原因是缺陷 1（场景丢失）先于缺陷 3（annotation 缺失）在流程中触发，pipeline 在场景 1 成功后继续进入场景 2 的旧分支路径时 `scene = planned_scenes[0]` 又被赋值回第一个场景，最终只渲染一次且该场景 annotation 齐全。要触发缺陷 3，必须先修复缺陷 1（使逐场景循环成立），届时逐场景 `resolve_visual_representation()` 返回的 `local_asset` payload 不含 annotation，`WhiteboardRenderer.render()` 才会 `KeyError`。测试中三类失败（A 静默丢场景、B annotation 缺失、E 本地素材分支缺 annotation）互斥报告，任一发生都会明确指认。**该测试是阶段 1B 修复的正确验收标准**。

测试自身无生产代码依赖注入以外的额外行为：TTS/audio/final/merge 全部内联 stub，`create_renderer` 用 `mock.Mock(wraps=renderer, render=fake_render)` 包装记录，`fake_render` 只写 11 字节的占位文件；`probe_local_asset_representation` 独立复算每个场景的 representation。所有临时目录由 `tempfile.mkdtemp()` 生成并在 `try/finally` 中 `shutil.rmtree` 清理，**未写入 `output/`**。

### 5.3 `tests/test_video_pipeline_multiscene_asset_path.py` —— 阶段 1B 执行结果

修复后重跑：**通过**。同一命令：`PYTHONPATH=scripts .venv/bin/python tests/test_video_pipeline_multiscene_asset_path.py`，退出码 `0`。

真实执行记录（2026-10-18 11:40 JST）：

- 前置探测 `probe_local_asset_representation()`：`scenes=3`，全部 `kind="local_asset"`，payload 键 `['environment','image','subject']`。**annotation 由 pipeline 内桥接生成，不在 representation 层**——此为设计契约，非缺陷。
- `renderer.render()` 调用次数 = **3**（每个 planned scene 一次）；`scene.json` 场景数 = **3**；`merge` 输入数量 = **3**。
- 三个 clip 均落盘：`out-scene-01/02/03.mp4`，每场景 `duration_ms=4500`，`annotation_exists=True`。
- 失败断言：**无**；打印 `[ok] 3 个场景全部经 auto_annotate_bridge + WhiteboardRenderer 渲染，annotation 均为真实生成文件，merge 收到 3 个片段`。`elapsed_sec=0.353`。

**回归结果（阶段 1B 收敛点）**：

| 测试 | 结果 | 备注 |
|---|---|---|
| test_video_pipeline_multiscene_asset_path.py | PASS | 目标测试 |
| test_visual_representation.py | PASS | 表示解析不受影响 |
| test_whiteboard_bridge.py | PASS | 白板桥接不受影响 |
| test_scene_loss_fix.py | PASS | 场景丢失修复不回退 |
| test_auto_annotation.py | PASS | annotation 生成链正常 |
| test_scene_annotation.py | PASS | annotation 策略链正常 |
| test_scene_planner.py | PASS | 场景规划不受影响 |
| test_scene_plan.py | PASS | 场景计划不受影响 |
| test_video_pipeline.py | FAIL（**与本次修改无关**） | Edge TTS 403 网络问题，非代码回归 |
| test_asset_auto_resolution.py | FAIL（**与本次修改无关**） | `parse_srt` 模块导入路径缺失，测试脚本本身导入错误 |

两个 FAIL 均为预存在问题，在阶段 1B 修改前即无法通过（前者是 TTS 服务 403、后者是 `ModuleNotFoundError: No module named 'parse_srt'`）。**本次修改未引入新的回归**。

### 5.4 阶段 1B 修复内容

#### 5.4.1 源码补丁（`scripts/video_pipeline.py`）

**补丁 1：统一循环内对 local_asset 补跑桥接生成 annotation**

在 `for index, scene_item in enumerate(scenes_to_render)` 循环中，`resolve_visual_representation(...)` 得到 `item_payload` 后，新增分支：当 `item_rep.kind == "local_asset"` 且 payload 缺 `annotation` 时，调用 `resolve_assets(scene_item, asset_roots)` → `auto_annotate_bridge(scene_item, resolution)` → `bridge_whiteboard(bridge_input.scene)`，将桥接产出的 `visual` 字段合并回 `item_payload`（保留 `subject`/`environment` 键）。这使统一循环内的每一场景渲染时都携带真实生成的 annotation 文件路径。

**补丁 2：扩展统一循环进入条件**

将 `if representation.kind == "procedural_svg" or cinematic_mode:` 扩展为 `if representation.kind == "procedural_svg" or cinematic_mode or whiteboard_multiscene:`，其中：

```python
whiteboard_multiscene = (
    selected_renderer_name == "whiteboard"
    and image_path is None
    and len(planned_scenes) > 1
    and representation.kind == "local_asset"
)
```

该条件让白板多场景（`renderer="whiteboard"`、多 SRT 场景、无显式 image_path、视觉层解析到 local_asset）也走统一循环，而非落回只处理 `planned_scenes[0]` 的旧单场景分支，从而彻底消除 4.3 缺陷 1（`scene = planned_scenes[0]` 静默丢场景）。

#### 5.4.2 测试修正（`tests/test_video_pipeline_multiscene_asset_path.py`）

保留测试的核心验收要求（每场景渲染一次、每场景独立 annotation、annotation 父目录等于 assets_dir、merge 输入数等于场景数）。三处架构修正：

1. **`run_pipeline_capture()` 的 `finally` 中 `shutil.rmtree(tmp_root)` 早于断言执行**：将 `shutil.rmtree` 从内层 finally 中移除，改为通过 `result.tmp_root` 暴露给调用方；调用方 `test_whiteboard_multiscene_local_asset_path()` 用外层 `try/finally` 在断言完成后清理临时目录。
2. **`fake_merge_scenes_main` 补齐 `--output` 文件的写入**：真实 `merge_scenes_main` 会写入 `--output` 指定的 `render.mp4`；stub 原本只记录 `--inputs` 切片，导致 D 类断言 `result.render_mp4.exists()` 假失败。补齐为：解析到 `--output` 路径时写入占位字节。
3. **E 类断言语义与实现契约对齐**：原 E 类要求 `resolve_visual_representation(...).payload` 携带 `annotation`，但按设计契约 representation 层只读取已存在的 annotation、不生成 annotation（annotation 由 pipeline 统一循环内的桥接生成）。修改 E 类为：对每次渲染调用，检查 `record["visual"]["annotation"]` 存在——即验证"渲染时场景确实带 annotation"，这与真实契约一致。B 类断言（`annotation_exists`、`annotation_dir_ok`）已覆盖 per-call 落盘验证，C 类断言保证渲染数等于规划数，D 类断言保证 merge 输入数等于场景数，E 类作为跨查冗余覆盖。

### 5.5 阶段 1B 文件变更

- 修改：`engine/srt-whiteboard-animation/scripts/video_pipeline.py`（两补丁；补丁 1 增加 ~15 行桥接调用，补丁 2 增加 `whiteboard_multiscene` 变量与条件项）
- 修改：`engine/srt-whiteboard-animation/tests/test_video_pipeline_multiscene_asset_path.py`（三处测试架构修正：清理时机、stub 补 output 文件、E 类断言语义对齐）
- 修改：`video-factory/PROJECT_PLAN.md`（本节）
- 补丁脚本落盘在会话 temp 目录 `temp/patch_pipeline.py`、`patch_pipeline2.py`、`patch_cleanup_v10.py`、`patch_stub_merge.py`、`patch_eclass.py`（Windows 侧写入后 `cp` 到 WSL 侧执行，未污染仓库）
- **未修改** `.gitignore` / `.gitignore.ours` / `.gitignore.theirs`；未修改音频相关代码；未写入 `output/phase1-fix-test/` 或任何固定 `output/phase...` 目录。
- 未执行 git reset/restore/checkout/clean/merge/rebase/commit/stash；未安装依赖；未调用网络或 LLM。

## 6. 技术决策及其理由

| 决策 | 理由 |
|---|---|
| zoompan 用 `time` 变量 | 本机 FFmpeg 6.1.1 中 `t`/`n`/`out`/`pos` 均无效（exit 234），`time` 有效且实测随帧递增 |
| 2x 超采样 + yuv444p + lanczos 下采样 | zoompan 的 crop 坐标取整为整数像素；放大 2 倍后每次整数步进成为半像素运动，下采样后消除抖动 |
| 字幕留在最终合并层（全局时间轴） | 逐场景 clip 只需局部时间轴；全局对齐由 `plan_render_duration_ms()` 的 clip 时长保证，实验确认无间隙 |
| 移除 `-shortest` | 原标志在视频短于音频时截断旁白 |
| `Scene.to_dict()` 用 `sceneId` 展开 | 兼容旧格式消费者（`image`/`srt`/`subtitleCount` 提到顶层） |

## 7. 已知风险与需要用户确认的事项

1. **`output/phase1-fix-test/` 产物在任务执行期间被覆盖**（mtime 07:39，任务 07:24 开始）。需确认是否有并行进程仍在运行写入 `output/`。
2. **pytest 缺失**：阶段 3 需明确测试入口（补依赖 or 统一为脚本直跑）。
3. **测试/生产语义覆盖**：`visual_semantic.py` 用泛化 semantic 覆盖 `PLANS_BY_INTENT` 的确定值，属语义缺陷。修复方案需用户选择：(a) 改生产代码让 semantic 只作兜底；(b) 改测试期望接受 `scene`。
4. **白板多场景设计决策已收敛**（阶段 1B 落地方案）：在统一管线（原 681 行起）内对逐场景项补跑 `auto_annotate_bridge`，未拆分 `local_asset` 独立路由。理由：统一循环已在 cinematic/procedural_svg 分支落地，白板多场景走同一循环能最大化复用 clip 合并与字幕对齐逻辑，避免维护两套并行的多场景路径。
5. **`renderer="auto"` + `planned_scenes`**：`select_renderer(planned_scenes[0])` 返回 `whiteboard`，但逐场景实际渲染器由 `resolve_visual_representation` 决定（可能是 `svg` 或 `whiteboard`）。即"选中的渲染器"与"实际使用的渲染器"可能不一致，需在阶段 2 一并处理。
6. **`.gitignore` 冲突未解决**，任何后续提交都会受阻。本轮按要求未处理。

## 8. 阶段 1A 文件变更（历史）

- 新增：`engine/srt-whiteboard-animation/tests/test_video_pipeline_multiscene_asset_path.py`（470 行，本阶段唯一新增的测试文件）
- 修改：`video-factory/PROJECT_PLAN.md`（仅更新头部状态字段、阶段 2 表格加入 1A/1B 行、章节 5 表格新增测试行、新增 5.2 小节、本章节替换）
- **未修改任何生产代码**（`scripts/` 目录下任何文件均未编辑）。
- **未编辑** `.gitignore` / `.gitignore.ours` / `.gitignore.theirs`。
- 未使用 `output/phase1-fix-test/` 或任何固定 `output/phase...` 目录；测试临时目录全部位于 `/tmp/wb-multiscene-*`，退出时清理。
- 未执行 git reset/restore/checkout/clean/merge/rebase/commit/stash。
- 未安装依赖、未调用网络或 LLM、未生成真实视频或音频（TTS/audio/final/merge/render 全部 stub）。

## 9. 阶段 0 文件变更（历史）

- 新增：`video-factory/PROJECT_PLAN.md`（本文件初版）
- 未修改任何其他文件。未编辑 `.gitignore`/`.gitignore.ours`/`.gitignore.theirs`。
- 全部验证性渲染与 ffmpeg 实验输出均落在 `/tmp/`（`/tmp/cine_ax.srt`、`/tmp/clp{1,2}.mp4`、`/tmp/merged.mp4`、`/tmp/merged_sub.raw`、`/tmp/clplist.txt`、`/tmp/wb_verify_*`、`/tmp/wb_rok_*`），**未写入 `output/`**。
- 未执行 git reset/restore/checkout/clean/merge/rebase/commit/stash。
- 未安装依赖、未调用网络或 LLM、未修改音频处理逻辑。

## 10. 阶段 F：嫦娥 14 幕旁白与成片（2026-10-18 的阻塞记录）

本节记录截至 2026-10-18 的 Edge TTS 403 阻塞。后来已有离线逐句音频合成的成片产物，见 `output/phase-f5-chang-e-final/generation-log.json`；这证明可完成音视频合成，但不是流水线自动调用 TTS 的验证。

- 目标：基于已验收无声视频 `engine/srt-whiteboard-animation/output/phase1b-visual-acceptance-chang-e/chang-e-14scenes-render.mp4`（1080x600 / 60fps / 1830 帧 / 30.500 s / 2,329,903 字节）生成中文旁白成片。
- 已完成（`已由当前代码或命令输出确认`）：
  - 输入视频存在且规格如上；画面**未烧录字幕**（抽帧 t=1/15/29 s 确认无字幕条）。
  - 对应 SRT：`conv_ad99d3bd83c24feab487a582c6da4f47/output/changement_shorts_30s_storytelling.srt`，9 条 cue，0–30.5 s，与 `scene_map.json`（total_ms=30500，14 幕）逐条一致；该 SRT 为英文，项目内不存在中文旁白文本。
  - 经用户授权，按原 cue 逐条忠实译为中文（不改剧情、不增删），落盘 `output/phase1b-visual-acceptance-chang-e-final/narration.srt`；第 8 条 `……` 为静默停顿，不送 TTS。
  - 输出目录 `output/phase1b-visual-acceptance-chang-e-final/` 执行前不存在（新建后仅写入 `narration.srt`、`report.md`）。
  - 音频参数沿用既有流程：`zh-CN-XiaoxiaoNeural` / `rate=-10%` / `pitch=-1Hz` / `loudnorm=I=-16:TP=-1.5:LRA=11` / 48 kHz stereo / AAC 192k。
- 阻塞点：`edge-tts` 7.2.8 调用微软 Edge TTS 返回 `WSServerHandshakeError: 403`（token 版本 `Sec-MS-GEC-Version=1-143.0.3650.75`）；`edge-tts` CLI 单条复现同样 403；WSL 时间与 HTTP Date 完全一致，排除时钟偏差。
- 影响：未生成 `narration.m4a` / `chang-e-final.mp4`；未修改任何音频、渲染、场景规划代码；未重跑白板渲染；未覆盖已验收文件。
- 测试：`PYTHONPATH=scripts .venv/bin/python tests/test_video_pipeline_multiscene_asset_path.py` → `EXIT_CODE=0`（PASS，全部使用 `/tmp`）；`test_video_pipeline.py` 与 `test_phase5a_audio_fix.py` 会写固定 `output/` 目录，按约束未运行；`.venv` 无 `pytest`，测试为脚本直跑。
- 修改文件：`PROJECT_PLAN.md`（新增本阶段表行 F、本小节、更新头部"最近一次更新"）；新增 `output/phase1b-visual-acceptance-chang-e-final/{narration.srt,report.md,tts-failure.log}`。生产代码零改动。
- 待批准：升级 `edge-tts`（属安装依赖，需授权）或改用其他 TTS 方案。
- 详细记录：`engine/srt-whiteboard-animation/output/phase1b-visual-acceptance-chang-e-final/report.md`。

## 11. 阶段 G：TTS 自动回退（2026-11-02）

- 目标：默认 Story → 视频链路在 Edge TTS 不可用时仍能生成配音，避免服务端错误让整条任务中断。
- 已实现并通过针对性回归及本地端到端验证：
  - `scripts/tts/edge.py` 改为合成时才导入可选依赖 `edge-tts`，让本地离线 TTS 在缺少该包时仍可加载。
  - 新增 `scripts/tts/espeak.py`：用系统 eSpeak-NG/eSpeak 合成普通话 WAV，再用 FFmpeg 转成流水线使用的 MP3；语速百分比转换为 eSpeak WPM。
  - 新增 `scripts/tts/auto.py`：先尝试 Edge；首次失败后记录警告并对本任务剩余字幕固定使用 eSpeak，减少重复网络失败并避免后续字幕再切换音色。
  - Provider 路由支持 `auto`、`edge`、`espeak`；主题生成、Story pipeline 和视频 pipeline 的默认值改为 `auto`。
  - 若任务中途从 Edge 切到 eSpeak，会重新合成之前已生成的字幕音频，避免最终旁白混用两种音色；`run-manifest.json` 记录请求后端、实际后端、是否回退及 MP4 音视频轨道检查。
  - `run-manifest.json` 另记录 scene.json 的 cue 覆盖检查，报告缺失、重复或意外的字幕 ID，帮助发现分镜阶段丢场景/字幕。
  - README 说明回退条件、依赖和离线语音质量限制。
- 验证结果（2026-11-02）：新增 TTS/媒体检查回归 8 项通过；白板多场景本地素材回归通过；离线 eSpeak 生成了可被 ffprobe 读取的 MP3。初次完整 mock Story → eSpeak → 白板 → MP4 smoke 发现中文语音时长约为字幕预算两倍，manifest 标记 `review_required`。将中文 WPM 基准改为 350 后，复跑成功生成 2.9MB MP4，4 条字幕完整，音画结束差 17ms，`quality_check=passed`。随后用模拟 Edge 403 的方式完整运行 `--tts auto`，再次生成 2.9MB MP4，manifest 记录 `backend=espeak` / `offline_fallback_used=true`，字幕完整且音画结束差 17ms。
- 风险/待验收：eSpeak 音质明显低于神经网络 TTS，回退会打印警告。`pitch` 参数暂未映射到 eSpeak。自动检查覆盖场景与字幕 ID、文件及音视频轨道/结束时间差，不评价视觉审美或脚本质量。Edge 网络服务没有在本次验证中调用，切换行为用故障模拟覆盖。
- 验证注意：旧 `tests/test_visual_spec.py` 会把报告写到固定路径 `output/visual-spec-test/`，不符合本阶段临时目录约束；本次虽运行并通过，但没有删除或清理该文件。
- 本阶段没有清理或覆盖工作区既有改动；`.gitignore` 合并冲突仍保留。

## 12. 同步修正已知问题（2026-11-02）

- `scripts/renderers/cinematic.py`：标题统一做空白归一化，最长 22 字符，过长时以省略号收尾，不再固定截断前 8 字。
- `scripts/scene/visual_spec.py`：已确定的 Visual Plan subject/environment/action 优先；语义抽取仅在对应字段仍是默认值时补充，避免 `scene`/`story` 等泛化词覆盖火箭、天空等计划值。
- 状态：标题截断与语义优先级的针对性回归均通过；其它既有失败记录仍需后续处理。

## 13. 阶段 H：成片抽帧复核（2026-11-02）

- 成片完成后在输出目录生成 `review/contact-sheet.jpg` 和 `review/report.json`；联系表均匀抽样 5 帧，报告计算每帧平均亮度与对比度。
- 采样帧近黑或近乎纯色时，`quality_check` 标记 `review_required`；抽帧不可用时也要求复核。检查不判断内容语义、镜头连续性或审美质量。
- `_target_artifact_paths()` 把联系表和报告纳入输出覆盖保护，避免重跑静默覆盖。
- 已验证：10 项 TTS/质量回归通过；黑色视频样本被识别并标记复核；模拟 Edge 故障的完整主题 → 配音 → 白板 → MP4 链路生成 2.9MB 成片，清单记录回退、5 帧抽样、字幕完整、音画结束差 17ms，质量状态 `passed`。所有 smoke 输出在 `/tmp` 并已清理。

## 14. 阶段 I：首次运行启动路径（2026-11-02）

- `story/router.py` 改为按选择加载 provider；mock/OpenAI 不再因为 Gemini SDK 缺失而在启动导入阶段失败。
- `GeminiStoryProvider` 延迟导入 `google-genai`；缺依赖时提示使用 `prepare_env.py --provider gemini` 安装。
- `prepare_env.py` 增加 `--provider` / `--tts` 选项，按需准备 Gemini 或 Edge SDK，并检查 `ffmpeg`、`ffprobe`、eSpeak 命令。
- README 明确 Linux/WSL 的 `python3` 首次启动命令、系统依赖、模型 `.env` 配置和离线演练命令。
- 验证：`prepare_env.py --check --provider mock --tts auto` 与 Gemini 检查均通过；Story CLI `--help` 可启动；在屏蔽 `google` 模块的进程中，mock provider 仍可创建、Gemini provider 返回明确依赖提示；11 项启动/TTS/质量回归及多场景回归通过。未调用在线 LLM，也未安装依赖。

## 15. 离线中文配音语速修正（2026-11-02）

- 用户反馈生成的语音听起来像倍速，检查对应 `output/my-first-video/run-manifest.json` 确认实际后端为 eSpeak，旧中文基准为 350 WPM（`-10%` 后 315 WPM）。
- 将中文 eSpeak 基准改为 200 WPM（`-10%` 后 180 WPM），并移除“为贴合 SRT 字符速率而提高中文语速”的旧注释。离线中文配音以可懂度优先；音频较长时由既有最终时间轴扩展字幕和画面。
- 用户试听降速样片后反馈仍难懂。继续检查确认 `zh-CN...` 错映射到 eSpeak-NG 的 `cmn`（Latin-as-English Mandarin）voice；改为直接使用 `zh` Chinese voice。此映射修正才是当前重点，降速仅作辅助。
- 针对性测试：TTS/媒体质量回归 11 项通过。
- 降速且修正为 `zh` voice 后，mock Story → MP4 smoke 成功生成；11 项针对性回归通过。独立音轨与 MP4 音轨均为 48 kHz，时长匹配，没有发现播放器/编码链路将音频加速的证据。eSpeak 仍是合成声线；最终可懂度需试听确认。工作区没有 Piper/Kokoro/Coqui 等本地神经 TTS 引擎，未调用在线服务。

## 16. Edge TTS 成片验证（2026-11-02）

- 用户明确要求直接使用 Edge TTS 后，使用 `--provider mock --tts edge` 生成本地临时样片；未调用在线 LLM。
- Edge TTS 请求成功，manifest 确认 `requested=edge`、`backend=edge`、`offline_fallback_used=false`；生成约 18.5 秒 MP4，音画结束差 5 ms，`quality_check=passed`。完整 MP4 与旁白音轨保存在 `/tmp/video-factory-edge-tts-smoke-network/`，供用户试听。
- 该结果说明此前 403 并非当前必现；本次直接指定 Edge，不经过 eSpeak 回退。

## 17. Edge TTS 设为默认（2026-11-02）

- `story_from_topic.py`、`story_pipeline.py`、`video_pipeline.py` 的 TTS 默认值改为 `edge`；`prepare_env.py` 默认准备 Edge SDK。
- `auto` 仍可显式选择并保留 Edge → eSpeak 回退；`espeak` 作为明确的离线选项。默认 Edge 失败时直接报错并写失败清单，避免静默生成用户难以理解的声音。
- `prepare_env.py --check --provider mock --tts edge` 通过；Story CLI 帮助显示 `default: edge`；11 项 TTS/媒体质量回归通过。

## 18. 分镜意图大小写与特异性（2026-11-02）

- 复现英文 Rocket Launch 被 infer_visual_intent() 误分为 rocket_scene：分类时先做了小写归一，但动作词仍用原始文本匹配，标题大小写导致漏匹配。
- 将英文和中文动作匹配统一基于小写文本；将更具体的 rocket_space 放在通用 rocket_flying 前面，避免组合句“升空、飞行、突破大气层”只命中普通飞行。
- 更新分镜测试，新增 Rocket Launch 与 Rocket Flying in Space 大小写/优先级用例，并修正组合场景预期为 rocket_space。
- 验证：分镜规划、故事语义 6 案例、mock Story 可视化 2 主题共 3 个脚本回归通过，产物全部写入并清理于 /tmp。未调用在线 LLM。

## 19. 常见自然主题语义与画面（2026-11-02）

- mock Story 的泛化主题“云朵旅行”此前所有场景都是 story_scene，主体和环境落到 scene/story，程序化画面没有具体对象。
- VisualSemantic 词条匹配改为大小写不敏感；补充云朵/云层/cloud(s)、鸟/bird、船/boat 主体，sky/ocean 环境识别，以及旅行/飞行/航行动作映射。
- 程序化 SVG fallback 增加云朵、飞鸟、帆船和波浪布局，并纳入主体布局表。
- 验证：VisualSemantic 集成 10 案例通过；程序化构图 13 案例与 12 项布局边界检查通过，所有报告写入并清理于 /tmp。

## 20. 泛化主体语义复核（2026-11-02）

- 最终成片质量报告此前检查媒体轨道、字幕覆盖、时间轴和抽样画面的黑帧/纯色，但不识别分镜规划仍把主体写成泛化 scene。
- 新增 `_inspect_scene_semantics()`：读取 scene.json，以 Visual Plan 为主、semantic 为兜底，报告仍为 generic scene 的场景；其状态为 review_required 时并入最终质量清单。
- 泛化主体只提示复核，不中断出片；manifest 新增 scene_semantics，记录每幕有效主体和命中项。
- 验证：新增泛化主体与已解析 cloud 主体回归；TTS/质量回归 12 项通过。VisualSemantic 10 案例、程序化构图 13 案例及 12 项布局检查通过，所有测试产物写入并清理于 /tmp。

## 21. 自动视频链路现状复核（2026-11-03）

- 复核确认已有主题 → Story JSON → SRT → TTS → 分镜 → 白板/程序化渲染 → MP4 合成入口：`story_from_topic.py`；已有 Story JSON 的入口为 `story_pipeline.py`，手工 SRT 入口为 `video_pipeline.py`。
- 修复 `story_pipeline.py` 的失败顺序：在生成 SRT 之前先检查视频目标产物冲突及输入覆盖风险；现有 SRT 目标也拒绝覆盖。这样目标目录已有 `scene.json` 等成片产物时，任务会在产生新 SRT 前退出。
- 当前验证：`test_tts_fallback_and_quality_checks.py` 12 项通过；`test_video_pipeline_multiscene_asset_path.py` 三幕均执行标注、渲染和合并；`test_asset_resolver.py` 通过；在 `/tmp` 中验证 Story pipeline 冲突预检不会写入 SRT，空目录则可继续生成 SRT 并调用视频入口。
- `test_story_pipeline.py` 本次未能端到端通过：它把固定目录 `output/phase17-test/mp4-output/` 作为输出，目录已有旧产物，视频管线按覆盖保护拒绝重跑。旧测试在失败前曾生成 `output/phase17-test/mp4-output/story.srt`；本轮未清理该文件或任何既有产物。需将该测试改为临时目录后再作为可重复的端到端验收。
- 历史“白板多场景阻塞”和 `test_asset_resolver.py` 失败记录被本次当前代码验证覆盖，现状分别为通过；此前章节作为故障历史保留。pytest 仍不是本项目测试入口，测试说明采用脚本直跑。
- 尚未验收项：在线 OpenAI/Gemini 文案质量与真实账户配置；白板视觉语义/审美质量需要人工审核；Edge TTS 服务依赖网络。已有自动检查可验证结构、时长、轨道、字幕覆盖和基础画面异常，但不能替代内容审查。

## 22. 云朵与小鸟主题：文案、插画与旁白修正（2026-11-03）

- 用户试听首版后反馈：程序化画面过于简单，eSpeak 中文配音像机器人且难以听懂。
- `scripts/story/mock_provider.py`：为云朵与小鸟主题增加连贯的四句短故事；其它 mock 通用文案也不再描述镜头/画面位置，改为简单旁白。
- `scripts/scene/visual_semantic.py`：云朵是主体且文案提到小鸟时，将小鸟保留为画面对象，避免字幕说到小鸟而画面只画云。
- `scripts/scene/procedural_composition.py`：为天空故事增加全画幅渐变天空、日光/晚霞、远近山层、风迹和花草；移除调试画框与语义标签；云朵主体支持伴随小鸟，暮色画面增加枝条。
- 完整链路以 Edge TTS 重跑至 `/tmp/video-factory-artistic-voice-final-20261103/`。`run-manifest.json` 记录 `backend=edge`、`offline_fallback_used=false`、`quality_check=passed`；ffprobe 确认 1280×720 H.264 + 48 kHz AAC，22.667 秒。字幕/配音时间轴检查无冲突。
- 验证说明：实际运行是 `mock Story → Edge TTS → SVG 场景渲染 → 最终 MP4`；未调用在线 LLM。成片声音为 Edge 中文神经语音；视觉为项目内 SVG 插画风格，不是逐幕 AI 绘制的写实图像。最终可懂度和艺术偏好以用户试听/观感为准。

## 23. SenseNova 分镜图像生成（2026-11-03）

- 新增 `scripts/image_generation/sensenova.py`：调用 `https://token.sensenova.cn/v1/images/generations`，默认模型 `sensenova-u1.5-lite`，读取 `SENSENOVA_API_KEY` 或复用 `.env` 里与 `https://token.sensenova.cn/v1` 配对的 `OPENAI_API_KEY`。请求使用 `1024x576` PNG、`b64_json`，并验证返回 PNG 有效。
- `story_from_topic.py`、`story_pipeline.py` 与 `video_pipeline.py` 新增 `--image-provider sensenova`。该选项每个规划场景调用一次图像 API，图像保存到输出目录 `.images/scene-NN.png`；运行清单记录模型、提示词和文件。目标文件覆盖保护包含 `.images/`。
- AI 场景路由为全屏 cinematic renderer，隐藏重复的顶部旁白标题，保留底部字幕；不传 `--image-provider` 时沿用本地资产和 SVG 程序化渲染，不产生图像 API 请求。
- 官方接入信息：SenseNova U1.5 Lite 模型 ID、base URL、文生图端点和参数根据[商汤官方发布说明](https://www.sensetime.com/cn/news/sensenova-u1-5-lite-token-plan-20260911-1741)配置。图像尺寸须为 32 的倍数，故使用 1024×576。
- 端到端验证：新目录 `/tmp/video-factory-sensenova-final-20261103/` 成功生成 2 张 SenseNova 图片，使用 Edge TTS 完成双场景 22.68 秒 H.264 1280×720 + AAC MP4。`run-manifest.json`: `status=success`、`quality_check=passed`、`renderer=cinematic`、`tts.backend=edge`。未调用在线 LLM；Story 使用 mock provider。
- 回归：`test_video_pipeline_multiscene_asset_path.py` 通过；`test_tts_fallback_and_quality_checks.py` 12 项通过；新增 provider 配置/尺寸/场景提示词检查通过；关键脚本 `py_compile` 通过。

## 24. Audio8 TTS 替换 Edge 默认语音（2026-11-03）

- 根据用户提供的 Audio8 接入规格新增 `scripts/tts/audio8.py`，通过本机 tts-hub 调用 Audio8，强制 `allow_fallback=false`；服务未启动时按配置启动 Audio8 与 tts-hub，串行请求并输出 MP3 供现有流水线使用。
- `audio8` 成为各视频入口和环境准备脚本的默认 TTS；保留 `edge` 作为显式兼容选项。`auto` 切换为 Audio8 → eSpeak，避免隐式 Edge 回退。Audio8 默认音色 `default`，速率映射至 tts-hub，清单记录模型、服务、采样参数、分片估算和 pitch 不支持提示。
- 更新 README 和 `.env.example`，记录服务地址、启动行为和配置项。
- 验证完成：语法编译通过；Audio8 契约检查 3 项、TTS fallback/质量回归 12 项通过。按规格启动本机服务后，mock Story 成功输出 25 秒 H.264 1280×720 + AAC 48 kHz 双声道 MP4；清单确认 `backend=audio8`、`fallback_used=false`、音色 `default`、4 次请求/4 个估算分片、模型与 44.1 kHz 单声道源参数正确。
- 质量清单为 `review_required`，原因是输入 SRT 给每句安排的时间短于 Audio8 实际语音时长，自动扩展字幕并后移后续句子，其中一条后移超过 5 秒阈值；这不是合成失败，最终音画结束差 19 ms。生成的时间轴和复核报告保留在 `/tmp/video-factory-audio8-e2e-20261103/`。

## 25. 文案/图像 API 超时重试与 SenseNova 续跑（2026-11-03）

- OpenAI-compatible 文案与 SenseNova 图像请求默认超时提高到 300 秒，并默认对网络超时、429 和常见 5xx 重试 2 次；通过 `.env.example` 中对应环境变量可调整。
- `story_from_topic.py` / `story_pipeline.py` 增加显式 `--resume`：复用已有 Story 与 SRT，校验并复用有效的 `.images/scene-NN.png`，仅请求尚未生成的场景图。已有最终视频/TTS 等目标仍由原覆盖保护拦截。
- 用户当前 `output/my-video1` 的 7 张已生成 PNG 均验证有效，resume 预检通过；模拟文案与图像 API 首次超时后均在重试成功。项目 `.env` 中仅更新/补充超时与重试配置，没有调用额外 SenseNova 生成请求。
- 首次交付后发现 `story_pipeline` 的预检调用漏传 `resume=True`，导致旧 `.images` 和 manifest 仍触发覆盖保护。现已补齐参数并将前置冲突显示改为清晰错误；用用户现有目录做 mock CLI 预检，确认 SRT 可复用且 `resume=True` 传入视频管线，未触发外部 API。
