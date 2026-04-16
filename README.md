# amaranth-sim-mcp

`amaranth-sim-mcp` is a minimal MCP server for running Amaranth HDL simulations over stdio.
It exposes two tools:

- `simulate(...)` for either running an existing simulation script unchanged or building a simple generated testbench from a module definition.
- `check_environment()` for reporting Python, Amaranth, and server versions to the client.

## Scope

This v1 stays intentionally small:

- no Docker
- no sandboxing beyond subprocess isolation
- no MCP resources or prompts
- no dependency resolution for user projects

## Local Development

Install dependencies:

```bash
uv sync
```

Run the MCP server:

```bash
uv run amaranth-sim-mcp
```

You can also start it with:

```bash
uv run python -m amaranth_sim_mcp
```

## Published Usage

Once the package is published to PyPI, run it with:

```bash
uvx amaranth-sim-mcp
```

## MCP Client Config

Using the published package:

```json
{
  "mcpServers": {
    "amaranth-sim": {
      "command": "uvx",
      "args": ["amaranth-sim-mcp"]
    }
  }
}
```

Using the local checkout during development:

```json
{
  "mcpServers": {
    "amaranth-sim": {
      "command": "uv",
      "args": ["run", "amaranth-sim-mcp"]
    }
  }
}
```

## Simulation Modes

`simulate(..., mode="script")`

- Runs the target file as `__main__` in a worker subprocess.
- Use this when the project already contains its own `Simulator(...)`, `sim.add_clock(...)`, and `sim.run()` or `sim.run_until(...)` logic.
- Captures `stdout`, `stderr`, and discovered `.vcd` files.

`simulate(..., mode="definitions")`

- Loads the target file in a definitions-only mode.
- Instantiates `class_name(**init_kwargs)`.
- Builds a simple generated testbench using `clocks`, `observe`, `stimulus`, and `cycles`.
- Returns a JSON trace instead of raw script output.

Signal paths are dotted for nested submodules, for example `alu.result`.

Stimulus events use this shape:

```json
{
  "cycle": 3,
  "domain": "sync",
  "set": {
    "en": 1,
    "alu.op": 2
  }
}
```

In v1, `domain` must be omitted or match the selected primary clock domain.
Multi-domain stimulus timing is not synchronized yet.

For `definitions` mode, recorded samples use post-tick semantics: events for cycle `N` are applied before that cycle's tick, and observations for cycle `N` are captured after the tick.

## Import Resolution In Definitions Mode

The loader prepends the user file's directory and, for package-nested files,
the enclosing package root to `sys.path` for both the initial import and the
entire simulation run. Imports from higher up the directory tree or from
sibling projects are not resolved automatically; install those dependencies in
the server's Python environment or restructure the project so imports resolve
from the file's directory or its package root.
