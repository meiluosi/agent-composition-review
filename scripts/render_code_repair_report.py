#!/usr/bin/env python3
"""Render existing frozen scores and separately labeled post-hoc findings."""
import json
from run_code_repair_eval import ROOT,RUN,ARMS,digest,verify


def render():
    manifest,cases=verify(check_cli=False)
    s=json.loads((RUN/'summary.json').read_text());extra=json.loads((RUN/'boundary-audit.json').read_text())
    recovery=json.loads((RUN/'review-recovery-2026-10-04/summary.json').read_text())
    assert (RUN/'CURATOR_REVIEW.md').exists(), 'Finish curator review before rendering the report'
    rows=[json.loads((RUN/'results'/digest(j)[:20]/'record.json').read_text()) for j in manifest['schedule']]
    lines=['# 自主建模与跨运行机制修复：开发试验报告','',
      '**原冻结行为验收、独立结构评审、事后补充探针分别报告。不得拼成统一质量分数。**','',
      '本轮从原始代码和需求出发，直接修改实现、自行交付结构模型。三个运行机制各两个变体，三组各重复两次，共 36 次修复尝试。全部为开发材料，图家族此前已见过。','',
      '**额度故障：30 次修复有效完成，6 次中断；原 36 次盲评全部失败，没有语义判定。** 失败仍计入原计划分母。2026-10-04 另行注册评审恢复批次，对全部 31 份可解析模型重新发起独立评审；不补跑代码任务。CLI 版本不同，两个批次不能混写。5 次缺少模型，1 份模型来自未完成的 B/c03/2。','',
      '**重要更正：c04 和 c06 的预设正常标签不成立。** c04 漏掉提交失败后的撤销；c06 漏掉清理与取消的交错。原输入、调度、验收和分数不变。两个任务的修改不能被解释为误改正常实现；`summary.json` 中按旧标签计算的初始判断匹配数和正常不修改数也不能当作正确率或误报率。','',
      '## 原冻结结果','',
      '| 条件 | 有效完成 | 全部原行为检查通过 | 完成任务的模型格式有效 | 原盲评 |',
      '| --- | ---: | ---: | ---: | --- |']
    for arm in ARMS:
        x=s['conditions'][arm]
        lines.append(f"| {arm} | {x['status_counts'].get('completed',0)}/12 | {x['behavior_pass']}/12 | {x['structure_valid']}/12 | 不可用：12 次额度失败 |")
    lines+=['','A 为基本工具；B 加确定性契约工具；C 在 B 上增加 v4 指导与按需参考，包括运行包自带的通用检索示例。没有提供当前任务的参考模型。全部共同要求 model.json，所以不是自然触发测试。评审 covered 表示满足有限的机制判据及范围说明，不是数学证明或完整行为正确。无效执行保留在计划分母中。','',
      '![原验收与额度失败，固定任务顺序累计](../../../assets/code-repair-audited.png)','',
      '累计曲线按 c01–c06、每例两个重复排序；这不是训练曲线。不能与此前外部归因题或配置修复分数拼成提升曲线。','',
      '原自动图和原统计仍保留，但其中原评审的 0/12 代表没有有效评审，不能解释为零语义覆盖率。30 次有效完成任务均通过原有限检查，并不意味着完整契约已满足。','',
      '## 独立结构评审恢复批次','',
      f"请求模型仍为 gpt-6-astra/high，CLI 为 {recovery['cli_version']}；原 CLI 是 0.158.0-alpha.2.1。评审者只看原冻结输入包，不看组别和事后探针。文风可能透露指导风格，所以仍是部分盲评。",'',
      '| 条件 | 有效评审 / 可评模型 | 完成修复产物：三项均 covered | 所有产物的判据标签 |','| --- | ---: | ---: | --- |']
    for arm in ARMS:
        x=recovery['conditions'][arm]
        lines.append(f"| {arm} | {x['valid']}/{x['eligible']} | {x['all_covered_completed_repair']}/{x['completed_repair_eligible']} | {json.dumps(x['criteria_counts_all_artifacts'])} |")
    lines+=['','B 多出的一份是中断任务的中间产物，其评审不能升级原任务状态。covered 表示满足预设的有限机制判据；维护者的事后反例复核另列，不能当作这些评审已经看到的证据。','',
      '## 逐任务原行为验收','',
      '| 任务 | 机制及原标签 | A_basic | B_tools | C_v4 |','| --- | --- | ---: | ---: | ---: |']
    for cid,c in sorted(cases.items()):
        vals=[]
        for arm in ARMS:
            subset=[r for r in rows if r['case_id']==cid and r['condition']==arm]
            vals.append(f"{sum(r['status']=='completed' and r['acceptance']['passed'] for r in subset)}/2")
        label=c['variant']+('（标签已证伪）' if cid in ('c04','c06') else '')
        lines.append('| '+ ' | '.join([cid,c['family']+' / '+label,*vals])+' |')
    lines+=['','配对差值先在每个机制内平均变体和重复，只有三个家族。下列重采样范围枚举 27 种抽取三个家族的有放回组合，仅是敏感性描述，不能作为可靠总体置信区间。','',
      '| 对比 | 三个家族的差值 | 均值 | 27 种重采样范围 |','| --- | --- | ---: | --- |']
    for name,x in s['paired_family_differences'].items():
        vals=', '.join(f'{v*100:+.1f}' for v in x['family_differences']);low,high=x['bootstrap_27_range']
        lines.append(f"| {name} | {vals} pp | {x['mean']*100:+.1f} pp | [{low*100:+.1f}, {high*100:+.1f}] pp |")
    lines+=['','## 事后边界探针','',
      '**本节由已观察到的代码和缺陷启发，没有预注册，不用于宣称 Skill 增益。** 统一检查所有事务和异步提交，原分数不被替换。异步探针含两个清理/取消交错；事务探针检查提交被读锁阻止后批次撤销与连接复用。','',
      '| 机制 | A_basic | B_tools | C_v4 |','| --- | ---: | ---: | ---: |']
    for family,values in extra['conditions'].items():
        lines.append('| '+ ' | '.join([family,*[f"{values[a]['pass']}/{values[a]['completed_n']}（计划 {values[a]['pass']}/{values[a]['n']}）" for a in ARMS]])+' |')
    lines+=['','每格先列有效完成产物的结果，括号保留全部计划分母；每组每机制有一次额度失败。有效正常控制只剩图任务 c02；每组两个重复，不能估计通用误报率。修订的事务/异步正常参考通过原验收和新增探针，但尚未作为新输入重做模型对照。','',
      '| c02 正常控制 | 原行为验收通过 | 源文件不变 |','| --- | ---: | ---: |']
    for arm,x in extra['valid_normal_control_c02'].items():lines.append(f"| {arm} | {x['behavior_pass']}/{x['n']} | {x['unchanged']}/{x['n']} |")
    lines+=['','## 实际资源记录','',
      '| 条件 | 已记录修复输入 token | 其中缓存输入 | 已记录修复输出 token | 全部尝试墙钟中位数 |','| --- | ---: | ---: | ---: | ---: |']
    for arm in ARMS:
        x=s['conditions'][arm];u=x['repair_tokens']
        lines.append(f"| {arm} | {u['input_tokens']:,} | {u['cached_input_tokens']:,} | {u['output_tokens']:,} | {x['median_seconds']:.1f}s |")
    lines+=['','| 条件 | 恢复评审输入 token | 恢复评审输出 token | 原修复工具执行命令匹配运行数 |','| --- | ---: | ---: | ---: |']
    for arm in ARMS:
        x=s['conditions'][arm];u=recovery['conditions'][arm]['tokens']
        lines.append(f"| {arm} | {u['input_tokens']:,} | {u['output_tokens']:,} | {x['tool_execution_command_runs']}/12 |")
    missing={a:{k:s['conditions'][a][k] for k in ('repair_usage_missing','review_usage_missing')} for a in ARMS}
    lines+=['','资源数据直接使用 CLI 记录。6 次失败修复和全部原评审缺少用量，不能把缺失当成零成本；列出的修复总量是不完整的已记录总量。输入含缓存部分，不等于收费 token；费用未知。墙钟含额度失败、模型与工具等待，不是纯服务延迟。恢复评审各组产物数量不同，不直接比较总成本高低。工具统计为命令文本匹配的代理指标，不是工具效用证明。原批次缺失用量条目：`'+json.dumps(missing)+'`。','',
      '## 复核入口','',
      '- [冻结方案](ANALYSIS_PLAN.md) 与 [清单](manifest.json)。',
      '- [原汇总](summary.json) 与 [事后边界审计](boundary-audit.json)。',
      '- [原额度故障证据](infrastructure-audit.json) 与 [评审恢复协议](review-recovery-2026-10-04/PROTOCOL.md)、[恢复汇总](review-recovery-2026-10-04/summary.json)。',
      '- [维护者复核](CURATOR_REVIEW.md)：保留盲评原标签，并记录补充证据或分歧。',
      '- [复现说明](../../../docs/code-repair-evaluation.md) 与 [完成边界分析](../../../docs/completion-boundaries.md)。','',
      '逐次产物位于 results/，每个目录包含提交代码、模型、测试、补丁、命令审计和评审记录。原始服务日志留在本地忽略目录；公开记录保留其哈希。所有实验失败、无效输出和修订说明必须保留，不按结果补跑。','']
    (RUN/'REPORT.md').write_text('\n'.join(lines))
    plot_availability(rows)
    print('Rendered report from frozen summary plus explicitly separate post-hoc audit.')


def plot_availability(rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#687582','#23957d','#7653b3'];labels=['Basic','+ Contract tools','+ v4 guidance']
    fig,axes=plt.subplots(1,2,figsize=(12,4.8))
    for i,arm in enumerate(ARMS):
        rs=sorted([r for r in rows if r['condition']==arm],key=lambda r:(r['case_id'],r['trial']))
        passed=sum(r['status']=='completed' and r['acceptance']['passed'] for r in rs)
        unavailable=sum(r['status']!='completed' for r in rs)
        axes[0].bar(i,passed,color=colors[i]);axes[0].bar(i,unavailable,bottom=passed,color='#d8dde3',hatch='//')
        axes[0].text(i,passed/2,f'{passed} pass',ha='center',color='white')
        axes[0].text(i,passed+unavailable/2,f'{unavailable} quota',ha='center',fontsize=9)
        values=[r['status']=='completed' and r['acceptance']['passed'] for r in rs]
        axes[1].plot(range(1,13),[sum(values[:n])/n for n in range(1,13)],marker=['o','s','^'][i],markersize=8-2*i,
                     markerfacecolor='none',color=colors[i],label=labels[i],alpha=.9)
    axes[0].set_xticks(range(3),labels);axes[0].set_ylim(0,13);axes[0].set_ylabel('Planned attempts per condition');axes[0].set_title('Frozen behavior checks + unavailable attempts')
    axes[1].axhline(1,color='black',linestyle='--',linewidth=1,label='Completed repairs only: 100%')
    axes[1].set_ylim(.65,1.08);axes[1].set_xticks([2,4,6,8,10,12],['c01','c02','c03','c04','c05','c06'])
    axes[1].set_ylabel('Pass rate / planned attempts');axes[1].set_title('Fixed case order; all condition curves overlap');axes[1].legend(fontsize=8,loc='lower left')
    fig.suptitle('Autonomous repair: 30 completed, 6 quota failures · development only',fontsize=13)
    fig.text(.5,.015,'Quota failures are not behavior failures. Controls c04/c06 were defective; post-hoc probes and recovered reviews are separate.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.05,1,.94))
    fig.savefig(ROOT/'assets/code-repair-audited.png',dpi=160);fig.savefig(ROOT/'assets/code-repair-audited.svg');plt.close(fig)


if __name__=='__main__':render()
