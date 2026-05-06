"""FastMCP server entry point."""

from __future__ import annotations

import importlib.metadata
import logging
import platform
import sys
from pathlib import Path
from typing import Annotated, Any

import amaranth
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from . import __version__
from .runner import DEFAULT_CYCLES, SimulationRequestError, run_simulation_request

logger = logging.getLogger("amaranth_sim_mcp")
mcp = FastMCP("amaranth-sim-mcp")


@mcp.tool()
def simulate(
    file_path: Annotated[
        str,
        Field(description="Absolute or relative path to the .py file containing the design"),
    ],
    mode: Annotated[
        str,
        Field(
            description=(
                "Either 'script' (run file as-is) or 'definitions' (load class and build testbench)"
            ),
        ),
    ],
    class_name: Annotated[
        str | None,
        Field(description="For 'definitions' mode: the Elaboratable class name to instantiate"),
    ] = None,
    init_kwargs: Annotated[
        dict[str, Any] | None,
        Field(description="For 'definitions' mode: kwargs passed to class constructor"),
    ] = None,
    clocks: Annotated[
        dict[str, float] | None,
        Field(description="For 'definitions' mode: domain name -> period mapping"),
    ] = None,
    observe: Annotated[
        list[str] | None,
        Field(
            description=(
                "For 'definitions' mode: signal paths to record "
                "(dotted for nested, e.g., 'alu.result')"
            ),
        ),
    ] = None,
    stimulus: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "For 'definitions' mode: list of {'cycle': N, 'set': {'sig': val}} events"
            ),
        ),
    ] = None,
    cycles: Annotated[
        int,
        Field(description="For 'definitions' mode: number of cycles to simulate"),
    ] = DEFAULT_CYCLES,
) -> dict[str, Any]:
    """
    Run an Amaranth simulation in either `script` or `definitions` mode.

    The agent is expected to read the user's code and choose `class_name`,
    `init_kwargs`, `clocks`, `observe`, and `stimulus` based on what it finds.

    Use `mode="script"` when the file already contains its own `Simulator(...)`
    setup and `sim.run()` or `sim.run_until(...)` calls and should be executed
    unmodified.

    Use `mode="definitions"` when the file defines one or more Elaboratable
    classes and you want this server to instantiate the DUT and build a simple
    generated testbench.

    Signal paths are dotted for nested submodules, for example `alu.result`.
    Files may import from sibling modules in the same directory or enclosing
    package; imports from higher directories or external locations must be
    installed in the server's environment.

    Stimulus events use this format:
    `{"cycle": 3, "domain": "sync", "set": {"en": 1, "alu.op": 2}}`

    In v1, `stimulus.domain` must be omitted or match the resolved primary
    clock domain; multi-domain stimulus timing is not synchronized yet.
    """

    try:
        return run_simulation_request(
            file_path=file_path,
            mode=mode,
            class_name=class_name,
            init_kwargs=init_kwargs,
            clocks=clocks,
            observe=observe,
            stimulus=stimulus,
            cycles=cycles,
        )
    except SimulationRequestError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def check_environment() -> dict[str, str]:
    """Return Python, Amaranth, MCP, and server version details."""

    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "amaranth_version": amaranth.__version__,
        "mcp_version": importlib.metadata.version("mcp"),
        "server_version": __version__,
        "cwd": str(Path.cwd()),
    }


def main() -> None:
    logger.info("amaranth-sim-mcp starting (server_version=%s)", __version__)
    mcp.run()
