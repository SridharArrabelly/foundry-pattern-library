"""
Foundry-hosted entry point for Pattern 5.

Serves the concurrent specialist workflow as a Responses 2.0 endpoint on port 8088.
"""
import os

from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from workflow import create_workflow

load_dotenv()


def main():
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = (
        os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.environ.get("MODEL_DEPLOYMENT_NAME")
        or "gpt-5.4-mini"
    )
    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )
    workflow = create_workflow(client)
    ResponsesHostServer(workflow.as_agent()).run()


if __name__ == "__main__":
    main()
