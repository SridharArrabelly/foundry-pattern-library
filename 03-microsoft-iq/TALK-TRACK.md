# Pattern 3 — Microsoft IQ (Web IQ + Foundry IQ)

**Slide title:** *Grounding that's truly yours — the live web AND your enterprise, over MCP.*

## The 60-second track
> "An agent is only as good as its context. Microsoft's answer is the **IQ platform** —
> a context layer, not a single feature:
> - **Web IQ** — AI-first web grounding, **MCP-native**, ~**2.5× faster** than the next
>   best alternative. It's registered as a **governed MCP tool connection** in Foundry
>   ("WebIQ-MCP-1") — the key stays server-side, traffic rides the **AI Gateway**. Watch a
>   client pull live, cited regulatory context — **keyless** (the app reads the connection
>   at runtime via Entra, no secret in code). [run `web_iq_mcp.py`]
> - **Foundry IQ** — retrieval planning across **enterprise + web**. Here it's **Azure AI
>   Search** over our wealth product/benefits corpus, exposed as the `rag-search` skill in
>   skill-forge. [switch to skill-forge, ask a product question → cited enterprise answer]
> - (And **Work IQ** — the M365 graph of work — for when the agent needs to know *how your
>   org actually operates*: people, docs, meetings. APIs GA.)
>
> AWS can front the web and a vector store. It cannot ground agents in **your Microsoft 365
> graph of work**, and it doesn't ship a single MCP-native context plane across web +
> enterprise."

## What it beats in a homegrown factory
- **MCP-native grounding** — one governed endpoint, any agent/model calls it.
- **Retrieval planning** across sources (Foundry IQ) instead of hand-wired RAG.
- **Org-context (Work IQ)** — a moat AWS structurally can't match.

## Money line
> "Web IQ grounds you in the world. Foundry IQ grounds you in your enterprise. Work IQ
> grounds you in how your company actually works."

## Demo steps
1. **Web IQ:** `uv run python 03-microsoft-iq/web_iq_mcp.py` — the script reads the governed
   **WebIQ-MCP-1** tool connection from Foundry (keyless), opens an MCP session through the
   AI Gateway, lists the Web IQ tools, and runs one cited web query.
2. **Foundry IQ:** in `../skill-forge`, `uv run skill-forge`, pick the `rag-search` skill,
   ask a product question → cited answer from Azure AI Search.
3. Note the **same governed MCP tool** is callable from Foundry, Copilot, or a Bedrock agent.

## Why it's registered in Foundry (not hand-wired)
The MCP endpoint + key live once in the Foundry **tool connection** (Tools > WebIQ-MCP-1),
governed by the AI Gateway. Apps never hold the key — they read it at runtime with their
Entra identity. That's the bank-friendly story: one governed grounding endpoint, keyless.

## If the MCP call won't connect live
Fall back to skill-forge's **web-grounding** skill (WebIQ SDK) — same Web IQ, no MCP
transport to debug on stage.
