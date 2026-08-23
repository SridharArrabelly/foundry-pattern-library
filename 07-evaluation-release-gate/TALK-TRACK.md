# Pattern 7 — Evaluation & release gate

**Group:** Lifecycle, assurance & operations  ·  **Runs 10th of 12** in the run order

**Slide title:** *Evaluation is the starting point — then hill-climb quality, cost and latency.*

## In brief
> "A gateway can't tell you if your agent is *right*. Foundry's **Evaluation SDK** scores
> agents on **groundedness, relevance, coherence** — and agent-grade metrics like
> **intent resolution** and **tool-call accuracy**. The checked-in golden set contains
> questions and policy context, not pre-written answers. [run `run_eval.py`] The candidate
> prompt and model answer every row first; those generated answers are what Foundry judges.
> A change to the version-controlled candidate instructions, configured model, evaluation
> fixtures, or this gate's direct dependencies can therefore move the score and block that PR.
>
> For the live failure move, add `--demo-failure`. Only that explicit mode replaces one
> generated answer with the wrong claim that 90% equities is suitable for a Conservative
> client. CI never enables the planted regression.
>
> Build 2026's framing: **evaluation is the starting point, not the finish line**. From
> these signals you **hill-climb** across quality, cost and latency — and Foundry now ships
> **Agent Optimizer** and **Rubric Evaluators** that auto-generate criteria and improve
> agents continuously in production. Without an eval harness and a gate, none of that is possible."

## What Foundry gives you here
- **Agent-grade evaluators** (groundedness, tool-call accuracy, intent) — offline *and* online.
- **Scoped CI gate** (`eval-ci.yml`) — runs when this sample's candidate/gate inputs or direct
  dependencies change, so relevant quality regressions can't merge.
- **Continuous optimization** (Agent Optimizer, Rubric Evaluators) — eval → hill-climb.

## The one-liner
> "If you can't score it, you can't ship it safely — and you certainly can't optimize it."

## Running it
1. `uv run python 07-evaluation-release-gate/run_eval.py` → generates current candidate
   answers, judges them in **Foundry cloud**, and can pass the release gate.
2. `uv run python 07-evaluation-release-gate/run_eval.py --demo-failure` → plants one
   wrong answer after generation so the gate exits non-zero for the walkthrough.
3. Open the printed **report URL** (or Foundry → **Evaluations →
   private-banking-release-gate**) → the same run with per-row scores and reasoning.
4. Point at `eval-ci.yml`: default mode, OIDC, no stored model key, fail-closed checks.
5. Mention online eval + Agent Optimizer for the production hill-climb.

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
(per-row groundedness / relevance / coherence + reasoning). Candidate generation and judges
both use the configured Foundry model; auth is keyless via `DefaultAzureCredential`.
Unsuccessful runs, errored rows, missing metrics and any failed required metric all fail closed.
