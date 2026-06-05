from __future__ import annotations

import logging
import typing
from pathlib import Path
from typing import Any

import pydantic_curator.criterion_schema as cs
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# =============================================================================
# Shims / safe helpers
# =============================================================================

def _make_shim(class_name: str):
    """
    Simple object shim:
    - accepts any kwargs
    - stores them in __dict__
    """
    def __init__(self, *_, **kwargs):
        self.__dict__.update(kwargs)

    return type(class_name, (), {"__init__": __init__})


class _TypingInternalShim:
    """
    A permissive stand-in for typing internal classes/functions that sometimes
    appear in serialized *_overwritten.py files.

    Goal: allow exec() to succeed. We do NOT need correct typing semantics at runtime.
    """

    def __init__(self, *_, **__):
        pass

    def __call__(self, *_, **__):
        return _TypingInternalShim()

    def __getitem__(self, _):
        return _TypingInternalShim()

    def __getattr__(self, _):
        return _TypingInternalShim()

    def __repr__(self) -> str:  # pragma: no cover
        return "_TypingInternalShim()"


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

    for line in lines:
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


def _try_import_typing_extensions():
    try:
        import typing_extensions  # type: ignore
        return typing_extensions
    except Exception:
        return None


# =============================================================================
# Typing internals injection (comprehensive + safe)
# =============================================================================

# A curated list of common private symbols that have shown up in serialized files.
# We prefer SHIMS for these rather than the real typing objects, because:
# - they are private & version-specific
# - real typing internals may reject kwargs (e.g. __doc__) and crash exec()
_TYPING_INTERNAL_NAMES = (
    # Previously seen in your errors
    "_LiteralGenericAlias",
    "_TypedCacheSpecialForm",
    "_SpecialForm",
    # Commonly adjacent internals across versions
    "_SpecialGenericAlias",
    "_GenericAlias",
    "_UnionGenericAlias",
    "_BaseGenericAlias",
    "_CallableGenericAlias",
    "_AnnotatedAlias",
    "_Final",
    "_NotIterable",
)


def _inject_typing_internals(module_globs: dict[str, Any]) -> None:
    """
    Inject permissive shims for typing internals that may appear in serialized files.
    Never overwrites existing bindings.
    """
    te = _try_import_typing_extensions()

    for name in _TYPING_INTERNAL_NAMES:
        if name in module_globs:
            continue

        # Prefer shim for private/internal typing symbols.
        # If you ever need a "real" object for a *public* symbol, bind it explicitly elsewhere.
        module_globs[name] = _make_shim(name)

        # For completeness, also expose the real object under a different name if available
        # (debugging only). We do NOT use it by default.
        real = getattr(typing, name, None)
        if real is None and te is not None:
            real = getattr(te, name, None)
        if real is not None:
            module_globs[f"__REAL_{name}__"] = real


def _try_inject_missing_name_from_exception(module_globs: dict[str, Any], exc: BaseException) -> bool:
    """
    If exec() fails with NameError for a missing symbol, inject it.

    Strategy:
    - if name looks like a typing internal (starts with "_"), inject a shim
    - else try importing from typing / typing_extensions; if not present, shim it anyway

    Returns True if we injected something (caller may retry exec once).
    """
    if not isinstance(exc, NameError):
        return False

    name = getattr(exc, "name", None)
    if not name or not isinstance(name, str):
        msg = str(exc)
        if "name '" in msg and "' is not defined" in msg:
            try:
                name = msg.split("name '", 1)[1].split("'", 1)[0]
            except Exception:
                name = None

    if not name or not isinstance(name, str):
        return False

    if name in module_globs:
        return False

    te = _try_import_typing_extensions()

    if name.startswith("_"):
        module_globs[name] = _make_shim(name)
        logger.info("Injected shim for missing internal symbol %r into exec globals.", name)
        return True

    obj = getattr(typing, name, None)
    if obj is None and te is not None:
        obj = getattr(te, name, None)

    if obj is not None:
        module_globs[name] = obj
        logger.info("Injected missing symbol %r into exec globals (from typing/typing_extensions).", name)
        return True

    # Fallback: shim it
    module_globs[name] = _make_shim(name)
    logger.info("Injected shim for missing symbol %r into exec globals (fallback).", name)
    return True


def _maybe_fix_typing_specialform_typeerror(module_globs: dict[str, Any], exc: BaseException) -> bool:
    """
    Handle the class of errors like:
      TypeError: _SpecialForm.__init__() got an unexpected keyword argument '__doc__'

    This happens when serialized files instantiate typing internals with kwargs that a
    particular Python version's typing internals don't accept.

    Fix: ensure those symbols are shims (not real typing objects) and request a retry.
    """
    if not isinstance(exc, TypeError):
        return False

    msg = str(exc)
    if "_SpecialForm.__init__()" not in msg:
        return False
    if "unexpected keyword argument '__doc__'" not in msg:
        return False

    changed = False
    for name in ("_SpecialForm", "_TypedCacheSpecialForm", "_LiteralGenericAlias"):
        if name not in module_globs or not isinstance(module_globs[name], type):
            # enforce shim
            module_globs[name] = _make_shim(name)
            changed = True

    if changed:
        logger.info("Replaced typing internals with shims after _SpecialForm TypeError; retrying exec once.")
    return changed


# =============================================================================
# Loader
# =============================================================================

def load_curated_rules(py_filepath: Path) -> list[Any] | None:
    module_globs: dict[str, Any] = {"__builtins__": __builtins__}

    # Bring in criterion schema objects, swapping Criterion classes to shims
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
        # KEEP old fixes: inject typing internals (now shim-based and comprehensive)
        _inject_typing_internals(module_globs)

        try:
            code = compile(src, label, "exec")
        except SyntaxError:
            raise

        # Attempt exec; if NameError or known typing TypeError occurs, patch globals and retry once.
        try:
            exec(code, module_globs)
        except Exception as e:  # defensive
            retried = False

            if _try_inject_missing_name_from_exception(module_globs, e):
                retried = True
            elif _maybe_fix_typing_specialform_typeerror(module_globs, e):
                retried = True

            if retried:
                try:
                    exec(code, module_globs)
                except Exception as e2:
                    logger.exception("While executing %s (%s): %s", py_filepath, label, e2)
                    return None
            else:
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

        # 2) Attempt automatic repairs (KEEP old fixes)
        fixed = source
        changed = False

        fixers: list[tuple[str, Any]] = [
            ("positional/list after keyword", _auto_fix_positional_after_keyword),
            ("embedded criterion classes", _auto_fix_embedded_criterion_classes),
            ("dangling 'criteria' tokens", _auto_fix_dangling_criteria_after_list),
            ("orphan helper assignments", _auto_fix_orphan_helper_assignments),
            ("extra closing brackets", _auto_fix_extra_closing_brackets),
        ]

        for lbl, fixer in fixers:
            new_fixed = fixer(fixed)
            if new_fixed != fixed:
                logger.info("Auto-fix (%s) applied to %s", lbl, py_filepath)
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
            logger.info("Auto-fix succeeded for %s; using repaired source.", py_filepath)
        return rules

    except Exception as e:
        logger.exception("While executing %s: %s", py_filepath, e)
        return None
