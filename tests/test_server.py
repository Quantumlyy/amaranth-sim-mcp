from __future__ import annotations

import asyncio
import textwrap

from mcp.shared.memory import create_connected_server_and_client_session

from amaranth_sim_mcp import __version__
from amaranth_sim_mcp.server import mcp


def test_server_tools_are_exposed_and_callable(tmp_path):
    script_path = tmp_path / "script_sim.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import sys
            from pathlib import Path

            print("hello from tool")
            print("tool stderr", file=sys.stderr)
            Path("tool-wave.vcd").write_text("$date now $end", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )

    async def run_test() -> None:
        async with create_connected_server_and_client_session(
            mcp, raise_exceptions=True
        ) as session:
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"simulate", "check_environment"}

            environment = await session.call_tool("check_environment", {})
            assert environment.structuredContent["server_version"] == __version__
            assert "python_version" in environment.structuredContent

            result = await session.call_tool(
                "simulate",
                {
                    "file_path": str(script_path),
                    "mode": "script",
                },
            )
            assert result.structuredContent["mode"] == "script"
            assert result.structuredContent["stdout"].strip() == "hello from tool"
            assert result.structuredContent["stderr"].strip() == "tool stderr"
            assert result.structuredContent["vcd_paths"] == [
                str((tmp_path / "tool-wave.vcd").resolve())
            ]

    asyncio.run(run_test())
