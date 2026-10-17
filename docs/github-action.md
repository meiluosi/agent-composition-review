# 在 GitHub Actions 中使用 ACR

ACR 提供一个 composite action：在 CI 里静态扫描 LangGraph/LangChain 构图代码，生成 SARIF 2.1.0，并可选上传到 GitHub code scanning。它只运行 `acr scan`，不执行被扫描代码、不调用模型，也不需要 API key。

## 最小示例（不上传 code scanning）

```yaml
on: [push, pull_request]
permissions:
  contents: read
jobs:
  acr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: meiluosi/agent-composition-review@v0.5.1
        with:
          path: .
          fail-on: high
```

Action 会把 SARIF 写到 workspace 的 `acr.sarif`，但默认不上传。把 `uses:` 换成你实际使用的发布 tag；示例固定为 `v0.5.1`，发布新版本后需要同步更新，并建议 pin 到完整 commit SHA 以获得可复现、不可被移动的构建。

## 上传到 GitHub code scanning

在最小示例基础上给 job 增加 `security-events: write` 权限，并打开 `upload-sarif`：

```yaml
on: [push, pull_request]
permissions:
  contents: read
  security-events: write
jobs:
  acr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: meiluosi/agent-composition-review@v0.5.1
        with:
          upload-sarif: true
```

- `security-events: write` 是上传所必需的；如果仓库或 PR 没有该权限，保持默认 `upload-sarif: false`，否则上传步骤会失败。
- 上传使用 `github/codeql-action/upload-sarif@v3`，`category` 固定为 `acr`；即使 `acr scan` 因发现 high 级问题以退出码 1 结束，也会在 `always()` 条件下尝试上传已有 SARIF。
- **不上传时 SARIF 仍然保留在 workspace**：默认路径 `acr.sarif`，可用 [`actions/upload-artifact`](https://github.com/actions/upload-artifact) 自行保存；`exit 2`（参数或路径错误）时可能没有生成文件，需自行处理。

## 输入

| 输入 | 默认值 | 说明 |
| --- | --- | --- |
| `path` | `.` | 要扫描的文件或目录，相对于 `github.workspace`。 |
| `fail-on` | `high` | 命中该级别或以上时以退出码 1 失败。可选 `high`、`review`（medium+high）、`never`（只报告不阻断）。 |
| `args` | 空 | 追加给 `acr scan` 的额外参数。按 shell 单词拆分；不要传入不可信的用户输入。不要传 `--json`、`--sarif` 或 `--output`，Action 已经使用它们。 |
| `upload-sarif` | `false` | 是否把 SARIF 上传到 GitHub code scanning。需要 `permissions: security-events: write`。 |
| `sarif-file` | `acr.sarif` | SARIF 输出路径。相对路径基于 `github.workspace` 解析；绝对路径原样使用。 |

## 输出

| 输出 | 说明 |
| --- | --- |
| `findings` | SARIF `results` 的数量（整数）。没有 finding 时为 `0`。 |
| `sarif-file` | 实际写出的 SARIF 路径；输入为相对路径时输出为绝对路径。 |

因为输出名包含连字符，表达式里要用中括号访问：

```yaml
      - name: ACR scan
        id: acr
        uses: meiluosi/agent-composition-review@v0.5.1
        with:
          upload-sarif: true
      - name: Show findings count
        run: echo "ACR findings: ${{ steps.acr.outputs.findings }}"
      - name: Keep the SARIF file as an artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: acr-sarif
          path: ${{ steps.acr.outputs['sarif-file'] }}
          if-no-files-found: warn
```

## Action 实际做什么

1. 用 `actions/setup-python@v5` 准备 Python 3.12。
2. 从 GitHub 安装与 action ref 对应的包：
   `agent-composition-review @ git+https://github.com/meiluosi/agent-composition-review.git@<action ref>`。
   通过 tag、分支或 commit SHA 引用 action 时，安装同一 ref；用 `uses: ./` 引用本仓库本地 action 时 `github.action_ref` 为空，回退到 `main`。
3. 运行 `acr scan "<path>" --fail-on "<fail-on>" --sarif --output "<sarif-file>" <args>`，捕获退出码；用 Python 从 SARIF 统计 `results` 数量并写入 step outputs，然后以原退出码退出（保持 `fail-on` 语义）。
4. 如果 `upload-sarif: true`，用 `github/codeql-action/upload-sarif@v3` 上传 `steps.scan.outputs['sarif-file']`，`category: acr`。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 没有达到 `fail-on` 阈值的发现。 |
| `1` | 有达到阈值的发现（`fail-on: never` 时始终为 0）；SARIF 仍会写出，并可在开启上传时上传。 |
| `2` | 参数错误、路径不存在或扫描失败；此时可能没有 SARIF。 |

## SARIF 只在 `acr scan` 上

`acr scan` 的 SARIF 用于 GitHub code scanning，`acr trace` 不产生 SARIF，Action 也只运行 scan。需要运行期轨迹诊断时，请另外运行 `acr trace`（可在单独 step 中安装并使用 CLI），不要期待 Action 输出 trace 的 SARIF。

另外 `--json` 与 `--sarif` 互斥：Action 内部固定使用 `--sarif`，所以不要把 `--json` 放进 `args`；需要 JSON 时另开一步运行 `acr scan --json`。

## 实验性规则

当前 Action 只运行 `acr scan` 的**默认规则集**，不传入实验性参数，也不会把实验性规则（例如后续可能引入的 `--experimental`）默认启用或作为阻断条件。规则分层仍在开发中；等 CLI 正式提供该能力后会先更新本页和 action 的输入，不会悄悄改变默认行为。

需要查看当前规则与级别：

```sh
acr rules
```

规则文档见 [docs/rules](rules/)；每条 finding 也会输出自己的文档路径。

## 版本与可复现性

- Action 安装的包版本由 action ref 决定：`@v0.5.1` 安装 `v0.5.1`，`@<sha>` 安装对应 commit。
- 本 Action 随仓库根目录的 `action.yml` 提供。如果某个 tag 早于该文件，GitHub 会报告找不到 `action.yml`；请改用包含本 Action 的 tag 或直接 pin 到对应 commit SHA。
- 用 `uses: ./`（仓库内示例 workflow）时没有 action ref，会安装 `main`，因此结果可能跟随最新提交变化；只建议用于本仓库演示。
- 运行时需要访问 GitHub（`pip install` 会 clone 仓库），并需要 runner 提供 `git`；GitHub 托管的 Ubuntu/macOS/Windows runner 默认满足。
- 每次运行都会重新安装包，不复用 pip 缓存；这是为了简单和避免跨 ref 污染。

## 本仓库内的示例 workflow

[.github/workflows/acr-example.yml](../.github/workflows/acr-example.yml) 用 `uses: ./` 演示本地 action，只能手动触发（`workflow_dispatch`）。它扫描仓库根目录，仓库内评测样本含故意写坏的图，因此可能报告 high 级问题；示例会打印 `findings` 并把 SARIF 作为 artifact 保留，不依赖 code scanning 权限——这是演示，不是发布门禁。

## 限制

- 只做静态 AST 扫描；不运行图、不读取运行轨迹，因此不会发现只在运行期出现的组合故障。运行期问题请单独使用 `acr trace`。
- 不安装 `langgraph`；扫描不需要框架依赖，`acr demo` 不在 Action 范围内。
- 只生成 SARIF 与 step outputs，不生成其他报告格式，也不自动修改代码。
- 规则是启发式的：不确定的情况只报 `review` 级别，仍可能误报，请结合 finding 中的证据人工确认。

## 安全

- Action 会执行 `pip install` 并在 runner 上运行扫描器；这是可执行供应链。生产仓库建议把 `uses:` pin 到完整 commit SHA 并审核升级。
- `upload-sarif: true` 需要显式授予 `security-events: write`；不要为了省事给整个 workflow 过宽权限。
- `path`、`fail-on` 和 `sarif-file` 会作为带引号的 shell 变量传入；`args` 会有意做单词拆分以传递多个 CLI 参数，只应放入来自受信任 workflow 配置的静态值，不要拼接 PR 标题、issue 正文等不可信输入。
- 不要提交含密钥的生产轨迹或客户代码；上传的 SARIF 与日志会引用源码片段和 finding 证据，上传前请自行检查。

## 常见问题

**上传报 `Resource not accessible by integration`**
缺少 `permissions: security-events: write`，或仓库未启用 code scanning。保持 `upload-sarif: false` 先只使用 SARIF artifact，或补上权限。

**找不到 `acr.sarif` / artifact 为空**
`exit 2`（路径或参数错误）时不会生成 SARIF；检查 `path`、`args` 和上一步日志。默认 `sarif-file` 相对 `github.workspace` 写出。

**输出里的 `sarif-file` 是绝对路径**
这是预期：相对输入会在 workspace 下解析，输出实际写出的绝对路径，便于后续 artifact/上传步骤使用。

**本地 action（`uses: ./`）安装的是 main**
预期行为：本地 action 没有 ref。需要固定版本时改用已发布 tag，或把 Action 复制到你的仓库并显式安装具体版本。

**想只报告不阻断**
设 `fail-on: never`；如果只关心 high，保持默认 `high`，用 `review` 会把 medium 级 finding 也当作失败。
