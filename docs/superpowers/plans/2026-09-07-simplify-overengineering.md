# Overengineering Simplification Implementation Plan

> 执行方式：本会话逐项执行并验证；代码集中于同一主流程，避免同时修改共享文件。完成后独立代码审查。不自动提交或合并。

**Goal:** 实施已确认的 1A、5A、2A、4A、3A，删除重复责任并保留内容保护。

**Architecture:** ownership 使用单一显式 API；PyMuPDF 负责栅格 PDF 组装；translation_batch 负责单批次翻译执行；vector 绘制、QA 和产物共享最终页计划；布局只保留有证据支持的阶段调用。

**Tech Stack:** Python 3.12（当前本地 vendor 对应版本）、unittest、Pillow、PyMuPDF、Poppler。

**Spec:** [已确认的方案](../specs/2026-09-07-overengineering-options.md)

## Global Constraints

- 用户确认没有外部 Python API/JSON 兼容要求，不需要 TeX，但保留 raster。
- 保留现有 CLI、组批边界、缓存策略、内容分类和显式 fallback。
- 缓存身份/刷新、输出跳过及硬编码语义修复不在本次范围。
- 不覆盖用户的两个 expected-plan fixture 修改及未跟踪文件。
- 最终计划的验证与绘制使用同一 fit 规则；QA 不修改计划。
- 不引入通用规则引擎、插件、布局求解器或弃用体系。
- 在当前独立分支执行；避免迁移现有未提交 fixture/vendor 到另一目录。

## Task 1: ownership API 和 JSON

**Files:** ownership.py, render_plan.py, translate_pdf_via_codex.py, tests/test_ownership.py, tests/test_render_plan.py, tests/pdf_render_fixture_runner.py。

- [x] 记录基线；用既有 ownership/fixture 测试保护结果。
- [x] 将多签名函数改为显式签名，同步仓库调用者。
- [x] 删除 *_compat 和 serializer/validator 能力探测，统一 components 字段。
- [x] 运行 ownership、render-plan 和 fixture 聚焦测试并检查差异。

## Task 2: PyMuPDF 栅格组装

**Files:** render_pdf.py, translate_pdf_via_codex.py, translate_pdf_parallel.py, README.md, tests/test_render_pdf.py, tests/test_translate_pdf_parallel.py。

- [x] 补无 TeX、多图页顺序/尺寸、缺图不覆盖目标的可观察测试，确认改动前失败。
- [x] 迁移现有 PyMuPDF 组装实现；直接接收图片路径，删除 TeX/legacy/转发和闲置 job keys。
- [x] 同步调用及依赖说明，运行渲染和并行入口测试。

## Task 3: 共享翻译批次执行

**Files:** 新增 translation_batch.py, tests/test_translation_batch.py；修改 translate_pdf_via_codex.py, translate_pdf_parallel.py 及相关测试。

- [x] 阅读 prompt/schema/normalize 依赖，写实际 runner 边界的故障输入测试。
- [x] 迁移必要纯函数，统一命令、重试、日志和 payload 校验。
- [x] 串行/并行复用执行器，保留各自分批和缓存策略。
- [x] 空译文和重复 ID 均失败并重试；验证不复用上一轮残留输出。
- [x] 运行两入口和新模块测试，检查调度结果与缓存写入。

## Task 4: 最终计划交接

**Files:** render_plan.py, translate_pdf_via_codex.py, translate_pdf_parallel.py, qa_visual.py, tests/test_render_plan.py, tests/test_translate_pdf_parallel.py, tests/test_qa_visual.py。

- [x] 为实际页尺寸、源页/输出页映射及 QA 不重建/不改写最终计划补测试。
- [x] 绘制返回最终计划及必要逐页元数据；vector QA 消费同一对象，移除二次构建。
- [x] 保留无绘制的独立诊断用途，明确 raster 诊断不是实际绘制计划。
- [x] visual QA 使用逐页尺寸与输出页映射；保留 JSON 产物与离线入口。
- [x] 验证失败计划、混合尺寸、部分页和跨页检查。

## Task 5: 布局阶段收敛

**Files:** translate_pdf_via_codex.py, layout.py, tests/test_layout.py, tests/test_render_plan.py, tests/test_pdf_render_fixtures.py。

- [x] 保存现有 fixture 最终计划对照，检查重复调用的真实作用。
- [x] 添加布局幂等性/内容守恒/可见几何测试；用多栏、图文、编号和参考文献场景覆盖。
- [x] 保持必要顺序，提取有明确职责的阶段；仅删除经验证无必要或有直接等价替代的调用。
- [x] 写明保留的重复操作为何必要，避免通用循环收敛。

## Final Verification

- [x] 执行 AGENTS.md 的完整回归，额外包含 ownership 和新增 translation_batch 测试。
- [x] 对规定模块及新增/改动模块执行 py_compile。
- [x] 运行确定性 fixture 路径，写出计划、PDF/PNG 和 visual QA 报告，检查问题数及路径。
- [x] 审查最终差异，处理引入的问题；更新本计划的执行记录。
- [x] 保留未提交改动供用户审阅。

## Execution Record

- 初始化：分支 codex/simplify-overengineering；基线日志 /private/tmp/translatePaper_simplify_baseline.log。


### 实施结果

- 1A：ownership 改为显式参数；删除四个兼容入口和序列化能力探测；JSON 仅保留 `components`。
- 5A：`render_pdf.write_raster_pdf` 直接接收图片路径，通过 PyMuPDF 组装并在成功后替换目标；删除 TeX、旧实现、转发函数和闲置路径。
- 2A：新增 `translation_batch.py`，集中 prompt/schema、命令、重试、输出解析。空译文、重复/缺失/额外 ID、非法 JSON 和失败退出均不接受；每次尝试删除残留输出，避免误用旧结果。保留串行跨页组批、并行按页组批及不同缓存接受策略，并添加特征测试。
- 4A：绘制返回 `DocumentRenderResult`；最终计划携带实际尺寸及源页到输出页映射。确定性 QA 和进程内 visual QA 直接消费最终计划；离线 visual QA 仍可读取 JSON。raster 的源布局诊断独立命名，不宣称对应最终栅格布局。
- 3A：提取固定顺序的 `arrange_page_render_items`，保留最终绘制边界的规范化；合并密集正文和普通文字重复的缩字号循环，显式传入上限。新增多栏、视觉障碍、内容/覆盖守恒和七个已有 fixture 的规范化稳定性检查。

### 布局删减的证据边界

临时 mutation probe 对每种删除变体比较了 95 个构建案例及 16 个规范化案例。删除第二次 rebalance 会使一个案例的正文位置变化约 17 pt，因此保留。其他部分重复调用在这些案例中没有产生差异，但这不足以证明全局冗余；本轮保留其固定顺序，说明拆分、邻接关系改变和 fallback 障碍对后续步骤的作用。未引入迭代收敛器，也不宣称布局算法已成为一次规划。

探查日志：`/private/tmp/translatePaper_layout_probe_summary.log`；具体差异：`/private/tmp/translatePaper_layout_probe_examples.json`。这些是本地诊断，不纳入源码。

### 验证与审查

- 环境：系统默认 Python 3.14 缺少 Pillow；使用本地 vendor 对应的 Python 3.12，命令前缀为 `PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_review_pycache python3.12`。
- 完整回归运行 AGENTS.md 列出的十个模块，加 `tests.test_ownership`、`tests.test_translation_batch`、`tests.test_final_render_plan`、`tests.test_layout_stages`。最终日志：`/private/tmp/translatePaper_simplify_final_tests.log`。
- 全量结果：424 项，374 通过、49 跳过、1 失败。唯一失败与改动前一致：`GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`，像素断言 `39 not greater than 63`。没有修改该测试或声称全绿。49 项跳过源于缺少 `work/jobs/wait-free-synchronization/source_pages.json`。
- AGENTS.md 规定的模块及新增/修改 Python 文件已通过 `py_compile`；`git diff --check` 通过。
- 确定性产物位于 `work/simplify-verification-20260907/`：`translated.pdf`、两页最终计划、`deterministic_quality_report.json`、`visual_qa/visual_qa_report.json` 和渲染 PNG。样例包含混合页宽及源页 2/3 → 输出页 1/2；两份报告的问题数均为 0，已检查第 2 源页 PNG 的文字显示及位置。全程无模型调用；这是布局验证，不是翻译质量评估。
- 独立代码审查发现 visual QA 最初仍读取 JSON，已改为直接接收最终对象，并用禁止重新加载的测试验证。最后补齐组批/缓存策略特征测试；未发现其余新增代码缺陷。
- 改动保留在 `codex/simplify-overengineering`，未提交。用户原有两处 expected-plan fixture 修改及无关未跟踪文件未改动；本地验证产物不纳入提交。
