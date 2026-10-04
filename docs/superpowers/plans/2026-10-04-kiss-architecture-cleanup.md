# 翻译架构 KISS 清理实施记录

**Goal:** 删除已确认的冗余代码和状态，复用现有模型执行器，保持翻译选择、排版与覆盖检查行为。

**Architecture:** 在现有模块内收敛责任。组件保存源内容事实，归属账本在导出时生成；回译保留自己的响应校验，共享 `translation_batch.execute_json_task()` 的进程与重试逻辑。

**Tech Stack:** Python 3.12、unittest、仓库 vendor 中的 Pillow/PyMuPDF。

**Spec:** [模块职责与边界](../specs/2026-10-04-translation-module-boundaries-design.md)。本记录实施其中的冗余清理与执行器统一，文档中的完整流水线迁移另行分批实施。

## 约束

- 保留用户已有的 page-017/page-025 fixture 修改，不重新生成它们。
- 保留 unknown、显式 fallback、角色分类、源归属和覆盖检查。
- 不增加阶段基类、插件接口、通用缓存图或候选模块空壳。
- 本批不修改组批策略、CLI 选项、布局算法和 raster/vector 能力。
- 在当前工作区执行，交付可审阅 diff，不自动提交。

## 任务与验证

### 1. 删除无消费者代码及浅包装

- [x] 删除 `prepare_page_ownership()`，并行入口直接调用组件分析函数，读取其参考文献延续状态。
- [x] 删除 `TranslationPageOwnership.raw_visual_regions/force_reference_page` 对外字段；内部分析规则保持。
- [x] 删除 `write_vector_pdf()` 未使用的页尺寸参数并更新所有调用方。
- [x] 删除 QA 无调用私有函数、图片对包装、仅测试使用的 JSON 包装，以及 CLI 无用重导出。
- [x] 删除仅检查包装层存在的测试；已有参考文献延续、翻译选择和 CLI 产物测试继续保护行为。
- [x] 报告稳定性测试改为读实际写出的 JSON 和 Markdown。

### 2. 复用回译执行器

- [x] 增加回译回归：残留输出、非法 JSON、重复/缺失/多余 ID、空文本或错误字段类型均重试；耗尽次数时失败并保留诊断。
- [x] 先运行并确认上述测试暴露旧路径的问题。
- [x] 回译通过 `execute_json_task()` 执行，保留 prompt/schema/批次选择；删除独立进程包装及测试专用 runner 参数。
- [x] 在 subprocess 边界模拟模型输出，验证批次结果、重试次数和日志。
- [x] 回译原始响应产物统一为 `backtranslate-NN.out.json`，日志和最终报告路径保持；在说明中记录。

### 3. 消除重复归属状态

- [x] 增加回归：直接构造或更新计划组件后，导出的归属账本必须与当前组件一致。
- [x] 删除 `PageComponent.render_strategy`、固定的 `ownership_role`，更新序列化契约断言。
- [x] 删除计划对象中的 `ownership_ledger` 副本和仅用于中转的记录类型，在 JSON 边界由组件生成账本。
- [x] 保持排序、去重、原因、置信度、页号与覆盖账本信息；unknown 保留由实际 render item 测试验证。

### 4. 验证与交付

- [x] 更新边界文档中的 KISS 实施约束和本批契约变化。
- [x] 运行 AGENTS.md 要求的完整回归及 compilation 检查，另运行 unittest discovery 覆盖新增模块测试。
- [x] 用确定性 fixture 写出计划与视觉 QA 报告，检查问题数与文件路径。
- [x] 审阅最终 diff，核对原有 fixture 的 SHA-256，记录结果。

## 环境与执行结果

- 默认 Python 3.14 缺少 Pillow；使用 `PYTHONPATH=vendor python3.12` 加载已有依赖。
- 编译缓存写入 `/private/tmp/translatePaper_pycache`，避免写入工作区或沙箱外的用户目录。


| 验证 | 结果 |
| --- | --- |
| 改动前 discovery | 435 项：385 通过、49 跳过、1 失败 |
| 新增回归 RED | 复现接受旧输出、接受重复 ID、非法 JSON/字段类型未重试、缺失诊断及账本漏项 |
| 改动后定向测试 | 147 项全部通过 |
| AGENTS.md 指定回归 | 380 项：330 通过、49 跳过、1 项原有失败 |
| 改动后 discovery | 435 项：385 通过、49 跳过、1 项原有失败 |
| 指定模块及新增涉及模块编译 | 通过 |
| git diff --check | 通过 |
| 独立代码审阅 | 无 Critical/Important；已修正旧函数名文档引用 |
| 用户原有 fixture | page-017/page-025 的 SHA-256 与开工前一致 |

基线与改动后唯一失败均为 `test_render_plan.GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`，像素计数相同：39 不大于 63。本批未改动该绘制逻辑或测试断言。49 项跳过均由于本地缺少 Wait-free 缓存源数据；仓库内的 7 页确定性 fixture 已实际执行。

独立审阅另运行语义 QA、归属与并行组批的 58 项测试，全部通过。完整流水线迁移、用户原有产物与上述基线失败不计入本批完成范围。本批尚未提交。

确定性产物位于 `/private/tmp/translatePaper-kiss-verify-5pvb7zrl/`：

- `baseline/` 使用 HEAD 中的生产代码；`candidate/` 使用当前代码，两者读取相同的当前 fixture。
- 共导出 7 份计划；去除本批退役的 `render_strategy`、`ownership_role` 两个 JSON 字段后，计划逐项相等。
- `candidate/bert/qa/visual_qa_report.json`：3 页，0 问题。
- `candidate/wait-free/qa/visual_qa_report.json`：4 页，9 项 `text_overlap`，与基线的每项诊断完全相同。这是规划阶段 fixture 的几何诊断，未运行真实输出像素检查。
- 测试日志：`/private/tmp/translatePaper-kiss-{baseline,red,focused,regression,discovery}.log`。
