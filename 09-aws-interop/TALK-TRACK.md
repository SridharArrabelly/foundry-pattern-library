# Pattern 9 — AWS cross-cloud interop (the close)

**Slide title:** *Coexistence: Bedrock runs an agent. Foundry runs the agent factory. MCP/A2A joins them.*

> **This pattern is a slide + code walkthrough** — no AWS access in this environment. The
> mock server proves the wire; the topology slide shows the real thing.

## The 60-second track
> "You're an AWS shop, and nothing here asked you to leave. Watch the coexistence pattern:
> a **Foundry agent** needs a quote from your **AWS-hosted core-banking / pricing engine**.
> It calls it over **MCP** — [run `call_via_mcp.py`, which spawns the mock 'AWS side'] — and
> gets the quote back mid-run. The **wire protocol is identical** whether that tool is local,
> an **AWS Lambda**, or a **Bedrock agent**; when you have AWS you swap in
> `mcp_aws_lambda_server.py` and the agent code doesn't change. The reverse works too:
> **A2A** exposes a Foundry agent to a Bedrock orchestrator.
>
> So the strategy is simple: **keep Bedrock where it's working, add Foundry where you have
> gaps** — the factory: identity, evaluation, tracing, grounding, safety — and let the two
> clouds talk over open protocols. Even for Bedrock-hosted flows, route data through
> **Purview DSPM for AI** so audit and DLP are unified across both."

## Topology (slide)
```
   Foundry agent ──MCP──▶ AWS Lambda / Bedrock tool   (Foundry calls AWS)
   Foundry agent ◀─A2A──  Bedrock orchestrator        (AWS calls Foundry)
        └── governance overlay: Entra Agent ID + Purview DSPM for AI (both clouds) ──┘
```

## Money line
> "Bedrock runs *an agent*. Foundry runs the *agent factory*. MCP and A2A let each cloud do what it's best at."

## Demo steps
1. `uv run python 09-aws-interop/call_via_mcp.py` — Foundry-side client → mock AWS tool over MCP.
2. Show `mcp_aws_lambda_server.py` — the *real* AWS version (boto3 Lambda invoke); "swap this in, agent unchanged."
3. Land the coexistence + migration path (see `../bedrock-vs-foundry.md`).
