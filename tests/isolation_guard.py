"""
pytest plugin that refuses any test run in which the database isolation setup
did not run.

It is loaded for every pytest invocation through `addopts` in pyproject.toml
(`-p tests.isolation_guard`), which `--noconftest` does not disable. The
repository-root conftest.py creates the disposable test database and marks
the pytest config when it has done so; if that mark is missing when
configuration finishes, the run stops before any test module (and so any
application module) is imported.

What this covers: accidental invocations that skip the root conftest.py,
such as `--noconftest` or `--confcutdir` pointing below the repository root.

What it does not cover: deliberately disabling it (`-p no:tests.isolation_guard`,
overriding `addopts`, a different config file), running test code outside
pytest, or arbitrary code. Those are outside what a test harness can enforce.
"""

import os

import pytest

# Set when pytest imports this plugin, before any conftest or test module:
# the application's load_dotenv() calls must never load the development .env
# during a test run, even one that is about to be refused.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# Set on the pytest config by the repository-root conftest.py.
ISOLATION_ATTRIBUTE = "_medibridge_database_isolation"


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    if not getattr(config, ISOLATION_ATTRIBUTE, False):
        raise pytest.UsageError(
            "The repository-root conftest.py was not loaded, so the disposable test "
            "database was not set up (for example because of --noconftest or "
            "--confcutdir). Refusing to run tests. Run pytest from the repository root "
            "or from tests/ without those options."
        )
