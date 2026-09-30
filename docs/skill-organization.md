# 运行包与维护仓库

2026-09-30 核对 [OpenAI 的 Skill 文档](https://learn.chatgpt.com/docs/build-skills) 和 [Anthropic 的编写建议](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)。双方以含名称和触发描述的 SKILL.md 为入口，允许按需参考及可执行脚本。它们没有要求每个项目必须拥有 scripts、references、assets、docs 全套空目录。分层读取能控制上下文；目录名称本身不保证模型效果。

本仓库采用以下边界：

- `skills/agent-composition-review/`：当前默认包，入口、参考与界面配置。此次仅移动位置，内容保持逐字一致。
- `candidates/v4/agent-composition-review/`：尚未晋升的候选，入口、条件参考、确定性检查器和可改造的结构模板。模板是运行资源，不包含评测答案。
- `scripts/`：维护脚本，不进入运行包。
- `docs/`：理论说明、使用及结果解释，供人阅读。
- `evals/`：案例、实验协议、冻结快照、验收及结果，不进入运行包。旧候选保留原位置以保持冻结实验可复核。

默认打包仍是当前主版本。候选使用显式 `--source candidates/v4/agent-composition-review` 单独打包。不得直接复制整个仓库到个人 Skill 目录；安装副本也不是第二个维护源。当前仓库已是独立 Git 项目，不在外层集合目录初始化 Git。

入口只保留任务决策与资源索引，模型按症状读取参考，重复检查交给脚本。复杂问题仍可能需要组合多个模块；只有形成独立用户任务、验收和发布周期才拆成其他 Skill。

与原来相比，这次修复的是维护脚本与运行资源混在根目录带来的打包和使用歧义，尚无证据表明原布局导致模型退化。模型对照会评估新工作流及工具的整体增量，不把结果单独归因于目录调整。
