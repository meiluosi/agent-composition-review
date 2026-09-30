#!/usr/bin/env python3
"""Package only runtime skill instructions; exclude tests, answers and maintainer docs."""

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "skills/agent-composition-review"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Runtime source directory; default is current main skill, not a candidate")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output or ROOT / "dist" / ("agent-composition-review.zip" if source == DEFAULT_SOURCE else "agent-composition-review-candidate.zip")
    if source == ROOT:
        raise SystemExit("Use a dedicated runtime directory, not the maintenance repository root")
    paths = [source / "SKILL.md"]
    for name in ["agents", "references", "scripts", "assets"]:
        paths.extend(p for p in (source / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for path in paths:
        if not path.resolve().is_relative_to(source):
            raise SystemExit("Runtime symlink escapes source: " + str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "agent-composition-review/" + str(path.relative_to(source)))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(paths)
    print(f"Packaged {len(paths)} runtime files: {output}")


if __name__ == "__main__":
    main()
