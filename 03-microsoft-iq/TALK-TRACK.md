# Pattern 3 — Microsoft IQ: the intelligence layer

**Group:** Agent factory  ·  **Runs 4th of 13** in the hour (minutes 14–19)

**Slide title:** *Grounding that's truly yours — and every tool call governed.*

## The 60-second track
> "An agent is only as good as its context. Microsoft's answer is **Microsoft IQ** — four
> layers of grounding, not a single feature:
> - **Web IQ** — AI-first web grounding, **MCP-native**, ~**2.5× faster** than the next best
>   alternative. This is the one we run live.
> - **Foundry IQ** — enterprise knowledge (policies, contracts, product docs) with retrieval
>   planning over Azure AI Search, instead of hand-wired RAG. Narrated here, not wired.
> - **Fabric IQ** — your business data: KPIs, semantic models and analytics over OneLake. For
>   a bank that's AUM, risk metrics and portfolio performance as *governed* context.
> - **Work IQ** — the M365 graph of work: people, documents, meetings.
>
> Now the part that matters for a regulated shop. An agent doesn't just call **models** — it
> calls **tools**, and most gateways govern only the first. So we published Web IQ as **our
> own MCP API on API Management**. The gateway authenticates the caller, holds the Web IQ key
> as a secret and injects it upstream, and meters every call *before* it proxies.
>
> The client I'm about to run carries **no Web IQ credential at all**. [run `microsoft_iq.py`]
>
> Any cloud can front the web and a vector store. None of them can ground agents in your
> **Microsoft 365 graph of work** or your **Fabric business model**, or ship a single
> MCP-native context plane across all four."

## What's live vs narrated
**Web IQ is the only layer this repo runs.** It's live through the APIM MCP route. Foundry IQ,
Fabric IQ and Work IQ are real product layers and part of the story, but nothing here calls
them — they're drawn dashed on the slide for exactly that reason. If you're asked, say so
plainly: wiring Foundry IQ needs an Azure AI Search index with real content behind it.

## What it beats in a homegrown factory
- **Tool calls governed like model calls** — same gateway, same control point, one audit trail.
- **Key custody in the gateway** — the backend credential never reaches a client or a `.env`.
- **Retrieval planning** across sources (Foundry IQ) instead of hand-wired RAG.
- **Org + business context** (Work IQ, Fabric IQ) — a moat no other platform can match.

## Money line
> "Web IQ grounds you in the world. Foundry IQ grounds you in your enterprise. And every one
> of those tool calls goes through the same gateway as your model calls."

## Demo steps
1. `uv run python 03-microsoft-iq/microsoft_iq.py` — resolves the **APIM MCP route**, opens an
   MCP session, lists the Web IQ tools, and runs one cited regulatory query.
2. Point at the header line: the only credential sent is an **APIM subscription key**.
3. Show the governance evidence (measured on this APIM):

   | Call | Result |
   |------|--------|
   | No subscription key | **401** |
   | Bogus key | **401** |
   | Valid key (client sends no Web IQ key) | **200** — gateway injected the credential |
   | Past the rate limit | **429** |

4. Note the **same governed MCP endpoint** is callable from Foundry, Copilot, or a Bedrock agent.

## Why we hand-built the MCP API
Foundry can auto-publish MCP tools through its managed gateway, but then the policy surface
isn't yours. Hand-authoring the API on APIM gives us the secret named value, the rate-limit
policy and the subscription model — the things a bank's platform team actually needs to own.
Setup (backend + API + policy) is in [`docs/coexistence.md`](../docs/coexistence.md).

**One caveat:** MCP streams over SSE, so a policy must never read `context.Response.Body` —
that forces buffering and breaks the stream. Control is inbound-side (auth, quota,
allow-listing), not response inspection.

## If the MCP call won't connect live
Clear `WEBIQ_MCP_URL` to fall back to the Foundry-managed tool connection
(`WEBIQ_CONNECTION_NAME`), which keeps the key server-side but without our policy layer.
