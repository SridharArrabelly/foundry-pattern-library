# Pattern 5 — Multi-agent orchestration (Agent Framework)

**Group:** Orchestration & interop  ·  **Runs 8th of 12** in the run order

**Slide title:** *Multi-agent — when it earns its keep. Orchestrate specialists, don't hand-wire them.*

## In brief
> "You asked about multi-agent orchestration — here's the honest version. **Microsoft
> Agent Framework** (GA) gives you orchestration primitives — sequential, concurrent,
> group-chat, handoff, Magentic — without hand-coding a handoff graph. Watch: one client
> request fans out **concurrently** to a **Portfolio Analyst** and a **Compliance Officer**;
> they reason in parallel and I aggregate. [run `orchestrator.py`]
>
> But note what Build 2026 also said: *build skills, not agents*. The 50-agent handoff
> mesh is where teams got burned — discovery failures, infinite loops, context pollution.
> So my guidance: **default to one loop + N skills (Pattern 4); reach for multi-agent
> orchestration when you have true parallel specialists or hard org/trust boundaries** —
> like separating an advice engine from an independent compliance check. That separation
> is exactly what this demo shows."

## What Foundry gives you here
- **Framework-managed orchestration** (concurrent/sequential/handoff/group-chat/Magentic)
  vs. bespoke queue-and-glue code.
- **Open + code-first** (SK/AutoGen lineage), model-portable (BYOM), and it runs on the
  **same gateway** you already operate.

## Agent Framework vs GitHub Copilot SDK (Build 2026 S43)
- **Agent Framework** — GA, supports skills, **BYOM**, often more token-efficient, *build
  your own harness*.
- **Copilot SDK** — GA, agentic loop + skills, more "OpenClaw-like", fast dev, bigger token use.
- You saw both in skill-forge (Pattern 4). Pick per use case; the skills are portable across both.

## The one-liner
> "Orchestrate specialists when the problem is genuinely parallel — not because a framework let you."

## Running it
1. `uv run python 05-multi-agent/orchestrator.py`
2. Show the analyst's risk view and the compliance officer's **BLOCK** with the 70%-equities rule.
3. Segue to Pattern 9: "now let's make those specialists reach across clouds."
