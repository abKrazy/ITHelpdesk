"""Shared pytest fixtures / import bootstrapping for the mock-mode suites.

Guarantees two things BEFORE any ``helpdesk`` / ``servicenow`` module is imported:

  * ``HELPDESK_MOCK=1`` — the whole stack runs offline (local KB search + the
    in-memory ServiceNow mock), so the suite needs no live Azure or ServiceNow.
  * ``src/`` is importable — works from a fresh clone that hasn't been
    ``pip install -e .``-ed yet (mirrors ``tests/test_smoke.py``).

Importing this module (pytest does so automatically) has the side effects; the
env var is also set at collection time in ``pytest_configure`` for safety.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HELPDESK_MOCK", "1")

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    os.environ.setdefault("HELPDESK_MOCK", "1")
