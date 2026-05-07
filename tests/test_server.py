from __future__ import annotations

from pathlib import Path

from amaranth_sim_mcp import __version__


async def test_server_lists_simulate_and_check_environment_tools(mcp_session):
    async with mcp_session() as session:
        tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {"simulate", "check_environment"}


async def test_check_environment_returns_versions(mcp_session):
    async with mcp_session() as session:
        environment = await session.call_tool("check_environment", {})

    assert environment.structuredContent["server_version"] == __version__
    assert "python_version" in environment.structuredContent
    assert "amaranth_version" in environment.structuredContent
    assert "mcp_version" in environment.structuredContent


async def test_simulate_script_mode_returns_captured_output_and_vcd(
    tmp_path, write_design, mcp_session
):
    script_path = write_design(
        "script_sim.py",
        """\
        import sys
        from pathlib import Path

        print("hello from tool")
        print("tool stderr", file=sys.stderr)
        Path("tool-wave.vcd").write_text("$date now $end", encoding="utf-8")
        """,
    )

    async with mcp_session() as session:
        result = await session.call_tool(
            "simulate",
            {"file_path": str(script_path), "mode": "script"},
        )

    structured = result.structuredContent
    assert structured["mode"] == "script"
    assert structured["stdout"].strip() == "hello from tool"
    assert structured["stderr"].strip() == "tool stderr"
    assert structured["vcd_paths"] == [str((tmp_path / "tool-wave.vcd").resolve())]


async def test_simulate_definitions_mode_returns_trace_and_vcd(
    counter_design, cleanup_vcd, mcp_session
):
    async with mcp_session() as session:
        result = await session.call_tool(
            "simulate",
            {
                "file_path": str(counter_design),
                "mode": "definitions",
                "observe": ["count"],
                "stimulus": [{"cycle": 0, "set": {"en": 1}}],
                "cycles": 3,
            },
        )

    structured = result.structuredContent
    cleanup_vcd(structured["vcd_path"])

    assert structured["mode"] == "definitions"
    assert structured["class_name"] == "Counter"
    assert structured["primary_domain"] == "sync"
    assert [sample["signals"]["count"] for sample in structured["trace"]] == [1, 2, 3]
    assert Path(structured["vcd_path"]).is_file()
    assert "amaranth-sim-mcp-vcd-" in structured["vcd_path"]
