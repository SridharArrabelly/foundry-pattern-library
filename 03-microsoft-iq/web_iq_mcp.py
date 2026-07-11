"""
Pattern 3 — Microsoft IQ: Web IQ via a governed MCP tool (Foundry tool connection).

Web IQ is Microsoft's AI-first web-grounding stack: model-agnostic, MCP-native,
~2.5x faster than the next best alternative (Build 2026). In THIS project it is
registered as a Foundry **tool connection** ("WebIQ-MCP-1") whose traffic is
governed by your Azure AI Gateway (the key is stored server-side, not in code).

That's the pattern: the MCP server is registered ONCE as a governed tool, and any
MCP client — a Foundry agent, GitHub Copilot, even a Bedrock agent — grounds
through the same governed endpoint. Here we consume it directly:

  1. Read the governed connection from Foundry at RUNTIME (keyless: we auth with
     Entra via DefaultAzureCredential; the platform returns the endpoint + the
     header key it stored). Nothing secret lives in code or .env.
  2. Open an MCP (streamable HTTP) session, list the Web IQ tools, and run one
     grounded web query for a private-banking research question — with citations.

For the *Foundry IQ* half (enterprise grounding over Azure AI Search) run skill-forge's
`rag-search` skill — see TALK-TRACK.md.

Verified against azure-ai-projects 2.3.0 + mcp 1.28.1 (keyless, DefaultAzureCredential).

Run:  uv run python 03-microsoft-iq/web_iq_mcp.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:  # citations often contain unicode; keep Windows consoles happy
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()
ENDPOINT = os.environ["PROJECT_ENDPOINT"]
# The Foundry tool connection holding the Web IQ MCP endpoint + key (AI Gateway
# governed). Registered in the portal under Tools; referenced here by name.
MCP_CONNECTION = os.environ.get("WEBIQ_CONNECTION_NAME", "WebIQ-MCP-1")
# Preferred web-grounding tool exposed by the Web IQ server.
PREFERRED_TOOLS = ("web", "news", "sonic")

QUERY = (
    "Latest regulatory news on MiFID II suitability rules for private banking in 2026. "
    "Summarise briefly and include the source URLs."
)


def resolve_connection():
    """Keyless: read the governed Web IQ connection (endpoint + header key) from Foundry."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())
    with project:
        conn = project.connections.get(MCP_CONNECTION, include_credentials=True)
        # Custom-key connection: {'x-apikey': '...', 'type': 'CustomKeys'} -> drop 'type'.
        headers = {k: v for k, v in conn.credentials.items() if k != "type"}
        return conn.target, headers


async def main():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url, headers = resolve_connection()
    print(f"Web IQ MCP server (AI Gateway governed): {url}")
    print(f"Auth: {', '.join(headers) or 'none'} (read from Foundry connection '{MCP_CONNECTION}')\n")

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("Web IQ tools:", names, "\n")

            tool = next((n for n in PREFERRED_TOOLS if n in names), names[0] if names else None)
            if not tool:
                print("No tools exposed by the MCP server.")
                return

            print(f"Calling `{tool}` with a grounded query...\n> {QUERY}\n")
            result = await session.call_tool(tool, {"query": QUERY})
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print(text[:1800])

    print("\nTALK TRACK: Web IQ is registered ONCE as a governed MCP tool in Foundry (key")
    print("stored server-side, AI-Gateway governed). ANY agent — Foundry, Copilot, even a")
    print("Bedrock agent — grounds through this same governed endpoint. Pair Web IQ (web)")
    print("with Foundry IQ (Azure AI Search) to plan retrieval across web + enterprise.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[connection issue] {e}")
        print("Check PROJECT_ENDPOINT and that the 'WebIQ-MCP-1' tool connection exists in")
        print("your Foundry project (Tools). Override the name with WEBIQ_CONNECTION_NAME.")
