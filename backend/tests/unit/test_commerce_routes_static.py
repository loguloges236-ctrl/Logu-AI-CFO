"""Static guard on the Phase 3 API surface (no FastAPI import needed).

- Every route in app/api/routes/commerce.py requires an authenticated session (get_current_auth or require_csrf,
  which itself depends on get_current_auth).
- Every state-changing route (POST, PATCH, PUT, DELETE) requires the CSRF token.
- There are no unauthenticated business routes and no raw SQL execution.
"""

import ast
import pathlib
import unittest

ROUTES = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "routes" / "commerce.py"
MUTATING = {"post", "put", "patch", "delete"}


def _route_functions():
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in {"get", "post", "put", "patch", "delete"}:
                yield node, dec.func.attr, ast.unparse(dec.args[0]) if dec.args else ""


def _dependency_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for arg in node.args.args + node.args.kwonlyargs:
        if arg.annotation is not None:
            names.add(ast.unparse(arg.annotation))
    for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
        names.add(ast.unparse(default))
    return names


class CommerceRouteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = list(_route_functions())

    def test_routes_exist_and_are_under_the_commerce_router(self):
        self.assertGreater(len(self.routes), 60)
        self.assertIn('APIRouter(prefix="/commerce"', ROUTES.read_text(encoding="utf-8"))

    def test_every_route_requires_an_authenticated_session(self):
        for node, method, path in self.routes:
            deps = " ".join(_dependency_names(node))
            self.assertTrue(
                "get_current_auth" in deps or "require_csrf" in deps,
                f"{method.upper()} {path} ({node.name}) has no session dependency",
            )

    def test_every_state_changing_route_requires_csrf(self):
        for node, method, path in self.routes:
            if method in MUTATING:
                deps = " ".join(_dependency_names(node))
                self.assertIn("require_csrf", deps, f"{method.upper()} {path} ({node.name}) lacks CSRF")

    def test_no_raw_sql_execution_in_routes(self):
        code = ast.unparse(ast.parse(ROUTES.read_text(encoding="utf-8")))  # docstrings and comments excluded
        for pattern in (r"\btext\(", r"\.execute\(", r"create_all", r"drop_all"):
            self.assertNotRegex(code, pattern)

    def test_no_route_is_public(self):
        for node, method, path in self.routes:
            self.assertNotIn(node.name, {"health", "login", "public"}, path)


if __name__ == "__main__":
    unittest.main()
