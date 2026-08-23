# Pattern 7 — Evaluation → optimization

**Group:** Operate & optimise  ·  **Runs 11th of 13** in the run order

**Slide title:** *Evaluation is the starting point — then hill-climb quality, cost and latency.*

## In brief
> "A gateway can't tell you if your agent is *right*. Foundry's **Evaluation SDK** scores
> agents on **groundedness, relevance, coherence** — and agent-grade metrics like
> **intent resolution** and **tool-call accuracy**. Here's a golden set for our RM
> assistant. [run `run_eval.py`] Watch **row 3** — I planted a wrong answer that says a
> 90%-equities Conservative portfolio is *suitable*. Groundedness collapses, the **CI gate
> exits non-zero, and the merge is blocked**. That's a regression caught before a client
> ever sees it.
>
> Build 2026's framing: **evaluation is the starting point, not the finish line**. From
> these signals you **hill-climb** across quality, cost and latency — and Foundry now ships
> **Agent Optimizer** and **Rubric Evaluators** that auto-generate criteria and improve
> agents continuously in production. Without an eval harness and a gate, none of that is possible."

## What Foundry gives you here
- **Agent-grade evaluators** (groundedness, tool-call accuracy, intent) — offline *and* online.
- **CI gate** on every PR (`eval-ci.yml`) — quality regressions can't merge.
- **Continuous optimization** (Agent Optimizer, Rubric Evaluators) — eval → hill-climb.

## The one-liner
> "If you can't score it, you can't ship it safely — and you certainly can't optimize it."

## Running it
1. `uv run python 07-evaluations/run_eval.py` → judges run in **Foundry cloud**; terminal
   prints the scorecard: groundedness **4 passed / 1 failed** (the planted wrong row) →
   **GATE FAILED** (exit 1).
2. Open the printed **report URL** (or Foundry → **Evaluations → private-banking-suitability**)
   → the same run with per-row scores and the judge's reasoning.
3. Show the non-zero exit → point at `eval-ci.yml` (the merge-blocking gate, OIDC, no secrets).
4. Mention online eval + Agent Optimizer for the production hill-climb.

## Why the judges don't run through the gateway
Expect this question. The judge model stays on the **direct deployment**, not the APIM/BYOM
route used by Patterns 2 and 6 — deliberately. Evaluation is offline QA, not production
traffic: metering it against the same gateway budget distorts the usage picture, and a large
eval run could trip the very rate limits that protect live agents. Route what represents
production; keep test harnesses off that meter. See [`docs/coexistence.md`](../docs/coexistence.md).

## Both places
The run scores in **Foundry's cloud evaluation service** (`openai_client.evals.create` +
`evals.runs.create`) — the same API the portal Evaluations tab is built on. So the run appears
**in the terminal** (scorecard + CI exit code) **and in the Foundry portal** Evaluations tab
(per-row groundedness / relevance / coherence + reasoning). Judges run server-side (no local
compute); auth is keyless (Entra via `DefaultAzureCredential`, pre-warmed with a longer CLI
timeout so the token calls don't flake).


