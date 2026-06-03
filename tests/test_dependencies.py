from __future__ import annotations

import tomllib
from pathlib import Path


def test_httpx_dependency_includes_socks_extra():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("httpx[socks]") for dependency in dependencies)
