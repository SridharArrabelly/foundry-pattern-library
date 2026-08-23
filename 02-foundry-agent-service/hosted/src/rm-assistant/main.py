"""
Pattern 2 — Foundry Agent Service (prompt and hosted agents), model B:
a real Foundry *hosted agent*.

This is YOUR code (Microsoft Agent Framework) packaged in a container and run by
Foundry Agent Service. The platform pulls the image, provisions compute, assigns the
agent its own **Entra Agent ID**, and exposes a dedicated endpoint. Compare with the
prompt-based agent in ../../create_prompt_agent.py — same business scenario, two hosting models.

Serves the **Responses** protocol on port 8088 via ResponsesHostServer.

Local run:   azd ai agent run           (or: python main.py, with .env set)
Deploy:      azd deploy
Invoke:      azd ai agent invoke "Is client C-1290 compliant?"
"""
import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

load_dotenv()

# --- grounding: load the suitability policy shipped in the container image ---
_POLICY_PATH = os.path.join(os.path.dirname(__file__), "policy.md")
with open(_POLICY_PATH, encoding="utf-8") as f:
    _POLICY = f.read()

# --- demo "core-banking" data (stands in for a downstream system of record) ---
_HOLDINGS = {
    "C-8842": "Mrs. Chen — 62% equities, 30% bonds, 8% cash; risk profile: Balanced; suitability: OK.",
    "C-1290": "Mr. Okafor — 90% equities; risk profile: Conservative; suitability: FLAGGED (over-weight equities).",
}


@tool(approval_mode="never_require")
def get_suitability_policy() -> str:
    """Return the wealth-management suitability & advice policy text."""
    return _POLICY


@tool(approval_mode="never_require")
def get_client_holdings(
    client_id: Annotated[str, Field(description="Private-banking client id, e.g. C-1290.")],
) -> str:
    """Return a short holdings + suitability summary for a private-banking client id."""
    return _HOLDINGS.get(client_id, "unknown client id")


def main():
    # FOUNDRY_PROJECT_ENDPOINT is auto-injected by the hosting platform; locally it
    # comes from .env. The model deployment name may arrive under either spelling
    # (or be baked into azure.yaml env) — fall back so the container never boots blind.
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = (
        os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.environ.get("MODEL_DEPLOYMENT_NAME")
        or "gpt-5.4-mini"
    )

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=(
            "You are a private-banking Relationship Manager assistant. Use "
            "get_suitability_policy for policy/suitability questions and "
            "get_client_holdings for client portfolio questions. Always cite the "
            "relevant policy rule when you assess suitability. Keep answers brief."
        ),
        tools=[get_suitability_policy, get_client_holdings],
        # The hosting platform manages conversation history; don't double-store it.
        default_options={"store": False},
    )

    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
