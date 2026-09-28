#!/usr/bin/env python3
"""Publish the completed pilot's measured tables and figures into README."""

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RESULTS=ROOT/"evals/external/results/pilot-2026-09-29"
START="<!-- external-pilot:start -->"
END="<!-- external-pilot:end -->"


def pct(value): return f"{100*value:.1f}%"


def main():
    summary=json.loads((RESULTS/"summary.json").read_text())
    assert summary["recorded_runs"]==summary["planned_runs"]==144
    for name in ["external-pilot-accuracy.png","external-pilot-cumulative.png","external-pilot-difference.png"]:
        assert (ROOT/"docs/assets"/name).is_file()
    rows=[]; resource_rows=[]; conclusions=[]
    for name,data in summary["datasets"].items():
        metric=data["metrics"]["location"];delta=metric["paired_difference"]
        lo,hi=delta["ci95"]
        rows.append(f"| {name} | {data['unique_tasks']} | {pct(metric['without_skill']['mean'])} | {pct(metric['with_skill']['mean'])} | {delta['mean']*100:+.1f} | [{lo*100:+.1f}, {hi*100:+.1f}] |")
        if lo<=0<=hi: conclusions.append(f"{name} 的差值区间包含 0")
        elif hi<0: conclusions.append(f"{name} 在本次样本上的差值区间低于 0")
        else: conclusions.append(f"{name} 在本次样本上的差值区间高于 0")
        for condition,label in [("without_skill","无 Skill"),("with_skill","有 Skill")]:
            r=data["resources"][condition];u=r["usage_totals"]
            resource_rows.append(f"| {name} | {label} | {u['input_tokens']:,} | {u['cached_input_tokens']:,} | {u['output_tokens']:,} | {r['median_elapsed_seconds']:.1f}s |")
    counts=summary["status_counts"]
    outcome=f"完成 {summary['recorded_runs']}/{summary['planned_runs']} 次计划运行；有效完成 {counts.get('completed',0)} 次"
    if counts.get('completed',0)!=144:
        outcome+="；含运行失败，具体分布见结果文件，不能将基础设施失败直接归为方法能力差异"
    status_table="\n".join(rows)
    observed_gain=any(d["metrics"]["location"]["paired_difference"]["mean"]>0 for d in summary["datasets"].values())
    takeaway="本轮未观察到首要指标的提升。" if not observed_gain else "点估计的提升需要结合配对区间解释。"
    degenerate=any(d["metrics"]["location"]["paired_difference"]["ci95"]==[0.0,0.0] for d in summary["datasets"].values())
    interval_note=" 若差值区间为 [0, 0]，表示本次每个任务的两组平均得分恰好相同；小样本的经验 bootstrap 无法由此推断未来差异也为零。" if degenerate else ""
    section=f"""{START}
## 外部基准首轮实验

2026-09-29，**{outcome}**。请求模型为 **gpt-6-astra / high**，每个数据集 12 个任务、每组每例 3 次重复。实验前冻结输入、评分器、Skill 快照和分析方案；运行中未调优 Skill。

首要指标为错误位置严格匹配的准确率。先对同一任务的重复取平均，区间按任务聚类进行 10,000 次配对 bootstrap；不是把 144 次运行当作 144 个独立样本。

| 数据集 | 任务数 | 无 Skill | 有 Skill | 差值（百分点） | 差值的 95% 区间（百分点） |
| --- | ---: | ---: | ---: | ---: | --- |
{status_table}

**{takeaway}** {'；'.join(conclusions)}。这是一项分层抽样的小样本试验，不是全量基准成绩，也不能据此证明生产系统、代码修复或整体架构质量的提升。{interval_note}

![有无 Skill 的归因准确率及任务聚类区间](docs/assets/external-pilot-accuracy.png)

图中 n 为指标适用的任务数；无 Agent 标签时联合指标只检查位置。[查看配对差值图](docs/assets/external-pilot-difference.png)。

下图按预先固定的任务顺序展示累计定位准确率。它反映样本增加时估计如何变化，**不是训练曲线**，没有按结果调整顺序。

![固定任务顺序下的累计定位准确率](docs/assets/external-pilot-cumulative.png)

范围：已下载 Who&When 184 条及 Pro text 6,257 条；这里只评测所列 24 个任务。Pro 的部分位置映射及 Who&When 的超长输入未进入抽样池，详见排除记录。AgentRx 尚受访问授权限制，未运行。所有外部样本都是失败轨迹，不能据此估计正常系统误报率。公开数据也可能已被模型预训练见过。

结果与复现：[完整实验说明](docs/external-evaluation.md) · [冻结清单](evals/external/pilot-manifest.json) · [逐次预测与评分](evals/external/results/pilot-2026-09-29/predictions.json) · [汇总](evals/external/results/pilot-2026-09-29/summary.json) · [资源统计](evals/external/results/pilot-2026-09-29/report.md)。图表可以从公开评分表重画，无需再次调用模型。
{END}
"""
    path=ROOT/"README.md";text=path.read_text()
    if START in text:
        before,tail=text.split(START,1);_,after=tail.split(END,1)
        text=before+section.rstrip()+after
    else:
        anchor="## 仓库结构"
        assert anchor in text
        text=text.replace(anchor,section+"\n"+anchor,1)
    text=text.replace("尚无证据证明它比不加载 Skill 更准确。", "已完成首轮外部数据对照，实际结果与限制见下方评测区。")
    old="规模更大的外部基准已完成初步核查，见 [专业数据集与接入计划](docs/benchmarks.md)：优先考虑 Who&When，后续扩展 Who&When Pro，并在取得访问权限后考虑 AgentRx。目前尚未运行这些基准，不将其规模计入项目成绩。"
    new="外部数据已接入 Who&When 与 Who&When Pro text，并完成下方列出的首轮 144 次对照运行；AgentRx 等待访问授权。来源与覆盖限制见 [专业数据集与接入计划](docs/benchmarks.md)。数据集完整规模不等于本轮已评测样本数。"
    text=text.replace(old,new)
    path.write_text(text)
    report="""# 外部试验结果补充

这份表格由实际运行记录生成。输入 token 包含缓存部分；output_tokens 按 CLI 原样记录，不再额外叠加 reasoning_output_tokens。墙钟时间包括本地启动与网络开销，且以三路并发执行，不能直接当作推理服务纯延迟。货币费用不可见，未估算。

| 数据集 | 条件 | 输入 token 合计 | 其中缓存 token | 输出 token 合计 | 单次墙钟中位数 |
| --- | --- | ---: | ---: | ---: | ---: |
"""+"\n".join(resource_rows)+"\n\n这些资源统计对应各数据集每组 36 次运行。方法、置信区间和局限见 [实验说明](../../../docs/external-evaluation.md)。\n"
    # Keep this report next to the result files; its relative link is four levels up.
    report=report.replace("../../../docs/","../../../../docs/")
    (RESULTS/"report.md").write_text(report)
    print("README now contains measured results, two embedded figures and a paired-difference figure link.")


if __name__=="__main__": main()
