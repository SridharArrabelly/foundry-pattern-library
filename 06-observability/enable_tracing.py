"""
Pattern 6 — Observability & tracing for a REAL Foundry agent.

Creates (versions) a Foundry agent, turns on OpenTelemetry tracing, and runs one agent
turn — so the SAME trace shows up in TWO places AND the agent is a first-class object in
the portal:

  * Foundry portal -> project -> Agents -> "rm-assistant-traced" (chattable, versioned).
  * Foundry portal -> project -> "Tracing" tab (agent-native waterfall).
  * Application Insights -> Transaction search / Application map / Logs (KQL).

Because it's OpenTelemetry underneath, the same stream can also fan out to your own
backend (Datadog/Grafana/Elastic) — no lock-in.

Why a Foundry agent (not an in-process one): a Foundry agent has a portal identity and
Foundry traces its runs server-side to the project's App Insights. We ALSO wire the
client-side Azure Monitor exporter so the whole turn (incl. our parent span + the tool
call we run locally) is captured with agent/model/tool metadata, token counts and latency.
Prompt and completion content is metadata-only by default; set
TRACE_CONTENT_RECORDING=true only after approving the data-sensitivity and retention impact.

Run:  uv run python 06-observability/enable_tracing.py
Then open the agent in the portal, and the trace in BOTH the Tracing tab and App Insights.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import (
    PROJECT_ENDPOINT,
    agent_model,
    app_insights_connection_string,
)

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential

AGENT_NAME = "rm-assistant-traced"
QUESTION = "Is client C-1290 compliant per the suitability policy?"


# --- a function tool so the trace shows a real agent -> tool -> model waterfall ---
def get_client_holdings(client_id: str) -> str:
    """Return holdings + suitability summary for a private-banking client id, e.g. C-1290."""
    demo = {
        "C-8842": "Mrs. Chen — 62% equities, 30% bonds, 8% cash; risk: Balanced; suitability: OK.",
        "C-1290": "Mr. Okafor — 90% equities; risk: Conservative; suitability: FLAGGED (over-weight equities).",
    }
    return demo.get(client_id, "unknown client id")


HOLDINGS_TOOL = FunctionTool(
    name="get_client_holdings",
    description="Return holdings + suitability summary for a private-banking client id, e.g. C-1290.",
    parameters={
        "type": "object",
        "properties": {"client_id": {"type": "string", "description": "Client id, e.g. C-1290."}},
        "required": ["client_id"],
        "additionalProperties": False,
    },
    strict=True,
)


def enable_tracing():
    """Send metadata-only spans unless content recording is explicitly enabled."""
    conn = app_insights_connection_string()
    print("Resolved the Application Insights connected to the Foundry project.")

    record_content = os.environ.get("TRACE_CONTENT_RECORDING", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    # Set before instrumentation. Semantic spans remain enabled in both modes; only
    # prompt/completion bodies are gated by the explicit enterprise opt-in.
    os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = (
        "true" if record_content else "false"
    )
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    if record_content:
        print(
            "Trace content recording ENABLED: prompts/completions are exported and inherit "
            "the telemetry backend's access and retention policy."
        )
    else:
        print("Trace content recording disabled: exporting metadata, tokens and latency only.")

    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=conn)

    # Instrument the OpenAI SDK -> model spans (tokens/latency) for the Responses calls.
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
        print("OpenAI SDK instrumented — model spans (tokens/latency) will be emitted.")
    except Exception as e:
        print(f"(OpenAI instrumentor not applied: {e} — agent spans still flow.)")


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
            outputs.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": get_client_holdings(**args)}
            )
        resp = openai_client.responses.create(
            previous_response_id=resp.id, input=outputs, extra_body={"agent_reference": ref}
        )


def main():
    # 1) Tracing ON, pointed at the project's App Insights (backs the portal Tracing tab).
    enable_tracing()

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    with project:
        openai_client = project.get_openai_client()

        # 2) Create (or version) a real Foundry agent — shows in the portal Agents list.
        #    agent_model() gateway-qualifies the deployment when BYOM is configured, so
        #    this single call shows up in BOTH the trace and the APIM metrics.
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=agent_model(),
                instructions=(
                    "You are a private-banking Relationship Manager assistant. Use "
                    "get_client_holdings for portfolio/suitability questions. Be brief."
                ),
                tools=[HOLDINGS_TOOL],
            ),
        )
        print(f"Foundry agent: {agent.name} (version {agent.version}) — visible in the portal.\n")

        # 3) Run one turn under a parent span so the whole thing groups into one trace.
        from opentelemetry import trace

        tracer = trace.get_tracer("foundry-demo-pack")
        with tracer.start_as_current_span("rm-observability-demo") as span:
            span.set_attribute("gen_ai.agent.name", AGENT_NAME)
            span.set_attribute("demo.client_id", "C-1290")
            print(f"> {QUESTION}")
            answer = ask(openai_client, agent.name, QUESTION)
            print("\nAgent answer:\n", answer)

        # 4) Force-flush so spans are exported before the process exits.
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()

    print("\nSpans flushed. Show it in THREE places:")
    print(f"  1. Foundry portal -> your project -> Agents -> {AGENT_NAME} (chat with it live).")
    print("  2. Foundry portal -> your project -> 'Tracing' tab (agent waterfall).")
    print("  3. Application Insights -> Transaction search / Logs (may take ~1-2 min).")
    print("Each span carries: model, token counts, latency and tool name.")
    print(
        "Prompt/completion bodies are "
        + ("included (explicit opt-in)." if os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] == "true" else "excluded (enterprise-safe default).")
    )
    print("Waterfall: rm-observability-demo -> agent run -> get_client_holdings -> model call.")

    print("\nNO LOCK-IN: it's OpenTelemetry. To ALSO ship to your own collector, set")
    print("OTEL_EXPORTER_OTLP_ENDPOINT in .env and add a BatchSpanProcessor(OTLPSpanExporter).")
    # To remove this agent version later:
    # project.agents.delete_version(agent_name=AGENT_NAME, agent_version=agent.version)


if __name__ == "__main__":
    main()
