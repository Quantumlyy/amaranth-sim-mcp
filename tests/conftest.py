from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from amaranth_sim_mcp.server import mcp

COUNTER_SOURCE = """\
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Counter(Elaboratable):
    def __init__(self):
        self.en = Signal()
        self.count = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        with m.If(self.en):
            m.d.sync += self.count.eq(self.count + 1)
        return m
"""


FAST_COUNTER_SOURCE = """\
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Counter(Elaboratable):
    def __init__(self):
        self.en = Signal(init=1)
        self.count = Signal(32)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        with m.If(self.en):
            m.d.sync += self.count.eq(self.count + 1)
        return m
"""


NESTED_COUNTER_SOURCE = """\
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Alu:
    def __init__(self):
        self.result = Signal(8)
        self.valid = Signal()


class Wrapper(Elaboratable):
    def __init__(self):
        self.en = Signal()
        self.alu = Alu()

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        return m
"""


TOP_LEVEL_IMPORT_SOURCE = """\
from helper import STEP
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Counter(Elaboratable):
    def __init__(self):
        self.count = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.d.sync += self.count.eq(self.count + STEP)
        return m
"""


LAZY_IMPORT_SOURCE = """\
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Counter(Elaboratable):
    def __init__(self):
        self.count = Signal(8)

    def elaborate(self, platform):
        from helper import STEP
        m = Module()
        m.domains.sync = ClockDomain()
        m.d.sync += self.count.eq(self.count + STEP)
        return m
"""


PACKAGE_IMPORT_SOURCE = """\
from .helpers import STEP
from amaranth import Elaboratable, ClockDomain, Module, Signal


class Counter(Elaboratable):
    def __init__(self):
        self.count = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.d.sync += self.count.eq(self.count + STEP)
        return m
"""


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
def nested_design(write_design: Callable[[str, str], Path]) -> Path:
    """A Wrapper Elaboratable with a nested non-Elaboratable Alu submodule."""
    return write_design("nested.py", NESTED_COUNTER_SOURCE)


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
