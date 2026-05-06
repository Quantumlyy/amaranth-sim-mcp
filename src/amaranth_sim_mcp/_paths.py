"""Internal path helpers shared by the loader and runner."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator


@contextlib.contextmanager
def temporary_sys_path(entries: list[str]) -> Iterator[None]:
    """Prepend `entries` to `sys.path`, restoring the original list on exit."""
    original = list(sys.path)
    sys.path[:0] = entries
    try:
        yield
    finally:
        sys.path[:] = original
