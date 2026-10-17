# 贡献与维护

ACR 是一个面向公开开发者的 LangGraph/LangChain 组合故障诊断工具。最有价值的贡献是：**一个可复现的故障、一个证明规则误报的正常反例，或一个真实修复案例**。比新增抽象原则更有用。

## 本地开发

需要 Python 3.10+。

```sh
git clone https://github.com/meiluosi/agent-composition-review.git
cd agent-composition-review
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,langgraph]"

.venv/bin/python -m pytest -q          # 产品代码测试
.venv/bin/python scripts/check_repo.py # 仓库结构、链接、Skill frontmatter
```

跑真实 demo：

```sh
.venv/bin/acr demo
.venv/bin/acr scan examples/langgraph-subgraph-leak/broken.py
.venv/bin/acr trace examples/langgraph-subgraph-leak/trace.jsonl
```

## 提交故障或误报

请附：

1. 最小构图代码或最小 trace（脱敏，删除密钥、用户数据、客户数据和私有 URL）。
2. Python、LangGraph/LangChain、`acr --version`。
3. 实际输出与期望输出；如果是误报，说明为什么这段代码在业务上是正确的。
4. 能复现的命令。不要上传整个仓库、虚拟环境或聊天导出。

Issue 模板在 `.github/ISSUE_TEMPLATE/`。安全漏洞不要公开提交，见 [SECURITY.md](SECURITY.md)。

## 修改规则

新增或修改 `acr scan` / `acr trace` 规则时，一次只改一个规则，并同时提交：

1. **正例 fixture**：`tests/fixtures/` 下能被规则命中、且最小可读的代码或 trace。
2. **负例 fixture**：证明“看起来像但不应报”的情况，防止规则退化成噪声。
3. **测试**：在 `tests/test_scan.py` 或 `tests/test_trace.py` 中断言规则 ID、严重度、证据行/事件 ID。
4. **规则页**：在 `docs/rules/<RULE>.md` 写症状、为什么发生、输出示例、最小修复、误报边界。
5. **规则表**：更新 `src/agent_composition_review/rules.py` 的标题、严重度、摘要与修复建议，保持 README 规则表一致。

原则：

- 不确定就降级为 medium/low 或跳过，不要硬报 high。
- 证据必须回到具体文件行号或事件 ID，不能只给结论。
- 规则命中不等于根因；修复建议必须是最小改动，不要建议重写整张图。
- 跨文件、动态构图、无法解析的写者：宁可漏报，不要猜。

## 修改 Skill

Skill 入口在 `skills/agent-composition-review/`，规则速查在 `references/rules.md`，修复模式在 `references/fix-patterns.md`。修改后运行：

```sh
.venv/bin/python scripts/check_repo.py
.venv/bin/python scripts/build_skill.py
```

PR 说明写清楚：哪个真实请求或故障触发了修改、改动前后行为、相关规则/测试、未覆盖的边界。不要因为一条经验有效就把它变成所有任务都必须执行的步骤。

## 评测档案

`evals/`、`docs/evaluation-history.md` 和 2026 年 9–10 月的实验记录是**历史证据档案**，不是 v0.1 的产品效果承诺。它们记录了当时的负结果、未完成记录和限制，不应被改写或挑选性地引用为 `acr` 的准确率。

如果要做新的效果评测：

- 先冻结题目、输入、评分器和判据，再运行；
- 保留无工具/旧版本对照，按任务配对统计，不把重复运行当独立样本；
- 不把开发集成绩写成保留集成绩；
- 不编造运行结果或把绿色 CI 当模型能力证据。

## PR 检查清单

- [ ] `pytest -q` 通过；
- [ ] `scripts/check_repo.py` 通过（Markdown 链接、SKILL frontmatter）；
- [ ] 新规则有正例 + 负例 + 规则页；
- [ ] `acr demo` 仍能跑通（改了 demo/示例时）；
- [ ] 没有提交密钥、私有轨迹、客户数据、`.venv/`、`build/`、`dist/`、`*.egg-info/`；
- [ ] PR 描述包含触发问题、行为变化、验证命令与已知限制。

## 发布

维护者发布流程见 [docs/launch/README.md](docs/launch/README.md)。版本号在 `VERSION` 与 `pyproject.toml` 保持一致；tag 使用 `vX.Y.Z`。发布前构建 sdist/wheel 并在干净环境验证安装：

```sh
python -m build
twine check dist/*
```

## 许可证

贡献即同意以 [MIT](LICENSE) 许可发布。第三方代码、数据和图片必须保留其适用许可与来源说明。
