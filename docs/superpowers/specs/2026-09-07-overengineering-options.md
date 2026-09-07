**过度设计治理：方案对比与建议**

日期：2026-09-07。状态：用户已确认推荐路线 1A、2A、3A、4A、5A，代码已实施，未提交。以下保留方案比较与决策依据；具体执行和验收结果见[实施记录](../plans/2026-09-07-simplify-overengineering.md)。

**核心问题**

内部接口兼容、翻译执行、布局修复、计划构建和 PDF 组装存在重复责任，修改一处行为需要理解并同步多条路径。

**结论**

建议选择 **1A、2A、3A、4A、5A**：直接统一内部契约，先共享翻译批次执行，按阶段验证并削减布局修复，让 QA 使用实际绘制的最终计划，统一使用 PyMuPDF 组装栅格 PDF。先做可独立验证的删除与合并，再处理布局算法；不引入插件框架、通用流水线框架或布局求解器。

用户已确认：没有仓库外脚本需要兼容内部 Python API 或 render-plan JSON；不需要 XeLaTeX 和生成的 `.tex` 文件，但需要保留 raster 模式。这两项已知条件支持直接清理，不需要兼容过渡期。

**最小正文**

本轮覆盖上一轮 review 的五类过度设计。保留 vector/raster 模式、现有 CLI 用途、内容分类、ownership、coverage ledger 和显式 fallback。缓存身份、缓存刷新、QA 失败后输出跳过等 bug 不在本轮顺带修复；共享执行器引起的校验行为变化须明确列出并测试。渲染中的硬编码语义修复也不能以“重构”名义静默改变，另列修复项。

工作区现有 fixture 修改和未跟踪文件保持原样。历史设计文档提供背景，当前代码才是方案判断依据。任何历史方案中未落地的扩展均不作为本轮需求。

**问题 1：同仓库 API 被当成多个版本兼容，序列化字段重复**

事实：`build_page_components_compat` 和 `validate_ownership_compat` 捕获 `TypeError` 后尝试其他签名；`ownership` 内部同时使用 `*args/**kwargs` 解析多种调用形式。`render_plan_to_json` 对同一份 components 输出 `components` 和 `ownership_components`，序列化和校验还带有“函数不存在”的降级分支。[E1]

分析：调用方和被调用方同时承担兼容责任；内部抛出的 `TypeError` 可能触发错误的重试。这里没有已确认的外部版本兼容需求。

| 选项 | 做法 | 收益 | 代价与适用条件 |
| --- | --- | --- | --- |
| **1A：统一契约并直接删除兼容（推荐）** | 为 ownership 定义一套显式签名；所有仓库调用点和测试同步修改；直接调用本地 serializer/validator；JSON 只输出 `components` | 删除双重参数解析、能力探测及重复字段，错误在真实位置暴露 | 需要更新旧格式样例的仓库读取器；旧生成计划可重新生成，不自动重写用户工作目录 |
| 1B：只收敛 Python API，暂留双 JSON 字段 | 内部同 1A；对外产物仍同时输出两个字段 | 先缩小接口重构影响，保留历史产物消费者的缓冲 | 用户已确认没有外部消费者，双字段没有明确退出必要性；因此当前不推荐 |
| 1C：将兼容集中到单一边界适配器 | 核心只接受规范参数；旧调用和旧 JSON 仅在专门适配函数归一化 | 确有旧工具依赖时，避免兼容逻辑散布 | 仍需维护适配代码和测试；只有出现具体外部依赖才有价值 |

范围：`ownership.py`、`render_plan.py`、`translate_pdf_via_codex.py`、`tests/pdf_render_fixture_runner.py` 及直接调用测试。保留 coverage 和 ownership 两类记录：前者记录绘制结果，后者表达源内容归属，不能因为都叫 ledger 就合并。JSON 只在加载边界转为内部对象；合法的字典输入处理不属于待删除的版本兼容。

验收：相同输入产生相同组件、源 ID 归属和校验结论；内部 `TypeError` 不被重新解释或重试；fixture 与 QA 使用单一字段。签名与字段变更明确记录，不额外加入弃用警告体系。

**问题 2：串行与并行重复实现翻译执行**

事实：`translate_batches` 和 `run_translation_batch` 重复命令构造、输出解析、重试、日志和 ID 校验；`run_parallel_translation` 另行处理缓存写入。两条入口目前不是仅并发数不同：串行允许跨页组批，并行按页组批；缓存接受规则、空译文校验、跨页句子修复及 QA 的调用位置也不同。[E2]

| 选项 | 做法 | 收益 | 代价与适用条件 |
| --- | --- | --- | --- |
| **2A：共享批次执行，保留已有编排（推荐第一步）** | 提取具体的 `execute_translation_batch`，负责一次批次的命令、重试、日志和结果校验；两个入口传入各自已经分好的批次 | 删除真正相同的执行逻辑，不改变组批上下文；范围容易测试 | 组批、缓存接受规则和文档编排仍有差异，需在代码和测试中明确保留，不宣称完全统一 |
| 2B：统一文档翻译流程，CLI 只负责映射参数 | 一个文档执行入口负责分批、恢复、调度、后处理和渲染；worker 数控制顺序/并行；原脚本仅解析参数并调用 | 重复最少，后续修复只需一条流程 | 必须选定分批、缓存、跨页修复、QA 的统一行为，可能改变模型上下文、调用次数和产物命名；属于更大范围的行为调整 |

推荐 2A 的边界：共享函数放入具体模块，例如新增 `translation_batch.py`；prompt、schema、结果规范化及其必要纯函数依赖一并迁移，避免新模块反向 import 5,700 行的 CLI 文件。旧入口不保留仅为测试方便的转发和 re-export。先不把翻译、回译和跨页修复做成支持任意任务的通用执行引擎。

共享结果校验采用“预期 ID 完整、无重复 ID、每条译文非空”的明确契约。串行路径原先接受空译文，改为失败并重试属于有意行为修正，须单独记录并用故障输入验证。缓存命中数据不因此自动获得有效性保证，缓存治理仍单列。

验收：同一批次在两条入口得到相同解析和错误处理；用模拟 runner 验证退出失败、非法 JSON、遗漏/重复 ID、空译文和最终失败；同一预制批次集合在单 worker 与多 worker 下得到相同 ID→译文映射。原入口的批次边界、页顺序和缓存策略另用特征测试保护，不依赖真实模型调用。

若选择 2B，需要先决定跨页组批还是按页组批，以及原单 PDF 入口是否增加 QA；当前不把这些未知行为默认为已批准。

**问题 3：布局由多轮修复串联，阶段责任和终止条件不清楚**

事实：`build_page_render_plan` 收尾重复执行合并、拆分、编号顺序修复、短片段吸收和重排；`normalize_vector_text_layout` 又进行多轮视觉区域释放和文本扩展。一个步骤可能修复上一步，也可能重新破坏之前的条件。[E3]

分析：重复调用并不证明冗余。例如第一次拆分产生的新文本项，可能确实需要后续重排。未经 fixture 对照删除步骤，无法保证内容守恒与可读顺序。

| 选项 | 做法 | 收益 | 代价与适用条件 |
| --- | --- | --- | --- |
| **3A：先建立阶段契约，再删除有证据的冗余（推荐）** | 将现有顺序分为文本结构整理、保护区域分配、文本排版、最终验证；先保持执行顺序，补阶段前后断言；逐项验证哪些重复调用可删 | 保留已覆盖文档行为，逐步减少隐含依赖；无需新算法和新依赖 | 第一阶段未必立刻大幅减行数；有作用的二次执行仍需保留并说明触发原因 |
| 3B：重写为按栏位和可用区域一次规划 | 先确定保护区与可用文本区，再按阅读顺序分配文本并执行统一 fit；删除旧的事后修补链 | 长期模型更直接，有机会消除大量往返修复 | 改动最大，需重新验证跨栏、嵌入标题、混合图文及参考文献；不足以只靠当前少量 fixture 证明行为一致 |

推荐 3A 的边界：不创建可注册 pass、规则优先级框架或“直到不变”为止的通用迭代器。现有函数只有在共享同一规则和不变量时才合并。逐步使几何排版不再改写译文语义；结构重排的可见变化需显式报告。

验收：最终计划保留全部非平凡源内容，混合图文组件允许有明确声明的拆分归属；不新增文本重复、丢失、越界、遮挡和 style/fit 错误；对声明可重复运行的阶段验证幂等性。内容守恒检查应考虑有记录的空白规范化与跨项分配，不能简单比较原始字符串顺序。未证明幂等性的阶段只允许在固定位置执行，不能靠重复调用收敛。

fixture 策略：使用已提交的页数据与翻译，补独立于本地 `work/` 的多栏、混合视觉块、编号正文和参考文献场景；每删除一组修复，检查最终计划和 visual-QA 报告，保留明确失败而非增加隐式 fallback。

**问题 4：绘制与 QA 分别构建渲染计划**

事实：`write_vector_pdf` 构建并规范化计划后绘制、写 JSON；`validate_document_quality` 随后再次处理翻译、构建计划并规范化。绘制使用源 PDF 逐页尺寸，后者接收统一 page_size。visual QA 又从 artifact 读取计划。[E4]

| 选项 | 做法 | 收益 | 代价与适用条件 |
| --- | --- | --- | --- |
| **4A：进程内传递最终计划（推荐）** | 每页构建和规范化一次；绘制、确定性 QA、计划序列化使用同一个最终对象；逐页传递源页号、输出页号和实际尺寸；跨页检查在汇总后执行 | 无需 JSON 往返，QA 对应实际绘制布局；删除第二次构建及重复规范化 | 调整绘制返回值和 QA 参数；长文档要注意保留计划的内存，避免同时持有页面图片 |
| 4B：以最终 JSON artifact 为交接边界 | 绘制写完整计划及逐页元数据；后续 QA 只加载这些产物，不重新构建 | 独立复查和跨进程使用更直接，进程生命周期解耦 | 必须定义完整加载契约，拒绝缺页、过期和不完整产物；增加文件 I/O，不能静默回退到重建 |

推荐 4A 的实现边界：复用现有 `PageRenderPlan`，只增加传递逐页元数据必需的普通记录，不引入仓库、服务容器或文档图。最终计划完成后，绘制与 QA 均不得再修改；绘制必需的 fit/coverage 检查仍在绘制前执行，QA 不成为修复步骤。默认仍保存 JSON，既有离线 visual QA 能力保留；4A/4B 的区别是主流程的交接方式，不是是否输出报告。

vector 与 raster 的绘制流程目前不同。不能将 vector 计划标记为 raster 的实际绘制计划；本项先收敛 vector，raster 现有诊断须明确其范围，不新建另一套通用渲染框架。

验收：一次处理每页只完成一次最终计划构建/规范化；同一输入的绘制项与报告项一致；覆盖混合页尺寸和源页 5→输出页 1 的子集案例；校验前后对象内容一致；阶段错误仍可写出可检查的失败计划，禁止 QA 发现缺计划后自行重建来掩盖问题。

**问题 5：栅格图片组装维护多个后端和旧实现**

事实：`write_raster_pdf` 默认生成 TeX 并调用 XeLaTeX，缺少命令时才调用 `write_raster_pdf_with_pymupdf`；`write_latex` 只转发，`write_latex_legacy` 没有仓库调用点。当前任务只是将已完成翻译的页面图片组装成 PDF。[E5]

| 选项 | 做法 | 收益 | 代价与适用条件 |
| --- | --- | --- | --- |
| **5A：只用 PyMuPDF 组装（推荐，用户条件已确认）** | 以现有 PyMuPDF 实现作为 `write_raster_pdf`；删除 TeX 生成、命令调用、fallback、legacy 和转发别名；清理不再使用的 job path 键和 README 依赖描述 | 删除一条环境分支和外部运行依赖，错误路径与测试更少 | 不再生成 `.tex`；保留 raster 模式并明确依赖 PyMuPDF；PDF 文件结构/压缩可能变化，不要求字节一致 |
| 5B：保留两个后端，只删除 legacy | 删除无调用旧实现和转发别名，仍保留 XeLaTeX→PyMuPDF 逻辑 | 改动较小，适合尚未确认 TeX 用途时 | 保留不必要依赖与两套测试；已确认不需要 TeX，当前收益不足 |
| 5C：PyMuPDF 负责输出，TeX 仅作为独立导出 | 主流程同 5A；按真实用户需求另行提供 TeX 导出 | 若需要人工编辑 TeX，输出可靠性不再依赖其编译 | 新增一种产品能力；用户已确认不需要，当前不实施 |

范围：`translate_pdf_via_codex.py` 的组装函数和调用者、`translate_pdf_parallel.py`、`README.md`、`tests/test_translate_pdf_parallel.py`。不顺带更换 raster 绘字算法、字体、背景采样或页面尺寸策略。历史工作文件不自动删除。

验收：N 张输入图生成 N 页，顺序与尺寸符合原约定，没有前后空白页；缺图失败；保留现有临时 PDF 成功后替换目标的行为；无 XeLaTeX 环境可运行。使用带不同标记的多页图片检查顺序、中心像素和页面尺寸，不比较 PDF 二进制哈希。

**建议实施顺序与交付边界**

| 阶段 | 选择 | 可独立验收的交付 | 依赖与停止条件 |
| --- | --- | --- | --- |
| 第一阶段 | 1A、5A，分别提交审查 | 单一内部 API/JSON 契约；单一栅格 PDF 组装实现 | 两项可独立实施，不要求并行代理；先记录测试基线 |
| 第二阶段 | 2A | 两个翻译入口复用一个批次执行实现 | 测试明确保留的组批与缓存差异，校验变化单独说明 |
| 第三阶段 | 4A | vector 绘制和 QA 消费同一最终计划 | 在删除二次构建前覆盖逐页尺寸、部分页及失败产物 |
| 第四阶段 | 3A | 明确布局阶段，逐项删除经验证的冗余 | 有最终计划对照后再减少修复；发现内容/几何退化则保留原步骤并记录原因 |

大文件拆分随责任迁移完成：批次执行归 `translation_batch.py`，PDF 组装可在清理后移入已有 `render_pdf.py`；布局代码只有依赖边界清楚时才移动到 `layout.py`。不以行数、文件数或“减少所有重复”为验收目标。与 CLI 共享的函数由真实所有者导出，测试直接 import 所属模块。

每项分别运行聚焦测试；代码变更提交前运行 AGENTS.md 规定的完整回归和编译检查，ownership 调整另加 `tests.test_ownership`。布局/绘制/visual-QA 调整必须运行可写出计划和 QA 报告的确定性样例，并检查错误数和产物路径。测试代码不依赖在线模型，也不将未跟踪缓存加入提交。

**附录**

**证据索引**

| 编号 | 已检查路径及稳定符号 | 支持的事实 |
| --- | --- | --- |
| E1 | `ownership.py`: `_validation_args`, `validate_ownership`, `_build_page_components_positional`, `build_page_components`; `translate_pdf_via_codex.py`: 四个 `*_compat`; `render_plan.py`: `render_plan_to_json`, `_ownership_*_to_json`; `tests/pdf_render_fixture_runner.py`: `_ownership_components` | 双重兼容、serializer 能力探测、重复 components 字段与仓库消费者 |
| E2 | `translate_pdf_via_codex.py`: `build_batches`, `translate_batches`, `main`, `postprocess_cross_page_sentence_splits`; `translate_pdf_parallel.py`: `build_page_batches`, `load_cached_translations`, `run_translation_batch`, `run_parallel_translation`, `translate_one_pdf`; `qa_semantic.py`: `run_backtranslation` | 批次执行重复和入口行为差异；回译仍是独立任务 |
| E3 | `translate_pdf_via_codex.py`: `build_page_render_plan`, `normalize_vector_text_layout`; `layout.py`: `split_translated_text_around_protected`, `expand_text_boxes_to_fit`, `rebalance_body_text_flows`, `validate_plan_text_fit` | 重复修复顺序、文本分配与 fit 的关联 |
| E4 | `translate_pdf_via_codex.py`: `write_vector_pdf`, `validate_document_quality`; `translate_pdf_parallel.py`: `run_qa_for_job`, `run_visual_qa_for_job`; `qa_visual.py`: `generate_visual_qa_report`, `render_pdf_pages_to_png` | 两次计划构建、不同 page_size 来源、visual QA 使用 artifact 和源页号 |
| E5 | `translate_pdf_via_codex.py`: `write_raster_pdf`, `write_raster_pdf_with_pymupdf`, `write_latex`, `write_latex_legacy`, `build_job_paths`; `tests/test_translate_pdf_parallel.py`: `RasterPdfAssemblyTests`; `README.md`: Requirements | 多后端、legacy 无仓库调用、当前栅格组装验收 |
| E6 | `AGENTS.md`; `tests/test_waitfree_regression.py`: 类级 `skipUnless`; 上一轮本地完整测试输出 | 验证要求、依赖工作缓存的跳过项、当前失败基线 |

**补充材料**

本轮只检查代码、调用点、历史记录并整理文档，没有重新运行测试或修改程序。上一轮回归在 `PYTHONPATH=vendor`、Python 3.12 下运行 413 项，363 通过、1 失败、49 跳过；默认 Python 3.14 缺 Pillow。失败项为 `GlobalStyleTests.test_raster_draw_block_uses_light_text_on_dark_background`；已确认背景为黑色、选择浅色文字，但像素比例断言失败，原因尚未定论。49 项跳过来自缺少 Wait-free 工作缓存。这些结果是已观察基线，不代表当前所有行为已获验证；实施前应刷新基线，并处理或隔离已确认的环境差异，不能修改断言掩盖退化。

5A 去掉 TeX 依赖不等于去掉 Poppler 或 OCR；两者用途不同。4A 使用同一计划也不意味着证明实际 PDF 所有像素正确：输出图片检查和计划几何检查仍是不同证据。

**待讨论事项**

目前没有阻止按推荐路线细化的需求未知项。1A/5A 的兼容条件已由用户确认。以下问题仅在改选 2B 时影响设计，不阻塞 2A：

- **问题：**统一整个文档流程后，串行入口应继续允许跨页组批，还是与并行入口统一按页组批？
- **已知证据：**`build_batches` 跨页累计字符；`build_page_batches` 每页清空批次；两者影响模型上下文和请求数量。
- **缺失证据：**用户对翻译上下文、调用数量与页级恢复粒度的优先级，以及允许改变的范围。
- **影响：**决定统一分批契约、批次产物和对应回归；只选择 2A 时沿用原行为。

- **问题：**统一整个文档流程后，单 PDF 入口是否应执行与批量入口相同的 QA 和跨页修复策略？
- **已知证据：**单 PDF `main` 会传入 model 生成边界修复；批量入口可运行完整 QA，其边界修复调用方式不同。
- **缺失证据：**用户是否接受单 PDF 的调用成本和默认输出验收行为变化。
- **影响：**决定共享文档入口的后处理与验收默认值；只选择 2A 时不统一这些策略。
