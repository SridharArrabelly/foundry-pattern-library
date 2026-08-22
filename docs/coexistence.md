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

The second plane is the one teams miss. When Foundry runs an agent, *Foundry* calls the model —
your client is not in the loop, so pointing your SDK at the gateway has no bearing on that call.
BYOM is the supported way to pin it: register the gateway as a model connection and reference the
model as `"<connection>/<model>"`. Verified on this library's APIM — three agent invocations
through a BYOM model produced three gateway requests, with File Search and function calling
working normally through it.

Enabling the managed **AI Gateway** on a project provisions a per-project APIM product,
subscription key, and gateway URL — the quota hook your clients and connections point at.

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

## Governing tools, not just models

An agent calls **tools** as well as models, and tool calls deserve the same control point.
Pattern 3 publishes Web IQ as our own MCP API on APIM, giving the gateway three jobs:

- **authenticate the caller** — `subscriptionRequired: true`, so no APIM key means no access;
- **hold the backend credential** — the Web IQ key lives in a secret named value and is injected
  inbound, so it never reaches a client, a `.env`, or a source file;
- **meter usage** — `rate-limit-by-key` runs *before* the request is proxied upstream.

Measured on this APIM: no key → **401**, bogus key → **401**, valid key → **200** (credential added
by the gateway), calls past the limit → **429**.

Two things that cost time:

- **An MCP API needs a `backendId`, not just `serviceUrl`.** With only `serviceUrl`, APIM answers
  the MCP handshake itself and `tools/list` comes back **empty**. Point a backend entity at the
  upstream MCP URL and set `backendId` — then the real tool list passes through.
- **MCP routes are invisible at older api-versions.** Use `2025-09-01-preview`; `2024-05-01` omits
  `type: mcp` APIs entirely, so they look like they don't exist.

> **Never read `context.Response.Body` in an MCP policy.** MCP streams over SSE, and touching the
> response body forces buffering, which breaks the stream. Control is inbound-side — auth, quota,
> tool allow-listing — not response inspection.

```bash
# 1) the upstream MCP server, as a backend entity
az rest --method put --url ".../backends/webiq-mcp-backend?api-version=2025-09-01-preview" \
  --body '{"properties":{"url":"https://api.microsoft.ai/v3/mcp","protocol":"http"}}'

# 2) the MCP API itself (note backendId + subscriptionRequired)
az rest --method put --url ".../apis/webiq-mcp?api-version=2025-09-01-preview" --body '{"properties":{
  "displayName":"WebIQ MCP (governed)","path":"webiq-mcp","protocols":["https"],
  "serviceUrl":"https://api.microsoft.ai/v3/mcp","backendId":"webiq-mcp-backend",
  "subscriptionRequired":true,"type":"mcp",
  "mcpProperties":{"endpoints":{"mcp":{"uriTemplate":"/mcp"}},"isFederationRouter":false}}}'

# 3) the upstream key, held by the gateway as a secret
az rest --method put --url ".../namedValues/webiq-api-key?api-version=2025-09-01-preview" \
  --body '{"properties":{"displayName":"webiq-api-key","value":"<web-iq-key>","secret":true}}'
```

The policy that does the work:

```xml
<inbound>
  <base />
  <!-- callers never hold the upstream credential -->
  <set-header name="x-apikey" exists-action="delete" />
  <set-header name="x-apikey" exists-action="override"><value>{{webiq-api-key}}</value></set-header>
  <rate-limit-by-key calls="5" renewal-period="60"
    counter-key="@(context.Subscription?.Id ?? context.Request.IpAddress)" />
</inbound>
```



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
