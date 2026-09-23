from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

import pytest

from sunkcost_spec.resources import SPEC_ROOT


@pytest.fixture(scope="session")
def reference() -> ModuleType:
    """The independent reference implementation that generated the golden vectors."""
    name = "sunkcost_spec_gen_vectors"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SPEC_ROOT / "tools" / "gen_vectors.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
