from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

import pytest

from sunkcost_spec.resources import SPEC_ROOT


def _load_generator() -> ModuleType:
    path = SPEC_ROOT / "tools" / "gen_vectors.py"
    spec = importlib.util.spec_from_file_location("gen_vectors", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def gen() -> ModuleType:
    """The reference implementation in tools/gen_vectors.py."""
    return _load_generator()
