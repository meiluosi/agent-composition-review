"""3 分钟演示：先复现故障，再验证最小修复。

    .venv/bin/python examples/langgraph-subgraph-leak/run.py

退出码：0 = broken 按预期失败且 fixed 输出正确；1 = 任一预期未满足。
"""

import sys

from langgraph.errors import InvalidUpdateError

from broken import build_broken_graph
from fixed import build_fixed_graph

INPUT = {"a": 1, "b": "old", "s1": 0, "s2": 0}
EXPECTED = {"a": 1, "b": "old|updated", "s1": 4, "s2": -2}


def main() -> int:
    print("=" * 64)
    print("1) broken.py：子图未声明 output_schema，回传完整状态")
    print("=" * 64)
    try:
        build_broken_graph().invoke(INPUT)
    except InvalidUpdateError as exc:
        print(f"按预期失败：{type(exc).__name__}: {exc}")
        if "At key 'b'" not in str(exc):
            print("失败：错误类型正确，但冲突键不是 'b'。", file=sys.stderr)
            return 1
    except Exception as exc:  # 类型不符合预期同样算复现失败
        print(f"失败：抛出的是 {type(exc).__name__}，不是 InvalidUpdateError。", file=sys.stderr)
        print(f"      {exc}", file=sys.stderr)
        return 1
    else:
        print("失败：broken 版本没有抛出 InvalidUpdateError。", file=sys.stderr)
        return 1

    print()
    print("=" * 64)
    print("2) fixed.py：子图声明 output_schema=ChildOutput，只写 s1/s2")
    print("=" * 64)
    try:
        result = build_fixed_graph().invoke(INPUT)
    except Exception as exc:
        print(f"失败：fixed 版本仍然抛错：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"结果：  {result}")
    print(f"期望：  {EXPECTED}")
    if result != EXPECTED:
        print("失败：fixed 结果与期望不一致。", file=sys.stderr)
        return 1

    print()
    print("通过：broken 复现 InvalidUpdateError (At key 'b')，fixed 输出正确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
