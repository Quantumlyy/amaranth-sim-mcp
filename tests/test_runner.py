from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from amaranth_sim_mcp import runner
from amaranth_sim_mcp.runner import SimulationRequestError, run_simulation_request


def test_run_simulation_request_script_mode_captures_output_and_vcd(tmp_path, write_design):
    script_path = write_design(
        "script_sim.py",
        """\
        import sys
        from pathlib import Path

        print("hello stdout")
        print("hello stderr", file=sys.stderr)
        Path("wave.vcd").write_text("$date now $end", encoding="utf-8")
        """,
    )

    result = run_simulation_request(script_path, "script")

    assert result["mode"] == "script"
    assert result["file_path"] == str(script_path.resolve())
    assert result["cwd"] == str(tmp_path.resolve())
    assert result["stdout"].strip() == "hello stdout"
    assert result["stderr"].strip() == "hello stderr"
    assert result["vcd_paths"] == [str((tmp_path / "wave.vcd").resolve())]


def test_run_simulation_request_definitions_mode_auto_observes_public_signals(
    counter_design, cleanup_vcd
):
    result = run_simulation_request(counter_design, "definitions", cycles=2)
    cleanup_vcd(result["vcd_path"])

    assert sorted(result["observe"]) == ["count", "en"]
    assert all(set(sample["signals"]) == {"count", "en"} for sample in result["trace"])


def test_run_simulation_request_definitions_mode_returns_post_tick_trace(
    counter_design, cleanup_vcd
):
    result = run_simulation_request(
        counter_design,
        "definitions",
        observe=["count"],
        stimulus=[
            {"cycle": 0, "set": {"en": 1}},
            {"cycle": 3, "set": {"en": 0}},
        ],
        cycles=5,
    )
    cleanup_vcd(result["vcd_path"])

    assert result["mode"] == "definitions"
    assert result["class_name"] == "Counter"
    assert result["primary_domain"] == "sync"
    assert result["vcd_path"].endswith(".vcd")
    assert result["vcd_path"]
    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3, 3, 3]
    assert Path(result["vcd_path"]).is_file()


def test_run_simulation_request_definitions_mode_supports_loose_file_sibling_imports(
    top_level_import_design, cleanup_vcd
):
    result = run_simulation_request(
        top_level_import_design,
        "definitions",
        observe=["count"],
        cycles=3,
    )
    cleanup_vcd(result["vcd_path"])

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]


def test_run_simulation_request_definitions_mode_supports_lazy_imports_during_simulation(
    lazy_import_design, cleanup_vcd
):
    result = run_simulation_request(
        lazy_import_design,
        "definitions",
        observe=["count"],
        cycles=3,
    )
    cleanup_vcd(result["vcd_path"])

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]


def test_run_simulation_request_rejects_non_python_files(tmp_path):
    design_path = tmp_path / "counter.txt"
    design_path.write_text("print('hello')\n", encoding="utf-8")

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "script")

    assert str(exc_info.value) == f"File must be a .py file: {design_path.resolve()}"


@pytest.mark.parametrize(
    ("design_fixture", "extra_kwargs", "expected_substrings", "forbidden_substrings"),
    [
        pytest.param(
            "counter_design",
            {"observe": ["missing"]},
            ("Signal path 'missing' could not be resolved.", "en", "count"),
            (),
            id="missing-observe-signal",
        ),
        pytest.param(
            "counter_design",
            {"stimulus": [{"cycle": 0, "set": {"missing": 1}}]},
            ("Signal path 'missing' could not be resolved.", "en", "count"),
            (),
            id="missing-stimulus-signal",
        ),
        pytest.param(
            "counter_design",
            {"stimulus": [{"cycle": 0, "domain": "usb", "set": {"en": 1}}]},
            ("targets domain 'usb'", "primary domain 'sync'"),
            (),
            id="non-primary-stimulus-domain",
        ),
        pytest.param(
            "nested_design",
            {"class_name": "Wrapper", "observe": ["alu.bad_sig"]},
            (
                "Signal path 'alu.bad_sig' could not be resolved.",
                "Available attributes:",
                "result",
                "valid",
            ),
            ("en",),
            id="nested-observe-signal",
        ),
    ],
)
def test_definitions_mode_error_includes_context(
    request,
    design_fixture: str,
    extra_kwargs: dict,
    expected_substrings: tuple[str, ...],
    forbidden_substrings: tuple[str, ...],
):
    design_path = request.getfixturevalue(design_fixture)

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "definitions", **extra_kwargs)

    message = str(exc_info.value)
    for substring in expected_substrings:
        assert substring in message, f"missing {substring!r} in {message!r}"
    for substring in forbidden_substrings:
        assert substring not in message, f"unexpected {substring!r} in {message!r}"


def test_run_simulation_request_reports_structured_missing_import_errors(tmp_path, write_design):
    design_path = write_design(
        "main.py",
        """\
        import nonexistent_module_xyz
        from amaranth import Elaboratable, ClockDomain, Module

        class Counter(Elaboratable):
            def elaborate(self, platform):
                m = Module()
                m.domains.sync = ClockDomain()
                return m
        """,
    )

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "definitions")

    message = str(exc_info.value)
    assert "Failed to import" in message
    assert "nonexistent_module_xyz" in message
    assert "sys.path entries added by the loader:" in message
    assert str(tmp_path.resolve()) in message
    assert "install it into the Python environment used to run the server" in message


def test_run_simulation_request_reports_structured_syntax_errors(write_design):
    design_path = write_design(
        "main.py",
        """\
        from amaranth import Elaboratable

        class Counter(Elaboratable)
            pass
        """,
    )

    with pytest.raises(SimulationRequestError) as exc_info:
        run_simulation_request(design_path, "definitions")

    message = str(exc_info.value)
    assert f"Syntax error in '{design_path.resolve()}'" in message
    assert "line 3" in message


def test_run_simulation_request_definitions_mode_supports_nested_package_layouts(
    package_import_design, cleanup_vcd
):
    result = run_simulation_request(
        package_import_design,
        "definitions",
        observe=["count"],
        cycles=3,
    )
    cleanup_vcd(result["vcd_path"])

    assert [sample["signals"]["count"] for sample in result["trace"]] == [1, 2, 3]


def test_run_simulation_request_resolves_relative_vcd_dir(
    counter_design, cleanup_vcd, tmp_path, monkeypatch
):
    """A relative `vcd_dir` must be resolved against the caller's cwd, not the worker's."""
    output_root = tmp_path / "outputs"
    monkeypatch.chdir(tmp_path)

    result = run_simulation_request(
        counter_design,
        "definitions",
        cycles=1,
        vcd_dir="outputs",
    )
    cleanup_vcd(result["vcd_path"])

    vcd_path = Path(result["vcd_path"])
    assert vcd_path.is_absolute()
    assert vcd_path.is_file()
    assert vcd_path.parent == output_root.resolve()


def test_run_simulation_request_expands_user_in_vcd_dir(
    counter_design, cleanup_vcd, tmp_path, monkeypatch
):
    """`~/...` in `vcd_dir` must be expanded before being sent to the worker."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = run_simulation_request(
        counter_design,
        "definitions",
        cycles=1,
        vcd_dir="~/vcds",
    )
    cleanup_vcd(result["vcd_path"])

    vcd_path = Path(result["vcd_path"])
    assert vcd_path.is_absolute()
    assert vcd_path.is_file()
    assert vcd_path.parent == (home / "vcds").resolve()


def test_run_simulation_request_timeout_reports_last_completed_cycle(
    monkeypatch, tmp_path, fast_counter_design
):
    design_path = fast_counter_design

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


def test_worker_main_isolates_incidental_stdout_writes(monkeypatch, capsys):
    """A worker stdout write must not leak into the JSON payload channel."""
    monkeypatch.setattr(
        runner.sys,
        "stdin",
        io.StringIO(json.dumps({"mode": "definitions"})),
    )
    monkeypatch.setattr(runner, "_start_worker_watchdog", lambda _: None)

    def fake_run_worker_request(request):
        # Simulates user code (or logging configured to sys.stdout)
        # writing to stdout from inside the worker.
        sys.stdout.write("noisy log line\n")
        return {"mode": request["mode"]}

    monkeypatch.setattr(runner, "_run_worker_request", fake_run_worker_request)

    runner._worker_main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"ok": True, "result": {"mode": "definitions"}}
    assert "noisy log line" in captured.err


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
