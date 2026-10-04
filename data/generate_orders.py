"""Generate sample orders and split them across the three sources.

Writes data/out/{databricks,mysql,snowflake}/orders_<date>.csv. Upload each folder
to the matching landing container in ADLS Gen2 (see README), where the pipelines
pick them up. Each order_id goes to exactly one source.
"""
import csv
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PRODUCTS = {
    "Electronics": [("Laptop", 1200), ("Headphones", 150), ("Monitor", 300), ("Phone", 800)],
    "Furniture": [("Desk", 450), ("Chair", 220), ("Bookshelf", 180)],
    "Office": [("Paper Pack", 25), ("Printer Ink", 60), ("Stapler", 15)],
}
REGIONS = ["North", "South", "East", "West"]
STATUSES = ["Pending", "Shipped", "Delivered", "Delivered", "Delivered", "Cancelled"]
SOURCES = ["databricks", "mysql", "snowflake"]
COLUMNS = ["order_id", "order_date", "customer_id", "customer_name", "region", "product",
           "category", "quantity", "unit_price", "total_amount", "status", "updated_at"]


def main(n: int = 3000, seed: int = 7) -> None:
    rng = random.Random(seed)
    out = Path(__file__).parent / "out"
    rows = {s: [] for s in SOURCES}
    start = date.today() - timedelta(days=540)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for i in range(1, n + 1):
        category = rng.choice(list(PRODUCTS))
        product, price = rng.choice(PRODUCTS[category])
        qty = rng.randint(1, 10)
        cust = rng.randint(1, 400)
        rows[rng.choice(SOURCES)].append([
            f"ORD-{i:07d}", (start + timedelta(days=rng.randint(0, 540))).isoformat(),
            f"C{cust:05d}", f"Customer {cust}", rng.choice(REGIONS), product, category,
            qty, f"{price:.2f}", f"{price * qty:.2f}", rng.choice(STATUSES), now,
        ])

    for source, data in rows.items():
        folder = out / source
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"orders_{date.today():%Y%m%d}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            w.writerows(data)
        print(f"{source:10s} {len(data):5d} orders -> {path}")


if __name__ == "__main__":
    main()
