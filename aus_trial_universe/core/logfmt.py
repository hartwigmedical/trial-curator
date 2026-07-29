"""Shared run-log formatting for the v2 agentic pipeline.

One run -> one log, written for a human to *review*. The log shows, per trial:
the STAGE (via `stage()` banners), and within each stage WHO acted — the doer
agent vs the reviewer agent(s) — as separate, blank-line-separated blocks, one
field per line (never several fields crammed behind a `|`). Verdicts are spelled
out (PASS / FAIL / ADVISORY), not symbols.

Pure string helpers; the caller does the logging (and the blank lines between
blocks). Lean by design — only what a human needs to review a run.
"""
from __future__ import annotations

# Verdict words — spelled out on purpose (clearer than symbols in a log).
PASS = "PASS"
FAIL = "FAIL"
ADVISORY = "ADVISORY"


def stage(name: str) -> str:
    """A top-level stage banner: EXTRACTION / MAPPING / DRUG."""
    return f"▶ {name}"


def kv(label: str, value: str, *, indent: int = 8, pad: int = 17) -> str:
    """A 'label   value' field line — one field per line."""
    return f"{' ' * indent}{label:<{pad}}{value}"


def bullet(text: str, *, indent: int = 8) -> str:
    """A '- text' comment/issue line."""
    return f"{' ' * indent}- {text}"


def line(text: str, *, indent: int = 4) -> str:
    """A plain indented line (list item under a section header)."""
    return f"{' ' * indent}{text}"
