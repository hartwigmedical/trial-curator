"""Shared run-log formatting for the v2 agentic pipeline.

One run -> one log. These helpers keep that log aligned and scannable across the
orchestrator (run.py) and the task workflows, so a reviewer can see at a glance:
the STAGE (via `stage()` banners), and within a stage WHO spoke — the doer agent
vs the reviewer agent(s) — via a fixed-width left role gutter (`role()` / `cont()`).

Pure string helpers: the caller does the logging. Lean by design — only what a
human needs to review a run belongs in the log.
"""
from __future__ import annotations

GUTTER = 10  # width of the left role column ("reviewer" is the widest label)

# Verdict marks (doer/reviewer/validator outcomes).
OK = "✓"      # faithful / passed
FAIL = "✗"    # gating failure
WARN = "⚠"    # advisory (non-gating) note


def stage(name: str) -> str:
    """A top-level stage banner: EXTRACTION / MAPPING / DRUG."""
    return f"▶ {name}"


def role(label: str, content: str = "") -> str:
    """A role line: fixed-width label gutter + content (e.g. 'doer', 'reviewer')."""
    return f"{label:<{GUTTER}}{content}"


def cont(content: str) -> str:
    """A continuation/detail line, indented under the role content column."""
    return f"{'':<{GUTTER}}{content}"
