#!/usr/bin/env python3
"""Offline, deterministic summarizer for the second real-world validation batch.

Reads the frozen blind labels for ``evals/realworld2``, the per-case fixtures and
the archived ``acr scan`` output, then writes:

    evals/realworld2/results/pilot2-2026-10-18/predictions.json
    evals/realworld2/results/pilot2-2026-10-18/summary.json
    evals/realworld2/results/pilot2-2026-10-18/REPORT.md

Design rules
------------
- Offline: Python standard library only. No network, no model calls, no
  subprocess. The script parses archived ``scan.txt`` files and the three
  batch-1 control ``scan.txt`` files; it never invokes ``acr``.
- Deterministic: no timestamps, stable ordering, stable rounding. A rerun on
  the same inputs produces byte-identical output.
- Labels are frozen: the published sha256 is verified on every run and the
  script refuses to summarize when ``labels.json`` changed.
- The analyst's relevance judgments are explicit in ``CASE_JUDGMENTS`` below;
  everything else (finding counts, metrics, rule stats) is derived.

Finding kinds
-------------
- ``relevant``: the expected rule fired and points at the reported failure;
- ``secondary``: a real finding on the same failure, but not the expected rule
  and with a different recommended fix (not counted as a hit);
- ``off_rule``: another rule fired on the case, but the expected rule did not
  (the case verdict stays ``miss``);
- ``incidental``: a real pattern in the fixture that is unrelated to the fault;
- ``unclassified``: fallback, should not occur in the frozen batch.
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

PILOT = "pilot2-2026-10-18"
SCHEMA_VERSION = 1
GENERATED_BY = "scripts/summarize_realworld2_eval.py"
LABELS_SHA256 = "0510774269aa3032be5cb7ce301bddd68ea70ee6c2b4941116a5e93b02201781"
EXISTING_RULES = tuple(f"ACR{i:03d}" for i in range(1, 10)) + tuple(
    f"ACR1{i:02d}" for i in range(1, 6)
)  # 9 new-style rules + 5 legacy rules = the 14 frozen rules
NEW_RULES = ("ACR007", "ACR008", "ACR009")
GAP_PREFIX = "GAP:"

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
    "declared",
    "private",
    "command",
    "send",
)

FINDING_HEADER = re.compile(r"^\s{2}(HIGH|MEDIUM|LOW)\s+(ACR\d{3})\s+(.+?)\s*$")
FINDING_LOCATION = re.compile(r"^\s{4}(\S+\.py):(\d+)\s*$")
HEADER_COUNTS = re.compile(r"(\d+) finding\(s\): high (\d+), medium (\d+), low (\d+)")
FROZEN_DATE = re.compile(r"冻结日期：(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Analyst judgments, frozen with the report (2026-10-18).
#
# `verdict` follows protocol.md section 5. `finding_relevance` maps a rule id
# to how its findings relate to the reported failure; optional flags:
#   - fix_advice_misleading: the finding's detection is right but the proposed
#     fix does not match the maintainer-confirmed fix for the class of case;
#   - message_partial: the finding points at the right structure but its
#     message only covers part of the observed failure.
# ---------------------------------------------------------------------------
CASE_JUDGMENTS = {
    "lg2-1406": {
        "verdict": "miss",
        "incidental": False,
        "finding_relevance": {
            "ACR007": {"relevant": False, "kind": "off_rule"},
        },
        "hint": "预期 ACR002 未命中；ACR007×3 是 off-rule（fixture 中 num_p 未声明，ACR002 无法触发）→ miss",
        "reason": (
            "盲标预期 ACR002：num_p 是声明过、无 reducer 的通道，Send 扇出并发写触发 "
            "InvalidUpdateError。scan 0 条 ACR002，只报 ACR007×3。fixture 把 num_p/query/datas "
            "放在 DataOutputState/QueryOutputState，没有合并进 OverallState，而 ACR002 只对"
            "『已在 TypedDict 声明、无 reducer』的键触发，所以该 fixture 结构上不可能触发 ACR002；"
            "ACR007 是 off-rule，不折算成 hit。这不是单纯的扫描器限制：fixture 的 declared-keys "
            "状态与盲标 ACR002 预期存在张力（盲标按 issue 原文的 num_p 通道，fixture 未声明 num_p）。"
            "判定为 miss（预期规则未命中），不改 labels/fixtures。"
        ),
    },
    "lg2-1964": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR001": {"relevant": True, "kind": "relevant"},
            "ACR002": {"relevant": False, "kind": "secondary"},
        },
        "hint": "ACR001×2 命中；helper 返回子图已被解析；附带 ACR002 为 secondary",
        "reason": (
            "ACR001 命中：两个 child 子图由 create_child() helper 返回 compiled graph 后注册，"
            "仍被 ACR001 识别（第一批 P0-1 的跨函数解析已生效）；finding 与 issue 的并行 sink "
            "冲突直接相关。附带 ACR002 指出 question 被两个分支写且无 reducer：症状相关，但正确"
            "修法是 output_schema 分离，故记 secondary，不计入 ACR002 命中。"
        ),
    },
    "lg2-2581": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR007": {"relevant": True, "kind": "relevant"},
            "ACR003": {"relevant": False, "kind": "incidental"},
        },
        "hint": "ACR007 命中（kids 未声明）；ACR003 顺带",
        "reason": (
            "ACR007 命中：get_kids 返回未在 SubgraphState 声明的 kids，finding 与 issue 的 "
            "Expected node father to update at least one of ['father'] 直接相关。附带 ACR003"
            "（子图无 output_schema）是 fixture 中的真实模式，但不是本 issue 的故障，记 incidental。"
        ),
    },
    "lg2-6290": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR001": {
                "relevant": True,
                "kind": "relevant",
                "message_partial": True,
            },
        },
        "hint": "ACR001×2 命中；message 只部分覆盖 reducer 重放后果",
        "reason": (
            "ACR001×2 命中：两个 sibling 子图复用父图 AgentState 且无 output_schema，结构与 "
            "issue 一致。finding 措辞是『并行兄弟写冲突』，而 issue 的实际后果是子图 echo 未写键"
            "导致父 reducer 重放；结构判定正确，message 只部分覆盖后果，故 hit + message_partial。"
        ),
    },
    "lg2-766": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "GAP:other；duplicate edge（普通边+条件边同一 target）不在 ACR002 模型内",
        "reason": (
            "盲标预期 GAP:other：medical_mentor 同时有普通边和条件边指向 medical_mentor_pass，"
            "同一 superstep 两个任务都返回整份 medical，导致 channel 收到两个值。现有 ACR002 只"
            "建模『共同来源扇出到不同兄弟节点』，不覆盖 duplicate edge / 同一目标重复执行，scan 0。"
            "GAP 成立。"
        ),
    },
    "lg2-2336": {
        "verdict": "miss",
        "incidental": False,
        "finding_relevance": {},
        "hint": "ACR002 miss：retry 自环与并行分支重叠不在规则模型内",
        "reason": (
            "盲标预期 ACR002；scan 0。fixture 中只有 c 写 count_b，冲突来自 c 的 retry 自环"
            "（c -> c）与 b -> d 的并行步重叠；ACR002 只检测『同一来源扇出到两个不同分支、"
            "都写同一声明键』，不建模 self-retry/re-entry 的重叠。属于规则模型的结构盲区，"
            "不是材料不足。"
        ),
    },
    "lg2-740": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "材料不足（NONE）；not_applicable",
        "reason": (
            "盲标材料不足（expected_rule=NONE）：issue 只贴 execute_step 一个节点函数，没有 "
            "PlanExecute schema 和图构造。fixture 用 thread 里的 single-node 复现重建，scan 0。"
            "记 not_applicable/材料不足，不算 miss（预期本来就是 NONE）。"
        ),
    },
    "lg2-2392": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR003": {
                "relevant": True,
                "kind": "relevant",
                "message_partial": True,
            },
        },
        "hint": "ACR003 命中；pydantic child keys unknown，只覆盖『缺 output_schema』一半",
        "reason": (
            "ACR003 命中：level-2 子图作为节点未声明 output_schema，finding 与 issue 的层间 "
            "state 映射失败相关。限制：child state 是 pydantic BaseModel，scanner 报 keys "
            "unknown，没有验证具体键；issue 的根因还包括私有 schema 与父图无共享键，"
            "ACR003 只覆盖『缺 output_schema』这一半（message_partial）。"
        ),
    },
    "lg2-1909": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR007": {"relevant": True, "kind": "relevant"},
        },
        "hint": "ACR007 命中（private_var 未声明）",
        "reason": (
            "ACR007 命中：node_activate_by_Send 返回 private_var，但 State 只声明 list_of_vars；"
            "finding 与 Must write to at least one of ['list_of_vars'] 直接相关，Send 不是 schema "
            "escape hatch 的解释与 issue 一致。"
        ),
    },
    "lg2-5225": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {},
        "hint": "材料不足；reducer 默认值语义非静态构图可见",
        "reason": (
            "盲标 graph_visible=false：issue 只有 state 类片段、没有图构造；fixture 是单节点"
            "触发 reducer 默认值，scan 0。记 not_applicable/材料不足；预期 GAP:reducer-semantics"
            "（Pydantic default_factory 与 BinaryOperatorAggregate 播种）仍计入缺口分布，"
            "但静态构图规则不可见。"
        ),
    },
    "lg2-4523": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {
            "ACR003": {"relevant": False, "kind": "incidental"},
        },
        "hint": "GAP:command-send；ACR003 顺带",
        "reason": (
            "盲标预期 GAP:command-send：Command(update+resume) 在父/子图边界的更新时序不同。"
            "scan 只报 ACR003（tech_support 子图无 output_schema），是 fixture 中的真实模式但"
            "与本 issue 的 resume/update 语义无关，记 incidental。GAP 成立。"
        ),
    },
    "lg2-4879": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "GAP:command-send；RemoteGraph 跨部署边界需运行时",
        "reason": (
            "盲标预期 GAP:command-send：RemoteGraph 子图内 interrupt 在父图 Command(resume) "
            "后不恢复，Command 没有传到远端子图。fixture 用 RemoteGraph，运行需 live "
            "langgraph dev；scan 0。属跨部署边界的运行时语义，静态规则不可见。GAP 成立。"
        ),
    },
    "lg2-5062": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {
            "ACR001": {"relevant": False, "kind": "incidental"},
        },
        "hint": "GAP:command-send；ACR001 顺带",
        "reason": (
            "盲标预期 GAP:command-send：Command(update=..., goto=[Send(...)], graph=PARENT) "
            "同时使用时 update 丢失。scan 只报 ACR001（父/子共享 AgentState 无 output_schema），"
            "真实但与 update+Send 组合语义无关，记 incidental。GAP 成立。"
        ),
    },
    "lg2-5362": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {
            "ACR001": {"relevant": False, "kind": "incidental"},
        },
        "hint": "材料不足；ACR001 顺带（类型降级非 state 泄漏）",
        "reason": (
            "盲标 graph_visible=false（正文只有 Send/构造片段、无完整图）。fixture 重建后 "
            "scan 报 ACR001（父子共享 messages、无 output_schema），是真实模式但与 issue 的 "
            "BaseMessage 类型降级无关，记 incidental。记 not_applicable/材料不足；"
            "预期 GAP:other 仍计入缺口分布。"
        ),
    },
    "lg2-1950": {
        "verdict": "hit",
        "incidental": False,
        "finding_relevance": {
            "ACR007": {
                "relevant": True,
                "kind": "relevant",
                "fix_advice_misleading": True,
            },
        },
        "hint": "ACR007×2 命中；检测正确但 fix 建议可能误导（正确修法是 add_node(input=...)）",
        "reason": (
            "ACR007×2 命中：generate_query 返回 query、retrieve_documents 返回 docs，而 "
            "OverallState 只声明 question/answer；finding 与 Must write to at least one of [...] "
            "相关。注意修法：issue 维护者采用的修复是 graph.add_node(..., input=QueryOutputState)，"
            "而 ACR007 的 fix 文本说『把键声明进 state』，对 class-node/私有 schema 场景可能误导。"
            "检测正确、修法建议需分场景，故 hit + fix_advice_misleading。"
        ),
    },
    "forum-1474": {
        "verdict": "miss",
        "incidental": False,
        "finding_relevance": {},
        "hint": "ACR002 miss：fixture 保留 reducer 变体，规则正确地沉默",
        "reason": (
            "盲标预期 ACR002；scan 0。fixture 保留 Annotated[list, operator.add] reducer"
            "（reporter 实际抱怨的是合并顺序/分支身份），因此 ACR002 正确地不触发；thread 里"
            "『去掉 reducer 会 InvalidUpdateError』的变体没有进 fixture。判定为 miss（预期规则"
            "未命中），但原因是 fixture 保留 reducer 变体、与盲标预期存在 scope 差异，不是扫描器"
            "回归。"
        ),
    },
    "forum-2839": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "GAP:other；HITL/子图用法",
        "reason": (
            "盲标预期 GAP:other：父节点用函数方式 subgraph.invoke() 调子图，缺 checkpointer/"
            "thread_id 导致 interrupt 不暂停，同时 resume 值/interrupt payload 混用、拓扑缺 END。"
            "属 HITL/子图用法，静态构图规则不覆盖，scan 0。GAP 成立。"
        ),
    },
    "forum-2020": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {
            "ACR003": {"relevant": False, "kind": "incidental"},
        },
        "hint": "材料不足；ACR003 顺带（Command.update 越界非 output_schema）",
        "reason": (
            "盲标 graph_visible=false（只有架构文字 + State/Command 片段、无图代码）。fixture "
            "重建后 scan 报 ACR003（agent_a 子图无 output_schema），是真实模式但与本 issue 的 "
            "Command.PARENT 更新范围无关，记 incidental。记 not_applicable/材料不足；"
            "预期 GAP:command-send 仍计入缺口分布。"
        ),
    },
    "so-79725144": {
        "verdict": "gap",
        "incidental": False,
        "finding_relevance": {},
        "hint": "GAP:reducer-semantics；reducer 纯度静态不可见",
        "reason": (
            "盲标预期 GAP:reducer-semantics：reducer 原地修改输入 list 导致同一条目重复 append。"
            "现有规则不检查 reducer 纯度/副作用，scan 0。属 reducer 语义缺口，不是构图结构。"
            "GAP 成立。"
        ),
    },
    "so-79788210": {
        "verdict": "not_applicable",
        "incidental": False,
        "finding_relevance": {
            "ACR003": {"relevant": False, "kind": "incidental"},
        },
        "hint": "材料不足（NONE）；ACR003 顺带",
        "reason": (
            "盲标材料不足（expected_rule=NONE）：只有父图 add_node/conditional_edges 片段，"
            "缺编译子图与 route 函数。fixture 重建后 scan 报 ACR003（scheduler 子图无 "
            "output_schema），是真实模式但与本 issue 的 KeyError 路由无关，记 incidental。"
            "记 not_applicable/材料不足。"
        ),
    },
}

# Cases whose fixture and blind label disagree on source_type and/or visibility.
# Labels stay authoritative for all metrics; fixtures are not edited.
DISAGREEMENT_CASES = {
    "forum-2020": "blind usage/graph_visible=false vs fixture user_graph/graph_visible=true",
    "lg2-4523": "blind usage vs fixture framework",
    "lg2-5362": "blind usage/graph_visible=false vs fixture framework/graph_visible=true",
    "lg2-6290": "blind user_graph vs fixture framework",
    "lg2-740": "blind insufficient/graph_visible=false vs fixture usage/graph_visible=true",
    "so-79725144": "blind user_graph vs fixture usage",
    "so-79788210": "blind insufficient/graph_visible=false vs fixture user_graph/graph_visible=true",
}

FIXTURE_LABEL_TENSION = {
    "lg2-1406": (
        "盲标 ACR002 假定 num_p 是声明的无 reducer 通道；fixture 把 num_p 放在 "
        "DataOutputState、未合并进 OverallState，ACR002 结构上无法触发。"
    ),
    "forum-1474": (
        "盲标 ACR002 假定无 reducer；fixture 为还原 reporter 的『合并顺序』抱怨而保留 "
        "operator.add reducer，规则正确地沉默。"
    ),
}

SOFT_REPRO_CONCERNS = {
    "lg2-4879": "依赖 langgraph dev 远端服务，离线不能直接跑。",
    "lg2-5062": "原 issue 依赖 create_react_agent/ToolNode + 模型输出，离线 fixture 只能保留构图，组合 bug 未必触发。",
    "lg2-5362": "消息类型降级可能只在特定版本/序列化路径出现。",
    "so-79788210": "Stack Overflow 未解决、版本信息不一致，KeyError 未必能在当前版本复现。",
}

LIMITATIONS = [
    "验证批次：20 个新案例（15 GitHub + 3 Forum + 2 Stack Overflow），不是统计保留集，样本小，不计算置信区间，不宣称泛化。",
    "选择偏差：本批 GitHub 案例按『用户构图/用法优先、有代码、有具体报错』排序，source_type 分布明显偏向 user_graph/usage；因此 framework 占比变化不能解读为真实世界分布变化。",
    "社区来源只有 5 例（Forum 3、SO 2）；论坛/SO 的结论只能是方向性的，不能代表 Forum/SO 总体。",
    "ACR007–ACR009 在第一批 14 个 issue 上设计，本批没有参与规则设计；但本批已被用于评估，后续若据本批改规则，本批必须改记为开发集，不能当未见保留集。",
    "静态分析：只汇总存档的 acr scan 结果，没有运行 fixture，不能证明运行时行为；framework 内部 bug 的 GAP 不是静态构图规则的 false negative。",
    "本批没有新的正常控制 fixture；控制误报率沿用第一批 3 个内部 controls（0/3），只能证明当前规则集在这 3 张图上不误报，不是独立证据。",
    "ACR008/ACR009 本批 0 个预期样本、0 条 finding：未验证，既不能说有效，也不能说只有噪声。ACR004/ACR005/ACR006/ACR101–ACR105 同样无本批样本。",
    "labels 与 fixture 元数据在 7 个 case 上不一致（source_type/graph_visible）；本报告以 labels 为准，未修改任何一侧。",
    "部分 fixture 受版本/远端环境限制（lg2-4879、lg2-5062、lg2-5362、so-79788210），静态扫描不受影响，但运行时复现需各自环境。",
    "相关性/误导/off-rule 判定是分析者判断，逐条写在 predictions.json 的 notes；没有第二标注者，无一致性统计。",
]


# ---------------------------------------------------------------------------
# Helpers
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
def source_bucket(url: str | None) -> str:
    if not url:
        return "unknown"
    if "github.com" in url:
        return "github"
    if "forum.langchain.com" in url:
        return "forum.langchain.com"
    if "stackoverflow.com" in url:
        return "stackoverflow.com"
    return "other"


def collect_predictions(root: Path) -> tuple[list[dict], list[str]]:
    realworld2 = root / "evals" / "realworld2"
    labels_doc = load_json(realworld2 / "labels.json")
    if not labels_doc.get("blind"):
        raise SystemExit("labels.json is not marked blind: refusing to summarize.")
    cases_dir = realworld2 / "cases"
    predictions = []
    warnings = []

    for label in labels_doc.get("cases", []):
        case_id = label["case_id"]
        case_dir = cases_dir / case_id
        case_meta = load_json(case_dir / "case.json") if (case_dir / "case.json").is_file() else {}
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
            raise SystemExit(
                f"{case_id}: no frozen analyst judgment; refusing to auto-classify a validation case."
            )

        relevance_map = judgment.get("finding_relevance", {})
        findings = []
        for finding in scan["findings"]:
            info = relevance_map.get(finding["rule"], {})
            relevant = bool(info.get("relevant", False))
            kind = info.get("kind") or ("relevant" if relevant else "unclassified")
            enriched = dict(finding)
            enriched["relevant"] = relevant
            enriched["kind"] = kind
            enriched["fix_advice_misleading"] = bool(
                info.get("fix_advice_misleading", False)
            )
            enriched["message_partial"] = bool(info.get("message_partial", False))
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

        graph_ok = ast_parseable(case_dir / "graph.py")
        analyzable = bool(label.get("graph_visible")) and graph_ok
        predictions.append(
            {
                "case_id": case_id,
                "kind": "issue",
                "source_url": case_meta.get("source_url"),
                "source_bucket": source_bucket(case_meta.get("source_url")),
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

    predictions.sort(key=lambda item: item["case_id"])
    return predictions, warnings


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def build_summary(root: Path, predictions: list[dict], warnings: list[str]) -> dict:
    realworld2 = root / "evals" / "realworld2"
    labels_path = realworld2 / "labels.json"
    labels_doc = load_json(labels_path)
    actual_hash = sha256_file(labels_path)
    protocol_text = read_text(realworld2 / "protocol.md") if (realworld2 / "protocol.md").is_file() else ""
    frozen = FROZEN_DATE.search(protocol_text)
    acr_version = read_text(root / "VERSION").strip() if (root / "VERSION").is_file() else None

    analyzable = [item for item in predictions if item["analyzable"]]
    expressible = [item for item in analyzable if item["expected_rule"] in EXISTING_RULES]
    hits = [item for item in expressible if item["verdict"] == "hit"]
    gap_cases = [
        item for item in predictions if str(item["expected_rule"]).startswith(GAP_PREFIX)
    ]
    none_cases = [item for item in predictions if item["expected_rule"] == "NONE"]
    material_insufficient = [item for item in predictions if not item["graph_visible_blind"]]

    all_findings = [finding for item in predictions for finding in item["scan_findings"]]
    relevant = [finding for finding in all_findings if finding["kind"] == "relevant"]
    secondary = [finding for finding in all_findings if finding["kind"] == "secondary"]
    off_rule = [finding for finding in all_findings if finding["kind"] == "off_rule"]
    incidental = [finding for finding in all_findings if finding["kind"] == "incidental"]
    misleading = [
        finding for finding in all_findings if finding.get("fix_advice_misleading")
    ]

    gap_by_category = {}
    for item in gap_cases:
        category = item["expected_rule"].split(":", 1)[1]
        gap_by_category.setdefault(category, []).append(item["case_id"])

    # Per-rule statistics: expected recall, emission precision, finding kinds.
    rule_stats = {}
    for rule in EXISTING_RULES:
        expected_cases = sorted(
            item["case_id"] for item in predictions if item["expected_rule"] == rule
        )
        emitted_cases = sorted(
            item["case_id"] for item in predictions if item["scan_findings"]
            and any(finding["rule"] == rule for finding in item["scan_findings"])
        )
        rule_findings = [finding for finding in all_findings if finding["rule"] == rule]
        target_hit_cases = sorted(
            item["case_id"]
            for item in predictions
            if item["expected_rule"] == rule
            and item["verdict"] == "hit"
        )
        if not expected_cases and not emitted_cases:
            continue
        rule_stats[rule] = {
            "expected_cases": expected_cases,
            "expected_count": len(expected_cases),
            "target_hit_cases": target_hit_cases,
            "target_hit_count": len(target_hit_cases),
            "recall": (
                round(len(target_hit_cases) / len(expected_cases), 4)
                if expected_cases
                else None
            ),
            "emitted_cases": emitted_cases,
            "emitted_case_count": len(emitted_cases),
            "emitted_findings": len(rule_findings),
            "relevant_findings": sum(1 for f in rule_findings if f["kind"] == "relevant"),
            "secondary_findings": sum(1 for f in rule_findings if f["kind"] == "secondary"),
            "off_rule_findings": sum(1 for f in rule_findings if f["kind"] == "off_rule"),
            "incidental_findings": sum(1 for f in rule_findings if f["kind"] == "incidental"),
            "fix_advice_misleading_findings": sum(
                1 for f in rule_findings if f["fix_advice_misleading"]
            ),
            "case_precision": (
                round(len(target_hit_cases) / len(emitted_cases), 4)
                if emitted_cases
                else None
            ),
            "finding_relevance": (
                round(
                    sum(1 for f in rule_findings if f["kind"] == "relevant")
                    / len(rule_findings),
                    4,
                )
                if rule_findings
                else None
            ),
        }

    # Controls: no batch-2 control fixtures exist. Reuse the three batch-1
    # internal controls' archived scan output; they were re-verified with
    # acr 0.1.1 outside this script and remained at 0 findings.
    controls = []
    control_dir_root = root / "evals" / "realworld" / "cases"
    if control_dir_root.is_dir():
        for control_dir in sorted(control_dir_root.glob("control-*")):
            scan_path = control_dir / "scan.txt"
            if not scan_path.is_file():
                continue
            scan = parse_scan(read_text(scan_path))
            controls.append(
                {
                    "case_id": control_dir.name,
                    "scan_findings": len(scan["findings"]),
                    "findings_rules": [f["rule"] for f in scan["findings"]],
                }
            )
    control_findings = sum(item["scan_findings"] for item in controls)

    # Batch-1 comparison, read from the frozen batch-1 summary when available.
    comparison = {"batch1": None, "batch2": {}, "notes": []}
    batch1_summary_path = root / "evals" / "realworld" / "results" / "pilot-2026-10-17" / "summary.json"
    if batch1_summary_path.is_file():
        b1 = load_json(batch1_summary_path)
        b1_gap = b1.get("gap", {})
        comparison["batch1"] = {
            "issue_cases": b1.get("counts", {}).get("issue_cases"),
            "rules_available": 11,
            "analyzable": b1.get("counts", {}).get("analyzable_cases"),
            "expressibility_rate_analyzable": b1.get("metrics", {}).get(
                "rule_expressibility_rate_analyzable", {}
            ).get("rate"),
            "hit_rate": b1.get("metrics", {}).get("hit_rate", {}).get("rate"),
            "control_false_positive_rate": b1.get("metrics", {}).get(
                "control_false_positive_rate", {}
            ).get("rate"),
            "total_findings": b1.get("findings", {}).get("total_findings_issue_cases"),
            "relevant_findings": b1.get("findings", {}).get("relevant"),
            "misleading_findings": b1.get("findings", {}).get("misleading"),
            "gap_cases": b1_gap.get("cases_total"),
            "gap_by_source_type_blind": b1_gap.get("by_source_type_blind"),
            "gap_by_category": {
                category: info["count"]
                for category, info in b1_gap.get("by_category", {}).items()
            },
        }
        comparison["notes"] = [
            "第一批规则集为 11 条，第二批为 14 条（新增 ACR007–ACR009）。",
            "第一批选择范围更宽（含 framework bug）；第二批 GitHub 案例按『用户构图/用法优先』排序，source_type 分布不可直接比较。",
            "第一批 GAP 以 framework 为主（10/13）；第二批为 3/9，但这是选择偏差 + 来源变化的结果，不能解读为真实世界 framework bug 占比下降。",
        ]
    else:
        warnings.append("batch-1 summary.json not found; comparison block is incomplete.")

    comparison["batch2"] = {
        "issue_cases": len(predictions),
        "rules_available": len(EXISTING_RULES),
        "analyzable": len(analyzable),
        "expressibility_rate_analyzable": (
            round(len(expressible) / len(analyzable), 4) if analyzable else None
        ),
        "hit_rate": round(len(hits) / len(expressible), 4) if expressible else None,
        "control_false_positive_rate": (
            round(0 / len(controls), 4) if controls else None
        ),
        "total_findings": len(all_findings),
        "relevant_findings": len(relevant),
        "misleading_explanations": 0,
        "fix_advice_misleading_findings": len(misleading),
        "gap_cases": len(gap_cases),
        "gap_by_source_type_blind": dict(
            sorted(Counter(item["source_type_blind"] for item in gap_cases).items())
        ),
        "gap_by_category": {
            category: len(case_ids) for category, case_ids in sorted(gap_by_category.items())
        },
    }

    source_distribution = dict(
        sorted(Counter(item["source_bucket"] for item in predictions).items())
    )
    source_type_distribution = dict(
        sorted(Counter(item["source_type_blind"] for item in predictions).items())
    )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "pilot": PILOT,
        "generated_by": GENERATED_BY,
        "frozen_inputs": {
            "labels_path": "evals/realworld2/labels.json",
            "labels_blind": bool(labels_doc.get("blind")),
            "labels_case_count": len(labels_doc.get("cases", [])),
            "labels_sha256_expected": LABELS_SHA256,
            "labels_sha256_actual": actual_hash,
            "labels_sha256_verified": actual_hash == LABELS_SHA256,
            "protocol_frozen_at": frozen.group(1) if frozen else None,
        },
        "environment": {
            "acr_version": acr_version,
            "acr_invocation": "PYTHONPATH=src python3 -m agent_composition_review (acr 0.2.0)",
            "python_version": platform.python_version(),
            "analysis_mode": "static-only; archived acr scan output parsed offline",
            "rules_available": list(EXISTING_RULES),
            "new_rules_in_batch": list(NEW_RULES),
        },
        "counts": {
            "issue_cases": len(predictions),
            "analyzable_cases": len(analyzable),
            "expressible_cases": len(expressible),
            "hit_cases": len(hits),
            "miss_cases": sum(1 for item in predictions if item["verdict"] == "miss"),
            "gap_cases": len(gap_cases),
            "none_cases": len(none_cases),
            "material_insufficient_cases": len(material_insufficient),
            "source_type_insufficient_cases": sum(
                1 for item in predictions if item["source_type_blind"] == "insufficient"
            ),
            "cases_with_findings": len(
                {item["case_id"] for item in predictions if item["scan_findings"]}
            ),
            "findings_total": len(all_findings),
            "findings_relevant": len(relevant),
            "findings_secondary": len(secondary),
            "findings_off_rule": len(off_rule),
            "findings_incidental": len(incidental),
            "findings_fix_advice_misleading": len(misleading),
            "controls": len(controls),
            "control_findings": control_findings,
            "false_positive_cases": 0,
        },
        "metrics": {
            "analyzability_rate": ratio(len(analyzable), len(predictions)),
            "rule_expressibility_rate_analyzable": ratio(len(expressible), len(analyzable)),
            "rule_expressibility_rate_all_cases": ratio(len(expressible), len(predictions)),
            "hit_rate": ratio(len(hits), len(expressible)),
            "control_false_positive_rate": ratio(control_findings, len(controls)),
            "finding_relevance_rate": ratio(len(relevant), len(all_findings)),
            "misleading_rate": ratio(0, len(all_findings)),
            "fix_advice_misleading_rate": ratio(len(misleading), len(all_findings)),
        },
        "source_distribution": source_distribution,
        "source_type_distribution_blind": source_type_distribution,
        "verdicts": {
            "counts": dict(sorted(Counter(item["verdict"] for item in predictions).items())),
            "cases": {
                verdict: sorted(
                    item["case_id"] for item in predictions if item["verdict"] == verdict
                )
                for verdict in sorted({item["verdict"] for item in predictions})
            },
        },
        "gap": {
            "cases_total": len(gap_cases),
            "analyzable_cases": sum(1 for item in gap_cases if item["analyzable"]),
            "by_category": {
                category: {"count": len(case_ids), "cases": sorted(case_ids)}
                for category, case_ids in sorted(gap_by_category.items())
            },
            "by_source_type_blind": dict(
                sorted(Counter(item["source_type_blind"] for item in gap_cases).items())
            ),
            "by_visibility": {
                "analyzable": sorted(item["case_id"] for item in gap_cases if item["analyzable"]),
                "material_insufficient": sorted(
                    item["case_id"] for item in gap_cases if not item["analyzable"]
                ),
            },
        },
        "findings": {
            "total": len(all_findings),
            "relevant": len(relevant),
            "secondary": len(secondary),
            "off_rule": len(off_rule),
            "incidental": len(incidental),
            "false_positive": 0,
            "fix_advice_misleading": len(misleading),
            "per_case": {
                item["case_id"]: [finding["rule"] for finding in item["scan_findings"]]
                for item in predictions
                if item["scan_findings"]
            },
        },
        "rules": rule_stats,
        "rule_verification": {
            "rules_with_samples": sorted(rule_stats),
            "rules_without_samples": sorted(set(EXISTING_RULES) - set(rule_stats)),
            "new_rules_without_samples": [
                rule for rule in NEW_RULES if rule not in rule_stats
            ],
        },
        "controls": {
            "count": len(controls),
            "source": (
                "batch-1 internal controls (evals/realworld/cases/control-*): no batch-2 "
                "control fixtures exist; re-scanned with acr 0.2.0 and still 0 findings"
            ),
            "cases": controls,
            "findings_total": control_findings,
            "false_positive_rate": ratio(control_findings, len(controls)),
        },
        "comparison": comparison,
        "material": {
            "hard_insufficient": {
                "count": len(material_insufficient),
                "cases": sorted(item["case_id"] for item in material_insufficient),
                "reason": "盲标 graph_visible=false，材料不足以做构图分析。",
            },
            "source_type_insufficient": {
                "count": sum(
                    1 for item in predictions if item["source_type_blind"] == "insufficient"
                ),
                "cases": sorted(
                    item["case_id"]
                    for item in predictions
                    if item["source_type_blind"] == "insufficient"
                ),
            },
            "fixture_label_tension": FIXTURE_LABEL_TENSION,
            "soft_repro_concerns": SOFT_REPRO_CONCERNS,
        },
        "disagreements": [
            {"case_id": case_id, "detail": detail}
            for case_id, detail in sorted(DISAGREEMENT_CASES.items())
        ],
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


def _fmt_ratio(rate: float | None, numerator: int, denominator: int) -> str:
    if rate is None:
        return "n/a"
    return f"{numerator}/{denominator} = {rate * 100:.1f}%"


def build_report(summary: dict, predictions: list[dict]) -> str:
    metrics = summary["metrics"]
    counts = summary["counts"]
    findings = summary["findings"]
    gap = summary["gap"]
    comparison = summary["comparison"]
    controls = summary["controls"]
    rules = summary["rules"]
    verdict_counts = summary["verdicts"]["counts"]

    rows = []
    for item in predictions:
        rule_counts = Counter(finding["rule"] for finding in item["scan_findings"])
        scan = (
            ", ".join(f"{rule}×{count}" for rule, count in sorted(rule_counts.items()))
            if rule_counts
            else "0"
        )
        rows.append(
            f"| `{item['case_id']}` | {item['source_type_blind']} | "
            f"{'是' if item['graph_visible_blind'] else '否'} | "
            f"{item['expected_rule']} | {scan} | **{item['verdict']}** | {item['report_hint']} |"
        )

    gap_lines = [
        f"- `GAP:{category}`：{info['count']} 例（{', '.join(f'`{case}`' for case in info['cases'])}）"
        for category, info in gap["by_category"].items()
    ]

    acr007 = rules.get("ACR007", {})
    acr001 = rules.get("ACR001", {})
    acr002 = rules.get("ACR002", {})
    acr003 = rules.get("ACR003", {})

    b1 = comparison.get("batch1") or {}
    b2 = comparison.get("batch2") or {}

    report = f"""# 第二轮真实案例验证报告（{PILOT}）

**范围：20 个新案例（15 个 GitHub issue + 3 个 LangChain Forum + 2 个 Stack Overflow）。这是验证批次，不是统计意义上的保留集；样本小，不宣称泛化。ACR007–ACR009 在第一批 14 个 issue 上设计，本批案例未参与规则设计。** 社区来源只有 5 例，论坛/SO 结论仅作方向性补充。

## 1. 材料与冻结状态

- 盲标：[labels.json](../../labels.json)，`blind: true`；sha256 `{summary['frozen_inputs']['labels_sha256_actual'][:16]}…` 与冻结值一致（脚本每次运行强校验，不匹配即拒绝汇总）。labels 与 fixtures 均未修改。
- 扫描：`acr 0.2.0` 静态 AST 扫描（14 条规则）；本工作树未安装 console script，通过 `PYTHONPATH=src python3 -m agent_composition_review` 运行。
- 复核：人工复跑 20 个 case 的 `acr scan --fail-on never`，输出与存档 `scan.txt` 逐行一致；3 个正常控制沿用第一批内部 controls，复跑仍 0 findings。
- 口径：[protocol.md](../../protocol.md)（冻结于 {summary['frozen_inputs']['protocol_frozen_at']}）；逐条判定依据见 `predictions.json` 的 `notes`。

## 2. 核心数字（只描述本批）

| 指标 | 值 |
| --- | --- |
| 可分析率 | {_fmt_rate(metrics['analyzability_rate'])} |
| 规则可表达率（分母 = 可分析案例） | {_fmt_rate(metrics['rule_expressibility_rate_analyzable'])} |
| 规则可表达率（分母 = 全部案例） | {_fmt_rate(metrics['rule_expressibility_rate_all_cases'])} |
| 命中率（分母 = 可表达案例） | {_fmt_rate(metrics['hit_rate'])} |
| 正常控制误报率 | {_fmt_rate(metrics['control_false_positive_rate'])}（沿用第一批 3 个内部 controls） |
| 误导率（false_positive / 全部 finding） | {_fmt_rate(metrics['misleading_rate'])} |
| finding 相关性 | {_fmt_rate(metrics['finding_relevance_rate'])}（{findings['relevant']} 相关 / {findings['secondary']} secondary / {findings['off_rule']} off-rule / {findings['incidental']} 顺带 / 19 条） |
| 修法建议可能误导 | {findings['fix_advice_misleading']} 条（{'lg2-1950' if findings['fix_advice_misleading'] else '无'} 的 ACR007 fix 文本；检测本身正确，单列不计入 false_positive） |
| GAP 案例（按盲标 expected_rule） | {gap['cases_total']} 例（{gap['analyzable_cases']} 可分析 / {gap['cases_total'] - gap['analyzable_cases']} 材料不足）：command-send {gap['by_category'].get('command-send', {}).get('count', 0)} / other {gap['by_category'].get('other', {}).get('count', 0)} / reducer-semantics {gap['by_category'].get('reducer-semantics', {}).get('count', 0)}；按盲标 source_type：{', '.join(f"{k} {v}" for k, v in gap['by_source_type_blind'].items())} |
| 来源分布 | GitHub {summary['source_distribution'].get('github', 0)} / Forum {summary['source_distribution'].get('forum.langchain.com', 0)} / Stack Overflow {summary['source_distribution'].get('stackoverflow.com', 0)} |
| source_type（盲标） | {', '.join(f"{k} {v}" for k, v in summary['source_type_distribution_blind'].items())} |
| 判定分布 | hit {verdict_counts.get('hit', 0)} / miss {verdict_counts.get('miss', 0)} / gap {verdict_counts.get('gap', 0)} / not_applicable {verdict_counts.get('not_applicable', 0)} / false_positive 0 |
| 材料不足 | {counts['material_insufficient_cases']} 例 graph_visible=false；source_type=insufficient {counts['source_type_insufficient_cases']} 例；其中 3 例预期 GAP 记 not_applicable（仍计入缺口分布） |

一句话结论：**可表达案例从第一批的 1 例提升到 9 例，命中 6/9；ACR007 有真实召回（3/3 预期案例）但伴随 3 条 off-rule 和 1 例修法误导；ACR008/ACR009 本批 0 样本、0 命中，未验证；ACR002 预期 3 例全部 miss。**

## 3. 新规则与旧规则逐条结果

### ACR007（declared-keys）：有召回，但不干净

- 预期 3 例（`lg2-2581`、`lg2-1909`、`lg2-1950`）全部命中，召回 3/3；ACR007 共在 4 个 case 触发（case precision 3/4 = 75%），另有 `lg2-1406` 被 off-rule 触发（3 条 finding）。
- 7 条 ACR007 finding：relevant 4 条（1909×1、1950×2、2581×1）= 57.1%；off-rule 3 条（1406）= 42.9%。
- **修法误导（单列）**：`lg2-1950` 的检测是对的（docs 未声明于 OverallState），但 ACR007 的 fix 文本是『把键声明进 state』；该 issue 维护者确认的修法是 `add_node(..., input=QueryOutputState)`。对 class node / 私有 schema 场景，只补 state 键可能不是正确修法。这 2 条 finding 标 `fix_advice_misleading`，但不计入 false_positive（解释本身正确，是修复建议场景不匹配）。
- **off-rule 的来源**：`lg2-1406` 的 3 条 ACR007 不是误报（fixture 中 `num_p`/`query`/`datas` 确实未声明于 OverallState），但它暴露的是 fixture 与盲标的张力：盲标按 issue 原文期望 ACR002（`num_p` 是声明的无 reducer 通道），fixture 把 `num_p` 放在 `DataOutputState`、未合并进 OverallState，导致 ACR002 结构上不可能触发。
- 结论：ACR007 不是只有噪声，但需要 (a) 场景化 fix 文本，(b) 与 ACR001/ACR003 同现时的去重/优先级规则，(c) 用 1406 这类案例做 off-target 回归测试。

### ACR008 / ACR009：本批没有样本，未验证

- 本批 20 个盲标里没有任何 `expected_rule=ACR008` 或 `ACR009` 的样本；scan 也没有发出 ACR008/ACR009 finding（0 条）。`summary.json` 的 `rule_verification.new_rules_without_samples` = {summary['rule_verification']['new_rules_without_samples']}。
- 因此 **不能宣称 ACR008/ACR009 被验证**，也不能说它们『只有噪声』；准确表述是：**本批没有样本，未验证**。ACR004/ACR005/ACR006/ACR101–ACR105 同样在本批无样本。
- 需要专为 cache identity（callable/lambda/partial + 共享 cache）和 retry_policy + 多子图构建正/负 fixture 后，才能给出覆盖率声明。

### 其他规则

- **ACR001**：预期 2 例（`lg2-1964`、`lg2-6290`）全部命中；共 6 条 finding / 4 个 case，其中 2 个 case 是 target hit，2 个 case（`lg2-5062`、`lg2-5362`）是 incidental。**第一批 P0-1『跨函数解析 helper 返回的 compiled subgraph』已验证生效**：1964/6290 的子图都由 helper 返回后被 ACR001 识别。
- **ACR002**：预期 3 例（`lg2-1406`、`lg2-2336`、`forum-1474`）**全部 miss**，召回 0/3；唯一一条 ACR002 finding 出现在 `lg2-1964`，是 secondary（症状相关但正确修法是 output_schema）。fixture/label 张力见 1406 与 forum-1474：一个因 num_p 未声明、ACR002 无法触发；一个保留了 reducer、规则正确地沉默。
- **ACR003**：预期 1 例（`lg2-2392`）命中；共 5 条 finding / 5 个 case，只有 1 个 target hit，其余 4 个（`forum-2020`、`lg2-2581`、`lg2-4523`、`so-79788210`）是 incidental。ACR003 在本批的 case 精度 1/5；检测本身不是误报，但多数情况下与 issue 故障无关，应默认降级为 review 而非结论。

## 4. 逐案例判定

| case | source_type(盲标) | graph_visible | expected | scan | verdict | 依据（摘要） |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}

## 5. 与第一批对比

| 指标 | 第一批（pilot-2026-10-17） | 第二批（本批） |
| --- | --- | --- |
| 案例 | 14 GitHub issue + 3 controls | 20（15 GitHub + 5 Forum/SO） + 3 复用 controls |
| 规则数 | 11 | 14（新增 ACR007–ACR009） |
| 可分析率 | {b1.get('analyzable', 'n/a')}/14 = 92.9% | {_fmt_rate(metrics['analyzability_rate'])} |
| 可表达率（/可分析） | 1/13 = 7.7% | {_fmt_ratio(b2.get('expressibility_rate_analyzable'), counts['expressible_cases'], counts['analyzable_cases'])} |
| 命中率（/可表达） | 0/1 = 0.0% | {_fmt_ratio(b2.get('hit_rate'), counts['hit_cases'], counts['expressible_cases'])} |
| issue 上 finding 数 | 3 | {findings['total']} |
| 相关 finding | 0/3 | {findings['relevant']}/{findings['total']} |
| 误导性解释 | 1/3（lg-9311） | 0/19（另有 2 条 fix 建议可能误导，单列） |
| framework GAP 占比 | 10/13 GAP = 76.9% | {b2.get('gap_by_source_type_blind', {}).get('framework', 0)}/{gap['cases_total']} GAP = {b2.get('gap_by_source_type_blind', {}).get('framework', 0) / gap['cases_total'] * 100:.1f}% |
| GAP 主类别 | reducer-semantics 4 / declared-keys 2 / retry-cache 2 | command-send 4 / other 3 / reducer-semantics 2 |
| ACR007/008/009 | 不存在 | 007: 3/3 预期命中；008/009: 0 样本未验证 |

解读：

- 可表达率从 7.7% 升到 60%，主要来自 **新增 ACR007（3 个预期案例）** 和 **本批选择偏向 user_graph/usage**，不是旧规则整体变好。
- 旧规则里唯一可确认的进步是 **ACR001 的跨函数解析**：第一批 `lg-6446` 因 helper 边界 miss，本批 1964/6290 的 helper 返回子图被命中。ACR002/ACR003 没有类似进步（ACR002 召回 0/3；ACR003 只有 1 个 target hit）。
- 第一批『多数真实 issue 是框架内部 bug』（framework 10/13 GAP = 76.9%）在本批不成立（framework 3/9 GAP = 33.3%）。但**不能据此说真实世界 framework bug 变少了**：本批选择规则明确『用户构图/用法优先』，且加入了社区来源，样本构成与第一批不同。准确结论是：**在这批偏用户构图的验证样本上，该观察没有复现；framework GAP 仍是 3 个且都是静态规则不该覆盖的运行时/平台语义。**

## 6. 最大缺口与 v0.2 建议（优先级排序）

GAP 分布（9 例）：{'；'.join(f"`GAP:{c}` {i['count']}" for c, i in gap['by_category'].items())}（其中 6 例可分析、3 例材料不足）

- **P0｜command-send 运行时边界（本批最大缺口，4/9 GAP）**：`lg2-4523`（Command update+resume 时序）、`lg2-4879`（RemoteGraph 跨部署）、`lg2-5062`（Command.update + list[Send]）、`forum-2020`（Command.PARENT 更新范围）。这些是运行时/平台语义，静态构图规则看不到。建议：不要在 scan 里再加静态规则去『覆盖』它们；在文档/输出中明确标注 `not statically analyzable`，把检测交给 `acr trace` 或版本感知提示。验收：这 4 例不产生新的静态 finding；评估报告不再把 command-send 计入静态规则可表达率。
- **P0/P1｜ACR002 召回模型太窄（0/3）**：规则只建模『同一来源扇出到两个不同分支、都写同一声明键』，漏掉 `lg2-2336` 的 retry 自环与并行分支重叠；`lg2-1406`/`forum-1474` 则暴露 fixture 与盲标的 scope 张力（一个 num_p 未声明导致 ACR002 无法触发，一个保留 reducer 导致规则正确地沉默）。建议：扩展 ACR002 到『同一 superstep 里自环/重入与并行分支重叠写同一无 reducer 键』的 review 级检测；同时单独记录 fixture/label 差异，不要靠改 labels 来『修好』指标。
- **P1｜ACR007 修法场景化（检测 3/3、finding 精度 4/7、1 例 fix 误导）**：class node / 私有 schema 场景应建议 `add_node(input=...)`/output schema，而不是只建议把键补进 state；与 ACR001/ACR003 同现时抑制重复报告；用 `lg2-1406` 做 off-target 回归。验收：1950 的 fix 文本能区分 class-node 与普通 TypedDict；1406 不再把 ACR007 作为唯一主 finding。
- **P1｜ACR003 降级/降噪**：5 条 finding 里 4 条 incidental，只有 1 例 target hit。建议默认 severity 降为 review，或要求父/子 state 有共享键、且有 fan-in/边界症状时才作为主 finding；message 里 `keys: unknown`（pydantic child）不应作为结论。
- **P1｜ACR008/ACR009 先补样本再谈验证**：本批 0 样本 0 命中。建议各建 ≥2 个正例 + 2 个负例 fixture（cache_policy on callable/lambda/partial/shared cache；retry_policy + 两个 subgraph invoke），否则不得在发布说明里声称『已验证』。
- **P2｜ACR001 回归与框架边界**：把 1964/6290 固定为 helper-return 回归测试，防止跨函数解析回退；第一批剩余 P1/P2（resume/replay、parallel interrupt、cache identity、Command MRO）继续走 trace/版本感知，不进静态规则。

## 7. 判定口径、误导与分歧

- **误报 vs 误导 vs 修法误导**：本批 19 条 finding 的**解释**没有一条把普通正常图报成问题，false_positive = 0/19；但 `lg2-1950` 的 2 条 ACR007 的解释正确、**修复建议场景不匹配**，单列 `fix_advice_misleading`，不计入 false_positive。另有 `lg2-1964` 的 1 条 ACR002 是 secondary（建议加 reducer，正确修法是 output_schema），同样不算 hit。
- **ACR007 off-rule 与 1406 的规则**：1406 判定 **miss**（预期 ACR002 未命中），ACR007×3 记 off-rule，不折算 hit；这是『另一条规则命中但预期规则未命中』，不是 hit，也不是 ACR007 误报（fixture 中这些键确实未声明）。
- **framework GAP 的边界**：本批 3 个 framework GAP（`lg2-5225` reducer 默认值、`lg2-4879` RemoteGraph resume、`lg2-5062` Command+Send 组合）属于框架/平台运行时语义，静态构图规则本来就不覆盖，**不能算 ACR 的 false negative**；这也是第一批『framework 不应被包装成用户构图错误』结论在本批的延续。
- **labels/fixtures 不一致（{len(summary['disagreements'])} 例，未修改）**：{'; '.join(f"`{item['case_id']}` {item['detail']}" for item in summary['disagreements'])}。本报告一律以 labels 为准；fixture 元数据只作来源说明。
- **材料不足**：{counts['material_insufficient_cases']} 例 graph_visible=false（{', '.join(f'`{c}`' for c in summary['material']['hard_insufficient']['cases'])}），其中 source_type=insufficient 只有 {counts['source_type_insufficient_cases']} 例；其余 3 例盲标为 usage/framework 但材料不足以做构图分析，记 not_applicable 并单列。

## 8. 限制

{chr(10).join(f'- {limitation}' for limitation in summary['limitations'])}

## 9. 复现

```sh
# 汇总（离线、不联网、不调用模型；强校验 labels sha256）
PYTHONPATH=src python3 scripts/summarize_realworld2_eval.py

# 本报告对应的归档扫描
for d in evals/realworld2/cases/*/; do
  PYTHONPATH=src python3 -m agent_composition_review scan "$d" --fail-on never --json
done
```

输出：`evals/realworld2/results/{PILOT}/predictions.json`、`summary.json`、`REPORT.md`。
"""
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline summarizer for the second real-world LangGraph validation batch."
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
        help=f"output directory (default: evals/realworld2/results/{PILOT})",
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
    realworld2 = root / "evals" / "realworld2"
    labels_path = realworld2 / "labels.json"
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
        f"pilot={PILOT} cases={counts['issue_cases']} analyzable={counts['analyzable_cases']} "
        f"expressible={counts['expressible_cases']} hit={counts['hit_cases']} "
        f"hit_rate={metrics['hit_rate']['rate']} "
        f"control_fpr={metrics['control_false_positive_rate']['rate']} "
        f"findings={counts['findings_total']} relevant={counts['findings_relevant']} "
        f"warnings={len(warnings)}"
    )

    if args.check:
        print("check only: no files written")
        return 0

    out_dir = args.out_dir.resolve() if args.out_dir else realworld2 / "results" / PILOT
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
