"""
Guard for pytest runs that would bypass the repository-root conftest.py.

pytest only loads the root conftest.py (which creates the disposable test
database) when the run's root directory is the repository root. Started from
inside `tests/`, possibly with PYTHONPATH pointing at the repository, the
tests could otherwise import the application and reach whatever database the
development `.env` names. Import failures are not relied on for safety.

tests/isolation_guard.py (loaded through addopts) performs the same check
and also covers --noconftest, which disables this file.
"""

import os

import pytest

# Applied as soon as this file is imported, before any test module imports
# the application: the development `.env` must never be loaded by a test run.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

ISOLATION_ATTRIBUTE = "_medibridge_database_isolation"  # set by the root conftest.py


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    if not getattr(config, ISOLATION_ATTRIBUTE, False):
        raise pytest.UsageError(
            "The repository-root conftest.py was not loaded, so no disposable test "
            "database was set up. Run pytest from the repository root, for example: "
            "python -m pytest tests/test_main.py"
        )
