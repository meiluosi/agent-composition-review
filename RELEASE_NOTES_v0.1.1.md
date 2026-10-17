# ACR v0.1.1 — real-case calibration patch

v0.1.0 发布后，我们用 14 个真实 LangGraph GitHub issue + 3 个正常控制图做了一轮开发型试点。v0.1.1 是两个 P0 修复，以及这轮试点的公开记录。

## 修了什么

- **ACR001 跨函数解析**：现在能识别 helper 返回 compiled graph 的真实形态：
  - `parent.add_node("child", build_child())`
  - `child = build_child(); parent.add_node("child", child)`

  真实 issue #6446 的子图就是写在 `build_child()` 里返回的；v0.1.0 会漏报，v0.1.1 会报 ACR001（以及同一根因的 ACR002）。
- **reducer 限定符解包**：`ReadOnly[Annotated[list[str], operator.add]]`、`Required[...]`、`NotRequired[...]` 都会先解包再判断 reducer。修复了 `ReadOnly` 包住 reducer 时被 ACR002 误判为 “no reducer” 的问题。

## 真实案例试点

- 14 个真实 issue + 3 个正常控制图，盲标、离线扫描、逐例判定；
- v0.1.0 上：现有规则只能表达 1/14，唯一可表达案例因 helper 漏报；真实案例 finding 0 条相关、1 条误导；
- 控制图 0/3 误报；
- v0.1.1 复扫：`lg-6446` 命中，`lg-9311` 误导 finding 消失，控制图仍 0 findings；
- 其余 12 个案例仍是 GAP，多数是框架内部运行时 bug，静态构图规则不应硬覆盖。

报告：[evals/realworld/results/pilot-2026-10-17/REPORT.md](evals/realworld/results/pilot-2026-10-17/REPORT.md)

复扫记录：[evals/realworld/results/pilot-2026-10-17/POSTFIX.md](evals/realworld/results/pilot-2026-10-17/POSTFIX.md)

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.1.1"
pipx inject agent-composition-review langgraph   # acr demo 需要

acr --version
acr demo
```

或：

```sh
uv tool install "agent-composition-review[langgraph] @ git+https://github.com/meiluosi/agent-composition-review.git@v0.1.1"
```

## 验证

- `pytest -q`：41 passed
- `scripts/check_repo.py`：PASS
- `acr scan evals/realworld/cases/lg-6446`：ACR001 + ACR002
- `acr scan evals/realworld/cases/lg-9311`：0 findings
- 3 个 control：0 findings

## 边界

- 这次只修规则召回/误报，不改变“静态构图规则不覆盖框架内部运行时 bug”的边界。
- 真实案例试点是开发证据，不是保留集 benchmark；样本小，不宣称泛化。
- `acr trace` 未在真实案例试点中评估；resume/replay、parallel interrupt 等缺口仍待后续。
