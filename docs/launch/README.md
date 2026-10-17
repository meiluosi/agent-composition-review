# v0.1 公开发布手册

本文件是 `agent-composition-review` v0.1.0 公开发布的操作清单与渠道文案。它面向维护者，不是产品承诺；发布范围、日期和指标以实际执行为准。

## 0. 发布定位（所有文案共用）

- **一句话**：Find the boundary where your LangGraph agent breaks.
- **产品**：`acr` CLI，静态分析 LangGraph 构图代码、分析失败轨迹、生成一页报告。
- **许可**：MIT。
- **v0.1 能兑现的**：`acr scan` / `acr trace` / `acr report` / `acr demo` / `acr rules` 五条命令；ACR001–ACR006 静态规则、ACR101–ACR105 轨迹规则；可选 `[langgraph]` 依赖；Python 3.10+。
- **v0.1 不宣称的**：不宣称 benchmark 提升或检测准确率；不是运行时追踪器；不自动改代码；只覆盖 Python AST 与 LangGraph 1.x。旧评测历史见 [评测档案](../evaluation-history.md)，不得作为产品效果证据引用。

## 1. 发布前检查清单

### 仓库与法律

- [ ] 仓库已设为 public，确认描述与主页 URL 正确：`https://github.com/meiluosi/agent-composition-review`
- [ ] `LICENSE` 为 MIT，版权行正确；README 许可段落指向 `LICENSE`
- [ ] 扫描历史提交与工作树：无 API key、无私有轨迹、无内部数据；**新增文件**无本机绝对路径。历史评测 JSON 中 3 个 2026-09 的 code-repair record 含运行器临时路径，已在评测档案说明且不含密钥，不为本条修改历史证据文件
- [ ] `SECURITY.md` 可用；README 告知漏洞报告方式
- [ ] `CONTRIBUTING.md` 与 issue 模板存在，且要求附最小复现与版本

### 版本与发布物

- [ ] `VERSION` = `0.1.0`；`pyproject.toml` 版本与 tag 一致
- [ ] 构建 sdist/wheel 成功：`python -m build`
- [ ] 打 tag `v0.1.0`，创建 GitHub Release，上传 wheel、sdist、skill zip、`checksums.txt`
- [ ] Release 说明按 [第 2 节 GitHub 文案](#github-release-说明) 撰写，包含已知限制
- [ ] `CHANGELOG.md` 的 0.1.0 条目可读，且明确“不宣称 benchmark 提升”

### 安装与 demo 验证

在干净环境逐条跑通，记录实际输出（不要用预期输出代替）：

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"
pipx inject agent-composition-review "langgraph>=1.0,<2"   # acr demo 需要
acr --version
acr rules
acr demo

# 第二入口 uv
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"

# pip 与源码
python3 -m pip install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"
git clone https://github.com/meiluosi/agent-composition-review.git
cd agent-composition-review && python3 -m pip install -e ".[langgraph]"
```

- [ ] 四条安装路径都在 macOS/Linux 上验证，记录失败与回退命令
- [ ] `acr demo` 真实跑通，终端输出已保存为 README 示例与 GIF 源
- [ ] `acr scan examples/langgraph-subgraph-leak/broken.py` 能找到 ACR001；修复版通过
- [ ] `acr scan . --fail-on high` 在仓库自身代码上给出可解释结果，无误报阻塞

### 页面与物料

- [ ] README 顶部徽章：`https://github.com/meiluosi/agent-composition-review/actions/workflows/package.yml/badge.svg`
- [ ] README 含 demo 输出/GIF、安装、命令表、规则表、限制、评测档案链接
- [ ] `docs/rules/ACR001.md` … `ACR105.md` 可独立打开，链接有效
- [ ] GitHub topics 建议：`langgraph` `langchain` `ai-agents` `debugging` `static-analysis` `developer-tools` `python`
- [ ] About 简介一句话；社交预览图（1200×630）含 `acr` 与一句定位
- [ ] 运行 `python3 scripts/check_repo.py`，所有 Markdown 相对链接有效

## 2. 渠道文案

### GitHub Release 说明

```markdown
## ACR v0.1.0 — Find the boundary where your LangGraph agent breaks

`acr` 是一个面向 LangGraph/LangChain 开发者的组合故障诊断 CLI：静态分析构图代码，分析失败轨迹，给出一页报告。

### 包含

- `acr scan`：ACR001–ACR006，覆盖子图输出越界、并行写同键、retry/缓存副作用、隐藏状态读写
- `acr trace`：ACR101–ACR105，覆盖重复工具调用、节点重复写、同一步并行写同键、错误后部分写入、最终状态丢键
- `acr demo`：内置 LangGraph #6446 类示例，broken → scan → 修复 → 通过
- `acr report`：合并 scan/trace 结果为一页 Markdown
- 可选依赖 `[langgraph]`，Python 3.10+

### 安装

pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"
pipx inject agent-composition-review "langgraph>=1.0,<2"
acr demo

### 已知限制

Python AST 静态分析，面向 LangGraph 1.x；不是运行时追踪器；不自动改代码。v0.1 不宣称 benchmark 提升；早期评测记录见 docs/evaluation-history.md。

MIT License.
```

### LangChain Forum（English）

> **Title:** ACR v0.1 — a small CLI for finding LangGraph composition bugs (subgraph output, parallel writes, retries)
>
> We just published `acr`, an MIT-licensed diagnostic CLI for LangGraph/LangChain agents. It targets the class of bugs where each node works in isolation but composition fails:
>
> - subgraphs that return full state and clobber a parent key → `InvalidUpdateError`
> - parallel branches writing the same state key without a reducer
> - retries/recovery duplicating side effects, caches changing sampling semantics
> - final state silently dropping keys after merges
>
> `acr scan` does static AST checks on graph code; `acr trace` analyzes a failed run (JSON/JSONL/debug stream) and points to evidence event IDs. `acr demo` reproduces a LangGraph #6446-style failure, scans it, applies the minimal `output_schema` fix, and verifies it.
>
> Install: `pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"`, then `pipx inject agent-composition-review "langgraph>=1.0,<2"` (the demo needs LangGraph), then `acr demo`.
>
> Limitations: Python AST + LangGraph 1.x, not a runtime tracer, does not edit code. v0.1 is a diagnostic aid and does not claim benchmark improvements. Feedback and minimal reproductions are very welcome: https://github.com/meiluosi/agent-composition-review/issues

### Reddit r/LangChain（English）

> **Title:** I built a CLI for the "works alone, breaks when composed" LangGraph bugs
>
> I kept hitting failures that only show up after composing graphs: a subgraph returns its full state and overwrites a parent key; two branches write the same key; a retry fires a tool twice. Debugging these by reading logs is slow, so I packaged the checks into a small CLI.
>
> `acr scan` statically checks graph construction (subgraph output boundaries, missing reducers, retry/cache around side effects). `acr trace` reads a failed run and reports duplicate tool calls, same-step parallel writes, partial writes after errors, and dropped keys, with event IDs. `acr demo` runs a LangGraph #6446-style broken example end to end.
>
> Install (the demo needs the optional LangGraph dependency): `pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0" && pipx inject agent-composition-review "langgraph>=1.0,<2"`, then `acr demo`.
>
> MIT, Python 3.10+, LangGraph 1.x. It's v0.1: static analysis only, not a runtime tracer, and it does not auto-fix code. Not claiming any benchmark result. Honest feedback welcome, especially false positives.

### LangChain Discord（short, English）

> **ACR v0.1 — LangGraph composition diagnostics (MIT)**
> Sharing a small CLI for composition bugs: subgraph output clobbering parent state (`InvalidUpdateError`), parallel same-key writes, retried side effects, cache/sampling mismatches. `acr scan` = static AST checks; `acr trace` = failed-run timeline with event IDs; `acr demo` = #6446-style broken → scan → `output_schema` fix.
> `pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"` + `pipx inject agent-composition-review "langgraph>=1.0,<2"` (demo needs LangGraph) → `acr demo`.
> v0.1 limitations: Python AST + LangGraph 1.x, no auto-fix, no benchmark claims. Happy to look at minimal reproductions; false-positive reports are the most useful feedback right now.

### Hacker News — Show HN（English）

> **Title:** Show HN: ACR — find where a LangGraph agent breaks when composed
>
> I built `acr`, an MIT-licensed CLI for LangGraph/LangChain composition failures: cases where each graph works alone but the combination raises `InvalidUpdateError`, a subgraph overwrites a parent state key, parallel branches collide, or a retry duplicates a tool side effect.
>
> Two entry points: `acr scan` analyzes graph-construction code with Python's AST; `acr trace` reads a failed run and points to the events where a key was written twice, a tool was called repeatedly, or state went missing. `acr demo` reproduces a #6446-style failure and shows the one-line `output_schema` fix.
>
> It is deliberately narrow: Python, LangGraph 1.x, static + trace analysis, no runtime instrumentation, no auto-rewrite, no benchmark claims. I'd value bug reports with minimal reproductions and, especially, false positives.
>
> Install: `pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"`, then `pipx inject agent-composition-review "langgraph>=1.0,<2"` (LangGraph is needed for the demo; scan/trace don't need it).
>
> https://github.com/meiluosi/agent-composition-review

### 发布后 7 天

- [ ] 论坛/Reddit/Discord 的每个问题在 48 小时内至少回复一次（即使只是确认收到）
- [ ] 收集误报，按规则 ID 归档；不急于为单个反馈改规则
- [ ] 检查安装相关问题，补 FAQ 或修 install 脚本
- [ ] 记录真实使用案例（可脱敏）用于 README 的“真实案例”或 discussion

## 3. 设计伙伴邀请模板（5 个场景）

共同原则：先说明来意与边界，再请求**具体**帮助；一次只问一个小任务（跑 demo、跑 scan、看一份报告）；不承诺付费或生产支持；获得许可后再引用其反馈。

### 模板 1：在 issue/forum 里被 `InvalidUpdateError` 卡住的人

> 看到你在 `<issue/帖子>` 里遇到父子图并行的 `InvalidUpdateError`。我们在做 ACR——一个 MIT 的 LangGraph 组合故障诊断 CLI，v0.1 里有一条规则专门查“子图未声明 output_schema 导致父图键被覆盖”。如果你愿意，我可以把最小复现跑一遍 `acr scan`，把 finding 和修复建议发回这个 thread，你判断是否有用。不需要你安装任何东西，也不要求你采纳修复。

### 模板 2：多智能体合并丢字段的团队

> 你们的多智能体流程在合并结果时会丢字段吗？我们在找 3–5 个真实场景验证 `acr trace` 对“最终状态丢键 / 同一步并行写同键”的提示是否准确。你提供一份脱敏的运行轨迹（JSON/JSONL 或 debug stream）和最终状态即可；我回一份一页诊断报告，包含证据事件与最小修复建议。我们不会公开或复用你的数据，除非你明确同意。

### 模板 3：重试或缓存导致重复副作用的生产团队

> ACR v0.1 有针对 retry/缓存叠加副作用的静态检查（ACR004/ACR005）。想邀请你做设计伙伴：用你们的脱敏代码跑一次 `acr scan`，看看误报率如何。你只需要贴出 finding 是否命中真实问题；如果误报，我会记录为规则改进输入。不会要求你改代码或接入生产。

### 模板 4：正在用 LangGraph 搭内部工作流、想要可解释诊断的团队

> 我们在找内部 LangGraph 工作流的真实构图代码做早期验证。ACR 是本地运行的 CLI，不上传代码；`acr scan` 只读文件做 AST 分析。希望了解：你们的构图里有没有“节点单独正常、组合后异常”的历史问题？愿意用 20 分钟一起跑一次 scan，并告诉我哪些 finding 有用、哪些是噪音吗？

### 模板 5：写 LangGraph 教程/课程的内容创作者

> 我们在公开 ACR v0.1（MIT，LangGraph 组合故障诊断 CLI）。如果你在做 LangGraph 教程，愿意用 `acr demo` 的 #6446 案例做一个公开 walkthrough 吗？我们会提供命令、真实输出和规则页链接，你保留完全编辑权，也可以指出我们哪里讲错了。目标是让读者能复现“broken → scan → 最小修复 → 测试通过”，而不是宣传工具。

## 4. 前 30 天指标

v0.1 **不内置遥测、不上报任何使用数据**。以下指标全部来自 GitHub 与用户主动反馈，能看见的是代理指标，不是精确安装量；目标值是内部参考，不是对外承诺。

| 指标 | 定义与数据来源 | 30 天参考目标 | 说明 |
| --- | --- | ---: | --- |
| 安装数（代理） | GitHub Release 资产下载数 + `pipx`/`uv` 安装反馈 + 安装 issue 数 | 50–100 次下载 | 从 git 安装无法精确统计，不把下载数当用户数 |
| 首次 demo 完成 | discussion/issue 中主动报告“`acr demo` 跑通”的人数；demo 相关 issue | 10 | 失败也计入反馈；记录失败环境 |
| scan 使用 | issue/discussion 中带 `acr scan` 输出的反馈数 | 5 | 优先看是否附 finding 与真实代码结构 |
| issue 质量 | 附最小复现 + 版本 + 规则 ID 的 issue 占比 | ≥ 60% | 低于目标就改进 issue 模板，不追数量 |
| 真实修复案例 | 用户确认“按 finding 修复并通过回归测试”的案例数 | 3 | 每条需有 before/after 测试证据，可脱敏 |
| 误报质量 | 报告为误报的 finding 数 / 总反馈 finding 数 | 持续记录，不设硬指标 | 误报处理优先于新增规则 |

辅助观察（不作为成功判据）：stars、forks、docs 页面流量、渠道帖子回复质量。**不要**用单一 star 数或下载量对外宣称“被广泛采用”。

## 5. 风险与回退

- **安装失败是首要风险**：准备好 `python -m pip install -e ".[langgraph]"` 的回退命令；在 README 与 Release 中并列展示，不隐藏失败。
- **误报损害信任**：v0.1 宁可把不确定的 finding 标为 medium/low（需要人工确认），也不要为了“规则看起来强”而升级 severity；公开说明误报边界。
- **不要夸大**：所有渠道文案都保留“v0.1 限制”和“不宣称 benchmark 提升”；旧评测档案只作为历史证据，不放进发布主叙事。
- **数据安全**：不接收含密钥/生产轨迹的公开粘贴；邀请模板里明确脱敏与授权要求。

---

相关：[README](../../README.md) · [评测档案](../evaluation-history.md) · [ACR001 示例页](../rules/ACR001.md) · [贡献指南](../../CONTRIBUTING.md)
