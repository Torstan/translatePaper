# 共享文档执行流程

**目标：** 两个 CLI 共用提取、选页、翻译、跨页修复、绘制与可选 QA；删除重复编排、批次调度和缓存写入代码。沿用已确认的 KISS 边界设计，不改变排版算法。

**依据：** [模块职责与边界](../specs/2026-10-04-translation-module-boundaries-design.md)。用户要求继续实施，并明确本轮不提交代码。

## 边界与现有策略

| 责任 | 落点 |
| --- | --- |
| 文档执行及现有 PDF 分析/渲染实现 | `pipeline.py`，从原单文件脚本迁入实际实现 |
| 固定批次执行、恢复、缓存写入、worker 调度 | `translation_batch.py`，两种组批共用一个执行函数 |
| 单文件参数与路径映射 | `translate_pdf_via_codex.py` |
| 文件筛选、文档并发与结果汇总 | `translate_pdf_parallel.py` |

共享入口为 `translate_document(pdf_path, output_path, options, *, job_name=None)`。`DocumentOptions` 是具体配置记录，不引入插件、阶段对象、回调注册器或兼容转发。批次为 `TranslationBatch(prefix, items)`；组批不依赖 worker 数。QA 编排属于文档执行，使用绘制返回的实际计划与译文。

| 默认值 | 单文件 CLI | 批量 CLI |
| --- | --- | --- |
| 组批范围 | 跨页 | 每页独立 |
| 批次字符数 | 10000 | 7000 |
| worker 数 | 1 | `--page-workers`，默认 2 |
| 缓存接受阈值 | `max(1, int(valid_ids * 0.6))` | 至少一个匹配 ID |
| 输出存在 | 重绘 | 跳过，除非 `--force` |
| QA | 默认不运行 | 默认运行，可 `--no-qa` |

这些默认值由 CLI 显式映射。共享执行函数的缓存阈值、组批范围和 worker 数互不推导。缓存仍沿用现有 ID 匹配规则，其内容指纹治理不在本轮范围。

## 实施与验证

- [x] 保存本轮开始时源码快照，保留上一批改动及用户已有 fixture。
- [x] 增加共享批次执行回归，覆盖 worker 不改变请求分组、部分缓存、重译、失败重试和增量保存；先验证新入口尚不存在，再实现。
- [x] 将原单文件脚本的核心实现迁入 `pipeline.py`，更新实际 Python 消费者与测试导入，不保留旧模块重导出。
- [x] 统一源分析到批次选择，保留参考文献延续、源页号、批次顺序和产物 prefix。
- [x] 实现共享 `translate_document`，显式接收配置；两个 CLI 调用同一入口。
- [x] 保持 `--refresh-source`、`--retranslate`、跳过现有输出、页子集与 QA 的现有语义；空页选择统一明确报错。
- [x] 删除批量脚本中的页级模型执行、缓存和 QA 编排副本；保留目录脚本的 CLI 兼容行为。
- [x] 更新测试到所属模块；增加两个真实 CLI 的模拟模型/确定性绘制路径测试，不仅检查转发调用。
- [x] 运行 AGENTS.md 指定回归、unittest discovery 和编译检查；用 fixture 对比本轮前后计划及 QA 报告。
- [x] 独立代码审阅，更新 README、设计证据与验证记录，不提交代码。

## 基线与范围

- 依赖环境：`PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache python3.12`。
- 上一轮 discovery：435 项，385 通过、49 项因缺少缓存源数据跳过、1 项既有深色背景文字像素测试失败。
- 本轮保留渲染、源分析和离线 QA 的已有规则；正式输出接受与完整内容指纹缓存是独立改造。


## 执行结果

| 验证 | 结果 |
| --- | --- |
| 新共享接口 RED | 共享批次与文档入口尚不存在时，新增回归明确失败；原两 CLI 的行为特征测试先通过 |
| 定向回归 | 82 项全部通过 |
| 扩充后的 AGENTS.md 回归 | 398 项：348 通过、49 跳过、1 项既有失败 |
| unittest discovery | 443 项：393 通过、49 跳过、1 项既有失败 |
| 涉及模块和测试编译 | 通过，含新 `pipeline.py`、共享批次模块及两个 QA CLI |
| git diff --check | 通过 |
| 独立审阅 | 无 Critical/Important；已修正文档措辞并补齐本记录 |
| 迁移 AST 对比 | 原核心 193 个定义不变；8 个 QA/产物函数仅变更所属模块与配置参数名 |
| 用户已有 fixture | page-017/page-025 的 SHA-256 与开工前一致 |

全量回归唯一失败仍是 `test_render_plan.GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`，改动前后均为 39 不大于 63。49 项跳过仍因缺少本地 Wait-free 缓存源数据。本批没有改动该像素测试断言或绘制算法。

源码快照：`/private/tmp/translatePaper-shared-before-x7vhwu9o/`。两批之间保留的上一轮改动包含在此快照中，本轮对比以它为基线。新 CLI、核心与批次模块合计从 5841 行变为 5787 行；主要收益是只有一条文档执行流程和一个批次恢复/执行入口。

确定性产物：`/private/tmp/translatePaper-shared-verify-8vh7bpui/`。

- `baseline/` 与 `candidate/` 读取相同的当前 fixture；7 份完整计划 JSON 逐项相等。
- BERT 3 页：0 项 QA 问题。
- Wait-free 4 页：9 项既有 `text_overlap`，问题内容、数量和检查页与基线一致。这是 fixture 计划的几何诊断，不是实际输出像素检查。
- 新的两个 CLI 集成测试另行生成真实两页 PDF、源页映射计划及批量 QA 报告；仅模型 subprocess 与外部页尺寸查询使用模拟输入。
- 测试日志：`/private/tmp/translatePaper-shared-{red,focused,regression,discovery}.log`。

本轮按保留现有默认值实施。缓存内容指纹、正式输出的质量门以及大模块中的领域职责拆分仍未完成。独立审阅同时确认一个原有恢复限制：并发执行在第一个失败 future 处停止收集结果，其他 worker 可能完成但结果未保存，下次恢复可能需要重跑；本批沿用该失败策略，文档只承诺已收集成功结果的原子保存。

按用户要求，所有改动均保留在工作区，没有暂存或提交代码。
