# vanara：bound method 上的 cache_policy 被 ACR008 误报

## 一句话症状

真实公开代码把 `CachePolicy()` 放在类方法 `self._parse_jd_node` 上；LangGraph 对 bound method 有稳定的 module + qualname 标识，ACR008 却把它当成“匿名 callable”，产生误报。

## 来源

- 代码：<https://github.com/vanara-ai/vanara-server/blob/main/app/resume_optimizer.py>
- 线索：`evals/realworld4/verified_leads.json` / [`LEADS_VERIFIED.md`](../../evals/realworld4/LEADS_VERIFIED.md)
- 类型：真实公开代码，非 issue 故障报告；没有“缓存返回错误结果”的用户报告。

代码形态：

```python
workflow.add_node(
    "parse_jd",
    self._parse_jd_node,
    cache_policy=CachePolicy(),
    ...
)
```

## ACR 命令

```sh
acr scan app/resume_optimizer.py --fail-on never
```

修复前（v0.5.0）：

```text
MEDIUM ACR008 [experimental]  Cache policy on a callable without a stable function identity
  ...:218
  Node 'parse_jd' enables cache_policy but its callable is not a statically named function
  (callable object, lambda, partial, or unknown parameter).
```

修复后（v0.5.1）：

```text
0 ACR008 findings
```

## 证据

LangGraph 用 `identifier(call.func) or "__dynamic__"` 生成 cache key。bound method 的 identifier 是稳定的：

```python
from langgraph.pregel._call import identifier

class Nodes:
    def run(self, state): ...

identifier(Nodes().run)
# '__main__.Nodes.run'  —— 不是 None，不会落到 __dynamic__
```

所以这条命中的是**检测误报**，不是 cache identity 失败。ACR008 的 exact 正向案例仍是 #8753，且没有新的独立命中。

## 修复

v0.5.1 的 scanner 会收集类方法名；`add_node(..., nodes.run, ...)` 这类 `ast.Attribute` 能解析为 method，ACR008 不再把它当成身份不稳定的 callable。

回归 fixture：`tests/fixtures/acr008_negative_bound_method.py`。

## 验证边界

- 真实公开代码上的**负向控制**：规则应安静时安静。
- 没有报告该 repo 出现错误缓存结果；不能据此宣称 ACR008 能抓到真实 cache identity 故障。
- ACR008 仍保持 `experimental`；这条只证明“少了一个误报”，不证明正向检测能力。
- 该文件同时被 ACR007 报出 `rewrite_resume` 返回未声明键；那是另一个规则，不在本页结论内。
