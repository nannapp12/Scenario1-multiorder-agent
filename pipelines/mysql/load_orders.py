"""MySQL Orders refresh pipeline (runs as a scheduled Azure Container Apps Job).

1. Lists new CSVs under landing/mysql/ in ADLS (managed identity, HTTPS).
2. Upserts rows into orders_db.orders over TLS (INSERT ... ON DUPLICATE KEY UPDATE,
   only when the incoming updated_at is newer).
3. Moves processed files to processed/mysql/ so the next run skips them.
"""
import csv
import io
import logging
import os

import pymysql
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mysql-loader")

COLUMNS = ["order_id", "order_date", "customer_id", "customer_name", "region", "product",
           "category", "quantity", "unit_price", "total_amount", "status", "updated_at"]
UPSERT = (
    f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
    "ON DUPLICATE KEY UPDATE "
    + ", ".join(f"{c} = IF(VALUES(updated_at) >= updated_at, VALUES({c}), {c})"
                for c in COLUMNS if c not in ("order_id", "updated_at"))
    + ", updated_at = GREATEST(updated_at, VALUES(updated_at))"
)
BATCH = 1000


def connect():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],  # Key Vault reference
        database=os.environ.get("MYSQL_DATABASE", "orders_db"),
        ssl={"ca": os.environ.get("MYSQL_SSL_CA", "/etc/ssl/certs/ca-certificates.crt")},
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        autocommit=False,
    )


def main() -> None:
    account_url = f"https://{os.environ['STORAGE_ACCOUNT']}.blob.core.windows.net"
    container = BlobServiceClient(account_url, credential=DefaultAzureCredential()) \
        .get_container_client(os.environ.get("LANDING_CONTAINER", "landing"))

    blobs = [b.name for b in container.list_blobs(name_starts_with="mysql/") if b.name.endswith(".csv")]
    if not blobs:
        log.info("No new files.")
        return

    conn = connect()
    try:
        for name in sorted(blobs):
            text = container.download_blob(name).readall().decode("utf-8")
            rows = [[r[c] or None for c in COLUMNS] for r in csv.DictReader(io.StringIO(text))]
            with conn.cursor() as cur:
                for i in range(0, len(rows), BATCH):
                    cur.executemany(UPSERT, rows[i:i + BATCH])
            conn.commit()  # one transaction per file

            dest = container.get_blob_client(name.replace("mysql/", "processed/mysql/", 1))
            dest.upload_blob(text, overwrite=True)
            container.delete_blob(name)
            log.info("Loaded %d rows from %s", len(rows), name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
