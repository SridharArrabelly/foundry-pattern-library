"""
Pattern 3 — Microsoft IQ: Web IQ as a governed MCP tool behind APIM.

Web IQ is Microsoft's AI-first web-grounding stack: model-agnostic, MCP-native,
~2.5x faster than the next best alternative (Build 2026).

The pattern is *tool governance*. Models are only half of what an agent calls --
it also calls tools, and those calls deserve the same control point. So Web IQ is
published as our own MCP API on APIM, and the gateway does three jobs:

  * authenticates the caller (APIM subscription key -- no key, no access);
  * holds the Web IQ credential as a secret named value and injects it upstream,
    so the backend key never reaches a client or a .env file;
  * meters usage with a rate-limit policy enforced BEFORE the call is proxied.

Measured on this APIM: no key -> 401, bogus key -> 401, valid key -> 200 with the
credential added by the gateway, and calls past the limit -> 429.

One caveat worth knowing: MCP streams over SSE, so a policy must never read
`context.Response.Body` -- that forces buffering and breaks the stream. Control is
therefore inbound-side (auth, quota, allow-listing), not response inspection.

Here we consume it directly:

  1. Resolve the route (our APIM MCP endpoint; falls back to the Foundry-managed
     tool connection if WEBIQ_MCP_URL isn't set).
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
ENDPOINT = os.environ.get("PROJECT_ENDPOINT")
# Preferred route: our OWN MCP API on APIM. The gateway holds the Web IQ key as a
# secret named value and injects it, so this process only ever carries an APIM
# subscription key -- which is also what the rate-limit policy meters.
GATEWAY_MCP_URL = os.environ.get("WEBIQ_MCP_URL")
GATEWAY_MCP_KEY = os.environ.get("WEBIQ_APIM_KEY", "")
# Fallback: the Foundry-managed tool connection (key stored server-side by Foundry).
MCP_CONNECTION = os.environ.get("WEBIQ_CONNECTION_NAME", "WebIQ-MCP-1")
# Preferred web-grounding tool exposed by the Web IQ server.
PREFERRED_TOOLS = ("web", "news", "sonic")

QUERY = (
    "Latest regulatory news on MiFID II suitability rules for private banking in 2026. "
    "Summarise briefly and include the source URLs."
)


def resolve_connection():
    """Return (url, headers, route_label) for the Web IQ MCP server.

    Prefers our hand-built APIM route so the gateway is the single control point:
    it authenticates the caller, injects the Web IQ credential, and meters usage.
    Falls back to the Foundry-managed tool connection when it isn't configured.
    """
    if GATEWAY_MCP_URL:
        # No backend credential here on purpose -- APIM adds `x-apikey` itself.
        headers = {"Ocp-Apim-Subscription-Key": GATEWAY_MCP_KEY} if GATEWAY_MCP_KEY else {}
        return GATEWAY_MCP_URL, headers, "APIM (our route, policy-governed)"

    if not ENDPOINT:
        raise RuntimeError("Set WEBIQ_MCP_URL (preferred) or PROJECT_ENDPOINT in .env")

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())
    with project:
        conn = project.connections.get(MCP_CONNECTION, include_credentials=True)
        # Custom-key connection: {'x-apikey': '...', 'type': 'CustomKeys'} -> drop 'type'.
        headers = {k: v for k, v in conn.credentials.items() if k != "type"}
        return conn.target, headers, f"Foundry-managed connection '{MCP_CONNECTION}'"


async def main():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url, headers, route = resolve_connection()
    print(f"Web IQ MCP server: {url}")
    print(f"Route: {route}")
    print(f"Headers sent: {', '.join(headers) or 'none'} (no Web IQ key client-side)\n")

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

    print("\nTALK TRACK: agents call TOOLS as well as models, so tools need the same control")
    print("point. Web IQ is published as our own MCP API on APIM: the gateway authenticates")
    print("the caller, injects the Web IQ key from a secret it holds, and meters every call")
    print("(401 without a key, 429 past the limit). The client you just ran carries no Web IQ")
    print("credential at all. Pair Web IQ (web) with Foundry IQ (Azure AI Search) to plan")
    print("retrieval across web + enterprise — both governed by the same gateway.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[connection issue] {e}")
        print("Check PROJECT_ENDPOINT and that the 'WebIQ-MCP-1' tool connection exists in")
        print("your Foundry project (Tools). Override the name with WEBIQ_CONNECTION_NAME.")
