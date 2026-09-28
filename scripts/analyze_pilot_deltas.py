#!/usr/bin/env python3
"""Post-hoc paired analysis of published results; does not rescore or call models."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.external.core import digest

RESULTS = ROOT / "evals/external/results/pilot-2026-09-29"


def main():
    path = RESULTS / "predictions.json"
    rows = json.loads(path.read_text())
    original = json.loads((RESULTS / "summary.json").read_text())
    assert digest(path.read_bytes()) == original["predictions_sha256"]
    by = {(r["case_id"], r["condition"], r["trial"]): r for r in rows}
    assert len(by) == len(rows) == 144
    pairs = [(r, by[r["case_id"], "with_skill", r["trial"]])
             for r in rows if r["condition"] == "without_skill"]
    fields = ["location", "agents", "error_mode"]
    transitions = Counter()
    per_case = defaultdict(lambda: {"without_skill": 0, "with_skill": 0, "trials": 0})
    disagreements = []
    for a, b in pairs:
        state = ("correct" if a["scores"]["location"] else "wrong") + "_to_" + (
            "correct" if b["scores"]["location"] else "wrong")
        transitions[state] += 1
        c = per_case[a["case_id"]]
        c["trials"] += 1
        c["without_skill"] += int(a["scores"]["location"])
        c["with_skill"] += int(b["scores"]["location"])
        if any(a["prediction"][f] != b["prediction"][f] for f in fields):
            disagreements.append({"case_id": a["case_id"], "trial": a["trial"],
                                  "without_skill": a["prediction"], "with_skill": b["prediction"],
                                  "location_transition": state})
    resources = {}
    for condition in ["without_skill", "with_skill"]:
        group = [r for r in rows if r["condition"] == condition]
        resources[condition] = {
            "input_tokens": sum(r["usage"]["input_tokens"] for r in group),
            "output_tokens": sum(r["usage"]["output_tokens"] for r in group),
            "median_wall_seconds": median(r["elapsed_seconds"] for r in group),
            "mean_location_confidence": mean(r["prediction"]["confidence"] for r in group),
            "location_brier_exploratory": mean((r["prediction"]["confidence"] - r["scores"]["location"]) ** 2 for r in group),
        }
    result = {
        "kind": "post_hoc_development_analysis_not_new_evaluation",
        "source_predictions_sha256": digest(path.read_bytes()),
        "pairs": len(pairs), "identical_attribution_pairs": len(pairs) - len(disagreements),
        "location_transitions": dict(transitions),
        "case_location_counts": dict(per_case), "disagreements": disagreements,
        "resources": resources,
        "limitations": [
            "Trial-number pairing is descriptive, not shared model randomness or a counterfactual.",
            "Repeated trials are clustered by task; transition counts are not independent evidence.",
            "Confidence/Brier inspection was not a predeclared primary analysis.",
            "Differences in rationales do not identify which instruction caused a change.",
            "The 24 cases become development material for subsequent skill iteration.",
        ],
    }
    out = ROOT / "evals/iterations/pilot-postmortem.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ["pairs", "identical_attribution_pairs", "location_transitions"]}))


if __name__ == "__main__":
    main()
