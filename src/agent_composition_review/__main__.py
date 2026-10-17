"""Allow ``python -m agent_composition_review`` to behave like ``acr``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
