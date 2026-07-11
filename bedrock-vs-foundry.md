# Bedrock Agents ↔ Foundry Agent Service — coexist & elevate

You already use **Amazon Bedrock Agents**. So **do not think** this as "rip out Bedrock."
I wold say: *keep Bedrock where it's working, add Foundry where you have gaps, and
let the two interoperate on open protocols.*

## Capability map (honest)

| Capability | Bedrock Agents | Foundry Agent Service | Where Foundry pulls ahead |
|---|---|---|---|
| Hosted agent runtime + memory | Yes | Yes | — |
| Tool / action groups | Action groups (Lambda, OpenAPI) | Functions, OpenAPI, Logic Apps, **MCP** | Native MCP + broader connectors |
| Knowledge / RAG | Knowledge Bases | File Search + Azure AI Search | Managed vector store *and* enterprise search |
| Multi-agent | Multi-agent collaboration | Connected Agents + **Agent Framework (SK+AutoGen)** | Open-source, code-first + declarative |
| Guardrails | Bedrock Guardrails | Content Safety + **Prompt Shields (direct + XPIA)** | Indirect-injection defence, per deployment |
| Agent identity | IAM role | **Entra Agent ID** | First-class non-human identity, conditional access |
| Data governance | — (bring your own) | **Microsoft Purview** (DLP, DSPM for AI, audit) | Unified data governance plane |
| Eval | Model eval (limited agent eval) | **Evaluation SDK**: groundedness, tool-call accuracy, intent, online eval | Agent-grade, offline + continuous |
| Tracing | CloudWatch / X-Ray | **OpenTelemetry** → Azure Monitor **or your stack** | Open standard, portable |

## Coexistence patterns (pick per workload)

1. **Foundry behind your gateway, Bedrock stays** — LiteLLM fronts both; new agentic
   workloads land on Foundry for the eval/governance depth (Pattern 1).
2. **Cross-cloud tool calls via MCP** — a Foundry agent invokes a Bedrock-fronted tool
   or a Lambda (see `09-aws-interop/mcp_aws_lambda_server.py`), and vice-versa.
3. **A2A hand-off** — expose a Bedrock agent to a Foundry orchestrator (or the reverse)
   over the Agent-to-Agent protocol; each cloud owns the agents it's best at.
4. **Governance overlay** — even for Bedrock-hosted flows, route data interactions
   through Purview DSPM for AI so audit/DLP is unified across both clouds.

## Migration path (only where it earns its keep)
- **Lift the definition, not the plumbing.** A Bedrock action group is an OpenAPI/Lambda
  contract — re-expose it as a Foundry OpenAPI or MCP tool; the agent instructions port directly.
- **Knowledge Bases → AI Search / File Search.** Re-point the same source documents.
- **Guardrails → Content Safety + Prompt Shields.** Map categories; add XPIA coverage they lack.
- **Wrap, don't rewrite, first.** Start by calling Bedrock tools from Foundry via MCP; migrate
  the hosting only when the eval/governance/identity benefit is proven in the POC.

## One-liner for the room
> "Bedrock Agents runs your agent. Foundry runs your **agent factory** — the identity,
> evaluation, tracing and data-governance plane around it — and the two talk over MCP and A2A."
