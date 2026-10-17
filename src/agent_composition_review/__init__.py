"""agent-composition-review: composition diagnostics for LangGraph/LangChain.

The public surface is the ``acr`` CLI (``acr scan``, ``acr trace``,
``acr report``, ``acr demo``, ``acr rules``).  Python callers can import the
scan/trace helpers and result models directly.
"""

from __future__ import annotations

from .model import (
    SCHEMA_VERSION,
    Finding,
    ScanResult,
    TraceEvent,
    TraceResult,
)

__version__ = "0.5.0"

__all__ = [
    "SCHEMA_VERSION",
    "Finding",
    "ScanResult",
    "TraceEvent",
    "TraceResult",
    "__version__",
]
