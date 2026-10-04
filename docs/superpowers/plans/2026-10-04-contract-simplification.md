# 翻译与绘制契约最小收敛 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. 用户已在会话中批准三批方案并要求设计和编码；在当前会话连续实施，交付未提交 diff。

**Goal:** 修复已复现的计划加载、重叠和内容顺序盲区，让最终译文与源图片通过现有入口交接。

**Architecture:** 复用 `PageRenderPlan`、`RenderItem` 和文档入口。先校验外部计划、收敛同义几何规则，再将纯文本整理移至翻译出口，将图片保留决策移至规划；只增加图片资源表达所必需的字段。保留依赖几何的现有规划算法，并为实际跨块转移维护来源。

**Tech Stack:** Python 3.12、unittest、现有 vendor 中的 PyMuPDF/Pillow。

**Spec:** [模块边界设计](../specs/2026-10-04-translation-module-boundaries-design.md)；本计划细化用户已确认的 KISS/YAGNI 三批方案。

## Global Constraints

- 保留 vector/raster、源页和输出页映射、显式 fallback、未知内容和非翻译内容保护。
- 不增加通用来源图、字符跨度框架、阶段基类、配置开关或新的生产模块。
- 使用现有 source IDs 的顺序检查完整正文合流；不能宣称其证明任意文档的全局阅读顺序。
- 缓存保留未整理响应；最终译文只在翻译出口整理，渲染与 QA 不再次清理。
- 图片规划保留现有 xref 提取、蒙版及分组裁剪语义；绘制阶段执行已选择的资源方式。
- 在当前 `1004-opt` checkout 内工作。保持原有未跟踪文档、vendor、work、缓存不变；不提交。
- 检查使用 `PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper-contract-pycache python3.12`，避免沙箱外的缓存写入。

## Review Focus

- JSON 缺字段、错误类型或非有限几何必须失败，合法空页仍可加载。
- 同源重复文字必须被检查，标题、段落和受保护区域两侧的合法分片不误报。
- 相邻正文合流后再拆分必须保持顺序；同一句文本在不同来源重复出现不能被错误去重。
- 译文整理不能重复剥离前缀；保留原始缓存、混合图文和 source-backed 页眉处理。
- 无文字图片、同 xref 多处使用、透明图片和已有视觉裁剪必须可追溯，避免重复绘制；图片必须成为布局障碍。

## Task 1: 加载、重叠和操作契约

**Files:** `qa_visual.py`、`render_plan.py`、`pipeline.py`，对应 `tests/test_qa_visual.py`、`tests/test_final_render_plan.py`、`tests/test_layout_stages.py`。

**Interfaces:** 保留 `generate_visual_qa_report()` 与 `validate_final_page_plan()`；计划文件校验集中在加载边界。强制校验与 QA 共用文字重叠谓词。

- [x] RED：损坏 JSON 不得报告零问题；同源完全重叠不得通过最终校验；正文合流乱序须拒绝；合法分片接受。
- [x] GREEN：删除来源相同的重叠豁免，复用几何规则；校验当前计划读取所需字段；利用已有有序 `source_ids` 检查完整正文合流。
- [x] 验证跨块续句转移的来源，必要时修复现有列表而不新增通用类型。
- [x] 运行定向测试与完整回归，记录行为变化。

## Task 2: 最终译文出口

**Files:** `pipeline.py`、`tests/test_final_render_plan.py`、`tests/test_review_cleanup.py`、`tests/pdf_render_fixture_runner.py` 及直接调用测试。

**Interfaces:** `finalize_translations(selected_pages, translations)` 返回新字典；`translate_pages()` 在边界修复后调用。渲染入口消费最终译文，离线 fixture 显式整理其响应输入。

- [x] RED：带软换行和源页眉的响应在翻译出口整理；缓存不变；绘制和 QA 使用同一文本；渲染不再次清理。
- [x] GREEN：集中现有纯文本规则，移除下游重复清理；混合内容仅选择对应文本片段，保留现有几何修复。
- [x] 更新直接调用测试的输入契约，保留独立人工期望值。
- [x] 运行翻译、文档、渲染和 fixture 回归。

## Task 3: 将源图片纳入计划

**Files:** `render_pdf.py`、`pipeline.py`、`render_plan.py`、`tests/test_render_pdf.py`、`tests/test_final_render_plan.py`。

**Interfaces:** 图片规划返回现有 `RenderItem`，包含源图片身份与已选资源引用；最终页规划接收这些项目并在布局、校验前合入。`render_plan_item()` 仅执行已选方式，删除计划外复制调用。

- [x] RED：合成 PDF 的图片出现在最终计划/产物中；阻挡正文布局；同一图片不因视觉区域覆盖重复绘制；透明图片外观保持。
- [x] GREEN：拆开现有图片选择与绘制，增加最小必要资源字段及序列化；现有视觉裁剪已完整覆盖的图片通过来源关联保留。
- [x] 运行真实 PDF 像素和计划断言，校验失败不得替换既有输出。
- [x] 更新 README 和边界设计中过时的已实现状态，记录当前检查范围。

## Verification and progress

- Baseline: `unittest discover -q` → 492 tests, OK, skipped=49；日志 `/private/tmp/translatePaper-contract-baseline.log`。
- Task 1: 定向 249 tests, OK；JSON 和重叠/乱序检查 RED→GREEN，单来源续句转移 RED→GREEN。
- Task 2: discovery → 497 tests, OK, skipped=49；译文出口回归 RED→GREEN。
- Task 3: 图片/最终计划 40 tests, OK；图片/QA 80 tests, OK；分片接缝误报和叠放图片丢层均有 RED→GREEN。
- 验证快照：discovery → 503 tests, OK, skipped=49；AGENTS 回归 → 460 tests, OK, skipped=49；编译通过。接缝 QA 修复后再跑完整检查，见最终验证记录。
- 确定性产物：`/private/tmp/translatePaper-contract-verification/` 下 native-images（真实 PDF、计划、PNG、QA）和 bert（3 页 fixture 计划、QA），两份 QA 均为 0 errors / 0 warnings。
- [x] 完成 AGENTS.md 指定全部回归、discovery 和 compilation。
- [x] 使用确定性 fixture 写出计划及视觉 QA 报告，核查问题数和路径。
- [x] 独立整体审阅、处理重要问题、检查 diff 和原有工作区状态。

## Execution rulings

- 本轮直接落实用户已批准的方案。详细计划用于审阅和追踪，不新增许可等待或自动提交。
- 当前分支无已跟踪修改，只有已有未跟踪资源；无需复制 vendor/work 或操作 Git 元数据即可隔离本轮 diff。
- 仅外部 JSON 输入在加载边界严格校验；现有检测函数的对象/字典输入暂保留，避免为了修加载错误展开另一轮接口迁移。
- Task 1: Ruling: 多来源条目的续句无法准确归属时保留原文位置；单来源转移更新双方来源。避免把全部来源误挂到上一段，代价是个别枚举不再进行该项排版优化。
- Task 3: Ruling: 复用 RenderItem 表达源图片义务，原始图片列表只读传给最终校验；只增加 source_image_xref，不新增来源注册表。图片 ID 在 source_ids/coverage ledger 中记录，文字组件归属体系保持原范围。代价是离线 QA 不能仅凭最终裁剪反推原生图片的完整源边界，覆盖依据保存在最终校验结果中。
- Task 3: Ruling: 保留既有全页背景、边缘图标过滤及视觉区域 PNG/PDF 读取策略；本次只迁移原先计划外复制的图片。代价是已有过滤策略的局限仍在，不宣称覆盖所有 PDF 原生绘图对象。
- Task 3: Ruling: 像素比较以重新打开的已保存 PDF 为输入；PyMuPDF 保存透明资源时会有 1 色阶差异，不能把保存前的内存页面当作文件输入的精确期望。

## Final verification

- 最终修复后，discovery：506 tests / 457 passed / 49 skipped；AGENTS 指定回归：463 tests / 414 passed / 49 skipped。跳过项依赖本地缺失的 Wait-free 缓存，与基线一致。日志分别为 `/private/tmp/translatePaper-contract-final-discovery.log` 和 `/private/tmp/translatePaper-contract-required.log`。
- AGENTS 编译清单以及额外触及的 geometry、layout_stages、review_cleanup 模块全部通过；`git diff --check` 通过。
- 已查看真实样例渲染 PNG：正文与图片分离，透明叠放颜色和来源保持；重新加载计划的 QA 报告 0 errors / 0 warnings。
- 独立只读审阅完成，范围为 HEAD 到未提交工作区差异及本计划；不包含本轮之前的未跟踪文件。发现 2 项 Important，无 Critical / Minor；重要问题均已修复。
- Final: fixed 合流重复 source ID、局部分片被误当完整翻译：`test_continuation_then_merge_keeps_each_source_once_in_final_flow` 与 `test_partial_source_remerged_below_visual_barrier_does_not_require_prefix_twice` RED→GREEN。合并维持首次出现顺序并去重；只有全部来源片段均在同组时才执行完整译文顺序校验，其余保留逐来源守恒检查。
- Final: fixed 合法页外图片被拒绝：`test_images_outside_page_preserve_only_visible_pixels_without_stretching` RED→GREEN，覆盖四边及完全页外；只规划可见交集，裁剪读取避免把整幅图片拉伸进交集。
- 一次修复后定向 47 tests 通过，再运行上述全部回归及编译；未追加第二次审阅或自动提交。

## Final review scope rulings

- Final: Ruling: 全页背景和边缘图标过滤沿用原政策，理由与 Task 3 一致；代价是这些历史过滤条件仍需单独评估。
- Final: Ruling: 全局阅读顺序和字符级来源不在本次局部检查范围；正常调用者获得完整合流顺序检查及局部分片守恒检查，代价是不能仅凭当前记录证明任意分片的跨来源顺序。
- Final: Ruling: 离线重建原生图片义务未增加新模型；现场最终校验使用只读源图片义务，产物保存结果。代价是只有最终计划的离线 QA 不能重新推导全部原始图片范围。
- Final: Ruling: 原生 xref 图片旋转处理沿用已有行为，本次不扩展该策略；代价是该旧路径在部分旋转图片上的外观局限仍在。
- Final: Ruling: 可选 QA 对输出发布的政策保持不变；结构错误先阻断绘制，但可选 QA 仍发生在 PDF 写入后，代价是可选 QA 拒绝不会撤回已写入 PDF。
