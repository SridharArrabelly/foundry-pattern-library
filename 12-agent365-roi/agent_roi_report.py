"""
Pattern 12 — Agent 365 & ROI. Governance + FinOps in one plane.

Every agent is (a) an IDENTITY you can inventory and govern, and (b) a COST you
can tie to an OUTCOME. Agent 365 does the org-wide side in the portal — inventory,
Entra Agent ID, and policy across every agent. This script is the engineer-facing
half you can run today: it executes a batch of real RM tasks through the gateway
(keyless), captures token usage + task outcome per run, and prints a cost <-> value
<-> ROI table — the same signal 'ROI for agents' surfaces from your traces.

Cost = tokens x rate. Value = completed tasks x value-per-task (your assumption).
The exact numbers are yours to set; the POINT is that Foundry makes agent spend
attributable to outcomes — something a homegrown factory can't show a CFO.

The same runs are traced to Application Insights (Pattern 06); in production you'd
group by gen_ai.agent.name / version in KQL (see TALK-TRACK.md) instead of a live
batch. Agent 365 then layers org-wide inventory + identity + policy on top.

Run:  uv run python 12-agent365-roi/agent_roi_report.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import GATEWAY_MODEL, gateway_client

client = gateway_client()

# Rate card + business value — set these to your own numbers.
RATE_PER_1K = float(os.environ.get("USD_PER_1K_TOKENS", "0.0006"))  # blended $/1K
VALUE_PER_TASK = float(os.environ.get("USD_VALUE_PER_TASK", "12.0"))  # RM time saved
MONTHLY_VOLUME = int(os.environ.get("MONTHLY_TASK_VOLUME", "5000"))  # tasks/month

AGENT = "rm-assistant"

# A batch of real Relationship-Manager tasks the agent handles each day.
TASKS = [
    "Summarise the suitability rules for a Conservative client in two bullets.",
    "A client wants 70% equities but is Conservative — is that suitable? One line.",
    "List two capital-preservation fund types for a low-risk client.",
    "Explain 'concentration risk' to a client in one sentence.",
    "Draft a one-line rebalance note: trim equities above the 20% cap.",
]

SYSTEM = (
    "You are a private-banking RM assistant. Be concise, accurate and compliant."
)


def run_task(task):
    """Run one task; return (total_tokens, completed?)."""
    r = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": task},
        ],
    )
    answer = (r.choices[0].message.content or "").strip()
    completed = len(answer) > 40  # produced a usable answer
    return r.usage.total_tokens, completed


def main():
    print(f"Running {len(TASKS)} RM tasks through the gateway ({GATEWAY_MODEL})...\n")
    runs = tokens = done = 0
    for task in TASKS:
        t, ok = run_task(task)
        runs += 1
        tokens += t
        done += 1 if ok else 0
        print(f"  [{'ok' if ok else 'x '}] {t:>5} tok  {task[:52]}")

    cost = tokens / 1000 * RATE_PER_1K
    value = done * VALUE_PER_TASK
    roi = (value - cost) / cost if cost else 0.0
    avg_tokens = tokens / runs if runs else 0
    completion_rate = done / runs if runs else 0

    print(f"\n{'agent':20} {'runs':>5} {'done':>5} {'tokens':>8} {'cost$':>9} {'value$':>8} {'ROI':>9}")
    print(
        f"{AGENT:20} {runs:5d} {done:5d} {tokens:8d} {cost:9.4f} {value:8.2f} {roi:8,.0f}x"
    )

    # Project the same unit economics to a monthly agent workload — the CFO view.
    m_cost = MONTHLY_VOLUME * avg_tokens / 1000 * RATE_PER_1K
    m_value = MONTHLY_VOLUME * completion_rate * VALUE_PER_TASK
    print(
        f"\nProjected at {MONTHLY_VOLUME:,} tasks/month:"
        f"  spend ${m_cost:,.2f}  ->  value ${m_value:,.0f}  (net ${m_value - m_cost:,.0f})"
    )
    print(
        "\nCost tied to completed outcomes, per agent — the local mirror of 'ROI for agents'."
        "\nIn the portal, Agent 365 adds org-wide inventory + Entra Agent ID + policy on top."
    )


if __name__ == "__main__":
    main()
