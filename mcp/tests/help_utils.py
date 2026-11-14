"""Read command guidance through the same paginated surface used by clients."""

from mcp.server.fastmcp import FastMCP


async def command_guidance(server: FastMCP, name: str) -> str:
    parts = [(await server.call_tool("help", {"topic": name + " usage"}))[0].text]
    for number in range(1, 101):
        page = (await server.call_tool("help", {"topic": f"{name} details {number}"}))[0].text
        parts.append(page)
        if "\nNext: " not in page:
            return "\n".join(parts)
    raise AssertionError("Command help did not terminate within 100 pages")
