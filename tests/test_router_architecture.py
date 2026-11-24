"""
Architecture rules for the routers package:
- a router never imports main or another router, in any import form;
- every router module exposes one plain APIRouter (no prefix, tags or
  dependencies of its own; those stay on each route);
- the application registers each operation once.
"""

import ast
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute, iter_route_contexts

ROOT = Path(__file__).resolve().parents[1]
ROUTER_FILES = sorted(p for p in (ROOT / "routers").glob("*.py") if p.name != "__init__.py")
ROUTER_MODULES = [p.stem for p in ROUTER_FILES]


def _imported_modules(path: Path) -> list[str]:
    """Every module an import statement in the file refers to, relative ones resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import inside routers/: `from . import x`, `from .x import y`.
                base = "routers" if node.level == 1 else "<outside routers>"
                module = f"{base}.{node.module}" if node.module else base
                found += [module] + [f"{module}.{alias.name}" for alias in node.names]
            else:
                found += [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
    return found


def test_router_modules_exist():
    assert ROUTER_MODULES, "no router modules found"


@pytest.mark.parametrize("path", ROUTER_FILES, ids=lambda p: p.name)
def test_router_does_not_import_main_or_other_routers(path):
    for module in _imported_modules(path):
        top = module.split(".")[0]
        assert top != "main", f"{path.name} imports {module}"
        assert top != "routers", f"{path.name} imports {module}"
    # Router modules are only importable as routers.<name>, so a bare import such
    # as `from fhir import ...` refers to the top-level helper module, not a router.


def test_fhir_router_uses_the_top_level_fhir_helpers():
    import fhir
    import routers.fhir

    assert routers.fhir.create_operation_outcome is fhir.create_operation_outcome
    assert routers.fhir.patient_to_fhir is fhir.patient_to_fhir


@pytest.mark.parametrize("name", ROUTER_MODULES)
def test_router_is_a_plain_apirouter(name):
    module = __import__(f"routers.{name}", fromlist=["router"])
    router = module.router
    assert isinstance(router, APIRouter)
    assert router.prefix == ""
    assert router.tags == []
    assert router.dependencies == []


def _api_route_contexts():
    from main import app

    # FastAPI includes routers lazily; iter_route_contexts expands them.
    return [c for c in iter_route_contexts(app.routes) if isinstance(c.original_route, APIRoute)]


def test_every_router_module_is_registered_on_the_app():
    registered = {c.endpoint.__module__ for c in _api_route_contexts()}
    assert {f"routers.{name}" for name in ROUTER_MODULES} <= registered


def test_application_lifespan_does_no_startup_work_and_creates_no_engine(monkeypatch):
    # Including routers makes FastAPI wrap each router's (default) lifespan
    # around the app's; those must stay behaviourally empty. The app's own
    # lifespan (main.lifespan) does nothing at startup and, at shutdown, only
    # disposes the shared database engine if one already exists
    # (tests/test_app_lifespan.py). Here none exists, so the whole lifespan
    # must neither build an engine nor open a database connection.
    import asyncio

    import psycopg

    import db_engine
    from main import app

    def forbidden(*args, **kwargs):
        raise AssertionError("the application lifespan must not create an engine or connect")

    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_engine, "build_engine", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)

    messages = []

    async def run():
        async def receive():
            return {"type": "lifespan.startup" if not messages else "lifespan.shutdown"}

        async def send(message):
            messages.append(message["type"])

        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
        await app(scope, receive, send)
        return scope["state"]

    state = asyncio.run(run())
    assert messages == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert state == {}
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []
    assert app.user_middleware == []
    assert db_engine._engine is None


IMPORT_CHECK = """
import sys

import psycopg

def forbidden(*args, **kwargs):
    raise AssertionError("importing main must not open a database connection")

psycopg.connect = forbidden
import main  # noqa: E402
import db_engine  # noqa: E402

print(db_engine._engine is None)
print(sorted({"migrate", "seed_demo_data", "provision_user"} & set(sys.modules)))
"""


def test_importing_the_application_creates_no_engine_and_loads_no_setup_modules():
    # A fresh interpreter, because this test session has long since imported main. It
    # inherits the session's environment, so even a connection would only reach the
    # disposable database; connecting fails before that.
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [sys.executable, "-c", IMPORT_CHECK],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["True", "[]"]


def test_no_operation_is_registered_twice():
    operations = Counter(
        (method, context.path) for context in _api_route_contexts() for method in context.methods
    )
    assert [op for op, count in operations.items() if count > 1] == []
