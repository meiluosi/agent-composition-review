#!/usr/bin/env python3
"""Attach post-freeze validity disclosure without changing frozen scores."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'evals/iterations/model-interaction-2026-10-04'
START = '<!-- model-interaction-validity:start -->'
END = '<!-- model-interaction-validity:end -->'


def main():
    path = RUN / 'REPORT.md'
    source = path.read_text()
    if START in source:
        source = source.split(END, 1)[1].lstrip()
    note = f'''{START}
> **事后核实的有效性限制：** m08 的“正常参考”含已证实的非法 JSON 接受缺陷，原隐藏验收未覆盖。下文正常误报计数依冻结标签机械计算，不能解释为真实误报率；任何机器筛查门槛均不能据此授权版本升级。原始分数、输入和判据没有重写，修订反例另存为开发材料。[发现与复核](EXECUTION_REVIEW.md#运行中发现的-m08-正常标签错误)。本轮维护决定为保留候选，不升级默认 Skill；具体模型差异须结合完整结果与该限制解读。
{END}

'''
    path.write_text(note + source)
    print('Added post-freeze normal-control disclosure; frozen scores unchanged.')


if __name__ == '__main__':
    main()
