# ACR v0.6.0 — Agent-ready Skill pack

v0.6.0 是收尾优化：**不新增规则、不改 CLI 行为**，只把 Skill 从“流程说明”补成“可检索、可降级、可评测”的诊断协议。

## 新增

- **病例索引**：`references/case-index.md`
  - 症状/错误文本 → 规则 → 11 个真实案例；
  - 覆盖 `InvalidUpdateError`、字段静默丢失、resume 重复副作用、cache 串图、retry 自环等入口。
- **无 CLI 降级检查单**：`references/no-cli-checklist.md`
  - 没有 `acr` 时的 10 步人工审计；
  - 明确要求“声明未运行扫描”，不伪造命令输出。
- **三个 worked examples**：`references/worked-examples.md`
  - ACR001 / ACR007 / ACR106；
  - 每个都按“错误假设 → 证据 → 最小修复 → 回归断言”展开。
- **Skill 协议评测集**：`references/skill-evals/`
  - 10 个 prompt + 五维评分表（复现、证据、分级、作用域、诚实）；
  - 不调用模型 API，不测规则准确率或模型排名。

## 变更

- SKILL 对齐当前版本：
  - 删掉过时的“v0.1 边界”；
  - 增加维护模式说明；
  - 更新 grounded / experimental 规则状态；
  - 无 CLI 时指向 10 步降级清单；
  - 增加病例索引/worked examples/评测集入口。
- README 的 Agent 使用章节同步更新。

## 验证

- 81 tests passed
- `scripts/check_repo.py` PASS
- Skill 文档链接、病例索引覆盖、评测 prompt 结构均有测试
- 本轮不新增规则、不改 CLI 行为；ACR008/009/010 仍为 experimental

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.6.0"
pipx inject agent-composition-review langgraph

acr --version
acr demo
```

Skill 入口：[skills/agent-composition-review/SKILL.md](skills/agent-composition-review/SKILL.md)

## 边界

- 评测集测的是 Agent 是否遵守诊断协议，不是 ACR 规则准确率。
- 没有外部 design partner；不宣称 benchmark 或生产效果。
- 当前是维护模式：优先真实误报、兼容性和文档问题，不主动增加规则。
