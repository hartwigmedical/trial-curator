from __future__ import annotations

import hashlib

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.text import clean_text


def stable_key(*parts: object, prefix: str = "", digest_size: int = 24) -> str:
    """Make a deterministic compact key from one or more display values."""
    payload = "\x1f".join(clean_text(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:digest_size]
    return f"{prefix}{digest}" if prefix else digest
