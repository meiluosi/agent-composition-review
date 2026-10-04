# 可选辅助工具

`./run_python scripts/composition.py validate MODEL.json` 检查声明的结构格式与引用。
`./run_python scripts/composition.py plan MODEL.json` 列出声明对应的检查义务。
`./run_python scripts/composition.py generate MODEL.json --limit N` 从可选 probes 定义展开有限输入。
`./run_python scripts/composition.py check MODEL.json TRACE.json` 检查声明与动作记录的关系。

`./run_python scripts/check_evidence.py RECORD.json` 检查证据记录的引用和状态一致性。

输入接口可查看脚本或命令帮助。它们不自动提取 app.py 的语义，也不判定业务实现是否正确。工具可选；可直接编写 Python 验证。
