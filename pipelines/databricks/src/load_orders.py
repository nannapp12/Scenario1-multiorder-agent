"""Incrementally load landed order CSVs into a Unity Catalog Delta table.

Auto Loader tracks which files are already processed (checkpoint), and MERGE
upserts by order_id, so re-runs are idempotent. Delta data in ADLS is encrypted
at rest (Microsoft-managed keys, or your CMK via workspace encryption settings).
"""
import argparse

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.window import Window

p = argparse.ArgumentParser()
p.add_argument("--catalog", required=True)
p.add_argument("--schema", required=True)
p.add_argument("--landing-path", required=True)
args = p.parse_args()

spark = SparkSession.builder.getOrCreate()
table = f"{args.catalog}.{args.schema}.orders"
checkpoint = f"/Volumes/{args.catalog}/{args.schema}/checkpoints/orders"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{args.schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {args.catalog}.{args.schema}.checkpoints")
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {table} (
  order_id STRING NOT NULL, order_date DATE, customer_id STRING, customer_name STRING,
  region STRING, product STRING, category STRING, quantity INT,
  unit_price DECIMAL(12,2), total_amount DECIMAL(14,2), status STRING, updated_at TIMESTAMP,
  CONSTRAINT orders_pk PRIMARY KEY (order_id)
) CLUSTER BY (order_date, region)
""")

SCHEMA = ("order_id STRING, order_date DATE, customer_id STRING, customer_name STRING, region STRING, "
          "product STRING, category STRING, quantity INT, unit_price DECIMAL(12,2), "
          "total_amount DECIMAL(14,2), status STRING, updated_at TIMESTAMP")


def upsert(batch_df, _batch_id):
    latest = (batch_df.filter(F.col("order_id").isNotNull())
              .withColumn("_rn", F.row_number().over(
                  Window.partitionBy("order_id").orderBy(F.col("updated_at").desc())))
              .filter("_rn = 1").drop("_rn", "_rescued_data"))
    latest.createOrReplaceTempView("orders_updates")
    latest.sparkSession.sql(f"""
        MERGE INTO {table} t USING orders_updates s ON t.order_id = s.order_id
        WHEN MATCHED AND s.updated_at >= t.updated_at THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)


(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "csv")
    .option("header", "true")
    .option("cloudFiles.schemaLocation", f"{checkpoint}/schema")
    .schema(SCHEMA)
    .load(args.landing_path)
    .writeStream
    .foreachBatch(upsert)
    .option("checkpointLocation", checkpoint)
    .trigger(availableNow=True)
    .start()
    .awaitTermination())

print(f"{table}: {spark.table(table).count()} rows")
