# ACR v0.4.0 — usable ACR106, rule tiers, GitHub Action, case atlas

v0.4.0 把“能自己做的”三块补上：ACR106 可用化、规则证据分层、GitHub Action 和真实案例图谱。

## 新增

- **`acr trace --resume-at SEQ`**：在 LangGraph debug trace 中显式插入 resume 标记，不再要求手工改 trace；也支持统一 JSON 顶层 `resume_seq`。
- **规则状态分层**：`acr rules` 增加 STATUS 列；`grounded` = 至少一个真实公开/本地复现样本检查过行为（ACR001/002/003/007/106），其余为 `experimental`；finding JSON 增加 `status`，文本 finding 对实验规则显示 `[experimental]`。
- **GitHub Action**：`action.yml` 支持 `path` / `fail-on` / `args`；安装与 action ref 对应的包版本，只运行 `acr scan`。示例和文档见 `docs/github-action.md`。
- **真实案例图谱**：`docs/atlas/` 收录 10 个公开案例，按“症状 → 规则 → 证据 → 修复 → 验证边界”索引。

## 验证

- 67 tests passed
- `scripts/check_repo.py` PASS
- CI（package + framework）通过
- `acr trace` 在真实 #3875 trace 上 0 ACR106；在本地复现 #9276 trace 上命中 ACR106
- `acr trace --resume-at 4` 在无标记 trace 上可命中 ACR106

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.4.0"
pipx inject agent-composition-review langgraph

acr --version
acr demo
```

GitHub Action：

```yaml
- uses: actions/checkout@v4
- uses: meiluosi/agent-composition-review@v0.4.0
  with:
    path: .
    fail-on: high
```

## 边界

- `grounded` 只描述检测行为有真实样本，不表示生产效果、召回率或上游已修复。
- ACR008/009/010 仍是 experimental；没有独立 exact 命中。
- `--resume-at` 需要用户知道 resume 发生在哪条事件之后；LangGraph 默认 debug 流没有 resume 事件，这是显式标注，不是自动推断。
- Action 只跑 `acr scan`，不执行被扫描代码，不调用模型。
- 更早的 tag 不包含 `action.yml`，不能直接用作 Action 版本；请用 `v0.4.0` 或 pin SHA。
