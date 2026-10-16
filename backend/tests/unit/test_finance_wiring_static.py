"""Static wiring checks for the finance services, routes and bank layer. No SQLAlchemy or FastAPI needed.

These prove structure, not behaviour: imported names exist, every route has the right auth dependency, the bank code
calls only read methods, and the AI reader implements every allow-listed executor method. Runtime behaviour is covered
by tests/sql (PostgreSQL) and by the integration tests that need the Python packages (UNVERIFIED here).
"""

import ast
import pathlib
import unittest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
NEW_MODULES = [
    "app/services/finance.py",
    "app/services/settlements.py",
    "app/services/bank.py",
    "app/services/notifications.py",
    "app/services/finance_ai.py",
    "app/services/commerce_documents.py",
    "app/api/routes/finance.py",
    "app/finance/approval.py",
    "app/finance/dashboard.py",
    "app/settlements/posting.py",
    "app/notifications/scheduler.py",
    "app/schemas/finance.py",
    "app/ai/executor.py",
]


def _tree(relpath: str) -> ast.Module:
    return ast.parse((BACKEND / relpath).read_text(encoding="utf-8"), filename=relpath)


def _defined_names(relpath: str) -> set[str]:
    names: set[str] = set()
    for node in _tree(relpath).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _module_path(dotted: str) -> str | None:
    candidate = BACKEND / (dotted.replace(".", "/") + ".py")
    return str(candidate.relative_to(BACKEND)) if candidate.exists() else None


class ImportWiringTests(unittest.TestCase):
    def test_every_local_from_import_names_an_existing_symbol(self):
        problems = []
        for module in NEW_MODULES:
            for node in ast.walk(_tree(module)):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                    path = _module_path(node.module)
                    package = BACKEND / node.module.replace(".", "/") / "__init__.py"
                    if path is None and not package.exists():
                        problems.append(f"{module}: missing module {node.module}")
                        continue
                    available = _defined_names(path) if path else _defined_names(str(package.relative_to(BACKEND)))
                    for alias in node.names:
                        if alias.name in available or _module_path(f"{node.module}.{alias.name}") is not None:
                            continue
                        problems.append(f"{module}: {node.module} has no {alias.name}")
        self.assertEqual(problems, [])

    def test_service_attribute_calls_exist(self):
        """fin._open_documents and similar module-level attribute uses must exist in the service module."""
        checks = {
            "app/services/notifications.py": ("fin", "app/services/finance.py"),
            "app/services/finance_ai.py": ("fin", "app/services/finance.py"),
            "app/api/routes/finance.py": ("fin", "app/services/finance.py"),
        }
        for module, (alias, target) in checks.items():
            available = _defined_names(target)
            used = {n.attr for n in ast.walk(_tree(module)) if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == alias}
            self.assertTrue(used, module)
            self.assertFalse(used - available, f"{module} uses missing {used - available}")


class RouteSecurityTests(unittest.TestCase):
    def _routes(self):
        out = []
        for node in ast.walk(_tree("app/api/routes/finance.py")):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in (
                        "get", "post", "put", "patch", "delete"
                    ):
                        path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else ""
                        out.append((dec.func.attr, path, node))
        return out

    def test_routes_are_registered_once_per_method_and_path(self):
        seen = set()
        for method, path, _ in self._routes():
            self.assertNotIn((method, path), seen, f"duplicate route {method} {path}")
            seen.add((method, path))
        self.assertGreaterEqual(len(seen), 30)

    def test_every_get_requires_the_owner_session_and_every_mutation_requires_csrf(self):
        for method, path, node in self._routes():
            deps = ast.unparse(node.args)
            if method == "get":
                self.assertIn("get_current_auth", deps, f"GET {path} lacks owner session")
            else:
                self.assertIn("require_csrf", deps, f"{method.upper()} {path} lacks CSRF check")

    def test_no_route_returns_secrets_or_takes_raw_sql(self):
        tree = _tree("app/api/routes/finance.py")
        words = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for banned in ("text", "execute", "password", "token", "secret", "api_key", "cipher"):
            self.assertNotIn(banned, words, banned)

    def test_bank_routes_depend_on_the_read_only_provider_only(self):
        source = (BACKEND / "app/api/routes/finance.py").read_text(encoding="utf-8")
        self.assertIn("def get_bank_provider", source)
        self.assertIn("UnavailableBankProvider()", source)


class BankReadOnlyTests(unittest.TestCase):
    ALLOWED = {"list_accounts", "list_transactions"}

    def test_bank_service_calls_only_read_methods_on_the_provider(self):
        calls = set()
        for node in ast.walk(_tree("app/services/bank.py")):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "provider":
                calls.add(node.func.attr)
        self.assertTrue(calls)
        self.assertLessEqual(calls, self.ALLOWED)

    def test_bank_module_has_no_write_vocabulary_in_identifiers(self):
        names = {n.id for n in ast.walk(_tree("app/services/bank.py")) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(_tree("app/services/bank.py")) if isinstance(n, ast.Attribute)}
        for word in ("transfer", "beneficiar", "initiate", "send_money"):
            self.assertFalse(any(word in name.lower() for name in names), word)


class AIReaderTests(unittest.TestCase):
    def test_db_reader_implements_every_executor_method_and_no_write_methods(self):
        from app.ai.executor import READER_METHOD

        defined = {n.name for n in ast.walk(_tree("app/services/finance_ai.py"))
                   if isinstance(n, ast.FunctionDef) and n.name != "__init__"}
        cls = next(n for n in _tree("app/services/finance_ai.py").body if isinstance(n, ast.ClassDef) and n.name == "DBCFOReader")
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)} - {"__init__"}
        self.assertEqual(methods, set(READER_METHOD.values()))
        for name in methods:
            self.assertFalse(set(name.split("_")) & {"post", "pay", "transfer", "create", "update", "delete", "insert", "reverse"}, name)
        self.assertTrue(defined)


class MigrationSyncTests(unittest.TestCase):
    def test_settlement_and_finance_migrations_are_chained_in_order(self):
        import re

        chain = {}
        for path in sorted((BACKEND / "alembic" / "versions").glob("20261008_*.py")):
            text = path.read_text(encoding="utf-8")
            rev = re.search(r'^revision = "(\w+)"', text, re.M).group(1)
            down = re.search(r"^down_revision = (\S+)", text, re.M).group(1).strip('"')
            chain[rev] = down
        self.assertEqual(chain["20261008_0004"], "20261008_0003")
        self.assertEqual(chain["20261008_0005"], "20261008_0004")
        self.assertEqual(chain["20261008_0006"], "20261008_0005")
        self.assertEqual(chain["20261008_0007"], "20261008_0006")
        self.assertEqual(chain["20261008_0008"], "20261008_0007")
        self.assertEqual(len(set(chain.values()) & set(chain)), len(chain) - 1, "exactly one head expected")


if __name__ == "__main__":
    unittest.main()
