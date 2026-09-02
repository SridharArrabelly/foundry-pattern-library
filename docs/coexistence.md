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
   (Pattern 1, `01-ai-gateway-model-access/`).
2. **Cross-cloud tool calls via MCP** — a Foundry agent invokes a selected capability as a
   tool through APIM's REST-backed MCP API.
3. **A2A hand-off** — a Foundry-side client communicates with an independently operating
   agent through APIM's JSON-RPC A2A API and rewritten agent card.
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
- **Tools mostly work — with one known exception.** File Search and function calling both run
  fine through a BYOM model. `memory_search` does not: Foundry rejects it outright with *"The
  following tools are not supported with BYO model: memory_search"*, so Pattern 10 stays direct.
- **Allow ~60s to propagate.** Calls made seconds after creating the connection can fail with the
  same "not found" error.

### What deliberately stays direct

Routing everything through the gateway is not the goal — routing the traffic that *represents
production* is. Two patterns opt out on purpose:

| Pattern | Why it stays on the direct deployment |
| --- | --- |
| **7 — Evaluations** | The judge model is offline QA, not production traffic. Metering it against the same token budget distorts your usage picture and lets a large eval run trip the rate limits that protect live agents. |
| **10 — Memory** | `memory_search` is incompatible with BYOM (above). The store's own extraction/embedding calls are also Foundry-internal machinery and resolve against deployments only. |

Being explicit about the exceptions is what makes the rule credible.

## Governing tools, not just models

An agent calls **tools** as well as models, and tool calls deserve the same control point.
Pattern 3 publishes Web IQ as our own MCP API on APIM, giving the gateway three jobs:

- **authenticate the caller** — `subscriptionRequired: true`, so no APIM key means no access;
- **hold the backend credential** — the Web IQ key lives in a secret named value and is injected
  inbound, so it never reaches a client, a `.env`, or a source file;
- **meter usage** — `rate-limit-by-key` runs *before* the request is proxied upstream.

This is the documented keyless-first exception in the catalog: APIM **Basic v2** does not
provide the higher-tier keyless route used by the model gateway, so the Pattern 3 caller
uses an APIM subscription key. The separate upstream Web IQ credential still lives only in
the APIM secret named value. Higher-tier APIM is not required for this sample.

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

### APIM as a cross-cloud protocol gateway

Pattern 9 extends the tool-control point into two distinct lanes. The distinction is semantic,
not cosmetic:

| Lane | Caller intent | APIM API type | Backend contract |
| --- | --- | --- | --- |
| **MCP** | Invoke a capability as a tool | `type: "mcp"` with `mcpTools` referencing selected operations on a separate REST API | deterministic REST request/response |
| **A2A** | Communicate with an independently operating agent | `type: "a2a"` with agent-card and JSON-RPC mappings | agent card plus `message/send` / task result envelopes |

The Pattern 9 deployment uses API Management API version `2025-09-01-preview`. An MCP API
does not host `ApiOperation` children: `mcpTools[].operationId` points to operations on the
pattern-owned REST API. The A2A API maps `a2aProperties.agentCardBackendUrl` and
`jsonRpcProperties.backendUrl/path` to a genuine adapter runtime.

APIM Basic v2 supports both surfaces. For A2A, APIM replaces the agent-card hostname, sets
JSON-RPC as the preferred transport, removes other interfaces, and adds its subscription-key
requirement. APIM supports only JSON-RPC A2A APIs and cannot deserialize outgoing response
bodies. For MCP, global frontend response payload logging must be zero and no policy may read
`context.Response.Body`.

The sample backend is always labeled **AWS Lambda / Amazon Bedrock (simulated)**. The
Container App, APIM REST/MCP/A2A APIs, protocol clients, and deterministic correlation are
real. This is the credible "wrap first, rewrite later" boundary: replace the simulator only
after a real external-cloud environment, identity design, and evidence plan exist.

### Enterprise grounding with Azure AI Search

Pattern 3 also runs a separate enterprise-grounding leg through the official
`AzureAISearchTool`. This is intentionally distinct from Pattern 2's managed File
Search/vector store:

1. Create or reuse an Azure AI Search connection on the Foundry project.
2. Give the Foundry project's managed identity **Search Index Data Reader** on the Search
   service.
3. Index enterprise content with retrievable text and source fields.
4. Set `AI_SEARCH_CONNECTION_NAME` and `AI_SEARCH_INDEX_NAME`.
5. Run `uv run python 03-microsoft-iq/microsoft_iq.py --leg search`.

The client authenticates to Foundry with `DefaultAzureCredential`; Foundry uses the project
connection to query Search server-side and returns citation annotations. Foundry IQ managed
knowledge bases are a broader layer and are not relabeled as this direct Search tool path.

### Put Web IQ in a Foundry Toolbox without exposing its key

Pattern 12 does not read `WEBIQ_APIM_KEY`. Create a Foundry project connection whose target
is the APIM MCP endpoint and whose server-side credential is the Basic v2 subscription key.
Set `TOOLBOX_WEBIQ_CONNECTION_NAME` and `TOOLBOX_INCLUDE_WEBIQ=true`; the toolbox definition
passes `project_connection_id` to `MCPToolboxTool`. This keeps credential custody in the
project connection while APIM continues to hold the separate upstream Web IQ key.



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
- **Grounding** — Microsoft IQ: Web IQ (live web), Foundry IQ (enterprise knowledge), Fabric IQ
  (business data and KPIs) and Work IQ (M365 org context).
- **Evaluation** — agent-grade evaluators (groundedness, tool-call accuracy, intent), offline + online.
- **Tracing** — OpenTelemetry to Azure Monitor *or your existing stack* (Datadog, Grafana).
- **Governance & FinOps** — Purview DSPM for AI, plus Agent 365 org-wide inventory and
  ROI-for-agents.

> These are additive. Wherever your agents run today, Foundry can be the identity, evaluation,
> tracing and governance plane around them — connected over MCP and A2A.
