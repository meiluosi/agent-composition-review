#!/usr/bin/env python3
"""Score or reproduce the frozen paired mechanism evaluation; never calls a model."""

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
from evals.external.core import digest
from evals.mechanisms.core import score
from scripts.run_mechanism_eval import ARMS, RUN, verify, write

LABELS = ["No skill", "V1 routed", "V3 routed"]


def aggregate(manifest, rows, cases):
    keys = [(r["case_id"], r["condition"], r["trial"]) for r in rows]
    assert len(keys) == len(set(keys)) == len(manifest["schedule"])
    assert set(keys) == {(j["case_id"], j["condition"], j["trial"]) for j in manifest["schedule"]}
    indexed = {(r["case_id"], r["condition"], r["trial"]): r for r in rows}
    pairs = sorted({c["pair_id"] for c in cases.values()})
    matrix = []
    by_pair = {}
    for pair in pairs:
        ids = [cid for cid, c in cases.items() if c["pair_id"] == pair]
        assert len(ids) == 2
        by_pair[pair] = {}
        for arm in ARMS:
            values = [all(indexed[cid, arm, t]["scores"]["verdict"] for cid in ids)
                      for t in range(1, manifest["trials"] + 1)]
            by_pair[pair][arm] = sum(values) / len(values)
        matrix.append([by_pair[pair][a] for a in ARMS])
    matrix = np.asarray(matrix, dtype=float)
    rng = np.random.default_rng(20260929)
    draws = matrix[rng.integers(0, len(matrix), size=(10000, len(matrix)))].mean(axis=1)
    resources = {}; metrics = {}
    for a, arm in enumerate(ARMS):
        group = [r for r in rows if r["condition"] == arm]
        healthy = [r for r in group if cases[r["case_id"]]["oracle"]["healthy"]]
        metrics[arm] = {"runs": len(group), "pair_success": float(matrix[:, a].mean()),
                        "pair_ci95": np.quantile(draws[:, a], [.025, .975]).tolist(),
                        "verdict_correct": sum(r["scores"]["verdict"] for r in group),
                        "location_correct": sum(r["scores"]["location"] for r in group),
                        "claim_accuracy": sum(r["scores"]["claim_accuracy"] for r in group) / len(group),
                        "all_claims_correct": sum(r["scores"]["all_claims"] for r in group),
                        "reference_integrity": sum(r["scores"]["reference_integrity"] for r in group),
                        "anchor_coverage": sum(r["scores"]["evidence_anchor_coverage"] for r in group) / len(group),
                        "healthy_runs": len(healthy),
                        "healthy_false_alarms": sum(bool(r["scores"]["healthy_false_alarm"]) for r in healthy),
                        "healthy_invalid": sum(r["status"] != "completed" for r in healthy)}
        resources[arm] = {"statuses": dict(Counter(r["status"] for r in group)),
                          "median_seconds": median(r["elapsed_seconds"] for r in group),
                          "usage_missing": sum(r["usage"] is None for r in group), "cost": None,
                          "usage": {k: sum((r["usage"] or {}).get(k, 0) for r in group)
                                    for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")},
                          "tool_calls": sum(r["tool_item_count"] for r in group),
                          "runs_reading_references": sum(any(f.startswith("references/") for f in r["read_files"]) for r in group),
                          "reference_reads": dict(Counter(f for r in group for f in r["read_files"] if f.startswith("references/")))}
    differences = {name: {"mean": float(matrix[:, b].mean() - matrix[:, a].mean()),
                          "ci95": np.quantile(draws[:, b] - draws[:, a], [.025, .975]).tolist()}
                   for name, (a, b) in {"v3_minus_none": (0, 2), "v3_minus_v1": (1, 2)}.items()}
    return {"metrics": metrics, "by_pair": by_pair, "differences": differences, "resources": resources}


def load_scored():
    manifest, cases = verify(False)
    rows = json.loads((RUN / "predictions.json").read_text())
    summary = json.loads((RUN / "summary.json").read_text())
    assert summary["manifest_sha256"] == digest((RUN / "manifest.json").read_bytes())
    assert summary["predictions_sha256"] == digest((RUN / "predictions.json").read_bytes())
    for row in rows:
        assert row["scores"] == score(row["prediction"] if row["status"] == "completed" else None, cases[row["case_id"]])
    for k, v in aggregate(manifest, rows, cases).items(): assert summary[k] == v
    return manifest, cases, rows, summary


def score_records():
    manifest, cases = verify(False)
    rows = []
    for job in manifest["schedule"]:
        directory = RUN / "raw-runs" / digest(job)[:20]
        path = directory / "record.json"
        if not path.exists(): raise SystemExit("Incomplete experiment; refusing partial score publication.")
        r = json.loads(path.read_text())
        assert all(r[k] == v for k, v in job.items())
        assert datetime.fromisoformat(r["started_at"]) >= datetime.fromisoformat(manifest["frozen_at"])
        for k in ("cli_version", "model_requested", "reasoning_effort"): assert r[k] == manifest[k]
        assert r["input_sha256"] == digest(cases[job["case_id"]]["input"])
        assert r["prompt_sha256"] == manifest["prompt_sha256"][job["condition"]]
        for name, key in [("events.jsonl", "event_sha256"), ("stderr.txt", "stderr_sha256")]:
            assert digest((directory / name).read_bytes()) == r[key]
        pred = r["prediction"] if r["status"] == "completed" else None
        # Keep final free-form explanation private, but retain schema with blank public text for scoring.
        public = None if pred is None else {**pred, "explanation": "", "next_check": ""}
        rows.append({**job, "prediction": public, "scores": score(pred, cases[job["case_id"]]),
                     "explanation_sha256": None if pred is None else digest({k: pred[k] for k in ("explanation", "next_check")}),
                     "raw_record_sha256": digest(path.read_bytes()),
                     **{k: r[k] for k in ("status", "started_at", "elapsed_seconds", "usage", "input_sha256",
                                          "prompt_sha256", "event_sha256", "stderr_sha256", "tool_audit", "tool_item_count", "read_files")}})
    write(RUN / "predictions.json", rows)
    summary = {"kind": manifest["kind"], "planned_runs": len(manifest["schedule"]), "recorded_runs": len(rows),
               "manifest_sha256": digest((RUN / "manifest.json").read_bytes()),
               "predictions_sha256": digest((RUN / "predictions.json").read_bytes()),
               **aggregate(manifest, rows, cases)}
    write(RUN / "summary.json", summary)
    print(json.dumps(summary["metrics"], indent=2))


def publish():
    manifest, cases, rows, summary = load_scored()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "composition-mechanisms-v3"})
    colors = ["#8493A5", "#315B78", "#35897F"]
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.7))
    for i, arm in enumerate(ARMS):
        m = summary["metrics"][arm]; value = m["pair_success"] * 100
        lo, hi = np.array(m["pair_ci95"]) * 100
        axes[0].bar(i, value, color=colors[i], width=.6)
        axes[0].errorbar(i, value, yerr=[[max(0, value-lo)], [max(0, hi-value)]], color="#253246", capsize=5)
        axes[0].text(i, value+3, f"{value:.1f}%", ha="center")
        axes[1].bar(i, summary["resources"][arm]["usage"]["input_tokens"] / 1000, color=colors[i], width=.6)
    axes[0].set_ylim(0, 115); axes[0].set_ylabel("Both variants correct (%)")
    axes[0].set_title("Paired verdicts · 6 authored families")
    axes[1].set_ylabel("Input tokens, including cached (thousands)"); axes[1].set_title("Total context cost · 24 runs per arm")
    for ax in axes:
        ax.set_xticks(range(3), LABELS); ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Development mechanism check · forced entrypoint, optional reference reads", fontsize=12)
    fig.tight_layout(rect=[0, .035, 1, .95])
    fig.text(.5, .01, "No holdout, repair, or automatic-trigger claim. Degenerate bootstrap intervals do not establish certainty.", ha="center", fontsize=8)
    assets = ROOT / "docs/assets"; assets.mkdir(exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(assets / f"mechanisms-v3-overview.{ext}", dpi=180, metadata={"Date": None} if ext == "svg" else {})
    plt.close(fig)
    ordered_pairs = list(dict.fromkeys(c["pair_id"] for c in manifest["cases"]))
    fig, ax = plt.subplots(figsize=(9.4, 4.7))
    for i, arm in enumerate(ARMS):
        values = np.array([summary["by_pair"][p][arm] for p in ordered_pairs])
        ax.plot(range(1, len(values)+1), np.cumsum(values) / np.arange(1, len(values)+1)*100,
                marker=["o", "s", "^"][i], linestyle=["-", "--", ":"][i], color=colors[i], label=LABELS[i], alpha=.85)
    ax.set_xticks(range(1, 7), ordered_pairs, rotation=15); ax.set_ylim(0, 105)
    ax.set_ylabel("Cumulative paired-verdict accuracy (%)"); ax.set_xlabel("Fixed case-authoring order; not a learning curve")
    ax.set_title("Six development families · overlapping lines indicate matching scores")
    ax.legend(loc="lower left"); ax.spines[["top", "right"]].set_visible(False); fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(assets / f"mechanisms-v3-cumulative.{ext}", dpi=180, metadata={"Date": None} if ext == "svg" else {})
    plt.close(fig)
    lines = ["# v3 成对机制评测", "", "状态：72 次计划运行已全部记录。自建开发案例，6 个家族、每家族两个相邻变体，三组各重复两次。", "",
             "主指标：同一家族的两个变体在同一重复中，所问契约 verdict 均正确；先对重复平均，再按六个家族等权汇总。", "",
             "| 条件 | 成对判断 | 单例 verdict | 位置及 verdict | 命题准确率 | 正常误报 / 正常例 | 输入 token | 读取参考的运行 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        m, r = summary["metrics"][arm], summary["resources"][arm]
        lines.append(f"| {arm} | {m['pair_success']:.1%} | {m['verdict_correct']}/24 | {m['location_correct']}/24 | {m['claim_accuracy']:.1%} | {m['healthy_false_alarms']}/{m['healthy_runs']} | {r['usage']['input_tokens']:,} | {r['runs_reading_references']}/24 |")
    lines += ["", "## 按家族", "", "| 家族 | 无 Skill | v1 | v3 |", "| --- | ---: | ---: | ---: |"]
    for pair in ordered_pairs: lines.append("| " + pair + " | " + " | ".join(f"{summary['by_pair'][pair][a]:.0%}" for a in ARMS) + " |")
    lines += ["", "## 不确定性与资源", ""]
    for name, d in summary["differences"].items():
        lines.append(f"- {name}: {d['mean']*100:+.1f} pp，家族配对 bootstrap 95% 区间 [{d['ci95'][0]*100:+.1f}, {d['ci95'][1]*100:+.1f}] pp。")
    for arm in ARMS:
        m, r = summary["metrics"][arm], summary["resources"][arm]
        lines.append(f"- {arm}: 状态 {r['statuses']}；正常例无效运行 {m['healthy_invalid']}；命题全部正确 {m['all_claims_correct']}/24；引用结构合法 {m['reference_integrity']}/24；预设证据锚点覆盖 {m['anchor_coverage']:.1%}；输出 {r['usage']['output_tokens']:,} token；墙钟中位数 {r['median_seconds']:.1f}s；工具调用 {r['tool_calls']}。")
    lines += ["", "区间仅由六个开发家族的经验重采样产生；[0,0] 不能证明未来差异为零。命题评分只检查预先声明的离散判断，引用存在和锚点覆盖不证明解释真实，正常无效运行不作为无误报成功。无多重比较调整。", "",
              "## 范围与复现", "", "- 相同模型、共同任务协议、只读工具权限和原始案例；强制注入主入口，参考文件由执行者自行读取。不是自动触发评测。", 
              "- 共同协议已要求证据和未知区分，因此这是 Skill 在该协议之上的增量效果，不是裸模型闲聊对照。",
              "- 案例与候选同期编写，标签不进入执行目录。公开案例文件包含标签是为了复核；每次执行只复制 input 字段。",
              "- 本轮没有真实业务修复、因果干预或保留集验证；不自动推广候选，不与外部 144 次及旧消融 96 次合并。",
              "- 原始过程和自由文本解释留本地，公开结构化预测、工具读取审计、资源和哈希。只读沙箱并非完整读隔离，审计仅覆盖可见命令。",
              "- 重画：`python scripts/analyze_mechanisms.py publish`，无需模型；新运行必须使用新的实验目录和协议。", "",
              "[冻结方案](ANALYSIS_PLAN.md) · [清单](manifest.json) · [逐次预测](predictions.json) · [汇总](summary.json)", ""]
    (RUN / "REPORT.md").write_text("\n".join(lines))
    print("Verified all published scores; regenerated report and two figures.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("action", choices=["score", "publish"])
    if p.parse_args().action == "score": score_records()
    else: publish()
