# 模型与指导交互：复核与复现

本轮检验当前 v4 指导对两个模型配置的增量，以及它是否超过普通工程清单。正式调用前的预注册提交为 [`e79931e4a0a3d9854b9cae92f556284ecdd853e9`](https://github.com/meiluosi/agent-composition-review/commit/e79931e4a0a3d9854b9cae92f556284ecdd853e9)。题目、验收、指导、执行器、分析代码、调度和预检查记录均已冻结；本说明不修改这些材料，也不预填正式成绩。

维护者应先读[分析方案](../evals/iterations/model-interaction-2026-10-04/ANALYSIS_PLAN.md)、[冻结前复核](../evals/iterations/model-interaction-2026-10-04/PRECHECK_REVIEW.md)和[清单](../evals/iterations/model-interaction-2026-10-04/manifest.json)。本轮最终按停止规则结束在 100 次尝试，44 项从未开始；97 次有效完成、96 次成功交付。逐题结果、差值与决策以[生成报告](../evals/iterations/model-interaction-2026-10-04/REPORT.md)及其有效性披露为准，不是完整 144 次研究。

## 比较对象

| 条件 | 各组共有 | 额外材料 |
| --- | --- | --- |
| A_tools | 业务要求、原始代码、公开 smoke、Python、两份结构/证据检查脚本和中立 TOOLS.md | 无 |
| B_checklist | 与 A 相同，工具字节一致 | 简短普通工程清单 |
| C_skill | 与 A 相同，工具字节一致 | 冻结的 v4 SKILL.md、按需参考及通用检索示例 |

共同提示只要求完成业务交付与简短 JSON 记录，不强制生成 `model.json`、声明不变量或遵循特定分析步骤。执行者可自行新增测试和工作记录。附加指导被明确加载，因此本轮不评估自动触发、Skill 发现或目录组织效果。C 包含通用检索示例；它属于整个指导包，不能从成绩中单独分离它、某个范畴论概念或指导长度的作用。

模型为 `gpt-6-astra/high` 和 `gpt-6-luna/high`，通过已有 Codex 登录调用。请求别名不提供精确后端权重快照；两个模型的 `high` 也不代表相同推理算力。Luna 是否在这些任务上表现较弱，需要观察它自己的基线，不能仅凭产品定位预设。

四个家族分别为租约任务队列、检索缓存、分块事件流和工具结果合并。每家族有组合缺陷、正常实现、节点内部缺陷三个变体，共 12 题。同一家族三变体的题面和公开 smoke 相同；执行者看不到变体标签、正常参考、隐藏验收或 mutation 审计。

## 冻结和调用边界

正式上限是 **12 题 × 2 模型 × 3 条件 × 2 重复 = 144 次**，每个模型/条件单元计划 24 次。另有两个独立加法修复 canary，每模型一次，不参与效果统计，也没有模型裁判调用。两个 canary 的原始记录保持不变；它们发生在 Python helper 增加 `-S` 等本地环境收紧之前，随后仅离线重验既有源码。具体差异见冻结前复核记录。

清单记录 Python 3.12.14、codex-cli 0.160.0、Darwin、随机种子 `202610041`、三路并发、单次模型调用 360 秒以及后置验收 30 秒。固定种子交错任务及模型/条件，只固定调度，不固定模型采样。每次使用新临时目录与 ephemeral 会话，调用前先写开始标记。

所有组均禁用宿主 Skill 发现、插件、网络检索及多 Agent。只有原始业务文件 `app.py` 可修改；任务、原始副本、smoke、helper 和提供的工具/参考受保护。新增工作文件不进入独立业务验收。最终 `app.py` 必须自包含且只用标准库。

首批最多六次用于检查执行完整性，不根据成绩调整题目、模型、指导或评分。后续仅继续冻结计划中尚未开始的条目。答错、无效格式和任务超时均保留且不重试。额度、鉴权、传输、服务或隔离故障触发停止新增调度，已在运行的调用收完；重启执行器不能绕过既有基础设施失败。未开始项记为 pending，不当作模型答错。

已经存在的开始记录、完成记录或公开产物都不能通过删除本地 raw 日志来换取重跑。若需恢复环境或开展新实验，另行登记范围、原因、软件版本和新记录，不能覆盖此轮失败或解除冻结。

原执行器累计记录 64 次后，因把 shell snapshot 文件名中的 `401` 误识别为鉴权状态而停止。该次仍是未有效交付的超时尝试，原记录和原分析器不改。审计确认没有真实鉴权错误后，另行冻结[恢复登记](../evals/iterations/model-interaction-2026-10-04/RECOVERY_PLAN.md)及 `classifier-recovery-manifest.json`，只允许执行原调度中 80 个从未开始的条目。新执行器仅修正基础设施错误分类，保持原输入、验收、模型设置和总计 144 次上限；任何新真实基础设施失败仍停止调度。恢复代码、规则和 64 份旧记录先提交推送，再进行恢复调用。它属于有披露的软件修订，不能描述为未中断的单阶段实验。

恢复预注册提交为 [`48ff68b0aba6989ea18b4af6949f7f403f99dd0a`](https://github.com/meiluosi/agent-composition-review/commit/48ff68b0aba6989ea18b4af6949f7f403f99dd0a)。恢复新增 36 次后，一次执行超时伴随模型目录刷新及遥测请求错误，按停止规则结束。机器 transport 分类保留；辅助服务错误不证明推理通道故障或超时原因。没有第三次恢复，剩余 44 项不再在本轮执行；完整性审计核验全部 100 份公开记录及本机原始日志。具体证据见[执行审计](../evals/iterations/model-interaction-2026-10-04/EXECUTION_REVIEW.md)。

## 独立验收和记录

后置验收只把提交的 `app.py` 与维护者 `acceptance.py` 放到独立目录，在新的 Python `-S` 进程运行。模型自行新增的测试不会替代隐藏验收，也不会把隐藏失败反馈给同一次执行者。判定要求所有预定检查名称恰好出现一次、数量相符、全部通过、没有跳过、进程正常退出且没有超时。加载异常、遗漏检查和伪造一个总通过字段不能满足这些要求。

macOS 后置验收使用禁止网络、仅允许临时验收目录写入的 sandbox；沙箱建立失败显式记录，不静默降级。Linux 复现采用进程和超时隔离。两者都不是针对恶意执行者的完整文件读取隔离证明；复验前应审阅被执行源码和命令审计。

迭代目录中的材料分工如下：

| 路径 | 用途 |
| --- | --- |
| `manifest.json`、`cases.json`、`snapshots/` | 冻结任务、条件、提示哈希、文件哈希和调度 |
| `RECOVERY_PLAN.md`、`classifier-recovery-manifest.json` | 单独冻结的分类器修订、原 64 份证据及剩余 80 项调度；不覆盖原清单 |
| `preflight/` | 与正式成绩分开的 canary 记录和源码 |
| `results/<job digest>/started.json`、`record.json` | 尝试身份、状态、用量、提示/任务哈希、工作区与命令审计、验收结果 |
| `results/<job digest>/artifacts/`、`patch.diff` | 保留的文本产物及相对原始实现的改动 |
| `raw-runs/` | 本机保留并被 Git 忽略的原始事件、stderr 和调用输出 |
| `summary.json`、`REPORT.md`、仓库 `assets/model-interaction-*` | 从保存产物重新验收后生成的汇总、报告及图表 |

公开记录关联原始事件与 stderr 的 SHA-256；持有本机 raw 日志的维护者可以核验对应关系。只有哈希不能让其他读者检查未公开日志的内容，也不是服务端签名证明。产物文件的哈希用于发现修改；命令标记需要回读，不能仅凭没有标记就宣称完全没有越界。公开文本产物限制类型及单文件大小，符号链接和受保护文件变更会使交付失效。

独立完整性审计器只读取记录与哈希，不执行产物、不重评分、不调用模型。当前停止结果使用以下命令；`--require-complete` 要求 144 项齐全，在本轮应当失败，不用于伪装完成。审计器区分未开始、已开始未发布、发布未完成与有效公共记录；开始文件本身不证明进程仍在运行。没有本机 raw 的读者仍可验证公共产物，但输出会明确显示原始日志未核验。不要把这份完整性报告理解为 Skill 效果或正确性证明。

```sh
.venv/bin/python scripts/audit_model_interaction_results.py
.venv/bin/python scripts/resume_model_interaction.py verify --offline
```

原运行机器的审计保存为迭代目录中的 `integrity-audit.json`，包含每份公共记录的哈希和本机日志核验情况。该文件与冻结评分分开；后续机器复核不会默认覆盖它。CI 将新的公共审计与保存的最终快照比较，核验 100 份记录的身份、开始标记及哈希和 44 项未开始状态；不要求其他机器持有本机 raw 日志，也不把不同机器的缺失 raw 误记成已经核验。

## 不调用模型的复现

在包含已发布结果的本项目仓库根目录操作，使用 Python 3.12 环境。业务任务只需标准库；生成图表使用 `requirements-benchmark.txt` 中固定的绘图库。仓库全套其他历史实验另有 `requirements-eval.txt`。以下命令不需要 Codex 登录或模型额度，也不会补跑缺失任务。

先核对冻结字节。离线复核跳过当前 CLI/Python/平台与历史运行机器的精确版本比较，仍验证全部冻结文件、提示和调度：

```sh
.venv/bin/python -B - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from run_model_interaction import verify
manifest, cases = verify(check_cli=False)
print(len(cases), 'cases;', len(manifest['schedule']), 'planned attempts; frozen material verified')
PY
```

复验夹具、调度与统计边界，并重放冻结前的正负对照：

```sh
.venv/bin/python -m unittest evals.model_interaction.test_fixtures evals.model_interaction.test_protocol evals.model_interaction.test_analysis -v
.venv/bin/python -S evals/model_interaction/review_lease_queue.py
.venv/bin/python -S evals/model_interaction/tasks/lease_queue/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/retrieval_cache/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/stream_events/mutation_audit.py
.venv/bin/python -S evals/model_interaction/tasks/tool_results/mutation_audit.py
```

从公开源码重新评分并生成报告和图表：

```sh
.venv/bin/python scripts/analyze_model_interaction.py
.venv/bin/python scripts/render_model_interaction_report.py
```

分析器先校验每份产物的身份、提示、源码哈希及有效完成条件，再执行保存的源码，并与原记录的行为签名比较。签名包含检查名称、通过状态、数量、退出和超时等字段，不比较平台相关的完整异常文字。若重验不一致，应调查和披露环境或判据问题，不能直接改原分数使其通过。该命令会更新生成的汇总、报告和图片；`--no-regrade` 只适合预览，不替代最终复验。

第二条命令补充冻结后发现的 m02/m08 正常标签缺陷、域歧义、分类器修订和最终停止说明，不改变原始分数或分析代码。这些反例使正常误报率和版本升级门槛不能按字面解释；具体证据见[执行审计](../evals/iterations/model-interaction-2026-10-04/EXECUTION_REVIEW.md)。不得为了去掉披露而仅发布第一条命令生成的报告。

在原始执行环境中，`run_model_interaction.py verify` 还检查冻结的 CLI、Python 和平台版本。原执行器的 `canary`、`freeze`、`run` 和恢复执行器的 `freeze`、`run` 是模型执行的管理入口，不属于上述离线复现；不要为重新生成图表调用它们，更不能删除记录后重建实验。恢复执行器的 `verify --offline` 只核验公开冻结字节；`freeze`、`run --limit 3`、`run` 还必须核验原机器 raw 日志和版本，不能在别处绕过证据要求继续调用。

## 指标和事先决策

首指标是“执行有效且全部隐藏业务验收通过”。最终源码的行为通过率另列：中断时留下的代码即使通过，也不能提升成成功交付。所有已尝试项进入已尝试分母，同时报告计划分母、pending 和基础设施失败。正常例区分行为回归、行为无法评价、误报、不确定、缺失结论及源码改动；缺失源码不等于发生了业务回归，修改源码本身也不等于误改。

差值预先指定为各模型的 C−A、C−B、B−A，以及 `(Luna C−A)−(Astra C−A)`。报告全部已尝试的原始差值，并另用六个模型/条件单元都有记录的 case×repeat 配对块计算比较：先平均同题重复，再在家族内平均，最后对四家族等权。缺失家族配对时不形成完整汇总差值。四家族的 256 种重采样只展示敏感性，不是可靠总体置信区间；重复和相邻变体不能当作独立样本。

只有 144 次全部有可解释记录且无基础设施/隔离问题，才应用以下开发筛查门槛：

1. 当前 Skill 在某模型上 C−A 至少 `3/24`（12.5 个百分点），C−B 至少 `2/24`（约 8.33 个百分点），且 C−A 在至少两个家族为正，同时正常保护各项不劣于 A、B，才进入另行登记的保留集确认候选。
2. 普通清单达到相同的对 A 收益、跨家族和正常保护门槛，而 C 未显示额外实用优势时，优先验证简短清单方向。未过门槛不证明两种指导等效。
3. 两模型基线都达到至少 22/24 时，结束本轮并记录验收饱和；不追加更难题来改写本轮，也不据此判定 Skill 无效。
4. 没有条件达到门槛时，停止当前指导版本的追加调用，不继续增加理论说明以追求正分数。样本有限时结论是没有取得所需证据，不是证明零效应。

这些门槛用于选择下一步投入，不是显著性检验。通过门槛也不自动升级默认 Skill。确认性保留集需要新的任务与方案；本轮已见任务不能改名成为保留集，新的模型调用也不自动包含在 144 次授权范围中。

## 解释范围

四家族均为代理编写、在正式运行前交叉复核的开发夹具，不是外部专业数据集、生产系统或人类专家标注。正常参考和 mutation 检出只证明有限输入与指定错误上的表现。范围约束以题面为准，包括租约的时间域、缓存的顺序调用和 payload 深度、事件流的 LF JSON 协议等。

本轮固定共同工具，测量附加指导包的效果，不能判断工具自身是否有用，也不能把条件差值归因于范畴论整体。一个正交互只描述这两个请求模型及各自 high 配置；不能推出模型越弱越受益。累计图按固定任务编号组织，不是训练曲线。

输入、缓存输入和输出 token 分别记录；缓存输入已经包含在总输入中。超时、缺失用量、服务端缓存与并发都会影响成本解释。Codex 登录调用没有可核验的单次货币账单，因此不把 token、API 标价或墙钟时间换算成“实际节省金额”。
