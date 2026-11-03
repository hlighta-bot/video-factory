# 测试说明

本目录存放所有以 `test_*` 命名的测试脚本。运行时脚本保留在 `scripts/`，测试脚本统一放在 `tests/`。

## 运行前提

使用项目自带虚拟环境：

```bash
.venv/bin/python
```

如果尚未初始化环境，先在项目根目录执行：

```bash
python scripts/prepare_env.py
```

## 运行测试

由于测试脚本依赖 `scripts/` 中的运行时模块，推荐从项目根目录执行，并显式设置 `PYTHONPATH`：

```bash
PYTHONPATH=scripts .venv/bin/python tests/test_asset_resolver.py
```

如果只验证单个测试文件，把上面的文件名替换为 `tests/` 下对应的 `test_*.py` 即可。

## 常用回归命令

以下命令可直接在项目根目录执行：

```bash
# 资产解析器
PYTHONPATH=scripts .venv/bin/python tests/test_asset_resolver.py

# 资产自动补齐（Phase 31）
PYTHONPATH=scripts .venv/bin/python tests/test_asset_auto_resolution.py

# 视觉规范
PYTHONPATH=scripts .venv/bin/python tests/test_visual_spec.py

# Story 到视频链路
PYTHONPATH=scripts .venv/bin/python tests/test_story_pipeline.py

# Story 资产自动补齐端到端
PYTHONPATH=scripts .venv/bin/python tests/test_story_asset_e2e.py
```

## 测试文件分类

### 核心链路测试

这些测试覆盖 Story、Scene、Video Pipeline 的主流程，建议改动主流程后至少运行一次：

- `test_story_pipeline.py`
- `test_tts_fallback_and_quality_checks.py`
- `test_story_adapter.py`
- `test_story_schema.py`
- `test_scene_planner.py`
- `test_scene_plan.py`
- `test_video_pipeline.py`

### 视觉与资产相关测试

这些测试覆盖 Visual Spec、Asset Library、Asset Resolver、Asset Bridge 和 Annotation Policy，建议改动视觉资产逻辑时运行：

- `test_visual_spec.py`
- `test_asset_library.py`
- `test_asset_resolver.py`
- `test_asset_bridge.py`
- `test_asset_auto_resolution.py`
- `test_scene_annotation.py`
- `test_auto_annotation.py`

### 端到端 / 集成测试

这些测试运行更完整，耗时更长，适合确认全链路可用：

- `test_story_asset_e2e.py`
- `test_story_generator_e2e.py`
- `test_story_scene_e2e.py`
- `test_real_llm_story_integration.py`
- `test_scene_semantic_integration.py`
- `test_scene_plan_pipeline.py`

### 渲染相关测试

这些测试覆盖 Whiteboard、SVG、Manim 等 Renderer 路径：

- `test_whiteboard_bridge.py`
- `test_renderer_selector.py`
- `test_svg_renderer.py`
- `test_svg_animation.py`
- `test_manim_renderer.py`

## 结果判断

测试脚本采用直接运行方式，通常以退出码表示结果：

- `0`：通过
- 非 `0`：失败

如需排查失败原因，优先检查：

1. 是否在项目根目录执行
2. 是否显式带 `PYTHONPATH=scripts`
3. 是否使用项目虚拟环境 `.venv/bin/python`
4. 对应测试输入和 fixture 是否仍存在于 `tests/fixtures/`

## 目录约定

```text
srt-whiteboard-animation/
├── scripts/        # 运行时脚本
├── tests/          # 所有 test_* 测试脚本
└── tests/fixtures/ # 测试输入数据
```

新增测试时，统一命名为 `test_*.py`，并放入 `tests/` 目录。
