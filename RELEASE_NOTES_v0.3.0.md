# ACR v0.3.0 — retry self-loop and resume/replay checks

v0.3.0 按第二轮验证报告的建议，补了一个静态规则和一个 trace 规则，并给 ACR008/ACR009 补了更多正负 fixture。规则总数：16（scan 10 + trace 6）。

## 新增

- **ACR010 · retry 自环写非 reducer 键**：节点有条件边回到自己，并写已声明但没有 reducer 的键。retry/re-entry 可能与并行分支在同一 superstep 重叠，导致 `InvalidUpdateError` 或丢更新。复扫 `lg2-2336` 命中。
- **ACR106 · resume/replay 后重启已完成节点**：trace 里出现 `resume`/`replay` 标记，且同一节点在标记前已 `node_end`、标记后又 `node_start`，报重复执行风险。
- ACR008/ACR009 各补第二个正例和负例 fixture：lambda cache、class node retry、无 retry、无 cache_policy。

## 验证边界（很重要）

- ACR010 是在第二轮 `lg2-2336` 发现 ACR002 miss 之后设计的；该案例已成为 ACR010 的开发集，不是独立验证。
- ACR106 目前只有合成 trace fixture，**没有真实社区 trace 样本**；不能宣称真实案例已验证。
- ACR008/ACR009 仍没有真实案例命中；本轮只是补了合成 fixture，不是真实验证。
- 第二轮报告的原始数字保持不变，补充记录在 `evals/realworld2/results/pilot2-2026-10-18/POSTFIX.md`。

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.3.0"
pipx inject agent-composition-review langgraph   # acr demo 需要

acr --version
acr demo
```

## 当前限制

- 静态规则仍不覆盖 command-send / RemoteGraph / Command.PARENT 等运行时/平台语义；这些应走 `acr trace` 或版本感知提示。
- ACR002 仍只建模兄弟扇出；ACR010 覆盖自环，但不覆盖所有重入/重叠形态。
- 真实案例试点样本小、来源有偏；两批之间的比例变化不能解读为真实分布变化。
- 不宣称 benchmark 提升或检测准确率。
