"""Database backends. Each one opens TLS-encrypted, read-only connections.

Secrets come from environment variables that Azure Container Apps fills
from Azure Key Vault secret references (see infra/main.bicep).
"""
import os
from contextlib import contextmanager
from typing import Any


class Backend:
    name: str
    dialect: str
    table: str

    @contextmanager
    def cursor(self):
        raise NotImplementedError

    def query(self, sql: str, max_rows: int, timeout_s: int) -> dict[str, Any]:
        with self.cursor() as cur:
            self._set_timeout(cur, timeout_s)
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = [[_jsonable(v) for v in r] for r in rows[:max_rows]]
        return {"source": self.name, "columns": columns, "rows": rows,
                "row_count": len(rows), "truncated": truncated}

    def _set_timeout(self, cur, timeout_s: int) -> None:
        pass

    def schema(self) -> dict[str, Any]:
        raise NotImplementedError


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)  # Decimal, date, datetime -> string


class DatabricksBackend(Backend):
    name, dialect = "databricks", "databricks"

    def __init__(self):
        self.table = os.environ.get("DATABRICKS_TABLE", "orders_catalog.sales.orders")

    @contextmanager
    def cursor(self):
        from databricks import sql  # HTTPS (TLS 1.2+) to the SQL warehouse

        conn = sql.connect(
            server_hostname=os.environ["DATABRICKS_HOST"],
            http_path=os.environ["DATABRICKS_HTTP_PATH"],
            access_token=os.environ["DATABRICKS_TOKEN"],
        )
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    def _set_timeout(self, cur, timeout_s):
        cur.execute(f"SET STATEMENT_TIMEOUT = {int(timeout_s)}")

    def schema(self):
        with self.cursor() as cur:
            cur.execute(f"DESCRIBE TABLE {self.table}")
            cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall() if r[0] and not r[0].startswith("#")]
        return {"source": self.name, "dialect": "Databricks SQL", "table": self.table, "columns": cols}


class MySQLBackend(Backend):
    name, dialect = "mysql", "mysql"

    def __init__(self):
        self.table = os.environ.get("MYSQL_TABLE", "orders")

    @contextmanager
    def cursor(self):
        import pymysql

        conn = pymysql.connect(
            host=os.environ["MYSQL_HOST"],
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            # Azure MySQL Flexible Server enforces require_secure_transport; verify the server cert.
            ssl={"ca": os.environ.get("MYSQL_SSL_CA", "/etc/ssl/certs/ca-certificates.crt")},
            ssl_verify_cert=True,
            ssl_verify_identity=True,
            read_timeout=60,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                yield cur
        finally:
            conn.close()

    def _set_timeout(self, cur, timeout_s):
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s) * 1000}")

    def schema(self):
        with self.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position",
                (self.table,),
            )
            cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
        return {"source": self.name, "dialect": "MySQL 8", "table": self.table, "columns": cols}


class SnowflakeBackend(Backend):
    name, dialect = "snowflake", "snowflake"

    def __init__(self):
        self.table = os.environ.get("SNOWFLAKE_TABLE", "ORDERS_DB.SALES.ORDERS")

    @contextmanager
    def cursor(self):
        import snowflake.connector
        from cryptography.hazmat.primitives import serialization

        # Key-pair auth; the PEM private key lives in Key Vault.
        pem = os.environ["SNOWFLAKE_PRIVATE_KEY"].encode()
        passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        key = serialization.load_pem_private_key(pem, password=passphrase.encode() if passphrase else None)
        der = key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        conn = snowflake.connector.connect(  # always HTTPS/TLS
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            private_key=der,
            role=os.environ.get("SNOWFLAKE_ROLE", "ORDERS_READER"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "ORDERS_WH"),
        )
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    def _set_timeout(self, cur, timeout_s):
        cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(timeout_s)}")

    def schema(self):
        with self.cursor() as cur:
            cur.execute(f"DESCRIBE TABLE {self.table}")
            cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
        return {"source": self.name, "dialect": "Snowflake SQL", "table": self.table, "columns": cols}


BACKENDS = {"databricks": DatabricksBackend, "mysql": MySQLBackend, "snowflake": SnowflakeBackend}
