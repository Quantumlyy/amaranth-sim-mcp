from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from _designs import (
    COUNTER_SOURCE,
    FAST_COUNTER_SOURCE,
    LAZY_IMPORT_SOURCE,
    NESTED_COUNTER_SOURCE,
    PACKAGE_IMPORT_SOURCE,
    TOP_LEVEL_IMPORT_SOURCE,
)
from mcp.shared.memory import create_connected_server_and_client_session

from amaranth_sim_mcp.server import mcp


@pytest.fixture
def write_design(tmp_path: Path) -> Callable[[str, str], Path]:
    """Return a factory that writes dedented Python source to tmp_path."""

    def _write(name: str, source: str) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def counter_design(write_design: Callable[[str, str], Path]) -> Path:
    """A ready-to-use Counter Elaboratable design at tmp_path/counter.py."""
    return write_design("counter.py", COUNTER_SOURCE)


@pytest.fixture
def fast_counter_design(write_design: Callable[[str, str], Path]) -> Path:
    """A wide enabled Counter that runs quickly past many cycles."""
    return write_design("counter.py", FAST_COUNTER_SOURCE)


@pytest.fixture
def nested_design(write_design: Callable[[str, str], Path]) -> Path:
    """A Wrapper Elaboratable with a nested non-Elaboratable Alu submodule."""
    return write_design("nested.py", NESTED_COUNTER_SOURCE)


@pytest.fixture
def top_level_import_design(tmp_path: Path, write_design: Callable[[str, str], Path]) -> Path:
    """A Counter design that imports `STEP` from a sibling `helper.py`."""
    (tmp_path / "helper.py").write_text("STEP = 1\n", encoding="utf-8")
    return write_design("main.py", TOP_LEVEL_IMPORT_SOURCE)


@pytest.fixture
def lazy_import_design(tmp_path: Path, write_design: Callable[[str, str], Path]) -> Path:
    """A Counter that imports `STEP` from `helper.py` lazily inside elaborate()."""
    (tmp_path / "helper.py").write_text("STEP = 1\n", encoding="utf-8")
    return write_design("main.py", LAZY_IMPORT_SOURCE)


@pytest.fixture
def package_import_design(write_design: Callable[[str, str], Path]) -> Path:
    """A Counter inside `my_pkg/` that imports from `.helpers`."""
    design_path = write_design("my_pkg/design.py", PACKAGE_IMPORT_SOURCE)
    package_dir = design_path.parent
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "helpers.py").write_text("STEP = 1\n", encoding="utf-8")
    return design_path


@pytest.fixture
def cleanup_vcd() -> Iterator[Callable[[str | Path], None]]:
    """Yield a callback that registers VCD paths for removal after the test."""
    tracked: list[Path] = []

    def _register(path: str | Path) -> None:
        tracked.append(Path(path))

    yield _register
    for path in tracked:
        path.unlink(missing_ok=True)


@pytest.fixture
def mcp_session() -> Callable[[], Any]:
    """Async context-manager factory for an in-memory MCP client session.

    Returned as a factory (rather than yielded directly) so that the
    `async with` lives entirely inside the test's task, avoiding the
    anyio cancel-scope cross-task error that pytest-asyncio fixtures hit
    when wrapping `create_connected_server_and_client_session`.
    """

    @asynccontextmanager
    async def _session() -> AsyncIterator[object]:
        async with create_connected_server_and_client_session(
            mcp, raise_exceptions=True
        ) as session:
            yield session

    return _session
