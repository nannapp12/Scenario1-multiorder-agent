"""Read-only SQL guard shared by all MCP servers.

Defense in depth: the database users are also SELECT-only, but we refuse
anything that is not a single SELECT before it ever reaches the engine.
"""
import sqlglot
from sqlglot import exp

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.Command, exp.Grant, exp.TruncateTable, exp.Use, exp.Set,
)


class UnsafeSQLError(ValueError):
    pass


def ensure_read_only(query: str, dialect: str) -> str:
    try:
        statements = [s for s in sqlglot.parse(query, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"Could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With)):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    if any(isinstance(node, _FORBIDDEN) for node in stmt.walk()):
        raise UnsafeSQLError("Query contains a forbidden operation.")

    return stmt.sql(dialect=dialect)
