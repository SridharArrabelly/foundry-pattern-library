# Pattern 13 — Human approval for consequential tool actions

**Group:** Platform foundation & governance  ·  **Runs 3rd of 15** in the run order

**Slide title:** *Pause the exact tool call — approve intent, then enforce it downstream.*

## In brief
> "Human approval belongs at the moment an agent is about to cause a consequential
> side effect. This prompt agent has two MCP tools. `get_change_request` is read-only and
> runs without interruption. `schedule_change` is configured with
> `require_approval=always`, so the Responses API returns an `mcp_approval_request`
> instead of calling the service.
>
> The operator sees the exact normalized tool name and arguments. Reject records the
> decision and schedules nothing. Approve sends an explicit `McpApprovalResponse`, then
> Foundry invokes the tool. The service still checks downstream authorization, expiry,
> argument equality and idempotency. Approval is a runtime control — it is not a
> substitute for authorization."

## What is implemented
- **Current Foundry flow** — a versioned prompt agent with `MCPTool`,
  selective `require_approval`, Responses `mcp_approval_request`, and
  `McpApprovalResponse`.
- **Two-tool MCP service** — read-only `get_change_request`; consequential
  `schedule_change`.
- **Fail-closed state machine** — malformed, missing, stale or mismatched approvals fail;
  duplicate decision/approval IDs cannot be changed on replay.
- **Exactly-once effect** — request, Foundry approval-request, operator decision and
  deterministic side-effect IDs are retained in one audit view.
- **Remote deployment** — Azure Container Apps with HTTPS ingress, scale-to-zero,
  one replica and Azure Files. SQLite is explicitly demo-only.

## Running it
1. Deploy the service using [`infra/README.md`](infra/README.md), then create a Foundry
   project connection that stores the endpoint and `x-mcp-api-key`.
2. Set `MCP_CHANGE_CONTROL_URL`, `MCP_CHANGE_CONTROL_CONNECTION_NAME`, and the
   operator-only `MCP_CHANGE_CONTROL_API_KEY` in the current process.
3. Reject path:
   `uv run python 13-human-approval/run_approval_demo.py --change-request CRQ-1002`,
   inspect the call, type `REJECT`, and show **zero** side effects.
4. Approve + replay path:
   `uv run python 13-human-approval/run_approval_demo.py --change-request CRQ-1003`,
   inspect the call, type `APPROVE`, and show one correlated side effect. The script
   replays the same decision and tool arguments; the count remains one.
5. Run local negative-path tests:
   `uv run python -m unittest tests.test_human_approval`.

The main demo has no auto-approve flag. A Foundry prompt agent cannot call localhost;
local tests validate the service and state machine but are not presented as cloud
approval evidence.

## Security and correctness boundaries
- The project connection, not the agent definition, holds the MCP credential.
- The service never prints or persists bearer tokens or the MCP key.
- A human approves the exact normalized call, not a natural-language summary.
- An approval expires after five minutes and authorizes only exact reviewed arguments.
- The MCP key is downstream authentication. Production authorization should also check
  workload identity, tenant, change ownership and policy in the system of record.
- The SQLite store and single-replica limit are for deterministic demonstration only.

## The one-liner
> "Pause before consequence, show the exact call, and still enforce authorization at the tool."
