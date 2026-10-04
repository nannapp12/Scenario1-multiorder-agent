"""Microsoft Foundry agent with three remote MCP tools (Databricks, MySQL, Snowflake).

Uses the Foundry Agent Service SDK (azure-ai-projects / azure-ai-agents).
Auth to Foundry is Entra ID (managed identity in Azure, `az login` locally), so
no Foundry key exists. The MCP API key is passed per run as a header and is never
stored on the agent definition.
"""
import json
import logging
import re
import time

from azure.ai.agents.models import ListSortOrder, McpTool, MessageRole, RunStatus
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from pydantic import ValidationError

from .config import settings
from .prompts import AGENT_INSTRUCTIONS
from .schemas import AskResponse

log = logging.getLogger(__name__)


def _mcp_tools() -> list[McpTool]:
    tools = []
    for label, url in (
        ("databricks", settings.mcp_databricks_url),
        ("mysql", settings.mcp_mysql_url),
        ("snowflake", settings.mcp_snowflake_url),
    ):
        if not url.startswith("https://"):
            raise ValueError(f"MCP server '{label}' must use HTTPS: {url}")
        tool = McpTool(server_label=label, server_url=url, allowed_tools=["get_schema", "run_sql"])
        tool.update_headers("x-api-key", settings.mcp_api_key)
        tool.set_approval_mode("never")  # read-only tools; enforced server-side
        tools.append(tool)
    return tools


class OrdersAgent:
    def __init__(self):
        self.client = AIProjectClient(
            endpoint=settings.foundry_project_endpoint,
            credential=DefaultAzureCredential(),
        )
        self.tools = _mcp_tools()
        self.agent_id = self._ensure_agent()

    def _ensure_agent(self) -> str:
        definitions = [d for t in self.tools for d in t.definitions]
        agents = self.client.agents
        for existing in agents.list_agents():
            if existing.name == settings.agent_name:
                agents.update_agent(
                    existing.id,
                    model=settings.foundry_model_deployment,
                    instructions=AGENT_INSTRUCTIONS,
                    tools=definitions,
                    temperature=0,
                )
                return existing.id
        created = agents.create_agent(
            model=settings.foundry_model_deployment,
            name=settings.agent_name,
            instructions=AGENT_INSTRUCTIONS,
            tools=definitions,
            temperature=0,
        )
        log.info("Created Foundry agent %s", created.id)
        return created.id

    def ask(self, question: str) -> AskResponse:
        agents = self.client.agents
        thread = agents.threads.create()
        try:
            agents.messages.create(thread_id=thread.id, role=MessageRole.USER, content=question)

            # Merge per-server headers into one tool_resources payload.
            resources = self.tools[0].resources
            for t in self.tools[1:]:
                resources.mcp.extend(t.resources.mcp)

            run = agents.runs.create(thread_id=thread.id, agent_id=self.agent_id, tool_resources=resources)
            deadline = time.monotonic() + settings.run_timeout_s
            while run.status in (RunStatus.QUEUED, RunStatus.IN_PROGRESS, RunStatus.REQUIRES_ACTION):
                if time.monotonic() > deadline:
                    agents.runs.cancel(thread_id=thread.id, run_id=run.id)
                    raise TimeoutError("The agent took too long to answer.")
                time.sleep(1)
                run = agents.runs.get(thread_id=thread.id, run_id=run.id)

            if run.status != RunStatus.COMPLETED:
                raise RuntimeError(f"Agent run {run.status}: {run.last_error}")

            text = ""
            for msg in agents.messages.list(thread_id=thread.id, order=ListSortOrder.DESCENDING):
                if msg.role == MessageRole.AGENT and msg.text_messages:
                    text = msg.text_messages[-1].text.value
                    break
            return _parse(text)
        finally:
            agents.threads.delete(thread.id)


def _parse(text: str) -> AskResponse:
    match = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate stray fences/prose
    if match:
        try:
            resp = AskResponse.model_validate(json.loads(match.group(0)))
            if resp.chart and resp.table:
                cols = set(resp.table.columns)
                if resp.chart.x not in cols or not set(resp.chart.y) <= cols:
                    resp.chart = None  # don't render a chart that references missing columns
            return resp
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning("Agent returned malformed JSON: %s", e)
    return AskResponse(answer=text or "Sorry, I couldn't produce an answer.")
