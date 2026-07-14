# Pattern 12 — Agent 365 & ROI

**Slide title:** *Every agent is an identity you can govern and a cost you can justify.*

## The 60-second track
> "This is the leadership money slide. Two questions a homegrown factory can't
> answer: *what agents do we have* and *are they worth it?* **Agent 365** answers
> the first — org-wide inventory, an **Entra Agent ID** per agent, and policy across
> all of them, in the portal. The second is FinOps: [run it] we run a batch of real
> RM tasks, capture **tokens + outcome** per run, and print a **cost ↔ value ↔ ROI**
> table. Projected to 5,000 tasks a month: spend a few dollars, deliver tens of
> thousands in RM time saved. Same signal 'ROI for agents' surfaces from the traces
> you already emit (Pattern 6) — grouped by agent and **version**, so you can prove a
> new version is cheaper *and* better before you ship it."

## What it beats in a homegrown factory
- **Inventory + identity** — Agent 365 + Entra Agent ID; no spreadsheet of agents.
- **Cost tied to outcomes** — per agent, per version; a CFO-legible number.
- **From telemetry you already have** — same OTel traces as Pattern 6, no new pipe.

## Money line
> "You can inventory every agent as an identity and tie its cost to an outcome —
> governance and FinOps in one plane. Your factory can't show a CFO either."

## Demo steps
1. `uv run python 12-agent365-roi/agent_roi_report.py`.
2. 5 RM tasks run through the gateway → per-task tokens + a completion check.
3. Read the table: runs / done / tokens / cost$ / value$ / ROI, then the **monthly
   projection** (spend vs. value vs. net).
4. Tie it back: these runs are traced to App Insights (Pattern 6); in production you
   group by `gen_ai.agent.name` / version in KQL. Agent 365 adds the org-wide plane.

## Portal / KQL appendix (the org-wide half)
`ROI for agents` and `Agent 365` are 2026-new (private preview / admin-driven). The
same numbers come from traces:

```kusto
dependencies
| where timestamp > ago(7d)
| where customDimensions has 'gen_ai.usage.total_tokens'
| extend agent = tostring(customDimensions['gen_ai.agent.name']),
         ver   = tostring(customDimensions['gen_ai.agent.version']),
         tokens = toint(customDimensions['gen_ai.usage.total_tokens']),
         ok = iff(success == true, 1, 0)
| summarize runs=count(), completed=sum(ok), tokens=sum(tokens) by agent, ver
```

## Grounding
Runs keyless through the gateway (`gateway_client()`), Responses/Chat usage payload
for token counts. Cost/value assumptions are env-driven
(`USD_PER_1K_TOKENS`, `USD_VALUE_PER_TASK`, `MONTHLY_TASK_VOLUME`). Agent 365 / ROI
for agents are preview/portal — this is the engineer-facing equivalent you can run
today.
