# 四组消融的复现方式

这轮针对已经用于设计的八个开发任务运行四组对照，不是新保留集。运行前冻结提交为 `5bc7224`；具体条件和统计口径见 [冻结清单](../evals/iterations/ablation-2026-09-29/manifest.json) 与 [分析方案](../evals/iterations/ablation-2026-09-29/ANALYSIS_PLAN.md)。[正式结果](../evals/iterations/ablation-2026-09-29/REPORT.md) 和 [后续判断](ablation-findings.md) 应与首轮 144 次结果分别阅读。

## 只复核已保存的成绩和图表

安装 `requirements-benchmark.txt` 后，运行：

```sh
.venv/bin/python -m unittest evals.iterations.test_ablation -v
.venv/bin/python scripts/analyze_ablation.py publish
```

不需要模型登录或原始数据。绘图会核对 manifest、逐次预测的摘要，从公开得分重新计算各组统计，再与保存的汇总比较。置信区间按任务计算，四组对比使用同一重采样索引。

## 重新运行模型

需要首轮所用的 Python 3.12、Codex CLI 0.158.0-alpha.2.1 与有效登录，运行会消耗登录额度。在独立仓库副本中执行，保留新实验来源，不能把新输出提交成对历史记录的覆盖。

```sh
.venv/bin/python scripts/prepare_external_eval.py --restore-inputs
.venv/bin/python scripts/run_ablation.py verify
.venv/bin/python scripts/run_ablation.py run
.venv/bin/python scripts/analyze_ablation.py score
.venv/bin/python scripts/analyze_ablation.py publish
```

restore-inputs 使用首轮固定数据版本，恢复被忽略的输入与标签并核对哈希。run_ablation 使用本轮冻结的三个文本快照，而非当前安装目录中的 Skill。共同提示词沿用原评分核心，A/B 的提示词与首轮完全相同；新运行有自己的记录，未复用首轮预测。

每次模型调用单独启动 ephemeral 会话，使用空临时工作目录，忽略用户配置、禁用宿主 Skill 发现和工具。模型只能看到所选轨迹及该组材料，不能读取答案、其他组输出或仓库文件。正文组不能访问链接，因此测的是参考材料移除效果，不是自动路由。

已有 record.json 的运行不会重试，失败输出保留分母。新实例会执行 96 次，完整执行过的实例再次调用 run 不再发起模型请求。原始记录被 Git 忽略；score 需要全部 96 份本地记录，并校验开始时间、提示词、CLI 版本和事件文件哈希才生成成绩。

冻结之后不要编辑 run_ablation.py、原 core.py、分析方案或快照来追求某条题目的正确答案。新一轮修改应使用新的运行目录和方案。模型服务端快照不可见、生成存在随机性，因此可复核流程与原始证据，不承诺再次生成逐字相同结果。

## 解释边界

本次五条 Who&When 和三条 Pro 是目的性选择的开发案例，整体均值不代表数据集自然频率。三次重复也不能变成三个独立任务。四组差值和 token 成本分别报告，不事后加权选出“最佳总分”。

候选仅通过本轮开发检查仍不自动替换主 Skill。后续若开放工具、原生触发、按需读取或提问，必须让各组获得相同能力，并在新任务上独立验收。
