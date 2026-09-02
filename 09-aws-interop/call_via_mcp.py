"""Compatibility entry point for the live APIM MCP lane."""
from __future__ import annotations

import asyncio
import json
import os

from protocol_client import call_mcp, require_https


def main() -> int:
    result = asyncio.run(
        call_mcp(
            require_https("PATTERN9_MCP_URL", os.getenv("PATTERN9_MCP_URL")),
            os.getenv("PATTERN9_APIM_SUBSCRIPTION_KEY", ""),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
