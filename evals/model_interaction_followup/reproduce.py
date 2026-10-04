"""Post-hoc stream-parser counterexample; never replaces the frozen score."""
import hashlib
import json
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "evals/iterations/model-interaction-2026-10-04"
HERE = Path(__file__).resolve().parent
DISCOVERY_KEY = "2c2982447f7bea923d2c"
CONSTANTS = ("NaN", "Infinity", "-Infinity")


def frozen_case():
    return next(case for case in json.loads((RUN / "cases.json").read_text()) if case["case_id"] == "m08")


def load_source(source, name):
    module = types.ModuleType(name)
    exec(compile(source, "<" + name + ">", "exec"), module.__dict__)
    return module


def original_api():
    return load_source(frozen_case()["source"], "frozen_m08")


def revised_api():
    return load_source((HERE / "stream_reference.py").read_text(), "post_hoc_reference")


def masked_constant(value):
    return ('{"event":' + value + ',"event":"done"}\n').encode("utf-8")


def observe(api, wire):
    parser = api.EventStream()
    try:
        result = parser.feed(wire)
    except api.StreamError:
        return {"outcome": "StreamError", "state": parser.state,
                "buffered_bytes": parser.buffered_bytes}
    return {"outcome": "accepted", "state": parser.state,
            "buffered_bytes": parser.buffered_bytes, "events": result}


def reproduction():
    case = frozen_case()
    original, revised = original_api(), revised_api()
    return {
        "post_hoc": True,
        "purpose": "Counterexample and revised reference; not a replacement score or a holdout.",
        "case_id": "m08",
        "discovery": {"model": "gpt-6-astra", "condition": "A_tools", "trial": 1,
                      "result_key": DISCOVERY_KEY},
        "frozen_source_sha256": hashlib.sha256(case["source"].encode()).hexdigest(),
        "revised_source_sha256": hashlib.sha256((HERE / "stream_reference.py").read_bytes()).hexdigest(),
        "counterexamples": [
            {"constant": value, "wire": masked_constant(value).decode(),
             "frozen_reference": observe(original, masked_constant(value)),
             "revised_reference": observe(revised, masked_constant(value))}
            for value in CONSTANTS
        ],
        "duplicate_only_control": {
            "wire": '{"event":"done","event":"done"}\n',
            "frozen_reference": observe(original, b'{"event":"done","event":"done"}\n'),
            "revised_reference": observe(revised, b'{"event":"done","event":"done"}\n'),
            "note": "The revision preserves the original last-value behavior; the task did not explicitly ban duplicate names.",
        },
    }


if __name__ == "__main__":
    print(json.dumps(reproduction(), ensure_ascii=False, indent=2))
