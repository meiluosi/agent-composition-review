#!/usr/bin/env python3
"""Package only runtime skill instructions; exclude tests, answers and maintainer docs."""

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/agent-composition-review.zip")
    args = parser.parse_args()
    paths = [ROOT / "SKILL.md"]
    for name in ["agents", "references"]:
        paths.extend(p for p in (ROOT / name).rglob("*") if p.is_file())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "agent-composition-review/" + str(path.relative_to(ROOT)))
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(paths)
    print(f"Packaged {len(paths)} runtime files: {args.output}")


if __name__ == "__main__":
    main()
