import argparse

from pyspark.sql import SparkSession

p = argparse.ArgumentParser()
p.add_argument("--table", required=True)
args = p.parse_args()

spark = SparkSession.builder.getOrCreate()
spark.sql(f"OPTIMIZE {args.table}")
spark.sql(f"ANALYZE TABLE {args.table} COMPUTE STATISTICS FOR ALL COLUMNS")
