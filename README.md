# Orders Voice Analyst

Ask questions about orders by voice. A Microsoft Foundry agent turns each question into SQL,
queries Azure Databricks, MySQL and Snowflake through MCP servers, combines the results,
and the web page shows the answer as a table and, where it makes sense, a bar chart.

```
 Browser (web/)                         Azure Container Apps (HTTPS only)
 ┌───────────────────┐  HTTPS  ┌──────────────────────┐  Entra ID  ┌──────────────────────────┐
 │ mic → Azure AI    │────────▶│ FastAPI  (api/)      │───────────▶│ Microsoft Foundry agent  │
 │ Speech STT (token)│◀────────│ /api/speech-token    │            │  "orders-analyst"        │
 │ table + Chart.js  │         │ /api/ask             │            │  text-to-SQL + merge     │
 └───────────────────┘         └──────────────────────┘            └────────────┬─────────────┘
                                                        MCP over HTTPS + x-api-key│
                     ┌──────────────────────┬──────────────────────┬────────────┘
                     ▼                      ▼                      ▼
              mcp-databricks          mcp-mysql              mcp-snowflake       (mcp_servers/)
                     │ TLS                  │ TLS (verified)       │ TLS
                     ▼                      ▼                      ▼
          Databricks SQL warehouse   Azure MySQL Flexible    Snowflake
          orders_catalog.sales.orders  orders_db.orders      ORDERS_DB.SALES.ORDERS
                     ▲                      ▲                      ▲
            Lakeflow Job (hourly)   Container Apps Job      Snowflake TASK (hourly)
                     └────────── ADLS Gen2 "landing" container ─────┘
                                 (pipelines/ + data/)
          All secrets: Azure Key Vault → Container Apps Key Vault references (managed identity)
```

## Repository layout

| Path | What it is |
|---|---|
| `api/` | FastAPI REST API: `POST /api/ask`, `GET /api/speech-token`, `GET /healthz`. Creates or updates the Foundry agent on startup. Also serves `web/`. |
| `api/app/prompts.py` | Agent instructions: data layout, text-to-SQL rules, merge rules, JSON output contract |
| `mcp_servers/` | One remote MCP server image (streamable HTTP, `/mcp`), run 3× with `MCP_BACKEND=databricks\|mysql\|snowflake`. Tools: `get_schema`, `run_sql` |
| `mcp_servers/sql_guard.py` | Rejects anything except a single `SELECT` (sqlglot), with tests |
| `web/` | Voice UI: Azure AI Speech JS SDK, result table, Chart.js bar chart |
| `pipelines/databricks/` | Databricks Asset Bundle → **Lakeflow Job** (Auto Loader + `MERGE`, then `OPTIMIZE`) |
| `pipelines/mysql/` | Schema, users, and loader (upsert) run hourly as a Container Apps **Job** |
| `pipelines/snowflake/setup.sql` | Storage integration, stage, `COPY` + `MERGE` procedure, hourly **TASK**, read-only role |
| `data/generate_orders.py` | Generates sample orders and splits each `order_id` into exactly one source |
| `infra/main.bicep` | Key Vault, managed identity, ACR, Container Apps (API, 3 MCP servers, loader job), MySQL, Speech, ADLS |

## How a question is answered

1. The browser gets a 10-minute Speech token from `/api/speech-token`; the Speech key never leaves the server. The browser recognizes the speech and gets the question as text.
2. `POST /api/ask` creates a Foundry thread and runs the agent with three MCP tools. Each MCP server gets its `x-api-key` header per run; the key is not stored on the agent.
3. The agent writes SQL in each source's dialect and pushes aggregation down to each source (`GROUP BY`, `SUM`, `COUNT`). It then merges the partial results: it sums the counts and totals, recomputes averages from those totals, and re-ranks top-N lists across sources.
4. The agent returns JSON (`answer`, `table`, `chart`, `queries`). The API validates it and drops a chart that refers to columns the table doesn't have. The page renders the answer, the table, the bar chart and the SQL it ran.

Example questions: *"Total revenue by region"*, *"How many orders were cancelled last month?"*,
*"Top 5 products by quantity"*, *"Order count per source"*.

## Security

| Requirement | How it's met |
|---|---|
| HTTPS/TLS everywhere | Container Apps ingress `allowInsecure: false` (TLS 1.2+); the API sends HSTS. The API refuses MCP URLs that aren't `https://`. MySQL has `require_secure_transport=ON` and `tls_version=TLSv1.2,TLSv1.3`, and the client verifies the certificate and hostname. The Databricks and Snowflake connectors are TLS-only. Storage has `supportsHttpsTrafficOnly` and minimum TLS 1.2. |
| Encryption at rest | **Databricks**: Delta files in ADLS are AES-256 encrypted. Enable a customer-managed key (CMK) for managed services and disks in the workspace's encryption settings if needed. **MySQL Flexible Server**: AES-256 for data, backups and logs, with an optional CMK through `dataEncryption`. **Snowflake**: always AES-256, with Tri-Secret Secure for CMK. **Landing storage**: infrastructure (double) encryption. |
| Secrets in Key Vault | `speech-key`, `mcp-api-key`, `databricks-token`, `snowflake-private-key`, `mysql-*-password`. Apps read them through Key Vault references using a user-assigned identity (role: *Key Vault Secrets User*). Foundry and Storage use Entra ID, so they need no secrets. |
| Least privilege | MCP servers use read-only database principals (`mcp_reader`, `ORDERS_READER`, and `SELECT` grants in Unity Catalog). On top of that, the sqlglot guard allows only `SELECT`, MySQL sessions are `READ ONLY`, and queries have statement timeouts and a row cap. |

## Deploy

Prerequisites: Azure CLI, Docker (or `az acr build`), Databricks CLI, a Foundry project with a
model deployment (for example `gpt-4.1`), a Databricks workspace with a SQL warehouse and Unity Catalog, and a Snowflake account.

**1. Create the infrastructure without the apps** (the images don't exist yet):

```bash
az group create -n rg-orders-va -l eastus
```
```bash
az deployment group create -g rg-orders-va -f infra/main.bicep -p deployApps=false foundryProjectEndpoint=<endpoint> databricksHost=<host> databricksHttpPath=<path> databricksToken=<pat> snowflakeAccount=<org-acct> snowflakePrivateKey=@rsa_key.p8 mysqlAdminPassword=<pw> mysqlReaderPassword=<pw> mysqlLoaderPassword=<pw>
```

**2. Build the images into the new ACR** (use the `acrLoginServer` output):

```bash
az acr build -r <acr> -t orders-mcp:latest mcp_servers
```
```bash
az acr build -r <acr> -t orders-api:latest -f api/Dockerfile .
```
```bash
az acr build -r <acr> -t orders-mysql-loader:latest pipelines/mysql
```

**3. Deploy the apps**: rerun step 1's command with `deployApps=true`.

**4. Let the API's identity use Foundry**: give the `identityPrincipalId` output the **Azure AI User** role on the Foundry resource:

```bash
az role assignment create --assignee-object-id <identityPrincipalId> --assignee-principal-type ServicePrincipal --role "Azure AI User" --scope <foundry-resource-id>
```

**5. Set up the data sources and pipelines:**
- MySQL: run `pipelines/mysql/schema.sql` as the admin user, with the passwords from Key Vault.
- Snowflake: run `pipelines/snowflake/setup.sql`, then complete the Azure consent step it describes.
- Databricks: run `pipelines/databricks/grants.sql`, then `cd pipelines/databricks && databricks bundle deploy -t prod`.

**6. Load sample data:**
1. Run `python data/generate_orders.py`.
2. Upload each `data/out/<source>/` folder to `landing/<source>/` in the storage account.
3. Trigger the pipelines, or wait for their hourly schedules:
   - `databricks bundle run orders_refresh -t prod`
   - `az containerapp job start -n ordersva-mysql-loader -g rg-orders-va`
   - In Snowflake: `EXECUTE TASK REFRESH_ORDERS_HOURLY`

Open the `apiUrl` output and click the mic.

## Run locally

```bash
cp api/.env.example api/.env
```
```bash
cd api && pip install -r requirements.txt && az login && uvicorn app.main:app --reload
```

The MCP servers must be reachable from Foundry over public HTTPS. You can use the deployed ones, or a dev tunnel to a local `python mcp_servers/server.py`. Microphone access needs `localhost` or HTTPS.

## Tests

```bash
cd mcp_servers && pytest -q
```

## Production hardening (next steps)

- Protect the API with Entra ID (Container Apps built-in auth) and add rate limiting.
- Use private networking: Foundry network-secured agent setup with VNet injection, Container Apps internal ingress for the MCP servers, and private endpoints for MySQL, Key Vault and Storage, plus Databricks and Snowflake Private Link.
- Switch Databricks from a PAT to OAuth M2M with a service principal. You can also replace the custom Databricks or Snowflake MCP servers with the vendor-managed MCP servers (Databricks managed MCP for DBSQL, Snowflake-managed MCP server).
- Add a semantic layer, such as Unity Catalog metric views or Snowflake semantic views, and evaluation sets in Foundry to measure text-to-SQL accuracy.
- For very large merges, move the cross-source combine step into the API (for example DuckDB over the MCP results) instead of having the LLM do the arithmetic.
