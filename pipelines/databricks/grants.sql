-- Read-only principal used by the Databricks MCP server (service principal or token owner).
GRANT USE CATALOG ON CATALOG orders_catalog TO `mcp-orders-reader`;
GRANT USE SCHEMA ON SCHEMA orders_catalog.sales TO `mcp-orders-reader`;
GRANT SELECT ON TABLE orders_catalog.sales.orders TO `mcp-orders-reader`;
-- Plus: CAN USE on the SQL warehouse referenced by DATABRICKS_HTTP_PATH.
