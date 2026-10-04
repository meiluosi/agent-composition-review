#!/usr/bin/env python3
"""Verify saved artifacts, replay acceptance, and report the frozen cross-model screen."""
import argparse
import json
from pathlib import Path
import statistics

from run_model_interaction import ROOT, RUN, ARMS, MODELS, digest, verify, write
from evals.model_interaction.analysis import summarize, success
from evals.model_interaction.grading import behavioral_check, behavioral_signature
from evals.model_interaction.protocol import final_valid


def load_rows(cases, manifest, regrade=True):
    rows = []
    for job in manifest['schedule']:
        folder = RUN / 'results' / digest(job)[:20]
        record = folder / 'record.json'
        if not record.exists():
            continue
        row = json.loads(record.read_text())
        assert all(row[key] == value for key, value in job.items()), 'Wrong scheduled identity'
        assert row['case_sha256'] == digest(cases[row['case_id']]), 'Case changed'
        assert row['prompt_sha256'] == manifest['prompt_sha256'][row['condition']], 'Prompt changed'
        assert row['cli_version'] == manifest['cli_version'], 'CLI changed'
        assert row['reasoning_effort'] == manifest['reasoning_effort'], 'Reasoning setting changed'
        for name, expected in row['artifacts'].items():
            path = folder / 'artifacts' / name
            assert not path.is_symlink() and path.resolve().is_relative_to((folder / 'artifacts').resolve())
            assert digest(path.read_bytes()) == expected, 'Artifact changed: ' + name
        source_path = folder / 'artifacts/app.py'
        source = source_path.read_text() if source_path.exists() else ''
        if source_path.exists():
            assert row['source_changed'] == (source != cases[row['case_id']]['source'])
        else:
            assert row['source_changed'] is None, 'Missing source must stay unknown'
        if row['status'] == 'completed':
            assert row['completed'] and row['exit_code'] == 0 and not row['timed_out']
            assert final_valid(row['prediction'])
            assert not row['protected_changes'] and not row['outside_changes'] and not row['artifact_errors']
            assert not row.get('infra_kind')
        if regrade and source:
            fresh = behavioral_check(cases[row['case_id']]['family'], source)
            assert behavioral_signature(fresh) == behavioral_signature(row['acceptance']), 'Acceptance changed: ' + str(job)
        rows.append(row)
    return rows


DECISIONS = {
    'incomplete_or_infrastructure_confounded_no_promotion': '计划未完整完成，或有基础设施/隔离问题；不作效果升级决定。',
    'signal_for_separately_registered_skill_holdout_no_promotion': '当前 Skill 达到开发筛查门槛，值得另行冻结保留集确认；本轮不升级默认版本。',
    'prefer_checklist_for_separately_registered_holdout_no_equivalence_claim': '优先将简短工程清单带入另行登记的确认试验；不能据此声称清单与 Skill 等效。',
    'stop_screen_saturated_no_effectiveness_conclusion': '两个模型基线均接近满分；结束本轮，判据缺少区分度，不下 Skill 有效或无效结论。',
    'stop_expanding_current_guidance_no_practical_signal_no_zero_effect_claim': '未达到预先声明的实用收益门槛，结束当前指导版本的追加调用；不继续补充理论以追求正分数，也不声称证明零效应。',
}
ARM_LABELS = {'A_tools': '共同工具', 'B_checklist': '加简短清单', 'C_skill': '加 v4 指导'}
SHORT_MODELS = {'gpt-6-astra': 'Astra', 'gpt-6-luna': 'Luna'}


def pp(number):
    return '未估计' if number is None else f'{number * 100:+.1f}'


def report(rows, cases, manifest, summary):
    lines = [
        '# 模型与指导交互：开发筛查结果', '',
        f"计划 {summary['planned']} 次，已尝试 {summary['attempted']} 次，未开始 {summary['pending']} 次。两个工具连通性 canary 另列，不计入效果。",
        '', '**版本决定：**' + DECISIONS[summary['decision']], '',
        '本轮为四个新编写的标准库任务家族，每个包含组合缺陷、正常实现、节点内部缺陷，两个模型、三个指导条件、每题两次。全部是开发材料；不是外部专业基准或生产泛化结果。', '',
        '## 业务交付与成本记录', '',
        '| 模型 | 条件 | 有效完成 | 首指标通过 / 已尝试（计划 24） | 最终源码行为通过 | 输入 token | 输出 token | 缺失用量 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for model in MODELS:
        for arm in ARMS:
            group = summary['conditions'][model][arm]
            lines.append(f"| {SHORT_MODELS[model]} | {ARM_LABELS[arm]} | {group['completed']} | {group['passed']}/{group['attempted']} | {group['source_behavior_passed']} | {group['tokens']['input_tokens']:,} | {group['tokens']['output_tokens']:,} | {group['usage_missing']} |")
    lines += ['', '首指标要求有效交付且全部隐藏业务验收通过；中断产物即使通过也不算成功。输入包含缓存读取，缓存 token 不再叠加。Codex 登录运行没有可核验单次货币账单；这些数字不代表实际收费。两个模型的相同 high 设置不等于相同算力。', '',
              '![业务结果与家族差异](../../../assets/model-interaction-results.png)', '',
              '## 正常保护与任务种类', '',
              '| 模型 | 条件 | 组合缺陷修复 | 节点内部缺陷修复 | 正常业务通过 | 正常误报 / 不确定 / 无结论 | 正常源码改动 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for model in MODELS:
        for arm in ARMS:
            group = summary['conditions'][model][arm]
            counts = [f"{group['by_variant'][variant]['passed']}/{group['by_variant'][variant]['attempted']}" for variant in ('incident', 'local', 'normal')]
            lines.append(f"| {SHORT_MODELS[model]} | {ARM_LABELS[arm]} | {' | '.join(counts)} | {group['normal_false_positives']} / {group['normal_insufficient']} / {group['normal_missing_verdict']} | {group['normal_source_changes']} |")
    lines += ['', '源码改动不是误报；正常行为回归与口头诊断分开。全部验收通过仅覆盖冻结的输入与义务，不是对所有实现状态的证明。', '',
              '## 预先声明的配对比较', '',
              f"六个模型/条件单元全部有记录的 case×repeat 配对块为 {summary['fully_paired_blocks']}/24。先在家族内平均，再对家族等权；下表使用这些完整块。", '',
              '| 模型 | 比较 | 完整配对差值（百分点） | 全部已尝试的原始差值 | 为正的家族数 / 4 |',
              '| --- | --- | ---: | ---: | ---: |']
    names = {'C_skill_minus_A_tools': 'v4 − 基线', 'C_skill_minus_B_checklist': 'v4 − 简短清单', 'B_checklist_minus_A_tools': '简短清单 − 基线'}
    for model in MODELS:
        for key, label in names.items():
            value = summary['comparisons'][model][key]
            raw_difference = summary['attempted_rate_differences_unpaired'][model][key]
            lines.append(f"| {SHORT_MODELS[model]} | {label} | {pp(value['mean'])} | {pp(raw_difference)} | {value['positive_families']} |")
    lines += ['', f"交互差值 `(Luna v4−基线)−(Astra v4−基线)` 为 **{pp(summary['luna_minus_astra_skill_increment']['mean'])} 个百分点**。正数也只描述这两个指定配置，不是模型越小越有收益的普遍规律。", '',
              '全部已尝试的原始差值在运行不完整时可能受到任务构成不平衡影响。四家族的 256 种重采样敏感性分布保存在 summary.json，不能当可靠总体置信区间。没有显著性或多重比较结论；全满分时退化区间不证明效果相等。', '',
              '![固定任务顺序的累计交付比例](../../../assets/model-interaction-cumulative.png)', '',
              '曲线按预先编号的任务顺序、每题两个重复累积，不是训练曲线。不同家族与正常/故障变体相关，不能把调用数或验收断言数当独立样本数。', '',
              '## 逐题结果', '',
              '| 任务 | 家族 / 变体 | Astra 基线 | Astra 清单 | Astra v4 | Luna 基线 | Luna 清单 | Luna v4 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for case_id, case in sorted(cases.items()):
        scores = []
        for model in MODELS:
            for arm in ARMS:
                selected = [r for r in rows if r['case_id'] == case_id and r['model_requested'] == model and r['condition'] == arm]
                scores.append(f'{sum(success(r) for r in selected)}/{len(selected)}')
        lines.append(f"| {case_id} | {case['family']} / {case['variant']} | {' | '.join(scores)} |")
    lines += ['', '## 解释范围', '',
              '- 当前比较固定共同工具，识别附加指导包效应；所有组都可查看结构检查脚本，因此不能用本轮分离工具自身的价值。',
              '- 共同提示不强制契约建模；C 附带通用检索示例，B 与 C 的长度/内容不匹配，不能将差异单独归因范畴论或某条原则。',
              '- 指导明确加载，不评估自动触发；模型后端精确快照不可见。',
              '- 任务和测试由代理编写并在运行前交叉复核，仍有合成任务偏差与验收遗漏的可能。正常实现不等于已证明完整正确。',
              '- 新进程与工作区限制不能构成恶意执行者的完整读隔离证明；执行记录需要人工/维护者回读。', '',
              '[冻结方案](ANALYSIS_PLAN.md) · [运行前复核](PRECHECK_REVIEW.md) · [冻结清单](manifest.json) · [机器可读汇总](summary.json)', '']
    (RUN / 'REPORT.md').write_text('\n'.join(lines))


def plot(rows, cases, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors = ['#64748b', '#159582', '#8458ba']
    labels = ['Tools baseline', '+ Plain checklist', '+ v4 guidance']
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for i, arm in enumerate(ARMS):
        positions = [model_index + (i - 1) * .25 for model_index in range(2)]
        groups = [summary['conditions'][model][arm] for model in MODELS]
        heights = [g['passed'] / g['attempted'] if g['attempted'] else 0 for g in groups]
        bars = axes[0].bar(positions, heights, width=.23, color=colors[i], label=labels[i])
        axes[0].bar_label(bars, labels=[f"{g['passed']}/{g['attempted']}" for g in groups], fontsize=9)
    axes[0].set_xticks([0, 1], ['Astra / high', 'Luna / high'])
    axes[0].set_ylim(0, 1.16)
    axes[0].set_ylabel('Successful delivery / attempted runs')
    axes[0].set_title('Same tools; different guidance')
    axes[0].legend(loc='lower left', fontsize=8)
    for model_index, model in enumerate(MODELS):
        for contrast_index, contrast in enumerate(['C_skill_minus_A_tools', 'C_skill_minus_B_checklist']):
            value = summary['comparisons'][model][contrast]
            x = model_index * 2 + contrast_index
            valid = [v for v in value['family_differences'] if v is not None]
            for family_index, delta in enumerate(valid):
                axes[1].scatter(x + (family_index - 1.5) * .045, delta * 100, color=colors[model_index + 1], alpha=.55, s=30)
            if value['mean'] is not None:
                axes[1].scatter(x, value['mean'] * 100, color='#172b4d', marker='D', s=52, zorder=4)
    axes[1].axhline(0, color='#94a3b8', linewidth=1)
    axes[1].set_xticks(range(4), ['Astra\nv4 − baseline', 'Astra\nv4 − checklist', 'Luna\nv4 − baseline', 'Luna\nv4 − checklist'])
    axes[1].set_ylabel('Paired difference (percentage points)')
    axes[1].set_title('Dots: families · diamonds: equal-weight mean')
    axes[1].grid(axis='y', alpha=.15)
    fig.suptitle('Model × guidance · 4 authored development families', fontsize=13)
    fig.text(.5, .02, '24 planned runs per cell. Family dots are descriptive; no population confidence or equivalence claim.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, .95))
    for extension in ('png', 'svg'):
        fig.savefig(ROOT / f'assets/model-interaction-results.{extension}', dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    ids = sorted(cases)
    for axis, model in zip(axes, MODELS):
        for i, arm in enumerate(ARMS):
            x_values, values, accumulated = [], [], []
            for index, case_id in enumerate(ids, 1):
                accumulated.extend(r for r in rows if r['model_requested'] == model and r['condition'] == arm and r['case_id'] == case_id)
                if accumulated:
                    x_values.append(index)
                    values.append(statistics.mean(success(r) for r in accumulated))
            axis.plot(x_values, values, marker=['o', 's', '^'][i], markersize=4, color=colors[i], label=labels[i], alpha=.85, linestyle=['-', '--', ':'][i])
        axis.set_title(SHORT_MODELS[model] + ' / high')
        axis.set_xticks(range(1, len(ids) + 1), ids, rotation=45, fontsize=8)
        axis.set_ylim(-.03, 1.08)
        axis.grid(alpha=.15)
        axis.legend(fontsize=8, loc='lower left')
    axes[0].set_ylabel('Cumulative successful delivery / attempted runs')
    fig.suptitle('Fixed case order · not a training curve', fontsize=13)
    fig.text(.5, .02, 'Related variants and repeats are not independent tasks. Pending jobs are excluded and reported separately.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .06, 1, .94))
    for extension in ('png', 'svg'):
        fig.savefig(ROOT / f'assets/model-interaction-cumulative.{extension}', dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-regrade', action='store_true', help='Preview only; final publication and CI must regrade.')
    args = parser.parse_args()
    manifest, cases = verify(check_cli=False)
    rows = load_rows(cases, manifest, regrade=not args.no_regrade)
    summary = summarize(rows, cases, manifest, ARMS, MODELS)
    write(RUN / 'summary.json', summary)
    plot(rows, cases, summary)
    report(rows, cases, manifest, summary)
    print(json.dumps({'attempted': summary['attempted'], 'pending': summary['pending'], 'decision': summary['decision']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
