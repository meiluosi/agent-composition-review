# ACR v0.3.1 — ACR106 false-positive fix and targeted trace validation

v0.3.1 是一次真实 trace 驱动的修正：公开 trace 暴露了 ACR106 的一个误报，修复后同时做了定向验证。

## 修复

- **ACR106 不再把被 interrupt 的 `node_end` 当成“已完成”**。真实公开 trace #3875 里，`branch_1` 在 resume 前是 interrupted，resume 后重启是预期行为；原实现会误报 ACR106。
- `TraceEvent` 增加 `interrupts` 字段；归一化时保留 `task_result.payload.interrupts`。
- 新增回归测试：被 interrupt 的节点不触发 ACR106；真正的 completed 节点仍会触发。

## 定向验证结果

- **ACR106**：
  - 正向：本地复现 #9276（langgraph 1.2.14）的 debug trace，`agent` 在 resume 前已完成、resume 后重跑，ACR106 命中；被 interrupt 的 `gate` 不报。
  - 负向：真实公开 trace #3875，修复后 0 条 ACR106；去掉 interrupts 字段会误报，证明修复必要。
  - 仍无社区直接附带的、带显式 resume/replay 标记的 trace。
- **ACR008**：7 条 cache identity 邻近案例，0 条 exact；**未验证**。
- **ACR009**：没有新的 `retry_policy` + 多子图 exact 样本；**no_sample_found**。
- **ACR010**：没有新的条件自环 + 非 reducer exact 样本；**no_sample_found**。

报告：[第三轮定向验证报告](evals/realworld3/results/pilot3-2026-10-18/REPORT.md)

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.3.1"
pipx inject agent-composition-review langgraph

acr --version
acr demo
```

## 已知限制

- ACR106 需要 trace 中有显式 `resume`/`replay` 标记；LangGraph 默认 debug 输出不产生这个标记，需要手动插入或适配器。
- 正向 trace 是本地复现公开 issue，不是 reporter 附件；版本固定。
- ACR008/ACR009/ACR010 仍无 exact 新样本，不能宣称已验证。
- 定向收集不估计真实世界流行率。
