# ACR v0.5.0 — SARIF, Action outputs, inferred resume, atlas site

v0.5.0 把 ACR 往真实开发流程再推一步：SARIF 进 code scanning、Action 有可消费 outputs、resume 边界可推断、案例图谱有搜索页。

## 新增

- **SARIF**：`acr scan . --sarif -o acr.sarif` 输出 SARIF 2.1.0；GitHub code scanning 可以直接渲染文件/行注解。`--sarif` 与 `--json` 互斥。
- **GitHub Action 升级**：
  - inputs `upload-sarif`（默认 false）、`sarif-file`；
  - outputs `findings`、`sarif-file`；
  - 可选上传 `github/codeql-action/upload-sarif@v3`，scan 退出 1 时也会尝试上传已生成的 SARIF；
  - 调用方需要 `permissions: security-events: write`（仅开启上传时）。
- **`acr trace --infer-resume`**：opt-in 启发式，从重复 `checkpoint_id` 推断 resume 边界，减少手工 `--resume-at`；默认关闭。
- **案例图谱站点**：纯静态搜索页，支持症状关键词/规则 ID/issue 号搜索和 grounded/experimental 过滤。
  - Pages：<https://meiluosi.github.io/agent-composition-review/atlas/>

## 验证

- 73 tests passed
- `scripts/check_repo.py` PASS
- CI（package + framework）通过
- Action 端到端 smoke workflow 会校验 `findings` 和 SARIF 文件（发布后跑）
- SARIF 结果数与 `--json` findings 数一致（单 fixture 验证）

## 安装

```sh
pipx install "git+https://github.com/meiluosi/agent-composition-review.git@v0.5.0"
pipx inject agent-composition-review langgraph

acr --version
acr scan . --sarif -o acr.sarif
acr demo
```

Action：

```yaml
permissions:
  contents: read
  security-events: write
steps:
  - uses: actions/checkout@v4
  - id: acr
    uses: meiluosi/agent-composition-review@v0.5.0
    with:
      path: .
      fail-on: high
      upload-sarif: true
  - run: echo "ACR findings: ${{ steps.acr.outputs.findings }}"
```

## 边界

- `--infer-resume` 是启发式，不替代显式 `--resume-at`；嵌套/子图 trace 可能有重复 checkpoint id，需要人工核对。
- SARIF 只来自 `acr scan`；trace 不产 SARIF。
- ACR008/009/010 仍是 experimental；本轮补充搜索若没有 exact 样本，不提升状态。
- `grounded` 只表示检测行为有真实样本，不表示生产效果或上游已修复。
