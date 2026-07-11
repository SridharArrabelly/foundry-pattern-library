"""
Pattern 2 — Foundry agents, model A: **prompt-based agent** (declarative).

Same Private Banking scenario as the hosted agent in ./hosted/, but the OTHER hosting
model: you hand Foundry a model + instructions + tools and it runs a managed agent for
you. Here that agent gets:
  (a) managed RAG over a policy doc — File Search + a **managed vector store** (no vector
      DB to run), and
  (b) a **function tool** (get_client_holdings) — your own business logic.

This uses the new unified Foundry SDK — the SAME surface as the hosted agent:
`AIProjectClient.agents.create_version(PromptAgentDefinition(...))`. That creates a
first-class, **versioned** agent that shows up in the Foundry portal (not a legacy
`asst_...` assistant). We invoke it through the OpenAI-compatible **Responses** API via an
`agent_reference`, and run a small tool-call loop for the function tool (File Search runs
server-side automatically).

Names (both land in the same project, so they must differ):
  * this one  -> "rm-assistant-prompt"   (declarative / managed assistant)
  * ./hosted/ -> "rm-assistant-hosted"   (your Agent Framework container)

Re-running just adds a new *version* of the same agent (dedupes by name) — unlike the old
assistants API, which created a brand-new agent every run.

Verified against azure-ai-projects 2.3.0 + openai 2.45.0 (keyless, DefaultAzureCredential).

Run:  uv run python 02-hosted-agents/create_agent.py
"""
import json
import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    FileSearchTool,
    FunctionTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()
ENDPOINT = os.environ["PROJECT_ENDPOINT"]
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini")
AGENT_NAME = "rm-assistant-prompt"


# --- a custom function tool: the agent can call your core-banking logic ---
def get_client_holdings(client_id: str) -> str:
    """Return a short holdings + suitability summary for a private-banking client id."""
    demo = {
        "C-8842": "Mrs. Chen — 62% equities, 30% bonds, 8% cash; risk profile: Balanced; suitability: OK.",
        "C-1290": "Mr. Okafor — 90% equities; risk profile: Conservative; suitability: FLAGGED (over-weight equities).",
    }
    return demo.get(client_id, "unknown client id")


# JSON-schema declaration of that tool for the model (execution stays on our side).
HOLDINGS_TOOL = FunctionTool(
    name="get_client_holdings",
    description="Return holdings + suitability summary for a private-banking client id, e.g. C-1290.",
    parameters={
        "type": "object",
        "properties": {
            "client_id": {"type": "string", "description": "Client id, e.g. C-1290."}
        },
        "required": ["client_id"],
        "additionalProperties": False,
    },
    strict=True,
)


def build_vector_store(openai_client):
    """Upload the policy doc and build a managed vector store for File Search (RAG)."""
    doc_path = os.path.join(os.path.dirname(__file__), "sample-policy.md")
    vstore = openai_client.vector_stores.create(name="wealth-policy-kb")
    with open(doc_path, "rb") as f:
        openai_client.vector_stores.files.upload_and_poll(
            vector_store_id=vstore.id, file=f
        )
    return vstore.id


def ask(openai_client, agent_name, question):
    """Ask the agent via the Responses API, running the function-tool loop as needed."""
    ref = {"name": agent_name, "type": "agent_reference"}
    resp = openai_client.responses.create(input=question, extra_body={"agent_reference": ref})

    while True:
        calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not calls:
            return resp.output_text
        outputs = []
        for call in calls:
            args = json.loads(call.arguments or "{}")
            result = get_client_holdings(**args)
            outputs.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": result}
            )
        resp = openai_client.responses.create(
            previous_response_id=resp.id,
            input=outputs,
            extra_body={"agent_reference": ref},
        )


def main():
    project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())
    with project:
        openai_client = project.get_openai_client()

        # 1) managed RAG: a vector store the platform hosts for us (no vector DB to run)
        vector_store_id = build_vector_store(openai_client)
        file_search = FileSearchTool(vector_store_ids=[vector_store_id])

        # 2) create (or version) the prompt-based agent — shows in the Foundry portal
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=MODEL,
                instructions=(
                    "You are a private-banking Relationship Manager assistant. Use File "
                    "Search for policy/suitability questions and get_client_holdings for "
                    "client portfolio questions. Always cite the policy when relevant. "
                    "Keep answers brief."
                ),
                tools=[file_search, HOLDINGS_TOOL],
            ),
        )
        print(f"agent name : {agent.name}  (version {agent.version})")

        # 3) ask a compound question — File Search + the function tool in one turn
        answer = ask(
            openai_client,
            agent.name,
            "What's the suitability rule in the policy, and is client C-1290 compliant?",
        )
        print("assistant  :", answer)

        print(
            f"\nPortal: Foundry > your project > Agents > {AGENT_NAME} "
            "(versioned) — Identity shows its Entra Agent ID."
        )
        # Left running for the observability + eval demos. To remove a version:
        # project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)


if __name__ == "__main__":
    main()
