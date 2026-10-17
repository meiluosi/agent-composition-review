# 第二轮真实案例（验证批次）

这一轮存在的目的只有一个：**验证 ACR007–ACR009 和已有规则在没参与设计的新案例上表现如何**。

第一批 14 个 issue 已经用于发现缺口和设计规则（见 `../realworld/`）。本批 20 个案例来自 GitHub issue（15）和 LangChain Forum / Stack Overflow（5），在规则实现之后才被选入，**没有参与 ACR007–ACR009 的设计**。这不等于统计意义上的保留集：样本小、来源有偏，结论仍只描述这一批。

## 目录

```text
candidates.json          20 个入选案例的元数据（链接、类型猜测、raw 路径）
protocol.md              入选、盲标、扫描、评分与限制
cases/<case_id>/         独立最小复现（不转载原帖正文）
labels.json              盲标（在看 scan 结果之前冻结）
results/pilot2-2026-10-18/  扫描结果、逐例判定、报告
raw/                     本地原始 issue/帖子缓存，git 忽略，不公开
```

## 和第一批的区别

- 第一批：14 个 GitHub issue + 3 个控制，结果用于**发现问题**；ACR001–ACR009 中有一部分规则是在看完第一批后写的。
- 第二批：20 个新案例，规则已冻结，结果用于**验证**；如果验证发现规则需要改，改完后本批就变成开发材料，不能再叫验证集。
- 本批新增 5 个非 GitHub 来源（Forum/Stack Overflow），用来检查 GitHub issue 是否系统性偏向框架内部 bug。

## 怎么跑

```sh
# 扫描某个案例（不联网）
.venv/bin/acr scan evals/realworld2/cases/<case_id> --fail-on never

# 汇总（离线、校验 labels sha256）
PYTHONPATH=src python3 scripts/summarize_realworld2_eval.py
```

协议与限制见 [protocol.md](protocol.md)。
