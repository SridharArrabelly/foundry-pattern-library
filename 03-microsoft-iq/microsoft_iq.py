"""
Pattern 3 — Microsoft IQ: live Web IQ plus enterprise grounding.

This pattern runs two distinct grounding paths:

  1. Web IQ over the existing APIM MCP route. APIM Basic v2 uses a subscription
     key for the caller-facing route; APIM retains the upstream Web IQ credential
     and applies the gateway policy.
  2. Azure AI Search through a versioned Foundry prompt agent using the official
     AzureAISearchTool. The Search connection credential remains in Foundry and
     the script authenticates to the project with DefaultAzureCredential.

Pattern 2 uses managed File Search/vector stores. That is useful RAG, but it is
not Azure AI Search. This implementation deliberately keeps the two patterns
separate and names the technology precisely.

Foundry IQ managed knowledge bases, Fabric IQ, and Work IQ are broader Microsoft
IQ product layers. They remain narrated here; this script wires Web IQ and the
Azure AI Search tool path only.

Run both legs:
  uv run python 03-microsoft-iq/microsoft_iq.py

Run one leg while configuring or diagnosing:
  uv run python 03-microsoft-iq/microsoft_iq.py --leg web
  uv run python 03-microsoft-iq/microsoft_iq.py --leg search
"""
import argparse
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
if hasattr(sys.stdout, "reconfigure"):  # citations often contain unicode
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()
ENDPOINT = os.environ.get("PROJECT_ENDPOINT")
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini")

# APIM Basic v2 subscription-key exception: the direct client may carry this APIM
# key, but never the upstream Web IQ credential. APIM injects that server-side.
GATEWAY_MCP_URL = os.environ.get("WEBIQ_MCP_URL")
GATEWAY_MCP_KEY = os.environ.get("WEBIQ_APIM_KEY", "")
PREFERRED_TOOLS = ("web", "news", "sonic")

WEB_QUERY = (
    "Latest regulatory news on MiFID II suitability rules for private banking in 2026. "
    "Summarise briefly and include the source URLs."
)
SEARCH_QUERY = (
    "According to the indexed suitability policy, can a Conservative client hold "
    "90% equities? State the applicable limit and cite the indexed source."
)
SEARCH_AGENT_NAME = "rm-assistant-enterprise-search"


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Set {name} in .env")
    return value


def resolve_web_connection():
    """Return the explicit APIM Basic v2 route and caller credential."""
    if not GATEWAY_MCP_URL:
        raise SystemExit("Set WEBIQ_MCP_URL to the APIM MCP route in .env")
    if not GATEWAY_MCP_KEY:
        raise SystemExit("Set WEBIQ_APIM_KEY to the APIM Basic v2 subscription key in .env")
    return (
        GATEWAY_MCP_URL,
        {"Ocp-Apim-Subscription-Key": GATEWAY_MCP_KEY},
        "APIM MCP route (Basic v2 subscription-key route)",
    )


async def run_web_iq():
    """Call the policy-governed Web IQ MCP route and print cited web context."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url, headers, route = resolve_web_connection()
    print("===== WEB IQ (LIVE) =====")
    print(f"Web IQ MCP server: {url}")
    print(f"Route: {route}")
    print(f"Headers sent: {', '.join(headers) or 'none'} (upstream Web IQ key stays on APIM)\n")

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("Web IQ tools:", names, "\n")

            tool = next((n for n in PREFERRED_TOOLS if n in names), names[0] if names else None)
            if not tool:
                raise RuntimeError("The configured Web IQ MCP route exposes no tools")

            print(f"Calling `{tool}` with a grounded query...\n> {WEB_QUERY}\n")
            result = await session.call_tool(tool, {"query": WEB_QUERY})
            texts, source_urls = validate_web_result(result)
            for text in texts:
                print(text[:1800])

    print("\nWeb IQ complete: APIM authenticated and metered the tool call; citations came")
    print(f"from live web grounding ({len(source_urls)} source URL(s) validated). The Basic")
    print("v2 caller subscription key is the documented exception to this repository's")
    print("keyless-first posture.")


def _source_urls(value) -> set[str]:
    urls = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key.lower() in {"url", "sourceurl", "source_url"}
                and isinstance(item, str)
                and item.startswith(("https://", "http://"))
            ):
                urls.add(item)
            urls.update(_source_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.update(_source_urls(item))
    return urls


def validate_web_result(result) -> tuple[list[str], list[str]]:
    """Fail on MCP errors or payloads without source-bearing grounded output."""
    if getattr(result, "isError", False):
        raise RuntimeError("Web IQ MCP tool returned isError=true")

    structured = getattr(result, "structuredContent", None)
    texts = [
        text
        for block in getattr(result, "content", []) or []
        if (text := getattr(block, "text", None))
    ]

    urls = _source_urls(structured) if structured is not None else set()
    for text in texts:
        try:
            urls.update(_source_urls(json.loads(text)))
        except json.JSONDecodeError:
            urls.update(re.findall(r"https?://[^\s\"'<>]+", text))
    if not urls:
        raise RuntimeError("Web IQ MCP output contained no source URLs/citations")
    if not texts and structured is not None:
        texts = [json.dumps(structured, ensure_ascii=False)]
    if not texts:
        raise RuntimeError("Web IQ MCP tool returned no printable grounded content")
    return texts, sorted(urls)


def annotation_dict(annotation) -> dict:
    if hasattr(annotation, "model_dump"):
        return annotation.model_dump(exclude_none=True)
    if hasattr(annotation, "as_dict"):
        return annotation.as_dict()
    if isinstance(annotation, dict):
        return annotation
    return {
        key: getattr(annotation, key)
        for key in ("type", "title", "text", "url", "filename", "index")
        if getattr(annotation, key, None) is not None
    }


def collect_search_citations(response) -> list[dict]:
    """Collect citation annotations returned by the Responses API."""
    citations = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                value = annotation_dict(annotation)
                annotation_type = value.get("type", "")
                if "citation" in annotation_type and value not in citations:
                    citations.append(value)
    return citations


def print_search_citations(citations: list[dict]):
    for number, citation in enumerate(citations, start=1):
        label = (
            citation.get("title")
            or citation.get("filename")
            or citation.get("text")
            or citation.get("url")
            or citation.get("type")
            or "Azure AI Search result"
        )
        print(f"  [{number}] {label}")


def validate_search_response(response) -> list[dict]:
    """Require a completed Search call, its output, and citation-bearing answer text."""
    if getattr(response, "status", None) != "completed":
        raise RuntimeError(
            f"Azure AI Search response did not complete: {getattr(response, 'status', None)!r}"
        )
    output = getattr(response, "output", []) or []
    calls = [item for item in output if getattr(item, "type", None) == "azure_ai_search_call"]
    call_outputs = [
        item for item in output if getattr(item, "type", None) == "azure_ai_search_call_output"
    ]
    if not calls:
        raise RuntimeError("The agent response contains no Azure AI Search tool invocation")
    if not call_outputs:
        raise RuntimeError("The agent response contains no Azure AI Search tool output")
    for item in calls + call_outputs:
        status = getattr(item, "status", None)
        if status not in (None, "completed"):
            raise RuntimeError(f"Azure AI Search tool item did not complete: {status!r}")

    citations = collect_search_citations(response)
    if not citations:
        raise RuntimeError("Azure AI Search answer contains no citation annotations")
    if not (getattr(response, "output_text", None) or "").strip():
        raise RuntimeError("Azure AI Search answer text is empty")
    return citations


def run_enterprise_search():
    """Ground a Foundry agent with the official AzureAISearchTool."""
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        AISearchIndexResource,
        AzureAISearchQueryType,
        AzureAISearchTool,
        AzureAISearchToolResource,
        PromptAgentDefinition,
    )
    from azure.identity import DefaultAzureCredential

    endpoint = required("PROJECT_ENDPOINT")
    connection_name = required("AI_SEARCH_CONNECTION_NAME")
    index_name = required("AI_SEARCH_INDEX_NAME")

    print("\n===== AZURE AI SEARCH TOOL (LIVE) =====")
    project = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(process_timeout=30),
    )
    with project:
        connection = project.connections.get(connection_name)
        search = AzureAISearchTool(
            azure_ai_search=AzureAISearchToolResource(
                indexes=[
                    AISearchIndexResource(
                        project_connection_id=connection.id,
                        index_name=index_name,
                        query_type=AzureAISearchQueryType.SIMPLE,
                        top_k=3,
                    )
                ]
            )
        )
        agent = project.agents.create_version(
            agent_name=SEARCH_AGENT_NAME,
            definition=PromptAgentDefinition(
                model=MODEL,
                instructions=(
                    "Answer enterprise policy questions only from Azure AI Search. "
                    "Cite the retrieved source title or path in every answer. If the "
                    "index does not support an answer, say so."
                ),
                tools=[search],
            ),
        )
        print(
            f"Foundry agent: {agent.name} v{agent.version}; "
            f"Azure AI Search connection: {connection_name}; index: {index_name}"
        )
        response = project.get_openai_client().responses.create(
            input=SEARCH_QUERY,
            tool_choice="required",
            extra_body={
                "agent_reference": {
                    "name": agent.name,
                    "type": "agent_reference",
                }
            },
        )
        citations = validate_search_response(response)
        print(f"\n> {SEARCH_QUERY}\n")
        print(response.output_text)
        print_search_citations(citations)

    print("\nAzure AI Search complete: a Search call + output + citation annotations were")
    print("validated. Foundry used the project connection server-side;")
    print("DefaultAzureCredential was used only for project authentication.")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--leg",
        choices=("all", "web", "search"),
        default="all",
        help="Run both grounding paths by default, or select one while configuring.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.leg in ("all", "web"):
        asyncio.run(run_web_iq())
    if args.leg in ("all", "search"):
        run_enterprise_search()

    print("\nTALK TRACK: Web IQ is live through the APIM MCP control point, and enterprise")
    print("grounding is live through the official Azure AI Search tool. Foundry IQ managed")
    print("knowledge bases, Fabric IQ, and Work IQ are broader narrated layers, not relabeled")
    print("File Search and not claimed as wired by this sample.")


if __name__ == "__main__":
    main()
