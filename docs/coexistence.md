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

## Migrate only where it earns its keep

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
