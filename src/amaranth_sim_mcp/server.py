"""FastMCP server entry point."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("amaranth-sim-mcp")


def main() -> None:
    mcp.run()
