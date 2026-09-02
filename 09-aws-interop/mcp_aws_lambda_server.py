"""Optional future adapter for replacing the simulator with a real AWS Lambda.

This file is not part of the verified Pattern 9 path. Install the ``aws`` extra,
configure AWS credentials outside the repository, and perform your own security
review before enabling it.
"""
import json
import os

from mcp.server.fastmcp import FastMCP

REGION = os.environ.get("PATTERN9_AWS_REGION")
FUNCTION = os.environ.get("PATTERN9_AWS_LAMBDA_FUNCTION_NAME")

mcp = FastMCP("pattern9-real-aws-adapter")


def lambda_client():
    if not REGION or not FUNCTION:
        raise RuntimeError(
            "Set PATTERN9_AWS_REGION and PATTERN9_AWS_LAMBDA_FUNCTION_NAME. "
            "The catalog's verified path keeps AWS/Bedrock simulated."
        )
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("Install the optional dependency with: uv sync --extra aws") from error
    return boto3.client("lambda", region_name=REGION)


@mcp.tool()
def invoke_aws_capability(payload: str) -> str:
    """Invoke a configured real AWS Lambda; this path is intentionally unverified."""
    response = lambda_client().invoke(
        FunctionName=FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"input": payload}).encode(),
    )
    return response["Payload"].read().decode()


if __name__ == "__main__":
    mcp.run()
