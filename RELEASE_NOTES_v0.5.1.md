# ACR v0.5.1 — ACR008 bound-method false-positive fix

v0.5.1 是 lead 追踪带来的小修：真实公开代码暴露了 ACR008 的一个误报，修掉并留作真实负向控制。

## 修复

- **ACR008 不再把 bound method 当成匿名 callable**。
  - 真实代码：<https://github.com/vanara-ai/vanara-server/blob/main/app/resume_optimizer.py>
  - `cache_policy=CachePolicy()` 放在 `self._parse_jd_node` 上，旧版 ACR008 报“callable 身份不稳定”。
  - LangGraph 对 bound method 用稳定的 module + qualname 生成 cache key，不会落到 `__dynamic__`；这是误报。
  - scanner 现在收集类方法名，`add_node(..., nodes.run, ...)` 这类 `ast.Attribute` 可解析为 method。

## 新增证据

- `evals/realworld4/LEADS_VERIFIED.md`：逐条追踪第三轮 4 条未验证代码搜索线索。
- 案例图谱第 11 个案例：[vanara bound method 误报](docs/atlas/lg-vanara-bound-method.md)。
- 回归 fixture：`tests/fixtures/acr008_negative_bound_method.py`。

## 边界

- 这是**真实公开代码上的负向控制**，不是“ACR008 抓到了真实 cache 故障”。
- ACR008 仍保持 `experimental`；正向 exact 案例仍只有 #8753，且没有新的独立命中。
- ACR009/ACR010 本轮仍没有新 exact 样本，继续保持 `experimental`。
- 真实代码不执行，只做静态扫描；没有在该 repo 复现缓存错误结果。

## 验证

- 74 tests passed
- `scripts/check_repo.py` PASS
- 修复前：ACR008 at line 218；修复后：0 ACR008 findings
- `identifier(Nodes().run)` = `__main__.Nodes.run`，不是 `None`
