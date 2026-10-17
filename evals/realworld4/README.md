# 第四轮：补充搜索（lead 追踪）

这一轮不是新的随机抽样，而是追第三轮留下的“未验证代码搜索线索”，看能否为 ACR008/009/010 找到 exact 真实样本。

## 结论

- **ACR008**：没有新的 exact 失败样本；但追到一条真实公开代码（vanara-ai/vanara-server）暴露了 ACR008 的**误报**——它把 bound method 当成身份不稳定的 callable。LangGraph 对 bound method 用 module + qualname 生成稳定标识，因此这是误报；已修，并留作真实负向控制。
- **ACR009**：本轮追到的 `retry_policy` 案例都只调用一个子图/工具，或 retry 节点不调用子图；没有满足“retry_policy + 2 个子图”的新 exact 样本。
- **ACR010**：没有新的 exact 线索。

因此 ACR008/009/010 仍保持 `experimental`，不提升为 `grounded`。

## 文件

- `github_candidates.json`：第三轮转入的邻近候选 + `unverified_code_search_leads`。
- `community_candidates.json`：本轮社区源未抓取（中断），空结果不代表社区没有样本。
- `verified_leads.json`：本轮实际抓取、扫描过的代码线索与结论。
- `LEADS_VERIFIED.md`：逐条说明。
- `raw/`：原始 issue/代码快照，git 忽略，不公开。
