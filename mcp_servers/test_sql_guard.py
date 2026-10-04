import pytest

from sql_guard import UnsafeSQLError, ensure_read_only

ALLOWED = [
    ("SELECT region, SUM(total_amount) FROM orders GROUP BY region", "mysql"),
    ("WITH x AS (SELECT * FROM orders) SELECT COUNT(*) FROM x", "snowflake"),
    ("SELECT 1 UNION ALL SELECT 2", "databricks"),
]
REJECTED = [
    ("DELETE FROM orders", "mysql"),
    ("SELECT 1; DROP TABLE orders", "mysql"),
    ("UPDATE orders SET status = 'x'", "snowflake"),
    ("INSERT INTO orders SELECT * FROM orders", "databricks"),
    ("CREATE TABLE t AS SELECT * FROM orders", "snowflake"),
    ("GRANT SELECT ON orders TO bob", "mysql"),
    ("TRUNCATE TABLE orders", "databricks"),
]


@pytest.mark.parametrize("query,dialect", ALLOWED)
def test_allows_selects(query, dialect):
    assert ensure_read_only(query, dialect)


@pytest.mark.parametrize("query,dialect", REJECTED)
def test_rejects_writes(query, dialect):
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(query, dialect)
