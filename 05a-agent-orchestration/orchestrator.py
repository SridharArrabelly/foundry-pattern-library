"""
Pattern 5A — Agent orchestration (multi-agent coordination).

A deliberately SIMPLE orchestration: a client request fans out concurrently to two
specialist agents — a Portfolio Analyst and a Compliance Officer — and their views are
returned as one fan-in result. This is the pattern to reach for when you genuinely need parallel
specialists or trust boundaries (contrast Pattern 4: default to one loop + N skills).

This local entry point runs on the customer's Azure AI Gateway (OpenAI-compatible).
The same workflow definition is hosted through Foundry's Responses protocol under
./hosted/, so the orchestration is also a first-class managed endpoint.

Run:  uv run python 05a-agent-orchestration/orchestrator.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
HOSTED_SOURCE = os.path.join(
    os.path.dirname(__file__),
    "hosted",
    "src",
    "multi-agent-orchestrator",
)
sys.path.insert(0, HOSTED_SOURCE)

from common.foundry import (
    GATEWAY_ENDPOINT,
    GATEWAY_KEY,
    GATEWAY_MODEL,
    GATEWAY_V1_API_VERSION,
)

from workflow import TASK, create_workflow


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
    client = build_client()
    workflow = create_workflow(client)

    print(f"TASK: {TASK}\n")
    result = await workflow.run(TASK)
    for msg in result.get_outputs():
        who = getattr(msg, "author_name", None) or getattr(msg, "role", "agent")
        text = getattr(msg, "text", None) or str(msg)
        print(f"--- {who} ---\n{text}\n")

    print("TALK TRACK: two specialists reasoned in parallel and returned one fan-in result — no")
    print("hand-wired handoff graph. Use this when parallelism or trust boundaries earn it;")
    print("otherwise prefer one loop + N skills (Pattern 4). The same workflow is published")
    print("through Foundry under 05a-agent-orchestration/hosted as a managed Responses endpoint.")


if __name__ == "__main__":
    asyncio.run(main())
