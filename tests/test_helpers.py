"""Direct tests for helper functions previously only covered indirectly."""

from __future__ import annotations

import pytest

from amaranth_sim_mcp.loader import (
    _dedupe_paths,
    _format_import_error,
    find_elaboratable_classes,
    load_definitions_module,
)
from amaranth_sim_mcp.runner import _normalize_observed_value, _parse_worker_response


class _IntLike:
    def __int__(self) -> int:
        return 5


class _ObjWithRepr:
    def __repr__(self) -> str:
        return "<ObjWithRepr>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param(True, True, id="bool"),
        pytest.param(42, 42, id="int"),
        pytest.param(3.14, 3.14, id="float"),
        pytest.param("x", "x", id="str"),
        pytest.param(_IntLike(), 5, id="int-castable"),
        pytest.param(_ObjWithRepr(), "<ObjWithRepr>", id="repr-fallback"),
    ],
)
def test_normalize_observed_value(value: object, expected: object) -> None:
    assert _normalize_observed_value(value) == expected


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "expected"),
    [
        pytest.param(
            '{"ok": true, "result": {"x": 1}}',
            "",
            0,
            {"ok": True, "result": {"x": 1}},
            id="valid-dict",
        ),
        pytest.param(
            "not-json",
            "",
            1,
            {"ok": False, "error": {"message": "not-json"}},
            id="malformed-json-falls-back-to-stdout",
        ),
        pytest.param(
            "[1, 2, 3]",
            "",
            1,
            {"ok": False, "error": {"message": "[1, 2, 3]"}},
            id="list-instead-of-dict",
        ),
        pytest.param(
            "",
            "stderr message",
            1,
            {"ok": False, "error": {"message": "stderr message"}},
            id="empty-stdout-falls-back-to-stderr",
        ),
        pytest.param(
            "",
            "",
            7,
            {"ok": False, "error": {"message": "Worker exited with return code 7."}},
            id="all-empty",
        ),
    ],
)
def test_parse_worker_response(stdout: str, stderr: str, returncode: int, expected: dict) -> None:
    assert _parse_worker_response(stdout, stderr, returncode) == expected


def test_dedupe_paths_preserves_first_seen_order() -> None:
    assert _dedupe_paths(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_paths_empty() -> None:
    assert _dedupe_paths([]) == []


def test_format_import_error_includes_paths(tmp_path) -> None:
    err = ImportError("No module named 'foo'", name="foo")
    msg = _format_import_error(tmp_path / "design.py", err, [str(tmp_path)])
    assert "Failed to import" in msg
    assert "foo" in msg
    assert str(tmp_path) in msg


def test_format_import_error_renders_no_paths_marker(tmp_path) -> None:
    err = ImportError("No module named 'foo'", name="foo")
    msg = _format_import_error(tmp_path / "design.py", err, [])
    assert "(none)" in msg


def test_find_elaboratable_classes_zero(write_design) -> None:
    path = write_design(
        "design.py",
        """\
        from amaranth import Module

        def helper():
            return Module()
        """,
    )
    module, _ = load_definitions_module(path)
    assert find_elaboratable_classes(module) == {}


def test_find_elaboratable_classes_one(write_design) -> None:
    path = write_design(
        "design.py",
        """\
        from amaranth import Elaboratable, ClockDomain, Module

        class Counter(Elaboratable):
            def elaborate(self, platform):
                m = Module()
                m.domains.sync = ClockDomain()
                return m
        """,
    )
    module, _ = load_definitions_module(path)
    assert list(find_elaboratable_classes(module)) == ["Counter"]


def test_find_elaboratable_classes_multiple(write_design) -> None:
    path = write_design(
        "design.py",
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
        """,
    )
    module, _ = load_definitions_module(path)
    assert sorted(find_elaboratable_classes(module)) == ["Alpha", "Beta"]


def test_find_elaboratable_classes_excludes_imported(write_design) -> None:
    write_design(
        "library.py",
        """\
        from amaranth import Elaboratable, ClockDomain, Module

        class Alpha(Elaboratable):
            def elaborate(self, platform):
                m = Module()
                m.domains.sync = ClockDomain()
                return m
        """,
    )
    path = write_design(
        "design.py",
        """\
        from library import Alpha
        from amaranth import Elaboratable, ClockDomain, Module

        class Beta(Elaboratable):
            def elaborate(self, platform):
                m = Module()
                m.domains.sync = ClockDomain()
                return m
        """,
    )
    module, _ = load_definitions_module(path)
    assert list(find_elaboratable_classes(module)) == ["Beta"]
