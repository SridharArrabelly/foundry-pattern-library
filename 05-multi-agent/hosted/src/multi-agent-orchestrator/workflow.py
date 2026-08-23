"""Shared concurrent workflow for the local and Foundry-hosted Pattern 5 entry points."""
from agent_framework import Agent
from agent_framework.orchestrations import ConcurrentBuilder

TASK = (
    "Client C-1290 (Mr. Okafor, risk profile: Conservative) holds 90% equities. "
    "He wants to add a leveraged technology structured product. Advise."
)


def create_workflow(client):
    """Build the two-specialist fan-out/fan-in workflow."""
    analyst = Agent(
        client,
        name="portfolio_analyst",
        description="Analyses holdings, benchmark drift and concentration risk.",
        instructions=(
            "You are a portfolio analyst. Assess concentration and benchmark risk in "
            "the client's holdings and proposed trade. Be specific and brief."
        ),
        default_options={"store": False},
    )
    compliance = Agent(
        client,
        name="compliance_officer",
        description="Independently checks suitability, KYC/AML and MiFID II rules.",
        instructions=(
            "You are an independent compliance officer. Judge suitability against the "
            "client's risk profile. A Conservative client must not exceed 70% equities. "
            "State APPROVE or BLOCK, cite the rule, and be brief."
        ),
        default_options={"store": False},
    )
    return ConcurrentBuilder(participants=[analyst, compliance]).build()
