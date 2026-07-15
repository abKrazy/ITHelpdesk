"""Static contracts for the local Python environment used by azd hooks."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agents_extra_contains_postprovision_dependencies() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    agents = project["project"]["optional-dependencies"]["agents"]
    assert "azure-search-documents==11.7.0b2" in agents
    assert "azure-ai-projects==2.3.0" in agents


def test_preprovision_bootstraps_root_venv_before_prompts() -> None:
    powershell = _read("scripts/preprovision.ps1")
    posix = _read("scripts/preprovision.sh")

    for script in (powershell, posix):
        assert ".venv" in script
        assert "-m venv" in script
        assert "-m pip install" in script
        assert ".[agents]" in script

    assert powershell.index("-m pip install") < powershell.index("function Get-AzdEnvValue")
    assert posix.index("-m pip install") < posix.index("get_val()")


def test_postprovision_uses_managed_python_without_changing_acr() -> None:
    powershell = _read("scripts/postprovision.ps1")
    posix = _read("scripts/postprovision.sh")

    for script in (powershell, posix):
        assert "az acr build" in script
        assert "--no-logs" in script

    assert "..\\.venv\\Scripts\\python.exe" in powershell
    assert '"$(dirname "$0")/../.venv/bin/python"' in posix
    assert 'python "$PSScriptRoot/postprovision.py"' not in powershell
    assert 'python "$(dirname "$0")/postprovision.py"' not in posix