"""Simulation runner and worker subprocess entrypoint."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import runpy
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from amaranth.hdl import Value, ValueCastable
from amaranth.hdl._ir import Fragment
from amaranth.sim import Simulator

from ._paths import temporary_sys_path
from .errors import SimulationRequestError
from .loader import load_definitions_module, select_elaboratable_class

DEFAULT_SYNC_PERIOD_SECONDS = 1e-6
DEFAULT_CLOCKS: dict[str, float] = {"sync": DEFAULT_SYNC_PERIOD_SECONDS}
DEFAULT_CYCLES = 100
WORKER_TIMEOUT_SECONDS = 30.0
WATCHDOG_GRACE_SECONDS = 5.0
VCD_RANDOM_SUFFIX_BYTES = 4

logger = logging.getLogger("amaranth_sim_mcp")


@dataclass(frozen=True)
class ResolvedAssignment:
    path: str
    target: Any
    value: Any


@dataclass(frozen=True)
class ResolvedStimulusEvent:
    cycle: int
    domain: str | None
    assignments: tuple[ResolvedAssignment, ...]


def run_simulation_request(
    file_path: str | os.PathLike[str],
    mode: str,
    class_name: str | None = None,
    init_kwargs: Mapping[str, Any] | None = None,
    clocks: Mapping[str, float] | None = None,
    observe: list[str] | None = None,
    stimulus: Sequence[Mapping[str, Any]] | None = None,
    cycles: int = DEFAULT_CYCLES,
    *,
    timeout_seconds: float = WORKER_TIMEOUT_SECONDS,
    vcd_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Run a simulation request in an isolated worker subprocess.

    `vcd_dir` controls where definitions-mode VCDs are written. If `None`,
    the system temp dir is used (paths are leaked to the caller's
    responsibility); pass a server-owned directory if you want lifetime
    bounded by the calling process.
    """

    path = (Path.cwd() / Path(file_path).expanduser()).resolve()

    if not path.is_file():
        raise SimulationRequestError(f"File not found: {path}")
    if path.suffix != ".py":
        raise SimulationRequestError(f"File must be a .py file: {path}")
    if mode not in {"script", "definitions"}:
        raise SimulationRequestError("mode must be either 'script' or 'definitions'.")
    if cycles < 0:
        raise SimulationRequestError("cycles must be greater than or equal to 0.")

    logger.info(
        "starting simulation: file=%s mode=%s cycles=%d timeout=%.1fs",
        path,
        mode,
        cycles,
        timeout_seconds,
    )

    # Resolve vcd_dir to an absolute path: the worker is launched with
    # cwd=design.parent, so a relative or `~/...` value would be
    # interpreted against that and the returned VCD path would be
    # un-openable from the parent's cwd.
    if vcd_dir is not None:
        resolved_vcd_dir: str | None = str((Path.cwd() / Path(vcd_dir).expanduser()).resolve())
    else:
        resolved_vcd_dir = None

    request = {
        "file_path": str(path),
        "mode": mode,
        "class_name": class_name,
        "init_kwargs": dict(init_kwargs or {}),
        "clocks": dict(DEFAULT_CLOCKS if clocks is None else clocks),
        "observe": list(observe) if observe is not None else None,
        "stimulus": list(stimulus or []),
        "cycles": cycles,
        "timeout_seconds": timeout_seconds,
        "vcd_dir": resolved_vcd_dir,
    }

    progress_path = _create_progress_file()
    request["progress_path"] = str(progress_path)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "amaranth_sim_mcp.runner", "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(path.parent),
        )
        try:
            stdout, stderr = proc.communicate(
                json.dumps(request),
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            logger.info("simulation timed out after %.1fs; killing worker", timeout_seconds)
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.communicate(timeout=1)
            last_cycle = _read_progress(progress_path)
            raise SimulationRequestError(
                _format_timeout_message(timeout_seconds, last_cycle)
            ) from exc

        payload = _parse_worker_response(stdout or "", stderr or "", proc.returncode)
        if not payload.get("ok", False):
            error = payload.get("error", {})
            message = error.get("message", "Simulation failed.")
            traceback_text = error.get("traceback")
            if traceback_text:
                message = f"{message}\n\nTraceback:\n{traceback_text.rstrip()}"
            raise SimulationRequestError(message)
        return cast(dict[str, Any], payload["result"])
    finally:
        progress_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Internal amaranth-sim-mcp worker.")
    parser.add_argument("--worker", action="store_true", help="Run the simulation worker.")
    args = parser.parse_args(argv)

    if not args.worker:
        raise SystemExit("This module is internal. Start the MCP server with `amaranth-sim-mcp`.")

    _worker_main()


def _worker_main() -> None:
    # The parent uses our stdout for the JSON response payload, so any
    # incidental writes (logging the user configured to sys.stdout, prints
    # from elaborate(), etc.) must not reach it. Reserve the real stdout
    # and point sys.stdout at sys.stderr while the request runs; restore
    # it for the final write.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        raw_request = sys.stdin.read()
        try:
            request = json.loads(raw_request)
            timeout_seconds = _parse_worker_timeout(request.get("timeout_seconds"))
            _start_worker_watchdog(timeout_seconds)
            result = _run_worker_request(request)
            payload = {"ok": True, "result": result}
        except SimulationRequestError as exc:
            payload = {"ok": False, "error": {"message": str(exc)}}
        except Exception as exc:  # pragma: no cover - defensive fallback
            payload = {
                "ok": False,
                "error": {
                    "message": f"Unhandled simulation error: {type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
            }
    finally:
        sys.stdout = real_stdout

    real_stdout.write(json.dumps(payload))
    real_stdout.flush()


def _run_worker_request(request: Mapping[str, Any]) -> dict[str, Any]:
    mode = request.get("mode")
    if mode == "script":
        return _run_script_mode(request)
    if mode == "definitions":
        return _run_definitions_mode(request)
    raise SimulationRequestError("mode must be either 'script' or 'definitions'.")


def _run_script_mode(request: Mapping[str, Any]) -> dict[str, Any]:
    file_path = Path(str(request["file_path"])).resolve(strict=True)
    cwd = file_path.parent
    before = _snapshot_vcds(cwd)
    stdout = io.StringIO()
    stderr = io.StringIO()
    started_at = time.perf_counter()

    try:
        with (
            _temporary_cwd(cwd),
            temporary_sys_path([str(cwd)]),
            _temporary_argv([str(file_path)]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            runpy.run_path(str(file_path), run_name="__main__")
    except Exception as exc:
        after = _snapshot_vcds(cwd)
        vcd_paths = _collect_vcd_changes(before, after)
        raise SimulationRequestError(
            _format_script_error(exc, stdout.getvalue(), stderr.getvalue(), vcd_paths)
        ) from exc

    after = _snapshot_vcds(cwd)
    duration = time.perf_counter() - started_at
    return {
        "mode": "script",
        "file_path": str(file_path),
        "cwd": str(cwd),
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "vcd_paths": _collect_vcd_changes(before, after),
        "duration_seconds": duration,
    }


def _run_definitions_mode(request: Mapping[str, Any]) -> dict[str, Any]:
    file_path = Path(str(request["file_path"])).resolve(strict=True)
    class_name = request.get("class_name")
    init_kwargs = dict(request.get("init_kwargs") or {})
    raw_clocks = request.get("clocks")
    clocks = dict(DEFAULT_CLOCKS if raw_clocks is None else raw_clocks)
    observe = request.get("observe")
    stimulus = list(request.get("stimulus") or [])
    cycles = int(request.get("cycles", DEFAULT_CYCLES))
    progress_path = Path(str(request["progress_path"]))

    if not clocks:
        raise SimulationRequestError("definitions mode requires at least one clock domain.")

    started_at = time.perf_counter()
    try:
        module, loader_path_entries = load_definitions_module(file_path)
    except SimulationRequestError:
        raise
    except SyntaxError as exc:
        raise SimulationRequestError(
            f"Syntax error in '{file_path}' at line {exc.lineno}: {exc.msg}"
        ) from exc
    except Exception as exc:
        raise SimulationRequestError(
            f"Failed to load '{file_path}': {type(exc).__name__}: {exc}"
        ) from exc

    with _temporary_cwd(file_path.parent), temporary_sys_path(loader_path_entries):
        try:
            dut_class = select_elaboratable_class(module, class_name)
        except ValueError as exc:
            raise SimulationRequestError(str(exc)) from exc

        try:
            dut = dut_class(**init_kwargs)
        except Exception as exc:
            raise SimulationRequestError(
                f"Failed to instantiate '{dut_class.__name__}': {type(exc).__name__}: {exc}"
            ) from exc

        available_domains = _available_domains(dut)
        _validate_clock_domains(clocks, available_domains)
        primary_domain = "sync" if "sync" in clocks else next(iter(clocks))

        observe_paths = list(observe) if observe is not None else _default_observe_paths(dut)
        observed_targets = {path: _resolve_signal_path(dut, path) for path in observe_paths}
        for path, target in observed_targets.items():
            _validate_signal_target(dut, path, target)

        resolved_events = _resolve_stimulus_events(dut, stimulus, primary_domain)

        sim = Simulator(dut)
        for domain, period in clocks.items():
            sim.add_clock(period, domain=domain)
        vcd_suffix = os.urandom(VCD_RANDOM_SUFFIX_BYTES).hex()
        raw_vcd_dir = request.get("vcd_dir")
        vcd_root = Path(str(raw_vcd_dir)) if raw_vcd_dir else Path(tempfile.gettempdir())
        try:
            vcd_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SimulationRequestError(
                f"Failed to create VCD directory '{vcd_root}': {type(exc).__name__}: {exc}"
            ) from exc
        vcd_path = str(vcd_root / f"amaranth_sim_{vcd_suffix}.vcd")
        logger.debug(
            "definitions mode: vcd=%s observed=%d signals", vcd_path, len(observed_targets)
        )

        trace: list[dict[str, Any]] = []

        async def testbench(ctx: Any) -> None:
            for cycle in range(cycles):
                for event in resolved_events.get(cycle, ()):
                    for assignment in event.assignments:
                        try:
                            ctx.set(assignment.target, assignment.value)
                        except Exception as exc:
                            raise SimulationRequestError(
                                f"Failed to set signal '{assignment.path}': "
                                f"{type(exc).__name__}: {exc}"
                            ) from exc

                await ctx.tick(primary_domain)

                signals: dict[str, Any] = {}
                for path, target in observed_targets.items():
                    try:
                        signals[path] = _normalize_observed_value(ctx.get(target))
                    except Exception as exc:
                        raise SimulationRequestError(
                            f"Failed to observe signal '{path}': {type(exc).__name__}: {exc}"
                        ) from exc

                trace.append(
                    {
                        "cycle": cycle,
                        "domain": primary_domain,
                        "signals": signals,
                    }
                )
                _write_progress(progress_path, cycle)

        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_path):
            sim.run()

    duration = time.perf_counter() - started_at
    return {
        "mode": "definitions",
        "file_path": str(file_path),
        "class_name": dut_class.__name__,
        "primary_domain": primary_domain,
        "clocks": clocks,
        "cycles": cycles,
        "observe": observe_paths,
        "trace": trace,
        "vcd_path": vcd_path,
        "duration_seconds": duration,
    }


def _resolve_stimulus_events(
    dut: Any,
    stimulus: list[Mapping[str, Any]],
    primary_domain: str,
) -> dict[int, tuple[ResolvedStimulusEvent, ...]]:
    events_by_cycle: dict[int, list[ResolvedStimulusEvent]] = defaultdict(list)
    for index, raw_event in enumerate(stimulus):
        cycle = raw_event.get("cycle")
        if not isinstance(cycle, int) or cycle < 0:
            raise SimulationRequestError(
                f"Stimulus event at index {index} has invalid cycle {cycle!r}; "
                "cycle must be a non-negative integer."
            )

        assignments: list[ResolvedAssignment] = []
        raw_assignments = raw_event.get("set")
        if not isinstance(raw_assignments, Mapping) or not raw_assignments:
            raise SimulationRequestError(
                f"Stimulus event at cycle {cycle} must include a non-empty 'set' mapping."
            )

        for path, value in raw_assignments.items():
            if not isinstance(path, str) or not path:
                raise SimulationRequestError(
                    f"Stimulus event at cycle {cycle} contains an invalid signal path {path!r}."
                )
            target = _resolve_signal_path(dut, path)
            _validate_signal_target(dut, path, target)
            assignments.append(ResolvedAssignment(path=path, target=target, value=value))

        domain = raw_event.get("domain")
        if domain is not None and not isinstance(domain, str):
            raise SimulationRequestError(
                f"Stimulus event at cycle {cycle} has invalid domain {domain!r}; "
                "domain must be a string if provided."
            )
        normalized_domain = primary_domain if domain is None else domain
        if normalized_domain != primary_domain:
            raise SimulationRequestError(
                f"Stimulus event at cycle {cycle} targets domain '{normalized_domain}', "
                f"but only the primary domain '{primary_domain}' is currently supported "
                "for stimulus timing."
            )

        events_by_cycle[cycle].append(
            ResolvedStimulusEvent(
                cycle=cycle,
                domain=normalized_domain,
                assignments=tuple(assignments),
            )
        )

    return {cycle: tuple(events) for cycle, events in events_by_cycle.items()}


def _available_domains(dut: Any) -> list[str]:
    fragment = Fragment.get(dut, None)
    return sorted(fragment.domains.keys())


def _validate_clock_domains(clocks: Mapping[str, float], available_domains: list[str]) -> None:
    available = ", ".join(available_domains) or "none"
    for domain, period in clocks.items():
        if not isinstance(domain, str) or not domain:
            raise SimulationRequestError(f"Invalid clock domain name {domain!r}.")
        if not isinstance(period, (int, float)) or period <= 0:
            raise SimulationRequestError(
                f"Clock period for domain '{domain}' must be a positive number."
            )
        if domain not in available_domains:
            raise SimulationRequestError(
                f"Clock domain '{domain}' was not found on the DUT. Available domains: {available}."
            )


def _default_observe_paths(dut: Any) -> list[str]:
    signal_names, _ = _public_attribute_names(dut)
    return signal_names


def _resolve_signal_path(dut: Any, signal_path: str) -> Any:
    current = dut
    for part in signal_path.split("."):
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise SimulationRequestError(_missing_signal_message(signal_path, current)) from exc
    return current


def _validate_signal_target(dut: Any, signal_path: str, target: Any) -> None:
    if not isinstance(target, (Value, ValueCastable)):
        raise SimulationRequestError(
            f"Signal path '{signal_path}' resolved to an unsupported object of type "
            f"{type(target).__name__}. Available attributes: {_format_available_attributes(dut)}."
        )


def _missing_signal_message(signal_path: str, obj: Any) -> str:
    return (
        f"Signal path '{signal_path}' could not be resolved. "
        f"Available attributes: {_format_available_attributes(obj)}."
    )


def _format_available_attributes(obj: Any) -> str:
    signal_names, other_names = _public_attribute_names(obj)
    names = signal_names + other_names
    return ", ".join(names) if names else "none"


def _public_attribute_names(dut: Any) -> tuple[list[str], list[str]]:
    ordered_names: list[str] = []
    seen: set[str] = set()

    for name in vars(dut):
        if name.startswith("_") or name in seen:
            continue
        seen.add(name)
        ordered_names.append(name)

    for name in dir(dut):
        if name.startswith("_") or name in seen:
            continue
        seen.add(name)
        ordered_names.append(name)

    signal_names: list[str] = []
    other_names: list[str] = []
    for name in ordered_names:
        try:
            value = getattr(dut, name)
        except Exception:
            other_names.append(name)
            continue
        if isinstance(value, (Value, ValueCastable)):
            signal_names.append(name)
        else:
            other_names.append(name)

    return signal_names, other_names


def _normalize_observed_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return repr(value)


def _snapshot_vcds(root: Path) -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for path in root.rglob("*.vcd"):
        if path.is_file():
            snapshot[str(path.resolve())] = path.stat().st_mtime_ns
    return snapshot


def _collect_vcd_changes(before: Mapping[str, int], after: Mapping[str, int]) -> list[str]:
    changed: list[str] = []
    for path, mtime in after.items():
        if path not in before or before[path] != mtime:
            changed.append(path)
    return sorted(changed)


def _format_script_error(
    exc: Exception,
    stdout: str,
    stderr: str,
    vcd_paths: list[str],
) -> str:
    lines = [f"Script simulation failed: {type(exc).__name__}: {exc}"]
    if stdout:
        lines.append("")
        lines.append("Captured stdout:")
        lines.append(stdout.rstrip())
    if stderr:
        lines.append("")
        lines.append("Captured stderr:")
        lines.append(stderr.rstrip())
    if vcd_paths:
        lines.append("")
        lines.append("Discovered VCD files:")
        lines.extend(vcd_paths)
    return "\n".join(lines)


def _parse_worker_response(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    if stdout.strip():
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and _is_well_formed_worker_response(payload):
            return payload

    message = stderr.strip() or stdout.strip() or f"Worker exited with return code {returncode}."
    return {"ok": False, "error": {"message": message}}


def _is_well_formed_worker_response(payload: Mapping[str, Any]) -> bool:
    ok = payload.get("ok")
    if not isinstance(ok, bool):
        return False
    if ok:
        return isinstance(payload.get("result"), dict)
    return isinstance(payload.get("error"), dict)


def _create_progress_file() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="amaranth-sim-mcp-progress-", suffix=".txt")
    os.close(fd)
    return Path(raw_path)


def _write_progress(progress_path: Path, cycle: int) -> None:
    progress_path.write_text(str(cycle), encoding="utf-8")


def _read_progress(progress_path: Path) -> int | None:
    try:
        content = progress_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not content:
        return None
    try:
        return int(content)
    except ValueError:
        return None


def _format_timeout_message(timeout_seconds: float, last_cycle: int | None) -> str:
    seconds = _format_seconds(timeout_seconds)
    if last_cycle is None:
        return f"Simulation timed out after {seconds}."
    return f"Simulation timed out after {seconds}. Last completed cycle: {last_cycle}."


def _format_seconds(timeout_seconds: float) -> str:
    if float(timeout_seconds).is_integer():
        return f"{int(timeout_seconds)}s"
    return f"{timeout_seconds:.3f}".rstrip("0").rstrip(".") + "s"


def _parse_worker_timeout(raw_timeout: Any) -> float:
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        return WORKER_TIMEOUT_SECONDS
    if timeout_seconds < 0:
        return WORKER_TIMEOUT_SECONDS
    return timeout_seconds


def _start_worker_watchdog(timeout_seconds: float) -> None:
    watchdog = threading.Thread(
        target=_worker_watchdog_main,
        args=(timeout_seconds,),
        daemon=True,
        name="amaranth-sim-mcp-watchdog",
    )
    watchdog.start()


def _worker_watchdog_main(timeout_seconds: float) -> None:
    time.sleep(max(timeout_seconds, 0.0) + WATCHDOG_GRACE_SECONDS)
    logger.warning(
        "watchdog firing after %.1fs (configured timeout %.1fs); calling os._exit(1)",
        max(timeout_seconds, 0.0) + WATCHDOG_GRACE_SECONDS,
        timeout_seconds,
    )
    os._exit(1)


@contextlib.contextmanager
def _temporary_cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def _temporary_argv(argv: list[str]) -> Iterator[None]:
    original = list(sys.argv)
    sys.argv[:] = argv
    try:
        yield
    finally:
        sys.argv[:] = original


__all__ = [
    "DEFAULT_CLOCKS",
    "DEFAULT_CYCLES",
    "WORKER_TIMEOUT_SECONDS",
    "SimulationRequestError",
    "main",
    "run_simulation_request",
]


if __name__ == "__main__":
    main()
