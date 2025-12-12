import logging
from pathlib import Path
from typing import Any

import pydantic_curator.criterion_schema as cs
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _make_shim(class_name: str):
    def __init__(self, *_, **kwargs):
        self.__dict__.update(kwargs)

    return type(class_name, (), {"__init__": __init__})


def _auto_fix_positional_after_keyword(source: str) -> str:
    lines = source.splitlines()
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if "curation=" in line and stripped.endswith("="):
            indent = line[: len(line) - len(line.lstrip())]
            idx = line.index("curation=")
            before = line[:idx]
            after = line[idx + len("curation="):]

            if after.strip() == "":
                j_nonempty = i + 1

                while j_nonempty < n:
                    cand = lines[j_nonempty].strip()
                    if cand == "" or cand.startswith("#"):
                        j_nonempty += 1
                        continue
                    break

                next_stripped = lines[j_nonempty].lstrip() if j_nonempty < n else ""

                if next_stripped.startswith("["):
                    lines[i] = before + "curation=[" + after
                    if lines[j_nonempty].strip() == "[":
                        lines[j_nonempty] = ""
                    i = j_nonempty + 1
                    continue

                lines[i] = before + "curation=[" + after

                j = i + 1
                last_paren: int | None = None

                while j < n:
                    l = lines[j]
                    ls = l.strip()

                    if ls == ")," and len(l) - len(l.lstrip()) <= len(indent):
                        break

                    if ls in (")", "),"):
                        last_paren = j

                    j += 1

                if last_paren is not None:
                    l = lines[last_paren]
                    ls = l.rstrip()
                    if ls.endswith("),"):
                        # ...)-> ...)],  (preserve existing comma)
                        lines[last_paren] = ls[:-2] + ")],"
                    elif ls.endswith(")"):
                        # ...)-> ...)],
                        lines[last_paren] = ls + "],"

                i = j
                continue

        i += 1

    return "\n".join(lines)


def _auto_fix_embedded_criterion_classes(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []

    def _last_nonempty_index() -> int | None:
        for idx in range(len(out) - 1, -1, -1):
            if out[idx].strip() != "":
                return idx
        return None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("class "):
            last_idx = _last_nonempty_index()
            inside_curation = False

            if last_idx is not None and "curation=" in out[last_idx]:
                inside_curation = True

            if not inside_curation:
                out.append(line)
                i += 1
                continue

            i += 1
            removed_list_bracket = False

            while i < len(lines):
                s2 = lines[i].lstrip()

                if s2.startswith("["):
                    removed_list_bracket = True
                    i += 1
                    break

                if s2.startswith(
                    (
                        "AndCriterion(",
                        "OrCriterion(",
                        "NotCriterion(",
                        "IfCriterion(",
                        "TimingCriterion(",
                        "Rule(",
                    )
                ) or ("Criterion(" in s2 and not s2.startswith("class ")):
                    break

                i += 1

            if removed_list_bracket and last_idx is not None:
                prev_line = out[last_idx]
                if "curation=" in prev_line:
                    idx = prev_line.index("curation=")
                    before = prev_line[:idx]
                    after = prev_line[idx + len("curation="):]
                    # Only inject '[' if not already present after '='
                    if after.strip().startswith("["):
                        out[last_idx] = prev_line
                    else:
                        out[last_idx] = before + "curation=[" + after
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


def _auto_fix_dangling_criteria_after_list(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "criteria":
            start = max(0, i - 10)
            window = lines[start:i]
            if any("curation=" in prev for prev in window):
                continue

        out.append(line)

    return "\n".join(out)


def _auto_fix_orphan_helper_assignments(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].lstrip()

        if stripped.startswith("include_criterion ="):
            i += 1
            while i < len(lines):
                s2 = lines[i].lstrip()
                if s2.startswith("criteria_list:"):
                    i += 1
                    break
                i += 1
            continue

        out.append(lines[i])
        i += 1

    return "\n".join(out)


def _auto_fix_extra_closing_brackets(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "]":
            j = len(out) - 1
            prev = None
            while j >= 0:
                if out[j].strip() != "":
                    prev = out[j].rstrip()
                    break
                j -= 1

            if prev is not None and (
                prev.endswith(")]") or prev.endswith(")],") or prev.endswith(")] ,")
            ):
                continue

        out.append(line)

    return "\n".join(out)


def load_curated_rules(py_filepath: Path) -> list[Any] | None:
    module_globs: dict[str, Any] = {"__builtins__": __builtins__}

    for name in dir(cs):
        obj = getattr(cs, name)

        if isinstance(obj, type):
            use_shim = False
            try:
                if issubclass(obj, BaseModel):
                    use_shim = True
            except TypeError:
                pass

            if name.endswith("Criterion"):
                use_shim = True

            module_globs[name] = _make_shim(name) if use_shim else obj
        else:
            module_globs[name] = obj

    def _skip_validation(value: Any, *_, **__) -> Any:
        return value

    module_globs["SkipValidation"] = _skip_validation

    class Rule:
        def __init__(self, **kwargs: Any):
            self.__dict__.update(kwargs)

    module_globs["Rule"] = Rule

    try:
        source = py_filepath.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Could not read %s: %s", py_filepath, e)
        return None

    def _try_exec(src: str, label: str) -> list[Any] | None:
        try:
            code = compile(src, label, "exec")
        except SyntaxError:
            raise

        try:
            exec(code, module_globs)
        except Exception as e:  # defensive
            logger.exception("While executing %s (%s): %s", py_filepath, label, e)
            return None

        rules = module_globs.get("rules")
        if rules is None:
            logger.error("%s (%s) has no `rules` variable", py_filepath, label)
            return None
        return rules

    # 1) Try as-is
    try:
        rules = _try_exec(source, str(py_filepath))
        if rules is not None:
            return rules

    except SyntaxError as e:
        logger.error(
            "SyntaxError in %s:%s\n%s",
            py_filepath,
            e.lineno,
            e.msg,
        )

        # 2) Attempt automatic repairs
        fixed = source
        changed = False

        fixers: list[tuple[str, Any]] = [
            ("positional/list after keyword", _auto_fix_positional_after_keyword),
            ("embedded criterion classes", _auto_fix_embedded_criterion_classes),
            ("dangling 'criteria' tokens", _auto_fix_dangling_criteria_after_list),
            ("orphan helper assignments", _auto_fix_orphan_helper_assignments),
            ("extra closing brackets", _auto_fix_extra_closing_brackets),
        ]

        for label, fixer in fixers:
            new_fixed = fixer(fixed)
            if new_fixed != fixed:
                logger.info("Auto-fix (%s) applied to %s", label, py_filepath)
                fixed = new_fixed
                changed = True

        if not changed:
            logger.error(
                "Auto-fix: no recognised pattern for %s; leaving file unchanged.",
                py_filepath,
            )
            return None

        # 3) Re-try compile/exec with fixed content
        try:
            rules = _try_exec(fixed, "<autofixed>")
        except SyntaxError as e2:
            logger.error(
                "Auto-fix attempt still invalid: %s (%s)",
                e2.msg,
                e2,
            )
            return None

        if rules is not None:
            logger.info(
                "Auto-fix succeeded for %s; using repaired source.",
                py_filepath,
            )
        return rules

    except Exception as e:
        logger.exception("While executing %s: %s", py_filepath, e)
        return None
