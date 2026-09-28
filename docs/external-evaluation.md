# 外部评测的运行与复核

首轮实验定义见 [冻结清单](../evals/external/pilot-manifest.json) 和 [分析方案](../evals/external/ANALYSIS_PLAN.md)。这个目录与日常 Skill 的运行包分开，使用者安装 Skill 不会获得评测答案或下载完整数据。

## 本轮覆盖

已下载固定版本的 Who&When 184 条任务及 Who&When Pro text 6,257 条轨迹。正式选取各 12 个任务组，每组 3 次独立运行，比较加载完整 Skill 与不加载 Skill，共 144 次计划运行。

Who&When 样本在算法生成与人工设计的系统之间均衡；Pro 样本覆盖七个已验证位置映射的框架。采用固定随机种子，不根据模型是否答对选择样本。这里按同源任务分组，不声称完成按失效家族保留的泛化验证。首轮检查过的开发样本及同一任务组从抽样池排除。

Pro 的 macnet 位置映射尚未验证，261 条不进入候选池；另有 77 条在本适配器中无法将金标准位置对应到展示的轨迹位置。这些属于适配覆盖限制，不是模型答错，也不据此认定原始数据错误。输入超过 120,000 个字符的案例未纳入试验；不截断长轨迹后仍套用原标签。完整排除计数在 [数据锁定记录](../evals/external/sources.lock.json)。

AgentRx 的下载入口要求登录并接受访问条件。本机既没有 HF_TOKEN，也没有已缓存的 Hugging Face 登录凭据，本轮未代为接受条款或共享联系方式。其状态记录为 blocked_access，没有测试分数；后续获得授权后还需核对实际轨迹与标注的关联方式。

## 标签如何隔离

Who&When 的 question、history、system_prompt 可进入输入；groundtruth / ground_truth、mistake_agent、mistake_step 和 mistake_reason 不进入模型提示词，评分器只提取需要的归因标签。步骤使用显式的零基 history:N 编号。人工设计系统的 Agent 名称在 role 字段，算法生成系统还使用 name；两者均保留。结构复核结果及一次审计误报的更正见 [输入审计](../evals/external/input-audit.json)。

Pro 只使用 task.query 与清理后的 trajectory；不把 task.answer、ground_truth 或 extras 交给模型。is_injected、is_replayed、injection_via 及相应注入元数据在嵌套轮次中递归移除。工具观察里合法的 answer 字段仍保留，不能为了去标签而删掉真实证据。

位置按框架转换为 step:N、stage:N、round:N 或 round:R/position:P，并在输入中明确展示。归因标签和错误类别只保留在本地独立 oracle 文件中。没有 Agent 标签的任务不参与 Agent 准确率；联合指标在这些任务上只要求位置正确。图中 n 是各指标适用的任务数。严格比较 Agent 集合和位置，不使用上游脚本的子字符串包含评分，因此不能将本轮分数与其官方榜单直接横向比较。

## 执行环境

本轮请求 gpt-6-astra、high reasoning，Codex CLI 0.158.0-alpha.2.1，经已有 ChatGPT 登录执行。每次单独启动 ephemeral 会话，在新的临时目录中运行，忽略用户配置，禁用宿主 Skill 发现、Skill 搜索、Shell、连接器和浏览等工具。模型只接收冻结输入；Skill 组额外接收冻结指令与三个参考文件的完整内容。

skip_host_skill_discovery 是该 CLI 版本的开发中特性，因此必须保留版本及参数记录，不假定所有 CLI 版本有相同隔离行为。任何实际工具调用会被记录为隔离违规并计失败。服务端模型快照及货币费用未由该执行器提供；记录请求别名、真实 token、缓存 token 和墙钟时间，不猜测金额。

网络使用当前系统已有代理，不修改全局代理或认证配置。首次无代理的连通性探测曾超时，使用已有系统代理后连通；探测不计入 144 次正式测试。

## 重现

需要 Python 3.12、上述 Codex CLI 版本及有效 Codex 登录。运行模型会消耗该登录的使用额度，CI 不自动执行它。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-benchmark.txt
.venv/bin/python -m unittest evals.external.test_core evals.external.test_analysis -v
.venv/bin/python scripts/prepare_external_eval.py --restore-inputs
.venv/bin/python scripts/run_external_eval.py
.venv/bin/python scripts/summarize_external_eval.py
.venv/bin/python scripts/plot_external_eval.py
.venv/bin/python scripts/update_benchmark_readme.py
```

restore-inputs 按固定 revision 下载数据，对全部源文件、所选输入、答案、提示词及 Skill 快照逐项校验摘要，不重新抽样。Skill 快照位于 evals/external/skill-snapshot；本地 data、private 和 raw-runs 目录被 Git 忽略。原始输出保留于本地，公开表保留预测、评分、元数据和原始记录摘要。模型再次运行可能得到不同结果，应保留为新的实验记录，不能把新结果冒充本轮原始输出。

仅重画已公布曲线时不需要模型登录或下载原始数据：安装绘图依赖后执行 plot_external_eval.py。它先检查公开预测表的摘要，再重新聚合评分，并核对 summary，随后生成 PNG 和 SVG。没有完整运行记录时 summarize_external_eval.py 会拒绝生成成绩。

## 解释结果

每个数据集只有 12 个任务，三次重复不能将它变成 36 个独立任务。区间按任务聚类重采样；比较差值时保持两组的任务配对。若所有任务的配对平均差都为零，经验 bootstrap 会退化为 [0, 0]；这不证明未来任务上无差异，只说明本次样本无法提供差值变动的信息。累计曲线按提前冻结的任务顺序绘制，表示估计随观察量变化，不表示训练进步。

本轮不调优 Skill，也不按中途成绩提前停止。样本规模、分层抽样、公开数据可能被预训练见过、位置标注的不确定性，以及没有正常轨迹，均限制结论。即使某组分数较高，也不能直接声称在生产系统、架构审查或代码修复上更有效。

## 来源

- [Who&When 数据集](https://huggingface.co/datasets/Kevin355/Who_and_When)，[研究与代码](https://github.com/ag2ai/Agents_Failure_Attribution)。本地核查的评测代码提交为 f4d2b6da464a826580e59b3a0eae15ea2d642d7c；本项目独立实现更严格的评分器，没有将原始轨迹重新发布。
- [Who&When Pro 数据集](https://huggingface.co/datasets/Leoxx/whowhen_pro)，[项目页](https://whowhenpro.github.io/)。数据卡标示 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本实验解析数据字段、移除注入标记、规范化位置并抽样；未改动源文件，源数据哈希与引用保存在锁定记录中。
- [AgentRx 数据页](https://huggingface.co/datasets/microsoft/AgentRx)。本轮仅记录访问条件，没有加载其受限数据。
