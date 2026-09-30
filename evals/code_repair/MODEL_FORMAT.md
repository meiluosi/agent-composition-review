# 结构交付格式

提交 `model.json`，描述你交付的 `app.py` 中最小的相关结构。类型名、动作 ID 和资源名由你根据代码确定，不能把下面的格式说明当成业务答案。允许只建模相关边界，但在 `scope.limitations` 写清楚省略内容。

根字段：

- `schema_version`: 1。
- `types`: 类型名 → 字段路径约束。每个约束含 `type`，可取 string/number/boolean/object/array/null，可选非空 `enum`。
- `actions`: 列表。每项含 `id`、`input_type`、`output_type`、`reads`、`writes`、`pre`、`post`。读写列表使用你声明的状态资源名；前后条件是列表。
- `edges`: 列表。每项含 `from`/`to` 动作 ID，以及 `bindings` 列表，绑定每项含 `output`/`input` 字段路径。这里表达实际交接；并行或控制/生命周期关系可另列为 `relations`，不要冒充顺序执行。
- `required_actions`: 模型范围内必需动作 ID 列表。
- `scope`: 含 `description`、`limitations`（字符串列表）和 `evidence`（`{path: "app.py", start: 行号, end: 行号, supports: 说明}` 列表）。描述当前提交版本；如果只是需求而非已有保证，明确标注。
- 每个动作额外添加 `meaning`，描述业务含义和对应代码符号。可增加 `relations`、`requirements` 等说明字段。

条件每项含 `id`、`requirement` 和 `must`，可选 `when`。谓词形式为 `{op, args}`：支持 eq/ne/le/ge/contains/subset/unique/and/or。表达式为单键对象：path（事件内字段路径）、literal（字面值）、count（表达式）、pluck（含 value 表达式和 field 字段路径）、scale（含 value 表达式和 factor 数值）。无需为了使用这些运算而虚构可执行数据或证明。

结构检查只能检查声明与引用；它不自动验证业务真相、并发、实际资源访问或因果。可以保留无法编码的要求并明确检查方法。不要用空条件列表表示“已证明无任何约束”。交付里区分已运行检查、推断与尚未验证的性质。
