#!/usr/bin/env python3
"""Offline, deterministic summarizer for the real-world LangGraph pilot.

Reads the frozen blind labels, the per-case fixtures and the saved
``acr scan`` output, then writes:

    evals/realworld/results/pilot-2026-10-17/predictions.json
    evals/realworld/results/pilot-2026-10-17/summary.json
    evals/realworld/results/pilot-2026-10-17/REPORT.md

Design rules
------------
- Offline: Python standard library only. No network, no model calls, no
  subprocess; the script never calls ``acr`` itself, it only parses the
  archived ``scan.txt`` files.
- Deterministic: no timestamps, stable ordering, stable rounding. A rerun on
  the same inputs produces byte-identical output.
- Labels are frozen: the published sha256 is verified on every run and the
  script refuses to summarize when ``labels.json`` changed. Labels and
  fixtures are never written by this script.

The analyst's relevance judgments are explicit in ``CASE_JUDGMENTS`` below.
Everything else (finding counts, metrics, gap distributions) is derived.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import re
import sys
from collections import Counter
from pathlib import Path

PILOT = "pilot-2026-10-17"
SCHEMA_VERSION = 1
GENERATED_BY = "scripts/summarize_realworld_eval.py"
LABELS_SHA256 = "d5c48148b6b4c27df7a37ae90fc75e818fb5ecbffb0d463cc5dcaea541576ab9"
EXISTING_RULES = tuple(f"ACR{i:03d}" for i in range(1, 7)) + tuple(
    f"ACR1{i:02d}" for i in range(1, 6)
)
GAP_PREFIX = "GAP:"

# Domain keywords used for the lightweight scan.txt message extraction.
MESSAGE_KEYWORDS = (
    "output_schema",
    "shared state key",
    "subgraph",
    "parallel",
    "reducer",
    "no reducer",
    "annotated",
    "readonly",
    "superstep",
    "invalidupdate",
    "cache",
    "checkpoint",
    "interrupt",
    "retry",
    "tags",
    "mro",
    "silent",
    "filtered",
    "state key",
    "compiled subgraph",
)

FINDING_HEADER = re.compile(r"^\s{2}(HIGH|MEDIUM|LOW)\s+(ACR\d{3})\s+(.+?)\s*$")
FINDING_LOCATION = re.compile(r"^\s{4}(\S+\.py):(\d+)\s*$")
HEADER_COUNTS = re.compile(
    r"(\d+) finding\(s\): high (\d+), medium (\d+), low (\d+)"
)
FROZEN_DATE = re.compile(r"冻结日期：(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Analyst judgments, frozen with the report (2026-10-17).
#
# `verdict` follows protocol.md section 5:
#   hit / miss / false_positive / gap / not_applicable, plus `incidental` for
#   cases whose only finding is a real but orthogonal pattern.
# `finding_relevance` maps a rule id to whether that finding points at the
# reported failure: kind = relevant | misleading | unrelated.
# ---------------------------------------------------------------------------
CASE_JUDGMENTS = {
    "lg-6446": {
        "verdict": "miss",
        "incidental": False,
        "finding_relevance": {},
        "hint": "ACR001 形态，但子图由 helper 返回 compiled graph，跨函数解析缺口 → miss",
        "reason": (
            "盲标预期 ACR001，scan 为 0 findings。fixture 在 build_child() 中构建子图并返回 "
            "compiled graph，再通过 add_node('child', build_child()) 注册；scanner 不跨函数解析 "
            "helper 的返回值，因此 helper function boundary -> miss。fixture 本身是 ACR001 的典型形态"
            "（子图无 output_schema、与父图共享 'b'），所以这是 ACR001 的分析深度/召回缺口，"
            "不是规则语义缺口，也不是框架内部 bug。"
        ),
    },
    "lg-8320": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "节点返回未声明键被静默过滤；现有 11 条规则无覆盖 → GAP",
        "reason": (
            "盲标预期 GAP:declared-keys，scan 为 0 findings。节点返回 {'x': 1, 'undeclared_key': ...}，"
            "框架在 node-return-to-state 边界静默过滤未声明键；现有规则不检查 declared-key 不匹配。"
            "fixture 元数据把 source_type 写成 framework，盲标写 feature（issue 实际请求 warning/strict，"
            "接近功能请求），分歧已记录但未修改任何一侧。"
        ),
    },
    "lg-9311": {
        "verdict": "false_positive",
        "incidental": False,
        "finding_relevance": {
            "ACR002": {"relevant": False, "kind": "misleading"},
        },
        "hint": "ReadOnly 包住 reducer，ACR002 误判为 no reducer → 误导性 finding",
        "reason": (
            "盲标预期 GAP:reducer-semantics，但 scan 报 ACR002，message 断言键 'findings' 'has no reducer'。"
            "源码是 findings: ReadOnly[Annotated[list[str], operator.add]]，reducer 存在、只是被 ReadOnly "
            "限定符包住；scan.py 的 _annotated_parts 只解包 Required/NotRequired，不识别 ReadOnly，"
            "因此 reducer 未被发现。"
            "该 finding 对故障给出了错误解释（issue 的真实问题是框架忽略被限定符包的 reducer），"
            "应记为 false_positive/misleading，不能算 gap hit。"
        ),
    },
    "lg-9005": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "Command.update 按 MRO 重复应用继承字段 → 运行时语义，GAP",
        "reason": (
            "盲标预期 GAP:command-send，scan 为 0 findings。图结构只是单节点，故障在 "
            "Command(update=...) 的字段收集路径（get_cached_annotated_keys 沿 MRO 重复追加继承注解，"
            "使 reducer 应用两次，count=5 而非 3）。属于 Command.update 通道的运行时语义，"
            "静态构图规则不覆盖。"
        ),
    },
    "lg-9086": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "tuple reducer 经 checkpoint 往返退化成 list → 运行时，GAP",
        "reason": (
            "盲标预期 GAP:reducer-semantics，scan 为 0 findings。Annotated[tuple, operator.add] 在 "
            "checkpoint 恢复时被 BinaryOperatorAggregate.from_checkpoint 还原成 list，下一次 invoke "
            "永久抛 TypeError。属于 checkpoint 序列化/类型往返的框架内部行为，静态构图不可见。"
        ),
    },
    "lg-9253": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "retry 未重置 subgraph 计数，第二个子图续用第一个的 checkpoint → 运行时，GAP",
        "reason": (
            "盲标预期 GAP:retry-cache，scan 为 0 findings。节点上的 RetryPolicy 重试时 task scratchpad 的 "
            "subgraph_counter 未重置，review 子图按位置命中 research 的 checkpoint（返回 RESEARCH）。"
            "根因是 retry 与子图 checkpoint 命名空间/身份的绑定，需要运行时 trace 或版本感知，"
            "静态规则只能给出可疑提示，不能断言。"
        ),
    },
    "lg-9106": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "嵌套并行 interrupt 已完成 sibling 被重放 → 运行时，GAP",
        "reason": (
            "盲标预期 GAP:parallel-interrupt，scan 为 0 findings。子图内两个并行节点各自 interrupt，"
            "逐个按 id 恢复时已完成 sibling 会再次执行 interrupt 之后的副作用；维护者已确认并在 #9315 修复。"
            "根因是嵌套图 is_replaying 误判，属于运行时恢复语义，静态构图规则不覆盖。"
        ),
    },
    "lg-9276": {
        "verdict": "incidental",
        "incidental": True,
        "finding_relevance": {
            "ACR001": {"relevant": False, "kind": "unrelated"},
        },
        "hint": "scan 的 ACR001 来自共享 State 的副作用，与 resume 重放故障无关 → 顺带",
        "reason": (
            "盲标预期 GAP:resume-replay，scan 报 1 条 ACR001（父子图共享 answer/approved/text、"
            "子图无 output_schema）。issue 的故障是 resume 后已完成的 sibling 节点被重跑"
            "（model 被调用两次），fixture 复用同一个 State 类只是让 ACR001 顺带触发；"
            "该 finding 与故障无关，记为 incidental/unrelated，不算 hit。"
        ),
    },
    "lg-8753": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "非函数 callable 的 cache namespace 退化为 __dynamic__ → 静态可部分检测，GAP",
        "reason": (
            "盲标预期 GAP:retry-cache，scan 为 0 findings。共享 InMemoryCache 时，callable object / "
            "lambda / partial / named async 的 cache namespace 退化为 '__dynamic__'，不同图的同名节点"
            "互相命中缓存。根因是 cache key 缺少调用者身份；ACR004 只覆盖副作用被缓存，不覆盖键身份，"
            "这一项静态规则有部分可检测空间。"
        ),
    },
    "lg-2644": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "节点返回未声明键；材料低置信度（维护者未复现）→ GAP",
        "reason": (
            "盲标预期 GAP:declared-keys，scan 为 0 findings。class node 返回图 state 未声明的 zoo 键，"
            "被过滤后没有合法写入，报 'Must write to at least one of [\\'foo\\']'。"
            "与 lg-8320 同属 declared-keys 缺口。材料限制：维护者表示原样代码无法复现，"
            "仓库 pinned langgraph 1.0.3 下 fixture 正常完成，故盲标 confidence=low，记为软性材料限制。"
        ),
    },
    "lg-7580": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "盲标 graph_visible=false，材料是 channel 级而非构图 → not_applicable（材料不足）",
        "reason": (
            "盲标 graph_visible=false：issue 的复现直接调用 BinaryOperatorAggregate.update，"
            "没有 StateGraph 构图；fixture 虽重建了隐含拓扑，但盲标判为材料不足以做构图分析。"
            "故记为 not_applicable（材料不足），不算 miss、不算可分析案例；"
            "其预期缺口 GAP:reducer-semantics 仍计入缺口分布并标注 not_analyzable。"
            "维护者表示这是 expected behavior、只缺文档，后续方向是文档修正。"
        ),
    },
    "lg-9084": {
        "verdict": "incidental",
        "incidental": True,
        "finding_relevance": {
            "ACR001": {"relevant": False, "kind": "unrelated"},
        },
        "hint": "scan 的 ACR001 来自共享 State/嵌套链条的副作用，与 tags 重复无关 → 顺带",
        "reason": (
            "盲标预期 GAP:other，scan 报 1 条 ACR001（父子图共享键 copies、子图无 output_schema）。"
            "issue 的故障是 tags 在每层子图被重复拼接（深度 d 看到 2^d 份），与共享键/并行写无关；"
            "fixture 用同一个 State 类逐层包裹，才让 ACR001 顺带触发。记为 incidental/unrelated，不算 hit。"
        ),
    },
    "lg-9019": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "LastValue 未解包 Overwrite，把 wrapper 存进状态 → 运行时，GAP",
        "reason": (
            "盲标预期 GAP:reducer-semantics，scan 为 0 findings。update_state 对普通 key 写 Overwrite 时，"
            "LastValue / LastValueAfterFinish / UntrackedValue 把 Overwrite wrapper 本身存进状态而不是 payload；"
            "属于单值通道的运行时语义缺陷，静态规则不覆盖。"
        ),
    },
    "lg-8608": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "error_handler 已处理仍重抛原异常 → 框架 executor 语义，GAP",
        "reason": (
            "盲标预期 GAP:other，scan 为 0 findings。error_handler 已运行并提交恢复更新，"
            "但在并行 sibling、stream_mode=custom/messages、subgraphs=True 等配置下原异常仍被重抛。"
            "根因在 BackgroundExecutor/AsyncBackgroundExecutor 的终止语义，不在用户构图；"
            "静态规则不覆盖。材料限制：fixture 只在 langgraph>=1.2 有意义，pinned 1.0.3 不调用 handler。"
        ),
    },
    # Internal false-positive controls.
    "control-output-schema": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "正常控制，0 findings（ACR001/ACR003 误报控制）",
        "reason": "正常控制图：子图声明了窄化 output_schema，只回传自己拥有的键；scan 0 findings，控制通过。",
    },
    "control-reducer": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "正常控制，0 findings（ACR002 误报控制）",
        "reason": "正常控制图：并行分支写 Annotated 且带 operator.add reducer；scan 0 findings，控制通过。",
    },
    "control-disjoint": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "正常控制，0 findings（ACR002 误报控制）",
        "reason": "正常控制图：并行分支写不相交的键，不需要 reducer；scan 0 findings，控制通过。",
    },
}

# Material notes that do not change the blind verdict but limit reproduction.
SOFT_MATERIAL_CONCERNS = {
    "lg-2644": "维护者未能复现；仓库 pinned langgraph 1.0.3 下 fixture 正常完成，报告错误未被确认（盲标 confidence=low）。",
    "lg-8608": "fixture 只在 langgraph>=1.2 有意义；pinned 1.0.3 接受 error_handler 参数但不调用 handler。",
    "lg-9084": "tags 重复自 1.2.3 起出现；pinned 1.0.3 不出现该行为。",
}

LIMITATIONS = [
    "开发型试点：14 个 issue（+3 个内部正常控制），样本小，未计算置信区间，不宣称泛化。",
    "本批案例已用于发现缺口，不能当作未见保留集复用；后续规则修改不得把同批案例当 benchmark 宣称。",
    "静态分析：本报告只汇总存档的 acr scan 结果，没有运行 fixture，不能证明运行时行为；运行时故障判定依赖 issue 文本、评论与 expected.md。",
    "相关性/误导/顺带判定是分析者判断，逐条写在 predictions.json 的 notes 中；没有第二标注者，无一致性统计。",
    "版本敏感：lg-8608 需 langgraph>=1.2、lg-9084 需 >=1.2.3，pinned 1.0.3 下 fixture 不表现报告行为；静态扫描不受影响，但复跑 fixture 需要版本矩阵。",
    "正常控制是内部 fixture，不是社区里的正常图；0/3 误报率是很弱的证据。",
    "acr 0.1.0 的静态 AST 规则不等于 acr trace 能力；本报告的 GAP 结论只针对 scan 路径。",
    "GAP 中多数是框架内部语义（framework 10/13），静态构图规则本来就不覆盖，不能算 ACR 的 false-negative 能力问题。",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ast_parseable(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    return True


def ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


# ---------------------------------------------------------------------------
# scan.txt parsing
# ---------------------------------------------------------------------------
def parse_scan(text: str) -> dict:
    """Parse the archived ``acr scan`` text output.

    Extracts the declared finding total and, per finding, rule id, severity,
    ``file:line``, title, first message line and domain keywords.
    """
    declared = None
    severity_totals = {}
    match = HEADER_COUNTS.search(text)
    if match:
        declared = int(match.group(1))
        severity_totals = {
            "high": int(match.group(2)),
            "medium": int(match.group(3)),
            "low": int(match.group(4)),
        }

    findings = []
    current = None
    for raw_line in text.splitlines():
        header = FINDING_HEADER.match(raw_line)
        if header:
            if current is not None:
                findings.append(current)
            current = {
                "severity": header.group(1).lower(),
                "rule": header.group(2),
                "title": header.group(3).strip(),
                "file": None,
                "line": None,
                "message": "",
            }
            continue
        if current is None:
            continue
        location = FINDING_LOCATION.match(raw_line)
        if location and current["file"] is None:
            current["file"] = location.group(1)
            current["line"] = int(location.group(2))
            continue
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("fix:") or stripped.startswith("docs:"):
            continue
        if not current["message"]:
            current["message"] = stripped
    if current is not None:
        findings.append(current)

    for finding in findings:
        haystack = f"{finding['title']} {finding['message']}".lower()
        finding["message_keywords"] = sorted(
            keyword for keyword in MESSAGE_KEYWORDS if keyword in haystack
        )

    return {
        "declared_findings": declared,
        "severity_totals": severity_totals,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def collect_predictions(root: Path) -> tuple[list[dict], list[str]]:
    realworld = root / "evals" / "realworld"
    labels_doc = load_json(realworld / "labels.json")
    if not labels_doc.get("blind"):
        raise SystemExit("labels.json is not marked blind: refusing to summarize.")
    candidates_path = realworld / "candidates.json"
    candidates = {}
    if candidates_path.is_file():
        for entry in load_json(candidates_path).get("cases", []):
            candidates[entry.get("case_id")] = entry

    cases_dir = realworld / "cases"
    predictions = []
    warnings = []

    for label in labels_doc.get("cases", []):
        case_id = label["case_id"]
        case_dir = cases_dir / case_id
        case_meta = load_json(case_dir / "case.json") if (case_dir / "case.json").is_file() else {}
        graph_path = case_dir / "graph.py"
        scan = parse_scan(read_text(case_dir / "scan.txt")) if (case_dir / "scan.txt").is_file() else {
            "declared_findings": None,
            "severity_totals": {},
            "findings": [],
        }
        if scan["declared_findings"] is not None and scan["declared_findings"] != len(scan["findings"]):
            warnings.append(
                f"{case_id}: scan header declares {scan['declared_findings']} finding(s) "
                f"but {len(scan['findings'])} block(s) were parsed."
            )

        judgment = CASE_JUDGMENTS.get(case_id)
        if judgment is None:
            expected = label.get("expected_rule", "")
            if expected in EXISTING_RULES:
                verdict = "hit" if any(f["rule"] == expected for f in scan["findings"]) else "miss"
            elif expected.startswith(GAP_PREFIX):
                verdict = "gap" if not scan["findings"] else "incidental"
            else:
                verdict = "not_applicable"
            judgment = {
                "verdict": verdict,
                "incidental": verdict == "incidental",
                "finding_relevance": {},
                "hint": "自动判定（无人工 judgment 记录）",
                "reason": "该 case 没有冻结的分析判断，按 expected_rule 与 scan 结果自动判定；需人工复核。",
            }
            warnings.append(f"{case_id}: no frozen judgment; verdict auto-derived ({verdict}).")

        relevance_map = judgment.get("finding_relevance", {})
        findings = []
        for finding in scan["findings"]:
            info = relevance_map.get(finding["rule"], {})
            relevant = bool(info.get("relevant", False))
            kind = info.get("kind") or ("relevant" if relevant else "unclassified")
            enriched = dict(finding)
            enriched["relevant"] = relevant
            enriched["kind"] = kind
            findings.append(enriched)

        disagreements = []
        if case_meta.get("source_type") and case_meta.get("source_type") != label.get("source_type"):
            disagreements.append(
                {
                    "field": "source_type",
                    "blind": label.get("source_type"),
                    "fixture": case_meta.get("source_type"),
                }
            )
        if "graph_visible" in case_meta and bool(case_meta.get("graph_visible")) != bool(
            label.get("graph_visible")
        ):
            disagreements.append(
                {
                    "field": "graph_visible",
                    "blind": bool(label.get("graph_visible")),
                    "fixture": bool(case_meta.get("graph_visible")),
                }
            )

        graph_ok = ast_parseable(graph_path)
        analyzable = bool(label.get("graph_visible")) and graph_ok
        predictions.append(
            {
                "case_id": case_id,
                "kind": "issue",
                "source_url": case_meta.get("source_url") or candidates.get(case_id, {}).get("url"),
                "title": case_meta.get("title"),
                "source_type_blind": label.get("source_type"),
                "source_type_fixture": case_meta.get("source_type"),
                "graph_visible_blind": bool(label.get("graph_visible")),
                "graph_visible_fixture": (
                    bool(case_meta.get("graph_visible")) if "graph_visible" in case_meta else None
                ),
                "fixture_ast_parseable": graph_ok,
                "analyzable": analyzable,
                "expected_rule": label.get("expected_rule"),
                "expected_finding": label.get("expected_finding"),
                "confidence": label.get("confidence"),
                "normal_control": bool(label.get("normal_control", False)),
                "scan": {
                    "declared_findings": scan["declared_findings"],
                    "severity_totals": scan["severity_totals"],
                    "findings": findings,
                },
                "scan_findings": findings,
                "verdict": judgment["verdict"],
                "incidental": bool(judgment.get("incidental", False)),
                "control_pass": None,
                "disagreements": disagreements,
                "notes": judgment["reason"],
                "report_hint": judgment.get("hint"),
            }
        )

    known = {prediction["case_id"] for prediction in predictions}
    for case_dir in sorted(cases_dir.glob("control-*")):
        case_id = case_dir.name
        if case_id in known:
            continue
        case_meta = load_json(case_dir / "case.json")
        scan = parse_scan(read_text(case_dir / "scan.txt"))
        if scan["declared_findings"] is not None and scan["declared_findings"] != len(scan["findings"]):
            warnings.append(
                f"{case_id}: scan header declares {scan['declared_findings']} finding(s) "
                f"but {len(scan['findings'])} block(s) were parsed."
            )
        judgment = CASE_JUDGMENTS.get(case_id)
        if judgment is None:
            judgment = {
                "verdict": "not_applicable",
                "incidental": False,
                "finding_relevance": {},
                "hint": "正常控制，0 findings",
                "reason": "正常控制图；scan 0 findings，控制通过。",
            }
            warnings.append(f"{case_id}: no frozen control judgment; assuming not_applicable.")
        findings = []
        for finding in scan["findings"]:
            enriched = dict(finding)
            enriched["relevant"] = False
            enriched["kind"] = "unclassified"
            findings.append(enriched)
        verdict = "false_positive" if findings else judgment["verdict"]
        predictions.append(
            {
                "case_id": case_id,
                "kind": "control",
                "source_url": case_meta.get("source_url"),
                "title": case_meta.get("title"),
                "source_type_blind": "control",
                "source_type_fixture": case_meta.get("source_type"),
                "graph_visible_blind": bool(case_meta.get("graph_visible")),
                "graph_visible_fixture": bool(case_meta.get("graph_visible")),
                "fixture_ast_parseable": ast_parseable(case_dir / "graph.py"),
                "analyzable": ast_parseable(case_dir / "graph.py"),
                "expected_rule": "NONE",
                "expected_finding": None,
                "confidence": None,
                "normal_control": True,
                "scan": {
                    "declared_findings": scan["declared_findings"],
                    "severity_totals": scan["severity_totals"],
                    "findings": findings,
                },
                "scan_findings": findings,
                "verdict": verdict,
                "incidental": False,
                "control_pass": not findings,
                "disagreements": [],
                "notes": judgment["reason"],
                "report_hint": judgment.get("hint"),
            }
        )

    predictions.sort(key=lambda item: item["case_id"])
    return predictions, warnings


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def build_summary(root: Path, predictions: list[dict], warnings: list[str]) -> dict:
    realworld = root / "evals" / "realworld"
    labels_path = realworld / "labels.json"
    labels_doc = load_json(labels_path)
    actual_hash = sha256_file(labels_path)
    protocol_text = read_text(realworld / "protocol.md") if (realworld / "protocol.md").is_file() else ""
    frozen = FROZEN_DATE.search(protocol_text)
    acr_version = read_text(root / "VERSION").strip() if (root / "VERSION").is_file() else None

    issue_predictions = [item for item in predictions if item["kind"] == "issue"]
    control_predictions = [item for item in predictions if item["kind"] == "control"]
    analyzable = [item for item in issue_predictions if item["analyzable"]]
    expressible = [item for item in analyzable if item["expected_rule"] in EXISTING_RULES]
    hits = [item for item in expressible if item["verdict"] == "hit"]
    gap_cases = [
        item for item in issue_predictions if str(item["expected_rule"]).startswith(GAP_PREFIX)
    ]

    real_findings = [finding for item in issue_predictions for finding in item["scan_findings"]]
    control_findings = [finding for item in control_predictions for finding in item["scan_findings"]]
    relevant = [finding for finding in real_findings if finding["relevant"]]
    misleading = [finding for finding in real_findings if finding["kind"] == "misleading"]
    unrelated = [finding for finding in real_findings if finding["kind"] == "unrelated"]

    gap_by_category = {}
    for item in gap_cases:
        category = item["expected_rule"].split(":", 1)[1]
        gap_by_category.setdefault(category, []).append(item["case_id"])
    gap_by_source_type = Counter(item["source_type_blind"] for item in gap_cases)
    gap_by_source_type_fixture = Counter(
        item["source_type_fixture"] for item in gap_cases if item["source_type_fixture"]
    )

    verdict_counts = Counter(item["verdict"] for item in issue_predictions)
    control_false_positives = [
        item for item in control_predictions if item["verdict"] == "false_positive"
    ]
    hard_insufficient = [item for item in issue_predictions if not item["graph_visible_blind"]]

    disagreements = [
        {"case_id": item["case_id"], **entry}
        for item in predictions
        for entry in item["disagreements"]
    ]

    findings_per_case = {
        item["case_id"]: len(item["scan_findings"])
        for item in predictions
        if item["scan_findings"]
    }

    summary = {
        "schema_version": SCHEMA_VERSION,
        "pilot": PILOT,
        "generated_by": GENERATED_BY,
        "frozen_inputs": {
            "labels_path": "evals/realworld/labels.json",
            "labels_blind": bool(labels_doc.get("blind")),
            "labels_case_count": len(labels_doc.get("cases", [])),
            "labels_sha256_expected": LABELS_SHA256,
            "labels_sha256_actual": actual_hash,
            "labels_sha256_verified": actual_hash == LABELS_SHA256,
            "protocol_frozen_at": frozen.group(1) if frozen else None,
        },
        "environment": {
            "acr_version": acr_version,
            "acr_invocation": "PYTHONPATH=src python3 -m agent_composition_review (acr 0.1.0)",
            "python_version": platform.python_version(),
            "analysis_mode": "static-only; archived acr scan output parsed offline",
        },
        "counts": {
            "issue_cases": len(issue_predictions),
            "normal_controls": len(control_predictions),
            "analyzable_cases": len(analyzable),
            "expressible_cases": len(expressible),
            "gap_cases": len(gap_cases),
            "material_insufficient_cases": len(hard_insufficient),
            "soft_material_concerns": len(SOFT_MATERIAL_CONCERNS),
            "cases_with_findings": len({item["case_id"] for item in issue_predictions if item["scan_findings"]}),
        },
        "metrics": {
            "analyzability_rate": ratio(len(analyzable), len(issue_predictions)),
            "rule_expressibility_rate_analyzable": ratio(len(expressible), len(analyzable)),
            "rule_expressibility_rate_all_cases": ratio(len(expressible), len(issue_predictions)),
            "hit_rate": ratio(len(hits), len(expressible)),
            "control_false_positive_rate": ratio(len(control_false_positives), len(control_predictions)),
            "real_case_finding_relevance_rate": ratio(len(relevant), len(real_findings)),
            "misleading_finding_rate": ratio(len(misleading), len(real_findings)),
        },
        "verdicts": {
            "counts": dict(sorted(verdict_counts.items())),
            "cases": {
                verdict: sorted(item["case_id"] for item in issue_predictions if item["verdict"] == verdict)
                for verdict in sorted(verdict_counts)
            },
        },
        "gap": {
            "cases_total": len(gap_cases),
            "analyzable_cases": sum(1 for item in gap_cases if item["analyzable"]),
            "by_category": {
                category: {
                    "count": len(case_ids),
                    "cases": sorted(case_ids),
                }
                for category, case_ids in sorted(gap_by_category.items())
            },
            "by_source_type_blind": dict(sorted(gap_by_source_type.items())),
            "by_source_type_fixture": dict(sorted(gap_by_source_type_fixture.items())),
            "verdict_counts": dict(
                sorted(Counter(item["verdict"] for item in gap_cases).items())
            ),
        },
        "findings": {
            "total_findings_issue_cases": len(real_findings),
            "total_findings_controls": len(control_findings),
            "relevant": len(relevant),
            "misleading": len(misleading),
            "unrelated": len(unrelated),
            "per_case": dict(sorted(findings_per_case.items())),
            "emitted_by_case": {
                item["case_id"]: [finding["rule"] for finding in item["scan_findings"]]
                for item in issue_predictions
                if item["scan_findings"]
            },
        },
        "disagreements": disagreements,
        "material": {
            "hard_insufficient": {
                "count": len(hard_insufficient),
                "cases": sorted(item["case_id"] for item in hard_insufficient),
                "reason": "盲标 graph_visible=false，材料不足以做构图分析。",
            },
            "soft_concerns": {
                case_id: reason
                for case_id, reason in sorted(SOFT_MATERIAL_CONCERNS.items())
            },
        },
        "limitations": LIMITATIONS,
        "warnings": sorted(warnings),
    }
    return summary


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------
def _fmt_rate(metric: dict) -> str:
    rate = metric["rate"]
    if rate is None:
        return "n/a"
    return f"{metric['numerator']}/{metric['denominator']} = {rate * 100:.1f}%"


def _fmt_percent(metric: dict) -> str:
    rate = metric["rate"]
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def build_report(summary: dict, predictions: list[dict]) -> str:
    metrics = summary["metrics"]
    counts = summary["counts"]
    findings = summary["findings"]
    gap = summary["gap"]
    verdict_counts = summary["verdicts"]["counts"]

    issue_predictions = [item for item in predictions if item["kind"] == "issue"]
    control_predictions = [item for item in predictions if item["kind"] == "control"]

    rows = []
    hash_verified = summary["frozen_inputs"]["labels_sha256_verified"]
    hash_note = (
        "与预期一致（脚本每次运行强校验，不匹配即拒绝汇总）"
        if hash_verified
        else "⚠️ 与预期不一致（本次为调试运行，结果不可作为报告）"
    )
    for item in predictions:
        source_type = item["source_type_blind"]
        expected = item["expected_rule"]
        rule_counts = Counter(finding["rule"] for finding in item["scan_findings"])
        scan = (
            ", ".join(f"{rule}×{count}" for rule, count in sorted(rule_counts.items()))
            if rule_counts
            else "0"
        )
        verdict = "control pass" if item["kind"] == "control" and item["control_pass"] else item["verdict"]
        rows.append(
            f"| `{item['case_id']}` | {source_type} | {expected} | {scan} | **{verdict}** | {item['report_hint']} |"
        )

    gap_lines = []
    for category, info in gap["by_category"].items():
        gap_lines.append(
            f"- `GAP:{category}`：{info['count']} 例（{', '.join(f'`{case}`' for case in info['cases'])}）"
        )

    report = f"""# 真实 LangGraph 社区案例试点报告（{PILOT}）

**范围：14 个真实 issue + 3 个内部正常控制图。这是开发型试点，不是保留集 benchmark；样本小，不计算置信区间，不宣称泛化。** 本报告用于发现 v0.2 的规则/能力缺口，所有数字只描述这一批案例。

## 1. 材料与冻结状态

- 盲标：`evals/realworld/labels.json`，`blind: true`；sha256 `{summary['frozen_inputs']['labels_sha256_actual'][:16]}…` {hash_note}。labels 与 fixtures 均未修改。
- 扫描：`acr 0.1.0` 静态 AST 扫描；本工作树未安装 console script，通过 `PYTHONPATH=src python3 -m agent_composition_review` 运行。
- 案例：14 个 issue + 3 个正常控制，全部 `graph.py` 可被 AST 解析。
- 复核：人工复跑 17 个目录的 `acr scan --fail-on never`，输出与存档 `scan.txt` 逐行一致；3 个 controls 均为 0 findings（未安装 console script，运行时为 `PYTHONPATH=src python3 -m agent_composition_review`，`acr 0.1.0`）。
- 口径：`evals/realworld/protocol.md`（冻结于 {summary['frozen_inputs']['protocol_frozen_at']}）；逐条判定依据见 `predictions.json` 的 `notes`。

## 2. 核心数字（只描述本批）

| 指标 | 值 |
| --- | --- |
| 可分析率 | {_fmt_rate(metrics['analyzability_rate'])} |
| 规则可表达率（分母 = 可分析案例） | {_fmt_rate(metrics['rule_expressibility_rate_analyzable'])} |
| 规则可表达率（分母 = 全部 issue） | {_fmt_rate(metrics['rule_expressibility_rate_all_cases'])} |
| 命中率（分母 = 可表达案例） | {_fmt_rate(metrics['hit_rate'])} |
| 正常控制误报率 | {_fmt_rate(metrics['control_false_positive_rate'])} |
| 真实案例 finding 相关性 | {_fmt_rate(metrics['real_case_finding_relevance_rate'])}（{findings['total_findings_issue_cases']} 条中误导 {findings['misleading']}、顺带 {findings['unrelated']}、相关 {findings['relevant']}） |
| GAP 案例（按盲标 source_type） | {gap['cases_total']} 例：{', '.join(f"{key} {value}" for key, value in gap['by_source_type_blind'].items())} |
| 判定分布（14 个 issue） | gap {verdict_counts['gap']} / incidental {verdict_counts.get('incidental', 0)} / false_positive {verdict_counts.get('false_positive', 0)} / miss {verdict_counts.get('miss', 0)} / not_applicable {verdict_counts.get('not_applicable', 0)} |
| 材料不足（硬） | {counts['material_insufficient_cases']}/14（`lg-7580`）；另有 {counts['soft_material_concerns']} 例版本敏感/低置信材料 |

一句话结论：**唯一的可表达案例 `lg-6446`（ACR001）是 miss；扫描在真实案例上只发出 {findings['total_findings_issue_cases']} 条 finding，0 条与故障相关（1 条误导、2 条顺带）。**

## 3. 四类问题必须分开看

- **用户构图问题（1 例，唯一可表达案例，但 miss）**：`lg-6446` 的 fixture 是标准 ACR001 形态——子图无 `output_schema`、与父图共享键 `b`、并与写 `b` 的 `nodeb` 并行。miss 的原因不是规则不存在，而是 fixture 把子图构建放在 `build_child()` helper 中并返回编译后的 graph；scanner 不跨函数解析返回值，**helper function boundary -> miss**。这是 ACR001 的分析深度/召回缺口。
- **框架内部 bug（GAP 的主体）**：13 个 GAP 中 10 个盲标为 `framework`（另 usage 2、feature 1）。这些是 LangGraph 的 reducer 解包、checkpoint 往返、cache 命名空间、retry/interrupt 恢复、executor 语义等内部行为；**静态构图规则本来就不覆盖，不能算 ACR 的 false-negative 能力问题**。正确修法常常是升级框架或上游 PR，而不是改用户图，ACR 不应把它们包装成用户错误。
- **规则误报（1 例）**：`lg-9311` 的 `findings: ReadOnly[Annotated[list[str], operator.add]]` 本身有 reducer，scan 却报 ACR002 说 "has no reducer"。这是 ReadOnly 限定符未解包导致的**误导性 finding（false_positive/misleading）**——现有 `_annotated_parts` 只解包 `Required`/`NotRequired`，不识别 `ReadOnly`——不是 gap hit。
- **顺带发现（2 例）**：`lg-9276`（resume 重放）和 `lg-9084`（tags 重复）各被报 1 条 ACR001，但这两条来自 fixture 复用同一 `State` 类的副作用，与 issue 故障无关，记为 **incidental/unrelated**，不算 hit。

## 4. 逐案例判定

| case | source_type(盲标) | expected | scan | verdict | 依据（摘要） |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}

## 5. 最值得补的缺口（3-5 个）

GAP 分布（13 例）：{', '.join(f"`{category}` {info['count']}" for category, info in gap['by_category'].items())}；按盲标 source_type：{', '.join(f"{key} {value}" for key, value in gap['by_source_type_blind'].items())}。

1. **ACR001 跨函数解析（P0，唯一可表达案例 miss）**：`lg-6446` 证明「helper 返回 compiled subgraph」会让现有 ACR001 完全失效。这是本次试点里最直接的规则召回缺口，且完全静态可修。
2. **reducer 注解限定符解包（P0，误报）**：`lg-9311` 证明 `ReadOnly` 包住 `Annotated[..., reducer]` 时，ACR002 会误判为「no reducer」（现有实现只解包 `Required`/`NotRequired`）。必须先解包限定符再做 reducer 发现，否则会给用户错误修法建议。
3. **declared-keys 能力（P1，2 例）**：`lg-8320`、`lg-2644` 都是节点返回未声明键、被框架静默过滤；现有 11 条规则无覆盖。这是最适合新增静态规则的缺口之一（节点返回 dict 字面量 vs TypedDict/dataclass 字段）。
4. **cache/retry 身份与命名空间（P1，2 例）**：`lg-8753`（非函数 callable 的 cache namespace 退化为 `__dynamic__`）可做静态身份检查；`lg-9253`（retry 后第二个子图续用第一个的 checkpoint）本质是运行时命名空间/位置身份问题，只能给可疑提示，不能静态断言。
5. **resume/replay 与 parallel interrupt（P1/P2，运行时）**：`lg-9276`、`lg-9106`（以及 `lg-9086` 的 checkpoint 往返）依赖恢复/重放运行时语义与版本，应交给 `acr trace` 或版本感知检测，**不应硬塞进静态规则**。

## 6. v0.2 建议（优先级排序）

- **P0-1｜ACR001 跨函数解析**：跟踪函数返回值与局部变量绑定，解析 `add_node("x", build_child())`、`child = build_child(); add_node("x", child)` 以及 helper 返回 `compile()` 后注册的形态。验收：`lg-6446` 报 ACR001；3 个 controls 仍 0；新增 helper-return 单测，防止误报正常 helper。
- **P0-2｜reducer 限定符解包**：在 reducer 发现前解包 `typing_extensions.ReadOnly`（`Required`/`NotRequired` 已支持，补上回归测试），同步修正 ACR002 与聚合分析。验收：`lg-9311` 不再报 ACR002；新增限定符 × reducer 矩阵单测；该 case 仍按盲标算 GAP（框架行为），不得记为 hit。
- **P1-1｜declared-keys 新规则**：检测节点返回键不在 state schema 中（含 class node 返回另一 schema 的情况）。验收：`lg-8320`、`lg-2644` 命中；controls 0；message 要指出声明位置与会被静默丢弃的键。
- **P1-2｜cache/retry 的静态/运行时分工**：把 cache 键身份（callable 的 `__qualname__`/`__module__` 缺失、partial、跨图共享 cache）做成静态检查（覆盖 `lg-8753`）；retry 与子图 checkpoint 命名空间（`lg-9253`）只做可疑提示或 trace 检测，不在静态规则里下结论。
- **P1-3｜评测口径补强**：在 `summary.json` 里把 miss 细分 `semantic_gap` / `analysis_depth_gap` / `material_gap`（本批：`lg-6446` = analysis_depth_gap，`lg-7580` = material_gap）；记录 fixture 的版本要求（`lg-8608` ≥1.2、`lg-9084` ≥1.2.3），否则跨版本复跑会把不表现行为的 fixture 误判为 miss。
- **P2｜框架内部语义保持边界**：`Command.update` MRO 重复（`lg-9005`）、`Overwrite` 未解包（`lg-9019`）、tuple reducer checkpoint 往返（`lg-9086`）、`error_handler` 重抛（`lg-8608`）、tags 重复（`lg-9084`）属框架/上游问题。可以进 `acr trace` 或版本感知提示，但不进静态构图规则，避免把「该升级 LangGraph」包装成「你的图有问题」。

## 7. 分歧与冻结记录

- **source_type 分歧（1 例）**：`lg-8320` 盲标 `feature`（issue 实际请求 warning/strict 的诊断功能），fixture 元数据写 `framework`。本报告一律用盲标；未修改 labels 或 fixtures。它只影响 GAP 按 source_type 的分布（盲标：feature 1、framework 10），不影响总缺口数。
- **graph_visible 分歧（1 例）**：`lg-7580` 盲标 `graph_visible=false`（issue 复现是 channel 级，没有构图），fixture 元数据写 true（fixture 重建了隐含拓扑）。本报告按盲标把该 case 记为 `not_applicable`（材料不足），不计入可分析率、不算 miss；其缺口 `GAP:reducer-semantics` 仍计入缺口分布并标注 not_analyzable。
- **口径复核**：任务摘要曾把 GAP 的 source_type 记为「framework 9、usage 2、feature 1、user_graph 1」；`labels.json` 的 13 个 GAP 实际为 **framework 10 / usage 2 / feature 1 / user_graph 0**（总分布为 framework 10 / usage 2 / feature 1 / user_graph 1，lg-6446 是 user_graph 但预期 ACR001）。报告与 summary.json 以 labels.json 为准。
- **误报判定**：`lg-9311` 的 ACR002 记 `false_positive/misleading`，不计入 gap hit；`lg-9276`、`lg-9084` 的 ACR001 记 `incidental/unrelated`，也不计入 hit。

## 8. 限制

{chr(10).join(f'- {limitation}' for limitation in summary['limitations'])}

## 9. 复现

```sh
# 汇总（离线、不联网、不调用模型；会校验 labels sha256）
PYTHONPATH=src python3 scripts/summarize_realworld_eval.py

# 归档扫描（本报告对应的 scan.txt 来源）
for d in evals/realworld/cases/*/; do
  PYTHONPATH=src python3 -m agent_composition_review scan "$d" --fail-on never --json
done
```

输出：`evals/realworld/results/{PILOT}/predictions.json`、`summary.json`、`REPORT.md`。
"""
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline summarizer for the real-world LangGraph pilot."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=f"output directory (default: evals/realworld/results/{PILOT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate inputs and print a summary, but write no files",
    )
    parser.add_argument(
        "--skip-hash-check",
        action="store_true",
        help="do not fail when labels.json differs from the frozen sha256",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    realworld = root / "evals" / "realworld"
    labels_path = realworld / "labels.json"
    if not labels_path.is_file():
        print(f"error: labels not found: {labels_path}", file=sys.stderr)
        return 2

    actual_hash = sha256_file(labels_path)
    if actual_hash != LABELS_SHA256 and not args.skip_hash_check:
        print(
            "error: labels.json sha256 mismatch; blind labels are frozen.\n"
            f"  expected: {LABELS_SHA256}\n"
            f"  actual:   {actual_hash}\n"
            "refusing to summarize (use --skip-hash-check only for local debugging).",
            file=sys.stderr,
        )
        return 2

    predictions, warnings = collect_predictions(root)
    summary = build_summary(root, predictions, warnings)
    report = build_report(summary, predictions)

    counts = summary["counts"]
    metrics = summary["metrics"]
    print(
        f"pilot={PILOT} cases={counts['issue_cases']} controls={counts['normal_controls']} "
        f"analyzable={counts['analyzable_cases']} expressible={counts['expressible_cases']} "
        f"hit_rate={_fmt_percent(metrics['hit_rate'])} "
        f"control_fpr={_fmt_percent(metrics['control_false_positive_rate'])} "
        f"warnings={len(warnings)}"
    )

    if args.check:
        print("check only: no files written")
        return 0

    out_dir = args.out_dir.resolve() if args.out_dir else realworld / "results" / PILOT
    out_dir.mkdir(parents=True, exist_ok=True)

    predictions_doc = {
        "schema_version": SCHEMA_VERSION,
        "pilot": PILOT,
        "generated_by": GENERATED_BY,
        "frozen_inputs": summary["frozen_inputs"],
        "cases": predictions,
    }
    (out_dir / "predictions.json").write_text(
        json.dumps(predictions_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")

    print(f"wrote {out_dir / 'predictions.json'}")
    print(f"wrote {out_dir / 'summary.json'}")
    print(f"wrote {out_dir / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
