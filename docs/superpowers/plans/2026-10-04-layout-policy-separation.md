# 布局行为与诊断原因解耦

用户已确认实施上一轮提出的下一步：删除原因字符串参与布局/样式判断的分支，必要决策明确表达，原因只用于解释；验证原因修改不影响排版、任意原因不能豁免字号、合法降级保持可用。继续保留工作区改动，不暂存或提交。

## 数据与责任

在既有 `RenderItem` 增加两个有实际消费者的有限字段：

- `layout_role`：normal、body_flow、source_paragraph、callout、mixed_visual_body、embedded_heading、title_metadata、journal_footer。规划产生布局关系，合流/拆分保留或明确替换它；`style_name` 继续表达 body/heading/reference 等样式角色。
- `font_policy`：document、source_adapted、compact_body_flow、dense_visual_row。布局产生实际字号选择；样式检查核对对应的范围与适用角色，而非整体跳过检查。
- `fallback_reason`：继续写入绘制项和覆盖账本，作为诊断说明。报告可显示、绘制异常可引用，禁止据此推导布局角色、字体政策或豁免。

两个新字段在计划 JSON 中保存，离线 QA 与内存计划使用同一检查规则。旧 JSON 缺失字段按普通布局/文档字体检查，不从原因字符串推导授权；历史特殊计划需重新生成。

普通字号要求等于文档样式；源适配只允许现有样式上限内的放大；紧凑正文只允许现有最低字号到标准正文字号，且必须属于指定正文布局。角色拆分仅允许对应的样式与源分类组合，仍检查字号。保留现有几何适配算法，不引入策略注册器或通用校验框架。

## 实施与验证

- [x] 阅读规则、调用者、JSON 消费者并保存本轮前快照。
- [x] 编写失败回归，复现白名单原因跳过校验以及原因修改导致布局变化。
- [x] 为规划、合流、拆分及字号适配传递明确字段，删除原因驱动的行为判断。
- [x] 收紧角色/字号检查，并更新离线 QA 和既有测试的数据契约。
- [x] 完整回归、编译、确定性 PDF/PNG/计划/QA 产物与前后对比。
- [x] 独立审阅，更新职责文档和验证记录。

## 基线

源码快照：`/private/tmp/translatePaper-policy-before-czpx8sp4/`。

环境：`PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache python3.12`。

上一轮 discovery 为 447 项：397 通过、49 缓存源数据缺失跳过、1 项既有深色背景文字像素失败（39 不大于 63）。已有 Wait-free page-017/page-025 fixture 保持原样。

## 实现结果

- 删除 `STYLE_POLICY_ROLE_SPLIT_EXCEPTIONS` 的整体跳过检查、`TEXT_FLOW_EXCLUDED_FALLBACK_REASONS` 和浅包装 `render_font_size_and_reason()`。既有原因字符串继续用于报告。
- 角色拆分核对允许的样式和源分类；字号独立检查。源适配从现有最小放大阈值的实际舍入值到样式上限，紧凑正文为 5pt 到 9.2pt，并限制布局角色。稠密行只适用于单一来源、body 分类及不超过原高度阈值的正文行。
- `split_translated_text_around_protected()` 用 `dataclasses.replace()` 保留字体政策、布局角色和组件身份。
- `item_can_repair_body_text()` 统一正文修复资格，使用共享 `text_item_style_issues()`；`item_can_merge_body_text()` 复用它。邻接/包含式合并、编号重排、续句搬运、短片段来源与目标都保护 callout/source_paragraph，并保留非法权限的诊断。原文可选正文仍可参与合法修复。
- 合流继承实际选用字号项的政策，不根据原因或字号数值自动授权。字体适配显式写入紧凑政策，测量和绘制继续使用现有公共规则。

独立审阅发现三类需要补齐的集成边界：非法源分类/角色在合流后被洗白、包含式合流吞掉合法 callout/source_paragraph、编号及短片段修复绕过角色权限。新增聚焦回归先复现失败，再统一消费者检查。复核已确认全部关闭，当前无剩余 Critical/Important/Minor。

## 验证

| 验证 | 结果 |
| --- | --- |
| 初始 RED | 原实现 10 项测试产生 30 个子例失败，复现原因豁免和行为耦合 |
| 审阅修复 RED | 角色/分类洗白、锚定角色移动/吸收在修复前明确失败 |
| 最终聚焦回归 | `tests.test_layout_policy` 20 项全部通过，独立审阅重跑也通过 |
| AGENTS.md 回归 | 425 项：375 通过、49 跳过、1 项既有失败 |
| unittest discovery | 467 项：417 通过、49 跳过、1 项既有失败 |
| Python 编译、git diff --check | 通过 |
| 用户已有 fixture | page-017/page-025 SHA-256 与本轮前一致 |

唯一失败仍是 `test_render_plan.GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`：39 不大于 63。49 项跳过仍因本地 Wait-free 缓存源页缺失。日志：`/private/tmp/translatePaper-policy-{red,review-red,green,focused,regression,discovery}.log`。

确定性产物目录：`/private/tmp/translatePaper-policy-verify/`。

- `baseline/` 与 `candidate/`：7 份最终 fixture 计划移除新增两个字段后完全一致；QA 问题也一致，BERT 为 0，Wait-free 为 9 个既有文本重叠。修改所有原因说明后，最终几何、文字、字号与样式检查保持相同。
- `sample-before/` 与 `sample-after/`：从同一预制源块和译文实际生成两页 PDF；涵盖 callout/source_adapted 与 body_flow/source_adapted。两份最终计划除新字段外相同；两张 PNG 逐字节相同。QA 为 0 error、0 warning；已查看 callout PNG。
- `sample-after/rejected/`：把 callout 字号改为 1pt、保留白名单原因仍被绘制前校验拒绝；报告为 1 error、0 warning，既有 PDF 字节不变。

新字段替代原先隐藏在原因中的行为许可，增加有限数据和检查，删除隐式授权；本轮不以文件行数下降作为收益依据。

边界：本轮针对渲染项 `fallback_reason`。源组件 `reason_codes` 仍参与合法图文拆分判断，将随源分析边界收敛；原文 callout 回退的既有样式/锚定限制仍需后续处理。缓存身份、正式输出接受、raster 真实计划均未在本轮修改。历史特殊 JSON 计划需要重新生成，新规则不靠旧原因推导权限。

本轮未暂存、未提交；此前改动及用户已有 fixture 保持在工作区。
