from __future__ import annotations

from pathlib import Path
import sys

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def reset_sessions():
    """Isolate each test by clearing the in-memory session store."""
    from app.core.state import _sessions
    _sessions.clear()
    yield
    _sessions.clear()
