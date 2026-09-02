# Pattern 9 — Cross-cloud protocol gateway (APIM + MCP + A2A)

**Group:** Orchestration & interoperability  ·  **Runs 12th of 16 demos** in the run order

**Slide title:** *Cross-cloud protocol gateway: one APIM boundary, two distinct lanes.*

> **AWS Lambda and Amazon Bedrock are simulated.** The Container App, APIM REST API,
> REST-backed MCP API, APIM A2A API, agent card, JSON-RPC runtime, and optional Foundry
> prompt-agent MCP invocation are real when deployed. Never present simulated AWS as live.

## In brief
> "This is one cross-cloud gateway with two intentionally different lanes. In lane one, a
> **Foundry prompt agent invokes a capability as an MCP tool**. APIM exposes selected REST
> operations as `mcpTools`, then routes `create_quote` to a deterministic simulated
> Lambda-style backend.
>
> In lane two, a **Foundry-side A2A client communicates with an independently operating
> agent**. APIM mediates JSON-RPC, publishes a rewritten agent card, and routes to a genuine
> task/artifact runtime that simulates the Bedrock side. A2A is not a plain Bedrock Agent API
> with a new label.
>
> Both lanes return the same deterministic quote correlation, but their semantics stay
> separate: MCP invokes a tool; A2A messages an agent. APIM supplies one access, rate-limit,
> and observability boundary across clouds."

## Topology (slide)
```text
   Foundry prompt agent -> APIM MCP API -> REST create_quote -> simulated Lambda tool
   Foundry-side client  -> APIM A2A API -> JSON-RPC runtime -> simulated Bedrock agent
```

## The one-liner
> "MCP invokes a capability. A2A communicates with an agent. APIM governs both without pretending they are the same protocol."

## Running it
1. Run `verify_live.py` and show one correlation ID across real APIM REST, MCP, and A2A.
2. Run `foundry_mcp_agent.py` only when the Foundry project connection exists; show the
   actual `mcp_call` item, not only natural-language output.
3. Point to every `simulation: true` and `AWS Lambda / Amazon Bedrock (simulated)` label.
4. Finish with the ownership preflight, response-body logging zero rule, cost cap, and cleanup.

## Accuracy guardrails

- APIM MCP management uses `2025-09-01-preview`; the MCP API has no operation children.
  Its `mcpTools` reference operations on the separate REST API.
- MCP policies never access `context.Response.Body`; deployment fails if a global diagnostic
  logs frontend response payload bytes.
- APIM A2A is JSON-RPC only. APIM rewrites hostname, preferred transport, interfaces, and
  subscription-key requirements in the agent card. Outgoing response deserialization is not
  supported.
- `mcp_aws_lambda_server.py` is optional future work, not live evidence.
