# 在 GitHub Actions 中使用 ACR

ACR 提供一个 composite action，在 CI 里静态扫描 LangGraph/LangChain 构图代码，把组合故障挡在合并之前。它只运行 `acr scan`，不执行被扫描代码、不调用模型，也不需要 API key。

## 最小示例

```yaml
on: [push, pull_request]
jobs:
  acr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: meiluosi/agent-composition-review@v0.4.0
        with:
          path: .
          fail-on: high
```

上面是最小可运行示例（`path` 默认就是 `.`，这里显式写出）。建议同时声明 `permissions: contents: read` 以限制 token 权限。把最后一步换成你实际使用的发布 tag：示例固定为 `v0.4.0`，发布新版本后需要同步更新，并建议 pin 到完整 commit SHA 以获得可复现、不可被移动的构建。

## 输入

| 输入 | 默认值 | 说明 |
| --- | --- | --- |
| `path` | `.` | 要扫描的文件或目录，相对于 `github.workspace`。 |
| `fail-on` | `high` | 命中该级别或以上时以退出码 1 失败。可选 `high`、`review`（medium+high）、`never`（只报告不阻断）。 |
| `args` | 空 | 追加给 `acr scan` 的额外参数，例如 `--json`。按 shell 单词拆分；不要传入不可信的用户输入。 |

没有正式定义的 outputs。扫描结果写入 job 日志；需要机器可读结果时传 `args: --json`，再在后续 step 中处理 stdout。

## Action 实际做什么

1. 用 `actions/setup-python@v5` 准备 Python 3.12。
2. 从 GitHub 安装与 action ref 对应的包：
   `agent-composition-review @ git+https://github.com/meiluosi/agent-composition-review.git@<action ref>`。
   通过 tag、分支或 commit SHA 引用 action 时，安装同一 ref；用 `uses: ./` 引用本仓库本地 action 时 `github.action_ref` 为空，回退到 `main`。
3. 运行 `acr scan "<path>" --fail-on "<fail-on>" <args>`。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 没有达到 `fail-on` 阈值的发现。 |
| `1` | 有达到阈值的发现（`fail-on: never` 时始终为 0）。 |
| `2` | 参数错误、路径不存在或扫描失败。 |

## 实验性规则

当前 Action 只运行 `acr scan` 的**默认规则集**，不传入实验性参数，也不会把实验性规则（例如后续可能引入的 `--experimental`）默认启用或作为阻断条件。规则分层仍在开发中；等 CLI 正式提供该能力后会先更新本页和 action 的输入，不会悄悄改变默认行为。

需要查看当前规则与级别：

```sh
acr rules
```

规则文档见 [docs/rules](rules/)；每条 finding 也会输出自己的文档路径。

## 版本与可复现性

- Action 安装的包版本由 action ref 决定：`@v0.4.0` 安装 `v0.4.0`，`@<sha>` 安装对应 commit。
- 本 Action 随仓库根目录的 `action.yml` 提供。如果某个 tag 早于该文件，GitHub 会报告找不到 `action.yml`；请改用包含本 Action 的 tag 或直接 pin 到对应 commit SHA。
- 用 `uses: ./`（仓库内示例 workflow）时没有 action ref，会安装 `main`，因此结果可能跟随最新提交变化；只建议用于本仓库演示。
- 运行时需要访问 GitHub（`pip install` 会 clone 仓库），并需要 runner 提供 `git`；GitHub 托管的 Ubuntu/macOS/Windows runner 默认满足。
- 每次运行都会重新安装包，不复用 pip 缓存；这是为了简单和避免跨 ref 污染。

## 本仓库内的示例 workflow

[.github/workflows/acr-example.yml](../.github/workflows/acr-example.yml) 用 `uses: ./` 演示本地 action，只能手动触发（`workflow_dispatch`）。它扫描仓库根目录，仓库内评测样本含故意写坏的图，因此可能报告 high 级问题——这是示例，不是发布门禁。

## 限制

- 只做静态 AST 扫描；不运行图、不读取运行轨迹，因此不会发现只在运行期出现的组合故障。运行期问题请单独使用 `acr trace`。
- 不安装 `langgraph`；扫描不需要框架依赖，`acr demo` 不在 Action 范围内。
- 不生成 SARIF 或 GitHub Code Scanning annotations，也不自动修改代码。
- 规则是启发式的：不确定的情况只报 `review` 级别，仍可能误报，请结合 finding 中的证据人工确认。

## 安全

- Action 会执行 `pip install` 并在 runner 上运行扫描器；这是可执行供应链。生产仓库建议把 `uses:` pin 到完整 commit SHA 并审核升级。
- `path` 和 `fail-on` 会作为带引号的 shell 变量传入；`args` 会有意做单词拆分以传递多个 CLI 参数，只应放入来自受信任 workflow 配置的静态值，不要拼接 PR 标题、issue 正文等不可信输入。
- 不要提交含密钥的生产轨迹或客户代码；扫描报告可能引用源码片段，上传日志前请自行检查。

## 常见问题

**`path` 不存在 / finding 文件名为空**
Action 的 `path` 是相对仓库根的路径；确认前面有 `actions/checkout`，且 `path` 没有指到未检出的目录。

**本地 action（`uses: ./`）安装的是 main**
预期行为：本地 action 没有 ref。需要固定版本时改用已发布 tag，或把 Action 复制到你的仓库并显式安装具体版本。

**想只报告不阻断**
设 `fail-on: never`；如果只关心 high，保持默认 `high`，用 `review` 会把 medium 级 finding 也当作失败。
