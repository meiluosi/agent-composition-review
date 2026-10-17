# Post-fix re-scan（开发集，2026-10-17）

原始 `REPORT.md` 记录的是 **acr 0.1.0** 的试点结果，保持冻结不改。之后做了两个 P0 修复，这一页是同一批开发案例上的**事后复扫**，用来看修复是否生效；不是新的保留集，也不宣称泛化。

## 改了什么

1. **ACR001 跨函数解析**：`scan.py` 现在能识别 helper 返回 compiled graph 的形态，例如 `add_node("child", build_child())` 和 `child = build_child()`；只处理唯一、静态可见的 `return child.compile()` / `return child`，不猜动态构造。
2. **reducer 限定符解包**：`_annotated_parts` 除了 `Required`/`NotRequired`，也会解包 `typing_extensions.ReadOnly`，再判断 `Annotated[..., reducer]`。

## 复扫结果（同一批 14 个 issue + 3 个控制）

| case | 0.1.0 | 修复后 | 说明 |
| --- | --- | --- | --- |
| `lg-6446` | 0 findings | **ACR001 + ACR002** | 唯一可表达案例现在命中；两条指向同一根因（子图输出越界 + 并行写 b） |
| `lg-9311` | ACR002（误导） | **0 findings** | reducer 在 `ReadOnly[Annotated[..., operator.add]]` 中，不再误判“没有 reducer” |
| `control-output-schema` | 0 | 0 | 正常控制仍不误报 |
| `control-reducer` | 0 | 0 | 正常控制仍不误报 |
| `control-disjoint` | 0 | 0 | 正常控制仍不误报 |
| 其余 12 个 issue | 0（或顺带 ACR001） | 不变 | 仍属试点报告里的 GAP/框架内部语义/材料不足，未被这次修复覆盖 |

## 怎么复现

```sh
for d in evals/realworld/cases/*/; do
  .venv/bin/acr scan "$d" --fail-on never
done
```

## 边界

- 这 14 个案例已经用于发现缺口，不能当“未见保留集”使用；这里只说明修复对开发案例生效。
- `lg-9311` 仍按盲标是 `GAP:reducer-semantics`（框架行为），修复只是消除了 ACR 的误导性 finding，不等于覆盖了该缺口。
- `lg-6446` 的 ACR001 命中是本次修复的目标；ACR002 是同一结构的附带提醒。
- 其余 GAP（resume/replay、parallel interrupt、cache/retry 身份、declared-keys、Command/Send、error handler）仍未覆盖，见 `REPORT.md` 的 v0.2 建议。
