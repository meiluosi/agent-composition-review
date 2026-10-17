"""Offline checks for the static atlas site."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs" / "atlas"


def test_atlas_site_indexes_all_cases() -> None:
    html = (ATLAS / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/json" id="case-data">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None
    data = json.loads(match.group(1))
    cases = data["cases"]
    assert len(cases) == 11
    slugs = {case["slug"] for case in cases}
    for slug in slugs:
        assert (ATLAS / f"{slug}.md").is_file(), slug
    assert "site.js" in html
    assert "site.css" in html


def test_atlas_site_has_no_external_scripts_or_styles() -> None:
    html = (ATLAS / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert not re.search(r'<link[^>]+href="https?://[^"]+\.css', html)
