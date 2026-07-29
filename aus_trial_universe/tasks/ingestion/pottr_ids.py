"""POTTR-listed trial IDs, id-aliases, and the manual removal list — self-contained (no eligibility_path).

POTTR (https://github.com/fpylin/POTTR) publishes the AU trials that must always reach the final eligibility
output. The download/extract steps use this set to broaden the CTGov query (id-append) and to exempt POTTR trials
from downstream drug-intervention filters so they are never dropped. This is a faithful port of the loaders in
the legacy ``eligibility_path/shared/trial_resource/combined_trial_resource_export.py`` plus the manual
``trials_to_remove`` list, with pandas/urllib swapped for a lean ``requests``-or-``urllib`` fetch + ``csv`` parse.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import urllib.request
from pathlib import Path
from typing import Iterator

from aus_trial_universe.core.paths import CTGOV_ROOT, CURRENT_VERSION

logger = logging.getLogger(__name__)

# `requests` is a declared project dependency; fall back to urllib so this module stays importable without it.
try:  # pragma: no cover - trivial import guard
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

# POTTR AU source TSVs (GitHub blob URLs, normalized to raw.githubusercontent.com before fetching).
DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/trial_eligibility.AU.tsv"
)
DEFAULT_POTTR_TRIAL_REGISTRY_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/trial_registry.AU.tsv"
)

# The requested->canonical POTTR id-alias table written by the CTGov downloader (ctgov.write_ctgov_current_version).
POTTR_ID_ALIASES_FILENAME = "02b_pottr_id_aliases_ctgov.tsv"

_WS_RE = re.compile(r"\s+")
_HTTP_TIMEOUT = 30  # seconds

# --------------------------------------------------------------------------- #
# Manual removal list
# --------------------------------------------------------------------------- #
# Non-cancer trials captured by API_CONDITIONS_POTTR that cannot be excluded in the query without also dropping
# legitimate POTTR trials (verbatim from aus_trial_universe/trials_to_remove/trials_to_remove.py).
TRIALS_TO_REMOVE = [
    "NCT06370351",
    "NCT06461286",
    "NCT06970106",
    "NCT05171075",
]


def _clean_text(value: object) -> str:
    """Strip, collapse internal whitespace, and treat None / "nan" as empty."""
    if value is None:
        return ""
    text = _WS_RE.sub(" ", str(value).strip())
    if not text or text.lower() == "nan":
        return ""
    return text


def normalize_trial_id(raw: object) -> str:
    """Canonical trial-id form: cleaned + upper-cased (e.g. ``NCT01234567`` / ``ACTRN12345678901234``)."""
    return _clean_text(raw).upper()


def infer_pottr_trial_registry(trial_id: object) -> str:
    """Registry a trial id belongs to: ``"ctgov"`` (NCT…), ``"anzctr"`` (ACTRN…), else ``""``."""
    text = normalize_trial_id(trial_id)
    if text.startswith("NCT"):
        return "ctgov"
    if text.startswith("ACTRN"):
        return "anzctr"
    return ""


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _normalize_github_tsv_url(value: str) -> str:
    """Rewrite a ``github.com/.../blob/...`` URL to its raw ``raw.githubusercontent.com`` form (else unchanged)."""
    text = _clean_text(value)
    if text.startswith("https://github.com/") and "/blob/" in text:
        return text.replace(
            "https://github.com/", "https://raw.githubusercontent.com/"
        ).replace("/blob/", "/", 1)
    return text


def _http_get_text(url: str) -> str:
    """GET a URL as text, preferring ``requests`` and falling back to ``urllib``."""
    if requests is not None:
        response = requests.get(url, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.text
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310 - public POTTR data over https
        return response.read().decode("utf-8")


def _iter_trial_ids(text: str) -> Iterator[str]:
    """Yield normalized non-empty ``trial_id`` values from a tab-delimited POTTR source."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None or "trial_id" not in reader.fieldnames:
        raise ValueError(
            f"POTTR source must contain a 'trial_id' column. Found: {reader.fieldnames}"
        )
    for row in reader:
        trial_id = normalize_trial_id(row.get("trial_id", ""))
        if trial_id:
            yield trial_id


def _load_trial_ids_from_source(source: str | Path, *, label: str) -> set[str]:
    normalized = _normalize_github_tsv_url(str(source))
    if _is_url(normalized):
        text = _http_get_text(normalized)
    else:
        text = Path(normalized).read_text(encoding="utf-8")
    return set(_iter_trial_ids(text))


def load_pottr_trial_ids(
    registry: str | None = None,
    *,
    pottr_trial_eligibility_url: str | Path = DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL,
    pottr_trial_registry_url: str | Path = DEFAULT_POTTR_TRIAL_REGISTRY_URL,
) -> set[str]:
    """Return the set of normalized POTTR-listed trial IDs (union of the eligibility + registry TSVs).

    When ``registry`` is ``"ctgov"`` or ``"anzctr"``, only IDs mapping to that registry are returned. Raises on any
    fetch/parse error — use :func:`load_pottr_trial_ids_best_effort` for the pipeline's degrade-gracefully path.
    """
    ids: set[str] = set()
    for source, label in (
        (pottr_trial_eligibility_url, "trial eligibility"),
        (pottr_trial_registry_url, "trial registry"),
    ):
        ids.update(_load_trial_ids_from_source(source, label=label))
    if registry is not None:
        ids = {tid for tid in ids if infer_pottr_trial_registry(tid) == registry}
    return ids


def load_pottr_trial_ids_best_effort(
    registry: str | None = None,
    *,
    pottr_trial_eligibility_url: str | Path = DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL,
    pottr_trial_registry_url: str | Path = DEFAULT_POTTR_TRIAL_REGISTRY_URL,
) -> set[str]:
    """Load POTTR trial IDs, degrading to an empty set on any network/parse error (logs a warning).

    A transient POTTR-source failure then becomes "no exemption / no id-append this run" rather than a crash; any
    POTTR trial dropped as a result is still reported as missing by the downstream coverage audit.
    """
    try:
        return load_pottr_trial_ids(
            registry,
            pottr_trial_eligibility_url=pottr_trial_eligibility_url,
            pottr_trial_registry_url=pottr_trial_registry_url,
        )
    except Exception as error:  # noqa: BLE001 - best-effort, must never block ingestion
        logger.warning(
            "Could not load POTTR trial list (%s); proceeding without POTTR this run. "
            "POTTR trials may be absent and reported as missing by the coverage audit.",
            error,
        )
        return set()


def load_pottr_id_aliases(path: str | Path | None = None) -> dict[str, str]:
    """Load the requested->canonical POTTR id-alias map written at download time.

    ClinicalTrials.gov can serve a requested (POTTR-listed) NCT id under a different canonical nctId for a
    merged/duplicate registration; the map lets downstream coverage treat the POTTR id and its canonical as one
    trial. Defaults to the CTGov ``current_version`` alias table. Returns an empty map when the file is absent.
    """
    path = Path(path) if path is not None else CTGOV_ROOT / CURRENT_VERSION / POTTR_ID_ALIASES_FILENAME
    if not path.is_file():
        return {}
    aliases: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            requested = normalize_trial_id(row.get("requested_id", ""))
            canonical = normalize_trial_id(row.get("canonical_id", ""))
            if requested and canonical:
                aliases[requested] = canonical
    return aliases


def normalize_removed_trial_id(value: object, *, registry: str) -> str:
    """Normalize a removal-list id to the given registry's canonical form (or ``""`` if it doesn't apply)."""
    if isinstance(value, float) and value.is_integer():
        trial_id = str(int(value))
    else:
        trial_id = str(value or "").strip().upper()

    registry_name = registry.lower()
    if registry_name == "ctgov":
        return trial_id if trial_id.startswith("NCT") else ""
    if registry_name == "anzctr":
        if not trial_id or trial_id.startswith("NCT"):
            return ""
        if trial_id.startswith("ACTRN"):
            return trial_id
        return f"ACTRN{trial_id}"
    return trial_id


def removed_trial_ids(*, registry: str) -> set[str]:
    """The manual removal set, normalized to ``registry``'s id form (empties dropped)."""
    return {
        normalize_removed_trial_id(trial_id, registry=registry)
        for trial_id in TRIALS_TO_REMOVE
        if normalize_removed_trial_id(trial_id, registry=registry)
    }


def should_remove_trial(trial_id: object, *, registry: str) -> bool:
    """Whether ``trial_id`` is on the manual removal list for ``registry``."""
    return normalize_removed_trial_id(trial_id, registry=registry) in removed_trial_ids(
        registry=registry,
    )
