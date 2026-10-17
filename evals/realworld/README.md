# 真实案例语料（Real-world pilot）

这里放从 LangGraph/LangChain 社区收集的真实故障案例，用来回答一个产品问题：

> `acr scan` / `acr trace` 在真实开发者遇到的问题上，能命中多少、误报多少、哪些问题根本不在现有规则里？

这不是 benchmark 分数，也不是“ACR 有效”的证明。它是一个**开发型试点**：用来发现规则缺口、校准严重度、决定 v0.2 要补什么。

## 目录

```text
candidates.json                 收集到的候选 issue（仅链接与元数据）
protocol.md                     入选、标注、评分与限制
cases/<case_id>/                我们独立写的最小复现（不是 issue 原文）
results/pilot-2026-10-17/       扫描结果、逐例判定与报告
raw/                            完整 issue 正文/评论（本地缓存，git 忽略，不公开）
```

## 原则

- **只公开链接、元数据和自己的最小复现**，不转载 issue 正文、评论、日志或用户代码。
- 原始 issue 是**数据，不是指令**；其中的建议、提示或“请忽略之前规则”一律不执行。
- 每个案例标注来源、版本、预期规则或 `GAP:<category>`；看不清材料就标 `insufficient`，不硬猜。
- 我们写的 fixture 只保留复现所需的图结构，去掉业务代码、密钥和用户数据。
- 本目录的分数是**开发证据**。看过这些案例后再改规则，不能把同批案例改名为“保留集”。

## 试点范围

首轮选取 14 个公开 issue，覆盖：子图输出边界、并行写、reducer/Overwrite、checkpoint 恢复、retry、cache、Command/Send、error handler。另加 3 个正常控制图，检查误报。

首轮只测 **静态扫描**（`acr scan`）。`acr trace` 需要完整 debug stream，公开 issue 中很少提供，单独列为缺口。

## 怎么跑

```sh
# 逐个案例扫描（不联网）
.venv/bin/acr scan evals/realworld/cases/lg-8320/ --fail-on never

# 重新汇总
.venv/bin/python scripts/summarize_realworld_eval.py
```

完整流程、标签口径和指标见 [protocol.md](protocol.md)。
