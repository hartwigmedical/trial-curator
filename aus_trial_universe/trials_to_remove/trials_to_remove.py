from __future__ import annotations

# These are non-cancer trials which got captured due to API_CONDITIONS_POTTR & which cannot be removed in the query without affecting legitimate POTTR trials
trials_remove = [
    "NCT06370351",
    "NCT06461286",
    "NCT06970106",
    "NCT05171075",
]


def normalize_removed_trial_id(value: object, *, registry: str) -> str:
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
    return {
        normalize_removed_trial_id(trial_id, registry=registry)
        for trial_id in trials_remove
        if normalize_removed_trial_id(trial_id, registry=registry)
    }


def should_remove_trial(trial_id: object, *, registry: str) -> bool:
    return normalize_removed_trial_id(trial_id, registry=registry) in removed_trial_ids(
        registry=registry,
    )
