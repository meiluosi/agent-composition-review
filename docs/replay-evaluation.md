# 配置修复实验复现

2026-09-30，8 个合成开发任务 × 无额外 Skill / v3 / v4 × 两次，共 48 次；gpt-6-astra、高推理配置。输入、输出格式、指导快照、执行器、评分器和分析方案在提交 `8c8b03f` 前冻结；实验完成后只发布结果和解释，没有修改冻结文件。

- [运行前方案](../evals/iterations/replay-2026-09-30/ANALYSIS_PLAN.md)
- [冻结清单](../evals/iterations/replay-2026-09-30/manifest.json)
- [任务与结构](../evals/iterations/replay-2026-09-30/cases.json)
- [逐次输出、工具审计与验收评分](../evals/iterations/replay-2026-09-30/predictions.json)
- [结果报告](../evals/iterations/replay-2026-09-30/REPORT.md)
- [解释与版本决定](replay-findings.md)

## 不调用模型的复核

在项目根目录使用 Python 3.12：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-eval.txt -r requirements-benchmark.txt
.venv/bin/python -m unittest evals.replay.test_runtime evals.replay.test_protocol -v
.venv/bin/python scripts/verify_contract_regression.py
.venv/bin/python scripts/analyze_replay.py publish
```

最后一条命令先验证冻结文件与预测任务列表，再把每个模型配置实际应用到本地系统，重新检查 48 个生成输入与 9 个新组合，并枚举 64 种配置求最少改动数。之后比对已公布评分、重新生成汇总及 PNG/SVG 图表。不是只把旧汇总读出来画图。CI 使用同样流程。

## 若要新跑模型实验

历史 `run_replay_eval.py` 会复用已完成记录，并阻止对未完成尝试静默重跑。原始运行日志在忽略提交的 raw-runs 下；公开预测保留结构化回答、命令、使用量和原始日志哈希。新实验应建立新目录、明确预算和协议，并在运行前冻结，不能覆盖本轮记录或选择性补样。

模型只能在临时目录中看到业务要求、初始配置、代码、结构、一次实际轨迹和共同工具，加上对应组的 Skill 文件；维护端的参考配置、最小值计算和独立验收器不复制进去。所有模型命令都通过既定审计，三组都实际读入任务并执行回放。只读沙箱和日志审计仍不等同于严格宿主文件隔离。

报告的 “无 Skill” 条件仍有共同结构工具。调用由现有 Codex 登录额度完成，没有建立新的 API 密钥。后端精确模型快照及货币成本无法获得；重复运行会产生随机变化，不能保证复现同样回答或时间。
