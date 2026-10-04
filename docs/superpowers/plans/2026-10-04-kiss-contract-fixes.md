# KISS 翻译契约修复实施记录

> **For agentic workers:** 使用 superpowers:executing-plans 连续实施，按任务验证。

**Goal:** 修复逐页 OCR 坐标、页脚内容替换、渲染补译及内容重复漏检，使独立 QA 留下完整报告。

**Architecture:** 复用现有源分析、最终译文、RenderItem 和最终校验入口。布局只分配文本；源关系由已有组件和必要的译文片段引用表达。保留 vector/raster、现有字体权限与可选 QA 接受政策。

**Tech Stack:** Python 3.12、unittest、现有 vendor Pillow/PyMuPDF。

**Spec:** ../specs/2026-10-04-translation-module-boundaries-design.md；本次对话中用户批准的 KISS/YAGNI 修复方案。

## 约束

- 在当前工作区交付可审阅 diff，不自动提交；不修改原有未跟踪文件。
- 不新增规则引擎、阶段框架、配置开关或缓存系统。
- 测试使用确定性输入，不调用在线模型。缓存写到 /private/tmp。
- 不变更可选 QA 是否接受正式输出的政策；强制结构校验继续阻止绘制。

## Review Focus

- 混合尺寸和选择部分页面：坐标始终采用源页真实尺寸。
- 不同卷期、日期与页尺寸：页脚文本不被替换，一致性只比较样式。
- 多源同文与同源拆分：合法重复文字通过，重复区间、遗漏和乱序失败。
- 混合图文与合流：来源不由绘制方式反推，保护视觉片段。
- 回译异常与 strict QA：其他独立检查产物仍可检查，失败不伪装为通过。

## 任务

### 1. 源页面和页脚

Files: pipeline.py、classify.py、tests/test_architecture_contracts.py。

- [x] 增加混合页尺寸 OCR、原卷期保留、跨尺寸页脚样式回归，先观察失败。
- [x] OCR 接收逐页尺寸；提取缓存版本递增，避免复用旧错误坐标。
- [x] 页脚使用源文本，清除固定卷期内容和跨页文本/绝对位置相等要求。
- [x] 运行聚焦测试。

### 2. 最终译文与来源

Files: pipeline.py、render_plan.py、layout.py、相关回归测试。

- [x] 增加缺译不补写、布局不改义、重复/遗漏/乱序和合法拆合回归，先观察失败。
- [x] 删除渲染补译和特定词句修复；保留几何拆合，语义整理归翻译出口。
- [x] 现有 RenderItem 增加必要的译文片段引用，合并/拆分传递；最终校验检查精确内容覆盖。
- [x] 生产路径复用完整源分析；组件元数据随创建/转换继承。
- [x] 更新旧行为测试与契约说明，运行相关 fixture。

### 3. 独立 QA

Files: pipeline.py、tests/test_architecture_contracts.py。

- [x] 增加回译失败及严格模式仍保留视觉、确定性报告的回归，先观察失败。
- [x] 独立检查各自完成，汇总后应用原有错误政策；不新增 QA 框架。
- [x] 运行聚焦测试。

### 4. 验证与审阅

- [x] 运行 AGENTS.md 指定回归、完整 discovery、Python 编译和 diff 检查。
- [x] 确定性 fixture 写出最终计划和 visual-QA，检查问题数量及产物路径。
- [x] 独立代码审阅并处理实际缺陷；记录结果。

## 执行记录

- 基线：HEAD 53e637c；Python 3.12 + PYTHONPATH=vendor，完整 discovery 通过，日志 /private/tmp/translatePaper-kiss-baseline.log。
- 用户已明确要求按已讨论方案修复，直接实施；不增加重复审批步骤。

- 源契约：逐页 OCR 坐标、子集页号、源分析中的编号标题分类、真实页脚内容均已落地；源提取缓存版本由 1 升至 2。
- 文本契约：删除渲染补译与特定词句改写；现有 RenderItem 增加最终译文去空白后的 TextSpan，拆合传递，检查重复、遗漏、区间顺序及标点/大小写。旧 helper/fixture 无区间时仅在校验副本中绑定；生产计划在布局前绑定。区间不证明英中对齐或全局阅读顺序。
- QA：视觉检查与回译分别完成或记录失败，再应用既有 strict 政策；新增 qa_summary.json。可选 QA 仍发生在 PDF 写入后。
- 13 项新架构回归覆盖上述契约。独立只读审阅发现跨源完整片段连同区间交换可绕过校验；新增失败回归后恢复封闭正文合流的源顺序检查，相关 198 项测试通过。
- 规定回归：478 项，429 项通过、49 项跳过；日志 `/private/tmp/translatePaper-kiss-required.log`。完整 discovery：535 项，486 项通过、49 项跳过；日志 `/private/tmp/translatePaper-kiss-final.log`。跳过项需要本地 Wait-free 缓存，与基线一致。
- AGENTS.md 所列 Python 模块及新增测试编译通过；使用 Python 3.12 + PYTHONPATH=vendor（默认 Python 3.14 环境缺少 Pillow）。编译缓存位于 `/private/tmp/translatePaper_pycache`。
- 7 页确定性 fixture 计划及 QA：`/private/tmp/translatePaper-kiss-contract-fixtures/summary.json`。BERT 页 12 的 5 处参考文献 fit 问题、Wait-free 页 25 的 9 处重叠与 HEAD 53e637c 基线逐条相同；没有新增问题。报告检查的是计划，未据此声称旧样本 PDF 通过像素验证。
- 实际绘制样本：`/private/tmp/translatePaper-kiss-sample/translated.pdf`；源页 2、3 映射到输出页 1、2，尺寸分别为 260×360、360×260 pt，中文文本可选择。对应 plans、visual_qa/rendered_png 已检查；visual_qa/visual_qa_report.json 为 0 错误、0 警告。
- 代码与说明保留为未提交 diff。原有未跟踪文件未改动，未生成工作区内的新缓存或输出。

## 2026-10-05 未提交代码审阅修复

- 复现并修复 3 处审阅发现：普通正文中的视觉样式前缀被区间校验误删、译文续句被移入原文回退项后漏算、分级标题编号的末尾句点导致最终计划失败。逐项新增失败回归后修复；区间只在明确的图文混合源组件上使用已规划的正文片段，其余使用完整最终译文；续句不跨绘制类型移动；编号保留源标点且不构造模型任务。
- AGENTS.md 规定回归：478 项，429 项通过、49 项跳过，日志 `/private/tmp/translatePaper-kiss-review-fix-required.log`。完整 discovery：538 项，489 项通过、49 项跳过，日志 `/private/tmp/translatePaper-kiss-review-fix-discovery.log`。Python 编译检查与 `git diff --check` 通过。
- 确定性 fixture 的最终校验问题及视觉 QA issues 与 HEAD 53e637c 基线相同；实际混合尺寸 PDF 样本视觉 QA 仍为 0 错误、0 警告。代码与说明仍为未提交 diff。
