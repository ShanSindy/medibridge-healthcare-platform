"""
Regression tests: pytest runs that skip the repository-root conftest.py
(and so the disposable test database) must be refused.

Each case starts a separate pytest process with dummy database settings that
point at a closed port, and targets tests/test_db_safety.py, which never
connects to a database. Even if a guard regressed, nothing could reach a
real database.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR
REFUSAL = "repository-root conftest.py was not loaded"


def _dummy_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DB_", "JWT_", "MEDIBRIDGE_", "TEST_DB_", "PYTEST_", "COV_"))
    }
    env.update(
        DB_HOST="127.0.0.1",
        DB_PORT="9",  # discard port: nothing listens there
        DB_USER="guard_check",
        DB_PASSWORD="not-a-real-password",
        DB_NAME="guard_check_no_database",
        PYTHON_DOTENV_DISABLED="1",
    )
    return env


def _run_pytest(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # Fixed arguments built in this module; no untrusted input.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args],
        cwd=cwd,
        env=_dummy_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "cwd"),
    [
        (["--noconftest", "tests/test_db_safety.py"], ROOT),
        (["--noconftest", "test_db_safety.py"], ROOT / "tests"),
        (["--confcutdir=.", "test_db_safety.py"], ROOT / "tests"),
    ],
    ids=["noconftest-from-root", "noconftest-from-tests", "confcutdir-from-tests"],
)
def test_runs_that_skip_the_isolation_setup_are_refused(args, cwd):
    result = _run_pytest(args, cwd)
    output = result.stdout + result.stderr

    assert result.returncode == USAGE_ERROR, output
    assert REFUSAL in output
    assert " passed" not in output


def test_the_guard_plugin_is_loaded_for_every_run():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = options["tool"]["pytest"]["ini_options"]
    assert ini["addopts"] == ["-p", "tests.isolation_guard"]
    assert ini["pythonpath"] == ["."]


def test_the_guard_plugin_is_active_in_this_session(pytestconfig):
    assert pytestconfig.pluginmanager.has_plugin("tests.isolation_guard")
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
