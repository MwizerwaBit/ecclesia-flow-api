"""Layer boundaries (ADR-0009), checked statically on every test run.

The rules, per module in app/modules/<name>/:

  router.py      controller — HTTP in/out, input schemas, permission and scope
                 checks. Calls its own module's service only. Never touches the
                 database or a repository.
  service.py     application/domain logic. Uses its own repository, other
                 modules' *services* and app.core. No FastAPI imports.
  repository.py  the only place a module queries the database.
  schemas.py     request/response contracts (DTOs).

Plus, everywhere in app/modules: never import another module's repository or
router. app/core is infrastructure and policy (sessions, RLS context, authz
attribute lookups) and is allowed raw SQL — documented in ADR-0009.

Violations that existed when these rules were introduced are listed in
KNOWN_* below. The tests fail if a NEW violation appears, and also if a known
one has been fixed but is still listed — so the lists only ever shrink.
"""

import ast
from pathlib import Path

MODULES = Path(__file__).resolve().parent.parent / "app" / "modules"

_PRISMA_METHODS = {
    "find_unique",
    "find_unique_or_raise",
    "find_first",
    "find_first_or_raise",
    "find_many",
    "create",
    "create_many",
    "update",
    "update_many",
    "upsert",
    "delete",
    "delete_many",
    "count",
    "group_by",
}
_RAW = {"query_raw", "execute_raw", "query_first"}


def _module_files():
    for path in sorted(MODULES.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path: Path) -> str:
    return path.relative_to(MODULES).as_posix()


def _module_of(path: Path) -> str:
    return path.relative_to(MODULES).parts[0]


def _db_calls(tree: ast.AST, *, is_router: bool) -> int:
    """Calls like db.member.find_many(...) or tx.query_raw(...). In routers,
    also setting the RLS session context: that is a service's job (a
    pre-tenant workflow such as registration may do it through app.core)."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _RAW:
                count += 1
            elif func.attr in _PRISMA_METHODS and isinstance(func.value, ast.Attribute):
                # <client>.<model>.<method>(...) — client is a Name like db/tx
                if isinstance(func.value.value, ast.Name):
                    count += 1
            elif func.attr == "tx" and isinstance(func.value, ast.Name):
                count += 1
        elif is_router and isinstance(func, ast.Name) and func.id in {"set_tenant_context", "clear_tenant_context"}:
            count += 1
    return count


def _imports(tree: ast.AST) -> list[str]:
    """Every imported dotted name, including `from x import y` as x.y."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


# ── Ratchets: existing violations, to be removed as modules are refactored ──

#: Files outside repository.py that still query the database directly.
KNOWN_DB_ACCESS_OUTSIDE_REPOSITORIES: set[str] = set()

#: Routers that still import a repository (their own or another module's).
KNOWN_ROUTER_REPOSITORY_IMPORTS: set[str] = set()

#: (importer, imported) cross-module imports of a repository or router.
KNOWN_CROSS_MODULE_INTERNALS: set[tuple[str, str]] = {
    ("identity/service.py", "people.repository"),
}

#: Routers calling another module's service (controllers use their own service).
KNOWN_ROUTER_FOREIGN_SERVICES: set[tuple[str, str]] = set()

#: Services (and other non-router module files) importing FastAPI.
KNOWN_FASTAPI_IN_SERVICES: set[str] = {"media/service.py"}


def _collect():
    db_access, router_repo, cross, fastapi_in_service, router_foreign = set(), set(), set(), set(), set()
    for path, tree in _module_files():
        rel, mod = _rel(path), _module_of(path)
        if path.name != "repository.py" and _db_calls(tree, is_router=path.name == "router.py"):
            db_access.add(rel)
        for name in _imports(tree):
            parts = name.split(".")
            if name.startswith("app.modules.") and len(parts) >= 4:
                other, sub = parts[2], parts[3]
                if path.name == "router.py" and sub == "repository":
                    router_repo.add(rel)
                if other != mod and sub in {"repository", "router"}:
                    cross.add((rel, f"{other}.{sub}"))
                if path.name == "router.py" and other != mod and sub == "service":
                    router_foreign.add((rel, f"{other}.service"))
            if path.name != "router.py" and parts[0] == "fastapi":
                fastapi_in_service.add(rel)
    return db_access, router_repo, cross, fastapi_in_service, router_foreign


def _check(found: set, known: set, what: str) -> None:
    new = sorted(found - known)
    fixed = sorted(known - found)
    assert not new, f"New {what} (see ADR-0009): {new}"
    assert not fixed, f"Fixed {what} — remove them from the KNOWN_ list in tests/test_architecture.py: {fixed}"


def test_only_repositories_query_the_database():
    _check(_collect()[0], KNOWN_DB_ACCESS_OUTSIDE_REPOSITORIES, "database access outside repository.py")


def test_routers_never_import_repositories():
    _check(_collect()[1], KNOWN_ROUTER_REPOSITORY_IMPORTS, "router → repository imports")


def test_modules_never_import_each_others_internals():
    _check(_collect()[2], KNOWN_CROSS_MODULE_INTERNALS, "cross-module repository/router imports")


def test_routers_only_call_their_own_service():
    _check(_collect()[4], KNOWN_ROUTER_FOREIGN_SERVICES, "router → other module's service imports")


def test_services_are_framework_free():
    _check(_collect()[3], KNOWN_FASTAPI_IN_SERVICES, "FastAPI imports outside routers")


def test_core_never_imports_modules():
    """app/core is the bottom layer: nothing in it may depend on a module."""
    core = MODULES.parent / "core"
    offenders = []
    for path in core.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(n.startswith("app.modules") for n in _imports(tree)):
            offenders.append(path.name)
    assert not offenders, f"app/core must not import app.modules: {offenders}"
