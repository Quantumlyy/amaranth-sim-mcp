from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest

from amaranth_sim_mcp import runner
from amaranth_sim_mcp.runner import SimulationRequestError, run_simulation_request


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


def test_run_simulation_request_script_mode_captures_output_and_vcd(tmp_path):
    script_path = tmp_path / "script_sim.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import sys
            from pathlib import Path

            print("hello stdout")
            print("hello stderr", file=sys.stderr)
            Path("wave.vcd").write_text("$date now $end", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )

    result = run_simulation_request(script_path, "script")

    assert result["mode"] == "script"
    assert result["file_path"] == str(script_path.resolve())
    assert result["cwd"] == str(tmp_path.resolve())
    assert result["stdout"].strip() == "hello stdout"
    assert result["stderr"].strip() == "hello stderr"
    assert result["vcd_paths"] == [str((tmp_path / "wave.vcd").resolve())]


def test_run_simulation_request_definitions_mode_returns_post_tick_trace(tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(COUNTER_SOURCE), encoding="utf-8")

    result = run_simulation_request(
        design_path,
        "definitions",
        observe=["count"],
        stimulus=[
            {"cycle": 0, "set": {"en": 1}},
            {"cycle": 3, "set": {"en": 0}},
        ],
        cycles=5,
    )

    assert result["mode"] == "definitions"
    assert result["class_name"] == "Counter"
    assert result["primary_domain"] == "sync"
    assert result["vcd_path"].endswith(".vcd")
    assert result["vcd_path"]
    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3, 3, 3]
    assert Path(result["vcd_path"]).is_file()
    Path(result["vcd_path"]).unlink(missing_ok=True)


def test_run_simulation_request_rejects_non_python_files(tmp_path):
    design_path = tmp_path / "counter.txt"
    design_path.write_text("print('hello')\n", encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "script")

    assert str(exc_info.value) == f"File must be a .py file: {design_path.resolve()}"


def test_run_simulation_request_reports_missing_observe_signal_paths(tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(COUNTER_SOURCE), encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            observe=["missing"],
        )

    message = str(exc_info.value)
    assert "Signal path 'missing' could not be resolved." in message
    assert "en" in message
    assert "count" in message


def test_run_simulation_request_reports_missing_stimulus_signal_paths(tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(COUNTER_SOURCE), encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            stimulus=[{"cycle": 0, "set": {"missing": 1}}],
        )

    message = str(exc_info.value)
    assert "Signal path 'missing' could not be resolved." in message
    assert "en" in message
    assert "count" in message


def test_run_simulation_request_timeout_reports_last_completed_cycle(tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(FAST_COUNTER_SOURCE), encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            observe=["count"],
            cycles=10_000_000,
            timeout_seconds=1.0,
        )

    message = str(exc_info.value)
    assert "Simulation timed out after 1s." in message
    assert "Last completed cycle:" in message


def test_worker_reports_tracebacks_for_unhandled_exceptions(monkeypatch, capsys):
    monkeypatch.setattr(runner.sys, "stdin", io.StringIO("{}"))

    def explode(_: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_worker_request", explode)

    runner._worker_main()

    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert "Unhandled simulation error: RuntimeError: boom" in output
    assert "Traceback" in output
