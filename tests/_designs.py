"""Shared Amaranth design source strings used to build test fixtures.

Lives outside `conftest.py` so that conftest stays fixtures-only and
test files don't have to import from it (which is fragile under
non-default pytest import modes). The leading underscore keeps
pytest from collecting this as a test module.
"""

from __future__ import annotations

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
