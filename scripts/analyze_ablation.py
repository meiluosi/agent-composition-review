#!/usr/bin/env python3
"""Score complete frozen ablation records or redraw public evidence without model calls."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.external.core import digest, score
from scripts.run_ablation import RUN, EXT, CONDITIONS

LABELS = ["A · No skill", "B · V1 full", "C · V1 core", "D · V2 core"]
COMPARISONS = {"C_minus_B": (1, 2), "D_minus_C": (2, 3), "D_minus_A": (0, 3), "B_minus_A": (0, 1)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def intervals(matrix):
    matrix = np.asarray(matrix, dtype=float)
    assert matrix.ndim == 2 and matrix.shape[1] == 4 and len(matrix)
    rng = np.random.default_rng(20260930)
    draws = matrix[rng.integers(0, len(matrix), size=(10000, len(matrix)))].mean(axis=1)
    means = matrix.mean(axis=0)
    result = {"tasks": len(matrix), "conditions": {}, "comparisons": {}}
    for i, arm in enumerate(CONDITIONS):
        result["conditions"][arm] = {"mean": float(means[i]), "ci95": np.quantile(draws[:, i], [.025, .975]).tolist()}
    for name, (a, b) in COMPARISONS.items():
        result["comparisons"][name] = {"mean": float(means[b] - means[a]),
                                      "ci95": np.quantile(draws[:, b] - draws[:, a], [.025, .975]).tolist()}
    return result


def aggregate(manifest, rows):
    assert len(rows) == len(manifest["schedule"]) == 96
    per_case = {}
    for c in manifest["cases"]:
        cid = c["case_id"]; per_case[cid] = {}
        for arm in CONDITIONS:
            group = [r for r in rows if r["case_id"] == cid and r["condition"] == arm]
            assert len(group) == manifest["trials"]
            per_case[cid][arm] = {}
            for metric in ["location", "agent", "joint", "error_mode"]:
                vals = [r["scores"][metric] for r in group]
                assert all(v is None for v in vals) or all(v is not None for v in vals)
                per_case[cid][arm][metric] = None if vals[0] is None else sum(bool(v) for v in vals) / len(vals)
    scopes = {"fixed_development_set": [c["case_id"] for c in manifest["cases"]]}
    scopes.update({d: [c["case_id"] for c in manifest["cases"] if c["dataset"] == d]
                   for d in sorted({c["dataset"] for c in manifest["cases"]})})
    statistics = {}
    for scope, ids in scopes.items():
        statistics[scope] = {}
        for metric in ["location", "agent", "joint", "error_mode"]:
            matrix = [[per_case[cid][a][metric] for a in CONDITIONS] for cid in ids]
            for row in matrix: assert all(x is None for x in row) or all(x is not None for x in row)
            matrix = [row for row in matrix if row[0] is not None]
            if matrix: statistics[scope][metric] = intervals(matrix)
    resources = {}
    for arm in CONDITIONS:
        group = [r for r in rows if r["condition"] == arm]
        resources[arm] = {"runs": len(group), "status_counts": dict(Counter(r["status"] for r in group)),
                          "median_elapsed_seconds": median(r["elapsed_seconds"] for r in group),
                          "usage_missing_runs": sum(r["usage"] is None for r in group), "cost": None,
                          "usage_totals": {key: sum((r["usage"] or {}).get(key, 0) for r in group)
                                           for key in ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]}}
    return {"statistics": statistics, "per_case": per_case, "resources": resources}


def score_records():
    manifest = json.loads((RUN / "manifest.json").read_text())
    for key, path in [("core_sha256", EXT / "core.py"), ("runner_sha256", ROOT / "scripts/run_ablation.py"),
                      ("analysis_plan_sha256", RUN / "ANALYSIS_PLAN.md"), ("source_pilot_sha256", EXT / "pilot-manifest.json")]:
        assert digest(path.read_bytes()) == manifest[key]
    cases = {c["case_id"]: c for c in manifest["cases"]}
    oracles = {r["case_id"]: r["gold"] for r in json.loads((EXT / "private/oracles.json").read_text())}
    for cid, c in cases.items(): assert digest(oracles[cid]) == c["oracle_sha256"]
    rows = []
    for job in manifest["schedule"]:
        path = RUN / "raw-runs" / digest(job)[:20] / "record.json"
        if not path.exists(): raise SystemExit(f"Incomplete experiment: {job}; no scores published.")
        r = json.loads(path.read_text())
        assert all(r[k] == v for k, v in job.items())
        assert datetime.fromisoformat(r["started_at"]) >= datetime.fromisoformat(manifest["frozen_at"])
        assert r["prompt_sha256"] == cases[job["case_id"]]["prompt_sha256"][job["condition"]]
        for key in ["cli_version", "model_requested", "reasoning_effort"]: assert r[key] == manifest[key]
        for filename, key in [("events.jsonl", "event_sha256"), ("stderr.txt", "stderr_sha256")]:
            assert digest((path.parent / filename).read_bytes()) == r[key]
        pred = r["prediction"] if r["status"] == "completed" else None
        rows.append({**job, "dataset": cases[job["case_id"]]["dataset"],
                     "prediction": None if pred is None else {k: v for k, v in pred.items() if k != "reason"},
                     "reason_sha256": None if pred is None else digest(pred["reason"].encode()),
                     "scores": score(pred, oracles[job["case_id"]]),
                     **{k: r[k] for k in ["status", "started_at", "elapsed_seconds", "usage", "model_requested",
                                          "model_snapshot", "reasoning_effort", "cli_version", "prompt_sha256",
                                          "event_sha256", "stderr_sha256", "tool_item_count"]},
                     "private_record_sha256": digest(path.read_bytes())})
    assert len({(r["case_id"], r["condition"], r["trial"]) for r in rows}) == 96
    write_json(RUN / "predictions.json", rows)
    summary = {"kind": "development_ablation_not_holdout", "planned_runs": 96, "recorded_runs": len(rows),
               "status_counts": dict(Counter(r["status"] for r in rows)),
               "manifest_sha256": digest((RUN / "manifest.json").read_bytes()),
               "predictions_sha256": digest((RUN / "predictions.json").read_bytes()),
               "analysis_script_sha256": digest(Path(__file__).read_bytes()),
               "limitations": manifest["limitations"], **aggregate(manifest, rows)}
    write_json(RUN / "summary.json", summary)
    print(json.dumps({"status": summary["status_counts"],
                      "location": summary["statistics"]["fixed_development_set"]["location"]}, indent=2))


def publish():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    manifest = json.loads((RUN / "manifest.json").read_text())
    rows = json.loads((RUN / "predictions.json").read_text())
    summary = json.loads((RUN / "summary.json").read_text())
    assert digest((RUN / "manifest.json").read_bytes()) == summary["manifest_sha256"]
    assert digest((RUN / "predictions.json").read_bytes()) == summary["predictions_sha256"]
    for k, v in aggregate(manifest, rows).items(): assert v == summary[k]
    stats = summary["statistics"]["fixed_development_set"]["location"]
    colors = ["#8996A8", "#315B78", "#438C87", "#CD7B36"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "svg.hashsalt": "composition-dev-ablation-20260929"})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.9))
    values = np.array([stats["conditions"][a]["mean"] * 100 for a in CONDITIONS])
    cis = np.array([stats["conditions"][a]["ci95"] for a in CONDITIONS]) * 100
    axes[0].bar(range(4), values, color=colors, width=.65)
    axes[0].errorbar(range(4), values, yerr=np.maximum(0, np.stack([values-cis[:, 0], cis[:, 1]-values])),
                     fmt="none", ecolor="#253246", capsize=4)
    for x, val in enumerate(values): axes[0].text(x, max(val/2, 3), f"{val:.1f}%", ha="center", color="white", fontweight="bold")
    axes[0].set_xticks(range(4), LABELS); axes[0].set_ylim(0, 105)
    axes[0].set_ylabel("Exact-location accuracy (%)"); axes[0].set_title("Fixed development set · 8 tasks")
    token_values = [summary["resources"][a]["usage_totals"]["input_tokens"] / 24000 for a in CONDITIONS]
    axes[1].bar(range(4), token_values, color=colors, width=.65)
    for x, val in enumerate(token_values): axes[1].text(x, val + .2, f"{val:.1f}k", ha="center")
    axes[1].set_xticks(range(4), LABELS); axes[1].set_ylim(0, max(token_values)*1.18)
    axes[1].set_ylabel("Mean input tokens per run (thousands)"); axes[1].set_title("Measured context cost · 24 runs per condition")
    for ax in axes: ax.grid(axis="y", alpha=.15); ax.set_axisbelow(True)
    fig.suptitle("Four-condition ablation · development evidence only", fontsize=16, fontweight="bold")
    fig.text(.5, .015, "96 new runs · 3 trials per task/condition · GPT-6 Astra / high\nTask-cluster 95% intervals; selected tasks already informed v2. Not a holdout or deployment score.", ha="center", fontsize=9)
    fig.tight_layout(rect=[0, .10, 1, .93])
    assets = ROOT / "docs/assets"; assets.mkdir(exist_ok=True)
    def save(name):
        fig.savefig(assets / (name + ".png"), dpi=180, facecolor="white")
        svg = assets / (name + ".svg")
        fig.savefig(svg, metadata={"Date": None}, facecolor="white")
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
        plt.close(fig)
    save("development-ablation-overview")
    ids = [c["case_id"] for c in manifest["cases"]]
    counts = np.array([[round(summary["per_case"][cid][a]["location"]*3) for a in CONDITIONS] for cid in ids])
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.imshow(counts, cmap="Blues", vmin=0, vmax=3, aspect="auto")
    for y in range(len(ids)):
        for x in range(4): ax.text(x, y, f"{counts[y,x]}/3", ha="center", va="center", color="white" if counts[y,x]>=2 else "#253246")
    ax.set_xticks(range(4), LABELS); ax.set_yticks(range(len(ids)), ids)
    ax.set_title("Exact-location successes by development task", fontsize=14, fontweight="bold", pad=15)
    fig.text(.5, .02, "All eight preselected tasks shown. Counts are repeated runs, not independent samples.", ha="center", fontsize=9)
    fig.tight_layout(rect=[0, .06, 1, 1]); save("development-ablation-cases")
    table = []
    for arm, label in zip(CONDITIONS, LABELS):
        s = stats["conditions"][arm]; r = summary["resources"][arm]; u = r["usage_totals"]
        successes = sum(int(row["scores"]["location"]) for row in rows if row["condition"] == arm)
        table.append(f"| {label} | {successes}/24 | {s['mean']*100:.1f}% | {u['input_tokens']:,} | {u['output_tokens']:,} | {r['median_elapsed_seconds']:.1f}s |")
    differences = [f"| {key} | {v['mean']*100:+.1f} | [{v['ci95'][0]*100:+.1f}, {v['ci95'][1]*100:+.1f}] |" for key, v in stats["comparisons"].items()]
    report = "# 四组开发消融结果\n\n"
    report += f"计划 96 次，记录 {len(rows)} 次；状态：{json.dumps(summary['status_counts'],ensure_ascii=False)}。这是已用于设计的八任务集合，不能作为保留集提升证据。\n\n"
    report += "| 条件 | 定位命中 | 准确率 | 输入 token | 输出 token | 墙钟中位数 |\n| --- | ---: | ---: | ---: | ---: | ---: |\n" + "\n".join(table)
    report += "\n\n| 对比 | 差值（百分点） | 配对 95% 区间（百分点） |\n| --- | ---: | --- |\n" + "\n".join(differences)
    report += "\n\n先按任务平均三次，再按八个任务聚类重采样。区间只描述当前开发集合；没有多重比较调整，不作为显著性结论。输入 token 包含缓存部分；输出按 CLI 原样记录，不再加推理 token。墙钟含启动与网络、并发影响，费用未知。\n\n"
    report += "![四组准确率与输入成本](../../../docs/assets/development-ablation-overview.png)\n\n![逐任务成功次数](../../../docs/assets/development-ablation-cases.png)\n\n"
    report += "[冻结清单](manifest.json) · [分析方案](ANALYSIS_PLAN.md) · [逐次结果](predictions.json) · [分数据集指标与资源](summary.json)。人工理由复盘另列，不是盲评或预先登记的效果指标。\n"
    (RUN / "REPORT.md").write_text(report)
    section = """<!-- development-ablation:start -->
## 四组开发集消融

首轮复盘后，针对 8 个已参与设计的任务重新运行四组、每例每组 3 次，共 **96 次新运行**。A 无 Skill；B 旧版全包；C 旧版正文；D 候选正文。A/B 提示词与首轮一致，本轮重新采样；正文组不能读取参考链接。这是开发机制检查，不能证明保留集提升或实际自动触发效果。

| 条件 | 定位命中 | 准确率 | 输入 token 合计 | 输出 token 合计 | 墙钟中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
""" + "\n".join(table) + """

![开发集四组准确率与上下文成本](docs/assets/development-ablation-overview.png)

每个条件有 24 次运行，但只有 8 个任务。按任务聚类的区间和逐例变化见 [实验报告](evals/iterations/ablation-2026-09-29/REPORT.md)。历史 144 次未并入此表；费用未知，token 与墙钟记录不等于货币报价或纯服务延迟。

[逐任务成功次数图](docs/assets/development-ablation-cases.png) · [冻结清单](evals/iterations/ablation-2026-09-29/manifest.json) · [逐次预测](evals/iterations/ablation-2026-09-29/predictions.json) · [复现方法](docs/ablation-evaluation.md)
<!-- development-ablation:end -->
"""
    # The v0.1 product README intentionally contains no evaluation sections.
    # Only refresh the embedded section when the historical markers are present;
    # never insert evaluation prose into a product-first README.
    readme = ROOT / "README.md"; text = readme.read_text()
    start = "<!-- development-ablation:start -->"; end = "<!-- development-ablation:end -->"
    if start in text:
        before, tail = text.split(start, 1); _, after = tail.split(end, 1)
        text = before + section.rstrip() + after
        text = text.replace("已保存一个未启用的 v2 候选和消融方案；尚未追加模型运行，也未替换当前 Skill。首轮成绩保持原样。",
                            "已保存未启用的 v2 候选，并完成下方四组开发消融。主 Skill 尚未替换，首轮成绩保持原样。")
        readme.write_text(text)
    print("Verified public scores; wrote two PNG/SVG figures and development report.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["score", "publish"])
    if p.parse_args().action == "score": score_records()
    else: publish()
