"""
Pattern 9 — cross-cloud interop, the MOCK "AWS side".

A minimal MCP server that stands in for an AWS-hosted capability (a core-banking /
pricing engine fronted by a Lambda, or a Bedrock agent). The wire protocol an agent
uses is IDENTICAL whether this runs locally or on AWS — so we can rehearse the whole
coexistence story without an AWS account. When you have AWS, swap this for
`mcp_aws_lambda_server.py` (real boto3 Lambda invoke); the agent doesn't change.

Run (local stdio):  uv run python 09-aws-interop/mock_aws_mcp_server.py
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aws-core-banking")


@mcp.tool()
def core_banking_quote(product: str, notional: float) -> str:
    """Pretend this executes on AWS (Lambda/Bedrock). Returns a pricing quote for a
    structured product given a notional amount. Proves a Foundry agent calling across
    to an AWS-hosted capability over MCP."""
    fee_bps = 45  # 0.45%
    fee = round(notional * fee_bps / 10_000, 2)
    return (
        f"[AWS core-banking] product={product} notional={notional:,.0f} "
        f"fee={fee:,.2f} ({fee_bps}bps) settlement=T+2 region=eu-west-1"
    )


if __name__ == "__main__":
    # stdio by default; use mcp.run(transport="sse") to serve over HTTP.
    mcp.run()
