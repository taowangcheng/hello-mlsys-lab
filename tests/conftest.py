import shutil
import uuid
from pathlib import Path
import pytest
from mlab.core.io import ROOT


def pytest_addoption(parser):
    parser.addoption("--backend", choices=["all", "fixture", "llm", "standin"], default="all", help="Select shared backend contract cases")


@pytest.fixture
def workspace_path():
    parent = ROOT / ".test-runs"
    parent.mkdir(exist_ok=True)
    path = parent / uuid.uuid4().hex
    yield path
    resolved = path.resolve()
    assert resolved.is_relative_to(parent.resolve()) and resolved != parent.resolve()
    if resolved.exists():
        shutil.rmtree(resolved)
