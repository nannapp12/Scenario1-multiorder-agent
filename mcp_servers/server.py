"""Remote MCP server (streamable HTTP) for one Orders data source.

The same image runs three times; MCP_BACKEND picks databricks | mysql | snowflake.
Foundry Agent Service calls it over HTTPS (Container Apps ingress terminates TLS)
and authenticates with the `x-api-key` header, whose value is in Key Vault.
"""
import hmac
import json
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backends import BACKENDS
from sql_guard import UnsafeSQLError, ensure_read_only

BACKEND = BACKENDS[os.environ["MCP_BACKEND"]]()
MAX_ROWS = int(os.environ.get("MCP_MAX_ROWS", "500"))
TIMEOUT_S = int(os.environ.get("MCP_QUERY_TIMEOUT_S", "60"))
API_KEY = os.environ["MCP_API_KEY"]

mcp = FastMCP(f"orders-{BACKEND.name}", stateless_http=True)


@mcp.tool()
def get_schema() -> str:
    """Return the Orders table name, SQL dialect, and column list for this source."""
    return json.dumps(BACKEND.schema())


@mcp.tool()
def run_sql(query: str) -> str:
    """Run ONE read-only SELECT against this source's Orders table.

    Prefer aggregating (GROUP BY / SUM / COUNT) here rather than pulling raw rows.
    Returns JSON: {source, columns, rows, row_count, truncated}.
    """
    try:
        safe = ensure_read_only(query, BACKEND.dialect)
        return json.dumps(BACKEND.query(safe, MAX_ROWS, TIMEOUT_S))
    except UnsafeSQLError as e:
        return json.dumps({"source": BACKEND.name, "error": f"Rejected: {e}"})
    except Exception as e:  # surface DB errors so the agent can fix its SQL
        return json.dumps({"source": BACKEND.name, "error": f"{type(e).__name__}: {e}"})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"status": "ok", "backend": BACKEND.name})
        supplied = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied, API_KEY):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = mcp.streamable_http_app()  # serves MCP at /mcp
app.add_middleware(ApiKeyMiddleware)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), proxy_headers=True)
