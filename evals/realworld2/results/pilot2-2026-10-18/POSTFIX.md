# Post-report 补充（开发集，2026-10-18）

原始 `REPORT.md` 记录的是 14 条规则（acr 0.2.0 发布前）的验证结果，保持冻结。之后按报告建议补了两项能力，这一页记录它们与本批的关系；**这不是新的验证批次**。

## 补了什么

1. **ACR010 · 条件自环写非 reducer 键**：覆盖 `lg2-2336` 的结构（并行分支 + retry 自环，`count_b` 无 reducer）。复扫 `lg2-2336` 现在报 ACR010 medium，而不是 0 findings。
2. **ACR106 · resume/replay 后重启已完成节点**：给 trace 增加 `resume`/`replay` 标记；若同一节点在标记前已 `node_end`、标记后又 `node_start`，报 ACR106。只有合成 trace fixture，**没有真实 trace 样本**。

同时把 ACR008/ACR009 的 fixture 补到各 2 正例 + 2 负例，但仍是合成 fixture，不是真实案例验证。

## 边界

- `lg2-2336` 已经在第二轮报告中作为 ACR002 miss 出现；ACR010 是在看到该结果后设计的，所以 **lg2-2336 对 ACR010 是开发集，不是验证集**。
- ACR106 未在任何真实社区 trace 上验证；不能宣称它已被真实案例验证。
- ACR008/ACR009 仍然没有真实案例命中：第二轮 20 例中没有 cache identity 或 retry_policy+多子图样本。
- 这些补充不改变 `REPORT.md` 的原始数字，也不把第二轮改名为“全部通过”。
