-- Azure Database for MySQL Flexible Server: data is encrypted at rest (AES-256,
-- service-managed or customer-managed key) and require_secure_transport=ON with
-- tls_version=TLSv1.2,TLSv1.3 (set in infra/main.bicep).
CREATE DATABASE IF NOT EXISTS orders_db;
USE orders_db;

CREATE TABLE IF NOT EXISTS orders (
  order_id      VARCHAR(32)    NOT NULL PRIMARY KEY,
  order_date    DATE           NOT NULL,
  customer_id   VARCHAR(32)    NOT NULL,
  customer_name VARCHAR(200),
  region        VARCHAR(32),
  product       VARCHAR(100),
  category      VARCHAR(50),
  quantity      INT,
  unit_price    DECIMAL(12,2),
  total_amount  DECIMAL(14,2),
  status        VARCHAR(20),
  updated_at    TIMESTAMP      NOT NULL,
  INDEX ix_orders_date (order_date),
  INDEX ix_orders_region (region)
);

-- Writer used by the load pipeline.
CREATE USER IF NOT EXISTS 'orders_loader'@'%' IDENTIFIED BY '<from Key Vault: mysql-loader-password>' REQUIRE SSL;
GRANT SELECT, INSERT, UPDATE ON orders_db.orders TO 'orders_loader'@'%';

-- Read-only user used by the MySQL MCP server.
CREATE USER IF NOT EXISTS 'mcp_reader'@'%' IDENTIFIED BY '<from Key Vault: mysql-reader-password>' REQUIRE SSL;
GRANT SELECT ON orders_db.orders TO 'mcp_reader'@'%';
