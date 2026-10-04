# 翻译与最终计划入口收敛 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 在当前会话逐项实施；延续已确认的模块职责设计和工作区。

**Goal:** 普通调用方获取最终译文和可绘制计划，无需拼接响应、修复、布局与校验状态。

**Architecture:** 在现有 `pipeline.py` 内收敛两个具体入口。复用最终译文字典、统计值和 `PageRenderPlan`，保持现有 CLI、缓存范围及布局规则。

**Tech Stack:** Python 3.12、unittest、PyMuPDF、Pillow；本地 `vendor` 依赖。

**Spec:** `docs/superpowers/specs/2026-10-04-translation-module-boundaries-design.md`，B/C 和 D/E/F 的 KISS 实施约束。

## Global Constraints

- 不预建候选 Python 文件、阶段基类、注册器或计划包装类型。
- 保留两个 CLI 的组批、worker、QA 默认值及缓存接受规则。
- 最终计划和 QA 报告继续作为追溯产物，不增加自动复用机制。
- 字体度量、未知内容保护、coverage 和 warning 契约保持不变。
- 保留用户已有 page-017/page-025 fixture 和其他无关工作区内容。
- 实施阶段不暂存或提交；提交按用户后续明确指令进行。

## Review Focus

- 缓存修复不得二次删除下一页译文前缀。
- 模型修复失败不得覆盖既有 PDF；已完成批次缓存仍可恢复。
- 最终计划按实际源页尺寸和输出页序号保存，离线 QA 使用同一内容。
- 校验失败保存完整诊断，报告写入失败仍保留主要错误。
- raster 绘制接收最终译文，其源布局诊断继续按既有范围运行。

### Task 1: 完整翻译入口

**Files:** `pipeline.py`、`tests/test_translation_batch.py`、`tests/test_final_render_plan.py`。

**Interfaces:**

- `translate_pages(selected_pages, page_size, job_paths, options: DocumentOptions) -> tuple[dict[str, str], int]`：内部组批、恢复/执行、校验和边界修复；返回最终译文及原任务 block 数，保留现有作业统计。
- `render_translated_pdf(..., *, render_mode: str) -> DocumentRenderResult`：消费最终译文，不再接收 model/reasoning_effort/retries 或执行修复。

- [x] 添加缓存恢复与修复只应用一次的入口回归，运行并确认缺失入口失败。
- [x] 将组批、执行、修复移入完整翻译入口，修改文档编排及绘制参数。
- [x] 验证 vector/raster 使用同一最终译文、缓存保留未修复响应，以及失败不覆盖 PDF。

### Task 2: 完整最终计划入口

**Files:** `pipeline.py`、`tests/test_final_render_plan.py`。

**Interfaces:**

- `build_final_page_plan(page_num, blocks, translations, page_size, *, bbox_lines=None, source_image_path=None, force_reference=False, output_page_num=None, job_paths=None, fitz=None) -> PageRenderPlan`：规划、适配、最终校验、保存诊断；失败抛出，不返回不合格计划。
- `write_vector_pdf()` 调用完整入口后执行绘制；保留逐页验证和资源关闭行为。

- [x] 添加可独立生成 fitting 计划、保存页映射及拒绝非法归属的回归；确认缺失入口失败。
- [x] 从绘制循环迁移 D/E/F 编排及诊断保存，复用现有校验规则。
- [x] 验证必需检查、完整失败报告、warning 和 QA 的现有回归。

### Task 3: 验证与文档

**Files:** `README.md`、既有职责文档、本计划。

- [x] 运行 AGENTS.md 完整回归、unittest discovery、编译和 diff 检查。
- [x] 对同一确定性源块/译文生成前后 PDF、PNG、计划和 QA；比较计划与输出像素。
- [x] 独立审阅本轮差异，更新 API 迁移说明和实际完成边界。

## 执行记录

- 基线源码：`/private/tmp/translatePaper-entry-before-wqaomsp_/`，提交 `dd497d8`。
- 已知基线：425 项回归中 375 通过、49 跳过、1 项深色背景文字像素失败（39 不大于 63）；提交前 HEAD 已复现相同失败。
- 用户已授权本轮 KISS/YAGNI 收敛；仅执行上述已确认职责范围。

### 实现结果

- `translate_document()` 只消费完整翻译入口的最终译文与统计，不再掌握组批、响应恢复及修复调用顺序。统计单独返回以保留原任务 block 数，未增加结果包装类型。
- `render_translated_pdf()` 删除模型/重试参数和句子修复调用；Python 调用方改用 `translate_pages()` 或完整文档入口。CLI 选项和默认行为保持。
- `build_final_page_plan()` 复用既有规划、布局及校验规则，只有通过必需检查才返回；通过 `job_paths` 请求的诊断产物由入口保存。绘制循环消费该计划，不自行编排布局与校验。
- 未新增生产 Python 文件或类型，未增加缓存机制或几何规则。源分析统一、raster 实际计划、缓存身份及正式输出接受仍属于后续迁移范围。

### 验证证据

| 验证 | 结果 |
| --- | --- |
| RED | `tests.test_final_render_plan` 9 项中 3 个失败，明确指向两个完整入口缺失 |
| 聚焦回归 | 最终计划、翻译批次、文档流程 27 项通过；随后新增的修复失败保护测试由完整回归覆盖 |
| AGENTS.md 回归 | 428 项：378 通过、49 跳过、1 项既有失败 |
| unittest discovery | 470 项：420 通过、49 跳过、1 项既有失败 |
| 必需 Python 编译、diff 检查 | 通过 |
| 独立审阅 | 20 项相关测试通过；没有 Critical/Important/Minor 问题 |
| 用户已有 fixture | page-017/page-025 与本轮前快照逐字节相同 |

唯一失败为 `tests.test_render_plan.GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`：39 不大于 63，与已验证 HEAD 基线相同。49 项跳过因为缺少本地 Wait-free 缓存源页。日志：`/private/tmp/translatePaper-entry-{red,green,regression,discovery}.log`。

确定性产物：`/private/tmp/translatePaper-entry-verify/{before,after}/`，每侧有 `summary.json`。

- 同一两页源块、缓存响应和修复结果，经真实文档流程生成 PDF、计划、渲染 PNG 与 QA 报告，无在线模型调用。
- 两份计划 JSON、两张 PNG 前后逐字节一致；正常 QA 均为 0 error、0 warning。原始响应缓存保持原字节，跨页前缀仅修复一次。
- 缺失归属样例保存失败计划，QA 为 1 error、0 warning，已有 PDF 保持原字节。
- 正常报告：`after/work/jobs/source/visual_qa/visual_qa_report.json`；拒绝报告：`after/rejected/visual_qa/visual_qa_report.json`。

实施阶段未暂存、未提交；用户后续已明确要求提交本轮改动。原有 fixture、未跟踪文档和本地产物保持在工作区。
