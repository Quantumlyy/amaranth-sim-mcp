from __future__ import annotations

import io
import json
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


def test_run_simulation_request_definitions_mode_supports_loose_file_sibling_imports(tmp_path):
    helper_path = tmp_path / "helper.py"
    helper_path.write_text("STEP = 1\n", encoding="utf-8")
    design_path = tmp_path / "main.py"
    design_path.write_text(textwrap.dedent(TOP_LEVEL_IMPORT_SOURCE), encoding="utf-8")

    result = run_simulation_request(
        design_path,
        "definitions",
        observe=["count"],
        cycles=3,
    )

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]
    Path(result["vcd_path"]).unlink(missing_ok=True)


def test_run_simulation_request_definitions_mode_supports_lazy_imports_during_simulation(tmp_path):
    helper_path = tmp_path / "helper.py"
    helper_path.write_text("STEP = 1\n", encoding="utf-8")
    design_path = tmp_path / "main.py"
    design_path.write_text(textwrap.dedent(LAZY_IMPORT_SOURCE), encoding="utf-8")

    result = run_simulation_request(
        design_path,
        "definitions",
        observe=["count"],
        cycles=3,
    )

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]
    Path(result["vcd_path"]).unlink(missing_ok=True)


def test_run_simulation_request_rejects_non_python_files(tmp_path):
    design_path = tmp_path / "counter.txt"
    design_path.write_text("print('hello')\n", encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "script")

    assert str(exc_info.value) == f"File must be a .py file: {design_path.resolve()}"


def test_run_simulation_request_rejects_non_primary_stimulus_domain(tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(COUNTER_SOURCE), encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            stimulus=[{"cycle": 0, "domain": "usb", "set": {"en": 1}}],
        )

    message = str(exc_info.value)
    assert "targets domain 'usb'" in message
    assert "primary domain 'sync'" in message


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


def test_run_simulation_request_reports_structured_missing_import_errors(tmp_path):
    design_path = tmp_path / "main.py"
    design_path.write_text(
        textwrap.dedent(
            """\
            import nonexistent_module_xyz
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

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "definitions")

    message = str(exc_info.value)
    assert "Failed to import" in message
    assert "nonexistent_module_xyz" in message
    assert "sys.path entries added by the loader:" in message
    assert str(tmp_path.resolve()) in message
    assert "install it into the Python environment used to run the server" in message


def test_run_simulation_request_reports_structured_syntax_errors(tmp_path):
    design_path = tmp_path / "main.py"
    design_path.write_text(
        textwrap.dedent(
            """\
            from amaranth import Elaboratable

            class Counter(Elaboratable)
                pass
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "definitions")

    message = str(exc_info.value)
    assert f"Syntax error in '{design_path.resolve()}'" in message
    assert "line 3" in message


def test_run_simulation_request_reports_nested_available_attributes(tmp_path):
    design_path = tmp_path / "nested.py"
    design_path.write_text(textwrap.dedent(NESTED_COUNTER_SOURCE), encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            class_name="Wrapper",
            observe=["alu.bad_sig"],
        )

    message = str(exc_info.value)
    assert "Signal path 'alu.bad_sig' could not be resolved." in message
    assert "Available attributes:" in message
    assert "result" in message
    assert "valid" in message
    assert "en" not in message


def test_run_simulation_request_definitions_mode_supports_nested_package_layouts(tmp_path):
    package_dir = tmp_path / "my_pkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "helpers.py").write_text("STEP = 1\n", encoding="utf-8")
    design_path = package_dir / "design.py"
    design_path.write_text(textwrap.dedent(PACKAGE_IMPORT_SOURCE), encoding="utf-8")

    result = run_simulation_request(
        design_path,
        "definitions",
        observe=["count"],
        cycles=3,
    )

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]
    Path(result["vcd_path"]).unlink(missing_ok=True)


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


def test_run_simulation_request_timeout_reports_last_completed_cycle(monkeypatch, tmp_path):
    design_path = tmp_path / "counter.py"
    design_path.write_text(textwrap.dedent(FAST_COUNTER_SOURCE), encoding="utf-8")

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.communicated = []

        def communicate(self, input=None, timeout=None):
            self.communicated.append((input, timeout))
            if self.killed:
                return ("", "")
            raise runner.subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

        def kill(self):
            self.killed = True

    fake_process = FakeProcess()

    def fake_popen(*args, **kwargs):
        assert kwargs["cwd"] == str(tmp_path.resolve())
        return fake_process

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner, "_read_progress", lambda _: 123)

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(
            design_path,
            "definitions",
            timeout_seconds=1.0,
        )

    message = str(exc_info.value)
    assert "Simulation timed out after 1s." in message
    assert "Last completed cycle: 123." in message
    request_payload = json.loads(fake_process.communicated[0][0])
    assert request_payload["timeout_seconds"] == 1.0
    assert fake_process.killed is True


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


def test_worker_main_starts_watchdog_from_request_timeout(monkeypatch, capsys):
    monkeypatch.setattr(
        runner.sys,
        "stdin",
        io.StringIO(json.dumps({"mode": "script", "timeout_seconds": 2.5})),
    )
    observed = {}

    def fake_watchdog(timeout_seconds):
        observed["timeout_seconds"] = timeout_seconds

    def fake_run_worker_request(request):
        return {"mode": request["mode"]}

    monkeypatch.setattr(runner, "_start_worker_watchdog", fake_watchdog)
    monkeypatch.setattr(runner, "_run_worker_request", fake_run_worker_request)

    runner._worker_main()

    output = json.loads(capsys.readouterr().out)
    assert observed["timeout_seconds"] == 2.5
    assert output["ok"] is True
    assert output["result"] == {"mode": "script"}


def test_worker_watchdog_main_exits_after_timeout(monkeypatch):
    observed = {}

    def fake_sleep(seconds):
        observed["sleep"] = seconds

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(runner.time, "sleep", fake_sleep)
    monkeypatch.setattr(runner.os, "_exit", fake_exit)

    with pytest.raises(SystemExit) as exc_info:
        runner._worker_watchdog_main(1.0)

    assert exc_info.value.code == 1
    assert observed["sleep"] == 6.0
