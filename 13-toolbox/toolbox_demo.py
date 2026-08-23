"""
Pattern 13 — Centralized Toolboxes: one governed tool plane for every agent.

Patterns 2 and 3 each wire tools to a single agent. That works until you have
forty agents: every team re-implements the same tools, every agent carries its
own credentials, and nobody can answer "what tools exist and who can call them".

A TOOLBOX is a managed Foundry resource that solves this. You curate tools once
-- MCP servers, Code Interpreter, Web Search, File Search, AI Search, OpenAPI,
A2A -- and Foundry exposes them behind ONE MCP endpoint:

    consumer  {project}/toolboxes/{name}/mcp?api-version=v1        <- default version
    developer {project}/toolboxes/{name}/versions/{v}/mcp?api-version=v1

Agents connect to the consumer endpoint. Credentials stay server-side, guardrails
apply at the toolbox level, and versions are promoted centrally -- so the tool
plane changes without redeploying a single agent.

This script proves two things that are hard to do without a managed tool plane:

  1. VERSIONING. v1 lists its tools directly. v2 adds `toolbox_search`, which
     hides them behind two meta-tools (`tool_search` + `call_tool`). We promote
     v2 to default and the SAME consumer URL starts serving it -- no agent
     redeployed, no code changed.

  2. CONTEXT COST. Every tool definition is input tokens on every request,
     whether the model uses it or not. Tool search collapses N definitions to a
     couple of meta-tools, so a toolbox can hold hundreds of tools without
     flooding the context window or degrading tool selection. The tool counts
     printed below are the evidence.

Auth is keyless: an Entra token for https://ai.azure.com/.default. Note the
scope -- it differs from the cognitiveservices scope the other patterns use.

Run:  uv run python 13-toolbox/toolbox_demo.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:  # tool descriptions carry unicode; keep Windows consoles happy
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from azure.ai.projects.models import (
    CodeInterpreterToolboxTool,
    MCPToolboxTool,
    ToolboxSearchPreviewToolboxTool,
)
from azure.identity import DefaultAzureCredential

from common.foundry import PROJECT_ENDPOINT, env, project_client

TOOLBOX_NAME = env("TOOLBOX_NAME", "rm-toolbox")
TOOLBOX_SCOPE = "https://ai.azure.com/.default"

# Pattern 3 published Web IQ as our own MCP API on APIM. Setting the flag below
# puts it IN the toolbox -- the gateway still authenticates and meters that call,
# and the toolbox hands it to every agent. The two patterns compose.
#
# It is OFF by default on purpose: the APIM rate-limit policy from Pattern 3 also
# meters the toolbox's own tool ENUMERATION, so listing tools repeatedly can trip
# a 429 and stall the demo. Turn it on to tell the composition story; leave it off
# for a run that only depends on the public Microsoft Learn MCP server.
INCLUDE_WEBIQ = (env("TOOLBOX_INCLUDE_WEBIQ", "") or "").strip().lower() in ("1", "true", "yes")
WEBIQ_MCP_URL = env("WEBIQ_MCP_URL")
WEBIQ_APIM_KEY = env("WEBIQ_APIM_KEY", "")


def curated_tools():
    """The tools a Private Banking RM assistant should have, curated once."""
    tools = [
        # Public and unauthenticated, so this pattern needs no extra infrastructure.
        MCPToolboxTool(
            server_label="microsoft_learn",
            server_url="https://learn.microsoft.com/api/mcp",
            server_description="Official Microsoft documentation.",
            require_approval="never",
        ),
        # A toolbox allows only ONE tool without a `name`, so name them all.
        CodeInterpreterToolboxTool(
            name="code",
            description="Run Python to compute portfolio drift and exposure.",
            container={"type": "auto"},
        ),
    ]
    if INCLUDE_WEBIQ and WEBIQ_MCP_URL:
        headers = {"Ocp-Apim-Subscription-Key": WEBIQ_APIM_KEY} if WEBIQ_APIM_KEY else None
        tools.append(
            MCPToolboxTool(
                server_label="web_iq",
                server_url=WEBIQ_MCP_URL,
                server_description="Web IQ grounding, governed by our APIM policy (Pattern 3).",
                headers=headers,
                require_approval="never",
            )
        )
    return tools


def mcp_url(version: str | None = None) -> str:
    """Consumer endpoint (default version) or developer endpoint (pinned version)."""
    tail = f"/versions/{version}/mcp" if version else "/mcp"
    return f"{PROJECT_ENDPOINT}/toolboxes/{TOOLBOX_NAME}{tail}?api-version=v1"


async def list_tools(url: str, headers: dict, attempts: int = 3, delay: float = 8.0) -> list[str]:
    """List tool names from a toolbox MCP endpoint.

    Retry sparingly: a new version needs a few seconds to propagate, but every
    attempt re-enumerates every MCP source behind the toolbox, and a metered
    source (Pattern 3's APIM policy) will start returning 429 if you hammer it.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    last = None
    for attempt in range(attempts):
        try:
            async with streamablehttp_client(url, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]
        except BaseException as e:  # noqa: BLE001 - anyio raises ExceptionGroup here
            last = e
            if attempt == 0:
                print("  (waiting for the new version to propagate...)", flush=True)
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
    raise RuntimeError(f"toolbox endpoint not ready after {attempts} attempts:\n{flatten(last)}")


def flatten(e, depth: int = 0) -> str:
    """anyio wraps failures in ExceptionGroup, which prints nothing useful."""
    out = [f"{'  ' * depth}{type(e).__name__}: {str(e)[:400]}"]
    for sub in getattr(e, "exceptions", []) or []:
        out.append(flatten(sub, depth + 1))
    return "\n".join(out)


def label(tool) -> str:
    return tool.get("server_label") or tool.get("name") or tool["type"]


async def main():
    if not PROJECT_ENDPOINT:
        raise SystemExit("Set PROJECT_ENDPOINT in .env")

    cred = DefaultAzureCredential(process_timeout=30)
    headers = {"Authorization": f"Bearer {cred.get_token(TOOLBOX_SCOPE).token}"}

    with project_client() as project:
        tools = curated_tools()
        print(f"Curating {len(tools)} tools into toolbox '{TOOLBOX_NAME}':")
        for t in tools:
            print(f"  - {label(t)} ({t['type']})")

        v1 = project.toolboxes.create_version(
            name=TOOLBOX_NAME,
            tools=tools,
            description="RM assistant tools, curated centrally.",
        )
        print(f"\nv{v1.version} created - tools listed directly.")

        # Same tools, but discovery switched on.
        v2 = project.toolboxes.create_version(
            name=TOOLBOX_NAME,
            tools=tools + [ToolboxSearchPreviewToolboxTool(name="tool_search")],
            description="Same tools, discovered via tool search.",
        )
        print(f"v{v2.version} created - same tools, plus tool search.")

        default_before = project.toolboxes.get(TOOLBOX_NAME).default_version
        print(f"\nDefault version is still v{default_before}. Agents are untouched.")

        direct = await list_tools(mcp_url(v1.version), headers)
        searched = await list_tools(mcp_url(v2.version), headers)

        print(f"\n  v{v1.version} exposes {len(direct)} tool definitions: {direct}")
        print(f"  v{v2.version} exposes {len(searched)}: {searched}")
        print("  Every definition in the first list is input tokens on EVERY request.")

        # The promotion -- this is the money moment.
        project.toolboxes.update(TOOLBOX_NAME, default_version=v2.version)
        default_after = project.toolboxes.get(TOOLBOX_NAME).default_version
        print(f"\nPromoted default: v{default_before} -> v{default_after}")

        consumer = await list_tools(mcp_url(), headers)
        print(f"Consumer endpoint (same URL as before) now serves: {consumer}")

        # Prove the meta-tools actually resolve a real tool.
        if any("search" in name for name in consumer):
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            search_tool = next(n for n in consumer if "search" in n)
            async with streamablehttp_client(mcp_url(), headers=headers) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    found = await session.call_tool(
                        search_tool, {"query": "look up official Microsoft documentation"}
                    )
                    for block in found.content:
                        text = getattr(block, "text", None)
                        if text:
                            print(f"\n{search_tool} resolved:\n{text[:700]}")

    print("\nTALK TRACK: the agent never changed. One endpoint, curated once,")
    print("credentials held server-side, guardrails at the toolbox level - and a")
    print("new version promoted centrally reached every consumer instantly. That")
    print("is the difference between wiring tools per agent and running a tool plane.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[toolbox]\n{flatten(e)}")
        print("\nNeeds the Foundry User role on the project and a supported region.")
        print("Token audience must be https://ai.azure.com — the cognitiveservices")
        print("scope the other patterns use returns 401 here.")
