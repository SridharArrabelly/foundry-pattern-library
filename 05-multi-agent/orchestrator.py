"""
Pattern 5 — Multi-agent orchestration with Microsoft Agent Framework.

A deliberately SIMPLE orchestration: a client request fans out concurrently to two
specialist agents — a Portfolio Analyst and a Compliance Officer — and their views are
aggregated. This is the pattern to reach for when you genuinely need parallel
specialists or trust boundaries (contrast Pattern 4: default to one loop + N skills).

Runs on the customer's Azure AI Gateway (OpenAI-compatible), so the whole orchestration
rides the same gateway you already trust. Verified against agent-framework 1.9.0.

Run:  uv run python 05-multi-agent/orchestrator.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import (
    GATEWAY_ENDPOINT,
    GATEWAY_KEY,
    GATEWAY_MODEL,
    GATEWAY_V1_API_VERSION,
)


TASK = (
    "Client C-1290 (Mr. Okafor, risk profile: Conservative) holds 90% equities. "
    "He wants to add a leveraged tech structured product. Advise."
)


def build_client():
    """Agent Framework chat client on the APIM gateway (keyless Entra by default)."""
    from agent_framework.openai import OpenAIChatClient

    endpoint = GATEWAY_ENDPOINT.rstrip("/")
    if GATEWAY_KEY:  # APIM subscription key -> sent as the `api-key` header
        return OpenAIChatClient(
            model=GATEWAY_MODEL,
            azure_endpoint=endpoint,
            api_key=GATEWAY_KEY,
            api_version=GATEWAY_V1_API_VERSION,
        )
    from azure.identity import DefaultAzureCredential

    return OpenAIChatClient(
        model=GATEWAY_MODEL,
        azure_endpoint=endpoint,
        credential=DefaultAzureCredential(),
        api_version=GATEWAY_V1_API_VERSION,
    )


async def main():
    from agent_framework import Agent
    from agent_framework.orchestrations import ConcurrentBuilder

    client = build_client()

    analyst = Agent(
        client,
        name="portfolio_analyst",
        description="Analyses holdings vs. benchmark and concentration risk.",
        instructions=(
            "You are a portfolio analyst. Assess concentration/benchmark risk in the "
            "client's holdings and the proposed trade. Be specific and brief."
        ),
    )
    compliance = Agent(
        client,
        name="compliance_officer",
        description="Checks suitability, KYC/AML and MiFID II rules.",
        instructions=(
            "You are a compliance officer. Judge suitability vs. the client's risk profile "
            "(a Conservative client must not exceed 70% equities). State APPROVE or BLOCK "
            "with the rule cited. Be brief."
        ),
    )

    workflow = ConcurrentBuilder(participants=[analyst, compliance]).build()

    print(f"TASK: {TASK}\n")
    result = await workflow.run(TASK)
    for msg in result.get_outputs():
        who = getattr(msg, "author_name", None) or getattr(msg, "role", "agent")
        text = getattr(msg, "text", None) or str(msg)
        print(f"--- {who} ---\n{text}\n")

    print("TALK TRACK: two specialists reasoned in parallel and were aggregated — no")
    print("hand-wired handoff graph. Use this when parallelism or trust boundaries earn it;")
    print("otherwise prefer one loop + N skills (Pattern 4). Same story, honest guidance.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[run issue] {e}")
        print("Check GATEWAY_ENDPOINT/GATEWAY_KEY in .env (needs the OpenAI-compatible base,")
        print("often .../openai/v1). You can also swap OpenAIChatClient for agent_framework")
        print("FoundryChatClient to run the specialists directly on the Foundry project.")
