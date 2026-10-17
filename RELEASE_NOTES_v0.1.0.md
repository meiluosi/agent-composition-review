# ACR v0.1.0 — Find the boundary where your LangGraph agent breaks

`acr` 是一个面向 LangGraph/LangChain 开发者的**组合故障诊断 CLI**：静态分析构图代码，分析失败轨迹，给出一页报告。它不调用模型、不需要 API key、不上传代码。

## 这个版本解决什么

单个节点跑得通，拼成图之后却报 `InvalidUpdateError`、字段被覆盖、工具被重复调用、缓存后结果“太稳定”——这类问题共同点是：**结构合法，组合后语义变了**。v0.1 把排查收敛到两类证据：

- 构图代码里的边界：子图输出越界、并行写同一状态键、retry/缓存包住副作用、节点内隐藏状态读写。
- 一次失败轨迹里的边界：重复工具调用、同一节点重复写、同一步并行写同键、错误后部分写入、最终状态丢键。

## 包含

- `acr scan`：ACR001–ACR006，基于 Python AST，输出文件行号、证据和最小修复建议。
- `acr trace`：ACR101–ACR105，支持 JSON 数组、JSONL 和 LangGraph debug stream，输出时间线与证据事件 ID。
- `acr report`：把 scan 与 trace 的 JSON 结果合并成一页 Markdown。
- `acr demo`：本地真实跑通一个 LangGraph #6446 类故障：broken → 捕获 `InvalidUpdateError` → scan 定位 → `output_schema` 最小修复 → 回归通过。
- `acr rules`：列出全部规则、严重度与文档。
- 运行时 Skill 入口：`skills/agent-composition-review/`，把同一流程写成 Agent 可执行步骤。
- 11 条规则各自有独立说明页：症状、为什么发生、输出示例、最小修复、误报边界。

## 安装

```sh
# 推荐：隔离安装（demo 需要 langgraph）
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"
pipx inject agent-composition-review langgraph

# 或者 uv
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.1.0"

# 或者源码
git clone https://github.com/meiluosi/agent-composition-review.git
cd agent-composition-review
python3 -m pip install -e ".[langgraph]"
```

验证：

```sh
acr --version
acr demo
```

## 快速看一眼

```text
$ acr scan examples/langgraph-subgraph-leak/broken.py
HIGH   ACR001  Compiled subgraph shares state keys with the parent graph
       examples/langgraph-subgraph-leak/broken.py:51
       Compiled subgraph registered as node 'child' has no output_schema,
       and child state 'State' shares 'a', 'b', 's1', 's2' with parent state 'State'.
```

修复只有一行：

```diff
- child = StateGraph(State)
+ child = StateGraph(State, output_schema=ChildOutput)
```

## 已知边界

- 只分析 Python 构图代码；动态构造可能漏报。
- 面向 LangGraph 1.x。
- 不是运行时追踪器，需要你提供轨迹文件。
- 不自动改代码；`fix` 是建议。
- 规则是启发式的，不确定项标成 medium/low，请结合证据人工确认。
- **v0.1 不宣称 benchmark 提升。** 旧评测档案保留在仓库 `docs/evaluation-history.md`，那是历史证据，不是本产品的效果承诺。

## 评测与证据

仓库继续保留 2026 年 9–10 月的评测档案：外部基准 144 次、开发集消融 96 次、机制检查 72 次、v4 执行修复 48 次、模型交互 100/144 次、自主代码修复 36 次。包括负结果、未完成记录和限制说明。它们测的是“模型读完 Skill 文本后的表现”，不是 `acr scan` / `acr trace` 的规则准确率，因此不能当作 v0.1 的产品效果证据。

## 许可证

MIT。详见 `LICENSE`。
