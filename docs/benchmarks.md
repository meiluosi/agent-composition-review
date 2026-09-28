# 外部评测集与接入状态

核查日期：2026-09-29。Who&When 与 Who&When Pro text 已接入独立执行器，首轮方案为 24 个任务 × 两组 × 三次重复。数据版本、抽样排除、标签隔离和复现命令见 [实验说明](external-evaluation.md)；实际成绩见 [README](../README.md)。现有 10 项框架测试不计入外部基准成绩。

## 优先顺序

| 数据集 | 官方规模与任务 | 对本 Skill 的用途 | 接入条件 |
| --- | --- | --- | --- |
| [Who&When](https://huggingface.co/datasets/Kevin355/Who_and_When) | 184 条标注失败任务；定位责任 Agent 和关键步骤 | 首批诊断对照，规模适中，直接匹配排障目标 | 有公开数据与官方评测代码；核对标签口径、步号和数据使用条款 |
| [Who&When Pro](https://whowhenpro.github.io/) | 项目页报告 12,326 条注入失败轨迹，覆盖文本、图像、视频 | 扩展框架、任务和错误类型覆盖；优先文本子集 | 数据版本、统计口径和评测代码可用性存在待核对差异，见下文 |
| [AgentRx](https://www.microsoft.com/en-us/research/blog/systematic-debugging-for-ai-agents-introducing-the-agentrx-framework/) | 官方文章报告 115 条人工标注失败轨迹，定位首次不可恢复的关键失败 | 约束违例、工具接口与根因证据，适合作为补充验证 | [数据页](https://huggingface.co/datasets/microsoft/AgentRx)需要登录并接受共享联系方式的访问条件；本轮未代为接受 |

这是按当前 Skill 的用途作出的选择。故障归因基准测“是否找对失败原因”，不能单独证明代码修复、架构质量或生产可靠性；这些仍需现有运行回归和带业务不变量的修复验收。

## 本轮核查记录

### Who&When

[官方仓库](https://github.com/ag2ai/Agents_Failure_Attribution)与数据卡均报告 184 条任务，来自算法生成和人工设计的多智能体系统。127 是项目页报告的系统数量，不是任务数量。官方仓库含归因方法和评测入口。

Hugging Face 元数据返回 revision `59b9fcba1aaed7bbf206b5f4d3c68b8face2f49c`，`gated=false`。包含 Algorithm-Generated 与 Hand-Crafted 两组数据。已下载并校验全部 184 条 JSON，首轮从符合输入预算且排除开发案例的池中抽取 12 条；没有运行全量评测。代码仓库的 MIT 标识不能自动替代数据及其上游任务的使用条款。

### Who&When Pro

[项目页](https://whowhenpro.github.io/)报告 12,326 条轨迹、26 个来源基准，通过在成功轨迹中注入单个错误产生标签。该生成方式有利于明确注入位置，但不等于覆盖自然发生的多重故障。

[数据仓库](https://huggingface.co/datasets/Leoxx/whowhen_pro)元数据返回 revision `0bd196c8a040841c4ae167ab33cc8151de246f1f`，`gated=false`，数据卡标示 CC BY 4.0。当前数据服务返回 text 子集 6,257 行；服务总计显示 10,784 行，且 `partial=true`，因此不能把这个数解释为完整发布规模或断言缺失了某些样本。本轮已下载此 revision 的 text.jsonl，实际计数为 6,257 条且 ID 唯一；原始文件哈希保存在数据锁定记录。

数据卡列出 17 个错误模式，项目页写 18 个；官方链接的 GitHub 评测代码本轮浏览返回 404。本轮冻结该数据仓库的 17 类 taxonomy，独立实现严格位置与 Agent 集合评分，没有声称复现官方 harness。

数据卡说明 task、trajectory、ground_truth、extras 是 JSON 字符串。适配时必须解析后分离输入和标签；task 中也可能有参考答案。image_gui 明确采用从 1 开始的步号，其他子集需分别核对，不能统一猜测。

### AgentRx

官方文章报告 115 条轨迹及三个领域；当前 Hugging Face 数据卡仅列 tau_retail 和 magentic_one 两个领域。访问前不能确认受限文件是否覆盖文章所述全部数据。页面标示 CC BY 4.0，但这不取消下载入口的访问条件。

root_cause、failure_summary、step_reason 等含诊断答案，不能作为待测模型的输入。其“首次不可恢复失败”标签定义也不一定等价于其他数据集的“第一个错误”；应分别评分。

## 后续扩展与迭代

1. 首轮的适配器、输入上限、抽样组、随机种子及分析方案已经冻结。扩大规模应建立新的 manifest 和结果目录，不能覆盖本轮记录或只追加答对的样本。
2. 本轮结果用于设计改动后，这 24 个任务即成为开发证据。新旧 Skill 比较应另留未用于规则设计的任务，并避免同源任务组跨入两边。
3. 相同模型和执行条件下比较 Skill 版本与基线，按任务配对统计重复结果。保留无效输出与运行失败，不将同一任务的多次采样视作独立任务。
4. 首轮报告位置、Agent、联合准确率及 Pro 错误类别准确率。将来有足够类别覆盖时，再预先声明 macro-F1 等指标；不要事后挑选更好看的指标替换首要指标。
5. 另设正常轨迹与证据不足场景，检查误报和不确定性表达。仅有失败样本的数据集无法测出“对正常架构过度挑错”。

归因成绩与修复成绩分开：轨迹归因通过后，再从可以重建环境的案例中检验补丁，记录结果、副作用、权限、并发与状态隔离是否保持。日志题没有足够代码时，不能把一段修复建议算作已修复。

## 接入边界

公开仓库先维护来源、固定版本、适配脚本、样本 ID 清单和汇总结果；不要把大体积原始数据、受限数据、API 密钥或答案包直接放进 Skill 运行包。依据数据条款决定可分享的内容。

接入不改变日常 SKILL.md。模型加载 Skill 时不应自动读取基准答案。本轮使用已有 ChatGPT 登录的 Codex 额度执行模型对照；没有配置额外 API 密钥。AgentRx 仍未获访问权限，不计入运行数或成绩。
