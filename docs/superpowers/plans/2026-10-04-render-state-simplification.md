# Render state simplification

**Goal:** 收敛计划表示、派生状态和布局修复顺序，保持现有翻译及 vector/raster 能力。

**Architecture:** JSON 只在 `render_plan.py` 的加载边界转换，QA 消费 `PageRenderPlan`。
源覆盖义务独立于绘制结果；coverage 报告与保护区由最终 items 推导。
结构整理使用布局改变前的关系，几何适配不再重复执行文本结构修复。

**Spec:** `docs/superpowers/specs/2026-10-04-translation-module-boundaries-design.md`

**Constraints:** 复用现有模块；不增加通用 pass 框架、求解器、插件、缓存或兼容层。
不修改模型调用、译文修复规则、QA 接受策略及 OCR 行为。不提交本地产物。
本轮在现有工作区实施；原有未跟踪文档、vendor、work 等保持不动。

## Tasks

- [x] 记录完整测试基线和确定性 fixture 计划。
- [x] JSON 加载返回完整的 `PageRenderPlan`，保留页映射、坐标、绘制参数和诊断；
      QA 与 ownership 只读取对象。迁移测试调用到显式加载边界。
- [x] 将 coverage 的源义务与结果分开；最终结果从 items 推导，显式 skip 仍是义务决策。
      删除 `update_ledger_render_kind` 和保护框同步写入。
      测试移除 item、修改图片框、改变呈现方式及混合组件分别计账。
- [x] 先整理包含片段、编号连续关系、正文合流和短片段，再做几何适配。
      用源布局关系确定关联，删除已被回归证明不必要的重复结构修复。
      检查文字守恒、编号顺序、多栏隔离、视觉障碍与可重复执行的适配稳定性。
- [x] 运行 AGENTS.md 完整回归、discovery、编译及确定性报告；审查差异和更新边界文档。

## Verification

默认 Python 3.14 缺 Pillow。本轮使用 `PYTHONPATH=vendor python3.12`，
bytecode/cache 写入 `/private/tmp`，不修改依赖。

- 基线：`unittest discover -q`，510 项，461 通过、49 因缺少本地缓存跳过。
- 聚焦验证每项修改后运行；最终运行 AGENTS.md 指定模块及 discovery。
- fixture 路径使用已提交的源块和预制译文，无在线模型。
- 比较内容、覆盖、几何和 QA 问题，允许派生诊断按实际最终 item 更新。
- 最终 AGENTS.md 指定回归：475 项，426 通过、49 跳过；完整 discovery：519 项，470 通过、49 跳过。
  跳过原因与基线一致，均为本地源页缓存缺失。本轮新增 9 项回归。
- AGENTS.md 列出的模块及另外改动的测试均通过 `py_compile`；`git diff --check` 通过。
  测试日志为 `/private/tmp/translatePaper-kiss-required.log` 和
  `/private/tmp/translatePaper-kiss-final-discovery.log`。

## Execution notes

用户已在架构 review 后要求实施这三项收敛；本记录用于约束与追踪实施。
具体删除步骤以测试结果为依据，保留有实际必要性的适配算法。

- 新增回归证明：压缩两个原本独立的段落后再次修复编号，会错误地把后段句子移入前段。
  删除布局后的结构修复，保留源位置下的关联判断；既有多栏、文字守恒及适配稳定性测试通过。
- 七页已提交 fixture 的最终 `render_items` 与修改前逐项相同，必需校验和视觉 QA 问题列表未增加。
  简化 fixture 路径仍有原有问题：wait-free 第 25 页 9 项文本重叠，BERT 第 12 页 5 项参考文献 fit 问题。
  前者也由视觉 QA 报告，后者由必需 fit 校验报告；其余五页均无错误或 warning。
  产物位于 `/private/tmp/translatePaper-kiss-{before,after}/`，不是提交内容。
- 独立审查发现并修复缺失字号时报告格式化的崩溃；回归先失败后通过。
- 独立审查还发现空组件 ID 的合并项会掩盖同源图片缺失。
  派生覆盖现在要求明确组件 ID，或由该源的唯一组件/唯一组件类型确定归属；
  同类多个组件不能由无身份项同时覆盖。缺图与同类歧义回归先失败后通过，
  JSON 往返保持缺失状态，复查无剩余阻断项。
- 后续审查发现单一视觉组件仍可被同身份文字项误判为覆盖，且加载器对部分畸形可选字段
  抛出原始 `TypeError`。新增回归先复现再修复：视觉组件必须有图片裁剪，平凡 OCR 文本也如此；
  加载时直接校验坐标空间、覆盖组件身份和组件原因码类型。
- 修复后规定回归 478 项（429 通过、49 跳过），完整 discovery 522 项（473 通过、49 跳过）；
  编译与 `git diff --check` 通过。七页 fixture 的最终绘制项、必需错误及视觉 QA 问题
  与修复前逐项一致。新日志和报告位于 `/private/tmp/translatePaper-review-fix-*`。
