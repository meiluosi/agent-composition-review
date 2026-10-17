"""Offline metadata tests for the composite action and example workflow.

These tests only parse YAML files; they never install the package, clone the
repository, or execute ``acr``. PyYAML is expected from the dev extra.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_PATH = ROOT / "action.yml"
EXAMPLE_PATH = ROOT / ".github" / "workflows" / "acr-example.yml"
DOCS_PATH = ROOT / "docs" / "github-action.md"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def action_steps(action: dict) -> list[dict]:
    return action["runs"]["steps"]


def run_steps(action: dict) -> list[dict]:
    return [step for step in action_steps(action) if "run" in step]


def test_action_metadata_name_and_kind() -> None:
    action = load_yaml(ACTION_PATH)
    assert action["name"] == "ACR scan"
    assert action["description"].strip()
    assert action["runs"]["using"] == "composite"


def test_action_inputs_are_documented_with_defaults() -> None:
    inputs = load_yaml(ACTION_PATH)["inputs"]
    assert set(inputs) == {"path", "fail-on", "args"}
    assert inputs["path"]["default"] == "."
    assert inputs["fail-on"]["default"] == "high"
    assert inputs["args"]["default"] == ""
    for choice in ("high", "review", "never"):
        assert choice in inputs["fail-on"]["description"]


def test_action_sets_up_python_and_installs_matching_ref() -> None:
    action = load_yaml(ACTION_PATH)
    setup = [
        step
        for step in action_steps(action)
        if step.get("uses", "").startswith("actions/setup-python@")
    ]
    assert len(setup) == 1
    assert setup[0]["with"]["python-version"] == "3.12"

    install = next(
        step
        for step in run_steps(action)
        if "git+https://github.com/meiluosi/agent-composition-review.git@" in step["run"]
    )
    assert (
        'agent-composition-review @ git+https://github.com/meiluosi/'
        "agent-composition-review.git@" in install["run"]
    )
    assert "${ACR_ACTION_REF:-main}" in install["run"]
    assert install["env"]["ACR_ACTION_REF"] == "${{ github.action_ref }}"


def test_action_runs_scan_with_quoted_inputs() -> None:
    scan = next(
        step for step in run_steps(load_yaml(ACTION_PATH)) if "acr scan" in step["run"]
    )
    assert scan["env"] == {
        "ACR_INPUT_PATH": "${{ inputs.path }}",
        "ACR_FAIL_ON": "${{ inputs['fail-on'] }}",
        "ACR_EXTRA_ARGS": "${{ inputs.args }}",
    }
    assert (
        'acr scan "${ACR_INPUT_PATH}" --fail-on "${ACR_FAIL_ON}" ${ACR_EXTRA_ARGS}'
        in scan["run"]
    )
    # The composite action validates fail-on itself because action inputs have
    # no choice type.
    assert "high|review|never" in scan["run"]


def test_composite_run_steps_declare_bash() -> None:
    for step in run_steps(load_yaml(ACTION_PATH)):
        assert step.get("shell") == "bash", step.get("name")


def test_action_only_runs_scan() -> None:
    text = ACTION_PATH.read_text(encoding="utf-8")
    for unsupported in ("acr trace", "acr report", "acr demo"):
        assert unsupported not in text


def test_example_workflow_is_manual_and_uses_local_action() -> None:
    workflow = load_yaml(EXAMPLE_PATH)
    # PyYAML 1.1 parses the `on` key as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["scan"]["runs-on"] == "ubuntu-latest"

    steps = workflow["jobs"]["scan"]["steps"]
    checkout = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkout) == 1
    assert checkout[0]["with"]["persist-credentials"] is False

    local = [step for step in steps if step.get("uses") == "./"]
    assert len(local) == 1
    assert local[0]["with"] == {"path": ".", "fail-on": "high"}
    # The example must not recurse into the published action.
    assert not any(
        "meiluosi/agent-composition-review@" in step.get("uses", "") for step in steps
    )


def test_docs_pin_a_release_and_state_the_experimental_boundary() -> None:
    docs = DOCS_PATH.read_text(encoding="utf-8")
    assert re.search(r"uses: meiluosi/agent-composition-review@v\d+\.\d+\.\d+", docs)
    for token in ("path", "fail-on", "args", "acr scan"):
        assert token in docs
    assert "--experimental" in docs
    assert "默认规则集" in docs
    assert "实验性规则" in docs
