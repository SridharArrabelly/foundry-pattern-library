"""
Pattern 9 — the cross-cloud interop money-shot (REAL AWS version).
A minimal MCP server that exposes an AWS Lambda as a tool. Register this MCP
server on your Foundry orchestrator agent and it can invoke an AWS-hosted
capability mid-run. Same trick works the other way (A2A) to expose a Foundry
agent to a Bedrock orchestrator.

This env has no AWS access — use mock_aws_mcp_server.py to rehearse. When you
have AWS, run THIS instead; the agent code is identical.

Run as a local MCP server:  uv run python 09-aws-interop/mcp_aws_lambda_server.py
Then add it to the agent as an MCP tool (Foundry supports MCP tool definitions),
or connect via your MCP client of choice.
"""
import json
import os
import boto3
from mcp.server.fastmcp import FastMCP

REGION = os.environ.get("AWS_REGION", "eu-west-1")
FUNCTION = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "foundry-interop-demo")

mcp = FastMCP("aws-interop")
_lambda = boto3.client("lambda", region_name=REGION)


@mcp.tool()
def invoke_aws_capability(payload: str) -> str:
    """Invoke an AWS Lambda (e.g. a pricing engine or a Bedrock-fronted tool) and
    return its JSON response. Proves a Foundry agent calling across to AWS."""
    resp = _lambda.invoke(
        FunctionName=FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"input": payload}).encode(),
    )
    body = resp["Payload"].read().decode()
    return body


if __name__ == "__main__":
    # Runs over stdio by default; use mcp.run(transport="sse") to serve over HTTP.
    mcp.run()
