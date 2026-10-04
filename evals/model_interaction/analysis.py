"""Predeclared descriptive estimates and development gates, not significance tests."""
from collections import Counter, defaultdict
import itertools
import statistics


def success(row):
    return row['status'] == 'completed' and row['acceptance']['passed'] is True


def verdict(row):
    value = row.get('prediction')
    return value.get('initial_verdict') if isinstance(value, dict) else None


def mean(values):
    return statistics.mean(values) if values else None


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (position - lower) * (values[upper] - values[lower])


def summarize(rows, cases, manifest, arms, models):
    schedule = manifest['schedule']
    families = sorted({c['family'] for c in cases.values()})
    by_group = {}
    for model in models:
        by_group[model] = {}
        for arm in arms:
            group = [r for r in rows if r['model_requested'] == model and r['condition'] == arm]
            planned = sum(j['model_requested'] == model and j['condition'] == arm for j in schedule)
            normal = [r for r in group if cases[r['case_id']]['variant'] == 'normal']
            complete = [r for r in group if r['status'] == 'completed']
            by_group[model][arm] = {
                'planned': planned, 'attempted': len(group), 'pending': planned - len(group),
                'completed': len(complete), 'passed': sum(success(r) for r in group),
                'pass_rate_attempted': mean([success(r) for r in group]),
                'source_behavior_passed': sum(r['acceptance']['passed'] is True for r in group),
                'normal_attempted': len(normal),
                'normal_behavior_regressions': sum(not r.get('infra_kind') and r.get('source_changed') is not None
                                                   and r['acceptance']['passed'] is not True for r in normal),
                'normal_behavior_unavailable': sum(bool(r.get('infra_kind')) or r.get('source_changed') is None for r in normal),
                'normal_false_positives': sum(verdict(r) == 'violation' for r in normal),
                'normal_insufficient': sum(verdict(r) == 'insufficient' for r in normal),
                'normal_missing_verdict': sum(verdict(r) not in ('violation', 'satisfied', 'insufficient') for r in normal),
                'normal_source_changes': sum(r.get('source_changed') is True for r in normal),
                'normal_source_missing': sum(r.get('source_changed') is None for r in normal),
                'initial_verdict_correct': sum(r['status'] == 'completed' and verdict(r) == ('satisfied' if cases[r['case_id']]['variant'] == 'normal' else 'violation') for r in group),
                'by_variant': {variant: {
                    'attempted': sum(cases[r['case_id']]['variant'] == variant for r in group),
                    'passed': sum(success(r) for r in group if cases[r['case_id']]['variant'] == variant),
                } for variant in ('incident', 'normal', 'local')},
                'status_counts': dict(Counter(r['status'] for r in group)),
                'usage_missing': sum(r.get('usage') is None for r in group),
                'tokens': {key: sum((r.get('usage') or {}).get(key, 0) for r in group)
                           for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
                'median_seconds_all_attempts': statistics.median([r['elapsed_seconds'] for r in group]) if group else None,
                'median_seconds_completed': statistics.median([r['elapsed_seconds'] for r in complete]) if complete else None,
            }

    # The same case and repeat must be represented in all six cells.
    blocks = defaultdict(dict)
    for r in rows:
        blocks[(r['case_id'], r['trial'])][(r['model_requested'], r['condition'])] = r
    expected_cells = set(itertools.product(models, arms))
    paired = {key: block for key, block in blocks.items() if set(block) == expected_cells}
    per_family = {}
    for family in families:
        by_case = defaultdict(list)
        for (case_id, _), block in paired.items():
            if cases[case_id]['family'] == family:
                by_case[case_id].append(block)
        per_family[family] = {
            model: {arm: mean([mean([success(block[(model, arm)]) for block in case_blocks])
                               for case_blocks in by_case.values()]) for arm in arms}
            for model in models
        }

    comparisons = {}

    def describe(values):
        observed = [value for value in values if value is not None]
        if len(observed) != len(families):
            return {'family_order': families, 'family_differences': values, 'mean': None,
                    'positive_families': sum(v > 0 for v in observed), 'resampling_percentile_95_descriptive': None,
                    'note': 'Missing fully paired family: no pooled difference.'}
        draws = [statistics.mean(items) for items in itertools.product(observed, repeat=len(observed))]
        return {'family_order': families, 'family_differences': values, 'mean': mean(observed),
                'positive_families': sum(v > 0 for v in observed),
                'resampling_percentile_95_descriptive': [percentile(draws, .025), percentile(draws, .975)],
                'resample_count': len(draws),
                'note': 'Four authored development families; empirical sensitivity only, not a reliable population confidence interval.'}

    for model in models:
        comparisons[model] = {}
        for left, right in [('C_skill', 'A_tools'), ('C_skill', 'B_checklist'), ('B_checklist', 'A_tools')]:
            values = [None if per_family[f][model][left] is None else per_family[f][model][left] - per_family[f][model][right] for f in families]
            comparisons[model][left + '_minus_' + right] = describe(values)
    interaction = []
    for index in range(len(families)):
        lower = comparisons['gpt-6-luna']['C_skill_minus_A_tools']['family_differences'][index]
        stronger = comparisons['gpt-6-astra']['C_skill_minus_A_tools']['family_differences'][index]
        interaction.append(None if lower is None or stronger is None else lower - stronger)

    complete_schedule = len(rows) == len(schedule)
    blockers = [r for r in rows if r.get('infra_kind') or r['status'] in ('infrastructure_failure', 'workspace_violation', 'runner_error')]
    eligible = complete_schedule and not blockers
    gates = {}
    for model in models:
        group = by_group[model]
        delta = comparisons[model]
        def normals_preserved(candidate, baselines):
            return all(group[candidate][key] <= group[baseline][key]
                       for key in ('normal_behavior_regressions', 'normal_behavior_unavailable', 'normal_false_positives', 'normal_insufficient', 'normal_missing_verdict')
                       for baseline in baselines)
        def meets(name, threshold, families_needed=0):
            value = delta[name]
            return value['mean'] is not None and value['mean'] + 1e-12 >= threshold and value['positive_families'] >= families_needed
        skill = (eligible and meets('C_skill_minus_A_tools', 3/24, 2)
                 and meets('C_skill_minus_B_checklist', 2/24) and normals_preserved('C_skill', ['A_tools', 'B_checklist']))
        checklist = (eligible and meets('B_checklist_minus_A_tools', 3/24, 2)
                     and normals_preserved('B_checklist', ['A_tools']) and not skill)
        gates[model] = {'skill_confirmatory_candidate': bool(skill), 'checklist_confirmatory_candidate': bool(checklist)}
    saturated = eligible and all(by_group[model]['A_tools']['passed'] >= 22 for model in models)
    if not eligible:
        decision = 'incomplete_or_infrastructure_confounded_no_promotion'
    elif any(g['skill_confirmatory_candidate'] for g in gates.values()):
        decision = 'signal_for_separately_registered_skill_holdout_no_promotion'
    elif any(g['checklist_confirmatory_candidate'] for g in gates.values()):
        decision = 'prefer_checklist_for_separately_registered_holdout_no_equivalence_claim'
    elif saturated:
        decision = 'stop_screen_saturated_no_effectiveness_conclusion'
    else:
        decision = 'stop_expanding_current_guidance_no_practical_signal_no_zero_effect_claim'
    attempted_differences = {}
    for model in models:
        attempted_differences[model] = {}
        for left, right in [('C_skill', 'A_tools'), ('C_skill', 'B_checklist'), ('B_checklist', 'A_tools')]:
            lrate = by_group[model][left]['pass_rate_attempted']
            rrate = by_group[model][right]['pass_rate_attempted']
            attempted_differences[model][left + '_minus_' + right] = None if lrate is None or rrate is None else lrate - rrate
    return {
        'development_only': True, 'planned': len(schedule), 'attempted': len(rows), 'pending': len(schedule) - len(rows),
        'fully_paired_blocks': len(paired), 'block_unit': 'case and repeat; six model-condition cells',
        'conditions': by_group, 'paired_family_pass_rates': per_family, 'comparisons': comparisons,
        'attempted_rate_differences_unpaired': attempted_differences,
        'luna_minus_astra_skill_increment': describe(interaction), 'gate_eligible': eligible,
        'infrastructure_or_isolation_blockers': len(blockers), 'development_gates': gates,
        'both_baselines_at_least_22_of_24': bool(saturated), 'decision': decision,
    }
