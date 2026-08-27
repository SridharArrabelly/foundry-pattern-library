# Pattern 5B — Workflow orchestration (graph-based pipeline)

**Group:** Orchestration & interoperability  ·  **Runs 11th of 16 demos** in the run order

**Slide title:** *Workflow orchestration — make the process own routing, state and control.*

## In brief
> "5A asked how agents collaborate. 5B asks a different question: *how does the
> application enforce an inspectable enterprise process that happens to contain agents?*
>
> This `WorkflowBuilder` graph starts with ordinary validation code, invokes an agent only
> to classify semantic risk, then applies deterministic application policy. A switch edge
> sends routine test work to a code processor and production, high-impact or uncertain
> work to an exception-review agent. Both paths terminate in one code-owned audit record.
> Invalid agent output follows the default **fail-closed** edge.
>
> The same input is resumed from a file checkpoint. The audit ID, route and decision stay
> identical because the process—not an LLM—owns control flow."

## 5A versus 5B
| | **5A — Agent orchestration** | **5B — Workflow orchestration** |
|---|---|---|
| Primary abstraction | collaborating agents | explicit process graph |
| Builder | `ConcurrentBuilder` | `WorkflowBuilder` |
| Nodes | agents | agents + conventional executors |
| Routing | selected orchestration pattern | edges, conditions and default branch |
| State | agent/workflow runtime | checkpointed superstep state |
| Best for | parallel specialists, handoffs, group collaboration | controlled enterprise pipelines |

The orchestration builders internally generate workflows, but that does not erase the
architectural distinction: 5A consumes a high-level collaboration pattern; 5B defines the
graph directly.

## What the implementation proves
- **Mixed nodes** — validation, policy normalization, branch processing and audit are
  conventional code; semantic classification and exception review are agents.
- **Deterministic routing** — production, deployment rotation, impact over 100 users, and
  non-low agent risk route to the exception branch.
- **Fail-closed default** — malformed classifier output never reaches the standard path.
- **Typed contracts** — strict Pydantic messages cross executor boundaries.
- **Durable local resume** — `FileCheckpointStorage` captures superstep checkpoints; an
  entry checkpoint replays to the same audit identity and decision.
- **Workflow as agent** — the hosted variant exposes `workflow.as_agent()` through the
  Responses protocol.

## Running it
1. `uv run python 05b-workflow-orchestration/run_workflow.py`
2. Point at the standard path: code validation → classifier agent → deterministic
   standard edge → code processor → audit.
3. Point at the exception path: the production/impact rules override a low-risk model
   answer, invoke the reviewer agent, and require external human approval rather than
   executing a consequential action.
4. Show the checkpoint line: resume preserves route, decision and audit ID.
5. To retain local checkpoints, pass
   `--checkpoint-dir <trusted-private-directory>`. Never load untrusted checkpoint files.
6. Hosted:
   `cd 05b-workflow-orchestration/hosted; azd ai agent run`, then invoke with a JSON
   change request as the user message.

## Production boundaries
- `FileCheckpointStorage` is for trusted, single-machine development and uses restricted
  pickle deserialization. Secure its directory and allowlisted types.
- Distributed production resume can use the separate Cosmos checkpoint provider with
  managed identity/RBAC where its current pre-release package status is acceptable; that
  provider is not provisioned by this sample.
- The reviewer agent recommends controls; it cannot authorize or execute the change.
  Pattern 13 is the separate human-approval control for consequential actions.
- Conditions and policy overrides are code-reviewed logic. An agent does not choose which
  edge exists or bypass the default branch.

## Live verification (2026-08-27)
Verified through a real Foundry `gpt-5.4-mini` client:

- the routine test restart followed the standard code branch and returned `APPROVED`;
- the production deployment rotation followed the exception branch and returned
  `REVIEW_REQUIRED` with `human-approval-required`;
- each run created superstep checkpoints, and resuming its entry checkpoint reproduced
  the same route, decision and deterministic audit ID.

The hosted `workflow.as_agent()` packaging is implemented and validated locally, but a
new hosted-agent version was not deployed for this addition.

## The one-liner
> "Agents contribute judgment. The workflow owns the process."

## Official references
- <https://learn.microsoft.com/agent-framework/concepts/workflows/>
- <https://learn.microsoft.com/agent-framework/concepts/workflows/builder-and-execution>
- <https://learn.microsoft.com/agent-framework/concepts/workflows/edges>
- <https://learn.microsoft.com/agent-framework/workflows/checkpoints>
- <https://learn.microsoft.com/agent-framework/workflows/agents-in-workflows>
