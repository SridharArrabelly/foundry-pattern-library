"""Serve Pattern 5B's explicit workflow graph as a Foundry hosted agent."""
import os

from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from workflow_graph import build_workflow


load_dotenv()


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=(
            os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            or os.environ.get("MODEL_DEPLOYMENT_NAME")
            or "gpt-5.4-mini"
        ),
        credential=DefaultAzureCredential(),
    )
    workflow = build_workflow(client)
    agent = workflow.as_agent(
        name="workflow-orchestrator",
        description=(
            "Explicit enterprise change graph with deterministic routing and audit."
        ),
    )
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
