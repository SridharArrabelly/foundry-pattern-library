# Pattern 6 — Observability & tracing (OpenTelemetry)

**Group:** Operate & optimise  ·  **Runs 10th of 12** in the run order

**Slide title:** *One OpenTelemetry trace tree per agent run — in the portal AND your stack.*

## In brief
> "This pattern creates a **real Foundry agent** — `rm-assistant-traced`, it shows up in the
> **Agents** list, you can chat with it live — and then runs one turn with **OpenTelemetry**
> switched on. Now look at the same run in **three** places:
>
>   1. **Foundry portal → Agents** — the agent is a first-class, versioned object.
>   2. **Foundry portal → Tracing tab** — the agent-native waterfall, no extra setup.
>   3. **Application Insights** — Transaction search / Logs (KQL) on the *same* data.
>
> The waterfall reads: `rm-observability-demo → invoke_agent → get_client_holdings →
> chat gpt-5.4-mini`, each span carrying **model, prompt + completion, token counts,
> latency and the tool name**. That's how you debug a non-deterministic agent and
> attribute spend.
>
> Two things that are hard to build yourself. First, it's **agent-aware** — spans
> understand agents, tools and runs, not just HTTP calls, and Foundry traces the run
> **server-side** into the Tracing tab for free. Second, it's **OpenTelemetry**, so there's
> **no lock-in**: the same stream goes to App Insights *and* your Datadog / Grafana /
> Elastic via one OTLP exporter — which a cloud-native APM tied to one provider won't do."

## And a fourth place, with BYOM
> "If you've set up **BYOM** (Pattern 2), there's a fourth: the **gateway's own metrics**.
> Because APIM is the model's declared backend, this agent's inference shows up as gateway
> requests alongside your client traffic — one place to see spend across both planes."

Set `AGENT_MODEL_CONNECTION` in `.env` and this pattern's agent routes through APIM too
(measured: 3 invocations → +3 gateway requests, tools working normally). Leave it blank and
the agent runs on the direct Foundry route — the traces are identical either way.

## Why a Foundry agent (not an in-process one)
A Foundry agent has a **portal identity** and Foundry exports its run traces server-side to
the project's connected App Insights — the exact resource that backs the **Tracing** tab.
We ALSO wire the client-side Azure Monitor exporter, so our parent span + the local tool
call are captured too. Result: the run shows in the portal Tracing tab **and** App Insights.

## What Foundry gives you here
- **Agent-native tracing** (agent/tool/run spans) out of the box, in the portal.
- **Token + cost + latency** attributes per span → real observability & FinOps.
- **OpenTelemetry** = portable; ship to Azure Monitor *and* your existing backend.

## Cost attribution comes free with this
The same spans carry token counts, so cost per agent is a KQL query away — no separate
metering pipeline, no spreadsheet. Group by `gen_ai.agent.name` and the agent **version** and
you can show that a new version is cheaper *and* better before you promote it:

```kusto
dependencies
| where isnotempty(customDimensions["gen_ai.agent.name"])
| summarize calls = count(),
            tokens = sum(toint(customDimensions["gen_ai.usage.total_tokens"]))
        by agent = tostring(customDimensions["gen_ai.agent.name"]),
           version = tostring(customDimensions["gen_ai.agent.version"])
```

Multiply tokens by your rate card for spend per agent. That last step is your arithmetic, not
a platform feature — but the attribution underneath it is real telemetry, which is the part
that's hard to build. Agent 365 adds the org-wide inventory, identity and policy layer on top.

## The one-liner
> "Same agent you saw in the Agents list — now with a flight recorder. And it's OTel, so it's yours."

## Running it
1. `uv run python 06-observability/enable_tracing.py` → prints the agent name/version + answer
   (C-1290 = **not compliant**, over-weight equities).
2. Portal: **Foundry → Agents → rm-assistant-traced** — show it exists; optionally chat with it.
3. Portal: **Foundry → Tracing** → open the run's waterfall (server-side spans).
4. **App Insights → Transaction search / Logs** → same trace, ~1–2 min ingestion lag.
5. Point at a model span: tokens, latency, prompt/completion. Mention the one-line OTLP dual-export.
6. With `AGENT_MODEL_CONNECTION` set, show the same run in the **gateway metrics** (BYOM).

## Notes
- Two env flags must be ON **before** instrumentation (the script sets them):
  `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true` (prompt/completion text on spans) and
  `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` (emit the GenAI semantic spans). Without the
  second, App Insights logs a warning and you get only generic spans.
- Benign on a laptop: red `169.254.169.254` / IMDS / WinError 10051 tracebacks are the Azure
  Monitor VM resource detector + managed-identity probe looking for cloud metadata. Harmless,
  and absent on Azure compute.
