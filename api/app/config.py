"""Settings. In Azure, secret values are injected from Key Vault references;
locally they come from a .env file (never commit it)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Microsoft Foundry
    foundry_project_endpoint: str           # https://<resource>.services.ai.azure.com/api/projects/<project>
    foundry_model_deployment: str = "gpt-4.1"
    agent_name: str = "orders-analyst"

    # Remote MCP servers (HTTPS only)
    mcp_databricks_url: str
    mcp_mysql_url: str
    mcp_snowflake_url: str
    mcp_api_key: str                        # Key Vault secret: mcp-api-key

    # Azure AI Speech
    speech_region: str
    speech_key: str                         # Key Vault secret: speech-key

    allowed_origins: str = ""               # comma-separated; empty = same-origin only
    run_timeout_s: int = 120


settings = Settings()
