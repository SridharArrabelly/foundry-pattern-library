# Pattern 3 — Microsoft IQ — the intelligence layer

**Group:** Agent construction & knowledge  ·  **Runs 4th of 12** in the run order

**Slide title:** *Grounding that's truly yours — and every tool call governed.*

## In brief
> "An agent is only as good as its context. Microsoft's answer is **Microsoft IQ** — four
> layers of grounding, not a single feature:
> - **Web IQ** — AI-first web grounding, **MCP-native**. We run it live through APIM.
> - **Foundry IQ** — enterprise knowledge (policies, contracts, product docs) with retrieval
>   planning over Azure AI Search. The managed knowledge-base layer is narrated here.
> - **Fabric IQ** — your business data: KPIs, semantic models and analytics over OneLake. For
>   a bank that's AUM, risk metrics and portfolio performance as *governed* context.
> - **Work IQ** — the M365 graph of work: people, documents, meetings.
>
> Now the part that matters for a regulated shop. An agent doesn't just call **models** — it
> calls **tools**, and most gateways govern only the first. So we published Web IQ as **our
> own MCP API on API Management**. The gateway authenticates the caller, holds the Web IQ key
> as a secret and injects it upstream, and meters every call *before* it proxies.
>
> The client I'm about to run carries an **APIM Basic v2 subscription key**, not the upstream
> Web IQ credential. Then the same script creates a Foundry agent with the official
> `AzureAISearchTool` and returns a policy answer with citations. [run `microsoft_iq.py`]
>
> Any cloud can front the web and a vector store. None of them can ground agents in your
> **Microsoft 365 graph of work** or your **Fabric business model**, or ship a single
> MCP-native context plane across all four."

## What's live vs narrated
**Web IQ and Azure AI Search are live.** Web IQ uses the APIM MCP route. Enterprise policy
grounding uses an actual Azure AI Search project connection and index through
`AzureAISearchTool`; this is not Pattern 2's managed File Search/vector store under another
name. Foundry IQ managed knowledge bases, Fabric IQ and Work IQ are broader product layers,
remain narrated, and are drawn dotted on the slide.

## What Foundry gives you here
- **Tool calls governed like model calls** — same gateway, same control point, one audit trail.
- **Key custody in the gateway** — the upstream Web IQ credential never reaches the client.
- **Enterprise Search grounding** — an official Foundry agent tool over an existing Search
  connection and index, authenticated keylessly to the project.
- **Retrieval planning** across sources (Foundry IQ) instead of hand-wired RAG.
- **Org + business context** (Work IQ, Fabric IQ) — grounded in your tenant and your data model.

## The one-liner
> "Web IQ grounds you in the world. Foundry IQ grounds you in your enterprise. And every one
> of those tool calls goes through the same gateway as your model calls."

## Running it
1. `uv run python 03-microsoft-iq/microsoft_iq.py` runs both legs.
2. **Web IQ:** resolves the **APIM MCP route**, lists tools, and runs one cited regulatory
   query. The only caller credential is the documented **APIM Basic v2 subscription key**.
3. Show the governance evidence (measured on this APIM):

   | Call | Result |
   |------|--------|
   | No subscription key | **401** |
   | Bogus key | **401** |
   | Valid key (client sends no Web IQ key) | **200** — gateway injected the credential |
   | Past the rate limit | **429** |

4. **Azure AI Search:** creates a versioned Foundry prompt agent with `AzureAISearchTool`,
   queries `AI_SEARCH_INDEX_NAME`, and prints the answer plus citation annotations.
5. Note the **same governed MCP endpoint** is callable from Foundry, Copilot, or a Bedrock agent.

## Why we hand-built the MCP API
Foundry can auto-publish MCP tools through its managed gateway, but then the policy surface
isn't yours. Hand-authoring the API on APIM gives us the secret named value, the rate-limit
policy and the subscription model — the things a bank's platform team actually needs to own.
Setup (backend + API + policy) is in [`docs/coexistence.md`](../docs/coexistence.md).

**One caveat:** MCP streams over SSE, so a policy must never read `context.Response.Body` —
that forces buffering and breaks the stream. Control is inbound-side (auth, quota,
allow-listing), not response inspection.

## Configuration prerequisites
Set `AI_SEARCH_CONNECTION_NAME` to a Foundry project connection of type Azure AI Search and
`AI_SEARCH_INDEX_NAME` to an existing index. The Foundry project managed identity needs the
**Search Index Data Reader** role on the Search service.

## If the MCP call won't connect live
Confirm `WEBIQ_MCP_URL` points at the hand-authored APIM MCP API base path and
`WEBIQ_APIM_KEY` is an active Basic v2 subscription key. Do not use the auto-generated
Foundry tool route here; Pattern 3 is specifically proving the policy-owned APIM route.
