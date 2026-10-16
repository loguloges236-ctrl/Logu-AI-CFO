"""Static drift checks for Phase 3 (no database, no SQLAlchemy needed).

- ORM column names must equal the columns created by the migration, table by table.
- Enum values in app/commerce/domain.py must appear in the migration's CHECK literals.
- The migration must be explicit: no create_all, drop_all, DROP TABLE or DROP SCHEMA.

Runtime proof of the DDL, triggers and constraints is in tests/sql (PostgreSQL) and the integration suite.
"""

import ast
import pathlib
import re
import unittest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "20261008_0003_commerce.py"
MODELS = BACKEND / "app" / "models" / "commerce.py"
DOMAIN = BACKEND / "app" / "commerce" / "domain.py"


def _migration_tables(text: str) -> dict[str, set[str]]:
    """Column names per CREATE TABLE. Only lines at the table's own indentation count; deeper lines are
    CHECK/UNIQUE continuations. `{_ts()}` expands to the two timestamp columns."""
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\s*\)", text, re.S):
        name, body = match.group(1), match.group(2)
        lines = [ln for ln in body.split("\n") if ln.strip()]
        base_indent = len(lines[0]) - len(lines[0].lstrip())
        columns: set[str] = set()
        for raw in lines:
            indent = len(raw) - len(raw.lstrip())
            if indent != base_indent:
                continue
            line = raw.strip().rstrip(",")
            if "{_ts()}" in line:
                columns |= {"created_at", "updated_at"}
                continue
            if line.startswith(("CONSTRAINT", "CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "--")):
                continue
            columns.add(line.split()[0])
        tables[name] = columns
    return tables


def _orm_tables() -> dict[str, set[str]]:
    """Columns per __tablename__, from annotated attributes (`x: Mapped[...] = ...`) and `declared_attr`
    methods, including attributes inherited from mixins defined in the same module."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def own_columns(node: ast.ClassDef) -> set[str]:
        cols: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and "Mapped" in ast.unparse(stmt.annotation):
                cols.add(stmt.target.id)
            if isinstance(stmt, ast.FunctionDef) and any("declared_attr" in ast.unparse(d) for d in stmt.decorator_list):
                cols.add(stmt.name)
        return cols

    def columns_with_bases(node: ast.ClassDef) -> set[str]:
        cols = own_columns(node)
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes:
                cols |= columns_with_bases(classes[base.id])
        return cols

    tables: dict[str, set[str]] = {}
    for node in classes.values():
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets):
                tables[stmt.value.value] = columns_with_bases(node)
    return tables


def _migration_constant(name: str) -> tuple[str, ...]:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return tuple(elt.value for elt in node.value.elts)
    raise AssertionError(f"{name} not defined in migration")


def _domain_literal(name: str) -> tuple[str, ...]:
    """Values of a module-level tuple, list, or frozenset({...}) literal in the domain module."""
    tree = ast.parse(DOMAIN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                value = value.args[0]
            return tuple(elt.value for elt in value.elts)
    raise AssertionError(f"{name} not defined in domain")


def _enum_members(class_name: str) -> set[str]:
    tree = ast.parse(DOMAIN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
            }
    raise AssertionError(f"{class_name} not found in domain")


class CommerceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = MIGRATION.read_text(encoding="utf-8")

    def test_revision_chain_follows_phase_two(self):
        self.assertIn('revision = "20261008_0003"', self.migration)
        self.assertIn('down_revision = "20261008_0002"', self.migration)

    def test_migration_is_explicit_and_non_destructive(self):
        code = re.sub(r'^\s*""".*?"""', "", self.migration, count=1, flags=re.S)  # the module docstring may name the rule
        for forbidden in ("create_all", "drop_all", "DROP TABLE", "DROP SCHEMA"):
            self.assertNotIn(forbidden, code, forbidden)
        # TRUNCATE may only appear as part of a trigger definition that blocks it.
        for match in re.finditer(r"TRUNCATE", code):
            self.assertRegex(code[max(0, match.start() - 12) : match.start()], r"(BEFORE|OR) $")

    def test_downgrade_is_refused(self):
        self.assertRegex(self.migration, r"def downgrade\(\) -> None:[\s\S]*raise RuntimeError")

    def test_every_orm_table_is_created_by_the_migration(self):
        migrated = _migration_tables(self.migration)
        orm = _orm_tables()
        self.assertTrue(orm, "no __tablename__ found in models")
        self.assertEqual(set(orm), set(migrated), "table set differs between ORM and migration")

    def test_orm_columns_match_migration_columns(self):
        migrated = _migration_tables(self.migration)
        orm = _orm_tables()
        for table in sorted(orm):
            self.assertEqual(orm[table], migrated[table], f"column drift on {table}")

    def test_movement_types_match_migration_constants(self):
        migration_values = set(_migration_constant("MOVEMENT_IN")) | set(_migration_constant("MOVEMENT_OUT"))
        self.assertEqual(_enum_members("MovementType"), migration_values)

    def test_units_and_party_kinds_match_migration_constants(self):
        self.assertEqual(set(_domain_literal("UNITS_OF_MEASURE")), set(_migration_constant("UOMS")))
        self.assertEqual(set(_domain_literal("PARTY_KINDS")), set(_migration_constant("PARTY_KINDS")))


if __name__ == "__main__":
    unittest.main()
