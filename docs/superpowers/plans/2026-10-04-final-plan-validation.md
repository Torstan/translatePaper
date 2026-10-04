# 最终计划校验收敛

依据：[模块职责与边界](../specs/2026-10-04-translation-module-boundaries-design.md) 的第 1 项迁移。用户已要求继续实施，并明确不提交代码。

## 本轮契约

- vector 在最终布局之后统一检查源归属、覆盖、几何、文字重叠、样式和 fit；任何结构错误都阻止该页绘制，与可选 QA 开关无关。
- 归属诊断从当前源块、components 和绘制项重新计算，替换旧快照。warning 保留原严重度，不能因整体 `ok=False` 被升级为 error。
- 一次收集所有检查结果，失败报告仍包含完整分类；报告写入失败不能掩盖主错误。
- 计划保存、源图片复制和文字绘制均在校验之后。失败关闭 PDF 资源，验证失败不覆盖既有 PDF。
- 复用现有验证函数，不增加校验注册器或阶段对象。可选内容质量策略、缓存指纹和 raster 真实布局计划继续按后续步骤处理。

## 实施步骤

- [x] 阅读实现和调用者，保存本轮前源码快照。
- [x] 增加归属缺失/重复、最终诊断刷新、warning 严重度及多类错误报告的失败回归。
- [x] 统一最终校验入口，删除重复错误分支，移动绘制到校验之后。
- [x] 运行聚焦测试、完整回归、编译检查，生成实际 PDF/PNG、计划及视觉 QA 报告。
- [x] 独立代码审阅，记录验证结果并同步职责文档。

## 基线

源码快照：`/private/tmp/translatePaper-final-before-sgft7e94/`。

环境：`PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache python3.12`。上一轮 discovery 共 443 项：393 通过、49 跳过、1 项既有深色背景文字像素测试失败（39 不大于 63）。本轮不修改已有的 page-017/page-025 fixture。

## 实现与行为变化

- `render_plan.validate_plan_ownership()` 统一计算源归属与图层互斥；几何检查移除重复的图层检查。规划阶段和最终布局阶段使用同一规则，最终结果替换旧诊断。
- `pipeline.validate_final_page_plan()` 收集归属、覆盖、几何、文本重叠、样式和 fit。绘制入口只保留一个失败分支；完整结果写入失败计划。
- `write_vector_pdf()` 使用上下文管理器，校验成功后才创建输出页、复制源图片和绘制。验证失败时原 PDF 不变，诊断写入异常仍不掩盖主错误。
- `qa_visual` 保留每条归属诊断的 severity；整体 `ok=False` 表示存在诊断，不代表所有问题都是 error。
- 文本互相重叠从可选质量检查提升为必需结构检查。原有 Wait-free 第 25 页 fixture 的 9 处重叠现在会阻止绘制；没有放宽规则或修改 fixture 隐藏问题。
- 可选 QA 仍在写 PDF 后执行，QA 拒绝不会撤回该 PDF；完整候选产物发布策略尚未实现。源图片仍存在计划外复制路径。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| RED | 新契约在旧实现产生 6 个失败，覆盖两种归属缺陷、旧诊断、文本重叠及 warning |
| 聚焦回归 | 最终计划、计划序列化、视觉报告 29 项全部通过；独立审阅也运行并通过相同范围 |
| 扩展定向回归 | 282 项，281 通过，1 项既有像素失败 |
| AGENTS.md 回归 | 405 项：355 通过、49 跳过、1 项既有失败 |
| unittest discovery | 447 项：397 通过、49 跳过、1 项既有失败 |
| 编译检查、git diff --check | 通过 |
| 独立代码审阅 | 未发现 Critical/Important/Minor 问题 |
| 用户已有 fixture | page-017/page-025 SHA-256 与本轮前一致 |

唯一测试失败仍是 `test_render_plan.GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`，39 不大于 63。49 项跳过因为缺少本地 Wait-free 缓存源页。日志位于 `/private/tmp/translatePaper-final-{red,green,focused,regression,discovery}.log`。

确定性产物：`/private/tmp/translatePaper-final-verify-c7hqg0zj/`，汇总为 `summary.json`。

- `accepted/translated.pdf`：实际生成 2 页 PDF；计划保存源页 2、3 及不同页尺寸；输出 PNG 的页映射一致。`accepted/visual_qa/visual_qa_report.json` 为 0 error、0 warning，已查看正文页 PNG。
- `rejected/plans/`：缺失归属计划被拒绝；`rejected/visual_qa/visual_qa_report.json` 为 1 error、0 warning；既有 PDF 字节保持不变。
- `fixtures/`：7 份最终布局计划与本轮前快照相比，除新增的 `validation_results` 外完全一致。视觉 QA 为 BERT 0 问题、Wait-free 9 个既有文本重叠。
- BERT 第 12 页另有 5 个既有 reference fit 错误；已使用 `baseline/` 快照确认旧绘制前检查也会报出。这些 fit 问题与视觉 QA 的检查范围不同，不能由视觉报告 0 问题推断页面可绘制。

三个生产模块合计净减少 6 行；更主要的变化是删除多处分支和重复归属检查，并使报告与绘制判断消费最终状态。本轮未暂存、未提交，已有其他工作区改动全部保留。
