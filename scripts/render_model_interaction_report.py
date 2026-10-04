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
> **事后核实的有效性限制：** m08 的“正常参考”遗漏非法 JSON 接受缺陷，m02 遗漏 Python 字符串的存储/往返边界并有 payload 域歧义，原隐藏验收均未覆盖。下文正常误报计数依冻结标签机械计算，不能解释为真实误报率；任何机器筛查门槛均不能据此授权版本升级。原始分数、输入和判据没有重写，修订反例另存为开发材料。[发现与复核](EXECUTION_REVIEW.md)。本轮维护决定为保留候选，不升级默认 Skill；具体模型差异须结合完整结果与这些限制解读。
>
> **执行修订披露：** 原执行器在 64 次尝试后把日志文件名中的 `401` 误判为鉴权失败；该次实际超时且未有效交付，旧记录与下文 infrastructure 计数仍保留，不能当作真实鉴权故障。后续仅允许按[单独恢复登记](RECOVERY_PLAN.md)继续原调度中 80 个从未开始的任务，最多仍为 144 次，没有重跑旧任务；当前实际完成数量以下表为准。
{END}

'''
    path.write_text(note + source)
    print('Added post-freeze normal-control disclosure; frozen scores unchanged.')


if __name__ == '__main__':
    main()
