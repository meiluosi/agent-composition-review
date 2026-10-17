# 第三轮：定向能力验证（targeted validation）

前两轮回答的是“规则在真实案例上的覆盖情况”；这一轮只回答四个具体问题：

1. **ACR106**：有没有真实公开的 debug trace / resume 日志，能证明“resume 后已完成节点被重启”？
2. **ACR008**：有没有新的真实 cache identity 案例（callable object/lambda/partial/共享 cache）？
3. **ACR009**：有没有新的真实 `retry_policy` + 多子图案例？
4. **ACR010**：有没有新的真实条件自环/重试循环写非 reducer 键案例（不复用 `lg2-2336`）？

**这不是流行率估计。** 样本是按目标关键词定向收集的，所以不能算“ACR 在真实世界的命中率”；它只回答“这些能力有没有被真实样本验证过”。

## 目录

```text
candidates.json          入选样本元数据
protocol.md              目标、样本标准、标注与判定
cases/<case_id>/         fixture（构图代码或真实 trace 的脱敏副本）
labels.json              盲标
results/pilot3-2026-10-18/  定向验证报告
raw/                     原始帖子/日志缓存，git 忽略，不公开
```

## 三种结果都允许

- `validated`：找到真实样本，规则命中/不命中且可解释；
- `not_validated`：找到了真实样本，但规则没命中或样本不适用；
- `no_sample_found`：公开来源里没有足够样本，明确写“未验证”。

没有样本不丢人；把合成 fixture 说成真实样本才是问题。
