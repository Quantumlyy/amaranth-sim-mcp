from __future__ import annotations

import textwrap

import pytest

from amaranth_sim_mcp.loader import load_definitions_module, select_elaboratable_class


def test_load_definitions_module_strips_top_level_side_effects(tmp_path):
    module_path = tmp_path / "design.py"
    module_path.write_text(
        textwrap.dedent(
            """\
            from amaranth import Elaboratable, ClockDomain, Module, Signal

            RAN = []
            RAN.append("boom")
            DROPPED = int("2")
            CONST = 7

            def helper():
                return CONST

            class Counter(Elaboratable):
                def __init__(self):
                    self.count = Signal(4)

                def elaborate(self, platform):
                    m = Module()
                    m.domains.sync = ClockDomain()
                    m.d.sync += self.count.eq(self.count + 1)
                    return m

            if __name__ == "__main__":
                raise RuntimeError("should not run")
            """
        ),
        encoding="utf-8",
    )

    module = load_definitions_module(module_path)

    assert module.RAN == []
    assert not hasattr(module, "DROPPED")
    assert module.CONST == 7
    assert module.helper() == 7
    assert module.Counter.__name__ == "Counter"


def test_load_definitions_module_supports_same_directory_imports(tmp_path):
    helper_path = tmp_path / "helper.py"
    helper_path.write_text('OFFSET = 3\n', encoding="utf-8")

    module_path = tmp_path / "design.py"
    module_path.write_text(
        textwrap.dedent(
            """\
            from helper import OFFSET
            from amaranth import Elaboratable, ClockDomain, Module, Signal

            OFFSET_VALUE = OFFSET

            class Counter(Elaboratable):
                def __init__(self):
                    self.count = Signal(4)

                def elaborate(self, platform):
                    m = Module()
                    m.domains.sync = ClockDomain()
                    return m
            """
        ),
        encoding="utf-8",
    )

    module = load_definitions_module(module_path)

    assert module.OFFSET_VALUE == 3
    assert module.Counter.__name__ == "Counter"


def test_select_elaboratable_class_auto_selects_single_class(tmp_path):
    module_path = tmp_path / "design.py"
    module_path.write_text(
        textwrap.dedent(
            """\
            from amaranth import Elaboratable, ClockDomain, Module

            class Counter(Elaboratable):
                def elaborate(self, platform):
                    m = Module()
                    m.domains.sync = ClockDomain()
                    return m
            """
        ),
        encoding="utf-8",
    )

    module = load_definitions_module(module_path)
    selected = select_elaboratable_class(module, None)

    assert selected.__name__ == "Counter"


def test_select_elaboratable_class_lists_available_classes_for_missing_name(tmp_path):
    module_path = tmp_path / "design.py"
    module_path.write_text(
        textwrap.dedent(
            """\
            from amaranth import Elaboratable, ClockDomain, Module

            class Alpha(Elaboratable):
                def elaborate(self, platform):
                    m = Module()
                    m.domains.sync = ClockDomain()
                    return m

            class Beta(Elaboratable):
                def elaborate(self, platform):
                    m = Module()
                    m.domains.sync = ClockDomain()
                    return m
            """
        ),
        encoding="utf-8",
    )

    module = load_definitions_module(module_path)

    with pytest.raises(ValueError) as exc_info:
        select_elaboratable_class(module, "Missing")

    message = str(exc_info.value)
    assert "Missing" in message
    assert "Alpha" in message
    assert "Beta" in message
