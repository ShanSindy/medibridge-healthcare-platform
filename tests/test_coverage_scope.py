"""
The coverage scope in pyproject.toml is an explicit list. These tests keep it
complete: every Python file tracked by git must be either measured or one of
the deliberate exclusions, so a new module can never drop out of coverage
silently, and untracked local files can never be counted.
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
COVERAGE_INCLUDE = set(PYPROJECT["tool"]["coverage"]["report"]["include"])


def _deliberately_excluded(path: str) -> bool:
    return (path.startswith("tests/") and Path(path).name.startswith("test_")) or path.startswith(
        "scripts/"
    )


def _tracked_python_files() -> set[str]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git checkout not available")
    # Fixed arguments; git is resolved with shutil.which. No untrusted input.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return set(result.stdout.split())


def test_every_tracked_python_file_is_measured_or_deliberately_excluded():
    tracked = _tracked_python_files()
    unmeasured = sorted(
        p for p in tracked if p not in COVERAGE_INCLUDE and not _deliberately_excluded(p)
    )
    assert unmeasured == [], f"Add these to [tool.coverage.report] include: {unmeasured}"


def test_coverage_scope_lists_only_tracked_files():
    tracked = _tracked_python_files()
    assert sorted(COVERAGE_INCLUDE - tracked) == []


def test_test_cases_and_ci_scripts_are_not_measured():
    assert not any(_deliberately_excluded(p) for p in COVERAGE_INCLUDE)


def test_poorly_covered_modules_stay_in_scope():
    # patients.py is unused sample data at 0%; it must keep counting.
    assert {
        "patients.py",
        "appointment_service.py",
        "provider_service.py",
        "migrate.py",
    } <= COVERAGE_INCLUDE


def test_database_safety_infrastructure_is_measured():
    assert {
        "conftest.py",
        "tests/conftest.py",
        "tests/db_safety.py",
        "tests/disposable_database.py",
        "tests/isolation_guard.py",
    } <= COVERAGE_INCLUDE


def test_pytest_collects_only_the_tests_directory():
    assert PYPROJECT["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
