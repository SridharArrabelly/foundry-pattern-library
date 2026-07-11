"""
Pattern 9 — cross-cloud interop, the "Foundry side" client.

Spawns the mock AWS-side MCP server (mock_aws_mcp_server.py), lists its tools, and calls
`core_banking_quote` — exactly as a Foundry agent would register and invoke an MCP tool
mid-run. Register the SAME server URL on a Foundry agent (Foundry supports MCP tool
definitions) and the agent calls across to AWS with zero bespoke glue.

Run:  uv run python 09-aws-interop/call_via_mcp.py
"""
import asyncio
import os
import sys


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = os.path.join(os.path.dirname(__file__), "mock_aws_mcp_server.py")
    params = StdioServerParameters(command=sys.executable, args=[server])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("AWS-side MCP tools:", [t.name for t in tools.tools], "\n")

            print("Foundry agent invokes AWS capability over MCP...\n")
            result = await session.call_tool(
                "core_banking_quote", {"product": "TechLeverage-3Y", "notional": 250000}
            )
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print("->", text)

    print("\nTALK TRACK: identical MCP wire whether the tool is local, on AWS Lambda, or a")
    print("Bedrock agent. Swap in mcp_aws_lambda_server.py for the real thing — the agent")
    print("code is unchanged. Bedrock runs an agent; Foundry runs the agent FACTORY; MCP/A2A")
    print("lets each cloud own what it's best at.")


if __name__ == "__main__":
    asyncio.run(main())
