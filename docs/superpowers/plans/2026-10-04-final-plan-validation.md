# 最终计划校验收敛

> 历史实施记录（2026-10-04）。当前完整计划入口见[翻译与最终计划入口收敛](2026-10-04-stage-entry-simplification.md)；模块职责见[模块职责与边界](../specs/2026-10-04-translation-module-boundaries-design.md)。

## 核心问题

布局和字体适配会改变绘制项。若只校验较早的计划，PDF 可能绘制未经最终检查的归属、覆盖或排版结果。

## 结论

vector 页在绘制前校验最终计划。源归属、覆盖、几何、文字重叠、样式和 fit 中的错误阻止绘制，不受可选视觉 QA 开关影响。诊断完整保存，warning 保持原严重度。

## 最小实现与边界

- `render_plan.validate_plan_ownership()` 按最终源块、components 和绘制项重算归属与图层互斥，替换旧诊断；几何检查不重复检查图层。
- `pipeline.validate_final_page_plan()` 一次收集各类必需检查结果。后续的 `build_final_page_plan()` 将规划、布局适配、校验和计划产物保存收进一个调用入口；绘制只消费通过校验的计划。
- 复用既有校验函数，不引入校验注册器、阶段对象或计划包装类型。
- 校验失败时仍尽力保存完整分类的诊断；报告写入失败不能掩盖校验错误。验证失败不覆盖既有 PDF，PDF 资源会关闭。
- 可选视觉 QA 在 PDF 写入后执行，拒绝结果不会撤回该 PDF。视觉 QA 与绘制前 fit 检查范围不同；视觉报告零问题不等于页面可绘制。源图片复制仍有计划外路径。

## 附录：实施时的证据与限制

- [最终计划回归](../../../tests/test_final_render_plan.py)覆盖归属缺失与重复、旧诊断刷新、warning、文字重叠及失败报告；当时聚焦回归 29 项通过。
- 当时 AGENTS.md 回归共 405 项：355 通过、49 跳过、1 项既有深色背景文字像素测试失败。跳过项缺少本地 Wait-free 缓存源页；失败测试为 `test_raster_draw_block_uses_light_text_on_dark_background`。
- 确定性两页样例生成 PDF、计划及视觉 QA 报告，正常报告为 0 error、0 warning；缺失归属样例被拒绝并报告 1 error，既有 PDF 字节不变。
- 7 份最终布局计划与实施前相比，仅新增 `validation_results`。Wait-free 第 25 页原有的 9 处文字重叠因此阻止绘制；BERT 第 12 页另有 5 个既有 fit 错误，虽然其视觉 QA 无问题。
