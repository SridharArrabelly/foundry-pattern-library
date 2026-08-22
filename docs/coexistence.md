# Coexist with your existing stack — add the agent factory

Most enterprises already run an **AI gateway** (LiteLLM, Azure APIM, or home-grown) and often
**existing agents** on another cloud. Foundry is designed to plug in *beside* what you have —
not replace it. The play is always the same: **keep what works, add Foundry where you have
gaps, and let the pieces interoperate on open protocols (MCP, A2A).**

## The one idea

> A gateway gives you **model access**. Foundry gives you the **agent factory** — the runtime,
> grounding, identity, evaluation, tracing and governance plane *around* the models.

Your gateway stays the front door. Foundry becomes one more provider behind it, and the place
new agentic workloads land for the depth a gateway alone can't give.

## Coexistence patterns (pick per workload)

1. **Foundry behind your gateway** — your gateway (LiteLLM / APIM / your own) fronts every
   provider; new agentic workloads land on Foundry for the eval/governance/identity depth
   (Pattern 1).
2. **Cross-cloud tool calls via MCP** — a Foundry agent invokes a tool hosted on another cloud
   (e.g., an AWS Lambda, see `09-aws-interop/mcp_aws_lambda_server.py`), and vice-versa.
3. **A2A hand-off** — expose an agent on another platform to a Foundry orchestrator (or the
   reverse) over the Agent-to-Agent protocol; each platform owns the agents it's best at.
4. **Governance overlay** — even for workloads hosted elsewhere, route data interactions
   through Purview DSPM for AI so audit / DLP is unified across clouds.

## Two routing planes: your traffic vs Foundry's traffic

If the gateway is your control point, be precise about *what* it actually sees. There are two
independent planes, and covering only the first is the common mistake:

| Plane | Who calls the model | How to route it via APIM |
| --- | --- | --- |
| **Client traffic** | your app / SDK | call the gateway URL instead of the Foundry endpoint (Pattern 1, `gateway_client()`) |
| **Agent traffic** | Foundry, server-side, while running an agent | **BYOM** — a gateway connection + `"<connection>/<model>"` (Pattern 2, `agent_model()`) |

Measured on this library's own APIM: three server-side agent invocations produced **0** gateway
requests; the same three with BYOM produced **3**. Client calls to the direct endpoint, and even
agent runs started from the Foundry portal UI, are also invisible to the gateway.

Enabling the managed **AI Gateway** on a project does *not* close this. It provisions a
per-project APIM product + subscription key and a wildcard API — a quota hook you opt into by
URL and key — but it does not intercept the direct endpoint, which stays open.

> **Routing is not enforcement.** BYOM and the gateway URL route the traffic you *ask* to route.
> The only thing that *prevents* bypass is network isolation — private endpoints plus
> `publicNetworkAccess: Disabled` on the Foundry resource. Do that before claiming containment.

### Setting up BYOM

Create the connection once, then set `AGENT_MODEL_CONNECTION` in `.env`:

```bash
az rest --method put --body @conn.json \
  --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>/connections/apim-mg?api-version=2025-06-01"
```

```jsonc
// conn.json — note: metadata is a flat string map, so "models" is a *serialized* JSON string
{"properties":{
  "category":"ModelGateway",
  "target":"https://<apim>.azure-api.net/<api>/openai/v1",
  "authType":"ApiKey",
  "credentials":{"key":"<apim-subscription-key>"},
  "metadata":{
    "deploymentInPath":"false",
    "models":"[{\"name\":\"gpt-5.4-mini\",\"properties\":{\"model\":{\"name\":\"gpt-5.4-mini\",\"version\":\"\",\"format\":\"OpenAI\"}}}]"
  }}}
```

Gotchas worth knowing up front:

- **Pick the right category.** `ApiManagement` is the better fit — keyless, via the project's
  managed identity — but it requires APIM **Standard v2 or Premium**. On other tiers it is
  accepted at create time and then fails at *inference* time with a misleading
  `Connection '<name>' not found`. `ModelGateway` works on any tier, at the cost of a static key.
- **Prompt agents only**, and tools still work — File Search and function calling both run fine
  through a BYOM model.
- **Allow ~60s to propagate.** Calls made seconds after creating the connection can fail with the
  same "not found" error.



- **Lift the definition, not the plumbing.** A tool contract (OpenAPI / Lambda / action group)
  re-exposes as a Foundry OpenAPI or MCP tool; the agent instructions port directly.
- **Re-point knowledge, don't rebuild it.** Existing knowledge bases → Azure AI Search /
  File Search over the same source documents.
- **Map guardrails, add what's missing.** Existing content filters → Content Safety +
  Prompt Shields; add XPIA / indirect-injection coverage.
- **Wrap first, rewrite later.** Start by calling external tools / agents from Foundry via MCP;
  move the hosting only when the eval / governance / identity benefit is proven in a POC.

## What Foundry adds around any stack

- **Managed agent runtime** — prompt-based or BYO-code hosted agents, both with a first-class
  **Entra Agent ID** (conditional access, M365 tenant identity).
- **Grounding** — Microsoft IQ (Web IQ + Foundry IQ + Work IQ) for world *and* enterprise context.
- **Evaluation** — agent-grade evaluators (groundedness, tool-call accuracy, intent), offline + online.
- **Tracing** — OpenTelemetry to Azure Monitor *or your existing stack* (Datadog, Grafana).
- **Governance & FinOps** — Purview DSPM for AI, plus Agent 365 org-wide inventory and
  ROI-for-agents.

> These are additive. Wherever your agents run today, Foundry can be the identity, evaluation,
> tracing and governance plane around them — connected over MCP and A2A.
