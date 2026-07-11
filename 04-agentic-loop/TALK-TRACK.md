# Pattern 4 — Agentic Loop: "Build Skills, Not Agents"

**Slide title:** *One agent. One loop (Plan → Act → Observe). N swappable skills.*

**Runs from `../skill-forge`** — a from-scratch implementation of the exact pattern
Microsoft shipped at Build 2026 (the Agentic Loop + Skills-as-folders).

## The 60-second track
> "The industry spent 2025 building **50+ specialised agents with handoffs** — and hit
> discovery failures, infinite loops, context pollution and un-maintainable graphs. Build
> 2026's guidance is the opposite: **build skills, not agents**. One agent, one **agentic
> loop** — Reason & gather context → Act (call a skill or MCP) → Observe & decide, looping
> until done — and **N domain skills** the loop chooses between by reasoning.
>
> A **skill is just a folder**: a `SKILL.md` with ~50-token metadata the agent discovers,
> full instructions loaded **on demand**, and optional scripts run at runtime — *progressive
> disclosure*. Watch skill-forge: same two skills — `rag-search` and `web-grounding` — and
> I add capability by dropping in a folder, no orchestration code.
>
> Now the A/B that matters to you: I switch the **engine** from our hand-rolled loop to the
> **GitHub Copilot SDK**, then to **Copilot SDK BYOM** — the *same loop*, but inference
> routes to **your Azure OpenAI (`gpt-5.4-mini`)**, so billing and data stay on your
> subscription. Same `SKILL.md` runs in Copilot CLI, VS Code, Copilot Studio, Claude Code,
> or your app via the SDK — **portable**."

## What it beats in a homegrown factory
- **One loop to maintain** vs. a brittle multi-agent handoff graph.
- **Skills portable across tools/models**; add one in minutes.
- **BYOM** — production-tested agentic loop (Copilot SDK), *your* model + billing.

## Money line
> "Don't orchestrate fifty agents. Give one good loop the right skills — and let it reason."

## Demo steps (in `../skill-forge`)
1. `uv run skill-forge` → open http://localhost:8000
2. Ask a portfolio/product question; watch the **skill chips** show which skill the loop chose.
3. Open `skills/rag-search/SKILL.md` — show the frontmatter (`name` + `description`) + `tool.py`.
4. Engine selector: **Hand-rolled → Copilot SDK → Copilot SDK BYOM → Agent Framework.**
   Same loop; only the model/harness swaps. (BYOM needs `az login` + gpt-5/o-series.)

## Pairs with Pattern 5
This is the honest setup for the multi-agent slide: *default here; escalate to
multi-agent orchestration only when you genuinely need parallel specialists or trust
boundaries.*
