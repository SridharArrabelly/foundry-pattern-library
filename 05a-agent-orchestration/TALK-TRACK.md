# Pattern 5A — Agent orchestration (multi-agent coordination)

**Group:** Orchestration & interoperability  ·  **Runs 10th of 16 demos** in the run order

**Slide title:** *Agent orchestration — coordinate specialists with a reusable collaboration pattern.*

## In brief
> "Agent orchestration answers: *how should specialized agents collaborate?* **Microsoft
> Agent Framework** gives you high-level patterns — sequential, concurrent,
> group-chat, handoff, Magentic — without hand-coding a handoff graph. Watch: one client
> request fans out **concurrently** to a **Portfolio Analyst** and a **Compliance Officer**;
> they reason in parallel and return one fan-in result. [run `orchestrator.py`]
>
> This is not a laptop-only graph. The same workflow is wrapped with
> `ResponsesHostServer(workflow.as_agent())`, published as a Foundry hosted agent, and
> exposed through a managed Responses endpoint with its own identity, logs and versions.
>
> But note what Build 2026 also said: *build skills, not agents*. The 50-agent handoff
> mesh is where teams got burned — discovery failures, infinite loops, context pollution.
> So my guidance: **default to one loop + N skills (Pattern 4); reach for multi-agent
> orchestration when you have true parallel specialists or hard org/trust boundaries** —
> like separating an advice engine from an independent compliance check. That separation
> is exactly what this demo shows."

## 5A versus 5B
- **5A coordinates agents.** `ConcurrentBuilder` generates the dispatcher, fan-out,
  participant and default fan-in topology for a known collaboration pattern.
- **5B controls a process.** `WorkflowBuilder` explicitly connects agents and conventional
  code executors with edges, deterministic conditions, checkpoints and audit output.
- Both use the same workflow runtime, but they are different abstraction levels and answer
  different architecture questions.

## What Foundry gives you here
- **Framework-managed orchestration** (concurrent/sequential/handoff/group-chat/Magentic)
  vs. bespoke queue-and-glue code.
- **Open + code-first** (SK/AutoGen lineage), model-portable (BYOM), and it runs on the
  **same gateway** you already operate.
- **Foundry-managed hosting** — a Responses endpoint, managed compute, versions, logs and a
  dedicated Entra Agent ID for the orchestrator.

## Agent Framework vs GitHub Copilot SDK (Build 2026 S43)
- **Agent Framework** — GA, supports skills, **BYOM**, often more token-efficient, *build
  your own harness*.
- **Copilot SDK** — GA, agentic loop + skills, more "OpenClaw-like", fast dev, bigger token use.
- You saw both in skill-forge (Pattern 4). Pick per use case; the skills are portable across both.

## The one-liner
> "Orchestrate specialists when the problem is genuinely parallel — not because a framework let you."

## Running it
1. `uv run python 05a-agent-orchestration/orchestrator.py`
2. Show the analyst's risk view and the compliance officer's **BLOCK** with the 70%-equities rule.
3. `cd 05a-agent-orchestration/hosted; azd ai agent run`, then invoke locally with
   `azd ai agent invoke --local "<request>"`.
4. After deployment, invoke `multi-agent-orchestrator-hosted` through its Foundry-managed endpoint.
5. Segue to 5B: "Now make the process—not the agents—own routing and control."
