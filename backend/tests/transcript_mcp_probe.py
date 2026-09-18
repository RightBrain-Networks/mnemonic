"""Consume actual REST replies through MCP in its own environment, without fixture repair."""

import asyncio
import json
import sys

import httpx
from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.config import Settings
from mnemonic_mcp.server import build_server


async def main():
    cases = json.load(sys.stdin)
    for case in cases:
        def response(request, payload=case["response"]):
            return httpx.Response(200, headers={"content-type": "application/json"},
                stream=httpx.ByteStream(json.dumps(payload).encode()))

        settings = Settings(api_key="transcript-contract-probe-key-for-isolated-tests")
        server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(response)))
        result = await server.call_tool(case["tool"], case["arguments"])
        assert result is not None
    print(f"{len(cases)} unmodified REST responses accepted through MCP")


if __name__ == "__main__":
    asyncio.run(main())
