# 定向验证协议（pilot3）

冻结日期：2026-10-18。
性质：定向能力验证，不是随机抽样，不估计流行率。

## 1. 四个目标

| 目标 | 需要的真实样本 | 判定 |
| --- | --- | --- |
| ACR106 | 带 resume/replay 标记的 trace，且同一节点在标记前已 `node_end`、标记后再次 `node_start` | 找到真实 trace → 脱敏后运行 `acr trace`；命中/未命中都要报告 |
| ACR008 | 节点启用 cache_policy，callable 是 callable object/lambda/partial/未解析参数，且涉及共享 cache 或错误命中 | 静态 `acr scan` 命中/未命中 |
| ACR009 | 节点有 retry_policy，函数体内调用 2 个以上子图（或等价结构） | 静态 `acr scan` 命中/未命中 |
| ACR010 | 条件边自环 + 写已声明、无 reducer 的键（排除 #2336） | 静态 `acr scan` 命中/未命中 |

## 2. 来源

- GitHub issue / PR / discussion / gist / 公开仓库中的日志或最小复现；
- LangChain Forum、Stack Overflow、Reddit、博客等公开页面；
- 只使用公开、可链接的内容；不绕过登录/付费墙；不抓取隐私。

每个候选记录：`target`、`source_url`、`title/date`、`has_code`/`has_trace`、`reproducible`、`summary`、`raw_path`。原始正文缓存到 `raw/`，不进入 Git。

## 3. 样本标准

- **真实**：来自第三方公开材料，不是我们为本规则写的合成 fixture；
- **可核对**：有 URL、版本/日期（至少一个）、足够代码或 trace；
- **可脱敏**：删除账号、密钥、业务数据后仍能保留结构；
- **目标相关**：能触发/接近触发目标规则的结构；
- **不重复**：排除前两批已用案例（#8753、#9253、#2336 等）。

如果只有代码、没有 trace，ACR106 不能算真实样本；如果只有文字描述、没有结构，标 `insufficient`。

## 4. 标注与扫描

1. 先写 `labels.json`（不看 scan/trace 输出）：`target`、`source_type`、`expected_rule`、`expected_finding`、`material_notes`、`confidence`。
2. 再运行 `acr scan` 或 `acr trace`，保存原始 stdout 到 `scan.txt`/`trace.txt`。
3. 判定：
   - `hit`：目标规则命中且解释与样本故障相关；
   - `miss`：目标规则存在但未命中；
   - `false_positive`：样本正常/规则解释误导；
   - `not_applicable`：材料不足或结构不匹配；
   - `no_sample_found`：没有真实样本。

## 5. 指标

只报告每个目标的 `n` 和逐例结果，不合并成总体准确率：

- 每个目标：找到几个真实样本、可分析几个、hit/miss/FP/not_applicable；
- ACR106：真实 trace 数、带 resume 标记的 trace 数、命中数；
- 明确列出 “no_sample_found” 的目标。

## 6. 限制

- 定向收集导致选择偏差，不能外推到真实世界流行率。
- 公开 issue 很少附完整 trace；ACR106 很可能 `no_sample_found`，这本身就是结论。
- 脱敏 trace 可能丢失节点/step 字段；如果因此不可分析，标 not_applicable。
- 如果样本看了之后改规则，本批就变成开发集，必须另找新样本验证。
